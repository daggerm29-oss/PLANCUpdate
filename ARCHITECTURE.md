# ARCHITECTURE

Living document. Last updated: 2026-10-11 (session 1).

## 1. System overview

```
PUBLIC SOURCES                BACKEND  (Python 3.11+, FastAPI, SQLite)                          FRONTEND (Next.js 16)
GLEIF API (LEI + Level 2) ─┐  backend/app/connectors/*  ── shared HTTP client (timeouts,        app/*  React 19 + TS +
OpenSanctions bulk (FtM) ──┤   bounded exp. backoff + jitter, Retry-After, per-source RPM)      Tailwind v4 + SWR
Companies House (planned) ─┤        │                                                               │
ICIJ / SEC / contracts ────┘        ▼                                                               │ fetch /api/backend/*
   (planned)                 raw_records  (original payload, sha256 content hash, URL)             │ (Next rewrite → FastAPI,
                                    ▼                                                               │  server-side, no CORS)
                             normalisers → entities · entity_names · identifiers ·                  ▼
                                           relationships · evidence  (+ FTS5 index)        FastAPI  app.api:app
                                    ▼                                                      127.0.0.1:8000
                             resolution/ identifier match → fuzzy (RapidFuzz) → review queue
                                    ▼          (same_as links, never destructive merges)
                             graph/ paths (hub-penalised Dijkstra) · ownership chains ·
                                    cycles (Tarjan SCC) · shared addresses · sanctions exposure
                                    ▼
                             ai/ assistant (evidence pack → prompt → citation validation)
                                    └─► ai/manager (ONE central, DB-backed request budget)
                                            └─► CodexProvider → legacy_ai/auth.py (your unchanged OAuth code)
```

Core rule: **deterministic code does all searching, matching, and graph traversal.** The LLM only writes
explanations over a small evidence pack that was already retrieved, and every claim it makes has to cite an evidence ID.

## 2. Components

| Component | Location | Responsibility |
|---|---|---|
| Settings | `backend/app/settings.py` | Loads `backend/.env` (tiny parser, no extra dependency) and exposes typed settings |
| DB layer | `backend/app/db.py`, `schema.sql` | SQLite connection (WAL, FK, busy timeout), schema init, transaction helper, upsert helpers |
| HTTP client | `backend/app/http.py` | Timeouts, retries (429/5xx/network), bounded exponential backoff with full jitter, Retry-After, min-interval rate limit |
| Connectors | `backend/app/connectors/` | `gleif.py` (API crawl of LEI + parent/child relationships), `opensanctions.py` (versioned bulk FtM download + 2-pass parse) |
| Resolution | `backend/app/resolution/` | `normalize.py` (names, legal forms, addresses, identifiers), `matcher.py` (identifier and fuzzy screening into `match_candidates`) |
| Graph/analytics | `backend/app/graph/` | `paths.py` (k evidence-backed paths, hub penalty, ownership chain), `analytics.py` (cycles, shared addresses, exposure leads) |
| AI | `backend/app/ai/` | `manager.py` (central budget/queue/cache), `providers.py` (Codex, Fake, Disabled), `assistant.py` (grounded prompts + validation), `cli.py` |
| Legacy auth | `backend/legacy_ai/` | `auth.py` copied **verbatim** from your file, plus a `config.py` shim that reads the same names from `.env` |
| API | `backend/app/api.py` | FastAPI endpoints (see §5) |
| CLI | `backend/app/cli.py` | `init-db`, `gleif-crawl`, `opensanctions-load`, `screen`, `stats` |
| Frontend | `app/`, `components/osint/`, `lib/api.ts` | Analyst workspace: search, entity profile, graph, evidence inspector, path finder, review queue, leads, system monitor |

## 3. Database design (SQLite now, PostgreSQL-portable)

| Table | Purpose | Key points |
|---|---|---|
| `sources` | Registry of data sources (licence, homepage) | Seeded on `init-db` |
| `ingest_runs` | One row per connector run | status, params, stats JSON (seen/new/changed/unchanged/errors) |
| `raw_records` | Original source payload | `UNIQUE(source_id, record_key)`, `content_hash` → unchanged records are skipped |
| `entities` | Normalised entity per source record (`gleif:<LEI>`, `os:<id>`) | kind, name, `name_norm`, country, status, address, `address_norm`, topics, attrs JSON, `raw_record_id` |
| `entity_names` | Legal names, aliases, previous names | indexed by `name_norm` |
| `identifiers` | (scheme, value), e.g. lei, bic, registration_number, inn | indexed `(scheme, value)` for exact joins |
| `relationships` | Edge subject → predicate → object | `basis` documented/reported/inferred, confidence, dates, status, `UNIQUE(source_id, subject, predicate, object)` |
| `evidence` | Why an entity or edge exists | source, URL, excerpt, `raw_record_id`, retrieved_at, unique `key` |
| `match_candidates` | Possible same entity / sanctions hits | method, score, features, status pending/confirmed/rejected, reviewer note |
| `entity_search` | FTS5 index (names + identifiers) | unicode61 with diacritics removed, prefix queries |
| `ai_request_log` | Every model attempt (no secrets) | used for RPM window, concurrency lease, daily budget, and monitoring |
| `ai_cache` | Prompt/response cache | key = sha256(model, task, system, prompt, evidence fingerprint), TTL |
| `ai_state`, `ai_counters` | 429 cooldown, cache hits, deduplications, rejections | shared across processes |
| `investigations`, `investigation_items` | Saved investigations | schema ready; API/UI in phase P10 |

