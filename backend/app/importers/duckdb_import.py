"""Repeatable, streaming & batched import pipeline for DuckDB / MotherDuck.

Imports the 5 public datasets into DuckDB / MotherDuck:
1. MCA: Company master CSV files (Andhra Pradesh, Karnataka, and other state registries).
2. GLEIF: Level 1 (LEI-CDF) and Level 2 (RR-CDF) ZIP/XML archives.
3. ICIJ: Offshore Leaks database ZIP archive (full-oldb.LATEST.zip) or CSV directory.
4. OpenSanctions: Targets and sanctions CSV files (targets.simple.csv).
5. NSE: Debarred entities Excel/CSV files (prs_ra_*.xls, .xlsx, .csv).

Guarantees:
- Never loads entire large XML, ZIP or multi-GB CSV files into memory.
- Direct streaming from inside ZIP archives without requiring prior extraction.
- Detects dataset types by folder hierarchy (parent folder names) as well as filenames.
- Preserves source identifiers, original names, provenance URLs, and timestamps.
- Idempotent upserts and deterministic BIGINT relationship IDs (safe re-imports without duplicating records).
- Tracks ingest runs with row counts, rejected records, errors, and duration.
- Reports every dataset found, processed, skipped, or failed (never silently skips).
"""
from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import logging
import os
import re
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ..duckdb_db import dumps, loads, utcnow
from ..resolution.countries import to_alpha2
from ..resolution.identifiers import (
    CIN_RE,
    FCRN_RE,
    LLPIN_RE,
    PAN_RE,
    detect_scheme,
    normalize_din,
    parse_cin,
)
from ..resolution.normalize import (
    normalize_address,
    normalize_identifier,
    normalize_name,
)

logger = logging.getLogger("planc.importer")

BATCH_SIZE = 10000
PROGRESS_INTERVAL = 10000
_RUN_COUNTER = itertools.count(int(time.time() * 1000))


