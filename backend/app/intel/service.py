"""Investigation workflow: resolve the query locally, optionally crawl, verify claims, and assemble a dossier
that keeps verified facts, unverified leads and connections apart."""
from __future__ import annotations

import re
import sqlite3

from .. import db
from ..connectors.crawler import CrawlerClient, CrawlerError, get_client
from ..graph.paths import entity_summaries, edge_label
from ..resolution.identifiers import classify
from ..resolution.matcher import AUTO_SCHEMES
from ..resolution.normalize import _LEGAL_TOKENS, normalize_identifier, normalize_name
from .claims import build_claims, claims_for, store_claims, verify_claims
from .parse import parse_response

_TOKEN = re.compile(r"\w+", re.UNICODE)
PARENT_PREDICATES = ("direct_parent", "ultimate_parent")
CAVEATS = [
    "Shared addresses, shared directors, offshore-database records and name matches are leads for review, not "
    "evidence of wrongdoing.",
    "An NSE debarment is a regulatory order with a period and may be revoked; a sanctions match must be confirmed "
    "against the official list.",
    "Web claims stay 'unverified' until a registry record confirms them; 'related' means the identifier belongs to a "
    "different company and was not merged.",
]


def search_entities(conn: sqlite3.Connection, q: str, limit: int = 25) -> tuple[list[str], list[tuple[str, str]]]:
    q = q.strip()
    detected = classify(q)
    ids: list[str] = []
    if db.entity_exists(conn, q):
        ids.append(q)
    for scheme, value in detected:
        ids += [r[0] for r in conn.execute(
            "SELECT DISTINCT entity_id FROM identifiers WHERE scheme=? AND value=? LIMIT ?", (scheme, value, limit))]
    compact = normalize_identifier("", q)
    if compact:
        ids += [r[0] for r in conn.execute(
            "SELECT DISTINCT entity_id FROM identifiers WHERE value=? LIMIT ?", (compact, limit))]
    raw_tokens = _TOKEN.findall(q.lower())
    # "pvt ltd" must not be required to appear literally in "PRIVATE LIMITED"; legal-form words carry no identity.
    tokens = [t for t in raw_tokens if t not in _LEGAL_TOKENS][:8] or raw_tokens[:8]
    if tokens:
        match = " ".join(f'"{t}"*' for t in tokens)
        ids += [r[0] for r in conn.execute(
            "SELECT entity_id FROM entity_search WHERE entity_search MATCH ? ORDER BY rank LIMIT ?", (match, limit))]
    return list(dict.fromkeys(ids))[:limit], detected


def resolve_subject(conn: sqlite3.Connection, query: str, entity_id: str | None) -> tuple[str | None, list[dict], list]:
    ids, detected = search_entities(conn, query, 10)
    summaries = entity_summaries(conn, ids)
    candidates = [summaries[i] for i in ids if i in summaries]
    if entity_id:
        return (entity_id if db.entity_exists(conn, entity_id) else None), candidates, detected
    if detected:
        hits = [c["id"] for c in candidates if conn.execute(
            "SELECT 1 FROM identifiers WHERE entity_id=? AND value IN (%s)" % ",".join("?" * len(detected)),
            [c["id"], *[v for _, v in detected]]).fetchone()]
        if hits:
            return hits[0], candidates, detected
    norm = normalize_name(query)
    exact = [c["id"] for c in candidates if norm and conn.execute(
        "SELECT 1 FROM entity_names WHERE entity_id=? AND name_norm=?", (c["id"], norm)).fetchone()]
    return (exact[0] if len(exact) == 1 else None), candidates, detected


def _rel_rows(conn, entity_id: str) -> list[dict]:
    rels = db.rows(conn.execute(
        "SELECT id, source_id, subject_id, predicate, object_id, basis, confidence, start_date, end_date, status "
        "FROM relationships WHERE subject_id=? OR object_id=? LIMIT 500", (entity_id, entity_id)))
    others = entity_summaries(conn, {r["object_id"] if r["subject_id"] == entity_id else r["subject_id"] for r in rels})
    for r in rels:
        forward = r["subject_id"] == entity_id
        r["forward"] = forward
        r["label"] = edge_label(r["predicate"], forward)
        r["other"] = others.get(r["object_id"] if forward else r["subject_id"])
    return rels


