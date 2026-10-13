# API_SOURCES

**Verification legend:** ✅ verified this session (live request or current official page/search) · ⚠️ from prior knowledge, re-check the official page before relying on it.
Never commit keys. Put them only in `backend/.env` (gitignored); `backend/.env.example` holds placeholders.

**Kinds of access:** an **API** answers small queries over HTTP (e.g. one company). A **bulk dataset** is a downloadable file of the whole database (best for screening). A **website** is for humans and often forbids scraping. A **document** (PDF order, filing) must be downloaded and text-extracted, so store its URL + hash + excerpt, not every file.

## Integrated now

### 1. GLEIF — LEI records + Level 2 parent/child relationships ✅ (status: INTEGRATED)
- **Contains:** Legal Entity Identifier (LEI) records, plus legal name, other and transliterated names, legal/HQ address, registry ID (`registeredAs`), BICs, status, and dates. **Level 2:** direct/ultimate accounting-consolidation parents, or a *reporting exception* explaining why no parent is reported.
- **Site / docs:** https://www.gleif.org · API v1 https://api.gleif.org/api/v1 (Postman docs linked from https://www.gleif.org/en/lei-data/gleif-api).
- **Endpoints used:** `GET /lei-records/{lei}`, `GET /lei-records/{lei}/direct-parent-relationship`, `GET /lei-records/{lei}/direct-children?page[size]=100&page[number]=n`, `GET /lei-records?filter[fulltext]=…` (search). The `relationships.direct-parent.links` of each record tells us whether a `relationship-record` or a `reporting-exception` exists, so we never probe for 404s.
- **Key:** none. **Cost:** free. **Licence:** CC0 1.0. **Limit:** 60 requests/minute per user (documented). We default to `GLEIF_RPM=50`.
- **Formats:** JSON:API. **Bulk:** yes, the daily *Golden Copy* (CSV/XML/JSON, multi-GB), planned. **Incremental:** compare `registration.lastUpdateDate`. We hash payloads, so unchanged records are skipped.
- **Difficulty:** easy. **Sample:** `curl https://api.gleif.org/api/v1/lei-records/549300WE6TAF5EEWQS81`.
- **Storage:** raw JSON in `raw_records` (`lei:<LEI>`) → `entities` `gleif:<LEI>`; edges `direct_parent` / `ultimate_parent` (basis `documented`).
- **Run:** `python -m app.cli gleif-search "sberbank"` then `python -m app.cli gleif-crawl --lei 549300WE6TAF5EEWQS81 --depth 2`.

### 2. OpenSanctions — consolidated sanctions, PEPs, debarment ✅ (status: INTEGRATED, bulk)
- **Contains:** sanctioned companies, persons, vessels; aliases; identifiers (LEI, INN, OGRN, BIC, IMO, registration numbers); `Sanction` records (authority, program, dates); Ownership/Directorship/Family links.
- **Site:** https://www.opensanctions.org · data https://data.opensanctions.org/datasets/latest/<dataset>/index.json (follow redirects) → `resources[]` (`entities.ftm.json`, sha1 `checksum`).
- **Key:** none for bulk files. The hosted match API (api.opensanctions.org) needs a key and is paid for commercial use.
- **Licence:** CC BY-NC 4.0: free for non-commercial use (your final-year project) **with attribution**. Commercial use requires a data licence.
- **Formats:** FollowTheMoney JSON lines (recommended), CSV (flattened, discouraged), Senzing, nested JSON. **Update:** daily or more often. **Incremental:** `version` in index.json, so we skip unchanged versions plus per-entity hash.
- **Measured:** `us_ofac_sdn` version 20261011161004-omw has 72,408 entities (20,379 targets) in a 53 MB `entities.ftm.json`. The full `default` collection is several GB, so start with single datasets (`us_ofac_sdn`, `eu_fsf`, `gb_hmt_sanctions`, `un_sc_sanctions`).
- **Run:** `python -m app.cli opensanctions-load --dataset us_ofac_sdn`.
- **Storage:** raw FtM JSON per entity (`raw_records`) → `entities` `os:<id>`, Sanction details folded into `attrs.sanctions` + `evidence`, link schemas → edges.

