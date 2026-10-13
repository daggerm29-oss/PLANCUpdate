"""
Corporate Intelligence Bridge - Multi-Source Corporate Profile API
Designed specifically for Render Free Tier (<= 256MB RAM ceiling).

Architecture:
1. Company discovery through Serper Google Search (knowledge graph + organic results).
2. Profile enrichment from permitted sources only: the search results themselves, Wikipedia, the company's own website
   and public registry-aggregator pages. Nothing extracted here is verified; every value is returned in `fields`
   with its source URL, source type, extraction method and retrieval time so the caller can verify it.
3. LinkedIn is never fetched. Only the public search-result title/snippet for a LinkedIn company page is used.
4. Access restrictions (login walls, CAPTCHAs, bot challenges, 401/403/429/451/999) are respected: such pages are
   recorded in `fetch_log` as blocked and are NOT retried through the Serper scrape gateway. The gateway is only a
   fallback for technical failures (network errors, timeouts, 5xx).
5. No fabricated values: fields that were not found are omitted (no hard-coded "Active", "Disclosed" or placeholder
   directors).
6. Strict 4,000 character cap on the Markdown dossier to protect against OOM crashes.
"""

import os
import re
import time
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse, quote_plus

from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup, Tag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("corp_intel_bridge")

app = Flask(__name__)

CRAWLER_VERSION = "2"
MAX_PAGE_CHAR_LIMIT = 4000
MAX_HTML_BYTES = 1_500_000
DEFAULT_REQUEST_TIMEOUT = 14

