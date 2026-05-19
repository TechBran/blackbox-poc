"""TTL cache for /models/{provider} responses.

Per-provider in-memory cache with two TTLs:
  - SUCCESS_TTL_S (600s = 10min)  — caches live-fetched results
  - FAILURE_TTL_S (30s)            — caches the fallback response after a
                                     live-fetch failure, so we don't DDoS
                                     a struggling upstream API on every
                                     request during an outage

In-memory only — survives uvicorn worker lifetime, NOT a service restart.
That's intentional: a service restart implies the operator may have changed
config (API keys, env vars), and freshly re-querying upstream on first
request is the safe default.

TODO (BYOK era — Brandon's docs/onboarding/discovery-notes.md):
  Once per-customer API keys land, the cache key must be (provider, customer_id)
  not just provider — otherwise customer A's live-fetch results would be
  served to customer B if they hit the same uvicorn worker.
"""
from __future__ import annotations

import time
from typing import Any, Callable


SUCCESS_TTL_S = 600.0  # 10 minutes
FAILURE_TTL_S = 30.0   # 30 seconds (negative-cache)


_cache: dict[str, dict] = {}  # provider -> {"value": dict, "ts": float, "source": str}


def get_cached_or_fetch(
    provider: str,
    fetcher: Callable[[], dict | None],
    fallback: Callable[[], dict],
) -> dict:
    """Return the cached value for `provider` if fresh; otherwise call `fetcher`.

    Args:
        provider: cache key
        fetcher: callable returning the live response dict, or None/raise on failure
        fallback: callable returning a static fallback dict (always succeeds)

    Returns:
        Whatever fetcher or fallback returned. The returned dict gets a "cached"
        key set to True if served from cache (False if just fetched), purely
        for observability.

    Behavior:
        - Cache HIT (fresh) -> return cached, no fetcher call
        - Cache MISS, fetcher succeeds -> cache result for SUCCESS_TTL_S
        - Cache MISS, fetcher fails (returns None or raises) -> cache fallback
          for FAILURE_TTL_S so we don't retry every request during outage
    """
    now = time.time()
    entry = _cache.get(provider)
    if entry:
        ttl = SUCCESS_TTL_S if entry["source"] == "live" else FAILURE_TTL_S
        if now - entry["ts"] < ttl:
            result = dict(entry["value"])  # shallow copy so caller mutation is safe
            result["cached"] = True
            return result

    # Try live fetch
    try:
        live_result = fetcher()
    except Exception:
        live_result = None

    if live_result is not None:
        _cache[provider] = {"value": live_result, "ts": now, "source": "live"}
        result = dict(live_result)
        result["cached"] = False
        return result

    # Fallback path
    fb = fallback()
    _cache[provider] = {"value": fb, "ts": now, "source": "fallback"}
    result = dict(fb)
    result["cached"] = False
    return result


def invalidate(provider: str | None = None) -> None:
    """Clear the cache for a specific provider, or all providers if None.

    Used by tests and (eventually) by an admin /models/refresh endpoint to
    force a fresh upstream fetch on next request.
    """
    if provider is None:
        _cache.clear()
    else:
        _cache.pop(provider, None)


def cache_state() -> dict[str, dict]:
    """Read-only snapshot of cache state for diagnostics."""
    now = time.time()
    return {
        provider: {
            "source": entry["source"],
            "age_s": int(now - entry["ts"]),
            "ttl_s": int(SUCCESS_TTL_S if entry["source"] == "live" else FAILURE_TTL_S),
        }
        for provider, entry in _cache.items()
    }
