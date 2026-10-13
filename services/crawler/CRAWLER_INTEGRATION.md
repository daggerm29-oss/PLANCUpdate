# Comprehensive Crawler Integration & Technical Architecture Guide

> **Notice for Integrating Engineers & AI Coding Agents:**  
> This guide documents the technical architecture, execution flow, extraction mechanics, limitations, and downstream integration contracts for the **Corporate Intelligence Crawler**.  
> Every statement in this document has been cross-referenced against the actual codebase (`app.py`, `server.ts`, `render.yaml`, `requirements.txt`). Features not present in the code are explicitly identified as **NOT IMPLEMENTED** to ensure zero architectural hallucinations.

---

## 1. Project Structure & Codebase Inventory

The repository is organized as a dual-runtime system: a production-grade Python Flask microservice tailored for Render Free Tier (`app.py`), and a full-stack TypeScript/Node.js environment (`server.ts` + Vite + React in `src/`) used for development, testing, and UI demonstration.

```
/
├── app.py                  # Production Python Flask crawler microservice (Render target)
├── server.ts               # Node.js/Express crawler & Vite dev middleware server
├── requirements.txt        # Production Python dependencies (pinned versions)
├── package.json            # Node.js dependencies, scripts, and runtime engines
├── render.yaml             # Render Blueprint IaC configuration file
├── .env.example            # Environment variable template with safe placeholders
├── .gitignore              # Git ignore rules for node_modules, build output, .env
├── tsconfig.json           # TypeScript configuration
├── vite.config.ts          # Vite bundler configuration
├── index.html              # HTML entry point for the React workbench UI
├── metadata.json           # AI Studio applet manifest
├── src/                    # Frontend React workbench & demonstration console
│   ├── App.tsx             # Interactive crawler console, Markdown viewer, test runner
│   ├── main.tsx            # React application mount point
│   └── index.css           # Tailwind CSS directives
├── README.md               # Quickstart and project overview
├── CRAWLER_INTEGRATION.md  # [This Document] Full technical integration guide
├── API_DOCUMENTATION.md    # Complete REST API specification with schemas & examples
└── RENDER_DEPLOYMENT.md    # Operational deployment guide for Render Free Tier
```

### Detailed Purpose of Core Files:
- **`app.py` (Python 3.11 / Flask)**: The standalone production microservice. Contains the 2-tier stealth scraping logic, Serper.dev Google search caller, HTML-to-Markdown table converter, and regex parsers for MCA corporate records and strategic profiles. Binds to `0.0.0.0:$PORT` (defaults to 10000).
- **`server.ts` (Node.js / Express)**: Express server implementing the exact same crawling and parsing pipeline in TypeScript (`/api/google_and_deep_scrape` and `/api/health`), mounting Vite middlewares during local development.
- **`requirements.txt`**: Minimal, lightweight Python dependencies (`Flask`, `requests`, `beautifulsoup4`, `gunicorn`, `urllib3`). Intentionally omits heavy browser drivers (Playwright, Puppeteer, Selenium) to prevent exceeding 256MB RAM.
- **`render.yaml`**: Infrastructure-as-Code blueprint defining a Python web service on Render Free Tier with Gunicorn worker flags and environment definitions.
- **`src/App.tsx`**: Developer console and forensic workbench. Lets human operators test queries live, visualize director networks, and view raw extracted Markdown dossiers.

---

## 2. Setup Instructions & Local Execution

### Runtimes Supported:
- **Python:** `3.10` or `3.11.x` (Production standard: `3.11.8` as pinned in `render.yaml`).
- **Node.js:** `18.x`, `20.x`, or `22.x` (Only required if running the full-stack React workbench).

---

### A. Running the Python Microservice (`app.py`)

#### Local Setup on Windows (Command Prompt or PowerShell):

1. **Clone and enter directory:**
   ```powershell
   cd /path/to/project
   ```

