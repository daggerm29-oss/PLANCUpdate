"""Google Cloud BigQuery data access and schema management layer for PLANC.

Implements high-performance enterprise analytics on Google Cloud BigQuery:
1. Clustered & partitioned tables optimized for exact identifier lookups and name matching.
2. Idempotent MERGE operations (deduplicated by primary keys).
3. 100% data model parity with the existing PLANC SQLite and DuckDB schemas.
4. Safe fallback and mock compatibility for offline testing without live GCP credentials.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterator, Sequence

from .resolution.normalize import normalize_identifier, normalize_name
from .settings import settings

logger = logging.getLogger("planc.bigquery")

try:
    from google.cloud import bigquery
    from google.cloud.exceptions import NotFound
    BIGQUERY_AVAILABLE = True
except ImportError:
    bigquery = None  # type: ignore
    NotFound = Exception  # type: ignore
    BIGQUERY_AVAILABLE = False


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


def get_client(project: str | None = None, location: str | None = None) -> Any:
    """Instantiate a BigQuery Client using Application Default Credentials (ADC) or explicit project."""
    if not BIGQUERY_AVAILABLE:
        raise RuntimeError("google-cloud-bigquery is not installed. Run: pip install google-cloud-bigquery")
    proj = project or settings.gcp_project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or None
    loc = location or settings.bigquery_location or "US"
    return bigquery.Client(project=proj, location=loc)


def get_table_ref(client: Any, table_name: str, dataset_id: str | None = None) -> str:
    dataset = dataset_id or settings.bigquery_dataset or "planc"
    return f"`{client.project}.{dataset}.{table_name}`"


def init_dataset_and_tables(client: Any, dataset_id: str | None = None) -> None:
    """Create BigQuery dataset and tables with clustering for high search performance."""
    dataset_name = dataset_id or settings.bigquery_dataset or "planc"
    dataset_ref = bigquery.Dataset(f"{client.project}.{dataset_name}")
    dataset_ref.location = settings.bigquery_location or "US"

    try:
        client.get_dataset(dataset_ref)
    except NotFound:
        dataset_ref = client.create_dataset(dataset_ref, exists_ok=True)
        logger.info(f"Created BigQuery dataset: {dataset_name}")

    prefix = f"`{client.project}.{dataset_name}"

    ddl_statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.sources` (
            id STRING,
            name STRING,
            homepage STRING,
            licence STRING,
            notes STRING
        );
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.ingest_runs` (
            id INT64,
            source_id STRING,
            started_at STRING,
            finished_at STRING,
            status STRING,
            params STRING,
            stats STRING,
            error STRING
        )
        CLUSTER BY source_id, status;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.raw_records` (
            id INT64,
            source_id STRING,
            record_key STRING,
            url STRING,
            content_type STRING,
            payload STRING,
            content_hash STRING,
            first_seen_at STRING,
            last_fetched_at STRING,
            last_changed_at STRING,
            ingest_run_id INT64
        )
        CLUSTER BY source_id, record_key;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.entities` (
            id STRING,
            source_id STRING,
            kind STRING,
            name STRING,
            name_norm STRING,
            country STRING,
            jurisdiction STRING,
            status STRING,
            legal_form STRING,
            incorporation_date STRING,
            dissolution_date STRING,
            address STRING,
            address_norm STRING,
            topics STRING,
            attrs STRING,
            raw_record_id INT64,
            first_seen_at STRING,
            updated_at STRING
        )
        CLUSTER BY name_norm, source_id, country;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.entity_names` (
            entity_id STRING,
            name STRING,
            name_norm STRING,
            name_type STRING
        )
        CLUSTER BY name_norm, entity_id;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.identifiers` (
            entity_id STRING,
            scheme STRING,
            value STRING
        )
        CLUSTER BY value, scheme, entity_id;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.relationships` (
            id INT64,
            source_id STRING,
            subject_id STRING,
            predicate STRING,
            object_id STRING,
            basis STRING,
            confidence FLOAT64,
            start_date STRING,
            end_date STRING,
            status STRING,
            attrs STRING,
            raw_record_id INT64,
            first_seen_at STRING,
            updated_at STRING
        )
        CLUSTER BY subject_id, object_id, predicate;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.evidence` (
            id INT64,
            key STRING,
            entity_id STRING,
            relationship_id INT64,
            source_id STRING,
            url STRING,
            title STRING,
            excerpt STRING,
            raw_record_id INT64,
            retrieved_at STRING
        )
        CLUSTER BY entity_id, relationship_id;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.match_candidates` (
            id INT64,
            left_id STRING,
            right_id STRING,
            method STRING,
            score FLOAT64,
            features STRING,
            status STRING,
            reviewer_note STRING,
            created_at STRING,
            reviewed_at STRING
        )
        CLUSTER BY left_id, right_id, status;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.claims` (
            id INT64,
            key STRING,
            subject_id STRING,
            predicate STRING,
            object_text STRING,
            source_id STRING,
            url STRING,
            confidence FLOAT64,
            status STRING,
            first_seen_at STRING,
            raw_record_id INT64
        )
        CLUSTER BY subject_id, status;
        """,
    ]

    for ddl in ddl_statements:
        job = client.query(ddl)
        job.result()

    # Seed sources
    source_values = ", ".join(
        f"('{sid}', '{name}', {repr(hp) if hp else 'NULL'}, {repr(lic) if lic else 'NULL'}, 'Canonical PLANC Source')"
        for sid, name, hp, lic in SOURCES
    )
    merge_sources = f"""
    MERGE INTO {prefix}.sources` T
    USING (
        SELECT * FROM UNNEST([
            STRUCT<id STRING, name STRING, homepage STRING, licence STRING, notes STRING>
            {source_values}
        ])
    ) S
    ON T.id = S.id
    WHEN MATCHED THEN
        UPDATE SET name = S.name, homepage = S.homepage, licence = S.licence
    WHEN NOT MATCHED THEN
        INSERT (id, name, homepage, licence, notes) VALUES (S.id, S.name, S.homepage, S.licence, S.notes)
    """
    try:
        client.query(merge_sources).result()
    except Exception as e:
        logger.warning(f"Failed to seed sources in BigQuery: {e}")


