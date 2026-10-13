# SESSION LOG: read this first when resuming

Each step gets an entry here as soon as it is done, so a new session can carry on from the last entry.
Other docs: `INTEGRATION_PLAN.md` (the plan), `PROJECT_STATE.md` (what is verified), `NEXT_STEPS.md` (what to do next).

## Session 7 (2026-10-12): Hosted DuckDB (MotherDuck) & Render API Architecture

1. DONE: MotherDuck database feasibility verification. Verified 10 GB monthly free storage vs. ~325 MB compressed footprint for the 1.14 GB public datasets (MCA, GLEIF, ICIJ, OpenSanctions, NSE). Documented in `MOTHERDUCK_EVALUATION.md` and Decision D14 in `DECISIONS.md`.
2. DONE: Uptime monitoring compute defense. Formulated lightweight `/api/health` architecture that responds in-memory so 5-minute UptimeRobot pings consume 0 MotherDuck compute credits.
3. DONE: Database access layer `backend/app/duckdb_db.py` supporting both MotherDuck cloud (`md:planc?motherduck_token=...`) and local DuckDB (`backend/data/osint.duckdb`) with fallback to SQLite.
4. DONE: Streaming and batch import pipeline `backend/app/importers/duckdb_import.py` for all 5 datasets (streaming XML iterparse with constant memory, batch upserts, source provenance, ingest_runs tracking, idempotent re-runs).
5. DONE: Verified with real `samples.zip` (720 entities, 1,120 identifiers, 120 relationships; re-run tested 100% idempotent).
6. DONE: CLI tooling `backend/app/duckdb_cli.py` and `backend/app/cli.py` subcommands (`init`, `stats`, `import`, `search`).
7. DONE: Render deployment configuration `render.yaml` at repo root with CORS, environment variables, and health checks.
8. DONE: Updated `backend/app/api.py` for multi-origin CORS, lightweight health check, and DuckDB query routing.
9. DONE: Next.js frontend updated with Data store Engine badge and compiled cleanly.
10. DONE: Deployment guide in `HOSTED_DUCKDB_RENDER_GUIDE.md`.

## Session 6 (2026-10-12): resumed from github.com/akhil333ask/CONTINUE1. Short session, low credit use.


1. DONE: imported CONTINUE1 into the v0 project (no `.git`/`__pycache__`). Fresh `backend/.venv`; baseline `pytest -q` gives 29 passed, 1 skipped.
2. CHECKED: no `SERPER_API_KEY` in the project env, so the credit-spending live crawl is **still not run**.
3. DONE: fixed AI-limit issue #1. On HTTP 401, `CodexProvider.complete()` now refreshes the token and raises a retryable
   `ProviderError("auth_refreshed", http_status=401)`, so the manager claims a NEW ledger slot for the retry.
   Before the fix, 2 model POSTs could happen in 1 slot. `refresh()` also turns `requests` exceptions into `ProviderError("auth")`.
4. DONE: fixed AI-limit issue #2 in `legacy_ai/auth.py`, timeouts only, logic unchanged: `HTTP_TIMEOUT=(10,30)` on all 3
   `requests` calls; `perform_login()` gives up after `CODEX_LOGIN_TIMEOUT` seconds (default 300) and closes the server.
5. DONE: added `backend/tests/test_ai_limits.py` (3 tests). `pytest -q` gives **32 passed, 1 skipped**.
6. DONE: `samples-load ../samples.zip` → 15,347 entities / 13,071 identifiers. Started uvicorn on :8000; `GET /` = 200 and
   `/api/health` proxied through Next.js = ok. The committed `backend/data/osint.db` held DEMO data only, so you must
   rerun `samples-load` after a fresh download.
7. DONE: live Serper crawl. The key was supplied in chat and passed inline only, never written to a file; this v0 project
   cannot see the "financial" project's env vars. Ran crawler `services/crawler/app.py` on :10000 and `CRAWLER_URL=http://127.0.0.1:10000`.
   `investigate "Wabtec Corporation" --crawl` → HTTP 200, 3976 ms, **~2 credits**, claims stored as `unverified`.
   The rerun was a **cache hit, 0 credits** (TTL 7 days), so the budget path is confirmed.
8. FIXED: the UI said "CRAWLER_URL is not set" because the API process was started without it (the CLI test had set it
   only inline). Now `backend/.env` (gitignored) holds `CRAWLER_URL=http://127.0.0.1:10000`. Restart uvicorn after you change it.
   The crawler process needs `SERPER_API_KEY` in its own env. Verified through the UI proxy: "telerad tech pvt ltd" → teleradtech.com,
   zaubacorp CIN U72200KA2009PTC049526, 5 unverified claims. Also restored the lost `.gitignore` lines and untracked 42 cache/venv files.
