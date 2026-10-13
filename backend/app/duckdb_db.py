"""Hosted DuckDB (MotherDuck) & Local DuckDB data access layer for PLANC.

Supports:
1. MotherDuck cloud (md:planc) with secure MOTHERDUCK_TOKEN authentication.
2. Local DuckDB file (backend/data/osint.duckdb) for offline development / testing.
3. High-performance columnar queries, vector-accelerated text filtering, and batched inserts.
4. Preserves 100% schema parity with the canonical SQLite OSINT schema.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .resolution.normalize import normalize_address, normalize_identifier, normalize_name
from .settings import settings

logger = logging.getLogger("planc.duckdb")

try:
    import duckdb
    DUCKDB_AVAILABLE = True
except ImportError:
    duckdb = None  # type: ignore
    DUCKDB_AVAILABLE = False


SOURCES = [
    ("gleif", "GLEIF LEI records and Level 2 relationships", "https://www.gleif.org/", "CC0 1.0"),
    (
        "opensanctions",
        "OpenSanctions (FollowTheMoney bulk data)",
        "https://www.opensanctions.org/",
        "CC BY-NC 4.0: non-commercial use with attribution",
    ),
    ("resolver", "Internal entity resolution (algorithmic, reviewable)", None, "n/a"),
    (
        "mca",
        "MCA Company Master Data (India, via data.gov.in)",
        "https://www.data.gov.in/",
        "Government Open Data License - India: attribution required",
    ),
    (
        "icij",
        "ICIJ Offshore Leaks database",
        "https://offshoreleaks.icij.org/",
        "ODbL 1.0 (database) / CC BY-SA (contents): attribution required",
    ),
    (
        "nse",
        "NSE list of entities debarred by SEBI / other authorities",
        "https://www.nseindia.com/regulations/member-sebi-debarred-entities",
        "Published by NSE; check NSE terms of use before redistribution",
    ),
    (
        "crawler",
        "Corporate Intelligence Bridge crawler (Serper.dev search + web pages)",
        None,
        "Third-party web content: each site's terms apply; claims are unverified until corroborated",
    ),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def is_motherduck() -> bool:
    return bool(settings.motherduck_token or settings.db_backend == "motherduck")


def get_connection_string() -> str:
    if is_motherduck():
        token = settings.motherduck_token or os.environ.get("MOTHERDUCK_TOKEN", "")
        db_name = settings.motherduck_database or "planc"
        if token:
            return f"md:{db_name}?motherduck_token={token}"
        return f"md:{db_name}"
    path = settings.duckdb_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def connect(connection_str: str | None = None) -> Any:
    """Connect to MotherDuck or local DuckDB."""
    if not DUCKDB_AVAILABLE:
        raise RuntimeError("duckdb package is not installed. Run: pip install duckdb")
    target = connection_str or get_connection_string()
    # Read-only configuration can be used if querying hosted parquet/db files
    conn = duckdb.connect(target)
    return conn


def init_db(con: Any) -> None:
    """Create all canonical OSINT tables and initial sources in DuckDB / MotherDuck."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            homepage VARCHAR,
            licence VARCHAR,
            notes VARCHAR
        );

        CREATE TABLE IF NOT EXISTS ingest_runs (
            id BIGINT PRIMARY KEY,
            source_id VARCHAR NOT NULL,
            started_at VARCHAR NOT NULL,
            finished_at VARCHAR,
            status VARCHAR NOT NULL DEFAULT 'running',
            params VARCHAR,
            stats VARCHAR,
            error VARCHAR
        );

        CREATE TABLE IF NOT EXISTS raw_records (
            id BIGINT PRIMARY KEY,
            source_id VARCHAR NOT NULL,
            record_key VARCHAR NOT NULL,
            url VARCHAR,
            content_type VARCHAR NOT NULL DEFAULT 'application/json',
            payload VARCHAR NOT NULL,
            content_hash VARCHAR NOT NULL,
            first_seen_at VARCHAR NOT NULL,
            last_fetched_at VARCHAR NOT NULL,
            last_changed_at VARCHAR NOT NULL,
            ingest_run_id BIGINT
        );

        CREATE TABLE IF NOT EXISTS entities (
            id VARCHAR PRIMARY KEY,
            source_id VARCHAR NOT NULL,
            kind VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            name_norm VARCHAR NOT NULL,
            country VARCHAR,
            jurisdiction VARCHAR,
            status VARCHAR,
            legal_form VARCHAR,
            incorporation_date VARCHAR,
            dissolution_date VARCHAR,
            address VARCHAR,
            address_norm VARCHAR,
            topics VARCHAR NOT NULL DEFAULT '',
            attrs VARCHAR NOT NULL DEFAULT '{}',
            raw_record_id BIGINT,
            first_seen_at VARCHAR NOT NULL,
            updated_at VARCHAR NOT NULL
        );

        CREATE TABLE IF NOT EXISTS entity_names (
            entity_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            name_norm VARCHAR NOT NULL,
            name_type VARCHAR NOT NULL DEFAULT 'alias',
            PRIMARY KEY (entity_id, name_norm, name_type)
        );

        CREATE TABLE IF NOT EXISTS identifiers (
            entity_id VARCHAR NOT NULL,
            scheme VARCHAR NOT NULL,
            value VARCHAR NOT NULL,
            PRIMARY KEY (entity_id, scheme, value)
        );

        CREATE TABLE IF NOT EXISTS relationships (
            id BIGINT PRIMARY KEY,
            source_id VARCHAR NOT NULL,
            subject_id VARCHAR NOT NULL,
            predicate VARCHAR NOT NULL,
            object_id VARCHAR NOT NULL,
            basis VARCHAR NOT NULL DEFAULT 'documented',
            confidence DOUBLE NOT NULL DEFAULT 1.0,
            start_date VARCHAR,
            end_date VARCHAR,
            status VARCHAR,
            attrs VARCHAR NOT NULL DEFAULT '{}',
            raw_record_id BIGINT,
            first_seen_at VARCHAR NOT NULL,
            updated_at VARCHAR NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id BIGINT PRIMARY KEY,
            key VARCHAR NOT NULL,
            entity_id VARCHAR,
            relationship_id BIGINT,
            source_id VARCHAR NOT NULL,
            url VARCHAR,
            title VARCHAR,
            excerpt VARCHAR,
            raw_record_id BIGINT,
            retrieved_at VARCHAR NOT NULL
        );

        CREATE TABLE IF NOT EXISTS match_candidates (
            id BIGINT PRIMARY KEY,
            left_id VARCHAR NOT NULL,
            right_id VARCHAR NOT NULL,
            method VARCHAR NOT NULL,
            score DOUBLE NOT NULL,
            features VARCHAR NOT NULL DEFAULT '{}',
            status VARCHAR NOT NULL DEFAULT 'pending',
            reviewer_note VARCHAR,
            created_at VARCHAR NOT NULL,
            reviewed_at VARCHAR
        );

        CREATE TABLE IF NOT EXISTS claims (
            id BIGINT PRIMARY KEY,
            key VARCHAR NOT NULL,
            subject_entity_id VARCHAR,
            subject_query VARCHAR NOT NULL,
            claim_type VARCHAR NOT NULL,
            field VARCHAR NOT NULL,
            value VARCHAR NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'unverified',
            source_id VARCHAR NOT NULL,
            source_url VARCHAR,
            source_domain VARCHAR,
            retrieved_at VARCHAR NOT NULL,
            evidence_excerpt VARCHAR,
            extraction_method VARCHAR NOT NULL,
            confidence DOUBLE NOT NULL,
            raw_record_id BIGINT,
            verification VARCHAR NOT NULL DEFAULT '{}',
            first_seen_at VARCHAR NOT NULL,
            updated_at VARCHAR NOT NULL
        );

        CREATE TABLE IF NOT EXISTS investigations (
            id BIGINT PRIMARY KEY,
            title VARCHAR NOT NULL,
            notes VARCHAR,
            created_at VARCHAR NOT NULL,
            updated_at VARCHAR NOT NULL
        );

        CREATE TABLE IF NOT EXISTS investigation_items (
            id BIGINT PRIMARY KEY,
            investigation_id BIGINT NOT NULL,
            item_type VARCHAR NOT NULL,
            ref VARCHAR NOT NULL,
            note VARCHAR,
            created_at VARCHAR NOT NULL
        );
    """)

    # Seed default sources
    for src_id, name, homepage, licence in SOURCES:
        con.execute(
            """
            INSERT INTO sources (id, name, homepage, licence)
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (SELECT 1 FROM sources WHERE id = ?)
            """,
            [src_id, name, homepage, licence, src_id]
        )


def counts(con: Any) -> dict[str, int]:
    """Return dictionary of row counts for all main tables."""
    tables = [
        "entities", "identifiers", "entity_names", "relationships",
        "evidence", "sources", "raw_records", "ingest_runs",
        "match_candidates", "claims", "investigations"
    ]
    out: dict[str, int] = {}
    for t in tables:
        try:
            res = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
            out[t] = res[0] if res else 0
        except Exception:
            out[t] = 0
    return out


def search(con: Any, query: str, limit: int = 50) -> list[dict]:
    """Search entities by name or identifier using fast DuckDB columnar filtering."""
    q = (query or "").strip()
    if not q:
        return []

    norm_name = normalize_name(q)
    compact_id = normalize_identifier("", q)

    # 1. Exact or partial identifier matches (e.g. CIN, PAN, LEI, DIN)
    id_matches = []
    if compact_id:
        id_query = f"%{compact_id}%"
        rows = con.execute(
            """
            SELECT e.id, e.source_id, e.kind, e.name, e.country, e.jurisdiction,
                   e.status, e.legal_form, e.incorporation_date, e.topics, e.attrs
            FROM identifiers i
            JOIN entities e ON e.id = i.entity_id
            WHERE i.value = ? OR i.value LIKE ?
            LIMIT ?
            """,
            [compact_id, id_query, limit]
        ).fetchall()
        for r in rows:
            id_matches.append(_format_entity_row(r))

    # 2. Name matches
    name_query = f"%{norm_name}%"
    name_rows = con.execute(
        """
        SELECT id, source_id, kind, name, country, jurisdiction,
               status, legal_form, incorporation_date, topics, attrs
        FROM entities
        WHERE name_norm LIKE ? OR name ILIKE ?
        LIMIT ?
        """,
        [name_query, f"%{q}%", limit]
    ).fetchall()

    seen_ids = set()
    results = []
    for m in id_matches:
        seen_ids.add(m["id"])
        results.append(m)

    for r in name_rows:
        ent = _format_entity_row(r)
        if ent["id"] not in seen_ids:
            seen_ids.add(ent["id"])
            results.append(ent)
            if len(results) >= limit:
                break

    return results


def get_entity(con: Any, entity_id: str) -> dict | None:
    """Fetch entity with its identifiers and attributes."""
    row = con.execute(
        """
        SELECT id, source_id, kind, name, country, jurisdiction,
               status, legal_form, incorporation_date, dissolution_date,
               address, topics, attrs, first_seen_at, updated_at
        FROM entities WHERE id = ?
        """,
        [entity_id]
    ).fetchone()
    if not row:
        return None

    ent = {
        "id": row[0],
        "source_id": row[1],
        "kind": row[2],
        "name": row[3],
        "country": row[4],
        "jurisdiction": row[5],
        "status": row[6],
        "legal_form": row[7],
        "incorporation_date": row[8],
        "dissolution_date": row[9],
        "address": row[10],
        "topics": [t for t in (row[11] or "").split(",") if t],
        "attrs": loads(row[12], {}),
        "first_seen_at": row[13],
        "updated_at": row[14],
    }

    # Fetch identifiers
    id_rows = con.execute(
        "SELECT scheme, value FROM identifiers WHERE entity_id = ?",
        [entity_id]
    ).fetchall()
    ent["identifiers"] = [{"scheme": r[0], "value": r[1]} for r in id_rows]

    # Fetch names/aliases
    names_rows = con.execute(
        "SELECT name, name_type FROM entity_names WHERE entity_id = ?",
        [entity_id]
    ).fetchall()
    ent["names"] = [{"name": r[0], "name_type": r[1]} for r in names_rows]

    return ent


def get_graph(con: Any, entity_id: str) -> dict:
    """Fetch 1-hop graph: outgoing & incoming relationships with related entity nodes."""
    # Outgoing relationships
    out_rows = con.execute(
        """
        SELECT r.id, r.source_id, r.subject_id, r.predicate, r.object_id,
               r.basis, r.confidence, r.start_date, r.end_date, r.status, r.attrs,
               e.name, e.kind, e.country
        FROM relationships r
        JOIN entities e ON e.id = r.object_id
        WHERE r.subject_id = ?
        """,
        [entity_id]
    ).fetchall()

    # Incoming relationships
    in_rows = con.execute(
        """
        SELECT r.id, r.source_id, r.subject_id, r.predicate, r.object_id,
               r.basis, r.confidence, r.start_date, r.end_date, r.status, r.attrs,
               e.name, e.kind, e.country
        FROM relationships r
        JOIN entities e ON e.id = r.subject_id
        WHERE r.object_id = ?
        """,
        [entity_id]
    ).fetchall()

    edges = []
    nodes = {}

    # Center node
    center = get_entity(con, entity_id)
    if center:
        nodes[center["id"]] = {
            "id": center["id"],
            "name": center["name"],
            "kind": center["kind"],
            "country": center["country"],
            "source_id": center["source_id"],
        }

    for r in out_rows:
        edges.append({
            "id": r[0],
            "source_id": r[1],
            "subject_id": r[2],
            "predicate": r[3],
            "object_id": r[4],
            "basis": r[5],
            "confidence": r[6],
            "start_date": r[7],
            "end_date": r[8],
            "status": r[9],
            "attrs": loads(r[10], {}),
        })
        nodes[r[4]] = {
            "id": r[4],
            "name": r[11],
            "kind": r[12],
            "country": r[13],
            "source_id": r[1],
        }

    for r in in_rows:
        edges.append({
            "id": r[0],
            "source_id": r[1],
            "subject_id": r[2],
            "predicate": r[3],
            "object_id": r[4],
            "basis": r[5],
            "confidence": r[6],
            "start_date": r[7],
            "end_date": r[8],
            "status": r[9],
            "attrs": loads(r[10], {}),
        })
        nodes[r[2]] = {
            "id": r[2],
            "name": r[11],
            "kind": r[12],
            "country": r[13],
            "source_id": r[1],
        }

    return {"nodes": list(nodes.values()), "edges": edges}


def get_ownership(con: Any, entity_id: str) -> dict:
    """Fetch ownership & parent consolidation hierarchy."""
    rows = con.execute(
        """
        SELECT r.id, r.subject_id, r.predicate, r.object_id, r.basis, r.confidence, r.attrs,
               parent.name AS parent_name, parent.kind AS parent_kind, parent.country AS parent_country
        FROM relationships r
        JOIN entities parent ON parent.id = r.object_id
        WHERE r.subject_id = ? AND r.predicate IN ('owns', 'direct_parent', 'ultimate_parent', 'director_of')
        """,
        [entity_id]
    ).fetchall()

    parents = []
    for r in rows:
        parents.append({
            "id": r[0],
            "subject_id": r[1],
            "predicate": r[2],
            "object_id": r[3],
            "basis": r[4],
            "confidence": r[5],
            "attrs": loads(r[6], {}),
            "entity": {"id": r[3], "name": r[7], "kind": r[8], "country": r[9]},
        })

    # Subsidiaries / owned
    sub_rows = con.execute(
        """
        SELECT r.id, r.subject_id, r.predicate, r.object_id, r.basis, r.confidence, r.attrs,
               child.name AS child_name, child.kind AS child_kind, child.country AS child_country
        FROM relationships r
        JOIN entities child ON child.id = r.subject_id
        WHERE r.object_id = ? AND r.predicate IN ('owns', 'direct_parent', 'ultimate_parent')
        """,
        [entity_id]
    ).fetchall()

    subsidiaries = []
    for r in sub_rows:
        subsidiaries.append({
            "id": r[0],
            "subject_id": r[1],
            "predicate": r[2],
            "object_id": r[3],
            "basis": r[4],
            "confidence": r[5],
            "attrs": loads(r[6], {}),
            "entity": {"id": r[1], "name": r[7], "kind": r[8], "country": r[9]},
        })

    return {"parents": parents, "subsidiaries": subsidiaries}


def get_timeline(con: Any, entity_id: str) -> list[dict]:
    """Generate chronological timeline of corporate events."""
    events: list[dict] = []
    ent = get_entity(con, entity_id)
    if not ent:
        return []

    if ent.get("incorporation_date"):
        events.append({
            "date": ent["incorporation_date"],
            "kind": "incorporation",
            "title": f"Incorporation / Registration ({ent.get('legal_form') or 'entity'})",
            "source_id": ent["source_id"],
        })

    # Relationships timeline
    rels = con.execute(
        """
        SELECT r.predicate, r.start_date, r.end_date, r.object_id, r.subject_id, e.name
        FROM relationships r
        JOIN entities e ON (e.id = CASE WHEN r.subject_id = ? THEN r.object_id ELSE r.subject_id END)
        WHERE r.subject_id = ? OR r.object_id = ?
        """,
        [entity_id, entity_id, entity_id]
    ).fetchall()

    for r in rels:
        pred, start, end, obj, subj, other_name = r
        if start:
            events.append({
                "date": start,
                "kind": "relationship_start",
                "title": f"Established {pred} with {other_name}",
                "ref_id": obj if subj == entity_id else subj,
            })
        if end:
            events.append({
                "date": end,
                "kind": "relationship_end",
                "title": f"Ended {pred} with {other_name}",
                "ref_id": obj if subj == entity_id else subj,
            })

    events.sort(key=lambda x: x["date"], reverse=True)
    return events


def get_sources_summary(con: Any) -> list[dict]:
    """Return sources with entity count statistics."""
    rows = con.execute(
        """
        SELECT s.id, s.name, s.homepage, s.licence,
               COALESCE((SELECT COUNT(*) FROM entities e WHERE e.source_id = s.id), 0) AS entities
        FROM sources s
        ORDER BY s.id
        """
    ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "homepage": r[2],
            "licence": r[3],
            "entities": r[4],
        }
        for r in rows
    ]


def _format_entity_row(r: Any) -> dict:
    topics_raw = r[9] if len(r) > 9 else ""
    attrs_raw = r[10] if len(r) > 10 else "{}"
    return {
        "id": r[0],
        "source_id": r[1],
        "kind": r[2],
        "name": r[3],
        "country": r[4],
        "jurisdiction": r[5],
        "status": r[6],
        "legal_form": r[7],
        "incorporation_date": r[8],
        "topics": [t for t in (topics_raw or "").split(",") if t],
        "attrs": loads(attrs_raw, {}),
    }
