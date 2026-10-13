# AI INTEGRATION

## How your scripts are used
- `backend/legacy_ai/auth.py`: your original file, functions unchanged (`perform_login`, `refresh_access_token`, `fetch_available_model`, `CallbackHandler`).
- `backend/legacy_ai/config.py`: a stand-in that exports the same names `auth.py` imports (`CLIENT_ID`, `AUTH_URL_BASE`, `TOKEN_URL`, `PORT`, `CALLBACK_PATH`, `REDIRECT_URI`). The values come from `backend/.env` (gitignored), so the redacted constants never get committed. Copy the real values from your original `config.py` into `CODEX_CLIENT_ID`, `CODEX_AUTH_URL_BASE`, `CODEX_TOKEN_URL`, `CODEX_CALLBACK_PORT`, `CODEX_CALLBACK_PATH`, `CODEX_REDIRECT_URI`.
- `backend/app/ai/providers.py` → `CodexProvider` loads both files by path and:
  - **login**: `auth.perform_login()` → tokens saved to `CODEX_TOKEN_FILE` (`backend/.secrets/`, gitignored).
  - **refresh**: `auth.refresh_access_token()` runs automatically when the JWT `exp` is under 2 minutes away.
  - **model discovery**: `auth.fetch_available_model()` returns `gpt-5.6-terra` (exposed through `python -m app.ai.cli models`).
  - **model invocation** (missing from your files, so it was added here): streaming `POST CODEX_RESPONSES_URL` (Responses API format: `model`, `instructions`, `input`, `stream: true`), with headers `Authorization: Bearer`, `originator`, and `chatgpt-account-id` (read from the JWT claims or `CODEX_ACCOUNT_ID`). The SSE stream is parsed into the final text and usage.
- Tokens stay on the backend. The frontend only calls `/api/*` and never receives a credential.

## Assumptions to verify on your machine (UNCONFIRMED)
The invocation endpoint, its headers and its payload shape come from the public Codex CLI and are not an officially documented API. They may change. If `python -m app.ai.cli ping` fails with `invalid_response` or HTTP 400, compare against your working agent script (`agent.chat_with_codex`, which is not part of this repo) and adjust only `CodexProvider.build_request` / `read_response`. Use of your ChatGPT account through this route is subject to OpenAI's terms; the budget defaults below are deliberately far under any plausible limit.

## Request budget (all in `backend/.env`)
| Variable | Default | Meaning |
|---|---|---|
| `AI_RPM_LIMIT` | 2 | max request starts per window |
| `AI_RPM_SAFETY_MARGIN_SECONDS` | 5 | window = 60 s + margin |
| `AI_MAX_CONCURRENT_REQUESTS` | 1 | simultaneous in-flight requests (all processes) |
| `AI_MAX_RETRIES` | 1 | retries on retryable errors (never on 429) |
| `AI_REQUEST_TIMEOUT` | 60 | seconds per request |
| `AI_CACHE_TTL_SECONDS` | 3600 | cached answer lifetime |
| `AI_DAILY_REQUEST_BUDGET` | 40 | hard cap per UTC day |
| `AI_DAILY_TOKEN_BUDGET` | 0 | 0 = off |
| `AI_QUEUE_MAX` / `AI_QUEUE_MAX_WAIT_SECONDS` | 8 / 75 | bounded queue; waiting longer → graceful fallback |
| `AI_BACKGROUND_RESERVE` | 10 | daily requests reserved for interactive questions |
| `AI_429_COOLDOWN_SECONDS` | 120 | pause after a 429 (or `Retry-After` if larger) |

## Behaviour
- One central `AIManager`. State lives in SQLite (`ai_request_log`, `ai_cache`, `ai_state`, `ai_counters`), so separate processes share **one** budget.
- Cache key = task + system prompt + prompt + evidence fingerprint. When the evidence changes, the key changes, which invalidates stale answers automatically.
- Duplicate in-flight prompts wait for the first result instead of issuing a second call.
- Priorities: interactive (0) runs before background (10). Background work is deferred during cooldowns and when only the reserve is left.
- Validation: the model must answer in JSON. Claims citing an unknown `E#` are rejected, and claims labelled more strongly than their evidence basis get downgraded.
- Fallback: when AI is unavailable, every endpoint still returns the deterministic facts, paths and evidence, plus `ai_status.reason`.
- Calls that never use AI: search, graph, ownership, timeline, leads, review, and reports without `?ai=true`.

## Tests (`backend/tests/test_ai_manager.py`, all passing)
RPM window compliance, cache hit with no second call, cache invalidation on an evidence change, daily budget exhaustion, 429 cooldown (exactly 1 provider call), background deferral, a budget shared across two manager instances, and max 1 concurrent request under 3 threads.

## Commands
```bash
cd backend
.venv/bin/python -m app.ai.cli login    # opens browser once
.venv/bin/python -m app.ai.cli models
.venv/bin/python -m app.ai.cli ping     # spends exactly 1 request
.venv/bin/python -m app.ai.cli status
```
