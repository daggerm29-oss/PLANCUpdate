"""Central AI request manager: the ONLY code path allowed to call a model provider.

Every limit is enforced from the shared SQLite ledger, so the API server, the CLI and background jobs that use
the same database share ONE budget:
  * RPM: at most AI_RPM_LIMIT request starts in any (60 + AI_RPM_SAFETY_MARGIN_SECONDS) second window
  * concurrency: at most AI_MAX_CONCURRENT_REQUESTS in flight (leases expire if a process dies)
  * daily request / token budgets; AI_BACKGROUND_RESERVE requests are kept for interactive questions
  * a global cooldown after HTTP 429 (Retry-After or AI_429_COOLDOWN_SECONDS, whichever is longer)
  * a response cache (TTL) keyed on model + task + prompt, where the prompt embeds the evidence, so changed
    evidence produces a new key; identical concurrent requests are de-duplicated
Slots are claimed inside BEGIN IMMEDIATE, which serialises claims across threads and processes.
"""
from __future__ import annotations

import hashlib
import heapq
import itertools
import os
import random
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from typing import Callable

from .. import db
from ..settings import Settings, settings as default_settings
from .providers import BaseProvider, ProviderError, get_provider

INTERACTIVE = 0
BACKGROUND = 10
WINDOW_SECONDS = 60.0
DAY_SECONDS = 86400.0
CACHE_VERSION = "v1"


