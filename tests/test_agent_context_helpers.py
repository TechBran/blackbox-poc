"""Pure-function tests for Orchestrator.agent_context helpers.

These cover the shared shape-massaging and composition logic extracted
from the three agent transports (Claude WS, Gemini WS, Gemini CU SSE).
No retrieval, no WebSocket, no SSE — just data in, data out.
"""
from Orchestrator.agent_context import (
    FOSSIL_FENCE_CLOSE,
    FOSSIL_FENCE_OPEN,
    append_fossils_to_system,
    compose_with_fossils,
    empty_provenance,
    normalize_provenance,
)


def test_empty_provenance_has_four_keys():
    p = empty_provenance()
    assert set(p) == {"recent", "keyword", "semantic", "checkpoint"}
    assert all(p[k] == [] for k in p)


def test_normalize_fills_missing_keys():
    p = normalize_provenance({"recent": ["SNAP-1"]})
    assert p["keyword"] == []
    assert p["semantic"] == []
    assert p["checkpoint"] == []
    assert p["recent"] == ["SNAP-1"]


def test_compose_with_fossils_empty_returns_prompt_unchanged():
    assert compose_with_fossils("", "hello") == "hello"


def test_compose_with_fossils_adds_fences():
    out = compose_with_fossils("FOSSIL", "HELLO")
    assert FOSSIL_FENCE_OPEN in out
    assert FOSSIL_FENCE_CLOSE in out
    assert "HELLO" in out
    assert out.index(FOSSIL_FENCE_OPEN) < out.index("HELLO")


def test_append_fossils_to_system_empty_returns_system_unchanged():
    assert append_fossils_to_system("SYS", "") == "SYS"


def test_append_fossils_to_system_wraps_fossil_block():
    out = append_fossils_to_system("SYS", "FOSSIL")
    assert "SYS" in out
    assert FOSSIL_FENCE_OPEN in out
    assert out.index("SYS") < out.index(FOSSIL_FENCE_OPEN)