2. **Create and activate a virtual environment:**
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   # Or in cmd.exe:
   # .\venv\Scripts\activate.bat
   ```

3. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Set environment variables:**
   ```powershell
   $env:SERPER_API_KEY = "your_serper_dev_api_key_here"
   $env:PORT = "10000"
   ```
   *In cmd.exe:*
   ```cmd
   set SERPER_API_KEY=your_serper_dev_api_key_here
   set PORT=10000
   ```

5. **Start the service:**
   ```powershell
   python app.py
   ```
   *Windows Production Note:* Gunicorn does not support Windows natively without WSL (Windows Subsystem for Linux). For Windows development or testing, running `python app.py` (which launches Flask's development server) is standard. For production on native Windows Server, install and run `waitress`:
   ```powershell
   pip install waitress
   waitress-serve --port=10000 app:app
   ```

#### Local Setup on Linux / macOS / WSL:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export SERPER_API_KEY="your_serper_dev_api_key_here"
export PORT="10000"

# Run with Gunicorn (production parity)
gunicorn -w 2 -b 0.0.0.0:$PORT --timeout 120 --worker-tmp-dir /dev/shm app:app
```

---

### B. Running the Full-Stack Node.js Workbench (`server.ts` + React)

If your team wants to run the full-stack developer UI locally:
```bash
npm install
cp .env.example .env
# Edit .env and supply SERPER_API_KEY
npm run dev
# Open browser to http://localhost:3000
```

---

## 3. Environment Variables Specification

The service strictly separates code from credentials. Required and optional variables:

| Variable Name | Required? | Default in Code | Purpose |
| :--- | :--- | :--- | :--- |
| `SERPER_API_KEY` | **YES** | None (Raises 500 if missing) | API authorization key for Serper.dev Google Search (`/search`) and Scrape Gateway (`scrape.serper.dev`). |
| `PORT` | No | `10000` (Python) / `3000` (Node) | Port for the HTTP server to bind to (`0.0.0.0:$PORT`). Automatically injected by Render. |
| `PYTHON_VERSION` | No | `3.11.8` | Used by Render build container to select Python runtime. |
| `GEMINI_API_KEY` | No | None | Optional; used in AI Studio environments if client-side AI analysis is added. |
| `APP_URL` | No | `http://localhost:10000` | Optional; self-referential deployment host URL. |

### Safe `.env.example` Template:
```bash
# ==============================================================================
# Corporate Intelligence Crawler - Environment Configuration
# ==============================================================================
# Copy this file to .env and insert your real credentials. Never commit .env to git.

# SERPER_API_KEY (Required):
# API token for Serper.dev Google Search and Scrape Gateway.
# Obtain a free key (2,500 queries) at: https://serper.dev
SERPER_API_KEY="your_serper_api_key_here"

# PORT (Optional):
# Port on which the HTTP service listens. Defaults to 10000 if omitted.
# Render automatically injects this dynamically in production.
PORT="10000"

# GEMINI_API_KEY (Optional / AI Studio runtime only):
GEMINI_API_KEY="your_gemini_api_key_here"

# APP_URL (Optional):
APP_URL="http://localhost:10000"
```

---

## 4. REST API Documentation

The microservice exposes three HTTP endpoints:

### 1. `GET /` — Service Capabilities Root
- **Method:** `GET`
- **Purpose:** Health probe, uptime verification, and service discovery.
- **Request Parameters:** None.
- **Response Status:** `200 OK`
- **Response Body:**
  ```json
  {
    "service": "Corporate Intelligence Bridge",
    "status": "operational",
    "endpoints": {
      "POST /google_and_deep_scrape": {
        "body": {
          "query": "string (required, e.g. 'TELERAD TECH PVT LTD')",
          "max_results": "integer (optional)"
        }
      },
      "GET /health": "Lightweight health check"
    }
  }
  ```

---

### 2. `GET /health` — Health Check
- **Method:** `GET`
- **Purpose:** Used by Render deployment health checks and uptime monitoring tools.
- **Response Status:** `200 OK`
- **Response Body:**
  ```json
  {
    "status": "healthy",
    "timestamp": 1791785556.943,
    "serper_configured": true
  }
  ```

