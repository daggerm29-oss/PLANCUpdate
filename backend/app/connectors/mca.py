"""MCA Company Master Data (data.gov.in, state-wise CSV) importer.

Observed header (2026 files):
  CIN, Company Name, Company Registration Date, Company Category, Company Class, Listing Status,
  Authorized Capital, Paidup Capital, Company ROC, Company Address, Pin Code, Company State,
  Company Status, Company Sub Category, Company Industrial Classification
Older data.gov.in exports use upper-case names (CORPORATE_IDENTIFICATION_NUMBER, ...); both are accepted.
The "Pin Code" column is blank in the 2026 sample, so the PIN is taken from the address ("...,560049-India").
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .. import db
from ..resolution.identifiers import CIN_RE, FCRN_RE, LLPIN_RE, parse_cin
from ..resolution.normalize import normalize_identifier
from ..settings import settings
from .files import batched, file_provenance, ingest_run, iter_csv, pick

SOURCE = "mca"
METHOD = "bulk_file:mca_company_master_csv"
PIN_RE = re.compile(r"\b(\d{6})\s*-?\s*India\b", re.IGNORECASE)

COLUMNS = {
    "cin": ("CIN", "CORPORATE_IDENTIFICATION_NUMBER", "Corporate Identification Number", "LLPIN"),
    "name": ("Company Name", "COMPANY_NAME", "LLP Name"),
    "registration_date": ("Company Registration Date", "DATE_OF_REGISTRATION", "Date of Incorporation"),
    "category": ("Company Category", "COMPANY_CATEGORY"),
    "class": ("Company Class", "COMPANY_CLASS"),
    "listing_status": ("Listing Status", "LISTING_STATUS"),
    "authorized_capital": ("Authorized Capital", "AUTHORIZED_CAP", "Authorised Capital"),
    "paid_up_capital": ("Paidup Capital", "PAIDUP_CAPITAL", "Paid up Capital"),
    "roc": ("Company ROC", "REGISTRAR_OF_COMPANIES", "ROC"),
    "address": ("Company Address", "REGISTERED_OFFICE_ADDRESS", "Registered Office Address"),
    "pin_code": ("Pin Code", "PIN_CODE"),
    "state": ("Company State", "REGISTERED_STATE", "State"),
    "status": ("Company Status", "COMPANY_STATUS"),
    "sub_category": ("Company Sub Category", "COMPANY_SUB_CATEGORY"),
    "industrial_classification": ("Company Industrial Classification", "PRINCIPAL_BUSINESS_ACTIVITY_AS_PER_CIN",
                                  "PRINCIPAL_BUSINESS_ACTIVITY"),
}


def parse_date(value: str) -> str | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_amount(value: str) -> float | None:
    cleaned = re.sub(r"[^\d.]", "", value or "")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def identifier_scheme(value: str) -> str | None:
    compact = normalize_identifier("", value)
    if CIN_RE.match(compact):
        return "cin"
    if LLPIN_RE.match(value.upper()):
        return "llpin"
    if FCRN_RE.match(compact):
        return "fcrn"
    return None


def map_row(row: dict, file_label: str) -> dict:
    get = {field: pick(row, *names) for field, names in COLUMNS.items()}
    reg_id = normalize_identifier("", get["cin"])
    scheme = identifier_scheme(get["cin"])
    if not reg_id or not get["name"] or not scheme:
        raise ValueError(f"row without a valid CIN/LLPIN/FCRN: {get['cin']!r}")
    pin = re.sub(r"\D", "", get["pin_code"]) or (PIN_RE.search(get["address"]).group(1) if PIN_RE.search(get["address"]) else None)
    legal_form = "; ".join(p for p in (get["category"], get["class"]) if p) or None
    return {
        "id": f"mca:{reg_id}",
        "source_id": SOURCE,
        "kind": "company",
        "name": re.sub(r"\s+", " ", get["name"]),
        "country": "in",
        "jurisdiction": get["state"] or None,
        "status": get["status"].lower() or None,
        "legal_form": legal_form,
        "incorporation_date": parse_date(get["registration_date"]),
        "address": re.sub(r"\s+", " ", get["address"]) or None,
        "topics": [],
        "names": [],
        "identifiers": [(scheme, reg_id)],
        "attrs": {
            "cin": reg_id if scheme == "cin" else None,
            "llpin": reg_id if scheme == "llpin" else None,
            "cin_parsed": parse_cin(reg_id) if scheme == "cin" else None,
            "category": get["category"] or None,
            "class": get["class"] or None,
            "listing_status": get["listing_status"] or None,
            "authorized_capital": parse_amount(get["authorized_capital"]),
            "paid_up_capital": parse_amount(get["paid_up_capital"]),
            "roc": get["roc"] or None,
            "pin_code": pin,
            "state": get["state"] or None,
            "sub_category": get["sub_category"] or None,
            "industrial_classification": get["industrial_classification"] or None,
            "file": file_label,
        },
    }


def load(conn: sqlite3.Connection, path: Path, *, source_url: str | None = None) -> dict:
    path = Path(path)
    prov = file_provenance(path)
    url = source_url or settings.mca_source_url
    with ingest_run(conn, SOURCE, {"kind": "mca_csv", "url": url, **prov}) as (run_id, stats):
        def handle(row: dict) -> None:
            entity = map_row(row, prov["file"])
            reg_id = entity["identifiers"][0][1]
            raw_id, state = db.upsert_raw(conn, SOURCE, f"company:{reg_id}", row, url, run_id)
            stats[state] += 1
            if state == "unchanged" and db.entity_exists(conn, entity["id"]):
                return
            entity["raw_record_id"] = raw_id
            db.upsert_entity(conn, entity)
            a = entity["attrs"]
            db.add_evidence(
                conn,
                key=f"mca:company:{reg_id}",
                source_id=SOURCE,
                url=url,
                title=f"MCA Company Master Data: {entity['name']} ({prov['file']})",
                excerpt=(
                    f"{reg_id}; status {entity['status'] or 'n/a'}; registered {entity['incorporation_date'] or 'n/a'}; "
                    f"{a['roc'] or 'ROC n/a'}; authorised capital {a['authorized_capital']}; paid-up {a['paid_up_capital']}; "
                    f"address: {entity['address'] or 'n/a'}"
                ),
                raw_record_id=raw_id,
                entity_id=entity["id"],
                extraction_method=METHOD,
                confidence=1.0,
            )

        batched(conn, iter_csv(path), handle, stats)
    return {"run_id": run_id, "file": prov["file"], **stats}
