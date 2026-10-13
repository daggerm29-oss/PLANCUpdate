"""Load every recognised dataset file from samples.zip (or an extracted folder) with the matching importer."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from . import gleif_files, icij, mca, nse, opensanctions_csv
from .files import extract_zip


def load_all(conn: sqlite3.Connection, path: Path) -> dict:
    path = Path(path)
    if path.is_file() and path.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory() as tmp:
            extract_zip(path, Path(tmp))
            return _load_folder(conn, Path(tmp))
    return _load_folder(conn, path)


def _load_folder(conn: sqlite3.Connection, folder: Path) -> dict:
    files = [p for p in folder.rglob("*") if p.is_file()]
    out: dict = {}
    for p in sorted(f for f in files if "mca" in f.name.lower() and f.suffix.lower() == ".csv"):
        out[p.name] = mca.load(conn, p)
    for p in files:
        name = p.name.lower()
        if name.startswith("gleif_level1") or "lei2" in name or "lei-cdf" in name:
            out[p.name] = gleif_files.load_level1(conn, p)
    for p in files:
        name = p.name.lower()
        if name.startswith("gleif_level2") or "rr-cdf" in name or "rr_cdf" in name:
            out[p.name] = gleif_files.load_level2(conn, p)
    icij_dirs = sorted({p.parent for p in files if "nodes-entities" in p.name.lower()})
    for d in icij_dirs:
        out[f"icij:{d.name}"] = icij.load(conn, d)
    for p in files:
        if "opensanctions" in p.name.lower() and "simple" in p.name.lower():
            out[p.name] = opensanctions_csv.load(conn, p)
    for p in sorted(f for f in files if "nse" in f.name.lower() and f.suffix.lower() in (".xls", ".xlsx", ".csv")):
        out[p.name] = nse.load(conn, p)
    return out
