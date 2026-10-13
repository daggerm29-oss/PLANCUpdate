"""GLEIF connector: LEI records (Level 1) and accounting-consolidation parents (Level 2).

API: https://api.gleif.org/api/v1 (no key, CC0). Documented limit: 60 requests/minute
per user; default GLEIF_RPM=50. The crawl is a bounded BFS from seed LEIs.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter, deque

from .. import db
from ..http import HttpClient, HttpError
from ..settings import settings

API = "https://api.gleif.org/api/v1"
SOURCE = "gleif"
LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
PREDICATE_BY_TYPE = {
    "IS_DIRECTLY_CONSOLIDATED_BY": "direct_parent",
    "IS_ULTIMATELY_CONSOLIDATED_BY": "ultimate_parent",
}
log = logging.getLogger(__name__)


def _date(value: str | None) -> str | None:
    return value[:10] if value else None


def _address(addr: dict | None) -> str | None:
    if not addr:
        return None
    region_postal = " ".join(p for p in (addr.get("region"), addr.get("postalCode")) if p)
    parts = [*(addr.get("addressLines") or []), addr.get("city"), region_postal, addr.get("country")]
    joined = ", ".join(p.strip() for p in parts if p and p.strip())
    return joined or None


def parse_lei_record(rec: dict) -> dict:
    """Map a GLEIF JSON:API lei-record to the common entity representation."""
    attrs = rec["attributes"]
    ent = attrs["entity"]
    reg = attrs.get("registration") or {}
    lei = attrs.get("lei") or rec["id"]
    country = (ent.get("legalAddress") or {}).get("country")
    names: list[tuple[str, str]] = []
    for item in ent.get("otherNames") or []:
        if item.get("name"):
            names.append((item["name"], "previous" if item.get("type") == "PREVIOUS_LEGAL_NAME" else "alias"))
    for item in ent.get("transliteratedOtherNames") or []:
        if item.get("name"):
            names.append((item["name"], "transliteration"))
    identifiers: list[tuple[str, str]] = [("lei", lei)]
    identifiers += [("bic", bic) for bic in attrs.get("bic") or []]
    reg_no = ent.get("registeredAs") or reg.get("validatedAs")
    if reg_no:
        identifiers.append(("registration_number", reg_no))
        if (country or "").upper() == "RU" and re.fullmatch(r"\d{13}", reg_no):
            identifiers.append(("ogrn", reg_no))  # Russian state registration numbers (OGRN) are 13 digits
    if attrs.get("ocid"):
        identifiers.append(("opencorporates", attrs["ocid"]))
    category = ent.get("category")
    kind = "organization" if category in {"RESIDENT_GOVERNMENT_ENTITY", "INTERNATIONAL_ORGANIZATION"} else "company"
    links = rec.get("relationships") or {}

    def link(kind_: str, name: str) -> str | None:
        return ((links.get(kind_) or {}).get("links") or {}).get(name)

    legal_form = ent.get("legalForm") or {}
    expiration = ent.get("expiration") or {}
    return {
        "id": f"gleif:{lei}",
        "source_id": SOURCE,
        "kind": kind,
        "name": ent["legalName"]["name"],
        "country": country,
        "jurisdiction": ent.get("jurisdiction"),
        "status": (ent.get("status") or "").lower() or None,
        "legal_form": legal_form.get("id") or legal_form.get("other"),
        "incorporation_date": _date(ent.get("creationDate")),
        "dissolution_date": _date(expiration.get("date")),
        "address": _address(ent.get("legalAddress")),
        "topics": [],
        "names": names,
        "identifiers": identifiers,
        "attrs": {
            "lei": lei,
            "category": category,
            "registration_status": reg.get("status"),
            "initial_registration_date": _date(reg.get("initialRegistrationDate")),
            "last_update_date": _date(reg.get("lastUpdateDate")),
            "next_renewal_date": _date(reg.get("nextRenewalDate")),
            "corroboration_level": reg.get("corroborationLevel"),
            "registered_at": (ent.get("registeredAt") or {}).get("id"),
            "headquarters_address": _address(ent.get("headquartersAddress")),
            "expiration_reason": expiration.get("reason"),
            "successor_lei": (ent.get("successorEntity") or {}).get("lei"),
            "direct_parent_reporting_exception": link("direct-parent", "reporting-exception"),
            "ultimate_parent_reporting_exception": link("ultimate-parent", "reporting-exception"),
        },
    }


def store_lei_record(conn: sqlite3.Connection, rec: dict, run_id: int | None, stats: Counter) -> dict:
    lei = rec["id"]
    url = f"{API}/lei-records/{lei}"
    raw_id, state = db.upsert_raw(conn, SOURCE, f"lei:{lei}", rec, url, run_id)
    stats[state] += 1
    entity = parse_lei_record(rec)
    if state != "unchanged" or not db.entity_exists(conn, entity["id"]):
        entity["raw_record_id"] = raw_id
        db.upsert_entity(conn, entity)
        db.add_evidence(
            conn,
            key=f"gleif:lei:{lei}",
            source_id=SOURCE,
            url=url,
            title=f"GLEIF LEI record {lei}",
            excerpt=(
                f"{entity['name']}; legal address: {entity['address'] or 'n/a'}; entity status: "
                f"{entity['status'] or 'n/a'}; LEI registration: {entity['attrs']['registration_status'] or 'n/a'}"
            ),
            raw_record_id=raw_id,
            entity_id=entity["id"],
        )
    return entity


def store_relationship_record(conn: sqlite3.Connection, rr: dict, run_id: int | None, stats: Counter) -> int | None:
    rel = rr["attributes"]["relationship"]
    predicate = PREDICATE_BY_TYPE.get(rel.get("type"))
    if not predicate:
        return None
    child, parent = rel["startNode"]["id"], rel["endNode"]["id"]
    kind = "direct" if predicate == "direct_parent" else "ultimate"
    url = f"{API}/lei-records/{child}/{kind}-parent-relationship"
    raw_id, state = db.upsert_raw(conn, SOURCE, f"rel:{child}:{rel['type']}:{parent}", rr, url, run_id)
    stats[f"relationship_{state}"] += 1
    periods = rel.get("periods") or []
    period = next((p for p in periods if p.get("type") == "RELATIONSHIP_PERIOD"), {})
    reg = rr["attributes"].get("registration") or {}
    rel_id = db.upsert_relationship(
        conn,
        {
            "source_id": SOURCE,
            "subject_id": f"gleif:{child}",
            "predicate": predicate,
            "object_id": f"gleif:{parent}",
            "basis": "documented",
            "confidence": 1.0,
            "start_date": _date(period.get("startDate")),
            "end_date": _date(period.get("endDate")),
            "status": (rel.get("status") or "").lower() or None,
            "attrs": {
                "gleif_type": rel.get("type"),
                "registration_status": reg.get("status"),
                "corroboration_level": reg.get("corroborationLevel"),
                "corroboration_documents": reg.get("corroborationDocuments"),
                "periods": periods,
            },
            "raw_record_id": raw_id,
        },
    )
    db.add_evidence(
        conn,
        key=f"gleif:rel:{child}:{predicate}:{parent}",
        source_id=SOURCE,
        url=url,
        title="GLEIF Level 2 relationship record",
        excerpt=(
            f"{child} {rel.get('type')} {parent}; relationship status {rel.get('status')}; "
            f"corroboration {reg.get('corroborationLevel')}; period start {_date(period.get('startDate')) or 'n/a'}"
        ),
        raw_record_id=raw_id,
        relationship_id=rel_id,
    )
    return rel_id


def store_child_edge(conn: sqlite3.Connection, child_lei: str, parent_lei: str) -> bool:
    """Edge implied by GLEIF's direct-children listing; a fetched relationship record later enriches it."""
    if db.relationship_id(conn, SOURCE, f"gleif:{child_lei}", "direct_parent", f"gleif:{parent_lei}"):
        return False
    url = f"{API}/lei-records/{parent_lei}/direct-children"
    rel_id = db.upsert_relationship(
        conn,
        {
            "source_id": SOURCE,
            "subject_id": f"gleif:{child_lei}",
            "predicate": "direct_parent",
            "object_id": f"gleif:{parent_lei}",
            "basis": "documented",
            "confidence": 1.0,
            "attrs": {"gleif_type": "IS_DIRECTLY_CONSOLIDATED_BY", "via": "direct-children listing"},
        },
    )
    db.add_evidence(
        conn,
        key=f"gleif:rel:{child_lei}:direct_parent:{parent_lei}",
        source_id=SOURCE,
        url=url,
        title="GLEIF direct-children listing",
        excerpt=f"GLEIF lists {child_lei} as a direct child (IS_DIRECTLY_CONSOLIDATED_BY) of {parent_lei}",
        relationship_id=rel_id,
    )
    return True


