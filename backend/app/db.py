"""SQLite access layer: connections, schema, transactions and idempotent upserts."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .resolution.normalize import normalize_address, normalize_identifier, normalize_name
from .settings import settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

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

# Columns added after the first release; init_db adds them to existing databases.
MIGRATIONS = {
    "evidence": {"extraction_method": "TEXT", "confidence": "REAL"},
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = str(db_path or settings.db_path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    for table, columns in MIGRATIONS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for column, decl in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.executemany(
        "INSERT INTO sources(id, name, homepage, licence) VALUES (?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, homepage=excluded.homepage, licence=excluded.licence",
        SOURCES,
    )


@contextmanager
def transaction(conn: sqlite3.Connection, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def content_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- ingest runs

def start_run(conn: sqlite3.Connection, source_id: str, params: dict) -> int:
    cur = conn.execute(
        "INSERT INTO ingest_runs(source_id, started_at, status, params) VALUES (?,?, 'running', ?)",
        (source_id, utcnow(), dumps(params)),
    )
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, stats: dict, error: str | None = None) -> None:
    conn.execute(
        "UPDATE ingest_runs SET finished_at=?, status=?, stats=?, error=? WHERE id=?",
        (utcnow(), status, dumps(dict(stats)), error, run_id),
    )


# --------------------------------------------------------------------------- raw records

def upsert_raw(
    conn: sqlite3.Connection,
    source_id: str,
    record_key: str,
    payload: Any,
    url: str | None,
    run_id: int | None,
    content_type: str = "application/json",
) -> tuple[int, str]:
    """Store an original payload. Returns (raw_id, state) with state new | changed | unchanged."""
    text = payload if isinstance(payload, str) else dumps(payload)
    digest = content_hash(text)
    now = utcnow()
    row = conn.execute(
        "SELECT id, content_hash FROM raw_records WHERE source_id=? AND record_key=?",
        (source_id, record_key),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO raw_records(source_id, record_key, url, content_type, payload, content_hash, first_seen_at, "
            "last_fetched_at, last_changed_at, ingest_run_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (source_id, record_key, url, content_type, text, digest, now, now, now, run_id),
        )
        return int(cur.lastrowid), "new"
    if row["content_hash"] == digest:
        conn.execute("UPDATE raw_records SET last_fetched_at=? WHERE id=?", (now, row["id"]))
        return int(row["id"]), "unchanged"
    conn.execute(
        "INSERT INTO raw_record_history(raw_record_id, payload, content_hash, valid_from, valid_to) "
        "SELECT id, payload, content_hash, last_changed_at, ? FROM raw_records WHERE id=?",
        (now, row["id"]),
    )
    conn.execute(
        "UPDATE raw_records SET payload=?, content_hash=?, url=?, last_fetched_at=?, last_changed_at=?, "
        "ingest_run_id=? WHERE id=?",
        (text, digest, url, now, now, run_id, row["id"]),
    )
    return int(row["id"]), "changed"


# --------------------------------------------------------------------------- entities

def entity_exists(conn: sqlite3.Connection, entity_id: str) -> bool:
    return conn.execute("SELECT 1 FROM entities WHERE id=?", (entity_id,)).fetchone() is not None


def upsert_entity(conn: sqlite3.Connection, entity: dict) -> None:
    """Insert or update a normalised entity with its names, identifiers and search row."""
    now = utcnow()
    eid = entity["id"]
    topics = ",".join(sorted({t for t in entity.get("topics") or [] if t}))
    country = (entity.get("country") or "").lower() or None
    conn.execute(
        """
        INSERT INTO entities(id, source_id, kind, name, name_norm, country, jurisdiction, status, legal_form,
            incorporation_date, dissolution_date, address, address_norm, topics, attrs, raw_record_id,
            first_seen_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            kind=excluded.kind, name=excluded.name, name_norm=excluded.name_norm, country=excluded.country,
            jurisdiction=excluded.jurisdiction, status=excluded.status, legal_form=excluded.legal_form,
            incorporation_date=excluded.incorporation_date, dissolution_date=excluded.dissolution_date,
            address=excluded.address, address_norm=excluded.address_norm, topics=excluded.topics,
            attrs=excluded.attrs, raw_record_id=excluded.raw_record_id, updated_at=excluded.updated_at
        """,
        (
            eid,
            entity["source_id"],
            entity.get("kind") or "organization",
            entity["name"],
            normalize_name(entity["name"]),
            country,
            entity.get("jurisdiction"),
            entity.get("status"),
            entity.get("legal_form"),
            entity.get("incorporation_date"),
            entity.get("dissolution_date"),
            entity.get("address"),
            normalize_address(entity.get("address")) or None,
            topics,
            dumps(entity.get("attrs") or {}),
            entity.get("raw_record_id"),
            now,
            now,
        ),
    )
    names: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, name_type in [(entity["name"], "legal"), *(entity.get("names") or [])]:
        norm = normalize_name(name)
        if name and norm and (norm, name_type) not in seen:
            seen.add((norm, name_type))
            names.append((name, norm, name_type))
    conn.execute("DELETE FROM entity_names WHERE entity_id=?", (eid,))
    conn.executemany(
        "INSERT OR IGNORE INTO entity_names(entity_id, name, name_norm, name_type) VALUES (?,?,?,?)",
        [(eid, n, norm, t) for n, norm, t in names],
    )
    idents = sorted(
        {(s, normalize_identifier(s, v)) for s, v in entity.get("identifiers") or [] if v and normalize_identifier(s, v)}
    )
    conn.execute("DELETE FROM identifiers WHERE entity_id=?", (eid,))
    conn.executemany(
        "INSERT OR IGNORE INTO identifiers(entity_id, scheme, value) VALUES (?,?,?)",
        [(eid, s, v) for s, v in idents],
    )
    rowid = conn.execute("SELECT rowid FROM entities WHERE id=?", (eid,)).fetchone()[0]
    search_names = " | ".join(dict.fromkeys([n for n, _, _ in names] + [norm for _, norm, _ in names]))
    conn.execute("DELETE FROM entity_search WHERE rowid=?", (rowid,))
    conn.execute(
        "INSERT INTO entity_search(rowid, entity_id, names, identifiers) VALUES (?,?,?,?)",
        (rowid, eid, search_names, " ".join(v for _, v in idents)),
    )


# --------------------------------------------------------------------------- relationships & evidence

def upsert_relationship(conn: sqlite3.Connection, rel: dict) -> int:
    now = utcnow()
    row = conn.execute(
        """
        INSERT INTO relationships(source_id, subject_id, predicate, object_id, basis, confidence, start_date,
            end_date, status, attrs, raw_record_id, first_seen_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id, subject_id, predicate, object_id) DO UPDATE SET
            basis=excluded.basis, confidence=excluded.confidence,
            start_date=COALESCE(excluded.start_date, relationships.start_date),
            end_date=COALESCE(excluded.end_date, relationships.end_date),
            status=COALESCE(excluded.status, relationships.status),
            attrs=excluded.attrs,
            raw_record_id=COALESCE(excluded.raw_record_id, relationships.raw_record_id),
            updated_at=excluded.updated_at
        RETURNING id
        """,
        (
            rel["source_id"],
            rel["subject_id"],
            rel["predicate"],
            rel["object_id"],
            rel.get("basis", "documented"),
            float(rel.get("confidence", 1.0)),
            rel.get("start_date"),
            rel.get("end_date"),
            rel.get("status"),
            dumps(rel.get("attrs") or {}),
            rel.get("raw_record_id"),
            now,
            now,
        ),
    ).fetchone()
    return int(row[0])


def relationship_id(conn: sqlite3.Connection, source_id: str, subject_id: str, predicate: str, object_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM relationships WHERE source_id=? AND subject_id=? AND predicate=? AND object_id=?",
        (source_id, subject_id, predicate, object_id),
    ).fetchone()
    return int(row[0]) if row else None


def add_evidence(
    conn: sqlite3.Connection,
    *,
    key: str,
    source_id: str,
    url: str | None,
    title: str | None,
    excerpt: str | None,
    raw_record_id: int | None = None,
    entity_id: str | None = None,
    relationship_id: int | None = None,
    extraction_method: str | None = None,
    confidence: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO evidence(key, entity_id, relationship_id, source_id, url, title, excerpt, raw_record_id, retrieved_at,
            extraction_method, confidence)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET url=excluded.url, title=excluded.title, excerpt=excluded.excerpt,
            raw_record_id=COALESCE(excluded.raw_record_id, evidence.raw_record_id),
            entity_id=COALESCE(excluded.entity_id, evidence.entity_id),
            relationship_id=COALESCE(excluded.relationship_id, evidence.relationship_id),
            extraction_method=COALESCE(excluded.extraction_method, evidence.extraction_method),
            confidence=COALESCE(excluded.confidence, evidence.confidence),
            retrieved_at=excluded.retrieved_at
        """,
        (key, entity_id, relationship_id, source_id, url, title, (excerpt or "")[:1000], raw_record_id, utcnow(),
         extraction_method, confidence),
    )


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ["entities", "relationships", "evidence", "raw_records", "raw_record_history", "match_candidates",
              "identifiers", "claims"]
    return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables}


def rows(cursor: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in cursor]
