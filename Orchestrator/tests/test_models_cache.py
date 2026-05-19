"""Tests for Orchestrator/utils/models_cache.py.

Covers the two TTL pathways (positive 10min, negative 30s) + the
invalidate helpers. No HTTP — pure cache behavior with mock fetchers.
"""
import time

import pytest

from Orchestrator.utils import models_cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Reset the module-level cache between tests so they're isolated."""
    models_cache.invalidate()
    yield
    models_cache.invalidate()


def test_success_caches_and_returns_live():
    fetched = {"call_count": 0}
    def fetcher():
        fetched["call_count"] += 1
        return {"provider": "test", "models": [{"id": "m1"}], "source": "live"}
    def fallback():
        return {"provider": "test", "models": [], "source": "fallback"}

    r1 = models_cache.get_cached_or_fetch("test", fetcher, fallback)
    assert r1["source"] == "live"
    assert r1["cached"] is False
    assert fetched["call_count"] == 1

    # Second call within TTL should be cached, no new fetcher call.
    r2 = models_cache.get_cached_or_fetch("test", fetcher, fallback)
    assert r2["source"] == "live"
    assert r2["cached"] is True
    assert fetched["call_count"] == 1  # fetcher NOT called again


def test_fetcher_failure_uses_fallback_and_negative_caches():
    fetched = {"call_count": 0}
    def failing_fetcher():
        fetched["call_count"] += 1
        raise RuntimeError("upstream down")
    def fallback():
        return {"provider": "test", "models": [{"id": "fb"}], "source": "fallback"}

    r1 = models_cache.get_cached_or_fetch("test", failing_fetcher, fallback)
    assert r1["source"] == "fallback"
    assert r1["models"] == [{"id": "fb"}]
    assert fetched["call_count"] == 1

    # Next call within negative-cache window: returns cached fallback,
    # does NOT retry the failing fetcher (audit M5: don't DDoS during outage)
    r2 = models_cache.get_cached_or_fetch("test", failing_fetcher, fallback)
    assert r2["source"] == "fallback"
    assert r2["cached"] is True
    assert fetched["call_count"] == 1  # still 1 — no retry


def test_fetcher_returns_none_treated_as_failure():
    """A fetcher that returns None (e.g., missing API key) gets fallback,
    same as one that raises."""
    def none_fetcher():
        return None
    def fallback():
        return {"provider": "test", "models": [{"id": "fb"}], "source": "fallback"}

    r = models_cache.get_cached_or_fetch("test", none_fetcher, fallback)
    assert r["source"] == "fallback"


def test_invalidate_provider_clears_only_that_key():
    def fetcher_a():
        return {"provider": "a", "models": [], "source": "live"}
    def fetcher_b():
        return {"provider": "b", "models": [], "source": "live"}
    def fallback():
        return {"provider": "x", "models": [], "source": "fallback"}

    models_cache.get_cached_or_fetch("a", fetcher_a, fallback)
    models_cache.get_cached_or_fetch("b", fetcher_b, fallback)
    state = models_cache.cache_state()
    assert "a" in state and "b" in state

    models_cache.invalidate("a")
    state = models_cache.cache_state()
    assert "a" not in state
    assert "b" in state


def test_invalidate_all_clears_everything():
    def fetcher():
        return {"provider": "x", "models": [], "source": "live"}
    models_cache.get_cached_or_fetch("a", fetcher, lambda: {"source": "fallback"})
    models_cache.get_cached_or_fetch("b", fetcher, lambda: {"source": "fallback"})
    assert len(models_cache.cache_state()) == 2

    models_cache.invalidate()
    assert len(models_cache.cache_state()) == 0


def test_cache_state_includes_age_and_source(monkeypatch):
    def fetcher():
        return {"provider": "test", "models": [], "source": "live"}

    # Pin time so we can assert age
    fake_now = [1000.0]
    monkeypatch.setattr(models_cache.time, "time", lambda: fake_now[0])
    models_cache.get_cached_or_fetch("test", fetcher, lambda: {})
    fake_now[0] = 1042.0  # advance 42 seconds
    state = models_cache.cache_state()
    assert state["test"]["age_s"] == 42
    assert state["test"]["source"] == "live"
    assert state["test"]["ttl_s"] == int(models_cache.SUCCESS_TTL_S)


def test_negative_cache_ttl_shorter_than_success(monkeypatch):
    """Verifies the audit M5 fix: failures cache 30s, successes cache 10min."""
    fake_now = [1000.0]
    monkeypatch.setattr(models_cache.time, "time", lambda: fake_now[0])
    call_count = [0]
    def failing_fetcher():
        call_count[0] += 1
        return None
    def fallback():
        return {"provider": "test", "models": [], "source": "fallback"}

    models_cache.get_cached_or_fetch("test", failing_fetcher, fallback)
    assert call_count[0] == 1

    # Still within negative TTL — cached, no retry
    fake_now[0] = 1025.0  # 25s later — still within 30s
    models_cache.get_cached_or_fetch("test", failing_fetcher, fallback)
    assert call_count[0] == 1

    # Past negative TTL — retry
    fake_now[0] = 1035.0  # 35s — past 30s
    models_cache.get_cached_or_fetch("test", failing_fetcher, fallback)
    assert call_count[0] == 2