class AIUnavailable(Exception):
    """reason: disabled | not_configured | auth | cooldown | rate_limited | rate_window | budget_exhausted | deferred |
    queue_full | queue_timeout | timeout | network | server | bad_request | invalid_response | provider_error"""

    def __init__(self, reason: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.reason = reason
        self.retry_after = None if retry_after is None else round(max(0.0, retry_after), 1)


@dataclass
class AIResult:
    text: str
    model: str
    cache_key: str
    cached: bool = False
    deduplicated: bool = False
    attempts: int = 0
    usage: dict | None = None
    request_ids: list[int] = field(default_factory=list)


class _SharedResult(Exception):
    def __init__(self, hit: dict):
        super().__init__("served by an identical request from another process")
        self.hit = hit


def _state_get(conn: sqlite3.Connection, key: str) -> float:
    row = conn.execute("SELECT value FROM ai_state WHERE key=?", (key,)).fetchone()
    try:
        return float(row[0]) if row else 0.0
    except (TypeError, ValueError):
        return 0.0


def _state_set(conn: sqlite3.Connection, key: str, value: float) -> None:
    conn.execute(
        "INSERT INTO ai_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, repr(float(value))),
    )


def _status_for(exc: ProviderError) -> str:
    return {"rate_limited": "rate_limited", "timeout": "timeout"}.get(exc.kind, "error")


class AIManager:
    def __init__(
        self,
        cfg: Settings | None = None,
        provider: BaseProvider | None = None,
        *,
        db_path: str | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.25,
        max_sleep: float = 1.0,
        process_id: str | None = None,
    ):
        self.cfg = cfg or default_settings
        self.provider = provider or get_provider(self.cfg)
        self.db_path = str(db_path or self.cfg.db_path)
        self.clock = clock
        self.sleep = sleep
        self.poll = poll_interval
        self.max_sleep = max_sleep
        self.process_id = process_id or f"{os.getpid()}:{id(self):x}"
        self._heap: list[tuple[int, int]] = []
        self._seq = itertools.count()
        self._heap_lock = threading.Lock()
        self._inflight: dict[str, tuple[threading.Event, dict]] = {}
        self._inflight_lock = threading.Lock()
        with self._connect() as conn:
            db.init_db(conn)

    def _connect(self):
        return closing(db.connect(self.db_path))

    # ------------------------------------------------------------------ public API

    def cache_key(self, task: str, system: str, prompt: str, fingerprint: str = "") -> str:
        payload = db.dumps({"v": CACHE_VERSION, "model": self.provider.model, "task": task, "system": system,
                            "prompt": prompt, "fingerprint": fingerprint})
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def complete(self, task: str, system: str, prompt: str, *, fingerprint: str = "", priority: int = INTERACTIVE,
                 use_cache: bool = True) -> AIResult:
        key = self.cache_key(task, system, prompt, fingerprint)
        if use_cache:
            hit = self._cache_get(key)
            if hit is not None:
                self.bump("cache_hits")
                return AIResult(text=hit["text"], model=hit["model"], cache_key=key, cached=True, usage=hit["usage"])
            self.bump("cache_misses")
        with self._inflight_lock:
            entry = self._inflight.get(key)
            owner = entry is None
            if owner:
                entry = (threading.Event(), {})
                self._inflight[key] = entry
        event, outcome = entry
        if not owner:
            self.bump("dedup_hits")
            event.wait(self.cfg.ai_queue_max_wait_seconds + self.cfg.ai_request_timeout * (2 + self.cfg.ai_max_retries))
            if "result" in outcome:
                res: AIResult = outcome["result"]
                return AIResult(text=res.text, model=res.model, cache_key=key, cached=res.cached, deduplicated=True,
                                usage=res.usage)
            err = outcome.get("error")
            if isinstance(err, AIUnavailable):
                raise AIUnavailable(err.reason, str(err), err.retry_after)
            raise AIUnavailable("provider_error", "an identical in-flight request did not complete")
        try:
            result = self._execute(key, task, system, prompt, fingerprint, priority)
            outcome["result"] = result
            return result
        except BaseException as exc:
            outcome["error"] = exc
            raise
        finally:
            with self._inflight_lock:
                self._inflight.pop(key, None)
            event.set()

    def bump(self, name: str, amount: int = 1) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ai_counters(name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = value + excluded.value",
                (name, amount),
            )

    def invalidate_cache(self, task: str | None = None, fingerprint: str | None = None) -> int:
        clauses, params = [], []
        if task:
            clauses.append("task=?")
            params.append(task)
        if fingerprint:
            clauses.append("fingerprint=?")
            params.append(fingerprint)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            return conn.execute(f"DELETE FROM ai_cache{where}", params).rowcount

    def purge_expired(self) -> int:
        with self._connect() as conn:
            return conn.execute("DELETE FROM ai_cache WHERE expires_at <= ?", (self.clock(),)).rowcount

    def status(self, recent: int = 15) -> dict:
        cfg, now = self.cfg, self.clock()
        rpm = max(1, cfg.ai_rpm_limit)
        window = WINDOW_SECONDS + max(0.0, cfg.ai_rpm_safety_margin_seconds)
        day_start = now - (now % DAY_SECONDS)
        with self._connect() as conn:
            today = dict(conn.execute(
                "SELECT COUNT(*) AS requests, COALESCE(SUM(status='ok'), 0) AS ok, "
                "COALESCE(SUM(status='rate_limited'), 0) AS rate_limited, "
                "COALESCE(SUM(status IN ('error', 'timeout', 'abandoned')), 0) AS failed, "
                "COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens, "
                "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
                "CAST(AVG(CASE WHEN status='ok' THEN latency_ms END) AS INTEGER) AS avg_latency_ms "
                "FROM ai_request_log WHERE started_at >= ?",
                (day_start,),
            ).fetchone())
            starts = [r[0] for r in conn.execute(
                "SELECT started_at FROM ai_request_log WHERE started_at > ? ORDER BY started_at DESC LIMIT ?",
                (now - window, rpm),
            )]
            last_minute = conn.execute(
                "SELECT COUNT(*) FROM ai_request_log WHERE started_at > ?", (now - WINDOW_SECONDS,)
            ).fetchone()[0]
            in_flight = conn.execute(
                "SELECT COUNT(*) FROM ai_request_log WHERE status='in_flight' AND lease_expires_at >= ?", (now,)
            ).fetchone()[0]
            cooldown = _state_get(conn, "cooldown_until")
            counters = {r["name"]: r["value"] for r in conn.execute("SELECT name, value FROM ai_counters")}
            entries = conn.execute("SELECT COUNT(*) FROM ai_cache WHERE expires_at > ?", (now,)).fetchone()[0]
            log_rows = db.rows(conn.execute(
                "SELECT id, started_at, finished_at, status, task, model, priority, attempt, http_status, error_kind, "
                "prompt_chars, input_tokens, output_tokens, total_tokens, latency_ms FROM ai_request_log "
                "ORDER BY id DESC LIMIT ?",
                (recent,),
            ))
        next_slot = max(0.0, min(starts) + window - now) if len(starts) >= rpm else 0.0
        if cooldown > now:
            next_slot = max(next_slot, cooldown - now)
        hits, misses = counters.get("cache_hits", 0), counters.get("cache_misses", 0)
        return {
            "provider": self.provider.status(),
            "limits": {
                "rpm_limit": cfg.ai_rpm_limit,
                "rpm_safety_margin_seconds": cfg.ai_rpm_safety_margin_seconds,
                "max_concurrent_requests": cfg.ai_max_concurrent_requests,
                "max_retries": cfg.ai_max_retries,
                "request_timeout_seconds": cfg.ai_request_timeout,
                "cache_ttl_seconds": cfg.ai_cache_ttl_seconds,
                "daily_request_budget": cfg.ai_daily_request_budget,
                "daily_token_budget": cfg.ai_daily_token_budget,
                "queue_max": cfg.ai_queue_max,
                "queue_max_wait_seconds": cfg.ai_queue_max_wait_seconds,
                "background_reserve": cfg.ai_background_reserve,
                "cooldown_after_429_seconds": cfg.ai_429_cooldown_seconds,
            },
            "today": {**today, "remaining_requests": max(0, cfg.ai_daily_request_budget - today["requests"])},
            "window": {"seconds": window, "requests_last_60s": last_minute, "next_slot_in_seconds": round(next_slot, 1)},
            "in_flight": in_flight,
            "queue": {"waiting_in_this_process": len(self._heap), "max": cfg.ai_queue_max},
            "cooldown": {"active": cooldown > now, "remaining_seconds": round(max(0.0, cooldown - now), 1)},
            "cache": {"entries": entries, "hits": hits, "misses": misses,
                      "hit_rate": round(hits / (hits + misses), 3) if hits + misses else None},
            "counters": counters,
            "recent_requests": log_rows,
            "server_time": now,
        }

    # ------------------------------------------------------------------ internals

    def _execute(self, key: str, task: str, system: str, prompt: str, fingerprint: str, priority: int) -> AIResult:
        provider_status = self.provider.status()
        if not provider_status.get("available"):
            reason = "disabled" if self.provider.name == "disabled" else "not_configured"
            raise AIUnavailable(reason, provider_status.get("reason") or "AI provider unavailable")
        attempts = 1 + max(0, self.cfg.ai_max_retries)
        ids: list[int] = []
        for attempt in range(attempts):
            if attempt:
                self.sleep(self._backoff(attempt))
            try:
                row_id = self._acquire(task, priority, key, len(system) + len(prompt), attempt)
            except _SharedResult as shared:
                self.bump("dedup_hits")
                return AIResult(text=shared.hit["text"], model=shared.hit["model"], cache_key=key, cached=True,
                                deduplicated=True, usage=shared.hit["usage"], request_ids=ids)
            ids.append(row_id)
            started = time.monotonic()
            try:
                resp = self.provider.complete(system, prompt, self.cfg.ai_request_timeout)
                if not (resp.text or "").strip():
                    raise ProviderError("invalid_response", "the model returned no text")
            except ProviderError as exc:
                self._finish(row_id, _status_for(exc), started, http_status=exc.http_status, error_kind=exc.kind)
                if exc.kind == "rate_limited":
                    until = self._set_cooldown(exc.retry_after)
                    raise AIUnavailable("rate_limited", "the provider returned HTTP 429; AI is paused until the "
                                        "cooldown ends", until - self.clock()) from exc
                if exc.retryable and attempt + 1 < attempts:
                    self.bump("retries")
                    continue
                raise AIUnavailable(exc.kind, str(exc)) from exc
            except Exception as exc:
                self._finish(row_id, "error", started, error_kind=type(exc).__name__)
                raise AIUnavailable("provider_error", f"{type(exc).__name__} raised by the provider") from exc
            self._finish(row_id, "ok", started, http_status=resp.http_status, usage=resp.usage)
            self._cache_put(key, task, fingerprint, resp.text, resp.usage, resp.model)
            return AIResult(text=resp.text, model=resp.model or self.provider.model, cache_key=key,
                            attempts=attempt + 1, usage=resp.usage, request_ids=ids)
        raise AIUnavailable("provider_error", "no attempts left")

    def _backoff(self, attempt: int) -> float:
        return random.uniform(0.0, min(30.0, 2.0 * (2 ** (attempt - 1))))

    def _acquire(self, task: str, priority: int, key: str, prompt_chars: int, attempt: int) -> int:
        ticket = (priority, next(self._seq))
        with self._heap_lock:
            if len(self._heap) >= max(1, self.cfg.ai_queue_max):
                self.bump("queue_rejections")
                raise AIUnavailable("queue_full", f"the AI queue is full ({self.cfg.ai_queue_max} waiting)")
            heapq.heappush(self._heap, ticket)
        deadline = self.clock() + self.cfg.ai_queue_max_wait_seconds
        try:
            while True:
                with self._heap_lock:
                    at_head = self._heap[0] == ticket
                if at_head:
                    row_id, wait, reason, fatal = self._try_claim(task, priority, key, prompt_chars, attempt)
                    if fatal is not None:
                        self.bump(fatal.reason)
                        raise fatal
                    if row_id is not None:
                        return row_id
                    if reason == "duplicate_in_flight":
                        hit = self._cache_get(key)
                        if hit is not None:
                            raise _SharedResult(hit)
                else:
                    wait, reason = self.poll, "queued"
                remaining = deadline - self.clock()
                if remaining <= 0 or (reason in ("cooldown", "rate_window") and wait > remaining):
                    self.bump("queue_timeouts")
                    if reason == "cooldown":
                        raise AIUnavailable("cooldown", "AI is cooling down after a rate-limit response", wait)
                    if reason == "rate_window":
                        raise AIUnavailable("rate_window", f"RPM limit reached ({self.cfg.ai_rpm_limit}/min); "
                                            f"next slot in {wait:.0f}s", wait)
                    raise AIUnavailable("queue_timeout", "timed out waiting for an AI slot", wait)
                self.sleep(max(0.0, min(wait, remaining, self.max_sleep)))
        finally:
            with self._heap_lock:
                if ticket in self._heap:
                    self._heap.remove(ticket)
                    heapq.heapify(self._heap)

    def _try_claim(self, task: str, priority: int, key: str, prompt_chars: int, attempt: int):
        with self._connect() as conn:
            with db.transaction(conn, immediate=True):
                return self._claim_locked(conn, task, priority, key, prompt_chars, attempt)

    def _claim_locked(self, conn, task, priority, key, prompt_chars, attempt):
        cfg, now = self.cfg, self.clock()
        conn.execute(
            "UPDATE ai_request_log SET status='abandoned', finished_at=?, error_kind='lease_expired', "
            "lease_expires_at=NULL WHERE status='in_flight' AND lease_expires_at < ?",
            (now, now),
        )
        cooldown = _state_get(conn, "cooldown_until")
        if cooldown > now:
            if priority >= BACKGROUND:
                return None, 0.0, "deferred", AIUnavailable(
                    "deferred", "background AI work deferred during the rate-limit cooldown", cooldown - now)
            return None, cooldown - now, "cooldown", None
        day_start = now - (now % DAY_SECONDS)
        used, tokens = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(total_tokens), 0) FROM ai_request_log WHERE started_at >= ?", (day_start,)
        ).fetchone()
        reset_in = day_start + DAY_SECONDS - now
        if used >= cfg.ai_daily_request_budget:
            return None, 0.0, "budget", AIUnavailable(
                "budget_exhausted", f"daily AI request budget used ({used}/{cfg.ai_daily_request_budget})", reset_in)
        if cfg.ai_daily_token_budget > 0 and tokens >= cfg.ai_daily_token_budget:
            return None, 0.0, "budget", AIUnavailable(
                "budget_exhausted", f"daily AI token budget used ({tokens}/{cfg.ai_daily_token_budget})", reset_in)
        if priority >= BACKGROUND:
            if cfg.ai_daily_request_budget - used <= cfg.ai_background_reserve:
                return None, 0.0, "deferred", AIUnavailable(
                    "deferred", "background AI work deferred: the remaining daily budget is reserved for "
                    "interactive questions", reset_in)
            if _state_get(conn, "interactive_waiting_until") > now:
                return None, self.poll, "interactive_waiting", None
        if conn.execute(
            "SELECT 1 FROM ai_request_log WHERE status='in_flight' AND cache_key=? AND process_id != ? LIMIT 1",
            (key, self.process_id),
        ).fetchone():
            return None, self.poll, "duplicate_in_flight", None
        wait, reason = 0.0, None
        in_flight = conn.execute("SELECT COUNT(*) FROM ai_request_log WHERE status='in_flight'").fetchone()[0]
        if in_flight >= max(1, cfg.ai_max_concurrent_requests):
            wait, reason = self.poll, "concurrency"
        else:
            rpm = max(1, cfg.ai_rpm_limit)
            window = WINDOW_SECONDS + max(0.0, cfg.ai_rpm_safety_margin_seconds)
            recent = [r[0] for r in conn.execute(
                "SELECT started_at FROM ai_request_log WHERE started_at > ? ORDER BY started_at DESC LIMIT ?",
                (now - window, rpm),
            )]
            if len(recent) >= rpm:
                wait, reason = max(0.01, min(recent) + window - now), "rate_window"
        if reason:
            if priority < BACKGROUND:
                _state_set(conn, "interactive_waiting_until", now + 4 * self.poll)
            return None, wait, reason, None
        cur = conn.execute(
            "INSERT INTO ai_request_log(started_at, lease_expires_at, status, task, model, priority, attempt, "
            "cache_key, prompt_chars, process_id) VALUES (?, ?, 'in_flight', ?, ?, ?, ?, ?, ?, ?)",
            (now, now + cfg.ai_request_timeout + 30, task, self.provider.model, priority, attempt, key, prompt_chars,
             self.process_id),
        )
        return int(cur.lastrowid), 0.0, "ok", None

    def _finish(self, row_id: int, status: str, started: float, *, http_status: int | None = None,
                error_kind: str | None = None, usage: dict | None = None) -> None:
        usage = usage or {}
        with self._connect() as conn:
            conn.execute(
                "UPDATE ai_request_log SET finished_at=?, status=?, lease_expires_at=NULL, http_status=?, error_kind=?, "
                "input_tokens=?, output_tokens=?, total_tokens=?, latency_ms=? WHERE id=?",
                (self.clock(), status, http_status, error_kind, usage.get("input_tokens"), usage.get("output_tokens"),
                 usage.get("total_tokens"), int((time.monotonic() - started) * 1000), row_id),
            )

    def _set_cooldown(self, retry_after: float | None) -> float:
        now = self.clock()
        until = now + max(float(retry_after or 0.0), self.cfg.ai_429_cooldown_seconds)
        with self._connect() as conn:
            with db.transaction(conn, immediate=True):
                until = max(until, _state_get(conn, "cooldown_until"))
                _state_set(conn, "cooldown_until", until)
                _state_set(conn, "last_rate_limited_at", now)
        self.bump("rate_limited")
        return until

    def _cache_get(self, key: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT response, usage, model FROM ai_cache WHERE key=? AND expires_at > ?", (key, self.clock())
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE ai_cache SET hits = hits + 1 WHERE key=?", (key,))
            return {"text": row["response"], "usage": db.loads(row["usage"]), "model": row["model"]}

    def _cache_put(self, key: str, task: str, fingerprint: str, text: str, usage: dict | None, model: str | None) -> None:
        now = self.clock()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ai_cache(key, task, model, fingerprint, response, usage, created_at, expires_at, hits) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0) ON CONFLICT(key) DO UPDATE SET response=excluded.response, "
                "usage=excluded.usage, created_at=excluded.created_at, expires_at=excluded.expires_at",
                (key, task, model or self.provider.model, fingerprint, text, db.dumps(usage) if usage else None, now,
                 now + max(0, self.cfg.ai_cache_ttl_seconds)),
            )


_MANAGER: AIManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> AIManager:
    """One manager per process so the in-process queue and de-duplication are shared by every caller."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = AIManager()
        return _MANAGER


def set_manager(manager: AIManager | None) -> None:
    global _MANAGER
    with _MANAGER_LOCK:
        _MANAGER = manager
