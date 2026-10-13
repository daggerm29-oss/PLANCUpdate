# PLANC: Hosted DuckDB (MotherDuck) & Render API Deployment Guide

This guide walks you through deploying the **PLANC Indian Corporate Intelligence Platform** using the target architecture:

```
[PLANC Frontend on Vercel]
           │
           ▼ (HTTPS REST / JSON)
[Render API (Python / FastAPI Web Service)]
           │
           ▼ (Encrypted TLS / DuckDB Wire Protocol)
[Hosted DuckDB on MotherDuck (md:planc)]
```

---

## 1. MotherDuck Database Setup

1. **Sign Up:** Create a free account at [motherduck.com](https://motherduck.com).
   - Free Starter Tier includes **10 GB cloud storage** and **10 hours of Pulse cloud compute** per month.
   - For our ~1.14 GB public datasets, DuckDB's columnar compression reduces storage to **~325 MB** (<4% of your free allowance).
2. **Generate Token:**
   - In MotherDuck, click on your profile $\rightarrow$ **Settings** $\rightarrow$ **Access Tokens**.
   - Create a new token and copy it. This will be your `MOTHERDUCK_TOKEN`.
3. **Database:**
   - Default database name is `planc`.

---

## 2. Importing Datasets into MotherDuck

We provide a standalone streaming & batch import pipeline in `backend/app/duckdb_cli.py` and `backend/app/importers/duckdb_import.py`.

### A. Test with Sample Datasets
To test the pipeline locally before running against the full datasets:
```bash
cd backend
python -m venv .venv
# On Windows: .venv\Scripts\activate
# On Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# Test import samples into local DuckDB
python -m app.duckdb_cli import --samples ../samples.zip --target local

# View table row counts
python -m app.duckdb_cli stats --target local
```

### B. Import Full Datasets into MotherDuck
When you are ready to upload the 1.14 GB public datasets (MCA, GLEIF, ICIJ, OpenSanctions, NSE):
```bash
cd backend

# Option 1: Set environment variable
export MOTHERDUCK_TOKEN="your_motherduck_token_here"
# (Windows PowerShell: $env:MOTHERDUCK_TOKEN="your_motherduck_token_here")

# Run streaming batch import
python -m app.duckdb_cli import --dir /path/to/public_datasets --target motherduck

# Option 2: Pass token directly
python -m app.duckdb_cli import --dir /path/to/public_datasets --target motherduck --token "your_token"
```

### Pipeline Guarantees:
- **Streaming & Constant Memory:** Large XML files (GLEIF Level 1 and Level 2) are parsed with `iterparse` and cleared element-by-element so RAM remains under 50 MB.
- **Idempotency & Resumability:** Uses `INSERT OR REPLACE` and `ingest_runs` tracking. Safe to re-run anytime without creating duplicate records.
- **Provenance:** Preserves source identifiers (CIN, LEI, PAN, DIN, IMO), source URLs, dataset names, and retrieval timestamps.

---

## 3. Deploying the API to Render

We have provided a complete `render.yaml` blueprint at the root of the repository.

### Method 1: Render Dashboard
1. Go to [dashboard.render.com](https://dashboard.render.com) and click **New +** $\rightarrow$ **Web Service**.
2. Connect your GitHub repository (`daggerm29-oss/PLANC`).
3. Configure the settings:
   - **Name:** `planc-api`
   - **Region:** `Oregon` (or closest to your users)
   - **Branch:** `main`
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r backend/requirements.txt`
   - **Start Command:** `cd backend && uvicorn app.api:app --host 0.0.0.0 --port $PORT --workers 2`
   - **Plan:** `Free`
4. Set **Environment Variables**:
   - `DB_BACKEND`: `motherduck`
   - `MOTHERDUCK_TOKEN`: `<your-motherduck-token>`
   - `MOTHERDUCK_DATABASE`: `planc`
   - `CORS_ORIGINS`: `https://*.vercel.app,http://localhost:3000`
   - `PYTHON_VERSION`: `3.11.8`
5. Click **Create Web Service**. Render will deploy your API and assign you a URL like:
   `https://planc-api.onrender.com`

---

## 4. Configuring UptimeRobot Monitoring

Render's free web services spin down after 15 minutes of inactivity. To keep the service warm:

1. Create a free monitor at [uptimerobot.com](https://uptimerobot.com).
2. Configure a new monitor:
   - **Monitor Type:** `HTTP(s)`
   - **Friendly Name:** `PLANC API Render Ping`
   - **URL (or IP):** `https://planc-api.onrender.com/api/health`
   - **Monitoring Interval:** `Every 5 minutes`
3. **MotherDuck Compute Preservation:**
   - The `/api/health` endpoint is specifically engineered to respond immediately in-memory with status `200 OK`.
   - It **does not execute queries against MotherDuck** on regular health pings (`uptime_ping_safe: true`), ensuring that 8,640 monthly UptimeRobot pings consume **0 seconds of your MotherDuck compute allowance**!
   - If you ever want to perform a deep database health check manually, query `/api/health?db=1`.

---

## 5. Connecting the Vercel Frontend

1. Go to your PLANC project on [vercel.com](https://vercel.com) $\rightarrow$ **Settings** $\rightarrow$ **Environment Variables**.
2. Add the following environment variable:
   - **Key:** `OSINT_API_URL`
   - **Value:** `https://planc-api.onrender.com` (your Render service URL)
3. Redeploy your Vercel project.
4. **How it works:**
   - `next.config.mjs` contains automatic rewrites that route all browser calls to `/api/:path*` directly to your Render API over HTTPS.
   - The frontend code never touches MotherDuck credentials.
   - The UI's **Data store** panel will display:
     `Engine: MotherDuck (hosted)` along with live row counts and sources.