def search(query: str, limit: int = 10, client: HttpClient | None = None) -> list[dict]:
    client = client or HttpClient(rpm=settings.gleif_rpm)
    data = client.get_json(f"{API}/lei-records", params={"filter[fulltext]": query, "page[size]": limit}) or {}
    out = []
    for rec in data.get("data") or []:
        ent = rec["attributes"]["entity"]
        out.append(
            {
                "lei": rec["id"],
                "name": ent["legalName"]["name"],
                "country": (ent.get("legalAddress") or {}).get("country"),
                "status": ent.get("status"),
            }
        )
    return out


def crawl(
    conn: sqlite3.Connection,
    seeds: list[str],
    *,
    depth: int = 1,
    max_records: int = 200,
    max_children: int = 100,
    include_ultimate: bool = False,
    client: HttpClient | None = None,
) -> dict:
    """Breadth-first crawl of parents and children around seed LEIs, bounded by depth and record count."""
    client = client or HttpClient(rpm=settings.gleif_rpm)
    params = {"seeds": seeds, "depth": depth, "max_records": max_records, "max_children": max_children}
    run_id = db.start_run(conn, SOURCE, params)
    stats: Counter = Counter()
    records: dict[str, dict] = {}
    stored: set[str] = set()
    expanded: set[str] = set()
    queue: deque[tuple[str, int]] = deque((s.strip().upper(), 0) for s in seeds)
    status, error = "success", None

    def get_record(lei: str) -> dict | None:
        if lei not in records:
            data = client.get_json(f"{API}/lei-records/{lei}", allow_404=True)
            if not data:
                stats["not_found"] += 1
                return None
            records[lei] = data["data"]
        return records[lei]

    def ensure_stored(lei: str) -> bool:
        if lei in stored:
            return True
        if len(stored) >= max_records:
            return False
        rec = get_record(lei)
        if rec is None:
            return False
        with db.transaction(conn):
            store_lei_record(conn, rec, run_id, stats)
        stored.add(lei)
        return True

    try:
        while queue:
            lei, level = queue.popleft()
            if lei in expanded or not LEI_RE.match(lei):
                continue
            try:
                if not ensure_stored(lei) or level >= depth:
                    continue
                expanded.add(lei)
                links = records[lei].get("relationships") or {}
                for kind in ["direct-parent"] + (["ultimate-parent"] if include_ultimate else []):
                    rel_url = ((links.get(kind) or {}).get("links") or {}).get("relationship-record")
                    if not rel_url:
                        continue
                    rr = client.get_json(rel_url, allow_404=True)
                    if not rr or not rr.get("data"):
                        continue
                    parent = rr["data"]["attributes"]["relationship"]["endNode"]["id"]
                    if not ensure_stored(parent):
                        continue
                    with db.transaction(conn):
                        store_relationship_record(conn, rr["data"], run_id, stats)
                    stats["relationships"] += 1
                    if kind == "direct-parent":
                        queue.append((parent, level + 1))
                page, taken = 1, 0
                page_size = max(1, min(100, max_children))
                while taken < max_children and len(stored) < max_records:
                    data = client.get_json(
                        f"{API}/lei-records/{lei}/direct-children",
                        params={"page[size]": page_size, "page[number]": page},
                    ) or {}
                    items = data.get("data") or []
                    with db.transaction(conn):
                        for child in items:
                            if taken >= max_children or len(stored) >= max_records:
                                break
                            records[child["id"]] = child
                            store_lei_record(conn, child, run_id, stats)
                            stored.add(child["id"])
                            if store_child_edge(conn, child["id"], lei):
                                stats["relationships"] += 1
                            queue.append((child["id"], level + 1))
                            taken += 1
                    last_page = ((data.get("meta") or {}).get("pagination") or {}).get("lastPage") or 1
                    if not items or page >= last_page:
                        break
                    page += 1
            except HttpError as exc:
                stats["errors"] += 1
                status, error = "partial", str(exc)[:500]
                log.warning("GLEIF error for %s: %s", lei, exc)
    except Exception as exc:  # unexpected failure: record it, then re-raise
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        stats["http_requests"] = client.stats["requests"]
        stats["entities_stored"] = len(stored)
        db.finish_run(conn, run_id, status, stats, error)
    return {"run_id": run_id, "status": status, **stats}
