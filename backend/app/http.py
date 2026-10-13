"""Shared HTTP client for connectors.

Timeouts on every request, bounded retries for network errors / 429 / 5xx with
exponential backoff + full jitter, Retry-After support, and a minimum interval
between requests (simple per-source RPM limiter). Streams large downloads to
disk with checksum verification.
"""
from __future__ import annotations

import email.utils
import hashlib
import logging
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests

from .settings import settings

log = logging.getLogger(__name__)
RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status = status
        self.url = url


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


class HttpClient:
    def __init__(
        self,
        rpm: int | None = None,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        backoff_base: float = 1.0,
        backoff_cap: float = 30.0,
        retry_after_cap: float = 120.0,
        session: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        user_agent: str | None = None,
    ):
        self.min_interval = 60.0 / rpm if rpm else 0.0
        self.timeout = timeout if timeout is not None else settings.http_timeout
        self.max_retries = settings.http_max_retries if max_retries is None else max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.retry_after_cap = retry_after_cap
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.headers = {"User-Agent": user_agent or settings.user_agent, "Accept": "application/json"}
        self._last_request = float("-inf")
        self._lock = threading.Lock()
        self.stats = {"requests": 0, "retries": 0, "errors": 0}

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_request + self.min_interval - self.clock()
            if wait > 0:
                self.sleep(wait)
            self._last_request = self.clock()

    def _backoff(self, attempt: int) -> float:
        return random.uniform(0, min(self.backoff_cap, self.backoff_base * (2 ** attempt)))

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        stream: bool = False,
        allow_404: bool = False,
        headers: dict | None = None,
    ) -> Any:
        attempt = 0
        merged = {**self.headers, **(headers or {})}
        while True:
            self._throttle()
            self.stats["requests"] += 1
            try:
                resp = self.session.request(method, url, params=params, headers=merged, timeout=self.timeout, stream=stream)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= self.max_retries:
                    self.stats["errors"] += 1
                    raise HttpError(f"network error after {attempt + 1} attempt(s): {type(exc).__name__}", url=url) from exc
                delay = self._backoff(attempt)
            else:
                if resp.status_code == 404 and allow_404:
                    return None
                if resp.status_code < 400:
                    return resp
                if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                    retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                    delay = min(retry_after if retry_after is not None else self._backoff(attempt), self.retry_after_cap)
                    resp.close()
                else:
                    self.stats["errors"] += 1
                    snippet = "" if stream else (resp.text or "")[:300]
                    raise HttpError(f"HTTP {resp.status_code} for {url}: {snippet}", status=resp.status_code, url=url)
            attempt += 1
            self.stats["retries"] += 1
            log.warning("retrying %s %s in %.1fs (attempt %d/%d)", method, url, delay, attempt, self.max_retries)
            self.sleep(delay)

    def get_json(self, url: str, *, params: dict | None = None, allow_404: bool = False) -> Any:
        resp = self.request("GET", url, params=params, allow_404=allow_404)
        if resp is None:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise HttpError(f"invalid JSON from {url}", status=resp.status_code, url=url) from exc

    def download(self, url: str, dest: Path, *, expected_sha1: str | None = None, chunk_size: int = 1 << 20) -> Path:
        """Stream a file to disk (never fully in memory); verify sha1 when the source publishes one."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        resp = self.request("GET", url, stream=True)
        digest = hashlib.sha1()
        try:
            with open(tmp, "wb") as fh:
                for block in resp.iter_content(chunk_size):
                    if block:
                        fh.write(block)
                        digest.update(block)
        finally:
            resp.close()
        if expected_sha1 and digest.hexdigest() != expected_sha1:
            tmp.unlink(missing_ok=True)
            raise HttpError(f"checksum mismatch for {url}", url=url)
        tmp.replace(dest)
        return dest


def sha1_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()
