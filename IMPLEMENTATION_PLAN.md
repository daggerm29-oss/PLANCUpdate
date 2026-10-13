# IMPLEMENTATION PLAN

Status keys: DONE · ACTIVE · PENDING · BLOCKED. Update this file whenever a phase changes state.
Rule for every phase: build → run its tests → record results in `PROJECT_STATE.md` → update `NEXT_STEPS.md`.

## Milestone 1 (session 1): working evidence engine on two real sources

| # | Phase | Objective | Acceptance criteria | Status |
|---|---|---|---|---|
| P0 | Research & source selection | Verified catalogue, start/defer decision | `API_SOURCES.md` lists source, access, licence, limits, verification status | DONE |
| P1 | Project setup | Repo layout, settings, `.env.example`, venv, progress docs | `pip install -r backend/requirements.txt` works; docs exist | DONE |
| P2 | Database & storage | SQLite schema: raw vs normalised, provenance, FTS, history | `python -m app.cli init-db` creates all tables; re-running is a no-op | DONE |
| P3 | Ingestion | Connectors for GLEIF (API) and OpenSanctions (bulk FtM) | Re-ingest marks records `unchanged`; changed payloads archived to `raw_record_history`; run stats in `ingest_runs` | DONE |
| P4 | Entity resolution | Identifier joins + RapidFuzz scoring into a review queue | LEI/BIC/OGRN/INN/IMO → auto `same_as`; fuzzy → `pending` only; accuracy test precision ≥ 0.9 | DONE |
| P5 | Knowledge graph | Edges carry source, dates, basis, confidence, evidence | Every edge opens to ≥1 evidence row; `same_as` marked `inferred` | DONE |
| P6 | Analytics | Paths (hub-penalised), ownership chains, cycles, shared addresses/directors, sanctions exposure, timeline, conflicts | Graph tests pass; hub path ranked below specific path | DONE |
| P7 | AI request manager | Central DB-backed budget: RPM+margin, concurrency 1, queue, dedup, cache, 429 cooldown, retries, daily budget, priorities | `tests/test_ai_manager.py` passes (RPM, concurrency, cache, dedup, 429, budget, deferral, multi-instance) | DONE |
| P8 | AI integration | Wrap your `auth.py`; Codex Responses invocation; grounded prompts; citation validation; deterministic fallback | Fake-provider tests pass; live call needs your login (see `AI_INTEGRATION.md`) | DONE (live call untested: needs your credentials) |
| P9 | API + analyst UI | FastAPI endpoints + Next.js dark workspace on real data | UI shows real records from the backend; no dead buttons | DONE (verified session 3) |

## Milestone 2 (next sessions)

| # | Phase | Objective | Acceptance criteria | Status |
|---|---|---|---|---|
| P10 | Saved investigations | API + UI over the existing `investigations` tables (add entities, notes, export) | Create, add item, and reload an investigation | DONE (session 3) |
| P11 | UK Companies House connector | Officers, PSC (beneficial owners), filings → real director/owner edges | Officers of a seeded company appear as `director_of` edges with evidence | PENDING (needs your free API key) |
| P12 | Contracts connector | USAspending.gov (no key) and/or UK Contracts Finder → `awarded_contract` edges | Contract recipients matched to entities through the review queue | PENDING |
| P13 | Documents & news | GDELT DOC API metadata, SEC EDGAR filings, regulator orders; store URL + hash + excerpt only | News items linked to entities with dedup by URL/hash | PENDING |
| P14 | ICIJ Offshore Leaks bulk import | Streamed CSV import (nodes/edges) with `reported` basis | Import resumable; edges marked as leak-derived and `reported` | PENDING |
| P15 | Scale & Postgres option | Port SQL to PostgreSQL (`tsvector`, `pg_trgm`) when >~50 GB or multi-user | Same test suite passes against Postgres | PENDING |
| P16 | Evaluation & demo script | Labelled match set, perf benchmarks, recorded demo walkthrough | `pytest -m perf` report + `DEMO.md` | PENDING |

## Task board (fine-grained)

- VERIFIED session 3: P2–P10 pass `pytest -q` (16 tests). Live Codex call still needs your login.
- Session-1 build order: settings/env loader → schema → HTTP client → GLEIF crawl + search → OpenSanctions download + 3-pass parse → normaliser → matcher + review → graph analytics → AI manager/providers/assistant/CLI → FastAPI → reports → tests → UI pages
- BLOCKED: live Codex call (requires `python -m app.ai.cli login` on your machine; values from your original `config.py` go into `backend/.env`)