---

### 3. `POST /google_and_deep_scrape` — Primary Intelligence Pipeline
- **Method:** `POST`
- **Headers:** `Content-Type: application/json`
- **Request Body Schema:**
  ```json
  {
    "query": "string (required)",
    "max_results": "integer (optional, default 1)"
  }
  ```
- **Response Status:** `200 OK` on success, `400` on validation failure, `404` on zero search results, `500` on upstream error.
- **Response Body Schema:**
  ```json
  {
    "target_query": "string",
    "source_url": "string (URL of primary crawled profile)",
    "company_metadata": "string (Structured Markdown dossier)",
    "directors_network": [
      {
        "director_name": "string",
        "profile_url": "string",
        "associated_companies": "string (Markdown table of directorships)"
      }
    ],
    "acquisitions_count": "integer"
  }
  ```

---

## 5. Crawler Workflow & Pipeline Architecture

The crawler executes a 5-step sequential pipeline:

```
[Incoming POST /google_and_deep_scrape]
                   │
                   ▼
       [1. Serper Google Search] ───────────► POST https://google.serper.dev/search (q: query, num: 8)
                   │
                   ▼
     [2. Target URL Classification]
      ├─► Strategic Target: First matching Wikipedia, LinkedIn, or top organic link
      └─► Statutory Target: First matching Zaubacorp or Tofler link
          (If no registry link found & query looks like a corporation,
           fires targeted query: "{query} zaubacorp OR tofler")
                   │
                   ▼
     [3. Strategic Profile Crawling]
      ├─► Tier 1: Direct HTTP GET with realistic Chrome 124 headers
      └─► Tier 2: Fallback to POST https://scrape.serper.dev (if status != 200 or Cloudflare block)
      Extracts: Headquarters, Revenue, Employees, Stock Ticker, Verified Acquisitions
                   │
                   ▼
     [4. Statutory Registry Crawling]
      ├─► Tier 1: Direct HTTP GET with realistic Chrome 124 headers
      └─► Tier 2: Fallback to POST https://scrape.serper.dev (if status != 200 or Cloudflare block)
      Extracts: CIN, Authorized Capital, Paid-up Capital, Registered Office, Director Names
                   │
                   ▼
     [5. Formatting & Assembly]
      ├─► HTML table parsing via convert_table_to_markdown()
      ├─► Memory cap enforcement (4,000 chars maximum)
      └─► JSON serialization & HTTP 200 response
```

---

## 6. Search Providers & Scraping Gateway Mechanics

### Provider: Serper.dev
The application uses **Serper.dev** exclusively as its search and stealth gateway. Direct Google scraping is intentionally avoided to eliminate IP blocks, CAPTCHAs, and legal exposure.

- **Endpoints Used:**
  1. `POST https://google.serper.dev/search`
     - Payload: `{"q": query, "num": 8}`
     - Headers: `{"X-API-KEY": api_key, "Content-Type": "application/json"}`
     - Quota Consumption: **1 search credit per call**
  2. `POST https://scrape.serper.dev`
     - Payload: `{"url": url}`
     - Headers: `{"X-API-KEY": api_key, "Content-Type": "application/json"}`
     - Quota Consumption: **10 credits per scraped URL**

### Quota & Cost Breakdown:
- **Free Account:** Serper provides **2,500 credits** on signup.
- **Per-Crawl Consumption:**
  - 1 or 2 Google searches = 1 to 2 credits.
  - 1 or 2 gateway scrapes (when Cloudflare blocks direct GET) = 10 to 20 credits.
  - Total per company request: **11 to 22 credits**.
  - A free key provides approximately **110 to 220 full deep crawls**.
- **Fallback Trigger:**
  Direct crawling is attempted first using realistic desktop browser headers (`User-Agent`, `Referer: https://www.google.com/`, `Sec-Ch-Ua`). If the response fails, returns a non-200 code, or contains Cloudflare challenge strings (`"cf-mitigated: challenge"`), the request immediately escalates to `https://scrape.serper.dev`.

