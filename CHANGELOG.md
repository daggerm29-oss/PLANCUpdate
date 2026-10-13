# CHANGELOG

## Session 7 (2026-10-12)
- Evaluated MotherDuck as hosted DuckDB backend for 1.14 GB public datasets (documented in `MOTHERDUCK_EVALUATION.md` and Decision D14).
- Built high-performance DuckDB data access layer (`backend/app/duckdb_db.py`) supporting both MotherDuck cloud (`md:planc` via `MOTHERDUCK_TOKEN`) and local DuckDB (`backend/data/osint.duckdb`) with graceful SQLite fallback.
- Implemented repeatable, streamed and batched import pipeline (`backend/app/importers/duckdb_import.py`) for all 5 public datasets (MCA, GLEIF Level 1 & 2 XML with `iterparse`, ICIJ CSV, OpenSanctions CSV, NSE debarred lists).
- Added standalone CLI (`backend/app/duckdb_cli.py`) and subcommands in `backend/app/cli.py` for `init`, `import`, and `stats`.
- Added root `render.yaml` deployment specification for Render Free Web Service with lightweight in-memory `/api/health` protecting against UptimeRobot compute burnout.
- Updated `backend/app/api.py` with multi-origin CORS, DuckDB query routing, and lightweight health check.
- Added comprehensive integration tests (`backend/tests/test_duckdb_integration.py`) covering all 5 dataset loaders, streaming XML, and idempotent re-imports.
- Generated end-to-end deployment guide (`HOSTED_DUCKDB_RENDER_GUIDE.md`).

## Session 6 (2026-10-12)

- Fix: an HTTP 401 no longer re-POSTs inside the same AI ledger slot. The token is refreshed, and the retry claims a new slot (`app/ai/providers.py`).
- Fix: timeouts on every OAuth HTTP call plus a bounded browser-login wait (`legacy_ai/auth.py`, logic unchanged).
- Tests: `backend/tests/test_ai_limits.py` (+3). Total: 32 passed, 1 skipped.
- Crawler: the CIN is taken from the registry search result (URL slug/snippet) even when the registry page is blocked.
- Tests: the crawler usage ledger is isolated to the test DB.
- Verified: one live Serper crawl for "Wabtec Corporation" (~2 credits); the rerun was a cache hit (0 credits).

## Session 1 — 2026-10-11
- Research: verified GLEIF API (live call, 60 req/min/user documented), OpenSanctions bulk index (live; CC BY-NC 4.0), Supabase Free limits, OpenCorporates token requirement.
- Added progress docs: `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `DECISIONS.md`, `API_SOURCES.md`, `AI_INTEGRATION.md`, `PROJECT_STATE.md`, `NEXT_STEPS.md`, `CHANGELOG.md`.
- Backend (`backend/`): settings, SQLite schema with provenance + history + FTS5, HTTP client, GLEIF and OpenSanctions connectors, normaliser, matcher with review queue, graph analytics, reports, FastAPI, CLI.
- AI: central request manager, Codex provider wrapping your `auth.py`, grounded assistant with citation validation and deterministic fallback, AI CLI.
- Tests: `backend/tests/` (AI budget, resolution accuracy, graph correctness, provenance/dedup, HTTP retries, API).
- Frontend: dark analyst workspace (search, entity profile, graph, evidence inspector, ownership, timeline, report, paths, assistant, leads, review queue, system monitor).

## Session 3 — 2026-10-11
- Resumed from GitHub. Session 2 had saved settings/db/connectors/resolution/graph/AI manager+providers+assistant only.
- Added: `app/api.py`, `app/cli.py`, `app/ai/cli.py`, `app/reports.py`, `app/demo.py` (synthetic fixture), `tests/` (16 passing).
- Added the frontend: `lib/api.ts`, `components/osint/*`, `app/page.tsx`, a `/api` rewrite and security headers in `next.config.mjs`, and the dark theme.
- Added `AI_INTEGRATION.md`. Hardened `.gitignore` (backend/.env, .secrets tokens, .venv, data).
- P10 saved investigations done (API + UI).

## Session 5 — 2026-10-12
- Resumed from GitHub (CONTINUE). Added `SESSION_LOG.md` (step-by-step resume log).
- requirements: + openpyxl, xlrd (NSE files), flask (crawler).
- claims.py: the overview claim now has a source URL (the reproducible search URL).
- Tests: `tests/test_integration.py` (13) + crawler fixture → 29 passed.
- UI: `components/osint/intel-view.tsx` (Intelligence tab, default) and a crawler health/usage panel.
- Zero-credit live test against the real Flask crawler.
