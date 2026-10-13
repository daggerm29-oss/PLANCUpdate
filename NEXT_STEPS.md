# NEXT STEPS

**Session 6 fixed AI-limit issues #1 and #2 (Windows step 5 is DONE for code; just rerun the tests). Read SESSION_LOG.md first.**
After a fresh download, ALWAYS run `python -m app.cli samples-load ../samples.zip` first. The committed DB holds demo data only.
Remaining, in order:
1. DONE in session 6 (~2 credits; rerun was a 0-credit cache hit). Original step: ONE live crawl (spends at most 4 Serper credits): start the crawler with `SERPER_API_KEY`, set `CRAWLER_URL`, run
   `python -m app.cli investigate "Wabtec Corporation" --crawl` once, then `crawler-usage`. Re-running it should be a cache hit (0 credits).
2. Load full files (one MCA state CSV, the GLEIF Golden Copy for India) to see real cross-source joins.
3. Decide whether the crawler should stop fetching LinkedIn / bypassing challenges (crawler-side change).
4. Then continue with the Windows plan below.

**Current phase: get the project fully working on the user's Windows PC, one verified step at a time.**
No new features until this is finished. P11 (Companies House) is paused; its task list is kept below.
Record each step's real output in `PROJECT_STATE.md` before moving to the next one.

## Windows verification plan (PowerShell, run inside the downloaded project folder)
Use `.\.venv\Scripts\python.exe` directly, so you never need `Activate.ps1` or to change PowerShell's execution policy.

| # | Step | Proves | Status |
|---|---|---|---|
| 1 | Check Python 3.11+ and Node 20.9+. Make a fresh `backend\.venv`, install `requirements.txt`, run `pytest -q` | The Python toolchain works on Windows | **CURRENT** |
| 2 | `copy backend\.env.example backend\.env`. Start `uvicorn app.api:app --port 8000`, open `/api/health`. Install pnpm, `pnpm install`, `pnpm dev`, open http://localhost:3000 with demo data | The backend and UI run locally | todo |
| 3 | Real data: `gleif-search` → `gleif-crawl <LEI>`, then `os-load --dataset us_ofac_sdn`, `screen`, `stats` → find the records in the UI | Real records are stored and shown | todo |
| 4 | Remove demo data (`demo --remove`); confirm `stats` and the UI show only real sources | No synthetic data is mixed in | todo |
| 5 | Fix the 2 AI-limit issues in `PROJECT_STATE.md` (only with the user's approval), then rerun the tests | The limits hold in every code path | todo |
| 6 | Fill the `CODEX_*` values from the original config.py → `app.ai.cli status` → `login` → `status` | OAuth login and token storage | todo |
| 7 | Exactly one `app.ai.cli ping`, then `status` (`requests_last_60s` = 1) and the request log | A real prompt gets a real response | todo |

## Proposed: India integration (P-IN). Plan only, waiting for the user's go-ahead. Facts checked 2026-10-12
- Sources: MCA Company Master Data (data.gov.in, NDSAP/GODL, attribution required; data.gov.in refuses connections from the v0 sandbox, so the user downloads it). GLEIF: 401,956 India LEIs; companies carry the CIN in `registeredAs` (RA000394); sole proprietors carry a GSTIN. OpenSanctions (CC BY-NC 4.0, non-commercial only): `in_nse_debarred` (30,790 entities, PAN in `taxNumber`), `in_mha_banned` (308), `un_sc_sanctions`, `sanctions` (367 MB).
- Gaps found: no MCA connector; `cin` is not in `AUTO_SCHEMES` (GLEIF stores CIN/GSTIN as generic `registration_number`, so matches score 0.9 and wait for review); `normalize_name` keeps "private", "pvt", "m s"; the `screen` CLI has no `--source/--target`.
- Build order: back up DB → `connectors/mca.py` + `mca-load --file` → CIN/GSTIN/PAN identifier tagging → normaliser fixes → `screen --source mca` → profile fields → tests with a saved sample CSV.
- First user action: back up `osint.db` and download ONE state's Company Master Data file; send the header row + file size + "Updated On".

## Paused: P11 Companies House connector
1. Free key: https://developer.company-information.service.gov.uk/ → Register → "Your applications" → Create → REST API key → `COMPANIES_HOUSE_API_KEY=` in `backend/.env` (HTTP Basic auth: key as username, empty password; 600 requests per 5 minutes).
2. Add a `companies_house` source row in `db.SOURCES` and a setting in `settings.py`.
3. `GET /company/{number}`, `/officers`, `/persons-with-significant-control` → raw records + `director_of` / `owns` edges (`basis=documented`) with evidence URLs.
4. `registration_number` (country `gb`) identifiers so the matcher can join them to GLEIF.
5. CLI `ch-load <company_number>` plus tests using a saved JSON fixture (no network).

After P11: P12 contracts (USAspending), P13 news/filings, P14 ICIJ, P16 evaluation + `DEMO.md`.