9. FIXED: when zaubacorp returns 403, the crawler now still records the CIN from the search result it already has
   (URL slug, title, snippet; source_type `registry_search_result`, no extra request). The no-bypass policy is unchanged.
   "telerad tech pvt ltd" now gives CIN U72200KA2009PTC049526; the backend says to load the MCA file for state KA to verify it.
   It cost about 2 credits (I expired only that query's cache row). Also fixed the test isolation: the `client` fixture now points
   the crawler ledger at the test DB, and conftest blanks `CRAWLER_URL`. Tests: 32 passed.
10. Still open: issue #3 (`fetch_available_model` hard-codes the model; it is user code and was left alone), full datasets,
   the LinkedIn/challenge-bypass decision, and the Windows steps.

## Session 5 (2026-10-12): resumed from github.com/akhil333ask/CONTINUE. All 8 plan steps done.

1. DONE: imported the CONTINUE repo into the v0 project. Code was further along than PROJECT_STATE.md said: integration
   plan steps 1–6 (crawler client, claims, verification, all importers, identifier search) were already coded.
2. DONE: baseline `pytest -q` gives 16 passed.
3. DONE: `samples-load ../samples.zip` failed with `ModuleNotFoundError: openpyxl`. Added `openpyxl`, `xlrd` and `flask` to
   `backend/requirements.txt`. After that, all 8 sample files loaded in about 12 s: MCA 200, GLEIF L1 20, GLEIF L2 20 rels (+19 stubs),
   ICIJ 500 nodes (100 rels skipped, no endpoints in the sample), OpenSanctions 100, NSE 4,344 + 11,777 rows.
   A second run created 0 new rows, so imports are idempotent. `investigate <CIN>` resolves to the NSE debarred record.
4. DONE: added `backend/tests/test_integration.py` (13 tests) plus `tests/fixtures_crawler_wabtec.json`, the documented
   crawler response. Covered: the real samples load and are idempotent; GLEIF L2 subject = StartNode (child) and
   object = EndNode (parent); CIN/PAN search; crawler cache, no retry on 500, bounded 503 retries, errors not cached,
   RPM + daily budget, 5 concurrent identical calls = 1 upstream POST, not-configured. Claims: the Wabtec US-parent
   crawl gets the Indian subsidiary's CIN, which is stored as `related` and not merged. Placeholders are dropped and
   no crawler graph edges are created. Matching CIN + name gives `verified`.
   Fix: the overview claim had no source URL. It now uses the reproducible Google search URL (claims.py).
   `pytest -q` gives **29 passed**.
5. DONE: UI. Added `components/osint/intel-view.tsx`, a new default "Intelligence" tab: query by CIN/LEI/PAN/DIN/name;
   an opt-in crawl checkbox that warns about credits; detected identifiers; Subject, Verified facts, Connections
   (parents/subsidiaries kept separate), Unverified leads (status, confidence, method, source URL, retrieval time),
   Caveats. Added a `CrawlerPanel` (health + usage) in that tab and in "Sources & AI budget".
   Browser-verified against the sample DB: CIN L29246GJ1988PLC010888 → NSE debarment fact with source URL.
6. DONE (zero-credit live test): ran the real `services/crawler/app.py` with no SERPER_API_KEY.
   `crawler-health` → reachable, serper_configured=false. `investigate "Wabtec Corporation" --crawl` → crawler HTTP 500
   "Missing SERPER_API_KEY", **not retried** (1 attempt in the usage ledger). Real HTTP path proven; no credits spent.
   NOT done: a credit-spending live crawl (no key provided). See NEXT_STEPS.md for the exact single command.
7. DONE: updated PROJECT_STATE.md, NEXT_STEPS.md, CHANGELOG.md.

## How to run (any machine)
```
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\python.exe -m pip ...
.venv/bin/python -m pytest -q                         # expect 29 passed
.venv/bin/python -m app.cli samples-load ../samples.zip
.venv/bin/uvicorn app.api:app --port 8000
cd .. && pnpm install && pnpm dev                     # http://localhost:3000
```
Crawler (optional): `cd services/crawler && pip install -r requirements.txt && SERPER_API_KEY=... python app.py`
(port 10000), then set `CRAWLER_URL=http://127.0.0.1:10000` in `backend/.env`.