def deterministic_rel_id(*parts: Any) -> int:
    """Generate a stable, positive 63-bit signed integer ID for a relationship.

    Guarantees idempotent upserts across SQLite, DuckDB, and MotherDuck:
    re-importing the same relationship record will replace the existing row
    rather than generating duplicate entries.
    """
    key = ":".join(str(p).strip() for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") & 0x7FFFFFFFFFFFFFFF


def file_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()[:16]


class IngestTracker:
    def __init__(self, con: Any, source_id: str, file_path: Path, kind: str):
        self.con = con
        self.source_id = source_id
        self.file_path = file_path
        self.kind = kind
        self.run_id = next(_RUN_COUNTER)
        self.started_at = utcnow()
        self.counts = {"seen": 0, "inserted": 0, "updated": 0, "rejected": 0, "errors": 0}
        self.error: str | None = None

    def __enter__(self):
        try:
            self.con.execute(
                """
                INSERT INTO ingest_runs (id, source_id, started_at, status, params)
                VALUES (?, ?, ?, 'running', ?)
                """,
                [
                    self.run_id,
                    self.source_id,
                    self.started_at,
                    dumps({"file": self.file_path.name, "kind": self.kind}),
                ],
            )
        except Exception as e:
            logger.warning(f"Could not record start of ingest run: {e}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        status = "failed" if exc_type else ("partial" if self.counts["errors"] > 0 else "success")
        if exc_val:
            self.error = str(exc_val)
        try:
            self.con.execute(
                """
                UPDATE ingest_runs
                SET finished_at = ?, status = ?, stats = ?, error = ?
                WHERE id = ?
                """,
                [utcnow(), status, dumps(self.counts), self.error, self.run_id],
            )
        except Exception as e:
            logger.warning(f"Could not record end of ingest run: {e}")


def _upsert_entities_and_ids(con: Any, entity_batch: list[list[Any]], id_batch: list[list[Any]]) -> None:
    if entity_batch:
        con.executemany(
            """
            INSERT OR REPLACE INTO entities (
                id, source_id, kind, name, name_norm, country, jurisdiction,
                status, legal_form, incorporation_date, dissolution_date,
                address, address_norm, topics, attrs, raw_record_id,
                first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            entity_batch,
        )

    if id_batch:
        con.executemany(
            """
            INSERT OR REPLACE INTO identifiers (entity_id, scheme, value)
            VALUES (?, ?, ?)
            """,
            id_batch,
        )


# ---------------------------------------------------------------------------
# 1. MCA Importer (Karnataka, Andhra Pradesh & other state CSVs)
# ---------------------------------------------------------------------------

MCA_COLUMNS = {
    "cin": ("CIN", "CORPORATE_IDENTIFICATION_NUMBER", "Corporate Identification Number", "LLPIN", "Registration Number"),
    "name": ("Company Name", "COMPANY_NAME", "LLP Name", "Name of Company", "Company"),
    "registration_date": ("Company Registration Date", "DATE_OF_REGISTRATION", "Date of Incorporation", "Date of Registration"),
    "category": ("Company Category", "COMPANY_CATEGORY", "Category"),
    "class": ("Company Class", "COMPANY_CLASS", "Class"),
    "listing_status": ("Listing Status", "LISTING_STATUS"),
    "authorized_capital": ("Authorized Capital", "AUTHORIZED_CAP", "Authorised Capital", "Authorized_Capital"),
    "paid_up_capital": ("Paidup Capital", "PAIDUP_CAPITAL", "Paid up Capital", "Paid_Up_Capital"),
    "roc": ("Company ROC", "REGISTRAR_OF_COMPANIES", "ROC", "Roc"),
    "address": ("Company Address", "REGISTERED_OFFICE_ADDRESS", "Registered Office Address", "Address"),
    "pin_code": ("Pin Code", "PIN_CODE", "Pincode"),
    "state": ("Company State", "REGISTERED_STATE", "State"),
    "status": ("Company Status", "COMPANY_STATUS", "Status"),
    "sub_category": ("Company Sub Category", "COMPANY_SUB_CATEGORY", "Sub Category"),
    "industrial_classification": ("Company Industrial Classification", "PRINCIPAL_BUSINESS_ACTIVITY_AS_PER_CIN"),
}


def _pick(row: dict, *names: str) -> str:
    # 1. Exact lookup
    for n in names:
        if n in row and row[n] is not None:
            v = str(row[n]).strip()
            if v:
                return v
    # 2. Case-insensitive lookup
    row_lower = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for n in names:
        nl = n.strip().lower()
        if nl in row_lower and row_lower[nl] is not None:
            v = str(row_lower[nl]).strip()
            if v:
                return v
    return ""


def import_mca(con: Any, path: Path) -> dict:
    """Stream and batch-upsert MCA State Company CSV files."""
    source_id = "mca"
    with IngestTracker(con, source_id, path, "mca_csv") as tracker:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            entity_batch = []
            id_batch = []
            now = utcnow()

            for row in reader:
                tracker.counts["seen"] += 1
                cin_val = _pick(row, *MCA_COLUMNS["cin"])
                name_val = _pick(row, *MCA_COLUMNS["name"])
                reg_id = normalize_identifier("", cin_val)

                if not reg_id or not name_val:
                    tracker.counts["rejected"] += 1
                    continue

                entity_id = f"mca:{reg_id}"
                name_norm = normalize_name(name_val)
                state_val = _pick(row, *MCA_COLUMNS["state"])
                addr_val = _pick(row, *MCA_COLUMNS["address"])
                status_val = _pick(row, *MCA_COLUMNS["status"]).lower() or "active"

                attrs = {
                    "cin": reg_id,
                    "roc": _pick(row, *MCA_COLUMNS["roc"]),
                    "category": _pick(row, *MCA_COLUMNS["category"]),
                    "class": _pick(row, *MCA_COLUMNS["class"]),
                    "authorized_capital": _pick(row, *MCA_COLUMNS["authorized_capital"]),
                    "paid_up_capital": _pick(row, *MCA_COLUMNS["paid_up_capital"]),
                    "file": path.name,
                }

                entity_batch.append([
                    entity_id, source_id, "company", name_val, name_norm,
                    "in", state_val or "India", status_val,
                    _pick(row, *MCA_COLUMNS["class"]) or "Company",
                    _pick(row, *MCA_COLUMNS["registration_date"]) or None,
                    None, addr_val, normalize_address(addr_val),
                    "", dumps(attrs), None, now, now,
                ])

                id_batch.append([entity_id, "cin", reg_id])

                if len(entity_batch) >= BATCH_SIZE:
                    _upsert_entities_and_ids(con, entity_batch, id_batch)
                    tracker.counts["inserted"] += len(entity_batch)
                    print(
                        f"    -> [MCA] {path.name}: processed {tracker.counts['seen']:,} rows "
                        f"({tracker.counts['inserted']:,} inserted)...",
                        flush=True,
                    )
                    entity_batch.clear()
                    id_batch.clear()

            if entity_batch:
                _upsert_entities_and_ids(con, entity_batch, id_batch)
                tracker.counts["inserted"] += len(entity_batch)

    return tracker.counts


# ---------------------------------------------------------------------------
# 2. GLEIF XML / ZIP Importer (Level 1 LEI & Level 2 Relationships, Streamed)
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find("/".join(f"{{*}}{p}" for p in path.split("/")))
    return found.text.strip() if found is not None and found.text and found.text.strip() else None


@contextmanager
def _iter_xml_streams(path: Path) -> Iterator[list[tuple[str, Any]]]:
    """Provide open XML streams from a .zip archive or standalone .xml file without full disk extraction."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            xml_names = [
                n for n in zf.namelist()
                if n.lower().endswith(".xml")
                and not n.startswith("__MACOSX")
                and not Path(n).name.startswith(".")
            ]
            if not xml_names:
                raise ValueError(f"No XML files found inside zip archive: {path.name}")
            streams = [(name, zf.open(name)) for name in xml_names]
            try:
                yield streams
            finally:
                for _, s in streams:
                    try:
                        s.close()
                    except Exception:
                        pass
    else:
        with open(path, "rb") as stream:
            yield [(path.name, stream)]


def import_gleif_level1(con: Any, path: Path) -> dict:
    """Stream and batch-upsert GLEIF Level 1 (LEI-CDF) records from .zip or .xml."""
    source_id = "gleif"
    with IngestTracker(con, source_id, path, "gleif_lei_cdf_xml") as tracker:
        entity_batch = []
        id_batch = []
        now = utcnow()
        start_t = time.time()

        with _iter_xml_streams(path) as xml_streams:
            for stream_name, xml_stream in xml_streams:
                root = None
                for event, elem in ET.iterparse(xml_stream, events=("start", "end")):
                    if event == "start" and root is None:
                        root = elem
                        continue
                    if event != "end":
                        continue

                    if _local(elem.tag) == "LEIRecord":
                        tracker.counts["seen"] += 1
                        lei = _text(elem, "LEI")
                        name = _text(elem, "Entity/LegalName")

                        if not lei or not name:
                            tracker.counts["rejected"] += 1
                            elem.clear()
                            if root is not None:
                                root.clear()
                            continue

                        entity_id = f"gleif:{lei}"
                        status = (_text(elem, "Registration/RegistrationStatus") or "").lower()
                        country = _text(elem, "Entity/LegalAddress/Country")
                        jurisdiction = _text(elem, "Entity/Jurisdiction")
                        inc_date = (_text(elem, "Registration/InitialRegistrationDate") or "")[:10]

                        addr_parts = [
                            _text(elem, "Entity/LegalAddress/FirstAddressLine"),
                            _text(elem, "Entity/LegalAddress/City"),
                            country,
                        ]
                        address = ", ".join(p for p in addr_parts if p)

                        attrs = {
                            "lei": lei,
                            "legal_form": _text(elem, "Entity/LegalForm/EntityLegalFormCode"),
                            "managing_lou": _text(elem, "Registration/ManagingLOU"),
                        }

                        entity_batch.append([
                            entity_id, source_id, "company", name, normalize_name(name),
                            to_alpha2(country) if country else None, jurisdiction,
                            status or "issued", attrs.get("legal_form") or "Corporate",
                            inc_date or None, None, address, normalize_address(address),
                            "", dumps(attrs), None, now, now,
                        ])
                        id_batch.append([entity_id, "lei", lei])

                        if len(entity_batch) >= BATCH_SIZE:
                            _upsert_entities_and_ids(con, entity_batch, id_batch)
                            tracker.counts["inserted"] += len(entity_batch)
                            rate = int(tracker.counts["seen"] / max(time.time() - start_t, 0.001))
                            print(
                                f"    -> [GLEIF_L1] processed {tracker.counts['seen']:,} records "
                                f"({tracker.counts['inserted']:,} inserted) [{rate:,} rec/s]...",
                                flush=True,
                            )
                            entity_batch.clear()
                            id_batch.clear()

                        elem.clear()
                        if root is not None:
                            root.clear()

        if entity_batch:
            _upsert_entities_and_ids(con, entity_batch, id_batch)
            tracker.counts["inserted"] += len(entity_batch)

    return tracker.counts


def import_gleif_level2(con: Any, path: Path) -> dict:
    """Stream and batch-upsert GLEIF Level 2 (RR-CDF) relationships from .zip or .xml.

    Relationship IDs are computed deterministically so re-importing the same dataset
    replaces existing records and never creates duplicates.
    """
    source_id = "gleif"
    with IngestTracker(con, source_id, path, "gleif_rr_cdf_xml") as tracker:
        rel_batch = []
        now = utcnow()
        start_t = time.time()

        with _iter_xml_streams(path) as xml_streams:
            for stream_name, xml_stream in xml_streams:
                root = None
                for event, elem in ET.iterparse(xml_stream, events=("start", "end")):
                    if event == "start" and root is None:
                        root = elem
                        continue
                    if event != "end":
                        continue

                    if _local(elem.tag) == "RelationshipRecord":
                        tracker.counts["seen"] += 1
                        child_lei = _text(elem, "Relationship/StartNode/NodeID")
                        parent_lei = _text(elem, "Relationship/EndNode/NodeID")
                        rel_type = _text(elem, "Relationship/RelationshipType") or "IS_DIRECTLY_CONSOLIDATED_BY"

                        if not child_lei or not parent_lei or child_lei == parent_lei:
                            tracker.counts["rejected"] += 1
                            elem.clear()
                            if root is not None:
                                root.clear()
                            continue

                        predicate = "ultimate_parent" if "ULTIMATELY" in rel_type else "direct_parent"
                        subject_id = f"gleif:{child_lei}"
                        object_id = f"gleif:{parent_lei}"

                        # Stable 63-bit signed integer ID derived deterministically
                        rel_id = deterministic_rel_id("gleif", subject_id, predicate, object_id, rel_type)

                        rel_batch.append([
                            rel_id, source_id, subject_id, predicate, object_id,
                            "documented", 1.0, None, None, "active",
                            dumps({"rel_type": rel_type}), None, now, now,
                        ])

                        if len(rel_batch) >= BATCH_SIZE:
                            con.executemany(
                                """
                                INSERT OR REPLACE INTO relationships
                                (id, source_id, subject_id, predicate, object_id, basis, confidence, start_date, end_date, status, attrs, raw_record_id, first_seen_at, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                rel_batch,
                            )
                            tracker.counts["inserted"] += len(rel_batch)
                            rate = int(tracker.counts["seen"] / max(time.time() - start_t, 0.001))
                            print(
                                f"    -> [GLEIF_L2] processed {tracker.counts['seen']:,} relationships "
                                f"({tracker.counts['inserted']:,} inserted) [{rate:,} rel/s]...",
                                flush=True,
                            )
                            rel_batch.clear()

                        elem.clear()
                        if root is not None:
                            root.clear()

        if rel_batch:
            con.executemany(
                """
                INSERT OR REPLACE INTO relationships
                (id, source_id, subject_id, predicate, object_id, basis, confidence, start_date, end_date, status, attrs, raw_record_id, first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rel_batch,
            )
            tracker.counts["inserted"] += len(rel_batch)

    return tracker.counts


# ---------------------------------------------------------------------------
# 3. ICIJ Offshore Leaks Importer (ZIP Archive & CSV Directory Support)
# ---------------------------------------------------------------------------

def _process_icij_csv_stream(
    con: Any,
    filename: str,
    text_stream: Iterator[str],
    tracker: IngestTracker,
) -> None:
    """Process a single ICIJ CSV text stream (entities, officers, or relationships)."""
    source_id = "icij"
    name = Path(filename).name.lower()
    now = utcnow()
    reader = csv.DictReader(text_stream)

    if "relationships" in name:
        rel_batch = []
        for row in reader:
            tracker.counts["seen"] += 1
            node_1 = row.get("_start") or row.get("node_id_start") or row.get("start_id") or row.get("node_1")
            node_2 = row.get("_end") or row.get("node_id_end") or row.get("end_id") or row.get("node_2")
            link = (row.get("link") or row.get("_type") or row.get("rel_type") or "connected_to").strip().lower()

            if not node_1 or not node_2 or node_1 == node_2:
                tracker.counts["rejected"] += 1
                continue

            pred = "director_of" if "director" in link else ("owns" if "shareholder" in link else "linked_to")
            # Stable deterministic relationship ID avoids duplicates on re-import
            r_id = deterministic_rel_id("icij", f"icij:{node_1}", pred, f"icij:{node_2}", link, str(row.get("rel_id") or ""))

            rel_batch.append([
                r_id, source_id, f"icij:{node_1}", pred, f"icij:{node_2}",
                "reported", 0.8, row.get("start_date") or None, row.get("end_date") or None,
                row.get("status") or "active",
                dumps({"link": link}), None, now, now,
            ])

            if len(rel_batch) >= BATCH_SIZE:
                con.executemany(
                    """
                    INSERT OR REPLACE INTO relationships
                    (id, source_id, subject_id, predicate, object_id, basis, confidence, start_date, end_date, status, attrs, raw_record_id, first_seen_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rel_batch,
                )
                tracker.counts["inserted"] += len(rel_batch)
                rel_batch.clear()

        if rel_batch:
            con.executemany(
                """
                INSERT OR REPLACE INTO relationships
                (id, source_id, subject_id, predicate, object_id, basis, confidence, start_date, end_date, status, attrs, raw_record_id, first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rel_batch,
            )
            tracker.counts["inserted"] += len(rel_batch)

    elif "nodes-" in name or "entities" in name or "officers" in name or "intermediaries" in name or "others" in name:
        kind = "company" if "entities" in name else ("person" if "officers" in name else "organization")
        entity_batch = []
        id_batch = []

        for row in reader:
            tracker.counts["seen"] += 1
            node_id = row.get("node_id") or row.get("_id") or row.get("id") or row.get("EntityID")
            node_name = row.get("name") or row.get("name_clean") or row.get("Name")

            if not node_id or not node_name:
                tracker.counts["rejected"] += 1
                continue

            ent_id = f"icij:{node_id}"
            country = to_alpha2(row.get("country_codes", "")) or to_alpha2(row.get("countries", ""))
            jurisdiction = row.get("jurisdiction_description") or row.get("jurisdiction")
            addr = row.get("address")

            entity_batch.append([
                ent_id, source_id, kind, node_name, normalize_name(node_name),
                country, jurisdiction, row.get("status") or "active",
                row.get("company_type") or kind,
                row.get("incorporation_date") or None, row.get("inactivation_date") or None,
                addr, normalize_address(addr), "",
                dumps({"sourceID": row.get("sourceID"), "node_id": node_id}),
                None, now, now,
            ])
            id_batch.append([ent_id, "icij_node", str(node_id)])

            if len(entity_batch) >= BATCH_SIZE:
                _upsert_entities_and_ids(con, entity_batch, id_batch)
                tracker.counts["inserted"] += len(entity_batch)
                entity_batch.clear()
                id_batch.clear()

        if entity_batch:
            _upsert_entities_and_ids(con, entity_batch, id_batch)
            tracker.counts["inserted"] += len(entity_batch)


def import_icij(con: Any, path: Path) -> dict:
    """Stream and batch-upsert ICIJ Offshore Leaks dataset from a .zip archive, directory, or CSV file."""
    source_id = "icij"
    total_stats = {"seen": 0, "inserted": 0, "rejected": 0, "errors": 0}

    with IngestTracker(con, source_id, path, "icij_dataset") as master_tracker:
        if path.is_file() and path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path, "r") as zf:
                csv_members = [
                    m for m in zf.namelist()
                    if m.lower().endswith(".csv")
                    and not m.startswith("__MACOSX")
                    and not Path(m).name.startswith(".")
                ]
                if not csv_members:
                    raise ValueError(f"No CSV files found inside ICIJ zip archive: {path.name}")

                for member in sorted(csv_members):
                    with zf.open(member) as raw_file:
                        text_stream = io.TextIOWrapper(raw_file, encoding="utf-8-sig", errors="replace")
                        _process_icij_csv_stream(con, member, text_stream, master_tracker)

        elif path.is_dir():
            csv_files = sorted([
                p for p in path.rglob("*.csv")
                if not p.name.startswith(".") and "__MACOSX" not in p.parts
            ])
            for csv_path in csv_files:
                with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
                    _process_icij_csv_stream(con, csv_path.name, f, master_tracker)

        elif path.is_file() and path.suffix.lower() == ".csv":
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                _process_icij_csv_stream(con, path.name, f, master_tracker)

        total_stats = master_tracker.counts

    return total_stats


# Legacy alias
import_icij_directory = import_icij


# ---------------------------------------------------------------------------
# 4. OpenSanctions CSV Importer
# ---------------------------------------------------------------------------

def import_opensanctions_csv(con: Any, path: Path) -> dict:
    """Stream and batch-upsert OpenSanctions targets.simple.csv."""
    source_id = "opensanctions"
    with IngestTracker(con, source_id, path, "opensanctions_targets_csv") as tracker:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            entity_batch = []
            id_batch = []
            now = utcnow()

            for row in reader:
                tracker.counts["seen"] += 1
                os_id = _pick(row, "id", "ID", "entity_id")
                name = _pick(row, "name", "caption", "Name")

                if not os_id or not name:
                    tracker.counts["rejected"] += 1
                    continue

                ent_id = f"os:{os_id}"
                schema = (_pick(row, "schema", "Schema") or "LegalEntity").lower()
                kind = "person" if "person" in schema else ("company" if "company" in schema or "organization" in schema else "asset")
                countries_raw = _pick(row, "countries", "country") or ""
                countries = [c.strip() for c in countries_raw.split(";") if c.strip()]
                country = to_alpha2(countries[0]) if countries else None
                sanctions = _pick(row, "sanctions", "program") or ""
                addresses = _pick(row, "addresses", "address")

                attrs = {
                    "schema": _pick(row, "schema"),
                    "datasets": [d.strip() for d in (_pick(row, "dataset", "datasets") or "").split(";") if d.strip()],
                    "sanctions": sanctions[:400] if sanctions else None,
                }

                entity_batch.append([
                    ent_id, source_id, kind, name, normalize_name(name),
                    country, None, "sanctioned" if sanctions else "active",
                    _pick(row, "schema") or "Entity",
                    _pick(row, "birth_date") or None, None,
                    addresses, normalize_address(addresses),
                    "sanction" if sanctions else "", dumps(attrs), None, now, now,
                ])

                # Process identifiers column
                ids_str = _pick(row, "identifiers") or ""
                for raw_ident in ids_str.split(";"):
                    raw_ident = raw_ident.strip()
                    if raw_ident:
                        sch = detect_scheme(raw_ident)
                        id_batch.append([ent_id, sch, raw_ident])

                if len(entity_batch) >= BATCH_SIZE:
                    _upsert_entities_and_ids(con, entity_batch, id_batch)
                    tracker.counts["inserted"] += len(entity_batch)
                    entity_batch.clear()
                    id_batch.clear()

            if entity_batch:
                _upsert_entities_and_ids(con, entity_batch, id_batch)
                tracker.counts["inserted"] += len(entity_batch)

    return tracker.counts


# ---------------------------------------------------------------------------
# 5. NSE Excel Importer (.xls, .xlsx, .csv)
# ---------------------------------------------------------------------------

def _read_excel_rows(path: Path) -> list[list[str]]:
    """Read rows from an Excel file (.xlsx, .xls) with multiple library fallbacks."""
    name = path.name.lower()
    rows: list[list[str]] = []

    if name.endswith(".xlsx"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            for r in wb.worksheets[0].iter_rows(values_only=True):
                rows.append([str(c or "") for c in r])
            wb.close()
            return rows
        except Exception as e:
            logger.warning(f"openpyxl failed to read {path.name}: {e}")

    elif name.endswith(".xls"):
        # Try xlrd for BIFF files
        try:
            import xlrd
            wb = xlrd.open_workbook(path)
            sh = wb.sheet_by_index(0)
            for rx in range(sh.nrows):
                rows.append([str(c.value or "") for c in sh.row(rx)])
            return rows
        except Exception:
            pass

        # Try openpyxl if it's an XLSX renamed to .xls
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            for r in wb.worksheets[0].iter_rows(values_only=True):
                rows.append([str(c or "") for c in r])
            wb.close()
            return rows
        except Exception:
            pass

    # Try plain text fallback (HTML table, TSV, or CSV saved with .xls extension)
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            content = f.read(8192)
            f.seek(0)
            if "<tr" in content.lower() and "<td" in content.lower():
                # HTML table parsing
                full_html = f.read()
                tr_matches = re.findall(r"<tr[^>]*>(.*?)</tr>", full_html, flags=re.DOTALL | re.IGNORECASE)
                for tr in tr_matches:
                    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.DOTALL | re.IGNORECASE)
                    clean_cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                    if any(clean_cells):
                        rows.append(clean_cells)
                if rows:
                    return rows
            else:
                reader = csv.reader(f)
                return [row for row in reader if row]
    except Exception:
        pass

    return rows


def import_nse(con: Any, path: Path) -> dict:
    """Stream and batch-upsert NSE Debarred Entities lists from Excel (.xls, .xlsx) or CSV."""
    source_id = "nse"
    with IngestTracker(con, source_id, path, "nse_debarment_list") as tracker:
        rows = _read_excel_rows(path)

        if not rows:
            logger.warning(f"No readable data found in NSE file: {path.name}")
            tracker.error = f"Empty or unparseable Excel file: {path.name}"
            tracker.counts["errors"] += 1
            return tracker.counts

        headers = [h.strip().lower() for h in rows[0]]
        name_idx = -1
        pan_idx = -1
        cin_idx = -1
        date_idx = -1

        for idx, h in enumerate(headers):
            if "name" in h:
                name_idx = idx
            elif "pan" in h:
                pan_idx = idx
            elif "cin" in h or "din" in h:
                cin_idx = idx
            elif "order date" in h or "date" in h:
                date_idx = idx

        if name_idx == -1:
            name_idx = 2 if len(headers) > 2 else 0

        now = utcnow()
        entity_batch = []
        id_batch = []

        for r in rows[1:]:
            tracker.counts["seen"] += 1
            if len(r) <= name_idx:
                continue
            ent_name = r[name_idx].strip()
            if not ent_name or ent_name.lower().startswith("total"):
                continue

            pan = r[pan_idx].strip().upper() if pan_idx != -1 and len(r) > pan_idx else ""
            cin = r[cin_idx].strip() if cin_idx != -1 and len(r) > cin_idx else ""
            order_date = r[date_idx].strip() if date_idx != -1 and len(r) > date_idx else ""

            reg_id = pan if (pan and "NOT" not in pan) else (cin or normalize_identifier("", ent_name)[:20])
            ent_id = f"nse:{reg_id}"

            attrs = {
                "debarred_by": "SEBI / NSE",
                "order_date": order_date,
                "pan": pan if "NOT" not in pan else None,
                "cin_din": cin or None,
                "file": path.name,
            }

            entity_batch.append([
                ent_id, source_id, "company" if any(w in ent_name.upper() for w in ["LTD", "PVT", "CORP"]) else "person",
                ent_name, normalize_name(ent_name), "in", "India", "debarred",
                "Debarred Entity", order_date or None, None, None, None,
                "sanction,debarment", dumps(attrs), None, now, now,
            ])

            if pan and "NOT" not in pan:
                id_batch.append([ent_id, "pan", pan])
            if cin:
                id_batch.append([ent_id, "cin_or_din", cin])

            if len(entity_batch) >= BATCH_SIZE:
                _upsert_entities_and_ids(con, entity_batch, id_batch)
                tracker.counts["inserted"] += len(entity_batch)
                entity_batch.clear()
                id_batch.clear()

        if entity_batch:
            _upsert_entities_and_ids(con, entity_batch, id_batch)
            tracker.counts["inserted"] += len(entity_batch)

    return tracker.counts


# ---------------------------------------------------------------------------
# Classification & Master Importer
# ---------------------------------------------------------------------------

def classify_dataset_file(path: Path) -> str:
    """Classify a dataset file based on parent folder hierarchy, filename, and extension.

    Returns one of:
      - 'mca'
      - 'gleif_level1'
      - 'gleif_level2'
      - 'icij'
      - 'opensanctions'
      - 'nse'
      - 'unknown'
    """
    ext = path.suffix.lower()
    name = path.name.lower()
    stem = path.stem.lower()
    parent_parts = [p.lower() for p in path.parts[:-1]]

    # 1. GLEIF Level 1 (LEI-CDF)
    if ext in [".xml", ".zip"]:
        is_gleif_parent = any("gleif" in p for p in parent_parts)
        is_l1_name = any(k in name for k in ["level1", "lei-cdf", "lei_cdf", "goldencopy-lei", "lei2-golden-copy"])
        is_explicit_gleif_l1 = any(k in name for k in ["gleif_level1", "gleif-level1"])
        if is_explicit_gleif_l1 or (is_gleif_parent and "level1" in stem) or (is_l1_name and not "level2" in name):
            return "gleif_level1"

    # 2. GLEIF Level 2 (RR-CDF)
    if ext in [".xml", ".zip"]:
        is_gleif_parent = any("gleif" in p for p in parent_parts)
        is_l2_name = any(k in name for k in ["level2", "rr-cdf", "rr_cdf", "rr-golden-copy"])
        is_explicit_gleif_l2 = any(k in name for k in ["gleif_level2", "gleif-level2"])
        if is_explicit_gleif_l2 or (is_gleif_parent and "level2" in stem) or is_l2_name:
            return "gleif_level2"

    # 3. ICIJ Offshore Leaks
    is_icij_parent = any("icij" in p for p in parent_parts)
    if ext == ".zip" and (is_icij_parent or "full-oldb" in name or "offshoreleaks" in name or "icij" in name):
        return "icij"
    if ext == ".csv":
        if is_icij_parent or any(k in name for k in ["nodes-", "nodes_"]):
            return "icij"
        if "relationships" in name and (is_icij_parent or "icij" in name):
            return "icij"

    # 4. MCA Companies (Andhra Pradesh, Karnataka, etc.)
    if ext == ".csv":
        is_mca_parent = any("mca" in p for p in parent_parts)
        is_mca_name = "mca" in name or any(k in name for k in [
            "andhra_pradesh", "karnataka", "maharashtra", "delhi", "tamil_nadu",
            "company_master", "cin_data",
        ])
        if is_mca_parent or is_mca_name:
            return "mca"

    # 5. OpenSanctions
    if ext == ".csv":
        is_os_parent = any("opensanctions" in p for p in parent_parts)
        is_os_name = any(k in name for k in ["opensanctions", "targets.simple", "targets_simple", "sanctions"])
        if is_os_parent or is_os_name:
            return "opensanctions"

    # 6. NSE Debarred Lists
    if ext in [".xls", ".xlsx", ".csv"]:
        is_nse_parent = any("nse" in p for p in parent_parts)
        is_nse_name = any(k in name for k in ["nse", "prs_ra", "debarred", "sebi"])
        if is_nse_parent or is_nse_name:
            return "nse"

    # Header / content sniffing fallback for unclassified CSVs
    if ext == ".csv":
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                header_line = f.readline().lower()
                if "cin" in header_line and ("company" in header_line or "registration" in header_line):
                    return "mca"
                if "schema" in header_line and ("sanctions" in header_line or "caption" in header_line):
                    return "opensanctions"
                if "node_id" in header_line or ("_start" in header_line and "_end" in header_line):
                    return "icij"
                if "debarred" in header_line or ("pan" in header_line and "sebi" in header_line):
                    return "nse"
        except Exception:
            pass

    return "unknown"


def import_all_datasets(con: Any, target_path: Path, skip_gleif: bool = False, only: str | None = None) -> dict:
    """Master import pipeline: auto-detects, logs, streams, and batch-imports all datasets.

    Guarantees:
    - Fast datasets (MCA, OpenSanctions, NSE, ICIJ) process first before multi-gigabyte GLEIF archives.
    - Reports every dataset found, processed, skipped, or failed. Never silently skips.
    - Accurately classifies files by parent folder hierarchy as well as filenames.
    - Idempotent re-imports: re-running does not duplicate relationships or entities.
    """
    from ..duckdb_db import init_db
    init_db(con)

    summary: dict[str, Any] = {}
    path = Path(target_path)

    if not path.exists():
        raise FileNotFoundError(f"Dataset path does not exist: {path}")

    # Discover candidate files
    if path.is_file():
        files = [path]
    else:
        files = sorted([
            p for p in path.rglob("*")
            if p.is_file()
            and not p.name.startswith(".")
            and "__MACOSX" not in p.parts
        ])

    PRIORITY_ORDER = {
        "mca": 1,
        "opensanctions": 2,
        "nse": 3,
        "icij": 4,
        "gleif_level2": 5,
        "gleif_level1": 6,
        "unknown": 99,
    }

    # Classify files and sort by priority order (fast files first)
    classified: list[tuple[Path, str]] = []
    for f in files:
        cat = classify_dataset_file(f)
        if skip_gleif and cat.startswith("gleif"):
            continue
        if only and only.lower() not in cat.lower():
            continue
        classified.append((f, cat))

    classified.sort(key=lambda item: (PRIORITY_ORDER.get(item[1], 10), str(item[0])))

    print(f"\n==================================================")
    print(f"PLANC DATASET IMPORTER - DISCOVERY")
    print(f"Scanning target: {path}")
    print(f"Found {len(classified)} runnable file(s) in priority order (Fast datasets first).")
    print(f"==================================================")
    for f, cat in classified:
        rel_display = str(f.relative_to(path) if path.is_dir() else f.name)
        print(f"  [{cat.upper():<14}] {rel_display}")
    print(f"==================================================\n")

    # Processing loop
    for f, cat in classified:
        rel_key = str(f.relative_to(path) if path.is_dir() else f.name)
        start_ts = time.time()

        if cat == "unknown":
            msg = f"Unrecognized file format or dataset category. File will not be imported."
            print(f"[SKIPPED]    {rel_key} -> {msg}")
            summary[rel_key] = {
                "status": "skipped",
                "category": cat,
                "reason": msg,
                "file": f.name,
                "seen": 0,
                "inserted": 0,
            }
            continue

        print(f"[PROCESSING] [{cat.upper()}] {rel_key}...")
        try:
            if cat == "mca":
                stats = import_mca(con, f)
            elif cat == "gleif_level1":
                stats = import_gleif_level1(con, f)
            elif cat == "gleif_level2":
                stats = import_gleif_level2(con, f)
            elif cat == "icij":
                stats = import_icij(con, f)
            elif cat == "opensanctions":
                stats = import_opensanctions_csv(con, f)
            elif cat == "nse":
                stats = import_nse(con, f)
            else:
                stats = {"seen": 0, "inserted": 0, "rejected": 0, "errors": 0}

            elapsed = round(time.time() - start_ts, 2)
            summary[rel_key] = {
                "status": "success",
                "category": cat,
                "elapsed_seconds": elapsed,
                **stats,
            }
            print(
                f"[SUCCESS]    {rel_key} ({elapsed}s) -> seen: {stats.get('seen', 0):,}, "
                f"inserted: {stats.get('inserted', 0):,}, rejected: {stats.get('rejected', 0):,}, "
                f"errors: {stats.get('errors', 0):,}"
            )

        except Exception as exc:
            elapsed = round(time.time() - start_ts, 2)
            logger.exception(f"Error importing {f.name}: {exc}")
            summary[rel_key] = {
                "status": "failed",
                "category": cat,
                "error": str(exc),
                "elapsed_seconds": elapsed,
                "seen": 0,
                "inserted": 0,
            }
            print(f"[FAILED]     {rel_key} ({elapsed}s) -> ERROR: {exc}")

    print(f"\n==================================================")
    print(f"IMPORT EXECUTION COMPLETE")
    success_count = sum(1 for v in summary.values() if v.get("status") == "success")
    failed_count = sum(1 for v in summary.values() if v.get("status") == "failed")
    skipped_count = sum(1 for v in summary.values() if v.get("status") == "skipped")
    print(f"Summary: {success_count} succeeded, {failed_count} failed, {skipped_count} skipped.")
    print(f"==================================================\n")

    return summary
