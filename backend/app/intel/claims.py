"""Claims: material statements from the crawler, stored with provenance and verified against registry records.

Status meanings
  unverified  no loaded structured record confirms it (default for every web statement)
  verified    a registry record (MCA / GLEIF) confirms it; `verification` says which and how
  conflict    a registry record contradicts it (both values are kept)
  related     the identifier exists but belongs to a differently named company: a possible parent, subsidiary or
              affiliate, or just another company on the same page. Never merged into the subject.
Claims never create graph edges. Only analysts confirming a match candidate do that (see resolution/matcher.py).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from urllib.parse import quote_plus

from .. import db
from ..resolution.identifiers import CIN_FIND_RE
from ..resolution.matcher import name_similarity, upsert_candidate
from ..resolution.normalize import normalize_identifier, normalize_name
from .parse import canonical_url, domain_of

SOURCE = "crawler"
NAME_MATCH = 88.0
DOMAIN_CONFIDENCE = {"wikipedia.org": 0.5, "zaubacorp.com": 0.5, "tofler.in": 0.5, "linkedin.com": 0.35}
RESTRICTED_TERMS = {"linkedin.com": "LinkedIn's user agreement prohibits automated scraping; treat as a pointer only."}
REGISTRY_SOURCES = ("mca", "gleif")


def _domain_confidence(url: str | None) -> float:
    domain = domain_of(url) or ""
    return next((v for d, v in DOMAIN_CONFIDENCE.items() if domain.endswith(d)), 0.3)


def _amount(value: str | None) -> float | None:
    cleaned = re.sub(r"[^\d.]", "", value or "")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


FIELD_CLAIM_TYPES = {
    "cin": "identifier", "co_listed_identifier": "associated_company", "director_name": "director",
    "parent_company_mention": "relationship_mention", "subsidiary_mention": "relationship_mention",
    "key_people_mention": "officer_mention", "acquisition": "acquisition", "description": "overview",
    "site_description": "overview", "linkedin_url": "profile_link",
}
SOURCE_TYPE_CONFIDENCE = {"official_site": 0.6, "wikipedia": 0.5, "registry_aggregator": 0.5,
                          "registry_search_result": 0.45,
                          "search_knowledge_graph": 0.4, "linkedin_search_result": 0.35, "search_result": 0.3}
FIELD_FACTOR = {"director_name": 0.9, "parent_company_mention": 0.8, "subsidiary_mention": 0.8,
                "key_people_mention": 0.8, "co_listed_identifier": 0.6, "acquisition": 0.8}
WEAK_METHODS = ("unlabelled", "unattributed")


def build_claims_from_fields(fields: list[dict]) -> list[dict]:
    out = []
    for f in fields:
        field, value = str(f["field"]), str(f["value"]).strip()
        source_type = str(f.get("source_type") or "unknown")
        method = str(f.get("method") or "")
        confidence = SOURCE_TYPE_CONFIDENCE.get(source_type, 0.3) * FIELD_FACTOR.get(field, 1.0)
        if any(w in method for w in WEAK_METHODS):
            confidence *= 0.7
        if field == "cin":
            m = CIN_FIND_RE.search(value.upper())
            if not m:
                continue
            value = m.group(0)
        out.append({"claim_type": FIELD_CLAIM_TYPES.get(field, "attribute"), "field": field, "value": value[:500],
                    "source_url": f.get("source_url"),
                    "evidence_excerpt": str(f.get("excerpt") or value)[:600],
                    "extraction_method": f"crawler:{source_type}:{method}"[:200],
                    "confidence": round(min(confidence, 0.6), 2), "retrieved_at": f.get("retrieved_at")})
    return out


def build_claims(parsed: dict) -> list[dict]:
    """Turn a parsed crawl into claim dicts (not yet stored)."""
    if parsed.get("fields") is not None:
        return build_claims_from_fields(parsed["fields"])
    out: list[dict] = []
    reg_url = parsed.get("registry_url")
    prof_url = parsed.get("source_url")

    def add(claim_type, field, value, url, excerpt, method, confidence):
        if value:
            out.append({"claim_type": claim_type, "field": field, "value": str(value).strip()[:500], "source_url": url,
                        "evidence_excerpt": excerpt[:600], "extraction_method": method, "confidence": round(confidence, 2)})

    reg = parsed.get("registry") or {}
    reg_conf = _domain_confidence(reg_url)
    if reg.get("cin"):
        cin = CIN_FIND_RE.search(reg["cin"].upper()).group(0)
        add("identifier", "cin", cin, reg_url, f"Registry table on the crawled page lists CIN {cin}.",
            "crawler:registry_page_regex(first CIN on page)", reg_conf)
    for field in ("authorized_capital", "paid_up_capital"):
        if reg.get(field):
            add("attribute", field, reg[field], reg_url, f"Registry table lists {field.replace('_', ' ')} {reg[field]}.",
                "crawler:registry_page_regex", reg_conf)
    for name in parsed.get("directors") or []:
        add("director", "director_name", name, reg_url,
            f"Registry page sentence 'Directors of ... are ...' names {name}.",
            "crawler:registry_sentence_regex", reg_conf * 0.9)
    for item in parsed.get("associated") or []:
        add("associated_company", "co_listed_identifier", item["identifier"], reg_url,
            f"{item['identifier']} {item['name']} appears in a table on the same registry page; the crawler does not "
            "say how (or whether) it is related to the subject or to any director.",
            "crawler:registry_page_table_regex(unattributed)", 0.3)
    prof_conf = _domain_confidence(prof_url)
    for field, value in (parsed.get("kpis") or {}).items():
        add("attribute", field, value, prof_url, f"Profile page states {field.replace('_', ' ')}: {value}.",
            "crawler:profile_page_regex", prof_conf * 0.8)
    for deal in parsed.get("acquisitions") or []:
        add("acquisition", "acquisition", deal["text"], prof_url, f"Profile page M&A line: {deal['text']}",
            "crawler:profile_page_keyword_lines", prof_conf * 0.8)
    if parsed.get("overview"):
        # The snippet comes from the Serper search itself, not a page, so the reproducible search URL is its source.
        query = parsed.get("target_query")
        search_url = ("https://www.google.com/search?q=" + quote_plus(query)) if query else prof_url
        add("overview", "description", parsed["overview"], search_url,
            "Google knowledge-graph description or first organic snippet returned through Serper.",
            "crawler:search_snippet", 0.3)
    return out


def claim_key(subject_query: str, claim: dict) -> str:
    basis = "|".join([normalize_name(subject_query) or subject_query.lower(), claim["claim_type"], claim["field"],
                      normalize_identifier("", claim["value"]) if claim["claim_type"] in ("identifier", "associated_company")
                      else re.sub(r"\s+", " ", claim["value"].lower()),
                      canonical_url(claim.get("source_url")) or ""])
    return "crawler:" + hashlib.sha256(basis.encode()).hexdigest()[:32]


def store_claims(conn: sqlite3.Connection, *, subject_entity_id: str | None, subject_query: str, claims: list[dict],
                 raw_record_id: int | None) -> list[int]:
    now = db.utcnow()
    ids = []
    for c in claims:
        domain = domain_of(c.get("source_url"))
        row = conn.execute(
            """
            INSERT INTO claims(key, subject_entity_id, subject_query, claim_type, field, value, status, source_id,
                source_url, source_domain, retrieved_at, evidence_excerpt, extraction_method, confidence, raw_record_id,
                verification, first_seen_at, updated_at)
            VALUES (?,?,?,?,?,?, 'unverified', ?,?,?,?,?,?,?,?, '{}', ?, ?)
            ON CONFLICT(key) DO UPDATE SET retrieved_at=excluded.retrieved_at, raw_record_id=excluded.raw_record_id,
                subject_entity_id=COALESCE(excluded.subject_entity_id, claims.subject_entity_id),
                evidence_excerpt=excluded.evidence_excerpt, updated_at=excluded.updated_at
            RETURNING id
            """,
            (claim_key(subject_query, c), subject_entity_id, subject_query, c["claim_type"], c["field"], c["value"],
             SOURCE, c.get("source_url"), domain, c.get("retrieved_at") or now, c["evidence_excerpt"], c["extraction_method"], c["confidence"],
             raw_record_id, now, now),
        ).fetchone()
        ids.append(int(row[0]))
    return ids


def _entities_with(conn, scheme: str, value: str) -> list[dict]:
    return db.rows(conn.execute(
        "SELECT e.id, e.name, e.name_norm, e.source_id, e.status, e.kind, e.attrs FROM identifiers i "
        "JOIN entities e ON e.id = i.entity_id WHERE i.scheme=? AND i.value=?", (scheme, value)))


def _set(conn, claim_id: int, status: str, verification: dict, confidence: float | None = None) -> None:
    conn.execute("UPDATE claims SET status=?, verification=?, confidence=COALESCE(?, confidence), updated_at=? WHERE id=?",
                 (status, db.dumps(verification), confidence, db.utcnow(), claim_id))


PARENT_PREDICATES = ("direct_parent", "ultimate_parent")


def _check_relationship_mention(conn, claim: dict, subject_entity_id: str | None) -> tuple[str, dict]:
    """A web page naming a parent or subsidiary is verified only by a registry relationship record (GLEIF Level 2)
    between the subject and an entity carrying that name. A matching name alone never links the two."""
    is_parent = claim["field"] == "parent_company_mention"
    rows = db.rows(conn.execute(
        "SELECT DISTINCT e.id, e.name, e.source_id FROM entity_names n JOIN entities e ON e.id = n.entity_id "
        "WHERE n.name_norm=? AND e.kind != 'person' LIMIT 10", (normalize_name(claim["value"]),)))
    info = {"method": "exact_normalised_name_lookup+relationship_lookup", "relationship_verified": False,
            "direction": "subject -> parent" if is_parent else "subsidiary -> subject", "name_matches": rows}
    if subject_entity_id:
        marks = ",".join("?" * len(PARENT_PREDICATES))
        for r in rows:
            child, parent = (subject_entity_id, r["id"]) if is_parent else (r["id"], subject_entity_id)
            rel = conn.execute(f"SELECT predicate, source_id FROM relationships WHERE subject_id=? AND object_id=? "
                               f"AND predicate IN ({marks}) LIMIT 1", (child, parent, *PARENT_PREDICATES)).fetchone()
            if rel:
                info.update(relationship_verified=True, registry_entity=r["id"], predicate=rel[0], registry_source=rel[1],
                            reason=f"{rel[1].upper()} records {child} {rel[0]} {parent}.")
                return "verified", info
    info["reason"] = ("A web page names this company as the subject's " + ("parent" if is_parent else "subsidiary")
                      + ". No loaded registry relationship confirms it" + (", and no registry entity carries the name"
                                                                         if not rows else "") +
                      ". Names are not linked to entities until identifiers match.")
    return "unverified", info


def verify_claims(conn: sqlite3.Connection, claim_ids: list[int], subject_name: str,
                  subject_entity_id: str | None) -> dict:
    """Decide each claim's status from loaded registry data. Returns counts per status and new review candidates."""
    if not claim_ids:
        return {"counts": {}, "review_candidates": []}
    marks = ",".join("?" * len(claim_ids))
    claims = db.rows(conn.execute(f"SELECT * FROM claims WHERE id IN ({marks})", claim_ids))
    subject_norm = normalize_name(subject_name)
    anchor: dict | None = None  # registry record the verified CIN points to
    candidates: list[int] = []

    for c in [c for c in claims if c["field"] == "cin"]:
        cin = normalize_identifier("cin", c["value"])
        records = [r for r in _entities_with(conn, "cin", cin) if r["source_id"] in REGISTRY_SOURCES]
        if not records:
            _set(conn, c["id"], "unverified", {"method": "identifier_lookup", "reason":
                 f"No MCA or GLEIF record with CIN {cin} is loaded. Load the MCA file for state "
                 f"'{cin[6:8]}' to check it."})
            continue
        scored = sorted(((name_similarity(subject_norm, r["name_norm"]), r) for r in records), key=lambda x: -x[0])
        score, best = scored[0]
        info = {"method": "identifier_lookup+name_match", "registry_records": [
            {"id": r["id"], "name": r["name"], "source_id": r["source_id"], "status": r["status"]} for _, r in scored],
            "name_score": round(score, 1), "subject_name": subject_name}
        if subject_entity_id in {r["id"] for r in records} or score >= NAME_MATCH:
            anchor = best
            info["reason"] = f"CIN {cin} is registered to '{best['name']}' ({best['source_id']}), matching the subject."
            _set(conn, c["id"], "verified", info, max(c["confidence"], 0.9))
            db.add_evidence(conn, key=f"crawler:claim:{c['id']}", source_id=SOURCE, url=c["source_url"],
                            title=f"Web page cites CIN {cin}", excerpt=c["evidence_excerpt"], entity_id=best["id"],
                            raw_record_id=c["raw_record_id"], extraction_method=c["extraction_method"],
                            confidence=c["confidence"])
            if subject_entity_id and subject_entity_id != best["id"] and db.entity_exists(conn, subject_entity_id):
                candidates.append(upsert_candidate(conn, subject_entity_id, best["id"], "crawler_cin", round(score / 100, 3), {
                    "identifiers": [{"scheme": "cin", "value": cin}], "via": "crawler claim", "claim_id": c["id"],
                    "left_name": subject_name, "right_name": best["name"], "adjusted_score": round(score, 1)}, "pending"))
        else:
            info["reason"] = (f"CIN {cin} belongs to '{best['name']}', whose name does not match '{subject_name}'. It may be "
                              "a parent, subsidiary or affiliate, or another company shown on the same page. Not merged.")
            _set(conn, c["id"], "related", info)

    for c in claims:
        if c["field"] == "cin":
            continue
        if c["field"] in ("authorized_capital", "paid_up_capital"):
            if anchor is None:
                _set(conn, c["id"], "unverified", {"reason": "No verified CIN to anchor the capital figure to."})
                continue
            recorded = db.loads(anchor["attrs"], {}).get(c["field"])
            claimed = _amount(c["value"])
            info = {"method": "registry_attribute_compare", "registry_entity": anchor["id"], "registry_value": recorded,
                    "claimed_value": claimed}
            if recorded is None or claimed is None:
                _set(conn, c["id"], "unverified", {**info, "reason": "Registry record has no comparable value."})
            elif abs(recorded - claimed) < 0.5:
                _set(conn, c["id"], "verified", {**info, "reason": "Matches the registry record."}, 0.9)
            else:
                _set(conn, c["id"], "conflict", {**info, "reason": "Differs from the registry record (figures may be "
                                                 "from different dates; the registry file is the dated primary source)."})
        elif c["field"] == "co_listed_identifier":
            value = normalize_identifier("", c["value"])
            records = _entities_with(conn, "cin", value) + _entities_with(conn, "llpin", value)
            info = {"method": "identifier_lookup", "relationship_verified": False,
                    "registry_records": [{"id": r["id"], "name": r["name"], "status": r["status"]} for r in records],
                    "reason": ("The company exists in the registry, but nothing loaded confirms how it relates to the subject."
                               if records else "Not in the loaded registry data.")}
            _set(conn, c["id"], "unverified", info)
        elif c["field"] == "director_name":
            rows = conn.execute(
                "SELECT e.id, e.name, e.source_id, e.topics FROM entity_names n JOIN entities e ON e.id = n.entity_id "
                "WHERE n.name_norm=? AND e.kind='person' LIMIT 10", (normalize_name(c["value"]),)).fetchall()
            info = {"method": "exact_normalised_name_lookup", "relationship_verified": False,
                    "name_matches": [dict(r) for r in rows],
                    "reason": "MCA company master data has no director lists, so the directorship is not verified."
                              + (" Same-name person records exist; names collide often, so compare DIN/PAN before "
                                 "treating them as the same person." if rows else "")}
            _set(conn, c["id"], "unverified", info)
        elif c["field"] in ("parent_company_mention", "subsidiary_mention"):
            status, info = _check_relationship_mention(conn, c, subject_entity_id)
            _set(conn, c["id"], status, info)
        elif c["field"] == "key_people_mention":
            _set(conn, c["id"], "unverified", {"relationship_verified": False, "reason":
                 "Officers named on a web page are not statutory directors. Only MCA director data (DIN) can "
                 "establish a directorship; this name is kept separate from directors."})
        else:
            reason = "No structured source is loaded that could confirm this statement."
            restricted = next((note for d, note in RESTRICTED_TERMS.items()
                               if (c["source_domain"] or "").endswith(d)), None)
            if restricted:
                reason += " " + restricted + " The value comes from the public search-result snippet, not the page."
            _set(conn, c["id"], "unverified", {"reason": reason})

    counts = dict(conn.execute(f"SELECT status, COUNT(*) FROM claims WHERE id IN ({marks}) GROUP BY status",
                               claim_ids).fetchall())
    return {"counts": counts, "review_candidates": candidates, "anchor": anchor["id"] if anchor else None}


def claims_for(conn: sqlite3.Connection, *, entity_id: str | None = None, query: str | None = None,
               limit: int = 200) -> list[dict]:
    if entity_id:
        rows = conn.execute("SELECT * FROM claims WHERE subject_entity_id=? ORDER BY status, confidence DESC LIMIT ?",
                            (entity_id, limit))
    else:
        rows = conn.execute("SELECT * FROM claims WHERE subject_query=? ORDER BY status, confidence DESC LIMIT ?",
                            (query, limit))
    out = db.rows(rows)
    for r in out:
        r["verification"] = db.loads(r["verification"], {})
    return out
