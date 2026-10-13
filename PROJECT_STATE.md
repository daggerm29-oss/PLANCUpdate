# PROJECT STATE

Last updated: 2026-10-12, session 7. Hosted DuckDB (MotherDuck) + Render API integration complete. Streaming pipeline tested on real samples.zip (720 entities, 100% idempotent). See SESSION_LOG.md and HOSTED_DUCKDB_RENDER_GUIDE.md.

## Architecture (see ARCHITECTURE.md & MOTHERDUCK_EVALUATION.md)
PLANC Frontend on Vercel (Next.js 16) → Render Free Web Service (FastAPI / Uvicorn) → Hosted DuckDB (MotherDuck `md:planc` via `MOTHERDUCK_TOKEN`). SQLite preserved as local fallback. AI goes through one budgeted manager that wraps your `auth.py`.

## Completed
- MotherDuck evaluation & verification: 10 GB free tier comfortably hosts ~325 MB compressed database for the 1.14 GB public datasets (<4% limit). Documented in `MOTHERDUCK_EVALUATION.md` & Decision D14.
- Uptime monitoring compute protection: `/api/health` responds in-memory without database queries on regular 5-minute pings, consuming 0 MotherDuck compute credits.
- Database access layer: `backend/app/duckdb_db.py` supporting MotherDuck, local DuckDB, and SQLite.
- Streaming & batched import pipeline: `backend/app/importers/duckdb_import.py` for MCA, GLEIF L1/L2 (streaming `iterparse`), ICIJ, OpenSanctions, NSE. Verified 100% idempotent.
- CLI tooling: `backend/app/duckdb_cli.py` (`init`, `import`, `stats`, `search`) + subcommands in `app.cli`.
- Render deployment blueprint: `render.yaml` with Python 3.11, environment variables, health check, and CORS configuration.
- End-to-end guide: `HOSTED_DUCKDB_RENDER_GUIDE.md`.
- Ingestion: GLEIF API connector (LEI records + Level 2 parent/child), OpenSanctions bulk FtM loader (cached download, 3-pass parse, unchanged-version skip). Raw payloads, history and run stats are stored.
- Resolution: normaliser, identifier auto-joins, RapidFuzz candidates go to a review queue (never auto-merged).
- Graph analytics: hub-penalised k-shortest paths, ownership chains, circular ownership (SCC), shared addresses/directors, sanctions-exposure leads, timeline, same_as conflicts.
- AI: central manager (RPM, concurrency, queue, dedup, cache, 429 cooldown, daily budget, priorities), Codex provider, grounded assistant with citation validation, deterministic fallback, `app.ai.cli`.
- API (`app/api.py`): health, stats, AI status, search, entity detail/graph/ownership/timeline/report, relationship evidence inspector, paths, leads, assistant brief, review queue, saved investigations.
- CLI (`app/cli.py`): init-db, stats, gleif-search, gleif-crawl, os-load, screen, review, leads, demo, duckdb-init, duckdb-stats, duckdb-import.
- Synthetic demo fixture (`app/demo.py`, source `demo`, clearly fictional) for tests and a first look.
- UI: entity explorer (overview, graph, ownership, timeline/contradictions, Markdown report download), connection paths with AI explanation, leads, match review, investigations, sources plus AI budget monitor, and an evidence inspector drawer.

## Verified
- `python3 -m py_compile` across all backend modules → 0 syntax or import errors.
- Unit & integration tests in `backend/tests/test_duckdb_integration.py` → all passed.
- Real `samples.zip` test: 720 entities, 1,120 identifiers, 120 relationships imported. Re-run verified 100% idempotent (0 duplicates).
- Search test against real sample records: CIN, name, and LEI search verified.
- Next.js application build (`npm run build`) → succeeded cleanly.
- `cd backend && .venv/bin/python -m pytest -q` → 32 passed, 1 skipped.

## Not verified (needs your machine / credentials)
- Live MotherDuck cloud connection: requires your `MOTHERDUCK_TOKEN` in the environment.
- Live Codex call (`python -m app.ai.cli ping`): needs your OAuth values in `backend/.env` and a login.
- Live Serper crawl: requires `SERPER_API_KEY`.


## Audit — session 4 (2026-10-12), before Windows setup. No code changed.
Evidence column = what was actually observed. "Untested" means the code exists but nobody has run it for real.

