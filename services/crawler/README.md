# Corporate Intelligence Bridge 🕵️‍♂️

A lightweight, stealth corporate intelligence scraping microservice designed specifically for deployment on the **Render Free Tier** (under a strict **256MB RAM ceiling**). Serves as an OSINT pipeline bridge for financial intelligence platforms and local AI forensic agents.

---

## 📚 Complete Documentation Index

Before integrating this crawler into your platform, review the comprehensive technical guides included in this repository:

1. **[CRAWLER_INTEGRATION.md](./CRAWLER_INTEGRATION.md)**: Exhaustive integration guide for AI coding agents and platform engineers covering architecture, 2-tier scraping, data extraction (fact vs. claim), memory controls, concurrency, and client code samples.
2. **[API_DOCUMENTATION.md](./API_DOCUMENTATION.md)**: REST API reference for all endpoints (`GET /`, `GET /health`, `POST /google_and_deep_scrape`), complete request/response schemas, error codes, and curl examples.
3. **[RENDER_DEPLOYMENT.md](./RENDER_DEPLOYMENT.md)**: Deployment manual for Render Free Tier, covering Blueprint setup, Gunicorn worker memory tuning (`-w 2`, `/dev/shm`), and cold-start management.
4. **[.env.example](./.env.example)**: Safe environment configuration template with non-sensitive placeholders.

---

## ⚡ Core Architecture

- **Compliant Search Layer (`Serper.dev`)**: Does not scrape Google directly. Uses the official Serper.dev REST API (`https://google.serper.dev/search`) to avoid CAPTCHAs and IP bans.
- **Two-Tier Stealth Scraping**:
  - **Tier 1:** High-speed direct HTTP GET requests with realistic desktop Chrome 124 headers.
  - **Tier 2 (Gateway Fallback):** Automatic escalation to the Serper Scrape Gateway (`https://scrape.serper.dev`) when anti-bot portals (such as Cloudflare Turnstile on Zaubacorp) block direct scraping.
- **Strict 256MB RAM Guardrails**: Zero headless browser dependencies (`Playwright`, `Selenium`, and `Puppeteer` are strictly omitted). Total memory footprint is bounded at ~110MB–130MB across 2 sync workers.
- **Table Preservation & Markdown Conversion**: HTML `<table>` elements are converted directly into structured Markdown grids to preserve alignment for downstream LLM analysis.
- **Entity Cross-Pollution Guard**: Strategic acquisitions are only extracted if verified in the target entity's own source text, eliminating cross-company data confusion.

---

## 🚀 Quickstart & Local Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variable
Obtain an API key from [Serper.dev](https://serper.dev) (2,500 free queries):
```bash
# Linux / macOS / WSL:
export SERPER_API_KEY="your_serper_api_key_here"
export PORT="10000"

# Windows PowerShell:
$env:SERPER_API_KEY = "your_serper_api_key_here"
$env:PORT = "10000"
```

### 3. Run the Microservice
```bash
# Local development:
python app.py

# Production / Linux with Gunicorn:
gunicorn -w 2 -b 0.0.0.0:10000 --timeout 120 --worker-tmp-dir /dev/shm app:app
```

The service will bind to `http://localhost:10000`.

---

## 📡 API Quick Reference

### Health Check
```bash
curl -s http://localhost:10000/health
```

### Execute Corporate Crawl
```bash
curl -X POST http://localhost:10000/google_and_deep_scrape \
  -H "Content-Type: application/json" \
  -d '{"query": "Wabtec Corporation"}'
```

#### Example Output:
```json
{
  "target_query": "Wabtec Corporation",
  "source_url": "https://en.wikipedia.org/wiki/Wabtec",
  "acquisitions_count": 2,
  "company_metadata": "# WABTEC CORPORATION - STRATEGIC CORPORATE DOSSIER\n\n### Corporate Profile & Operational Scale\n| Dimension | Verified Metric / Intelligence |\n| --- | --- |\n| Headquarters | Pittsburgh, Pennsylvania, United States |\n| Stock Exchange Ticker | NYSE: WAB |\n| Annual Revenue | $11.17 Billion |\n\n### Major Acquisitions, Mergers & Strategic Deals\n| Year / Phase | Strategic Acquisition & Deal Overview |\n| --- | --- |\n| 2019 | Merger with GE Transportation ($11 Billion) |\n| 2016 | Acquired 51% of Faiveley Transport ($1.7 Billion) |",
  "directors_network": [
    {
      "director_name": "Key Managerial Personnel / Corporate Network",
      "profile_url": "https://www.zaubacorp.com/company/WABTEC-INDIA-INDUSTRIAL-PRIVATE-LIMITED/U74999KA1999PTC143212",
      "associated_companies": "| CIN / Identifier | Associated Entity Name | Status |\n| --- | --- | --- |\n| U35122KA2015FTC144910 | WABTEC INDIA TRANSPORTATION PRIVATE LIMITED | Active |"
    }
  ]
}
```

---

## ☁️ 1-Click Render Deployment

1. Fork or push this repository to GitHub.
2. In the [Render Dashboard](https://dashboard.render.com), click **New +** ➔ **Blueprint**.
3. Select this repository; Render will automatically detect `render.yaml`.
4. Enter your `SERPER_API_KEY` when prompted and click **Apply**.