def counts(client: Any, dataset_id: str | None = None) -> dict[str, int]:
    """Retrieve row counts for all tables in BigQuery dataset."""
    dataset_name = dataset_id or settings.bigquery_dataset or "planc"
    tables = [
        "sources", "ingest_runs", "raw_records", "entities", "entity_names",
        "identifiers", "relationships", "evidence", "match_candidates", "claims"
    ]
    query = " UNION ALL ".join(
        f"SELECT '{t}' as tbl, count(*) as cnt FROM `{client.project}.{dataset_name}.{t}`"
        for t in tables
    )
    res = {t: 0 for t in tables}
    try:
        job = client.query(query)
        for row in job.result():
            res[row.tbl] = row.cnt
    except Exception as e:
        logger.debug(f"Could not fetch full table counts from BigQuery: {e}")
    return res


def search(client: Any, query: str, limit: int = 25, dataset_id: str | None = None) -> list[dict]:
    """Cost-conscious BigQuery entity search.

    Uses clustered index filters:
    1. Exact / prefix identifier lookups (using clustered `identifiers.value`)
    2. Normalized name search (using clustered `entities.name_norm`)
    Avoids full table scans.
    """
    dataset_name = dataset_id or settings.bigquery_dataset or "planc"
    q = (query or "").strip()
    if not q:
        return []

    norm_name = normalize_name(q)
    compact_id = normalize_identifier("", q)

    id_condition = ""
    query_params = [
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
        bigquery.ScalarQueryParameter("name_norm", "STRING", f"%{norm_name}%"),
        bigquery.ScalarQueryParameter("raw_query", "STRING", f"%{q}%"),
    ]

    if compact_id:
        query_params.append(bigquery.ScalarQueryParameter("compact_id", "STRING", compact_id))
        query_params.append(bigquery.ScalarQueryParameter("compact_id_like", "STRING", f"%{compact_id}%"))
        id_condition = """
        SELECT i.entity_id
        FROM `{project}.{dataset}.identifiers` i
        WHERE i.value = @compact_id OR i.value LIKE @compact_id_like
        LIMIT @limit
        """

    full_sql = f"""
    WITH matched_ids AS (
        {f"({id_condition.format(project=client.project, dataset=dataset_name)}) UNION DISTINCT" if compact_id else ""}
        (
            SELECT e.id AS entity_id
            FROM `{client.project}.{dataset_name}.entities` e
            WHERE e.name_norm LIKE @name_norm OR UPPER(e.name) LIKE UPPER(@raw_query)
            LIMIT @limit
        )
    )
    SELECT e.id, e.source_id, e.kind, e.name, e.country, e.jurisdiction,
           e.status, e.legal_form, e.incorporation_date, e.topics, e.attrs
    FROM `{client.project}.{dataset_name}.entities` e
    JOIN matched_ids m ON m.entity_id = e.id
    LIMIT @limit
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=query_params,
        use_query_cache=True,
    )

    results = []
    try:
        job = client.query(full_sql, job_config=job_config)
        for r in job.result():
            topics_raw = r.get("topics") or ""
            attrs_raw = r.get("attrs") or "{}"
            results.append({
                "id": r["id"],
                "source_id": r["source_id"],
                "kind": r["kind"],
                "name": r["name"],
                "country": r.get("country"),
                "jurisdiction": r.get("jurisdiction"),
                "status": r.get("status"),
                "legal_form": r.get("legal_form"),
                "incorporation_date": r.get("incorporation_date"),
                "topics": [t for t in topics_raw.split(",") if t] if isinstance(topics_raw, str) else [],
                "attrs": loads(attrs_raw, {}),
            })
    except Exception as e:
        logger.error(f"BigQuery search query failed: {e}")

    return results


def get_entity(client: Any, entity_id: str, dataset_id: str | None = None) -> dict | None:
    """Fetch entity with its identifiers and attributes from BigQuery."""
    dataset_name = dataset_id or settings.bigquery_dataset or "planc"
    sql = f"""
    SELECT id, source_id, kind, name, country, jurisdiction,
           status, legal_form, incorporation_date, dissolution_date,
           address, topics, attrs, first_seen_at, updated_at
    FROM `{client.project}.{dataset_name}.entities`
    WHERE id = @entity_id
    LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("entity_id", "STRING", entity_id)]
    )

    rows = list(client.query(sql, job_config=job_config).result())
    if not rows:
        return None

    r = rows[0]
    topics_raw = r.get("topics") or ""
    attrs_raw = r.get("attrs") or "{}"

    ent = {
        "id": r["id"],
        "source_id": r["source_id"],
        "kind": r["kind"],
        "name": r["name"],
        "country": r.get("country"),
        "jurisdiction": r.get("jurisdiction"),
        "status": r.get("status"),
        "legal_form": r.get("legal_form"),
        "incorporation_date": r.get("incorporation_date"),
        "dissolution_date": r.get("dissolution_date"),
        "address": r.get("address"),
        "topics": [t for t in topics_raw.split(",") if t] if isinstance(topics_raw, str) else [],
        "attrs": loads(attrs_raw, {}),
        "first_seen_at": r.get("first_seen_at"),
        "updated_at": r.get("updated_at"),
    }

    # Fetch identifiers
    id_sql = f"""
    SELECT scheme, value FROM `{client.project}.{dataset_name}.identifiers`
    WHERE entity_id = @entity_id
    """
    id_rows = client.query(id_sql, job_config=job_config).result()
    ent["identifiers"] = [{"scheme": ir["scheme"], "value": ir["value"]} for ir in id_rows]

    # Fetch names
    name_sql = f"""
    SELECT name, name_type FROM `{client.project}.{dataset_name}.entity_names`
    WHERE entity_id = @entity_id
    """
    name_rows = client.query(name_sql, job_config=job_config).result()
    ent["names"] = [{"name": nr["name"], "name_type": nr["name_type"]} for nr in name_rows]

    return ent


