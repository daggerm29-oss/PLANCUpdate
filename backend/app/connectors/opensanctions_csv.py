"""OpenSanctions `targets.simple.csv` importer.

Columns: id, schema, name, aliases, birth_date, countries, addresses, identifiers, sanctions, phones, emails,
program_ids, dataset, first_seen, last_seen, last_change (multi-values separated by ';').
The simple CSV has no topics and no relationship records (see DECISIONS D7: prefer the FtM loader when possible).
Entity ids match the FtM loader (`os:<id>`), so a later FtM load enriches the same rows.
Identifiers are unlabelled; their scheme is inferred from the format (IMO, LEI, CIN, PAN ...), else `id_number`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import db
from ..resolution.identifiers import detect_scheme
from .files import batched, file_provenance, ingest_run, iter_csv
from .opensanctions import NODE_KIND, SOURCE, entity_url

METHOD = "bulk_file:opensanctions_targets_simple_csv"


def _split(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(";") if v.strip()]


def map_row(row: dict) -> dict | None:
    kind = NODE_KIND.get(row.get("schema", ""))
    if not kind or not row.get("id") or not row.get("name"):
        return None
    sanctions = _split(row.get("sanctions"))
    countries = _split(row.get("countries"))
    return {
        "id": f"os:{row['id']}",
        "source_id": SOURCE,
        "kind": kind,
        "name": row["name"],
        "country": countries[0] if countries else None,
        "incorporation_date": row.get("birth_date") or None,
        "address": (_split(row.get("addresses")) or [None])[0],
        "topics": ["sanction"] if sanctions else [],
        "names": [(a, "alias") for a in _split(row.get("aliases"))],
        "identifiers": [(detect_scheme(v), v) for v in _split(row.get("identifiers"))],
        "attrs": {
            "schema": row.get("schema"),
            "datasets": _split(row.get("dataset")),
            "countries": countries,
            "sanctions_text": [s[:300] for s in sanctions[:10]],
            "programs": _split(row.get("program_ids")),
            "first_seen": row.get("first_seen") or None,
            "last_seen": row.get("last_seen") or None,
            "last_change": row.get("last_change") or None,
            "format": "targets.simple.csv",
        },
    }


def load(conn: sqlite3.Connection, path: Path) -> dict:
    path = Path(path)
    prov = file_provenance(path)
    with ingest_run(conn, SOURCE, {"kind": "opensanctions_simple_csv", **prov}) as (run_id, stats):
        def handle(row: dict) -> None:
            entity = map_row(row)
            if entity is None:
                stats["skipped_schema"] += 1
                return
            url = entity_url(row["id"])
            raw_id, state = db.upsert_raw(conn, SOURCE, f"simple:{row['id']}", row, url, run_id)
            stats[state] += 1
            if state == "unchanged" and db.entity_exists(conn, entity["id"]):
                return
            entity["raw_record_id"] = raw_id
            db.upsert_entity(conn, entity)
            datasets = ", ".join(entity["attrs"]["datasets"]) or "n/a"
            db.add_evidence(
                conn,
                key=f"os:entity:{row['id']}",
                source_id=SOURCE,
                url=url,
                title=f"OpenSanctions: {entity['name']}",
                excerpt=(f"{row.get('schema')} in dataset(s) {datasets}"
                         + (f"; sanctions: {entity['attrs']['sanctions_text'][0]}" if entity["attrs"]["sanctions_text"] else "")),
                raw_record_id=raw_id,
                entity_id=entity["id"],
                extraction_method=METHOD,
                confidence=1.0,
            )

        batched(conn, iter_csv(path), handle, stats)
    return {"run_id": run_id, "file": prov["file"], **stats}