| Area | Status | Evidence |
|---|---|---|
| Backend tests | VERIFIED (Linux, Python 3.13) | `pytest -q` → 16 passed. Tests use a temp DB + FakeProvider; no network, no AI budget used. Windows run: untested |
| GLEIF connector, live search | VERIFIED | `python -m app.cli gleif-search "Deutsche Bank" --limit 2` returned live LEI records (no API key needed) |
| GLEIF crawl into the DB | UNTESTED | `gleif-crawl` not run against the live API this session |
| OpenSanctions download source | VERIFIED reachable | `us_ofac_sdn` index → `entities.ftm.json`, 53 MB, 72,408 entities (2026-10-11) |
| OpenSanctions load into the DB | UNTESTED | `os-load` not run against the real file |
| `backend/data/osint.db` | Contains DEMO data only | `stats`: 7 entities, 10 relationships, 0 raw_records (demo rows are source `demo`) |
| Frontend ↔ backend | VERIFIED in the v0 sandbox | `/api/*` is rewritten to `OSINT_API_URL` (default `http://127.0.0.1:8000`). Windows: untested |
| OAuth login (`legacy_ai/auth.py`) | UNTESTED, cannot work until configured | `CODEX_CLIENT_ID`, `CODEX_AUTH_URL_BASE`, `CODEX_TOKEN_URL` are blank in `.env.example`; values must come from the user's original config.py |
| Model discovery (`app.ai.cli models`) | Weak check only | `fetch_available_model()` returns the hard-coded `gpt-5.6-terra` for ANY non-401 status, so it proves only that the token isn't rejected, not that the model exists |
| Prompt → response (`CodexProvider`) | UNTESTED, highest risk | The request body/headers in `providers.py` are reconstructed (the user's agent.py wasn't attached). Only proven by one successful `app.ai.cli ping` |
| RPM / concurrency / cache / retries | VERIFIED by unit tests with a fake provider | Slots are claimed in a shared SQLite ledger (`BEGIN IMMEDIATE`); every retry attempt claims its own slot |

### AI-limit issues: #1 and #2 FIXED in session 6 (tests/test_ai_limits.py, 32 passed). #3 is still open (user code).
1. **Uncounted second request on HTTP 401.** `CodexProvider.complete()` refreshes the token and re-POSTs inside the SAME ledger slot, so in the worst case one counted request can send 2 model POSTs. Fix: on 401, refresh and raise a retryable error so the manager claims a new slot.
2. **No timeouts on your OAuth HTTP calls.** `auth.py` calls `requests.post(TOKEN_URL, ...)` without a `timeout`. `refresh_access_token()` runs inside a claimed slot, so a stalled token server can hang that worker (the slot's lease frees itself after `AI_REQUEST_TIMEOUT + 30` s). `perform_login()` also waits forever for the browser callback.
3. `fetch_available_model()`: see "Model discovery" above.


## Session 5: India integration status (verified 2026-10-12)
| Feature | Status | Evidence |
|---|---|---|
| Tests | VERIFIED | `pytest -q` → 29 passed (16 old + 13 in `tests/test_integration.py`) |
| MCA / GLEIF L1 / GLEIF L2 / ICIJ / NSE (xls+xlsx) / OpenSanctions CSV importers | VERIFIED on samples | `samples-load samples.zip`; re-run adds 0 rows |
| GLEIF L2 parent/child direction | VERIFIED | test compares stored edges with StartNode→EndNode in the XML |
| CIN / LEI / PAN / DIN / name search | VERIFIED (CIN, PAN by test; LEI via existing tests) | `investigate <CIN>` |
| Claims with source URL, retrieval date, excerpt, method, confidence; unverified vs verified vs related | VERIFIED with mocked crawler | Wabtec fixture: subsidiary CIN → `related`, not merged |
| Crawler cache, dedup, concurrency, RPM, daily budget, bounded retries, usage ledger | VERIFIED with mocked HTTP | `test_crawler_*` |
| PLANC ↔ real Flask crawler over HTTP | VERIFIED, zero credits | health OK; 500 (no key) not retried |
| Live crawl with Serper | NOT RUN (no SERPER_API_KEY) | |
| Intelligence UI + crawler panel | VERIFIED in browser (sample DB) | |
| Cross-source joins | MOCKED only | the samples do not overlap; needs full MCA/GLEIF files |

Notes: `serper_credits_upper_bound` counts 4 for any upstream error, so it over-estimates (e.g. the no-key 500 spent 0).
LinkedIn/challenge-bypass fetching in the crawler is unchanged (crawler-side decision; LinkedIn claims get confidence 0.35 and a terms note).

## Known limitations
- No director/PSC data until Companies House (P11). The current person→company edges come from OpenSanctions only.
- SQLite is single-writer. Fine for one analyst; see P15 for Postgres.
- OpenSanctions is CC BY-NC 4.0: non-commercial use only, attribution required.

## Required environment variables
See `backend/.env.example`. Only the `CODEX_*` values are needed for AI; everything else has defaults.
