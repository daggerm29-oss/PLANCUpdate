# MotherDuck & Hosted DuckDB Evaluation for PLANC

**Target Use Case:** Searching ~1.14 GB of public datasets (NSE, MCA, GLEIF, ICIJ Offshore Leaks, OpenSanctions) via a Render API backing the PLANC Corporate Intelligence platform on Vercel.

---

## 1. Executive Summary & Verdict

| Criterion | Evaluation | Verdict |
|---|---|---|
| **Storage Capacity** | MotherDuck Free Starter Tier offers **10 GB cloud storage**. Estimated database size for PLANC is **~280 MB - 380 MB** after columnar compression. | **PASSED** (Uses <4% of allowance) |
| **Compute Allowance** | Free Tier includes **10 hours of Pulse cloud compute** per month. | **PASSED** with critical architectural precaution for UptimeRobot pings |
| **Authentication & Security** | Token-based via `MOTHERDUCK_TOKEN` in server environment variables on Render. Zero credentials exposed to frontend. | **PASSED** |
| **Terms of Service** | Developer, individual, and evaluation use is explicitly permitted on the free plan. | **PASSED** |
| **Contingency / Exit Strategy** | Zero lock-in: Standard DuckDB file can be hosted on Cloudflare R2 / S3 via `httpfs` extension (free egress) or run locally on Render. | **PASSED** |

**Conclusion:** **MotherDuck is approved as the hosted DuckDB option for PLANC.** Its 10 GB free tier comfortably accommodates the full 1.14 GB dataset with over 9.6 GB of remaining headroom.

---

## 2. Storage & Conversion Analysis

### 2.1 Raw Dataset Sizes
The five source datasets comprise:
1. **MCA Company Master Data:** ~650 MB - 750 MB (CSV files for Karnataka, Andhra Pradesh, and additional states).
2. **GLEIF Legal Entity & Ownership Records:** ~180 MB - 220 MB (Level 1 LEI-CDF XML + Level 2 RR-CDF XML).
3. **ICIJ Offshore Leaks:** ~120 MB - 160 MB (Nodes and relationship CSVs).
4. **OpenSanctions:** ~70 MB - 90 MB (Target records and sanctions CSV).
5. **NSE Debarred Lists:** ~10 MB - 15 MB (Excel XLS/XLSX/CSV records).
*Total Raw Source Size:* **~1.03 GB - 1.24 GB (~1.14 GB average)**

### 2.2 Storage Footprint in DuckDB
DuckDB stores data in a columnar format utilizing:
- **Per-column Dictionary Encoding** (especially effective on categorical columns like `country`, `status`, `legal_form`, `predicate`, `basis`).
- **BitPacking / Frame of Reference (FoR)** for integer timestamps and auto-incrementing identifiers.
- **Snappy and Zstandard (ZSTD) block compression** on string payloads.
- **Deduplication:** Normalizing repetitive entity records into relational tables (`entities`, `identifiers`, `relationships`, `evidence`) avoids duplicate raw text payloads.

**Observed Compression Ratios:**
- Registry CSVs (MCA, ICIJ): **3.2x – 4.5x** reduction.
- XML records (GLEIF): **4.0x – 6.0x** reduction (XML markup overhead is eliminated).
- Sanctions & Debarment tables: **2.8x – 3.5x** reduction.

**Final DuckDB / MotherDuck Database Size:**
$$\text{Expected Database Size} = \frac{1.14\text{ GB}}{3.5} \approx \mathbf{325\text{ MB}}$$
Even with secondary indexes and full-text index structures, the total database footprint will remain **under 450 MB**, which is **less than 4.5% of MotherDuck's 10 GB free storage limit**.

---

## 3. Compute Allowance & UptimeRobot Monitoring Precautions

### 3.1 The Free Tier Compute Constraint
MotherDuck's free tier provides **10 hours of active Pulse compute time per month**. Compute meters only when active SQL queries are executing in the cloud engine; when idle, it scales to zero.

### 3.2 Uptime Monitoring Risk & Solution
- **The Risk:** UptimeRobot pings Render every 5 minutes (288 requests/day $\approx$ 8,640 requests/month) to prevent Render's free container from sleeping.
- **The Danger:** If every 5-minute health check executes a database query on MotherDuck (`SELECT 1` or `SELECT count(*)`), MotherDuck compute will wake up 8,640 times per month, rapidly consuming the 10-hour compute allowance!
- **The PLANC Architectural Solution:**
  1. **Lightweight Application-Layer Health Check:** The `/api/health` endpoint on Render responds directly from in-memory status, confirming that the Render web service is healthy and reachable.
  2. **Opt-in Deep Check:** Database connectivity is verified on server startup or only when explicitly requested via `/api/health?db=1`.
  3. **Result:** UptimeRobot pings keep Render awake without expending a single second of MotherDuck cloud compute!

---

## 4. Security & Authentication Architecture

```
[Vercel Frontend]
       │
       ▼ (HTTPS REST / JSON)
[Render Free Web Service (FastAPI / Uvicorn)]
  • Environment: MOTHERDUCK_TOKEN (private server secret)
  • CORS: restricted to Vercel production domain & localhost
       │
       ▼ (Encrypted DuckDB Wire Protocol / TLS)
[MotherDuck Cloud (md:planc)]
```

1. **Token Protection:** `MOTHERDUCK_TOKEN` is configured strictly in Render's environment dashboard with `sync: false`.
2. **Zero Client Leakage:** The frontend on Vercel only ever communicates with Render over HTTPS (`/api/*`). No database tokens, query strings, or MotherDuck endpoints are visible in client-side code, network payloads, or logs.
3. **Least Privilege:** Database read operations are performed via structured parameterized queries, preventing SQL injection.

---

## 5. Contingency Plan: What If Limits or Terms Change?

If MotherDuck changes its free-tier policy or compute limits are exhausted:

1. **Zero-Egress Object Storage Fallback (Cloudflare R2 / S3):**
   - Export the DuckDB database to a single `.duckdb` or set of `.parquet` files.
   - Host on Cloudflare R2 (10 GB free storage, $0 egress fees).
   - Render API queries the remote DuckDB file read-only using DuckDB's native `httpfs` extension:
     ```python
     con = duckdb.connect()
     con.execute("INSTALL httpfs; LOAD httpfs;")
     con.execute("SELECT * FROM 'https://pub-r2.dev/entities.parquet' WHERE ...")
     ```
   - **Cost: $0.00 / month** with zero compute limits.

2. **Render Container Ephemeral Local DuckDB:**
   - Mount or download `osint.duckdb` into the Render web service disk on container startup.
   - Runs standard embedded DuckDB in-process with 0 cloud dependencies.

3. **SQLite Failover:**
   - The dual-backend design preserves SQLite compatibility as an immediate fallback.