def build_dossier(conn: sqlite3.Connection, entity_id: str) -> dict:
    row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    entity = dict(row)
    entity["attrs"] = db.loads(entity["attrs"], {})
    entity["topics"] = [t for t in entity["topics"].split(",") if t]
    identifiers = db.rows(conn.execute("SELECT scheme, value FROM identifiers WHERE entity_id=?", (entity_id,)))

    facts = db.rows(conn.execute(
        "SELECT key, source_id, url, title, excerpt, retrieved_at, extraction_method, confidence FROM evidence "
        "WHERE entity_id=? AND source_id != 'crawler' ORDER BY retrieved_at DESC LIMIT 30", (entity_id,)))
    verified_claims = [c for c in claims_for(conn, entity_id=entity_id) if c["status"] == "verified"]
    # Claims verified against this record may have been made about another subject entity.
    verified_claims += [c for c in db.rows(conn.execute(
        "SELECT c.* FROM claims c JOIN evidence ev ON ev.key = 'crawler:claim:' || c.id WHERE ev.entity_id=? "
        "AND c.status='verified'", (entity_id,))) if c["id"] not in {v["id"] for v in verified_claims}]
    for c in verified_claims:
        c["verification"] = db.loads(c["verification"], {}) if isinstance(c["verification"], str) else c["verification"]

    shared_ids: list[dict] = []
    for ident in identifiers:
        if ident["scheme"] not in AUTO_SCHEMES:
            continue
        others = [r[0] for r in conn.execute(
            "SELECT entity_id FROM identifiers WHERE scheme=? AND value=? AND entity_id != ?",
            (ident["scheme"], ident["value"], entity_id))]
        for other, summary in entity_summaries(conn, others).items():
            shared_ids.append({**ident, "entity": summary})

    rels = _rel_rows(conn, entity_id)
    parents = [r for r in rels if r["predicate"] in PARENT_PREDICATES and r["forward"]]
    subsidiaries = [r for r in rels if r["predicate"] in PARENT_PREDICATES and not r["forward"]]
    ownership = [r for r in rels if r["predicate"] == "owns"]
    people = [r for r in rels if r["predicate"] in ("director_of", "officer_of", "employed_by", "intermediary_of")]
    same_as = [r for r in rels if r["predicate"] in ("same_as", "similar_to")]
    other_links = [r for r in rels if r not in parents + subsidiaries + ownership + people + same_as]

    linked_ids = {s["entity"]["id"] for s in shared_ids} | {r["other"]["id"] for r in same_as if r.get("other")}
    pending = db.rows(conn.execute(
        "SELECT id, left_id, right_id, method, score, status FROM match_candidates WHERE (left_id=? OR right_id=?) "
        "AND status != 'rejected'", (entity_id, entity_id)))
    linked_ids |= {m["right_id"] if m["left_id"] == entity_id else m["left_id"] for m in pending}
    linked = entity_summaries(conn, linked_ids)
    flags = []
    for eid, summary in linked.items():
        topics = summary.get("topics") or ""
        topics = topics if isinstance(topics, list) else [t for t in topics.split(",") if t]
        if any(t.startswith(("sanction", "debarment", "crime", "role.pep")) for t in topics) or summary["source_id"] == "icij":
            how = next((f"shares {s['scheme'].upper()} {s['value']}" for s in shared_ids if s["entity"]["id"] == eid), None)
            how = how or next((f"match candidate #{m['id']} ({m['method']}, {m['status']})" for m in pending
                               if eid in (m["left_id"], m["right_id"])), "linked record")
            flags.append({"entity": summary, "topics": topics, "via": how})
    own_flags = [t for t in entity["topics"] if t.startswith(("sanction", "debarment", "crime", "role.pep"))]

    peers = []
    if entity.get("address_norm"):
        peers = db.rows(conn.execute(
            "SELECT id FROM entities WHERE address_norm=? AND id != ? AND kind IN ('company','organization') LIMIT 26",
            (entity["address_norm"], entity_id)))
    peer_summaries = entity_summaries(conn, [p["id"] for p in peers])

    unverified = [c for c in claims_for(conn, entity_id=entity_id) if c["status"] != "verified"]
    return {
        "entity": entity,
        "identifiers": identifiers,
        "verified": {"registry_evidence": facts, "claims": verified_claims},
        "connections": {
            "same_identifier": shared_ids,
            "parents": parents,
            "subsidiaries": subsidiaries,
            "ownership": ownership,
            "people": people,
            "possible_same_entity": same_as,
            "other": other_links[:50],
            "match_candidates": pending,
            "flags": {"own_topics": own_flags, "linked": flags},
            "shared_address_peers": {"count": len(peers), "sample": list(peer_summaries.values())[:10],
                                     "capped": len(peers) > 25},
        },
        "unverified_leads": unverified,
        "caveats": CAVEATS,
    }


def investigate(conn: sqlite3.Connection, query: str, *, entity_id: str | None = None, crawl: bool = False,
                force_refresh: bool = False, client: CrawlerClient | None = None) -> dict:
    query = query.strip()
    subject_id, candidates, detected = resolve_subject(conn, query, entity_id)
    subject_name = None
    if subject_id:
        subject_name = conn.execute("SELECT name FROM entities WHERE id=?", (subject_id,)).fetchone()[0]
    out: dict = {"query": query, "detected_identifiers": [{"scheme": s, "value": v} for s, v in detected],
                 "subject_id": subject_id, "candidates": candidates, "crawl": None}

    if crawl:
        client = client or get_client()
        crawl_query = subject_name or query
        try:
            result = client.scrape(crawl_query, force=force_refresh)
            crawl_info = {"ok": True, **result.as_dict()}
            if result.status == "ok":
                parsed = parse_response(result.response)
                built = build_claims(parsed)
                with db.transaction(conn):
                    ids = store_claims(conn, subject_entity_id=subject_id, subject_query=crawl_query, claims=built,
                                       raw_record_id=result.raw_record_id)
                    check = verify_claims(conn, ids, subject_name or parsed.get("title") or crawl_query, subject_id)
                crawl_info.update({"source_url": parsed["source_url"], "registry_url": parsed["registry_url"],
                                   "claims": len(ids), "verification": check, "dropped_artifacts": parsed["dropped"],
                                   "crawler_version": parsed["crawler_version"], "linkedin_url": parsed["linkedin_url"],
                                   "fetch_log": parsed["fetch_log"], "serper_calls": parsed["serper_calls"],
                                   "fields_retrieved": sorted({f["field"] for f in parsed["fields"] or []})})
            else:
                crawl_info["message"] = (result.response or {}).get("error") or "crawler found no results"
        except CrawlerError as exc:
            crawl_info = {"ok": False, "error": exc.as_dict()}
        out["crawl"] = {**crawl_info, "query_sent": crawl_query}

    out["dossier"] = build_dossier(conn, subject_id) if subject_id else None
    if not subject_id:
        crawl_query = (out.get("crawl") or {}).get("query_sent") or query
        out["claims"] = claims_for(conn, query=crawl_query)
    return out
