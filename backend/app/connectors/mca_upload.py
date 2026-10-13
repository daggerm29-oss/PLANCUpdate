"""Browser upload of an MCA Company Master CSV (or a .zip holding one) into the local store.

The body is streamed to disk (never held in memory), size-capped, header-checked, then loaded by `mca.load` in a
background thread so a 100+ MB state file does not hold an HTTP request open. Jobs live in memory: they are a
progress view only; the durable record is the `ingest_runs` row that `mca.load` writes.
"""
from __future__ import annotations

import csv
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import AsyncIterator

from .. import db
from ..settings import settings
from . import mca

MAX_UPLOAD_BYTES = 300 * 1024 * 1024  # keep equal to proxyClientMaxBodySize in next.config.mjs
db_path_override: Path | None = None
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class UploadError(ValueError):
    pass


def _cin_header(header: list[str]) -> bool:
    wanted = {n.lower() for n in mca.COLUMNS["cin"]}
    names = {n.lower() for n in mca.COLUMNS["name"]}
    cols = {(h or "").strip().lstrip("\ufeff").lower() for h in header}
    return bool(cols & wanted) and bool(cols & names)


def _check_csv(path: Path) -> None:
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        header = next(csv.reader(fh), [])
    if not _cin_header(header):
        raise UploadError(f"{path.name}: not an MCA Company Master CSV (need a CIN column and a company name column; "
                          f"got {', '.join(h for h in header[:6] if h) or 'no header'})")


def _extract_zip(path: Path, dest: Path) -> list[Path]:
    out: list[Path] = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if info.is_dir() or not name.lower().endswith(".csv") or name.startswith("."):
                continue
            target = dest / _SAFE_NAME.sub("_", name)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(target)
    if not out:
        raise UploadError("the .zip holds no .csv file")
    return out


async def save_stream(filename: str, chunks: AsyncIterator[bytes]) -> Path:
    suffix = Path(filename).suffix.lower()
    if suffix not in (".csv", ".zip"):
        raise UploadError("upload a .csv or .zip file")
    upload_dir = Path(db_path_override or settings.db_path).parent / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe = _SAFE_NAME.sub("_", Path(filename).name)[:120] or f"upload{suffix}"
    target = Path(tempfile.mkdtemp(dir=upload_dir)) / safe
    size = 0
    try:
        with open(target, "wb") as fh:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise UploadError(f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
                fh.write(chunk)
        if size == 0:
            raise UploadError("the file is empty")
    except BaseException:
        shutil.rmtree(target.parent, ignore_errors=True)
        raise
    return target


def prepare(path: Path) -> list[Path]:
    """Validate an uploaded file and return the CSV path(s) to load. Removes the upload on failure."""
    try:
        files = _extract_zip(path, path.parent) if path.suffix.lower() == ".zip" else [path]
        for f in files:
            _check_csv(f)
        return files
    except zipfile.BadZipFile as exc:
        shutil.rmtree(path.parent, ignore_errors=True)
        raise UploadError("the .zip file is damaged or not a zip") from exc
    except BaseException:
        shutil.rmtree(path.parent, ignore_errors=True)
        raise


def _run(job_id: str, files: list[Path], workdir: Path) -> None:
    conn = db.connect(db_path_override)
    try:
        for f in files:
            _update(job_id, current_file=f.name)
            result = mca.load(conn, f, source_url=settings.mca_source_url)
            with _lock:
                _jobs[job_id]["results"].append(result)
        _update(job_id, status="done", current_file=None)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as the job error
        _update(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        conn.close()
        _update(job_id, finished_at=time.time())
        shutil.rmtree(workdir, ignore_errors=True)


def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id].update(fields)


def start(filename: str, files: list[Path]) -> dict:
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        if any(j["status"] == "running" for j in _jobs.values()):
            shutil.rmtree(files[0].parent, ignore_errors=True)
            raise UploadError("another MCA upload is still loading; wait for it to finish")
        _jobs[job_id] = {"id": job_id, "filename": filename, "status": "running", "started_at": time.time(),
                         "finished_at": None, "current_file": None, "results": [], "error": None}
    threading.Thread(target=_run, args=(job_id, files, files[0].parent), daemon=True).start()
    return job(job_id)


def job(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return None if j is None else {**j, "results": list(j["results"])}


def recent(limit: int = 5) -> list[dict]:
    with _lock:
        items = sorted(_jobs.values(), key=lambda j: j["started_at"], reverse=True)[:limit]
        return [{**j, "results": list(j["results"])} for j in items]


def wait(job_id: str, timeout: float = 60.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = job(job_id)
        if j is None or j["status"] != "running":
            return j
        time.sleep(0.05)
    return job(job_id)


__all__ = ["UploadError", "save_stream", "prepare", "start", "job", "recent", "wait"]
