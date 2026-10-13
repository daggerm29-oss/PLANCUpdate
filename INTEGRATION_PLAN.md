# INTEGRATION PLAN — India corporate intelligence (PLANC + crawler + datasets)

Written 2026-10-12, before any code changed. Every finding below was observed in the code or the files.

## Phase 1 findings

### PLANC (main app)
- Python 3.11+ FastAPI + SQLite/FTS5 backend (`backend/app`), Next.js 16 UI that rewrites `/api/*` to `OSINT_API_URL`.
- Baseline: `pytest -q` → **16 passed** in this sandbox (Python 3.13) before any change.
- Strong existing pieces we reuse instead of duplicating: `HttpClient` (timeouts, bounded backoff, Retry-After, RPM),
  `upsert_raw` (content-hash change detection + history), `upsert_entity/relationship`, `evidence`,
  `match_candidates` + non-destructive `same_as`, leads, and a DB-backed AI budget manager.
- Gaps: no MCA / ICIJ / NSE importers, no GLEIF *file* importer (API only), OpenSanctions only as FtM JSON;
  `cin`, `pan`, `din` are not identifier schemes; search does an exact identifier lookup without normalisation;
  `evidence` has no extraction method or confidence; nothing separates unverified claims from facts.
- Docs say `/health`; code serves `/api/health`. Code wins.

### Crawler (`corporate-intelligence-bridge`)
- Flask `app.py`: `GET /health`, `POST /google_and_deep_scrape {query}` → `{target_query, source_url, company_metadata (Markdown), directors_network[], acquisitions_count}`.
  The Node `server.ts` mirrors it for a Vite demo UI; not needed by PLANC (would add a second runtime).
- Each call spends **1–2 Serper searches plus up to 2 Serper scrapes** (scrapes only when a direct fetch fails). No cache, no dedup, no rate limit inside the crawler.
- Output-quality problems the integration must not turn into "facts":
  1. `Company Status | Active` is **hard-coded** in the registry table.
  2. `Disclosed` is a placeholder when the CIN or capital regex finds nothing.
  3. The CIN is the **first CIN anywhere on the registry page**. For `Wabtec Corporation` (US parent) the docs show the CIN of *Wabtec India Industrial Pvt Ltd* (Indian subsidiary).
  4. Every director gets the **same** `associated_companies` table (regex over the whole page), and its `Status` column is hard-coded `Active`.
  5. Placeholder director `Key Managerial Personnel / Corporate Network` and placeholder row `Statutory Directorships`.
- Terms/access: it fetches LinkedIn and Zaubacorp with browser-impersonating headers and falls back to Serper's scrape proxy when blocked (Cloudflare challenge). LinkedIn's user agreement prohibits scraping. PLANC cannot control which URLs the crawler fetches, so PLANC labels claims by source domain and keeps them unverified; changing the crawler's fetch policy is a crawler-side decision (see "Open items").

### Samples (`samples.zip`) — real schemas
| File | Format | Rows | Key columns |
|---|---|---|---|
| `datasets_mca_*.csv.sample.csv` | CSV, UTF-8 BOM | 100 + 100 | CIN, Company Name, Registration Date, Category/Class/Listing, Authorized/Paidup Capital, ROC, Address, Pin Code (**blank**; PIN is inside the address), State, Status, Sub Category, Industrial Classification |
| `gleif_level1_sample.xml` | LEI-CDF 2016 XML | 20 (all NG) | LEI, LegalName, Legal/HQ address, RegistrationAuthority (ID + EntityID), LegalForm, Status, Registration |
| `gleif_level2_sample.xml` | RR-CDF 2016 XML | 20 | StartNode (child) → EndNode (parent), `IS_DIRECTLY/ULTIMATELY_CONSOLIDATED_BY`, periods, quantifiers. **No endpoint is in the L1 sample.** |
| `full-oldb.LATEST_*.csv.sample.csv` (ICIJ) | CSV | 100 each | entities / officers / intermediaries / addresses / others nodes + relationships (`registered_address`, `same_as`). **No relationship endpoint is in the node samples.** Countries are ISO alpha-3. |
| `datasets_opensanctions_targets.simple.csv.sample.csv` | CSV | 100 | id, schema, name, aliases, countries, identifiers (unlabelled, `;`-separated), sanctions, dataset, first/last seen. No topics, no edges. |
| `datasets_nse_prs_ra_others.xls` | **XLSX** despite the name | 4,444 | Order Date (text, e.g. `16-JUl-2026`), Order Particulars, Name, PAN, DIN/CIN, Symbol, Period, circulars |
| `datasets_nse_prs_ra_sebi.xls` | real BIFF XLS | 11,787 | same columns; dates are Excel serials; DIN stored as float (`6615175.0` → `06615175`) |

The NSE files are complete lists; the others are samples. **The samples do not overlap** (no shared CIN/LEI/name), so cross-source joins can only be shown with full files or targeted fixtures.

## Plan

1. **Import** PLANC into the v0 project; vendor the crawler unchanged under `services/crawler/` (Flask only).
2. **Crawler client** (`connectors/crawler.py`): env config (`CRAWLER_URL`, timeout, RPM, daily budget, cache TTL), health check, documented contract only.
   Efficiency: query cache (positive TTL, short negative TTL, errors not cached), canonical URL dedup for pages, in-process + DB lease so concurrent identical requests make one call, DB-ledger RPM + daily limit, retries only on network/429/502/503/504 (never on 500, which may already have spent credits), usage ledger with an upper-bound Serper credit estimate.
3. **Claims** (`claims` table): every material crawler statement is stored with source URL, retrieval time, evidence excerpt, extraction method, confidence and status `unverified | verified | conflict`. Known placeholders are dropped. Claims never create graph edges on their own.
4. **Verification**: a crawled CIN is *verified* only when an MCA/GLEIF record with that CIN exists **and** its name matches the subject. If the CIN exists under a different name it is recorded as a *related entity* lead (possible subsidiary/affiliate), never merged into the parent.
5. **Importers**: MCA CSV, GLEIF L1/L2 XML (streamed; L2 keeps child→parent direction, missing endpoints become clearly marked stubs), ICIJ CSV set, NSE XLS/XLSX, OpenSanctions simple CSV. All go through `upsert_raw` (idempotent, change-detected) and add `cin`/`pan`/`din` identifiers.
6. **Search & resolution**: detect CIN / LEI / DIN / PAN queries, normalise identifiers, add `cin`, `pan`, `din` to auto-join schemes, leads for NSE debarments, and caveats that shared addresses / directors / offshore records are not evidence of wrongdoing.
7. **UI**: an Intelligence view (query → verified facts vs unverified leads vs connections) and crawler health/usage in Sources.
8. **Verify**: existing tests, importer tests on the real samples, crawler integration test that runs the vendored Flask app with Serper mocked, then **one** limited live call.

## Open items for the user
- `SERPER_API_KEY` (only needed for the live crawler test; requested in chat, stored as a project env var).
- Whether the crawler should stop fetching LinkedIn / bypassing challenges (crawler-side change, not made).
- Full MCA state files and GLEIF Golden Copy for real cross-source joins.
