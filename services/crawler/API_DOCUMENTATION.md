# Corporate Intelligence Crawler - REST API Documentation

This document specifies the exact REST API contract for the **Corporate Intelligence Crawler** service as implemented in `app.py` (Python Flask) and mirrored in `server.ts` (Express / TypeScript).

---

## 1. General Specifications

- **Protocol:** HTTP/1.1 and HTTP/2 over TLS (HTTPS in production)
- **Content Type:** `application/json`
- **Default Port:**
  - Python Microservice (`app.py`): `10000` (or dynamic `$PORT` from environment)
  - Node/Express Workbench (`server.ts`): `3000` (or dynamic `$PORT` from environment)
- **CORS Support:** Full CORS enabled (`Access-Control-Allow-Origin: *`, `Methods: GET, POST, OPTIONS`, `Headers: Content-Type, Authorization`).
- **Authentication:** Service-level. Endpoints do not require client-side Bearer tokens, but the service requires `SERPER_API_KEY` configured in the backend environment.

---

## 2. Endpoints Inventory

| Method | Endpoint Path | Implementation | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/` | `app.py` | Service discovery and capabilities manifest |
| `GET` | `/health` (Python) / `/api/health` (Node) | `app.py` / `server.ts` | Uptime probe and `SERPER_API_KEY` status check |
| `POST` | `/google_and_deep_scrape` (Python) / `/api/google_and_deep_scrape` (Node) | `app.py` / `server.ts` | Primary intelligence search, extraction, and formatting pipeline |

---

## 3. Detailed Endpoint Reference

### Endpoint 1: Service Capabilities Root
Provides a human-readable manifest of operational endpoints.

- **HTTP Method:** `GET`
- **Path:** `/`
- **Authentication:** None

#### Response (200 OK):
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

### Endpoint 2: Health Check
Verifies process responsiveness and confirms that Serper credentials are loaded.

- **HTTP Method:** `GET`
- **Path:** `/health` (in `app.py`) or `/api/health` (in `server.ts`)
- **Authentication:** None

#### Response (200 OK):
```json
{
  "status": "healthy",
  "timestamp": 1791785556.943,
  "serper_configured": true
}
```

#### Fields Description:
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | string | Constant `"healthy"` when the service is executing |
| `timestamp` | float / int | POSIX timestamp generated at execution time |
| `serper_configured` | boolean | `true` if `SERPER_API_KEY` environment variable is non-empty |

---

### Endpoint 3: Google Search & Deep Scrape
Executes a multi-tier search, scrapes strategic and statutory profiles, and returns an aligned Markdown dossier.

- **HTTP Method:** `POST`
- **Path:** `/google_and_deep_scrape` (in `app.py`) or `/api/google_and_deep_scrape` (in `server.ts`)
- **Headers:** `Content-Type: application/json`

#### Request Schema:
```json
{
  "query": "string (required)",
  "max_results": 1
}
```

#### Parameters:
| Name | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `query` | string | **Yes** | — | Name of company or search query (e.g., `"Wabtec Corporation"`, `"TELERAD TECH PVT LTD"`) |
| `max_results` | integer | No | `1` | Optional parameter for downstream callers |

---

#### Success Response (200 OK) — Example 1: Global Strategic Entity

**Request:**
```bash
curl -X POST https://your-service.onrender.com/google_and_deep_scrape \
  -H "Content-Type: application/json" \
  -d '{"query": "Wabtec Corporation"}'
```

**Response Body:**
```json
{
  "target_query": "Wabtec Corporation",
  "source_url": "https://en.wikipedia.org/wiki/Wabtec",
  "acquisitions_count": 2,
  "company_metadata": "# WABTEC CORPORATION - STRATEGIC CORPORATE DOSSIER\n\n> **Corporate Overview**: Westinghouse Air Brake Technologies Corporation is an American company formed by the merger of Westinghouse Air Brake Company and MotivePower Industries...\n\n### Corporate Profile & Operational Scale\n| Dimension | Verified Metric / Intelligence |\n| --- | --- |\n| Headquarters | Pittsburgh, Pennsylvania, United States |\n| Stock Exchange Ticker | NYSE: WAB |\n| Annual Revenue | $11.17 Billion |\n| Company Size / Workforce | 29,500 employees |\n\n### Major Acquisitions, Mergers & Strategic Deals\n| Year / Phase | Strategic Acquisition & Deal Overview |\n| --- | --- |\n| 2019 | Merger with GE Transportation ($11 Billion) |\n| 2016 | Acquired 51% of Faiveley Transport ($1.7 Billion) |\n\n### Statutory Registry & Regulatory Filings (MCA Registry)\n| Registry Attribute | Verified Filing Value |\n| --- | --- |\n| CIN | U74999KA1999PTC143212 |\n| Company Status | Active |\n| Authorized Capital | ₹3520000000.00 |\n| Paid-up Capital | ₹491835300.00 |",
  "directors_network": [
    {
      "director_name": "Key Managerial Personnel / Corporate Network",
      "profile_url": "https://www.zaubacorp.com/company/WABTEC-INDIA-INDUSTRIAL-PRIVATE-LIMITED/U74999KA1999PTC143212",
      "associated_companies": "| CIN / Identifier | Associated Entity Name | Status |\n| --- | --- | --- |\n| U35122KA2015FTC144910 | WABTEC INDIA TRANSPORTATION PRIVATE LIMITED | Active |\n| U72200KA2007PTC043948 | XLHEALTH CORPORATION INDIA PRIVATE LIMITED | Active |"
    }
  ]
}
```

---

#### Success Response (200 OK) — Example 2: Private Entity (Statutory Focus)

**Request:**
```bash
curl -X POST https://your-service.onrender.com/google_and_deep_scrape \
  -H "Content-Type: application/json" \
  -d '{"query": "TELERAD TECH PVT LTD"}'
