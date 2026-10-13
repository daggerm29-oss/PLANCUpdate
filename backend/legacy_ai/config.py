# ==========================================
# config.py — env-backed stand-in for YOUR original config.py.
# auth.py (unchanged) imports these exact names. The values in the attached
# config.py were redacted, so they are read from backend/.env (gitignored)
# instead of being hard-coded in a file that gets pushed to GitHub.
# Only the OAuth constants are needed here; the MCP / grep / context-compaction
# constants from the original belong to your other agent and are not used.
# ==========================================
import os
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env_file():
    if not _ENV_FILE.is_file():
        return
    for raw in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip("\"'")
        os.environ.setdefault(key.strip(), value)


_load_env_file()

# --- OAuth / endpoint config (configured with project defaults) ---
CLIENT_ID = os.environ.get("CODEX_CLIENT_ID", "") or "app_EMoamEEZ73f0CkXaXp7hrann"
PORT = int(os.environ.get("CODEX_CALLBACK_PORT", "") or 1455)
CALLBACK_PATH = os.environ.get("CODEX_CALLBACK_PATH", "") or "/auth/callback"
REDIRECT_URI = os.environ.get("CODEX_REDIRECT_URI", "") or f"http://localhost:{PORT}{CALLBACK_PATH}"
AUTH_URL_BASE = os.environ.get("CODEX_AUTH_URL_BASE", "") or "https://auth.openai.com/oauth/authorize"
TOKEN_URL = os.environ.get("CODEX_TOKEN_URL", "") or "https://auth.openai.com/oauth/token"
TOKEN_FILE = "auth_session.json"

# The original MAX_RPM = 30 is deliberately NOT used: the app's central request
# manager enforces AI_RPM_LIMIT (default 2) for every process.
MAX_RPM = int(os.environ.get("AI_RPM_LIMIT", "") or 2)