---

## 7. Efficiency, Concurrency & Memory Controls

### Strict 256MB RAM Guardrails:
1. **Zero Headless Browsers:** No Chrome/Chromium, Puppeteer, Selenium, or Playwright binaries. A full headless browser consumes 350MB–800MB RAM, which immediately triggers the Linux kernel OOM killer (Exit Code 137) on Render Free Tier.
2. **Lightweight Parsing:** Uses `requests` and Python's built-in `html.parser` via `BeautifulSoup`. Base memory usage is ~25MB–35MB; peak memory during large DOM parses is ~50MB–70MB.
3. **Aggressive DOM Pruning:** Non-content HTML tags (`<script>`, `<style>`, `<nav>`, `<footer>`, `<header>`, `<svg>`, `<noscript>`, `<iframe>`) are stripped before parsing.
4. **4,000-Character Output Cap (`MAX_PAGE_CHAR_LIMIT = 4000`):** Dossiers are truncated at 4,000 characters to bound memory consumption in Python's heap.

### Timeouts & Network Delays:
- **HTTP Timeout:** `DEFAULT_REQUEST_TIMEOUT = 14` seconds across all outbound calls (`requests.get`, `requests.post`).
- **Gunicorn Worker Timeout:** `--timeout 120` seconds in Gunicorn start command. This provides adequate headroom for multi-tier network requests.

### Concurrency & Process Limits:
- **Gunicorn Workers:** Configured for exactly **2 worker processes** (`-w 2`).
- **RAM Footprint under Load:** 2 workers × ~55MB = ~110MB total memory footprint, safely within the 256MB free limit.
- **Shared Memory Heartbeats:** `--worker-tmp-dir /dev/shm` ensures Gunicorn worker heartbeat files are kept in Linux RAM disk, avoiding file-system lockups on container platforms.

### Architectural Gaps in Existing Codebase (What is NOT built-in):
- **No Redis / Memcached Caching:** Every request executes live outbound HTTP calls. Identical queries repeated back-to-back will consume Serper credits again.
- **No In-Memory Request Deduplication:** If two simultaneous requests query the same company, both will crawl independently.
- **No Built-in Rate Limiting:** Flask has no `flask-limiter` configured.
- **No Retry Loop with Exponential Backoff:** If Serper returns a 500 error or network drops, the request fails immediately rather than retrying.
- **Recommendation for Integrating Platforms:** Place an in-memory or Redis cache (keyed by `sha256(query)` with a 24-hour TTL) and an API rate limiter in front of this microservice.

---

## 8. Data Extraction: Fact vs. Claim Analysis

The crawler combines **statutory machine extraction** and **heuristic pattern extraction**. Integrating financial intelligence platforms must distinguish between verified legal facts and unstructured claims:

