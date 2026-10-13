"""Client for the Corporate Intelligence Bridge crawler (services/crawler/app.py), using only its documented API:

  GET  {CRAWLER_URL}/health                  -> {status, timestamp, serper_configured}
  POST {CRAWLER_URL}/google_and_deep_scrape  {query} -> {target_query, source_url, company_metadata,
                                                          directors_network[], acquisitions_count}

Every scrape spends Serper credits inside the crawler (1-2 searches + up to 2 scrape-proxy calls), and the crawler
has no cache or limits of its own, so this client is the single gate:
- cache: successful responses for CRAWLER_CACHE_TTL_SECONDS, "no results" (404) for CRAWLER_NEGATIVE_TTL_SECONDS;
  errors are never cached.
- dedup: an in-process per-query lock plus a DB lease (`crawl_requests.status = in_flight`), so identical concurrent
  requests from any thread or process cause one upstream call and the others reuse its result.
- limits: CRAWLER_RPM and CRAWLER_DAILY_BUDGET are enforced on a shared SQLite ledger (`provider_usage`), per attempt.
- retries: at most CRAWLER_MAX_RETRIES, and only when the request certainly did not run (connection refused,
  429, 502/503/504 from the hosting proxy). A 500 or a read timeout may already have spent credits, so neither is retried.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from .. import db
from ..http import parse_retry_after
from ..settings import Settings, settings

log = logging.getLogger(__name__)
SOURCE = "crawler"
PROVIDER = "crawler"
SCRAPE_PATH = "/google_and_deep_scrape"
HEALTH_PATH = "/health"
CREDITS_UPPER_BOUND = {"ok": 6, "not_found": 1, "error": 6, "timeout": 6}  # per app.py: <=2 searches + <=3 pages
SCRAPE_GATEWAY_CREDITS = 2  # conservative per-page cost assumed for scrape.serper.dev


def reported_credits(body: Any) -> int | None:
    """Crawler v2 reports the Serper calls it made; v1 does not, so the caller falls back to the upper bound."""
    calls = body.get("serper_calls") if isinstance(body, dict) else None
    if not isinstance(calls, dict):
        return None
    try:
        return int(calls.get("search", 0)) + SCRAPE_GATEWAY_CREDITS * int(calls.get("scrape_gateway", 0))
    except (TypeError, ValueError):
        return None
RETRYABLE_STATUS = {429, 502, 503, 504}
COUNTED = "status NOT IN ('cache_hit', 'deduplicated', 'rejected')"


class CrawlerError(Exception):
    """code: not_configured | rate_limited | budget_exhausted | in_progress | bad_request | upstream | timeout |
    unreachable | bad_response"""

    def __init__(self, code: str, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "status": self.status, "retry_after": self.retry_after}


@dataclass
class CrawlResult:
    key: str
    query: str
    status: str                # ok | not_found
    response: dict
    cached: bool
    deduplicated: bool
    fetched_at: float
    raw_record_id: int | None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("key", "query", "status", "cached", "deduplicated", "fetched_at",
                                              "raw_record_id")}


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip())


def query_key(query: str) -> str:
    return hashlib.sha256(("v1|" + normalize_query(query).lower()).encode()).hexdigest()[:32]


def validate_response(body: Any) -> dict:
    if not isinstance(body, dict) or not isinstance(body.get("company_metadata"), str):
        raise CrawlerError("bad_response", "crawler response does not match the documented schema")
    if not isinstance(body.get("directors_network", []), list):
        raise CrawlerError("bad_response", "directors_network is not a list")
    return body


_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _key_locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


class CrawlerClient:
    def __init__(self, cfg: Settings = settings, *, db_path: str | None = None, session: Any = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 poll_interval: float = 1.0):
        self.cfg = cfg
        self.base_url = cfg.crawler_url.rstrip("/")
        self.db_path = db_path or str(cfg.db_path)
        self.session = session or requests.Session()
        self.clock = clock
        self.sleep = sleep
        self.poll_interval = poll_interval
        self.lease_seconds = (cfg.crawler_connect_timeout + cfg.crawler_read_timeout) * (cfg.crawler_max_retries + 1) + 30

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _conn(self) -> sqlite3.Connection:
        conn = db.connect(self.db_path)
        db.init_db(conn)
        return conn

    # ------------------------------------------------------------------ health / usage

    def health(self) -> dict:
        if not self.configured:
            return {"configured": False, "reachable": False, "error": "CRAWLER_URL is not set"}
        started = self.clock()
        try:
            resp = self.session.get(self.base_url + HEALTH_PATH, timeout=(self.cfg.crawler_connect_timeout, 20))
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return {"configured": True, "reachable": resp.status_code == 200, "http_status": resp.status_code,
                    "serper_configured": bool(body.get("serper_configured")), "service_status": body.get("status"),
                    "latency_ms": int((self.clock() - started) * 1000), "url": self.base_url}
        except (requests.RequestException, ValueError) as exc:
            return {"configured": True, "reachable": False, "url": self.base_url,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    def usage(self) -> dict:
        conn = self._conn()
        try:
            now = self.clock()
            day = now - 86400
            counted = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(credits_estimate), 0) FROM provider_usage "
                f"WHERE provider=? AND operation='scrape' AND started_at>=? AND {COUNTED}", (PROVIDER, day)).fetchone()
            minute = conn.execute(
                f"SELECT COUNT(*) FROM provider_usage WHERE provider=? AND operation='scrape' AND started_at>=? AND {COUNTED}",
                (PROVIDER, now - 60)).fetchone()[0]
            by_status = dict(conn.execute(
                "SELECT status, COUNT(*) FROM provider_usage WHERE provider=? AND started_at>=? GROUP BY status",
                (PROVIDER, day)).fetchall())
            recent = db.rows(conn.execute(
                "SELECT id, request_key, started_at, finished_at, status, http_status, attempt, latency_ms, "
                "credits_estimate, error FROM provider_usage WHERE provider=? ORDER BY id DESC LIMIT 15", (PROVIDER,)))
            cache = conn.execute("SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM crawl_requests WHERE status IN ('ok','not_found')").fetchone()
            return {
                "configured": self.configured,
                "url": self.base_url or None,
                "limits": {"rpm": self.cfg.crawler_rpm, "daily_budget": self.cfg.crawler_daily_budget,
                           "max_retries": self.cfg.crawler_max_retries, "cache_ttl_seconds": self.cfg.crawler_cache_ttl_seconds,
                           "negative_ttl_seconds": self.cfg.crawler_negative_ttl_seconds,
                           "read_timeout_seconds": self.cfg.crawler_read_timeout},
                "last_24h": {"upstream_attempts": counted[0], "serper_credits_upper_bound": counted[1],
                             "remaining_attempts": max(0, self.cfg.crawler_daily_budget - counted[0]),
                             "by_status": by_status},
                "last_60s": {"upstream_attempts": minute},
                "cache": {"entries": cache[0], "hits": cache[1]},
                "recent": recent,
                "note": "Serper credits are an upper bound computed from the crawler code; the crawler does not report "
                        "actual usage. Check the Serper dashboard for exact figures.",
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------ scrape

    def scrape(self, query: str, *, force: bool = False, wait: bool = True) -> CrawlResult:
        query = normalize_query(query)
        if not query or len(query) > 200:
            raise CrawlerError("bad_request", "query must be 1-200 characters")
        if not self.configured:
            raise CrawlerError("not_configured", "CRAWLER_URL is not set; the crawler is disabled")
        key = query_key(query)
        lock = _lock_for(key)
        waited = not lock.acquire(blocking=False)
        if waited:
            if not wait:
                raise CrawlerError("in_progress", "an identical crawl is already running; retry shortly", retry_after=5.0)
            lock.acquire()
        try:
            conn = self._conn()
            try:
                cached = self._cached_or_lease(conn, key, query, force, wait, waited)
                if cached:
                    return cached
                try:
                    status, body, http_status = self._fetch_with_retries(conn, key, query)
                except CrawlerError as exc:
                    self._finish_lease(conn, key, "error", None, exc.status, str(exc))
                    raise
                return self._store(conn, key, query, status, body, http_status)
            finally:
                conn.close()
        finally:
            lock.release()

    def _cached_or_lease(self, conn, key, query, force, wait, waited=False) -> CrawlResult | None:
        deadline = self.clock() + self.lease_seconds
        while True:
            now = self.clock()
            with db.transaction(conn, immediate=True):
                row = conn.execute("SELECT * FROM crawl_requests WHERE key=?", (key,)).fetchone()
                fresh = row is not None and row["status"] in ("ok", "not_found") and (row["expires_at"] or 0) > now
                if fresh and (not force or waited):
                    conn.execute("UPDATE crawl_requests SET hits=hits+1 WHERE key=?", (key,))
                    self._log(conn, key, "deduplicated" if waited else "cache_hit", 0, now, finished=now)
                    return CrawlResult(key, row["query"], row["status"], db.loads(row["response"], {}), cached=not waited,
                                       deduplicated=waited, fetched_at=row["updated_at"], raw_record_id=row["raw_record_id"])
                busy = row is not None and row["status"] == "in_flight" and (row["lease_expires_at"] or 0) > now
                if not busy:
                    conn.execute(
                        "INSERT INTO crawl_requests(key, query, status, created_at, updated_at, lease_expires_at) "
                        "VALUES (?,?, 'in_flight', ?, ?, ?) ON CONFLICT(key) DO UPDATE SET status='in_flight', "
                        "query=excluded.query, updated_at=excluded.updated_at, lease_expires_at=excluded.lease_expires_at",
                        (key, query, now, now, now + self.lease_seconds))
                    return None
            if not wait or now >= deadline:
                raise CrawlerError("in_progress", "an identical crawl is already running; retry shortly",
                                   retry_after=max(1.0, (row["lease_expires_at"] or now) - now))
            waited = True
            self.sleep(self.poll_interval)

    def _claim_attempt(self, conn, key: str, attempt: int) -> int:
        now = self.clock()
        with db.transaction(conn, immediate=True):
            day_count = conn.execute(
                f"SELECT COUNT(*) FROM provider_usage WHERE provider=? AND operation='scrape' AND started_at>=? AND {COUNTED}",
                (PROVIDER, now - 86400)).fetchone()[0]
            if day_count >= self.cfg.crawler_daily_budget:
                self._log(conn, key, "rejected", attempt, now, finished=now, error="daily budget exhausted")
                raise CrawlerError("budget_exhausted",
                                   f"daily crawler budget of {self.cfg.crawler_daily_budget} upstream calls is used up",
                                   retry_after=3600.0)
            window = [r[0] for r in conn.execute(
                f"SELECT started_at FROM provider_usage WHERE provider=? AND operation='scrape' AND started_at>=? AND {COUNTED} "
                "ORDER BY started_at", (PROVIDER, now - 60))]
            if len(window) >= self.cfg.crawler_rpm:
                retry = max(1.0, window[0] + 60 - now)
                self._log(conn, key, "rejected", attempt, now, finished=now, error="rpm limit")
                raise CrawlerError("rate_limited", f"crawler limit of {self.cfg.crawler_rpm}/minute reached",
                                   retry_after=round(retry, 1))
            return self._log(conn, key, "in_flight", attempt, now)

    def _log(self, conn, key, status, attempt, started, *, finished=None, error=None) -> int:
        cur = conn.execute(
            "INSERT INTO provider_usage(provider, operation, request_key, started_at, finished_at, status, attempt, error) "
            "VALUES (?, 'scrape', ?, ?, ?, ?, ?, ?)", (PROVIDER, key, started, finished, status, attempt, error))
        return int(cur.lastrowid)

    def _finish_attempt(self, conn, usage_id, status, http_status, started, error=None, credits=None) -> None:
        now = self.clock()
        conn.execute(
            "UPDATE provider_usage SET finished_at=?, status=?, http_status=?, latency_ms=?, credits_estimate=?, error=? "
            "WHERE id=?",
            (now, status, http_status, int((now - started) * 1000),
             credits if credits is not None else CREDITS_UPPER_BOUND.get(status, 0),
             (error or "")[:300] or None, usage_id))

    def _fetch_with_retries(self, conn, key: str, query: str) -> tuple[str, dict, int]:
        url = self.base_url + SCRAPE_PATH
        last: CrawlerError | None = None
        for attempt in range(self.cfg.crawler_max_retries + 1):
            if attempt and last is not None:
                delay = last.retry_after if last.retry_after is not None else random.uniform(0, 2 ** attempt)
                self.sleep(min(delay, 30.0))
            try:
                usage_id = self._claim_attempt(conn, key, attempt)
            except CrawlerError:
                if last is not None:
                    raise last
                raise
            started = self.clock()
            try:
                resp = self.session.post(url, json={"query": query}, headers={"Content-Type": "application/json"},
                                         timeout=(self.cfg.crawler_connect_timeout, self.cfg.crawler_read_timeout))
            except requests.ConnectionError as exc:  # includes ConnectTimeout: the request never reached the crawler
                self._finish_attempt(conn, usage_id, "error", None, started, f"connection: {exc}")
                last = CrawlerError("unreachable", f"crawler unreachable at {self.base_url}: {type(exc).__name__}")
                continue
            except requests.Timeout:
                self._finish_attempt(conn, usage_id, "timeout", None, started, "read timeout")
                raise CrawlerError("timeout", f"crawler did not answer within {self.cfg.crawler_read_timeout:.0f}s; "
                                              "not retried because it may still be spending credits")
            try:
                body = resp.json()
            except ValueError:
                body = None
            if resp.status_code == 200:
                self._finish_attempt(conn, usage_id, "ok", 200, started, credits=reported_credits(body))
                return "ok", validate_response(body), 200
            if resp.status_code == 404:
                self._finish_attempt(conn, usage_id, "not_found", 404, started)
                return "not_found", body if isinstance(body, dict) else {}, 404
            message = (body or {}).get("error") if isinstance(body, dict) else (resp.text or "")[:200]
            self._finish_attempt(conn, usage_id, "error", resp.status_code, started, message)
            if resp.status_code in RETRYABLE_STATUS:
                last = CrawlerError("upstream", f"crawler HTTP {resp.status_code}: {message}", status=resp.status_code,
                                    retry_after=parse_retry_after(resp.headers.get("Retry-After")))
                continue
            code = "bad_request" if resp.status_code == 400 else "upstream"
            raise CrawlerError(code, f"crawler HTTP {resp.status_code}: {message}", status=resp.status_code)
        assert last is not None
        raise last

    def _finish_lease(self, conn, key, status, response, http_status, error=None) -> None:
        now = self.clock()
        conn.execute(
            "UPDATE crawl_requests SET status=?, response=?, http_status=?, error=?, updated_at=?, lease_expires_at=NULL, "
            "expires_at=? WHERE key=?",
            (status, json.dumps(response) if response is not None else None, http_status, error, now, now, key))

    def _store(self, conn, key, query, status, body, http_status) -> CrawlResult:
        now = self.clock()
        ttl = self.cfg.crawler_cache_ttl_seconds if status == "ok" else self.cfg.crawler_negative_ttl_seconds
        with db.transaction(conn):
            raw_id, _ = db.upsert_raw(conn, SOURCE, f"query:{key}", {"query": query, "status": status, "response": body},
                                      body.get("source_url") if isinstance(body, dict) else None, None)
            conn.execute(
                "UPDATE crawl_requests SET status=?, response=?, http_status=?, error=NULL, raw_record_id=?, updated_at=?, "
                "expires_at=?, lease_expires_at=NULL WHERE key=?",
                (status, json.dumps(body), http_status, raw_id, now, now + ttl, key))
        return CrawlResult(key, query, status, body, cached=False, deduplicated=False, fetched_at=now, raw_record_id=raw_id)


_client: CrawlerClient | None = None


def get_client() -> CrawlerClient:
    global _client
    if _client is None:
        _client = CrawlerClient()
    return _client


def set_client(client: CrawlerClient | None) -> None:
    global _client
    _client = client