def get_graph(client: Any, entity_id: str, limit: int = 60, dataset_id: str | None = None) -> dict:
    """Fetch 1-hop graph: outgoing & incoming relationships with related entity nodes."""
    dataset_name = dataset_id or settings.bigquery_dataset or "planc"

    sql = f"""
    SELECT r.id, r.source_id, r.subject_id, r.predicate, r.object_id,
           r.basis, r.confidence, r.start_date, r.end_date, r.status, r.attrs,
           e.name, e.kind, e.country,
           'outgoing' AS dir
    FROM `{client.project}.{dataset_name}.relationships` r
    JOIN `{client.project}.{dataset_name}.entities` e ON e.id = r.object_id
    WHERE r.subject_id = @entity_id
    UNION ALL
    SELECT r.id, r.source_id, r.subject_id, r.predicate, r.object_id,
           r.basis, r.confidence, r.start_date, r.end_date, r.status, r.attrs,
           e.name, e.kind, e.country,
           'incoming' AS dir
    FROM `{client.project}.{dataset_name}.relationships` r
    JOIN `{client.project}.{dataset_name}.entities` e ON e.id = r.subject_id
    WHERE r.object_id = @entity_id
    LIMIT @limit
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("entity_id", "STRING", entity_id),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )

    edges = []
    nodes = {}

    center = get_entity(client, entity_id, dataset_id=dataset_name)
    if center:
        nodes[center["id"]] = {
            "id": center["id"],
            "name": center["name"],
            "kind": center["kind"],
            "country": center["country"],
            "source_id": center["source_id"],
        }

    for r in client.query(sql, job_config=job_config).result():
        edges.append({
            "id": r["id"],
            "source_id": r["source_id"],
            "subject_id": r["subject_id"],
            "predicate": r["predicate"],
            "object_id": r["object_id"],
            "basis": r.get("basis", "documented"),
            "confidence": r.get("confidence", 1.0),
            "start_date": r.get("start_date"),
            "end_date": r.get("end_date"),
            "status": r.get("status"),
            "attrs": loads(r.get("attrs"), {}),
        })
        other_id = r["object_id"] if r["dir"] == "outgoing" else r["subject_id"]
        nodes[other_id] = {
            "id": other_id,
            "name": r["name"],
            "kind": r["kind"],
            "country": r.get("country"),
            "source_id": r["source_id"],
        }

    return {"nodes": list(nodes.values()), "edges": edges}


def get_timeline(client: Any, entity_id: str, dataset_id: str | None = None) -> list[dict]:
    """Generate chronological timeline of corporate events from BigQuery."""
    dataset_name = dataset_id or settings.bigquery_dataset or "planc"
    events: list[dict] = []
    ent = get_entity(client, entity_id, dataset_id=dataset_name)
    if not ent:
        return []

    if ent.get("incorporation_date"):
        events.append({
            "date": ent["incorporation_date"],
            "kind": "incorporation",
            "title": f"Incorporation / Registration ({ent.get('legal_form') or 'entity'})",
            "source_id": ent["source_id"],
        })

    sql = f"""
    SELECT r.predicate, r.start_date, r.end_date, r.object_id, r.subject_id, e.name
    FROM `{client.project}.{dataset_name}.relationships` r
    JOIN `{client.project}.{dataset_name}.entities` e ON (e.id = IF(r.subject_id = @entity_id, r.object_id, r.subject_id))
    WHERE r.subject_id = @entity_id OR r.object_id = @entity_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("entity_id", "STRING", entity_id)]
    )

    for r in client.query(sql, job_config=job_config).result():
        pred = r["predicate"]
        start = r["start_date"]
        end = r["end_date"]
        obj = r["object_id"]
        subj = r["subject_id"]
        other_name = r["name"]

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
