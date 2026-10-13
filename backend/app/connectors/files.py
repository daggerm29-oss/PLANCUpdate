"""Shared plumbing for bulk-file importers: provenance, CSV reading, batched transactions and run bookkeeping."""
from __future__ import annotations

import csv
import logging
import sqlite3
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .. import db
from ..http import sha1_file

BATCH = 1000
log = logging.getLogger(__name__)
csv.field_size_limit(16 * 1024 * 1024)


def file_provenance(path: Path) -> dict:
    stat = path.stat()
    return {
        "file": path.name,
        "sha1": sha1_file(path),
        "bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%dT%H:%M:%S"),
    }


def iter_csv(path: Path) -> Iterator[dict]:
    """Rows as dicts with stripped keys/values; tolerates a UTF-8 BOM and stray cp1252 bytes."""
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        reader.fieldnames = [(f or "").strip() for f in reader.fieldnames or []]
        for row in reader:
            yield {k: (v or "").strip() for k, v in row.items() if k}


def pick(row: dict, *names: str) -> str:
    """First non-empty value among alternative column names (case-insensitive)."""
    lowered = {k.lower(): v for k, v in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, "", "-", "NA", "N/A"):
            return value
    return ""


@contextmanager
def ingest_run(conn: sqlite3.Connection, source_id: str, params: dict) -> Iterator[tuple[int, Counter]]:
    run_id = db.start_run(conn, source_id, params)
    stats: Counter = Counter()
    try:
        yield run_id, stats
    except Exception as exc:
        db.finish_run(conn, run_id, "failed", stats, f"{type(exc).__name__}: {exc}"[:500])
        raise
    db.finish_run(conn, run_id, "partial" if stats.get("errors") else "success", stats)


def batched(conn: sqlite3.Connection, items: Iterable, handle: Callable[[object], None], stats: Counter) -> None:
    """Apply `handle` to each item, committing every BATCH items; a bad row is counted, never fatal."""
    pending = 0
    conn.execute("BEGIN")
    try:
        for item in items:
            try:
                conn.execute("SAVEPOINT row")
                handle(item)
                conn.execute("RELEASE row")
            except (KeyError, ValueError, TypeError) as exc:
                conn.execute("ROLLBACK TO row")
                conn.execute("RELEASE row")
                stats["errors"] += 1
                if stats["errors"] <= 5:
                    log.warning("skipping bad row: %s", exc)
            pending += 1
            if pending >= BATCH:
                conn.execute("COMMIT")
                conn.execute("BEGIN")
                pending = 0
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def extract_zip(zip_path: Path, dest: Path) -> list[Path]:
    """Extract an archive safely (no absolute paths or '..')."""
    out: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = Path(info.filename)
            if info.is_dir() or name.is_absolute() or ".." in name.parts:
                continue
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as fh:
                fh.write(src.read())
            out.append(target)
    return out


def read_bytes_head(path: Path, n: int = 8) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(n)
