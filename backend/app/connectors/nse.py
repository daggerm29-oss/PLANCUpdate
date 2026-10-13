"""NSE "entities debarred by SEBI / other authorities" lists (XLS, XLSX or CSV; format detected from bytes).

Observed columns: Order Date, Order Particulars, Entity / Individual Name, PAN, DIN / CIN, Symbol, Period,
NSE Circular No. (For Debarment), Date of NSE circular, NSE Circular No. (For Revocation), Date of NSE circular. (For Revocation)
Observed quirks: `datasets_nse_prs_ra_others.xls` is really XLSX; dates are text ('16-JUl-2026') in one file and
Excel serials in the other; DINs are stored as floats without the leading zero; PAN may be 'PAN Not Provided'.

Rows are grouped per PAN (or per name + CIN/DIN when no PAN), so one entity carries all of its orders.
A debarment is a regulatory order, not a criminal finding; matches against it are leads for review.
"""
from __future__ import annotations

import csv
import hashlib
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .. import db
from ..resolution.identifiers import CIN_RE, LLPIN_RE, PAN_HOLDER, PAN_RE, normalize_din
from ..resolution.normalize import normalize_identifier, normalize_name
from ..settings import settings
from .files import batched, file_provenance, ingest_run, read_bytes_head

SOURCE = "nse"
METHOD = "bulk_file:nse_debarred_list"
COMPANY_WORDS = re.compile(r"\b(LTD|LIMITED|PVT|PRIVATE|LLP|CORPORATION|INDUSTRIES|ENTERPRISES|INFRA\w*|"
                           r"FINANCE|SECURITIES|CAPITAL|VENTURES|HOLDINGS|TRADERS|EXPORTS|JEWELLERS|CO\.?)\b", re.I)
HEADERS = {
    "order_date": ("order date",),
    "particulars": ("order particulars",),
    "name": ("entity / individual name", "entity/individual name", "name"),
    "pan": ("pan",),
    "din_cin": ("din / cin", "din/cin"),
    "symbol": ("symbol",),
    "period": ("period",),
    "circular": ("nse circular no. (for debarment)",),
    "circular_date": ("date of nse circular",),
    "revocation_circular": ("nse circular no. (for revocation)",),
    "revocation_date": ("date of nse circular. (for revocation)", "date of nse circular (for revocation)"),
}


def _rows_from_xlsx(path: Path) -> Iterator[list]:
    import openpyxl  # openpyxl refuses a ".xls" name, so it is given a file object

    with open(path, "rb") as fh:
        wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
        try:
            for row in wb.worksheets[0].iter_rows(values_only=True):
                yield list(row)
        finally:
            wb.close()


def _rows_from_xls(path: Path) -> tuple[Iterator[list], int]:
    import xlrd

    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    return (sheet.row_values(i) for i in range(sheet.nrows)), book.datemode


def read_rows(path: Path) -> tuple[list[list], int | None, str]:
    head = read_bytes_head(path)
    if head.startswith(b"PK"):
        return list(_rows_from_xlsx(path)), None, "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        rows, datemode = _rows_from_xls(path)
        return list(rows), datemode, "xls"
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        return list(csv.reader(fh)), None, "csv"


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_date(value, datemode: int | None) -> str | None:
    if value in (None, "", "-"):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:  # Excel serial day
        base = datetime(1904, 1, 1) if datemode == 1 else datetime(1899, 12, 30)
        return (base + timedelta(days=float(value))).strftime("%Y-%m-%d")
    text = _cell(value)
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%b-%y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text.title() if "%b" in fmt or "%B" in fmt else text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _header_index(rows: list[list]) -> tuple[int, dict[str, int]]:
    for i, row in enumerate(rows[:20]):
        cells = [_cell(c).lower() for c in row]
        index = {field: cells.index(alias) for field, aliases in HEADERS.items() for alias in aliases if alias in cells}
        if "name" in index and "pan" in index:
            return i, index
    raise ValueError("could not find the header row (needs 'Entity / Individual Name' and 'PAN')")


def _kind(name: str, pan: str | None, cin: str | None) -> tuple[str, str | None]:
    holder = PAN_HOLDER.get(pan[3]) if pan else None
    if holder == "person":
        return "person", holder
    if holder == "company" or cin:
        return "company", holder or "company"
    if holder:
        return "organization", holder
    return ("company" if COMPANY_WORDS.search(name) else "person"), None