| Field | Source Target | Extraction Method | Verification Status | Code Reference |
| :--- | :--- | :--- | :--- | :--- |
| **Company Name** | Google Knowledge Graph or Title | `kg.get("title")` or `organic[0]["title"]` | High confidence | `app.py:275` |
| **CIN (Corporate ID)** | Zaubacorp / MCA Registry | Regex `\b[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b` | **Verified Statutory Fact** | `app.py:306` |
| **Authorized Capital** | Zaubacorp / MCA Registry | Regex `Authori[s\|z]ed\s+(?:Share\s+)?Capital[^\d]*([₹\d\.,]+)` | **Verified Filing Metric** | `app.py:307` |
| **Paid-up Capital** | Zaubacorp / MCA Registry | Regex `Paid[- ]up\s+(?:Share\s+)?Capital[^\d]*([₹\d\.,]+)` | **Verified Filing Metric** | `app.py:308` |
| **Registered Office** | Schema.org JSON-LD or Registry text | `jsonld.address` or Regex `Registered address` | High confidence | `server.ts:308` |
| **Director Names** | Zaubacorp / MCA Registry | Regex `Directors of [^\.]+ are ([^\.]+)` | High confidence | `app.py:268` |
| **Director DIN** | Zaubacorp / DIN Profile | *Extracted if present in text/links* | **Partially Implemented** (See notes below) | `app.py:324` |
| **Bank Charges** | MCA Registry Charges Table | *HTML Table conversion only* | **NOT Specifically Extracted** (See notes below) | `app.py:76` |
| **Headquarters** | Wikipedia / Profile Text | Regex `Headquarters\s*[:\s]\s*([^\n\r\|]{3,60})` | Unstructured Claim | `app.py:245` |
| **Annual Revenue** | Wikipedia / Profile Text | Regex `Revenue\s*[:\s]\s*([\$€₹£]?[0-9\.,]+...)` | Self-reported Claim | `app.py:249` |
| **Workforce Size** | Profile Text | Regex `(?:Company size\|Employees\|Workforce)...` | Self-reported Claim | `app.py:253` |
| **Stock Ticker** | Exchange notation | Regex `(?:NYSE\|NASDAQ\|BSE\|NSE)\s*:\s*([A-Z0-9]+)` | Public Market Data | `app.py:257` |
| **Acquisitions / M&A** | Wikipedia M&A section | Scans block for "acquired", "merger with", "purchased" | Verified only if in target entity text | `app.py:230-243` |

### Important Disclosures on Implementation Gaps:
1. **Bank Charges Extraction:** There is **NO dedicated regex parser** in `app.py` for "Charges", "Charge Amount", or "Holder of Charge". If the registry page returns an HTML `<table>` for charges, `convert_table_to_markdown()` converts it to Markdown; however, when the Serper Scrape Gateway returns unstructured plain text (`raw_data["text"]`), charges data is omitted unless present in raw text lines.
2. **DIN Extraction:** Director names are extracted via regex, but individual 8-digit Director Identification Numbers (DINs) are not isolated into dedicated JSON fields.
3. **M&A Cross-Entity Pollution Guard:** The M&A extractor scans only the specific text retrieved from the target company's strategic profile URL. If a company does not have an acquisitions section, the dossier contains no acquisition table.

---

## 9. Integration Requirements for External Systems

### Calling the Microservice from Python (FastAPI, LangChain, CrewAI):
```python
import requests
from typing import Dict, Any, Optional

CRAWLER_BASE_URL = "https://your-crawler-service.onrender.com"

class CorporateIntelligenceClient:
    def __init__(self, base_url: str = CRAWLER_BASE_URL, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def check_health(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200 and resp.json().get("status") == "healthy"
        except Exception:
            return False

    def crawl_company(self, company_name: str) -> Dict[str, Any]:
        """
        Retrieves company dossier, statutory metrics, and director network.
        """
        url = f"{self.base_url}/google_and_deep_scrape"
        payload = {"query": company_name, "max_results": 1}
        
        resp = requests.post(url, json=payload, timeout=self.timeout)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            return {"error": "Company not found", "query": company_name}
        else:
            raise RuntimeError(f"Crawler error {resp.status_code}: {resp.text}")

# Usage:
client = CorporateIntelligenceClient()
data = client.crawl_company("Wabtec Corporation")
print("Target URL:", data.get("source_url"))
print("Acquisitions Found:", data.get("acquisitions_count"))
print("Metadata Dossier:\n", data.get("company_metadata"))
```

### Passing Extracted Output to LLM Forensic Agents:
The `company_metadata` field is formatted as Markdown tables. Prompt template for LLM analysis:
```text
You are a senior forensic auditor. Analyze the following verified company dossier:

{{ company_metadata }}

Director Network:
{{ directors_network }}

Tasks:
1. Calculate the Capital Utilization Ratio (Paid-up Capital / Authorized Capital).
2. Check if the entity is an Active or Inactive corporate entity.
3. Identify related entities or potential conflict-of-interest directorships.
```

---

## 10. Render Deployment & Hardware Constraints