## Recommended next (free)

### 3. UK Companies House ⚠️ (status: PLANNED, P11)
- **Contains:** UK company profile, officers (directors), persons with significant control (beneficial owners), filing history, insolvency.
- **Docs:** https://developer.company-information.service.gov.uk/ · API base `https://api.company-information.service.gov.uk`.
- **Getting a key (beginner steps):** 1) Open the developer site, then *Register* (email + password, then verify email). 2) *Your applications*, then *Create an application* (name it, choose **Live** environment). 3) *Create new key*, then **REST** key type (no IP restriction needed for local use). 4) Copy the key into `backend/.env` as `COMPANIES_HOUSE_API_KEY=...`. 5) Test: `curl -u "$COMPANIES_HOUSE_API_KEY:" https://api.company-information.service.gov.uk/company/00445790`. The key is the *username*, and the password is empty. 6) Errors: 401 means a wrong key or the wrong environment; 429 means wait (limit ⚠️ 600 requests / 5 minutes).
- **Bulk:** free monthly *Basic Company Data* CSV and PSC snapshot (JSON lines) at http://download.companieshouse.gov.uk/. **Licence:** Open Government Licence ⚠️.

### 4. USAspending.gov (US federal contracts) ⚠️ (status: PLANNED, P12)
- **API:** https://api.usaspending.gov (no key). Example: `POST /api/v2/search/spending_by_award/` with filters. Recipient UEI/name maps to `uei` identifiers (OpenSanctions also carries `uniqueEntityId`). Public domain.

### 5. UK Contracts Finder / Find a Tender ⚠️ (P12 alternative)
- Contracts Finder API https://www.contractsfinder.service.gov.uk/apidocumentation (search without key); Find a Tender publishes OCDS. OGL licence.

### 6. SEC EDGAR ⚠️ (P13)
- https://www.sec.gov/edgar/sec-api-documentation: `https://data.sec.gov/submissions/CIK##########.json`. No key, but you **must** send a descriptive `User-Agent` with your email, at max 10 requests/second. Bulk nightly `submissions.zip`. Public domain.

### 7. ICIJ Offshore Leaks ⚠️ (P14)
- https://offshoreleaks.icij.org/pages/database: bulk CSV (nodes + relationships) download. Licence ODbL (database) + CC BY-SA (contents). Must be labelled **leaked/reported**, never documented fact, and presence in it does not imply wrongdoing (ICIJ's own disclaimer).

### 8. GDELT DOC 2.0 ⚠️ (P13)
- `https://api.gdeltproject.org/api/v2/doc/doc?query=…&mode=artlist&format=json`, no key, free. Returns article metadata/URLs only. Store URL + title + date + hash, never full articles (publisher copyright).

## Deferred / restricted
| Source | Why deferred |
|---|---|
| OpenCorporates ✅ | API token **required**. Free share-alike tokens are only for permitted public-benefit users (journalists, NGOs, academics) **on application** via their service desk. Apply early; GLEIF + Companies House cover the gap. |
| India MCA21 company master data ⚠️ | Per-company lookup on mca.gov.in has CAPTCHAs (no scraping). data.gov.in hosts some RoC company master CSVs (free, data.gov.in API key on registration). Verify freshness before use. |
| India procurement (CPPP eprocure.gov.in, GeM) ⚠️ | Website listings, no documented public API. Respect robots/terms. Manual CSV export only. |
| SEBI / RBI orders ⚠️ | Published as PDFs on sebi.gov.in / rbi.org.in. P13 document pipeline (download on demand, hash, extract excerpt). |
| Court judgments (eCourts, Indian Kanoon) ⚠️ | eCourts uses CAPTCHAs. Indian Kanoon API is paid per request. Deferred. |
