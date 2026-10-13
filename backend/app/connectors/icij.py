"""ICIJ Offshore Leaks database CSV importer (full-oldb.LATEST_nodes-*.csv + relationships.csv).

Nodes load first, then relationships; an edge whose endpoint is not loaded is counted and skipped
(the published samples contain no edge with both endpoints present). ICIJ's own note applies to every record:
appearing in the Offshore Leaks database does not suggest that a person or entity broke the law.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .. import db
from ..resolution.countries import to_alpha2
from .files import batched, file_provenance, ingest_run, iter_csv

SOURCE = "icij"
METHOD = "bulk_file:icij_offshore_leaks_csv"
NODE_FILES = {  # file marker -> (kind, role)
    "nodes-entities": ("company", "offshore_entity"),
    "nodes-officers": ("person", "officer"),
    "nodes-intermediaries": ("organization", "intermediary"),
    "nodes-others": ("organization", "other"),
    "nodes-addresses": ("address", "address"),
}
COMPANY_WORDS = re.compile(
    r"\b(LTD|LIMITED|INC|CORP|CORPORATION|S\.?A\.?|LLC|L\.L\.C|GMBH|B\.?V\.?|N\.?V\.?|AG|PLC|TRUST|FOUNDATION|HOLDINGS?|"
    r"COMPANY|CO\.|BEARER|NOMINEES?|SERVICES)\b",
    re.IGNORECASE,
)
ICIJ_CAVEAT = ("Appearing in the ICIJ Offshore Leaks database does not mean a person or entity broke the law; "
               "offshore structures have legitimate uses.")


def node_url(node_id: str) -> str:
    return f"https://offshoreleaks.icij.org/nodes/{node_id}"


def _predicate(rel_type: str, link: str) -> tuple[str, float]:
    rel_type, link = (rel_type or "").lower(), (link or "").lower()
    if rel_type == "officer_of":
        if "director" in link:
            return "director_of", 0.7
        if any(w in link for w in ("shareholder", "beneficial owner", "owner")):
            return "owns", 0.6
        return "officer_of", 0.7
    if rel_type == "intermediary_of":
        return "intermediary_of", 0.7
    if rel_type == "registered_address":
        return "registered_address", 0.8
    if rel_type in {"same_as", "similar", "same_name_as", "same_company_as", "same_intermediary_as",
                    "probably_same_officer_as", "same_address_as"}:
        return "similar_to", 0.5
    return "linked_to", 0.5


def find_files(folder: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(Path(folder).glob("*.csv")):
        name = path.name.lower()
        for marker in [*NODE_FILES, "relationships"]:
            if marker in name and marker not in found:
                found[marker] = path
    return found


def _node_entity(row: dict, marker: str, file_label: str) -> dict:
    kind, role = NODE_FILES[marker]
    node_id = row.get("node_id") or ""
    if not node_id:
        raise ValueError("ICIJ row without node_id")
    name = row.get("name") or row.get("address") or f"ICIJ node {node_id}"
    if marker == "nodes-officers" and COMPANY_WORDS.search(name):
        kind = "company"
    names = [(n, "previous" if col == "former_name" else "alias")
             for col in ("original_name", "former_name") if (n := row.get(col)) and n != name]
    return {
        "id": f"icij:{node_id}",
        "source_id": SOURCE,
        "kind": kind,
        "name": re.sub(r"\s+", " ", name)[:500],
        "country": to_alpha2(row.get("country_codes")),
        "jurisdiction": row.get("jurisdiction_description") or row.get("jurisdiction") or None,
        "status": (row.get("status") or "").lower() or None,
        "legal_form": row.get("company_type") or row.get("type") or None,
        "incorporation_date": row.get("incorporation_date") or None,
        "dissolution_date": row.get("struck_off_date") or row.get("inactivation_date") or row.get("closed_date") or None,
        "address": row.get("address") or None,
        "topics": [],
        "names": names,
        "identifiers": [],
        "attrs": {
            "icij_node_id": node_id,
            "role": role,
            "leak": row.get("sourceID") or None,
            "valid_until": row.get("valid_until") or None,
            "service_provider": row.get("service_provider") or None,
            "countries": row.get("countries") or None,
            "note": row.get("note") or None,
            "file": file_label,
        },
    }


def load(conn: sqlite3.Connection, folder: Path) -> dict:
    files = find_files(folder)
    if not files:
        raise FileNotFoundError(f"no ICIJ CSV files (nodes-*.csv / relationships.csv) in {folder}")
    params = {"kind": "icij_csv", "files": {k: file_provenance(p) for k, p in files.items()}}
    with ingest_run(conn, SOURCE, params) as (run_id, stats):
        for marker in NODE_FILES:
            path = files.get(marker)
            if not path:
                continue

            def handle_node(row: dict, marker=marker, label=path.name) -> None:
                entity = _node_entity(row, marker, label)
                node_id = entity["attrs"]["icij_node_id"]
                raw_id, state = db.upsert_raw(conn, SOURCE, f"node:{node_id}", row, node_url(node_id), run_id)
                stats[f"{marker}_{state}"] += 1
                if state == "unchanged" and db.entity_exists(conn, entity["id"]):
                    return
                entity["raw_record_id"] = raw_id
                db.upsert_entity(conn, entity)
                db.add_evidence(
                    conn,
                    key=f"icij:node:{node_id}",
                    source_id=SOURCE,
                    url=node_url(node_id),
                    title=f"ICIJ Offshore Leaks: {entity['name']}",
                    excerpt=(f"{entity['attrs']['role']} in {entity['attrs']['leak'] or 'ICIJ data'}"
                             + (f"; jurisdiction {entity['jurisdiction']}" if entity["jurisdiction"] else "")
                             + f". {ICIJ_CAVEAT}"),
                    raw_record_id=raw_id,
                    entity_id=entity["id"],
                    extraction_method=METHOD,
                    confidence=0.8,
                )

            batched(conn, iter_csv(path), handle_node, stats)

        rel_path = files.get("relationships")
        if rel_path:
            def handle_rel(row: dict) -> None:
                start, end = row.get("node_id_start"), row.get("node_id_end")
                if not start or not end or start == end:
                    raise ValueError("relationship without two distinct node ids")
                subject, obj = f"icij:{start}", f"icij:{end}"
                if not db.entity_exists(conn, subject) or not db.entity_exists(conn, obj):
                    stats["relationships_skipped_missing_endpoint"] += 1
                    return
                predicate, confidence = _predicate(row.get("rel_type"), row.get("link"))
                key = f"rel:{start}:{row.get('rel_type')}:{end}:{row.get('link')}"
                raw_id, state = db.upsert_raw(conn, SOURCE, key, row, node_url(start), run_id)
                stats[f"relationship_{state}"] += 1
                rel_id = db.upsert_relationship(conn, {
                    "source_id": SOURCE, "subject_id": subject, "predicate": predicate, "object_id": obj,
                    "basis": "reported", "confidence": confidence,
                    "start_date": row.get("start_date") or None, "end_date": row.get("end_date") or None,
                    "status": (row.get("status") or "").lower() or None,
                    "attrs": {"rel_type": row.get("rel_type"), "link": row.get("link"), "leak": row.get("sourceID")},
                    "raw_record_id": raw_id,
                })
                db.add_evidence(
                    conn,
                    key=f"icij:{key}",
                    source_id=SOURCE,
                    url=node_url(start),
                    title="ICIJ Offshore Leaks relationship",
                    excerpt=f"{start} '{row.get('link') or row.get('rel_type')}' {end} ({row.get('sourceID') or 'ICIJ'}). {ICIJ_CAVEAT}",
                    raw_record_id=raw_id,
                    relationship_id=rel_id,
                    extraction_method=METHOD,
                    confidence=confidence,
                )
                stats["relationships"] += 1

            batched(conn, iter_csv(rel_path), handle_rel, stats)
    return {"run_id": run_id, **stats}
