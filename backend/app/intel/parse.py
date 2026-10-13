"""Parse the crawler's Markdown dossier into discrete, attributable statements.

Crawler v2 also returns `fields` (one entry per extracted value with source_url, source_type, method, excerpt and
retrieved_at) and `fetch_log`; when present, claims are built from `fields` and the Markdown is only used for display.
The rules below apply to v1 responses.

Known artefacts of services/crawler/app.py that are dropped rather than stored (see INTEGRATION_PLAN.md):
- "Company Status | Active" in the registry table is hard-coded.
- "Disclosed" is a placeholder when a regex found nothing.
- The director "Key Managerial Personnel / Corporate Network" and the row "Statutory Directorships" are placeholders.
- The "Status" column of associated companies is hard-coded "Active".
- Every director receives the same associated-companies table, so those rows are attributed to the registry page,
  not to any individual director.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..resolution.identifiers import CIN_FIND_RE, LLPIN_RE

CELL_SPLIT = re.compile(r"(?<!\\)\|")
PLACEHOLDER_VALUES = {"", "disclosed", "n/a", "-"}
PLACEHOLDER_DIRECTORS = {"key managerial personnel / corporate network"}
PLACEHOLDER_ASSOC = {"statutory directorships"}
HARDCODED_REGISTRY_FIELDS = {"company status"}
REGISTRY_DOMAINS = ("zaubacorp.com", "tofler.in")
KPI_FIELDS = {
    "headquarters": "headquarters",
    "revenue": "revenue",
    "annual revenue": "revenue",
    "company size / workforce": "workforce",
    "stock ticker": "stock_ticker",
    "stock exchange ticker": "stock_ticker",
}
REGISTRY_FIELDS = {"cin": "cin", "authorized capital": "authorized_capital", "paid-up capital": "paid_up_capital"}
TRACKING_PARAMS = re.compile(r"^(utm_|gclid|fbclid|ref$|trk$)")


def canonical_url(url: str | None) -> str | None:
    """Lower-case host without www, no fragment, no tracking parameters, no trailing slash."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return url.strip()
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k.lower())))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https" if parts.scheme in ("http", "https") else parts.scheme, host, path, query, ""))


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return host or None


def _cells(line: str) -> list[str]:
    inner = line.strip().strip("|")
    return [c.strip().replace("\\|", "|") for c in CELL_SPLIT.split(inner)]


def _tables(lines: list[str]) -> list[list[list[str]]]:
    tables, current = [], []
    for line in lines:
        if line.strip().startswith("|"):
            cells = _cells(line)
            if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
                continue
            current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _sections(markdown: str) -> tuple[str | None, str | None, dict[str, list[str]]]:
    title = overview = None
    sections: dict[str, list[str]] = {}
    current = None
    for line in (markdown or "").splitlines():
        if line.startswith("# ") and title is None:
            title = re.sub(r"\s*-\s*STRATEGIC CORPORATE DOSSIER\s*$", "", line[2:].strip(), flags=re.I) or None
        elif line.startswith("> **Corporate Overview**:"):
            overview = line.split(":", 1)[1].strip() or None
        elif line.startswith("### "):
            current = line[4:].strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return title, overview, sections


def _section(sections: dict[str, list[str]], prefix: str) -> list[str]:
    return next((v for k, v in sections.items() if k.startswith(prefix)), [])


def parse_response(resp: dict) -> dict:
    title, overview, sections = _sections(resp.get("company_metadata") or "")
    dropped: list[str] = []

    kpis: dict[str, str] = {}
    for table in _tables(_section(sections, "corporate profile")):
        for row in table[1:]:
            if len(row) >= 2 and row[0].lower() in KPI_FIELDS and row[1].lower() not in PLACEHOLDER_VALUES:
                kpis[KPI_FIELDS[row[0].lower()]] = row[1]

    acquisitions: list[dict] = []
    for table in _tables(_section(sections, "major acquisitions")):
        for row in table[1:]:
            if len(row) >= 2 and row[1]:
                year = row[0] if re.fullmatch(r"(19|20)\d{2}", row[0]) else None
                acquisitions.append({"year": year, "text": row[1]})

    registry: dict[str, str] = {}
    for table in _tables(_section(sections, "statutory registry")):
        for row in table[1:]:
            if len(row) < 2:
                continue
            field = row[0].lower()
            if field in HARDCODED_REGISTRY_FIELDS:
                dropped.append(f"registry '{row[0]}' = '{row[1]}' (hard-coded by the crawler)")
            elif field in REGISTRY_FIELDS:
                if row[1].lower() in PLACEHOLDER_VALUES:
                    dropped.append(f"registry '{row[0]}' placeholder '{row[1]}'")
                else:
                    registry[REGISTRY_FIELDS[field]] = row[1]
    if "cin" in registry and not CIN_FIND_RE.search(registry["cin"].upper()):
        dropped.append(f"registry CIN '{registry.pop('cin')}' is not a valid CIN")

    directors: list[str] = []
    associated: dict[str, dict] = {}
    registry_url = None
    for item in resp.get("directors_network") or []:
        name = re.sub(r"\s+", " ", str(item.get("director_name") or "")).strip()
        url = item.get("profile_url")
        if url and any(d in (domain_of(url) or "") for d in REGISTRY_DOMAINS):
            registry_url = registry_url or url
        if name.lower() in PLACEHOLDER_DIRECTORS:
            dropped.append(f"placeholder director '{name}'")
        elif name and name not in directors:
            directors.append(name)
        for table in _tables((item.get("associated_companies") or "").splitlines()):
            for row in table[1:]:
                ident = row[0].upper() if row else ""
                if ident.lower() in PLACEHOLDER_ASSOC:
                    continue
                if (CIN_FIND_RE.fullmatch(ident) or LLPIN_RE.match(ident)) and len(row) >= 2:
                    associated.setdefault(ident, {"identifier": ident, "name": row[1]})

    fields = resp.get("fields")
    if isinstance(fields, list):
        fields = [f for f in fields if isinstance(f, dict) and f.get("field") and f.get("value")]
    else:
        fields = None
    return {
        "crawler_version": resp.get("crawler_version") or "1",
        "target_query": resp.get("target_query"),
        "title": title,
        "overview": overview,
        "source_url": resp.get("source_url"),
        "registry_url": resp.get("registry_url") or registry_url,
        "linkedin_url": resp.get("linkedin_url"),
        "fields": fields,
        "fetch_log": [e for e in resp.get("fetch_log") or [] if isinstance(e, dict)],
        "serper_calls": resp.get("serper_calls") if isinstance(resp.get("serper_calls"), dict) else None,
        "kpis": kpis,
        "acquisitions": acquisitions,
        "registry": registry,
        "directors": directors,
        "associated": list(associated.values()),
        "dropped": dropped,
    }
