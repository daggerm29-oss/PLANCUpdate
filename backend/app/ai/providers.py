"""Model providers behind one interface. Only `AIManager` may call `complete()`.

- CodexProvider: your ChatGPT OAuth login (legacy_ai/auth.py, unchanged) + the Codex Responses endpoint.
- FakeProvider: deterministic test double, always labelled as such.
- DisabledProvider: AI switched off; the app answers with deterministic output only.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import requests

from ..http import parse_retry_after
from ..settings import BACKEND_DIR, Settings

LEGACY_DIR = BACKEND_DIR / "legacy_ai"
REQUIRED_LEGACY = {"CLIENT_ID": "CODEX_CLIENT_ID", "AUTH_URL_BASE": "CODEX_AUTH_URL_BASE", "TOKEN_URL": "CODEX_TOKEN_URL"}


@dataclass
class ProviderResponse:
    text: str
    usage: dict | None = None
    model: str | None = None
    http_status: int | None = 200


class ProviderError(Exception):
    """kind: rate_limited | timeout | network | server | auth | bad_request | not_configured | invalid_response"""

    def __init__(self, kind: str, message: str, *, retryable: bool = False, http_status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.http_status = http_status
        self.retry_after = retry_after


def normalize_usage(usage: dict | None) -> dict | None:
    if not usage:
        return None
    inp = usage.get("input_tokens", usage.get("prompt_tokens"))
    out = usage.get("output_tokens", usage.get("completion_tokens"))
    total = usage.get("total_tokens")
    if total is None and inp is not None and out is not None:
        total = inp + out
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": total}


class BaseProvider:
    name = "base"

    def __init__(self, model: str):
        self.model = model

    def status(self) -> dict:
        return {"provider": self.name, "model": self.model, "available": True, "reason": None}

    def complete(self, system: str, prompt: str, timeout: float) -> ProviderResponse:
        raise NotImplementedError


class DisabledProvider(BaseProvider):
    name = "disabled"

    def status(self) -> dict:
        return {"provider": self.name, "model": self.model, "available": False, "reason": "AI_PROVIDER=disabled"}

    def complete(self, system: str, prompt: str, timeout: float) -> ProviderResponse:
        raise ProviderError("not_configured", "AI is disabled (AI_PROVIDER=disabled)")


_EVIDENCE_LINE = re.compile(r"^\[(E\d+)\]", re.MULTILINE)


def fake_answer(prompt: str) -> str:
    ids = _EVIDENCE_LINE.findall(prompt)[:3]
    return json.dumps({
        "summary": "FAKE PROVIDER OUTPUT (test double). It restates which evidence was retrieved and does no analysis.",
        "claims": [{"text": f"Evidence {i} was retrieved for this question.", "citations": [i], "basis": "documented"}
                   for i in ids],
        "uncertainties": ["Set AI_PROVIDER=codex and log in to get real model analysis."],
        "next_steps": [],
    })


class FakeProvider(BaseProvider):
    name = "fake"

    def __init__(self, model: str = "fake-model", responder: Callable[[str, str, int], Any] | None = None,
                 latency: float = 0.0):
        super().__init__(model)
        self.responder = responder
        self.latency = latency
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def status(self) -> dict:
        return {"provider": self.name, "model": self.model, "available": True,
                "reason": "test provider: deterministic output, not a real model"}

    def complete(self, system: str, prompt: str, timeout: float) -> ProviderResponse:
        with self._lock:
            self.calls.append(prompt)
            call_no = len(self.calls)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.latency:
                time.sleep(self.latency)
            result = self.responder(system, prompt, call_no) if self.responder else fake_answer(prompt)
            if isinstance(result, BaseException):
                raise result
            if isinstance(result, ProviderResponse):
                return result
            text = str(result)
            usage = {"input_tokens": len(prompt) // 4, "output_tokens": len(text) // 4}
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
            return ProviderResponse(text=text, usage=usage, model=self.model)
        finally:
            with self._lock:
                self.active -= 1


# --------------------------------------------------------------------------- legacy auth glue

_LEGACY: tuple[Any, Any] | None = None
_LEGACY_LOCK = threading.Lock()


def _load_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_legacy_modules() -> tuple[Any, Any]:
    """Import your unchanged auth.py; its `from config import ...` resolves to legacy_ai/config.py."""
    global _LEGACY
    with _LEGACY_LOCK:
        if _LEGACY is None:
            previous = sys.modules.get("config")
            config = _load_file("config", LEGACY_DIR / "config.py")
            sys.modules["config"] = config
            try:
                auth = _load_file("legacy_ai_auth", LEGACY_DIR / "auth.py")
            finally:
                if previous is not None:
                    sys.modules["config"] = previous
                else:
                    sys.modules.pop("config", None)
            _LEGACY = (auth, config)
        return _LEGACY


def jwt_claims(token: str | None) -> dict:
    """Read a JWT payload WITHOUT verifying it, only to get expiry / account id locally."""
    if not token or token.count(".") < 2:
        return {}
    payload = token.split(".")[1]
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


_DONE = object()


def _decode_event(data: str):
    if data.strip() == "[DONE]":
        return _DONE
    try:
        event = json.loads(data)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def iter_sse(resp: Any, deadline: float) -> Iterator[dict]:
    """Yield JSON events from a text/event-stream response, enforcing an overall deadline."""
    data_lines: list[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if time.monotonic() > deadline:
            raise ProviderError("timeout", "model response exceeded AI_REQUEST_TIMEOUT", retryable=True)
        if line is None:
            continue
        if line == "":
            if data_lines:
                event = _decode_event("\n".join(data_lines))
                data_lines = []
                if event is _DONE:
                    return
                if event:
                    yield event
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        event = _decode_event("\n".join(data_lines))
        if event and event is not _DONE:
            yield event


def _event_error(event: dict) -> ProviderError:
    err = event.get("error") or (event.get("response") or {}).get("error") or {}
    code = str(err.get("code") or err.get("type") or "")
    message = str(err.get("message") or code or "model request failed")[:300]
    if any(word in code for word in ("rate_limit", "usage_limit", "quota")):
        return ProviderError("rate_limited", message, http_status=429)
    if "context_length" in code or "invalid" in code:
        return ProviderError("bad_request", message)
    return ProviderError("server", message)


def _output_text(response: dict) -> str:
    parts = []
    for item in response.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    return "".join(parts)


def _snippet(resp: Any) -> str:
    try:
        return re.sub(r"\s+", " ", resp.text or "")[:300]
    except Exception:  # body unreadable: the status code is still reported
        return ""


class CodexProvider(BaseProvider):
    """Invocation layer that your attached files did not include (your agent.py `chat_with_codex` was not attached).

    Login, refresh and model discovery call YOUR functions unchanged; this class only stores tokens and sends
    one streamed request to CODEX_RESPONSES_URL per call.
    """

    name = "codex"

    def __init__(self, cfg: Settings, session: Any = None, legacy: tuple[Any, Any] | None = None):
        super().__init__(cfg.ai_model)
        self.cfg = cfg
        self.session = session or requests.Session()
        self.token_file = Path(cfg.codex_token_file)
        self._legacy = legacy
        self._lock = threading.RLock()

    @property
    def legacy(self) -> tuple[Any, Any]:
        if self._legacy is None:
            self._legacy = load_legacy_modules()
        return self._legacy

    def missing_config(self) -> list[str]:
        try:
            _, config = self.legacy
        except Exception as exc:
            return [f"legacy_ai import failed ({type(exc).__name__})"]
        return [env for attr, env in REQUIRED_LEGACY.items() if not getattr(config, attr, "")]

    def status(self) -> dict:
        base = {"provider": self.name, "model": self.model, "available": False, "reason": None,
                "logged_in": False, "token_expires_at": None}
        missing = self.missing_config()
        if missing:
            return {**base, "reason": f"set {', '.join(missing)} in backend/.env (values from your original config.py)"}
        tokens = self.load_tokens()
        if not tokens or not tokens.get("access_token"):
            return {**base, "reason": "not logged in: run `python -m app.ai.cli login` in backend/"}
        expires = self._expires_at(tokens)
        selected_model = tokens.get("selected_model") or self.model
        available_models = tokens.get("available_models") or [selected_model]
        email = tokens.get("email") or jwt_claims(tokens.get("id_token")).get("email")
        account_id = self.account_id(tokens)
        if expires and expires < time.time() and not tokens.get("refresh_token"):
            return {**base, "logged_in": True, "token_expires_at": expires, "selected_model": selected_model,
                    "available_models": available_models, "email": email, "account_id": account_id,
                    "reason": "token expired and no refresh token stored: log in again"}
        return {**base, "available": True, "logged_in": True, "token_expires_at": expires,
                "model": selected_model, "selected_model": selected_model, "available_models": available_models,
                "email": email, "account_id": account_id}

    # ------------------------------------------------------------------ tokens (never logged, file mode 600)

    def load_tokens(self) -> dict | None:
        try:
            if not self.token_file.is_file():
                # Check root or backend directories
                alt = Path("auth_session.json")
                if alt.is_file():
                    self.token_file = alt.resolve()
                else:
                    alt2 = BACKEND_DIR.parent / "auth_session.json"
                    if alt2.is_file():
                        self.token_file = alt2.resolve()
            data = json.loads(self.token_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(data, dict):
            if data.get("selected_model"):
                self.model = data["selected_model"]
            return data
        return None

    def save_tokens(self, tokens: dict, previous: dict | None = None) -> dict:
        data = {**(previous or {}), **{k: v for k, v in tokens.items() if v is not None}}
        if tokens.get("expires_in"):
            data["expires_at"] = time.time() + float(tokens["expires_in"])
        data["saved_at"] = time.time()
        for k in ("id_token", "access_token"):
            claims = jwt_claims(data.get(k))
            if claims.get("email") and not data.get("email"):
                data["email"] = claims["email"]
        if data.get("selected_model"):
            self.model = data["selected_model"]
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_file.with_name(self.token_file.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.token_file)
        return data

    @staticmethod
    def _expires_at(tokens: dict) -> float | None:
        if tokens.get("expires_at"):
            return float(tokens["expires_at"])
        exp = jwt_claims(tokens.get("access_token")).get("exp")
        return float(exp) if exp else None

    def login(self) -> dict:
        auth, _ = self.legacy
        tokens = auth.perform_login()
        if not tokens or not tokens.get("access_token"):
            raise ProviderError("auth", "login failed: the token exchange returned no access token")
        saved = self.save_tokens(tokens)
        self.discover_models()
        return saved

    def refresh(self, tokens: dict) -> dict:
        auth, _ = self.legacy
        if not tokens.get("refresh_token"):
            raise ProviderError("auth", "no refresh token stored: run `python -m app.ai.cli login` again")
        try:
            new = auth.refresh_access_token(tokens["refresh_token"])
        except requests.RequestException as exc:
            raise ProviderError("auth", f"token refresh failed: {type(exc).__name__}", retryable=False) from exc
        if not new or not new.get("access_token"):
            raise ProviderError("auth", "token refresh failed: run `python -m app.ai.cli login` again")
        return self.save_tokens(new, previous=tokens)

    def valid_tokens(self) -> dict:
        with self._lock:
            tokens = self.load_tokens()
            if not tokens or not tokens.get("access_token"):
                raise ProviderError("not_configured", "not logged in: run `python -m app.ai.cli login`")
            expires = self._expires_at(tokens)
            if expires and expires - 120 < time.time():
                tokens = self.refresh(tokens)
            return tokens

    def discover_model(self) -> tuple[str | None, bool]:
        """Model DISCOVERY through your fetch_available_model(). Separate from invocation; not a generation call."""
        auth, _ = self.legacy
        return auth.fetch_available_model(self.valid_tokens()["access_token"])

    def discover_models(self) -> list[str]:
        auth, _ = self.legacy
        tokens = self.valid_tokens()
        models, ok = auth.fetch_available_models(tokens["access_token"])
        if ok and models:
            tokens["available_models"] = models
            if not tokens.get("selected_model") or tokens.get("selected_model") not in models:
                tokens["selected_model"] = models[0]
            self.model = tokens["selected_model"]
            self.save_tokens(tokens)
            return models
        return tokens.get("available_models") or [self.model]

    def set_model(self, model_name: str) -> bool:
        with self._lock:
            tokens = self.load_tokens()
            if not tokens:
                return False
            tokens["selected_model"] = model_name
            self.model = model_name
            self.save_tokens(tokens)
            return True

    def disconnect(self) -> bool:
        with self._lock:
            try:
                if self.token_file.is_file():
                    self.token_file.unlink()
                auth, _ = self.legacy
                auth.clear_session()
                return True
            except Exception:
                return False

    def account_id(self, tokens: dict) -> str | None:
        if self.cfg.codex_account_id:
            return self.cfg.codex_account_id
        if tokens.get("account_id"):
            return str(tokens["account_id"])
        for key in ("id_token", "access_token"):
            claims = jwt_claims(tokens.get(key)).get("https://api.openai.com/auth") or {}
            if isinstance(claims, dict) and claims.get("chatgpt_account_id"):
                return str(claims["chatgpt_account_id"])
        return None

    # ------------------------------------------------------------------ invocation

    def build_request(self, tokens: dict, system: str, prompt: str) -> tuple[dict, dict]:
        user_msg = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]}
        path = self.cfg.codex_instructions_file
        if path and Path(path).is_file():
            instructions = Path(path).read_text(encoding="utf-8")
            items = [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": system}]},
                     user_msg]
        else:
            instructions, items = system, [user_msg]
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": items,
            "tools": [],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "reasoning": {"effort": self.cfg.codex_reasoning_effort},
            "store": False,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {tokens['access_token']}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "OpenAI-Beta": "responses=experimental",
            "originator": self.cfg.codex_originator,
            "session_id": str(uuid.uuid4()),
        }
        account = self.account_id(tokens)
        if account:
            headers["chatgpt-account-id"] = account
        return body, headers

    def _post(self, tokens: dict, system: str, prompt: str, timeout: float) -> Any:
        body, headers = self.build_request(tokens, system, prompt)
        try:
            return self.session.post(self.cfg.codex_responses_url, json=body, headers=headers, stream=True,
                                     timeout=(min(15.0, timeout), timeout))
        except requests.Timeout as exc:
            raise ProviderError("timeout", f"request timed out after {timeout:.0f}s", retryable=True) from exc
        except requests.RequestException as exc:
            raise ProviderError("network", f"network error: {type(exc).__name__}", retryable=True) from exc

    def complete(self, system: str, prompt: str, timeout: float) -> ProviderResponse:
        deadline = time.monotonic() + timeout
        tokens = self.valid_tokens()
        resp = self._post(tokens, system, prompt, timeout)
        if resp.status_code == 401:
            resp.close()
            with self._lock:
                self.refresh(self.load_tokens() or tokens)
            # Re-sending here would put 2 model POSTs in one ledger slot; let the manager claim a new slot instead.
            raise ProviderError("auth_refreshed", "HTTP 401: token refreshed, retrying in a new request slot",
                                retryable=True, http_status=401)
        return self.read_response(resp, deadline)

    def read_response(self, resp: Any, deadline: float) -> ProviderResponse:
        status = resp.status_code
        if status != 200:
            snippet = _snippet(resp)
            retry_after = parse_retry_after(resp.headers.get("Retry-After"))
            resp.close()
            if status == 429:
                raise ProviderError("rate_limited", f"HTTP 429: {snippet}", http_status=429, retry_after=retry_after)
            if status in (401, 403):
                raise ProviderError("auth", f"HTTP {status}: {snippet}", http_status=status)
            if status >= 500:
                raise ProviderError("server", f"HTTP {status}: {snippet}", retryable=True, http_status=status)
            raise ProviderError("bad_request", f"HTTP {status}: {snippet}", http_status=status)
        resp.encoding = "utf-8"
        deltas: list[str] = []
        done_text: list[str] = []
        final: dict = {}
        try:
            for event in iter_sse(resp, deadline):
                etype = event.get("type", "")
                if etype == "response.output_text.delta":
                    deltas.append(str(event.get("delta") or ""))
                elif etype == "response.output_text.done":
                    done_text.append(str(event.get("text") or ""))
                elif etype in ("response.completed", "response.done", "response.incomplete"):
                    final = event.get("response") or {}
                    break
                elif etype in ("response.failed", "error"):
                    raise _event_error(event)
        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as exc:
            raise ProviderError("network", f"stream interrupted: {type(exc).__name__}", retryable=True) from exc
        except requests.Timeout as exc:
            raise ProviderError("timeout", "stream read timed out", retryable=True) from exc
        finally:
            resp.close()
        text = "".join(deltas) or "".join(done_text) or _output_text(final)
        if not text.strip():
            raise ProviderError("invalid_response", "the model returned no text")
        return ProviderResponse(text=text, usage=normalize_usage(final.get("usage")),
                                model=final.get("model") or self.model, http_status=200)


def get_provider(cfg: Settings) -> BaseProvider:
    if cfg.ai_provider == "codex":
        return CodexProvider(cfg)
    if cfg.ai_provider == "fake":
        return FakeProvider()
    return DisabledProvider(cfg.ai_model)
