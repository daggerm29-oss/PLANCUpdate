"""Deterministic investigation analytics. None of this uses the AI budget.

Every lead carries a caveat: these are patterns that deserve review, not findings of wrongdoing.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict

from .. import db
from .paths import GraphView, edge_label, entity_summaries, ownership_chain

CAVEATS = {
    "sanctions_match": "Algorithmic match between records. Confirm identity (identifiers, addresses, dates) "
                       "with the official list before treating the entity as listed.",
    "sanctions_exposure": "Ownership proximity to a listed party is not itself a designation. Check the applicable "
                          "ownership rules (e.g. OFAC 50% rule) against primary sources.",
    "circular_ownership": "Circular structures can be legitimate cross-holdings or reporting errors. Check filing "
                          "dates and relationship status.",
    "shared_address": "Corporate groups, registered agents and serviced offices commonly share an address.",
    "shared_directors": "Group executives and professional directors legitimately sit on many boards.",
    "debarment_match": "NSE lists regulatory debarment orders with a period; check the order, its period and any "
                       "revocation circular. A debarment is not a criminal finding.",
    "debarment_exposure": "Ownership or consolidation proximity to a debarred party is a lead, not a finding about "
                          "this entity.",
}


def _topics(value: str | None) -> list[str]:
    return [t for t in (value or "").split(",") if t]


def strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan (no recursion limit issues on long chains)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0
    nodes = sorted(set(graph) | {v for targets in graph.values() for v in targets})
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(sorted(graph.get(root, ()))))]
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(graph.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == node:
                        break
                result.append(component)
    return result


def ownership_cycles(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    graph: dict[str, set[str]] = defaultdict(set)  # child -> parents/owners
    for r in conn.execute("SELECT subject_id, object_id, predicate FROM relationships WHERE predicate IN ('direct_parent','owns')"):
        if r["predicate"] == "direct_parent":
            graph[r["subject_id"]].add(r["object_id"])
        else:
            graph[r["object_id"]].add(r["subject_id"])
    cycles = [c for c in strongly_connected_components(graph) if len(c) > 1 or c[0] in graph.get(c[0], set())]
    summaries = entity_summaries(conn, {n for c in cycles for n in c})
    leads = []
    for comp in cycles[:limit]:
        names = [summaries.get(n, {}).get("name", n) for n in comp]
        leads.append({
            "type": "circular_ownership",
            "title": f"Circular ownership/consolidation among {len(comp)} entities",
            "score": round(min(1.0, 0.5 + 0.1 * len(comp)), 3),
            "entity_ids": comp,
            "entities": [summaries[n] for n in comp if n in summaries],
            "explanation": "Each entity is (directly or through others) recorded as a parent/owner of the next: "
                           + " → ".join(names) + " → " + names[0],
            "caveat": CAVEATS["circular_ownership"],
        })
    return leads


def shared_addresses(conn: sqlite3.Connection, min_entities: int = 3, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        """
        SELECT address_norm, COUNT(*) AS n, GROUP_CONCAT(id, '|') AS ids, MIN(address) AS address
        FROM entities WHERE address_norm IS NOT NULL AND address_norm != '' AND kind IN ('company','organization')
        GROUP BY address_norm HAVING COUNT(*) >= ? ORDER BY n DESC LIMIT ?
        """,
        (min_entities, limit),
    ).fetchall()
    leads = []
    for r in rows:
        ids = r["ids"].split("|")[:40]
        marks = ",".join("?" * len(ids))
        linked = conn.execute(
            f"SELECT COUNT(*) FROM relationships WHERE subject_id IN ({marks}) AND object_id IN ({marks})",
            ids + ids,
        ).fetchone()[0]
        unrelated_share = max(0.0, 1.0 - linked / max(1, len(ids) - 1))
        summaries = entity_summaries(conn, ids)
        leads.append({
            "type": "shared_address",
            "title": f"{r['n']} organisations registered at one address",
            "score": round(min(1.0, 0.2 + 0.05 * r["n"]) * (0.4 + 0.6 * unrelated_share), 3),
            "entity_ids": ids,
            "entities": [summaries[i] for i in ids if i in summaries],
            "explanation": f"Address: {r['address']}. {linked} recorded relationship(s) link these entities to each "
                           "other; unrelated entities at one address are more notable than a group sharing an HQ.",
            "caveat": CAVEATS["shared_address"],
        })
    return leads


def shared_directors(conn: sqlite3.Connection, min_orgs: int = 2, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        """
        SELECT subject_id, COUNT(DISTINCT object_id) AS n, GROUP_CONCAT(DISTINCT object_id) AS orgs
        FROM relationships WHERE predicate = 'director_of'
        GROUP BY subject_id HAVING COUNT(DISTINCT object_id) >= ? ORDER BY n DESC LIMIT ?
        """,
        (min_orgs, limit),
    ).fetchall()
    leads = []
    for r in rows:
        orgs = r["orgs"].split(",")
        summaries = entity_summaries(conn, [r["subject_id"], *orgs])
        person = summaries.get(r["subject_id"], {"name": r["subject_id"]})
        leads.append({
            "type": "shared_directors",
            "title": f"{person['name']} is recorded as director of {r['n']} organisations",
            "score": round(min(1.0, 0.3 + 0.1 * r["n"]), 3),
            "entity_ids": [r["subject_id"], *orgs],
            "entities": [summaries[i] for i in [r["subject_id"], *orgs] if i in summaries],
            "explanation": "Organisations: " + ", ".join(summaries.get(o, {}).get("name", o) for o in orgs),
            "caveat": CAVEATS["shared_directors"],
        })
    return leads


def sanctions_leads(conn: sqlite3.Connection, max_hops: int = 2, limit: int = 100, *, topic: str = "sanction",
                    lead_types: tuple[str, str] = ("sanctions_match", "sanctions_exposure"),
                    record_label: str = "a sanctions-related record") -> list[dict]:
    """Matches to a listed record (topic prefix) plus owners/subsidiaries of the matched entity."""
    match_type, exposure_type = lead_types
    pattern = f"%{topic}%"
    matches = conn.execute(
        """
        SELECT m.*, l.topics AS l_topics, r.topics AS r_topics FROM match_candidates m
        JOIN entities l ON l.id = m.left_id JOIN entities r ON r.id = m.right_id
        WHERE m.status != 'rejected' AND (l.topics LIKE ? OR r.topics LIKE ?)
        """,
        (pattern, pattern),
    ).fetchall()
    graph = GraphView(conn)
    best: dict[tuple[str, str], dict] = {}
    for m in matches:
        l_listed = any(t.startswith(topic) for t in _topics(m["l_topics"]))
        listed, subject = (m["left_id"], m["right_id"]) if l_listed else (m["right_id"], m["left_id"])
        listed_topics = _topics(m["l_topics"] if l_listed else m["r_topics"])
        weight = float(m["score"]) * (1.0 if m["status"] in ("auto", "confirmed") else 0.6)
        weight *= 1.0 if topic in listed_topics else 0.7  # sanction.linked, debarment.revoked etc. weigh less
        summaries = entity_summaries(conn, [listed, subject])
        listed_name = summaries.get(listed, {}).get("name", listed)
        subject_name = summaries.get(subject, {}).get("name", subject)
        key = (subject, listed)
        lead = {
            "type": match_type,
            "title": f"{subject_name} matches {record_label}",
            "score": round(weight, 3),
            "entity_ids": [subject, listed],
            "entities": [summaries[i] for i in (subject, listed) if i in summaries],
            "explanation": f"Match #{m['id']} ({m['method']}, status {m['status']}) links {subject_name} to "
                           f"{listed_name} (topics: {', '.join(listed_topics)}).",
            "caveat": CAVEATS[match_type],
            "match_id": m["id"],
        }
        if key not in best or best[key]["score"] < lead["score"]:
            best[key] = lead
        for direction in ("up", "down"):
            chain = ownership_chain(conn, subject, direction, max_depth=max_hops, max_nodes=200, follow_same_as=False)
            for node in chain["nodes"][1:]:
                neighbour = node["id"]
                hops = node["depth"]
                relation = "an owner/parent" if direction == "up" else "a subsidiary/asset"
                score = weight * (0.6 ** hops) / (1 + 0.15 * math.log1p(graph.degree(neighbour)))
                nkey = (neighbour, listed)
                if nkey in best and best[nkey]["score"] >= score:
                    continue
                n_name = node["entity"].get("name", neighbour)
                best[nkey] = {
                    "type": exposure_type,
                    "title": f"{n_name} is {relation} of an entity matched to {record_label}",
                    "score": round(score, 3),
                    "entity_ids": [neighbour, subject, listed],
                    "entities": [node["entity"], *[summaries[i] for i in (subject, listed) if i in summaries]],
                    "explanation": f"{n_name} is {relation} ({hops} hop(s), via documented ownership/consolidation "
                                   f"records) of {subject_name}, which matches {listed_name} (match #{m['id']}, "
                                   f"{m['status']}).",
                    "caveat": CAVEATS[exposure_type],
                    "match_id": m["id"],
                }
    return sorted(best.values(), key=lambda x: -x["score"])[:limit]


def all_leads(conn: sqlite3.Connection, lead_type: str | None = None, limit: int = 100) -> list[dict]:
    producers = {
        "sanctions": lambda: sanctions_leads(conn, limit=limit),
        "debarment": lambda: sanctions_leads(conn, limit=limit, topic="debarment",
                                             lead_types=("debarment_match", "debarment_exposure"),
                                             record_label="an NSE debarment record"),
        "circular_ownership": lambda: ownership_cycles(conn, limit=limit),
        "shared_address": lambda: shared_addresses(conn, limit=limit),
        "shared_directors": lambda: shared_directors(conn, limit=limit),
    }
    leads: list[dict] = []
    for name, produce in producers.items():
        if lead_type in (None, name) or (lead_type or "").startswith(name):
            leads += produce()
    return sorted(leads, key=lambda x: -x["score"])[:limit]


def timeline(conn: sqlite3.Connection, entity_id: str) -> list[dict]:
    e = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    if e is None:
        return []
    attrs = db.loads(e["attrs"], {})
    events: list[dict] = []

    def add(date, kind, label, source=None, ref=None):
        if date:
            events.append({"date": str(date)[:10], "type": kind, "label": label, "source_id": source or e["source_id"], "ref": ref})

    add(e["incorporation_date"], "incorporated", "Created / incorporated (per source)")
    add(e["dissolution_date"], "dissolved", "Expired / dissolved (per source)")
    add(attrs.get("initial_registration_date"), "lei_issued", "LEI first registered")
    add(attrs.get("last_update_date"), "lei_updated", f"LEI record last updated (status {attrs.get('registration_status')})")
    add(attrs.get("first_seen"), "first_seen", "First seen by OpenSanctions")
    add(attrs.get("last_change"), "changed", "Last changed in OpenSanctions")
    for s in attrs.get("sanctions") or []:
        add(s.get("start_date"), "sanction", f"Listed by {s.get('authority') or 'authority'} ({s.get('program') or 'program n/a'})", ref=s.get("id"))
        add(s.get("end_date"), "sanction_end", f"Listing ended ({s.get('authority') or 'authority'})", ref=s.get("id"))
    rels = conn.execute(
        "SELECT * FROM relationships WHERE subject_id=? OR object_id=?", (entity_id, entity_id)
    ).fetchall()
    others = entity_summaries(conn, {r["object_id"] if r["subject_id"] == entity_id else r["subject_id"] for r in rels})
    for r in rels:
        forward = r["subject_id"] == entity_id
        other = others.get(r["object_id"] if forward else r["subject_id"], {}).get("name", "?")
        label = f"{edge_label(r['predicate'], forward)} {other}"
        add(r["start_date"], "relationship_start", f"Start: {label}", r["source_id"], r["id"])
        add(r["end_date"], "relationship_end", f"End: {label}", r["source_id"], r["id"])
    if e["raw_record_id"]:
        for h in conn.execute("SELECT valid_to FROM raw_record_history WHERE raw_record_id=?", (e["raw_record_id"],)):
            add(h["valid_to"], "record_revised", "Source record changed (previous version archived)")
    return sorted(events, key=lambda x: x["date"])


def conflicts(conn: sqlite3.Connection, entity_id: str) -> list[dict]:
    """Field disagreements between this record and records linked to it by same_as."""
    cluster = [entity_id] + [
        r[0] for r in conn.execute(
            "SELECT CASE WHEN subject_id=? THEN object_id ELSE subject_id END FROM relationships "
            "WHERE predicate='same_as' AND (subject_id=? OR object_id=?)",
            (entity_id, entity_id, entity_id),
        )
    ]
    if len(cluster) < 2:
        return []
    marks = ",".join("?" * len(cluster))
    records = conn.execute(f"SELECT * FROM entities WHERE id IN ({marks})", cluster).fetchall()
    out = []
    for field in ("country", "incorporation_date", "dissolution_date", "status"):
        values = [{"entity_id": r["id"], "source_id": r["source_id"], "value": r[field]} for r in records if r[field]]
        distinct = {str(v["value"]).lower()[:10] if field.endswith("date") else str(v["value"]).lower() for v in values}
        if len(distinct) > 1:
            out.append({"field": field, "values": values})
    for scheme in ("registration_number", "inn", "ogrn"):
        values = [
            {"entity_id": r["entity_id"], "value": r["value"]}
            for r in conn.execute(f"SELECT entity_id, value FROM identifiers WHERE scheme=? AND entity_id IN ({marks})", [scheme, *cluster])
        ]
        if len({v["value"] for v in values}) > 1 and len({v["entity_id"] for v in values}) > 1:
            out.append({"field": scheme, "values": values})
    return out