```

**Response Body:**
```json
{
  "target_query": "TELERAD TECH PVT LTD",
  "source_url": "https://www.linkedin.com/company/teleradtech",
  "acquisitions_count": 0,
  "company_metadata": "# TELERAD TECH PRIVATE LIMITED - STRATEGIC CORPORATE DOSSIER\n\n> **Corporate Overview**: Telerad Tech is an Indian healthcare software enterprise developing AI-enabled radiology and PACS solutions...\n\n### Corporate Profile & Operational Scale\n| Dimension | Verified Metric / Intelligence |\n| --- | --- |\n| Headquarters | Bengaluru, Karnataka, India |\n| Company Size / Workforce | 50-200 employees |\n\n### Statutory Registry & Regulatory Filings (MCA Registry)\n| Registry Attribute | Verified Filing Value |\n| --- | --- |\n| CIN | U72200KA2009PTC049526 |\n| Company Status | Active |\n| Authorized Capital | ₹390000000.00 |\n| Paid-up Capital | ₹311584650.00 |",
  "directors_network": [
    {
      "director_name": "ARJUN KALYANPUR",
      "profile_url": "https://www.zaubacorp.com/company/TELERAD-TECH-PRIVATE-LIMITED/U72200KA2009PTC049526",
      "associated_companies": "| CIN / Identifier | Associated Entity Name | Status |\n| --- | --- | --- |\n| U64202KA2002PTC030092 | A-KAL TELEVERSE PRIVATE LIMITED | Active |\n| U74900KA2011PTC060045 | IMAGE CORE LAB PRIVATE LIMITED | Active |"
    },
    {
      "director_name": "SUNITA MAHESHWARI",
      "profile_url": "https://www.zaubacorp.com/company/TELERAD-TECH-PRIVATE-LIMITED/U72200KA2009PTC049526",
      "associated_companies": "| CIN / Identifier | Associated Entity Name | Status |\n| --- | --- | --- |\n| U85110KA2007PTC041369 | TELERAD RX DX HEALTHCARE PRIVATE LIMITED | Active |"
    }
  ]
}
```

---

## 4. Error Responses & Status Codes

The API uses standard HTTP error codes:

| HTTP Status | Condition | Example Response Body |
| :--- | :--- | :--- |
| **`400 Bad Request`** | Non-JSON payload or missing `query` parameter | `{"error": "Request body must be valid JSON"}`<br>`{"error": "The 'query' field is required in the JSON payload"}` |
| **`404 Not Found`** | Google organic search returned zero results | `{"error": "No organic search results found", "target_query": "NonExistentCompany"}` |
| **`500 Internal Server Error`** | Unconfigured `SERPER_API_KEY` or upstream gateway network failure | `{"error": "Missing SERPER_API_KEY environment variable"}`<br>`{"error": "Scraping error: Serper API failed: ..."}` |

---

## 5. Client Code Samples

### Python (`requests`):
```python
import requests

url = "https://your-service.onrender.com/google_and_deep_scrape"
payload = {"query": "Tata Consultancy Services zaubacorp"}

response = requests.post(url, json=payload, timeout=60)
if response.status_code == 200:
    data = response.json()
    print("Source URL:", data["source_url"])
    print("Metadata:\n", data["company_metadata"])
else:
    print(f"Error {response.status_code}: {response.text}")
```

### TypeScript (`fetch`):
```typescript
interface CrawlerResponse {
  target_query: string;
  source_url: string;
  company_metadata: string;
  directors_network: Array<{
    director_name: string;
    profile_url: string;
    associated_companies: string;
  }>;
  acquisitions_count: number;
}

async function runCorporateScrape(companyQuery: string): Promise<CrawlerResponse> {
  const res = await fetch("https://your-service.onrender.com/google_and_deep_scrape", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: companyQuery, max_results: 1 })
  });

  if (!res.ok) {
    const errorBody = await res.json();
    throw new Error(`Scraper failed: ${errorBody.error}`);
  }

  return await res.json();
}
```
