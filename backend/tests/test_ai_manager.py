"""Request-budget compliance: RPM window, cache, daily budget, 429 cooldown, background deferral,
and a budget shared by two manager instances (simulating two worker processes)."""
import pytest

from app.ai.manager import BACKGROUND, WINDOW_SECONDS, AIUnavailable
from app.ai.providers import FakeProvider, ProviderError


def _starts(mgr):
    return [r["started_at"] for r in reversed(mgr.status(recent=100)["recent_requests"])]


def test_rpm_limit_is_respected(make_manager):
    mgr = make_manager(ai_rpm_limit=2, ai_rpm_safety_margin_seconds=5, ai_queue_max_wait_seconds=200)
    for i in range(5):
        mgr.complete("t", "sys", f"prompt {i}")
    starts = _starts(mgr)
    assert len(starts) == 5
    window = WINDOW_SECONDS + 5
    for i in range(len(starts)):
        in_window = [s for s in starts if starts[i] <= s < starts[i] + window]
        assert len(in_window) <= 2, starts


def test_cache_and_no_second_call(make_manager):
    provider = FakeProvider()
    mgr = make_manager(provider)
    a = mgr.complete("t", "sys", "same prompt", fingerprint="f1")
    b = mgr.complete("t", "sys", "same prompt", fingerprint="f1")
    assert len(provider.calls) == 1 and b.cached and a.text == b.text
    mgr.complete("t", "sys", "same prompt", fingerprint="f2")  # evidence changed -> new key
    assert len(provider.calls) == 2


def test_daily_budget_exhaustion(make_manager):
    mgr = make_manager(ai_daily_request_budget=2, ai_rpm_limit=100)
    mgr.complete("t", "s", "1")
    mgr.complete("t", "s", "2")
    with pytest.raises(AIUnavailable) as exc:
        mgr.complete("t", "s", "3")
    assert exc.value.reason == "budget_exhausted"


def test_429_sets_cooldown_and_stops_calls(make_manager):
    err = ProviderError("rate_limited", "429", http_status=429, retry_after=90)
    provider = FakeProvider(responder=lambda s, p, n: err)
    mgr = make_manager(provider, ai_rpm_limit=100, ai_queue_max_wait_seconds=10)
    with pytest.raises(AIUnavailable) as exc:
        mgr.complete("t", "s", "a")
    assert exc.value.reason == "rate_limited"
    with pytest.raises(AIUnavailable) as exc2:
        mgr.complete("t", "s", "b")
    assert exc2.value.reason == "cooldown"
    assert len(provider.calls) == 1


def test_background_deferred_when_budget_reserved(make_manager):
    mgr = make_manager(ai_daily_request_budget=5, ai_background_reserve=5)
    with pytest.raises(AIUnavailable) as exc:
        mgr.complete("bg", "s", "x", priority=BACKGROUND)
    assert exc.value.reason == "deferred"


def test_budget_is_shared_across_instances(make_manager, db_path):
    first = make_manager(ai_daily_request_budget=2, ai_rpm_limit=100)
    second = make_manager(ai_daily_request_budget=2, ai_rpm_limit=100)
    second.fake_clock.now = first.fake_clock.now
    first.complete("t", "s", "1")
    second.complete("t", "s", "2")
    with pytest.raises(AIUnavailable):
        first.complete("t", "s", "3")


def test_max_one_concurrent(make_manager):
    import threading

    provider = FakeProvider(latency=0.05)
    mgr = make_manager(provider, ai_rpm_limit=100)
    mgr.clock = __import__("time").time
    mgr.sleep = __import__("time").sleep
    threads = [threading.Thread(target=mgr.complete, args=("t", "s", f"p{i}")) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert provider.max_active == 1 and len(provider.calls) == 3