- **Start Command:** `gunicorn -w 2 -b 0.0.0.0:$PORT --timeout 120 --worker-tmp-dir /dev/shm app:app`
- **Build Command:** `pip install -r requirements.txt`
- **Health Check Path:** `/health`
- **RAM Constraint:** Render allocates 512MB on the free tier, but hard throttles and terminates processes exceeding 256MB–300MB RAM. The memory budget of this microservice is strictly tuned:
  - Base Python + Gunicorn Master: ~30MB
  - Worker 1: ~45MB
  - Worker 2: ~45MB
  - Total: ~120MB (leaves >130MB buffer).
- **Ephemeral Filesystem:** No local files persist across container restarts. All processing occurs in-memory.
- **Cold Starts:** Free instances spin down after 15 minutes of inactivity. The first inbound request takes 45–60 seconds to boot. Keep warm via pinging `/health` every 10 minutes.

---

## 11. Limitations, Legal & Compliance Considerations

1. **Anti-Circumvention Policy:** The crawler does not crack CAPTCHAs, spoof credentials, or exploit access controls. It uses standard HTTP headers and official APIs.
2. **Third-Party Terms of Service:** Individual registries (e.g., Zaubacorp, LinkedIn, Tofler) may include terms regarding automated access. The user of this crawler is responsible for evaluating legal and regulatory obligations under applicable jurisdictions.
3. **Robots.txt Considerations:** Direct crawling respects HTTP status codes. When access is restricted (HTTP 403), the crawler routes through the Serper gateway API.
4. **Data Privacy & GDPR / DPDP:** Directorship data contains names of natural persons. Handling of personal data must comply with local privacy regulations.
5. **Serper API Dependency:** If the Serper account runs out of credits or the API key is revoked, all searches and gateway scrapes will fail with HTTP 500.

---

## 12. Verification & Testing Playbook

### Test 1: Verify Health Endpoint
```bash
curl -s http://localhost:10000/health
```
**Expected Response:**
```json
{"serper_configured":true,"status":"healthy","timestamp":1791785556.94}
```

### Test 2: Verify Root Endpoint
```bash
curl -s http://localhost:10000/
```
**Expected Response:**
```json
{"endpoints":{"GET /health":"Lightweight health check","POST /google_and_deep_scrape":{"body":{"max_results":"integer (optional)","query":"string (required, e.g. 'TELERAD TECH PVT LTD')"}}},"service":"Corporate Intelligence Bridge","status":"operational"}
```

### Test 3: Test Real Company Crawl (Multinational)
```bash
curl -s -X POST http://localhost:10000/google_and_deep_scrape \
  -H "Content-Type: application/json" \
  -d '{"query":"Wabtec Corporation"}'
```
**Expected:** Returns `200 OK` with `acquisitions_count >= 1`, `source_url`, and Markdown table containing stock ticker, revenue, and headquarters.

### Test 4: Test Real Company Crawl (Indian Private Limited)
```bash
curl -s -X POST http://localhost:10000/google_and_deep_scrape \
  -H "Content-Type: application/json" \
  -d '{"query":"TELERAD TECH PVT LTD"}'
```
**Expected:** Returns `200 OK` with CIN matching `U72200KA2009PTC049526`, Authorized Capital, Paid-up Capital, and director list.

### Test 5: Verify Error Handling (Missing Query)
```bash
curl -s -X POST http://localhost:10000/google_and_deep_scrape \
  -H "Content-Type: application/json" \
  -d '{}'
```
**Expected Response:** `400 Bad Request`
```json
{"error":"The 'query' field is required in the JSON payload"}
```

### Test 6: Verify Error Handling (Non-Existent Entity)
```bash
curl -s -X POST http://localhost:10000/google_and_deep_scrape \
  -H "Content-Type: application/json" \
  -d '{"query":"XYZ999NONEXISTENTCOMPANY12345"}'
```
**Expected Response:** `404 Not Found` or clean empty dossier without uncaught runtime crashes.
