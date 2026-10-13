import base64
import hashlib
import json
import os
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

from config import (
    AUTH_URL_BASE,
    CALLBACK_PATH,
    CLIENT_ID,
    PORT,
    REDIRECT_URI,
    TOKEN_FILE,
    TOKEN_URL,
)

HTTP_TIMEOUT = (10, 30)
LOGIN_TIMEOUT_SECONDS = int(os.environ.get("CODEX_LOGIN_TIMEOUT", "300"))

# GLOBAL STATE
auth_code = None
expected_state = None


def _jwt_claims(token: str | None) -> dict:
    if not token or token.count(".") < 2:
        return {}
    payload = token.split(".")[1]
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_token_file_path() -> Path:
    # Resolve TOKEN_FILE relative to project root or current working dir
    p = Path(TOKEN_FILE)
    if not p.is_absolute():
        # Check if running from backend/ or project root
        if Path("auth_session.json").exists():
            return Path("auth_session.json").resolve()
        backend_file = Path(__file__).resolve().parent.parent / "auth_session.json"
        if backend_file.exists():
            return backend_file
        root_file = Path(__file__).resolve().parent.parent.parent / "auth_session.json"
        if root_file.exists():
            return root_file
        return Path(TOKEN_FILE).resolve()
    return p


def load_session() -> dict | None:
    path = get_token_file_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("access_token"):
                return data
    except Exception as exc:
        print(f"[!] Warning reading session file {path}: {exc}")
    return None


def save_session(tokens: dict, previous: dict | None = None) -> dict:
    data = {**(previous or {}), **{k: v for k, v in tokens.items() if v is not None}}
    now = time.time()
    if tokens.get("expires_in"):
        data["expires_at"] = now + float(tokens["expires_in"])
    elif not data.get("expires_at"):
        exp = _jwt_claims(data.get("access_token")).get("exp")
        data["expires_at"] = float(exp) if exp else now + 3600
    data["saved_at"] = now

    # Extract account info from tokens
    for t_key in ("id_token", "access_token"):
        claims = _jwt_claims(data.get(t_key))
        if claims:
            if claims.get("email") and not data.get("email"):
                data["email"] = claims["email"]
            auth_claims = claims.get("https://api.openai.com/auth") or {}
            if isinstance(auth_claims, dict) and auth_claims.get("chatgpt_account_id") and not data.get("account_id"):
                data["account_id"] = str(auth_claims["chatgpt_account_id"])

    path = get_token_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)
    return data


def clear_session() -> bool:
    path = get_token_file_path()
    try:
        if path.is_file():
            path.unlink()
            return True
    except Exception as exc:
        print(f"[!] Error deleting session file: {exc}")
    return False


def get_or_refresh_session() -> dict | None:
    session = load_session()
    if not session:
        return None
    expires_at = session.get("expires_at")
    # If expired or expiring within 60s
    if expires_at and expires_at - 60 < time.time():
        refresh_token = session.get("refresh_token")
        if not refresh_token:
            return None
        new_tokens = refresh_access_token(refresh_token)
        if not new_tokens or not new_tokens.get("access_token"):
            return None
        session = save_session(new_tokens, previous=session)
    return session


class CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        global auth_code, expected_state
        parsed_url = urllib.parse.urlparse(self.path)

        if parsed_url.path == CALLBACK_PATH:
            query_components = urllib.parse.parse_qs(parsed_url.query)
            if 'code' in query_components:
                returned_state = query_components.get('state', [''])[0]
                if returned_state != expected_state:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Authentication failed: State mismatch.")
                    return
                auth_code = query_components['code'][0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                success_html = "<html><body><h2>Authentication Successful!</h2><p>You can safely close this window.</p></body></html>"
                self.wfile.write(success_html.encode('utf-8'))
            else:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


def perform_login():
    global auth_code, expected_state
    auth_code = None
    print("[*] Starting new browser login flow...")

    code_verifier = base64.urlsafe_b64encode(os.urandom(32)).decode('utf-8').rstrip('=')
    sha256_hash = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(sha256_hash).decode('utf-8').rstrip('=')
    expected_state = os.urandom(16).hex()

    auth_params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email offline_access",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": expected_state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "custom_python_script"
    }

    auth_url = f"{AUTH_URL_BASE}?{urllib.parse.urlencode(auth_params)}"
    server = HTTPServer(('localhost', PORT), CallbackHandler)
    webbrowser.open(auth_url)

    server.timeout = 5
    login_deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    try:
        while not auth_code:
            if time.monotonic() > login_deadline:
                print(f"[!] Login timed out after {LOGIN_TIMEOUT_SECONDS}s without a browser callback.")
                return None
            server.handle_request()
    finally:
        server.server_close()

    print("[+] Exchanging code for tokens...")
    token_payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier
    }
    response = requests.post(TOKEN_URL, data=token_payload, timeout=HTTP_TIMEOUT)
    if response.status_code == 200:
        tokens = response.json()
        saved = save_session(tokens)
        # Automatically discover and save models
        try:
            models, ok = fetch_available_models(saved.get("access_token"))
            if ok and models:
                saved["available_models"] = models
                if not saved.get("selected_model"):
                    saved["selected_model"] = models[0]
                save_session(saved)
        except Exception:
            pass
        return saved
    return None


def refresh_access_token(refresh_token):
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID}
    response = requests.post(TOKEN_URL, data=payload, timeout=HTTP_TIMEOUT)
    if response.status_code == 200:
        new_tokens = response.json()
        saved = save_session(new_tokens, previous=load_session())
        return saved
    return None


def fetch_available_models(access_token):
    """Retrieve available Codex models from the OpenAI endpoint."""
    url = "https://chatgpt.com/backend-api/codex/models?client_version=0.146.0"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        if response.status_code == 401:
            return [], False
        if response.status_code != 200:
            return ["gpt-5.6-terra", "gpt-5-turbo", "o1", "o3-mini", "gpt-4o"], True
        data = response.json()
        models = []
        if isinstance(data, list):
            for m in data:
                if isinstance(m, str):
                    models.append(m)
                elif isinstance(m, dict) and "slug" in m:
                    models.append(m["slug"])
                elif isinstance(m, dict) and "id" in m:
                    models.append(m["id"])
        elif isinstance(data, dict):
            raw_models = data.get("models") or data.get("data") or []
            if isinstance(raw_models, list):
                for m in raw_models:
                    if isinstance(m, str):
                        models.append(m)
                    elif isinstance(m, dict) and "slug" in m:
                        models.append(m["slug"])
                    elif isinstance(m, dict) and "id" in m:
                        models.append(m["id"])
        if not models:
            models = ["gpt-5.6-terra", "gpt-5-turbo", "o1", "o3-mini", "gpt-4o"]
        return models, True
    except Exception as exc:
        print(f"[!] Warning fetching models: {exc}")
        return ["gpt-5.6-terra", "gpt-5-turbo", "o1", "o3-mini", "gpt-4o"], True


def fetch_available_model(access_token):
    """Returns the user-selected model or the first available model from the retrieved list."""
    session = load_session()
    if session and session.get("selected_model"):
        return session["selected_model"], True
    models, ok = fetch_available_models(access_token)
    if not ok or not models:
        return None, False
    return models[0], True


def select_model(model_name: str) -> bool:
    session = load_session()
    if not session:
        return False
    session["selected_model"] = model_name
    save_session(session)
    return True