def load(conn: sqlite3.Connection, path: Path, *, list_name: str | None = None, source_url: str | None = None) -> dict:
    path = Path(path)
    prov = file_provenance(path)
    url = source_url or settings.nse_source_url
    rows, datemode, fmt = read_rows(path)
    header_row, idx = _header_index(rows)
    label = list_name or path.stem
    with ingest_run(conn, SOURCE, {"kind": "nse_debarred", "format": fmt, "list": label, "url": url, **prov}) as (run_id, stats):
        groups: dict[str, list[dict]] = defaultdict(list)
        for raw in rows[header_row + 1:]:
            rec = {f: (raw[i] if i < len(raw) else None) for f, i in idx.items()}
            name = _cell(rec.get("name"))
            if not name:
                stats["skipped_blank"] += 1
                continue
            pan = normalize_identifier("pan", _cell(rec.get("pan")))
            pan = pan if PAN_RE.match(pan) else None
            din_cin = _cell(rec.get("din_cin"))
            compact = normalize_identifier("cin", din_cin)
            cin = compact if CIN_RE.match(compact) else None
            llpin = compact if not cin and LLPIN_RE.match(din_cin.upper()) else None
            din = normalize_din(rec.get("din_cin")) if not cin and not llpin else None
            order = {
                "order_date": parse_date(rec.get("order_date"), datemode),
                "particulars": _cell(rec.get("particulars")) or None,
                "period": _cell(rec.get("period")) or None,
                "symbol": _cell(rec.get("symbol")) or None,
                "circular": _cell(rec.get("circular")) if _cell(rec.get("circular")) not in ("", "-") else None,
                "circular_date": parse_date(rec.get("circular_date"), datemode),
                "revocation_circular": _cell(rec.get("revocation_circular")) if _cell(rec.get("revocation_circular")) not in ("", "-") else None,
                "revocation_date": parse_date(rec.get("revocation_date"), datemode),
                "list": label,
            }
            row_payload = {"name": name, "pan": _cell(rec.get("pan")), "din_cin": din_cin, **order}
            row_hash = hashlib.sha1(db.dumps(row_payload).encode()).hexdigest()[:20]
            key = f"pan:{pan}" if pan else "name:" + hashlib.sha1(
                f"{normalize_name(name)}|{cin or llpin or din or ''}".encode()).hexdigest()[:16]
            groups[key].append({"name": name, "pan": pan, "cin": cin, "llpin": llpin, "din": din, "order": order,
                                "payload": row_payload, "row_hash": row_hash})
        stats["rows"] = sum(len(g) for g in groups.values())

        def handle(item: tuple[str, list[dict]]) -> None:
            key, all_members = item
            members = list({m["row_hash"]: m for m in all_members}.values())
            stats["duplicate_rows"] += len(all_members) - len(members)
            first = members[0]
            changed = False
            raw_ids = []
            for m in members:
                raw_id, state = db.upsert_raw(conn, SOURCE, f"row:{m['row_hash']}", m["payload"], url, run_id)
                stats[state] += 1
                changed |= state != "unchanged"
                raw_ids.append(raw_id)
            eid = f"nse:{key}"
            existing = conn.execute("SELECT attrs FROM entities WHERE id=?", (eid,)).fetchone()
            if not changed and existing:
                return
            # The same person/company can be on several lists: keep the orders loaded from the other files.
            other_orders = [o for o in db.loads(existing[0], {}).get("orders", []) if o.get("list") != label] if existing else []
            other_ids = {(r[0], r[1]) for r in conn.execute(
                "SELECT scheme, value FROM identifiers WHERE entity_id=?", (eid,))} if existing else set()
            pan = first["pan"]
            cin = next((m["cin"] for m in members if m["cin"]), None) or next((v for s, v in other_ids if s == "cin"), None)
            kind, holder = _kind(first["name"], pan, cin)
            identifiers = sorted(other_ids | ({("pan", pan)} if pan else set())
                                 | {(s, m[s]) for m in members for s in ("cin", "llpin", "din") if m[s]})
            orders = sorted([*(m["order"] for m in members), *other_orders], key=lambda o: o["order_date"] or "", reverse=True)
            active = [o for o in orders if not o["revocation_circular"] and not o["revocation_date"]]
            names = sorted({(m["name"], "alias") for m in members if m["name"] != first["name"]} | {
                (r[0], "alias") for r in conn.execute("SELECT name FROM entity_names WHERE entity_id=?", (eid,))
                if r[0] != first["name"]})
            db.upsert_entity(conn, {
                "id": eid,
                "source_id": SOURCE,
                "kind": kind,
                "name": first["name"],
                "country": "in",
                "status": "debarred" if active else "revoked",
                "topics": ["debarment"] if active else ["debarment.revoked"],
                "names": names,
                "identifiers": identifiers,
                "raw_record_id": raw_ids[0],
                "attrs": {"pan_holder_type": holder, "orders": orders[:50], "order_count": len(orders),
                          "active_orders": len(active), "lists": sorted({o["list"] for o in orders})},
            })
            for m, raw_id in zip(members, raw_ids):
                o = m["order"]
                db.add_evidence(
                    conn,
                    key=f"nse:order:{m['row_hash']}",
                    source_id=SOURCE,
                    url=url,
                    title=f"NSE debarment list ({o['list']}): {m['name']}",
                    excerpt=(f"{o['particulars'] or 'Order'}; order date {o['order_date'] or 'n/a'}; period {o['period'] or 'n/a'}; "
                             f"symbol {o['symbol'] or 'n/a'}; circular {o['circular'] or 'n/a'}"
                             + (f"; revoked by {o['revocation_circular']} {o['revocation_date'] or ''}" if o["revocation_circular"] else "")),
                    raw_record_id=raw_id,
                    entity_id=eid,
                    extraction_method=METHOD,
                    confidence=1.0,
                )
            stats["entities"] += 1

        batched(conn, groups.items(), handle, stats)
    return {"run_id": run_id, "file": prov["file"], "format": fmt, **stats}
