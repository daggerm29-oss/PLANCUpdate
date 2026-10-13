"""Synthetic demo fixture. Every record is fictional and stored under source `demo` so it can never be
mistaken for real GLEIF/OpenSanctions data. Used by tests and to explore the UI before any real ingest.
Remove with `python -m app.cli demo --remove`."""
from __future__ import annotations

import sqlite3

from . import db

SOURCE = ("demo", "Synthetic demo fixture (fictional records)", None, "n/a: fictional test data")
SHARED_ADDRESS = "1 Example Quay, Suite 100, Limassol, CY"

ENTITIES = [
    ("demo:alpha", "company", "Alpha Holdings Ltd", "gb", "2011-03-04", SHARED_ADDRESS, [], [("lei", "DEMO00000000000000A1")]),
    ("demo:beta", "company", "Beta Trading LLC", "cy", "2015-06-01", SHARED_ADDRESS, [], []),
    ("demo:delta", "company", "Delta Investments SA", "pa", "2016-01-20", SHARED_ADDRESS, [], []),
    ("demo:gamma", "company", "Gamma Shipping Co", "ae", "2014-09-09", None, ["sanction"], [("imo", "9999999")]),
    ("demo:epsilon", "company", "Epsilon Logistics Ltd", "gb", "2018-11-11", None, [], []),
    ("demo:person1", "person", "Jordan Sample", "cy", None, None, [], []),
    ("demo:hub", "organization", "Global Registered Agents Ltd", "cy", "2001-01-01", None, [], []),
]

RELATIONSHIPS = [
    ("demo:alpha", "owns", "demo:beta", "documented", 1.0, "2015-06-01"),
    ("demo:beta", "owns", "demo:delta", "documented", 1.0, "2016-02-01"),
    ("demo:delta", "owns", "demo:alpha", "reported", 0.7, "2019-05-01"),
    ("demo:person1", "director_of", "demo:alpha", "documented", 1.0, "2011-03-04"),
    ("demo:person1", "director_of", "demo:beta", "documented", 1.0, "2015-06-01"),
    ("demo:delta", "owns", "demo:gamma", "reported", 0.6, "2020-03-15"),
    ("demo:epsilon", "director_of", "demo:hub", "documented", 1.0, None),
    ("demo:hub", "agent_of", "demo:alpha", "documented", 1.0, None),
    ("demo:hub", "agent_of", "demo:epsilon", "documented", 1.0, None),
    ("demo:hub", "agent_of", "demo:gamma", "documented", 1.0, None),
]


def seed(conn: sqlite3.Connection) -> dict:
    db.init_db(conn)
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO sources(id, name, homepage, licence) VALUES (?,?,?,?) ON CONFLICT(id) DO NOTHING", SOURCE
        )
        for eid, kind, name, country, inc, addr, topics, idents in ENTITIES:
            db.upsert_entity(conn, {
                "id": eid, "source_id": "demo", "kind": kind, "name": name, "country": country,
                "incorporation_date": inc, "address": addr, "topics": topics, "identifiers": idents,
                "status": "ACTIVE", "attrs": {"synthetic": True},
            })
            db.add_evidence(conn, key=f"demo:entity:{eid}", source_id="demo", url=None,
                            title=f"Synthetic record: {name}", excerpt=f"Fictional demo record for {name}.",
                            entity_id=eid)
        for s, p, o, basis, conf, start in RELATIONSHIPS:
            rid = db.upsert_relationship(conn, {"source_id": "demo", "subject_id": s, "predicate": p, "object_id": o,
                                                "basis": basis, "confidence": conf, "start_date": start})
            db.add_evidence(conn, key=f"demo:rel:{s}:{p}:{o}", source_id="demo", url=None,
                            title=f"Synthetic {p} record", excerpt=f"Fictional: {s} {p} {o} ({basis}).",
                            relationship_id=rid)
    return {"entities": len(ENTITIES), "relationships": len(RELATIONSHIPS)}


def remove(conn: sqlite3.Connection) -> int:
    with db.transaction(conn):
        conn.execute("DELETE FROM entity_search WHERE entity_id LIKE 'demo:%'")
        conn.execute("DELETE FROM evidence WHERE source_id='demo'")
        conn.execute("DELETE FROM relationships WHERE source_id='demo'")
        n = conn.execute("DELETE FROM entities WHERE source_id='demo'").rowcount
    return n
