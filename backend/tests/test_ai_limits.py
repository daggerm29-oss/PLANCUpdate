"""Session 6 fixes: a 401 must not send a second model POST inside the same ledger slot, and a stalled
token refresh must surface as a ProviderError instead of hanging or leaking a raw requests exception."""
import pytest
import requests

from app.ai.providers import CodexProvider, ProviderError
from app.settings import settings


class _Resp:
    status_code = 401
    headers: dict = {}

    def close(self):
        pass


class _Session:
    def __init__(self):
        self.posts = 0

    def post(self, *args, **kwargs):
        self.posts += 1
        return _Resp()


def _provider(tmp_path, refresh_impl):
    session = _Session()

    class _Auth:
        refresh_access_token = staticmethod(refresh_impl)

    p = CodexProvider(settings, session=session, legacy=(_Auth, object()))
    p.token_file = tmp_path / "tokens.json"
    p.valid_tokens = lambda: {"access_token": "old", "refresh_token": "r"}
    p.load_tokens = lambda: {"access_token": "old", "refresh_token": "r"}
    p.save_tokens = lambda new, previous=None: new
    p.build_request = lambda tokens, system, prompt: ({}, {})
    return p, session


def test_401_refreshes_once_and_defers_retry_to_manager(tmp_path):
    refreshed = []
    p, session = _provider(tmp_path, lambda rt: refreshed.append(rt) or {"access_token": "new"})
    with pytest.raises(ProviderError) as exc:
        p.complete("s", "p", 5)
    assert session.posts == 1, "only ONE model POST per claimed slot"
    assert refreshed == ["r"]
    assert exc.value.retryable and exc.value.http_status == 401


def test_refresh_timeout_becomes_provider_error(tmp_path):
    def stalled(rt):
        raise requests.Timeout("token server stalled")

    p, session = _provider(tmp_path, stalled)
    with pytest.raises(ProviderError) as exc:
        p.complete("s", "p", 5)
    assert exc.value.kind == "auth" and not exc.value.retryable
    assert session.posts == 1


def test_legacy_auth_calls_have_timeouts():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "legacy_ai" / "auth.py").read_text()
    assert src.count("timeout=HTTP_TIMEOUT") == 3
    assert "LOGIN_TIMEOUT_SECONDS" in src