REQUEST_HEADERS = {
    "User-Agent": "CorporateIntelligenceBridge/2.0 (company due-diligence research bot; respects access restrictions)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

NO_FETCH_DOMAINS = ("linkedin.com",)
AGGREGATOR_DOMAINS = ("zaubacorp.com", "tofler.in")
NON_OFFICIAL_DOMAINS = NO_FETCH_DOMAINS + AGGREGATOR_DOMAINS + (
    "wikipedia.org", "wikidata.org", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "crunchbase.com", "bloomberg.com", "reuters.com", "glassdoor.com", "glassdoor.co.in", "indeed.com",
    "ambitionbox.com", "justdial.com", "moneycontrol.com", "economictimes.indiatimes.com", "zoominfo.com",
    "dnb.com", "opencorporates.com", "google.com",
)
RESTRICTED_STATUS = {401, 403, 407, 429, 451, 999}
LOGIN_PATH_MARKERS = ("/login", "/signin", "/sign-in", "/authwall", "/checkpoint", "/uas/")
RESTRICTION_MARKERS = ("cf-mitigated", "challenge-platform", "captcha", "authwall", "are you a robot",
                       "unusual traffic", "verify you are human", "please enable javascript and cookies",
                       "sign in to continue", "access denied")
PLACEHOLDER_VALUES = {"", "disclosed", "n/a", "na", "-", "none", "not available"}
CIN_RE = re.compile(r"\b[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")

INFOBOX_FIELDS = {
    "headquarters": "headquarters", "revenue": "revenue", "number of employees": "workforce",
    "traded as": "stock_ticker", "founded": "founded", "industry": "industry", "website": "website",
    "type": "company_type", "isin": "isin", "parent": "parent_company_mention",
    "subsidiaries": "subsidiary_mention", "divisions": "subsidiary_mention", "key people": "key_people_mention",
}
KG_ATTRIBUTE_FIELDS = {
    "headquarters": "headquarters", "founded": "founded", "revenue": "revenue",
    "number of employees": "workforce", "parent organization": "parent_company_mention",
    "parent organisation": "parent_company_mention", "parent": "parent_company_mention",
    "subsidiaries": "subsidiary_mention", "ceo": "key_people_mention", "founders": "key_people_mention",
    "founder": "key_people_mention", "key people": "key_people_mention", "stock price": "stock_ticker",
}
LIST_FIELDS = {"subsidiary_mention", "key_people_mention", "parent_company_mention"}
LINKEDIN_SNIPPET_PATTERNS = {
    "workforce": r"(?:company size[:\s]*)?(\d[\d,]*\+?(?:\s*[-–]\s*\d[\d,]*)?)\s+employees",
    "headquarters": r"Headquarters[:\s]+([^.|·]{3,60})",
    "industry": r"Industry[:\s]+([^.|·]{3,60})",
}
CAPITAL_PATTERNS = {
    "authorized_capital": r"Authori[sz]ed\s+(?:Share\s+)?Capital[^\d₹]{0,40}(₹?\s?[\d\.,]+(?:\s*(?:crore|lakh))?)",
    "paid_up_capital": r"Paid[- ]up\s+(?:Share\s+)?Capital[^\d₹]{0,40}(₹?\s?[\d\.,]+(?:\s*(?:crore|lakh))?)",
}
PROFILE_TABLE = [("headquarters", "Headquarters"), ("revenue", "Revenue"), ("workforce", "Company Size / Workforce"),
                 ("stock_ticker", "Stock Ticker"), ("founded", "Founded"), ("industry", "Industry"),
                 ("website", "Website")]
REGISTRY_TABLE = [("cin", "CIN"), ("authorized_capital", "Authorized Capital"), ("paid_up_capital", "Paid-up Capital")]

SERPER_CALLS: ContextVar[Dict[str, int]] = ContextVar("serper_calls")


def clean_cell_text(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned.replace("|", "\\|")


def clean_attribute_val(val: str) -> str:
    if not val:
        return ""
    val = re.sub(r"\[\s*\d+\s*\]", "", val)
    return re.sub(r"\s+", " ", val).replace("|", " ").strip()


def convert_table_to_markdown(table: Tag) -> str:
    rows = table.find_all("tr")
    if not rows:
        return ""

    parsed_rows: List[List[str]] = []
    max_cols = 0

    for tr in rows:
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        row_content = [clean_cell_text(c.get_text()) for c in cells]
        if any(row_content):
            parsed_rows.append(row_content)
            max_cols = max(max_cols, len(row_content))

    if not parsed_rows or max_cols == 0:
        return ""

    for row in parsed_rows:
        while len(row) < max_cols:
            row.append("")

    first_row = parsed_rows[0]
    has_explicit_th = bool(rows[0].find_all("th"))

    markdown_lines: List[str] = []
    if has_explicit_th or len(parsed_rows) > 1:
        headers = [c if c else f"Column {i+1}" for i, c in enumerate(first_row)]
        markdown_lines.append("| " + " | ".join(headers) + " |")
        markdown_lines.append("| " + " | ".join(["---"] * max_cols) + " |")
        body_rows = parsed_rows[1:]
    else:
        headers = [f"Field {i+1}" for i in range(max_cols)]
        markdown_lines.append("| " + " | ".join(headers) + " |")
        markdown_lines.append("| " + " | ".join(["---"] * max_cols) + " |")
        body_rows = parsed_rows

    for row in body_rows:
        markdown_lines.append("| " + " | ".join(row) + " |")

    return "\n" + "\n".join(markdown_lines) + "\n\n"


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "Corporate Intelligence Bridge",
        "version": CRAWLER_VERSION,
        "status": "operational",
        "endpoints": {
            "POST /google_and_deep_scrape": {
                "body": {"query": "string (required, e.g. 'TELERAD TECH PVT LTD')"}
            },
            "GET /health": "Lightweight health check"
        },
        "source_policy": "LinkedIn is never fetched (search snippets only); access restrictions are not bypassed."
    }), 200


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy", "timestamp": time.time(), "version": CRAWLER_VERSION,
                    "serper_configured": bool(os.getenv("SERPER_API_KEY"))}), 200


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def domain_of(url: str) -> str:
    host = urlparse(url or "").netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def on_domain(url: str, domains) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in domains)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup((html or "")[:MAX_HTML_BYTES], "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    text = soup.get_text("\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def restriction_reason(resp) -> Optional[str]:
    """Why a response is an access restriction (login wall, CAPTCHA, bot challenge, rate limit), or None."""
    if resp.status_code in RESTRICTED_STATUS:
        return f"HTTP {resp.status_code} (access restricted)"
    if (resp.headers.get("cf-mitigated") or "").lower() == "challenge":
        return "Cloudflare bot challenge"
    final_path = urlparse(resp.url or "").path.lower()
    if any(m in final_path for m in LOGIN_PATH_MARKERS):
        return f"redirected to a login page ({final_path})"
    body = (resp.text or "")[:200000].lower()
    # Short pages carrying a challenge marker are interstitials; long pages may just embed a reCAPTCHA in a form.
    if any(m in body for m in RESTRICTION_MARKERS) and len(html_to_text(resp.text or "")) < 1500:
        return "CAPTCHA / bot-check / login interstitial"
    return None


def fetch_page(url: str, api_key: str, kind: str, fetch_log: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Fetch a public page. Returns {"html", "text", "via"} or None; every outcome is appended to fetch_log.

    NO_FETCH_DOMAINS are never requested. Access restrictions end the attempt. The Serper scrape gateway is only used
    when the direct request failed for a technical reason (network error, timeout, 5xx).
    """
    entry: Dict[str, Any] = {"url": url, "kind": kind, "outcome": None, "via": None, "http_status": None,
                             "reason": None, "retrieved_at": utc_now()}
    fetch_log.append(entry)
    if on_domain(url, NO_FETCH_DOMAINS):
        entry.update(outcome="not_fetched", reason="Domain is never fetched; only the public search-result title "
                     "and snippet are used.")
        return None

    technical_failure = None
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=DEFAULT_REQUEST_TIMEOUT, allow_redirects=True)
        entry["http_status"] = resp.status_code
        blocked = restriction_reason(resp)
        if blocked:
            entry.update(outcome="blocked", via="direct", reason=blocked + "; not retried through the scrape gateway.")
            return None
        if resp.status_code == 200:
            entry.update(outcome="fetched", via="direct")
            return {"html": resp.text, "text": html_to_text(resp.text), "via": "direct"}
        if resp.status_code >= 500:
            technical_failure = f"HTTP {resp.status_code}"
        else:
            entry.update(outcome="http_error", via="direct", reason=f"HTTP {resp.status_code}")
            return None
    except requests.RequestException as exc:
        technical_failure = type(exc).__name__

    if not api_key:
        entry.update(outcome="error", via="direct", reason=f"{technical_failure}; no SERPER_API_KEY for the gateway.")
        return None
    SERPER_CALLS.get()["scrape_gateway"] += 1
    try:
        g = requests.post("https://scrape.serper.dev", headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                          json={"url": url}, timeout=DEFAULT_REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        entry.update(outcome="error", via="serper_scrape_gateway",
                     reason=f"{technical_failure}; gateway {type(exc).__name__}")
        return None
    if g.status_code != 200:
        entry.update(outcome="error", via="serper_scrape_gateway", http_status=g.status_code,
                     reason=f"{technical_failure}; gateway HTTP {g.status_code}")
        return None
    text = (g.json() or {}).get("text") or ""
    if any(m in text[:5000].lower() for m in RESTRICTION_MARKERS) and len(text) < 1500:
        entry.update(outcome="blocked", via="serper_scrape_gateway",
                     reason="gateway returned a login/CAPTCHA page; discarded")
        return None
    entry.update(outcome="fetched", via="serper_scrape_gateway", reason=f"direct request failed: {technical_failure}")
    return {"html": "", "text": text, "via": "serper_scrape_gateway"}


def serper_search(api_key: str, q: str, num: int, timeout: float = DEFAULT_REQUEST_TIMEOUT) -> Dict[str, Any]:
    SERPER_CALLS.get()["search"] += 1
    resp = requests.post("https://google.serper.dev/search",
                         headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                         json={"q": q, "num": num}, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Serper API failed: HTTP {resp.status_code}")
    return resp.json()


class FieldSink:
    """Collects extracted statements with provenance; drops empty, placeholder and duplicate values."""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []
        self._seen = set()

    def add(self, field: str, value: Any, url: Optional[str], source_type: str, method: str,
            excerpt: str = "", retrieved_at: Optional[str] = None) -> None:
        value = clean_attribute_val(re.sub(r"\[citation needed\]", "", str(value or ""))).strip(" ,;")
        if not value or value.lower() in PLACEHOLDER_VALUES or len(value) > 400:
            return
        key = (field, value.lower(), url)
        if key in self._seen:
            return
        self._seen.add(key)
        self.items.append({"field": field, "value": value, "source_url": url, "source_type": source_type,
                           "method": method, "excerpt": clean_attribute_val(excerpt or value)[:400],
                           "retrieved_at": retrieved_at or utc_now()})

    def first(self, field: str) -> Optional[str]:
        return next((i["value"] for i in self.items if i["field"] == field), None)


def split_list(text: str) -> List[str]:
    parts = re.split(r"\n|;|•|·|, (?=[A-Z])", text or "")
    return [p.strip(" ,.") for p in parts if 2 < len(p.strip(" ,.")) < 120][:15]


def extract_infobox(html: str) -> Dict[str, str]:
    soup = BeautifulSoup((html or "")[:MAX_HTML_BYTES], "html.parser")
    box = soup.select_one("table.infobox")
    out: Dict[str, str] = {}
    if not box:
        return out
    for tr in box.find_all("tr"):
        th, td = tr.find("th"), tr.find("td")
        if not th or not td:
            continue
        for sup in td.find_all("sup"):
            sup.decompose()
        out[th.get_text(" ", strip=True).lower()] = td.get_text("\n", strip=True)
    return out


def extract_profile_regex(text: str, sink: FieldSink, url: str, source_type: str, retrieved_at: str) -> None:
    patterns = {
        "headquarters": r"Headquarters\s*[:\n]\s*([^\n\r\|]{3,80})",
        "revenue": r"Revenue\s*[:\n]\s*([\$€₹£]?\s?[0-9\.,]+\s*(?:billion|million|crore|lakh)?(?:\s*\([^\)]+\))?)",
        "workforce": r"(?:Company size|Number of employees|Employees|Workforce)\s*[:\n]\s*([0-9][0-9,\+\-–\s]*(?:employees)?)",
        "stock_ticker": r"((?:NYSE|NASDAQ|BSE|NSE)\s*:\s*[A-Z0-9&]+)",
    }
    for field, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m and not any(k in m.group(1).lower() for k in ["report", "cookie"]):
            sink.add(field, m.group(1), url, source_type, "page_text_regex",
                     text[max(m.start() - 40, 0):m.end() + 40], retrieved_at)


def extract_acquisitions(text: str) -> List[str]:
    acquisitions: List[str] = []
    in_acq = False
    for line in text.splitlines():
        l_lower = line.lower()
        if "mergers and acquisitions" in l_lower or "major acquisitions" in l_lower or l_lower.startswith("acquisitions"):
            in_acq = True
        elif in_acq and (l_lower.startswith(("references", "see also", "notes", "external links")) or line.startswith("==")):
            in_acq = False
        elif in_acq and any(k in l_lower for k in ["acquired", "merger with", "purchased", "takeover"]):
            cleaned = re.sub(r"^[\*\-\•\d\.\s]+", "", line).strip()
            if 15 < len(cleaned) < 250 and cleaned not in acquisitions:
                acquisitions.append(cleaned)
    return acquisitions[:6]


def extract_registry_identifiers(text: str, sink: FieldSink, url: str, source_type: str, retrieved_at: str) -> None:
    labelled = re.search(r"\bCIN\s*(?:No\.?|Number)?\s*[:\-–]?\s*([UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6})\b", text)
    if labelled:
        sink.add("cin", labelled.group(1), url, source_type, "labelled_cin",
                 text[max(labelled.start() - 60, 0):labelled.end() + 20], retrieved_at)
    else:
        first = CIN_RE.search(text)
        if first:
            sink.add("cin", first.group(0), url, source_type, "first_cin_on_page(unlabelled)",
                     text[max(first.start() - 60, 0):first.end() + 20], retrieved_at)
    office = re.search(r"Registered\s+Office\s*(?:Address)?\s*[:\-–]\s*([^\n]{10,200})", text, re.IGNORECASE)
    if office:
        sink.add("registered_office", office.group(1), url, source_type, "labelled_registered_office",
                 office.group(0), retrieved_at)


def extract_search_result_fields(serper_data: Dict[str, Any], query: str, sink: FieldSink) -> Optional[str]:
    """Fields from the search response itself (knowledge graph, LinkedIn result snippet). Returns the LinkedIn URL."""
    now = utc_now()
    kg = serper_data.get("knowledgeGraph") or {}
    organic = serper_data.get("organic") or []
    search_url = "https://www.google.com/search?q=" + quote_plus(query)
    if kg:
        sink.add("description", kg.get("description"), kg.get("descriptionLink") or search_url,
                 "search_knowledge_graph", "knowledge_graph.description", retrieved_at=now)
        sink.add("website", kg.get("website"), search_url, "search_knowledge_graph", "knowledge_graph.website",
                 retrieved_at=now)
        sink.add("company_type", kg.get("type"), search_url, "search_knowledge_graph", "knowledge_graph.type",
                 retrieved_at=now)
        for label, value in (kg.get("attributes") or {}).items():
            field = KG_ATTRIBUTE_FIELDS.get(label.lower().strip())
            if not field:
                continue
            for v in (split_list(value) if field in LIST_FIELDS else [value]):
                sink.add(field, v, search_url, "search_knowledge_graph", f"knowledge_graph.attributes[{label}]",
                         f"{label}: {value}", now)
    if not sink.first("description") and organic:
        sink.add("description", organic[0].get("snippet"), organic[0].get("link"), "search_result",
                 "organic[0].snippet", retrieved_at=now)

    linkedin = next((r for r in organic if "linkedin.com/company/" in (r.get("link") or "")), None)
    if not linkedin:
        return None
    url = linkedin["link"]
    snippet = " ".join(filter(None, [linkedin.get("title"), linkedin.get("snippet")]))
    sink.add("linkedin_url", url, url, "linkedin_search_result", "organic_result.link", snippet, now)
    for field, pattern in LINKEDIN_SNIPPET_PATTERNS.items():
        m = re.search(pattern, snippet, re.IGNORECASE)
        if m:
            sink.add(field, m.group(1), url, "linkedin_search_result", "search_result_snippet_regex", snippet, now)
    return url


def pick_official_site(serper_data: Dict[str, Any]) -> Optional[str]:
    kg_site = (serper_data.get("knowledgeGraph") or {}).get("website")
    if kg_site and not on_domain(kg_site, NON_OFFICIAL_DOMAINS):
        return kg_site
    for r in serper_data.get("organic") or []:
        link = r.get("link") or ""
        if link and not on_domain(link, NON_OFFICIAL_DOMAINS) and not re.search(r"\.(pdf|xlsx?|docx?)$", link, re.I):
            return link
    return None


def build_markdown(title: str, sink: FieldSink, acquisitions: List[str]) -> str:
    lines = [f"# {title.upper()} - STRATEGIC CORPORATE DOSSIER", ""]
    overview = sink.first("description")
    if overview:
        lines += [f"> **Corporate Overview**: {overview}", ""]
    profile = [(label, sink.first(field)) for field, label in PROFILE_TABLE]
    profile = [(k, v) for k, v in profile if v]
    if profile:
        lines += ["### Corporate Profile & Operational Scale", "| Dimension | Extracted Value (unverified) |", "| --- | --- |"]
        lines += [f"| {k} | {clean_cell_text(v)} |" for k, v in profile] + [""]
    if acquisitions:
        lines += ["### Major Acquisitions, Mergers & Strategic Deals",
                  "| Year / Phase | Strategic Acquisition & Deal Overview |", "| --- | --- |"]
        for deal in acquisitions:
            y_m = re.search(r"\b(19\d{2}|20\d{2})\b", deal)
            lines.append(f"| {y_m.group(0) if y_m else 'Undated'} | {clean_cell_text(deal)} |")
        lines.append("")
    registry = [(label, sink.first(field)) for field, label in REGISTRY_TABLE]
    registry = [(k, v) for k, v in registry if v]
    if registry:
        lines += ["### Statutory Registry & Regulatory Filings (MCA Registry)", "| Registry Metric | Filing Value |",
                  "| --- | --- |"] + [f"| {k} | {clean_cell_text(v)} |" for k, v in registry]
    md = "\n".join(lines)
    return md if len(md) <= MAX_PAGE_CHAR_LIMIT else md[:MAX_PAGE_CHAR_LIMIT] + "\n\n[... Capped for memory safety ...]"


def enrich_from_wikipedia(url, api_key, sink, fetch_log) -> Optional[List[str]]:
    """Returns the acquisitions found, or None when the page could not be fetched."""
    page = fetch_page(url, api_key, "wikipedia", fetch_log)
    if not page:
        return None
    now = fetch_log[-1]["retrieved_at"]
    box = extract_infobox(page["html"])
    for label, value in box.items():
        field = INFOBOX_FIELDS.get(label)
        if field:
            for v in (split_list(value) if field in LIST_FIELDS else [value.replace("\n", " ")]):
                sink.add(field, v, url, "wikipedia", f"infobox[{label}]", f"{label}: {value[:300]}", now)
    if not box:
        extract_profile_regex(page["text"], sink, url, "wikipedia", now)
    acquisitions = extract_acquisitions(page["text"])
    for deal in acquisitions:
        sink.add("acquisition", deal, url, "wikipedia", "acquisitions_section_lines", deal, now)
    return acquisitions


def enrich_from_official_site(url, api_key, sink, fetch_log) -> bool:
    page = fetch_page(url, api_key, "official_site", fetch_log)
    if not page:
        return False
    now = fetch_log[-1]["retrieved_at"]
    if page["html"]:
        meta = BeautifulSoup(page["html"][:MAX_HTML_BYTES], "html.parser").find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            sink.add("site_description", meta["content"], url, "official_site", "meta_description", retrieved_at=now)
    extract_registry_identifiers(page["text"], sink, url, "official_site", now)
    extract_profile_regex(page["text"], sink, url, "official_site", now)
    return True


def extract_registry_search_result(result: Dict[str, Any], sink: FieldSink) -> None:
    """Identifiers already visible in the search result (URL slug, title, snippet). No page request is made, so this
    stays within the access policy when the aggregator page itself is blocked."""
    url = result.get("link") or ""
    now = utc_now()
    slug = CIN_RE.search(url.upper())
    if slug:
        sink.add("cin", slug.group(0), url, "registry_search_result", "registry_url_slug", url, now)
    snippet = " ".join(filter(None, [result.get("title"), result.get("snippet")]))
    if snippet:
        extract_registry_identifiers(snippet, sink, url, "registry_search_result", now)


def enrich_from_registry(url, api_key, sink, fetch_log) -> List[str]:
    page = fetch_page(url, api_key, "registry_aggregator", fetch_log)
    if not page:
        return []
    text, now = page["text"], fetch_log[-1]["retrieved_at"]
    extract_registry_identifiers(text, sink, url, "registry_aggregator", now)
    for field, pattern in CAPITAL_PATTERNS.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            sink.add(field, m.group(1), url, "registry_aggregator", "labelled_capital_regex", m.group(0), now)
    directors: List[str] = []
    dir_match = re.search(r"Directors of [^\.]+ are ([^\.]+)", text, re.IGNORECASE)
    if dir_match:
        directors = [s.strip() for s in re.split(r",\s*(?:and\s+)?|\s+and\s+", dir_match.group(1)) if s.strip()]
        for d in directors:
            sink.add("director_name", d, url, "registry_aggregator", "directors_sentence_regex", dir_match.group(0), now)
    subject_cin = next((i["value"] for i in sink.items if i["field"] == "cin" and i["source_url"] == url), None)
    for ident in dict.fromkeys(CIN_RE.findall(text)):
        if ident != subject_cin:
            sink.add("co_listed_identifier", ident, url, "registry_aggregator", "other_cin_on_page(unattributed)",
                     ident, now)
    return directors


@app.route("/google_and_deep_scrape", methods=["POST"])
def google_and_deep_scrape():
    if not request.is_json:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    payload = request.get_json()
    query = (payload.get("query") or "").strip()
    if not query:
        return jsonify({"error": "The 'query' field is required in the JSON payload"}), 400

    api_key = os.getenv("SERPER_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "Missing SERPER_API_KEY environment variable"}), 500

    calls = {"search": 0, "scrape_gateway": 0}
    SERPER_CALLS.set(calls)
    fetch_log: List[Dict[str, Any]] = []
    sink = FieldSink()
    try:
        serper_data = serper_search(api_key, query, 8)
        organic = serper_data.get("organic") or []
        kg = serper_data.get("knowledgeGraph") or {}
        if not organic:
            return jsonify({"error": "No organic search results found", "target_query": query,
                            "serper_calls": calls}), 404

        linkedin_url = extract_search_result_fields(serper_data, query, sink)
        wiki_url = next((r["link"] for r in organic if on_domain(r.get("link", ""), ("wikipedia.org",))), None)
        official_url = pick_official_site(serper_data)
        registry_result = next((r for r in organic if on_domain(r.get("link", ""), AGGREGATOR_DOMAINS)), None)
        if not registry_result and any(k in query.lower() for k in ["ltd", "limited", "pvt", "corporation", "inc", "services"]):
            try:
                reg_org = serper_search(api_key, f"{query} zaubacorp OR tofler", 3, timeout=8).get("organic") or []
                registry_result = next((r for r in reg_org if on_domain(r.get("link", ""), AGGREGATOR_DOMAINS)), None)
            except Exception as exc:
                logger.warning(f"Registry search failed: {exc}")
        registry_url = registry_result["link"] if registry_result else None
        if registry_result:
            extract_registry_search_result(registry_result, sink)

        profile_url = None
        acquisitions: List[str] = []
        if wiki_url:
            wiki_result = enrich_from_wikipedia(wiki_url, api_key, sink, fetch_log)
            if wiki_result is not None:
                acquisitions, profile_url = wiki_result, wiki_url
        if official_url and enrich_from_official_site(official_url, api_key, sink, fetch_log):
            profile_url = profile_url or official_url
        directors = enrich_from_registry(registry_url, api_key, sink, fetch_log) if registry_url else []

        title = kg.get("title") or organic[0].get("title", query).split(" - ")[0].strip()
        return jsonify({
            "crawler_version": CRAWLER_VERSION,
            "target_query": query,
            "source_url": profile_url or registry_url or linkedin_url or organic[0].get("link"),
            "registry_url": registry_url,
            "linkedin_url": linkedin_url,
            "company_metadata": build_markdown(title, sink, acquisitions),
            "directors_network": [{"director_name": d, "profile_url": registry_url, "associated_companies": ""}
                                  for d in directors],
            "acquisitions_count": len(acquisitions),
            "fields": sink.items,
            "fetch_log": fetch_log,
            "serper_calls": calls,
        }), 200

    except Exception as exc:
        logger.exception(f"Pipeline error: {exc}")
        return jsonify({"error": f"Scraping error: {str(exc)}", "serper_calls": calls}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