Predicates (subject → object): `direct_parent`, `ultimate_parent` (GLEIF accounting consolidation),
`owns`, `director_of`, `employed_by`, `member_of`, `associate_of`, `family_of`, `represents`, `linked_to`
(OpenSanctions FtM), `same_as` (resolver, basis = inferred).

**Original vs normalised:** raw payloads are never modified. Normalised rows point back to them through `raw_record_id`,
so a record can always be re-normalised later without downloading it again.

## 4. Data flow

1. **Ingest** (CLI): the connector fetches data and calls `upsert_raw()`. A record whose hash is unchanged just has its `last_fetched_at` updated and is not re-normalised.
2. **Normalise**: `upsert_entity()`, `upsert_relationship()`, and `add_evidence()` write rows idempotently with `ON CONFLICT` upserts.
3. **Resolve** (`screen`): matches exact identifiers first (LEI, BIC, registration number + same country). Fuzzy name candidates come from FTS and are scored with RapidFuzz. Everything goes into `match_candidates`. Identifier matches and analyst-confirmed matches produce a `same_as` edge (basis `inferred`). Rejecting a match removes its edge.
4. **Query**: the API serves search, profiles, edges with evidence, paths, ownership chains, and leads.
5. **AI (optional)**: the assistant builds an evidence pack (facts labelled `[E1]..[En]`) and computes a fingerprint. The request goes through the manager (cache, then budget, then provider), and the response is validated against the pack. Invalid citations are dropped and reported. If the AI is unavailable, the API returns a deterministic explanation instead.

## 5. Service interfaces (FastAPI, `backend/app/api.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | DB counts + AI availability |
| GET | `/search?q=&kind=&limit=` | FTS entity search (prefix, diacritic-insensitive) with flags |
| GET | `/entities/{id}` | Profile: names, identifiers, topics, entity evidence, match candidates |
| GET | `/entities/{id}/relationships` | Edges with counterpart summary + evidence |
| GET | `/entities/{id}/graph` | Ego network (nodes/edges) for visualisation |
| GET | `/entities/{id}/ownership?direction=up\|down` | Ownership/consolidation chain |
| GET | `/relationships/{rel_id}` | Evidence inspector for one edge |
| GET | `/raw/{raw_id}` | Original source record (document viewer) |
| GET | `/paths?a=&b=&k=3&max_depth=6` | k shortest evidence-backed paths with hubs penalised |
| GET | `/matches?status=pending` · POST `/matches/{id}/review` | Review queue |
| GET | `/leads` | Sanctions exposure, circular ownership, shared registered addresses |
| GET | `/sources` | Sources, counts, latest ingest runs |
| GET | `/ai/status` | Budget, usage, queue, cooldown, cache efficiency, provider state |
| POST | `/ai/explain-path` · `/ai/entity-brief` | Grounded AI output, or deterministic fallback |

The frontend calls these as `/api/backend/<path>` through the rewrite in `next.config.mjs` (`BACKEND_URL`, default `http://127.0.0.1:8000`).

## 6. Repository structure

```
/                         Next.js frontend (v0 project root) + progress docs (*.md)
  app/                    routes: / (search), /entity/[id], /paths, /review, /leads, /system
  components/osint/       analyst UI components
  lib/api.ts              typed fetch helpers + SWR fetcher
backend/
  app/                    settings, db, schema.sql, http, api, cli
    connectors/           gleif.py, opensanctions.py
    resolution/           normalize.py, matcher.py
    graph/                paths.py, analytics.py
    ai/                   manager.py, providers.py, assistant.py, cli.py
  legacy_ai/              auth.py (verbatim) + config.py (env shim)
  tests/                  pytest suite + fixtures/
  data/                   (gitignored) osint.db, raw/ bulk downloads
  .secrets/               (gitignored) OAuth token file
  requirements.txt, .env.example
```

## 7. Scaling path (no rebuild required)

- **SQLite → PostgreSQL**: all SQL lives in `db.py`, connectors, resolution, and graph. It uses standard `ON CONFLICT`, `WITH RECURSIVE`, and `RETURNING`, which PostgreSQL also supports. Porting means swapping the placeholder style (`?` → `%s`), replacing FTS5 with `tsvector` + `pg_trgm`, and replacing `json_extract` with JSONB operators. Switch when you need multiple concurrent writers or analysts, or more than about 50 GB.
- **Bulk sources** (GLEIF Golden Copy, ICIJ CSV, Companies House snapshots): stream file → batch upsert (1,000 rows per transaction) and never load whole files into memory. Keep the downloaded file under `data/raw/<source>/<version>/` and store `file#line` as the `record_key` instead of duplicating huge payloads.
- **Graph DB (Neo4j/Memgraph)**: optional later, for interactive multi-hop queries over more than about 10M edges. You'd export from `relationships`, and SQL stays the system of record.
