"""
ToolVault Manifest - Byte-offset index with embedded vectors.

The manifest maps tool names to their byte positions in the volume file,
along with embeddings, metadata, and tier classification.

Structure:
{
  "tools": {
    "web_search": {
      "number": 1,
      "byte_start": 0,
      "byte_end": 1234,
      "category": "web",
      "groups": ["chat", "realtime", ...],
      "tier": 2,
      "embedding": [float, float, ...],   # 3072-dim vector
      "minted_at": "2026-03-28T21:50:00Z"
    },
    ...
  },
  "meta": {
    "total_tools": 41,
    "last_number": 41,
    "last_updated": "2026-03-28T21:50:00Z",
    "volume_size_bytes": 12345
  }
}

Mirrors the caching pattern from Orchestrator/fossils.py:load_snapshot_index().
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

import Orchestrator.toolvault.config as _config
from Orchestrator.toolvault.config import DEFAULT_TIER


# ---------------------------------------------------------------------------
# In-memory cache (avoids re-parsing on every access)
# ---------------------------------------------------------------------------
_cache: Optional[Dict[str, Any]] = None
_cache_mtime: float = 0.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_manifest() -> Dict[str, Any]:
    """Return a fresh empty manifest structure."""
    return {
        "tools": {},
        "meta": {
            "total_tools": 0,
            "last_number": 0,
            "last_updated": _now_utc(),
            "volume_size_bytes": 0,
        },
    }


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def load_manifest() -> Dict[str, Any]:
    """Load the manifest from disk with in-memory caching.

    Cache invalidates when file mtime changes (same pattern as
    fossils.py:load_snapshot_index).
    """
    global _cache, _cache_mtime

    if not _config.MANIFEST_PATH.exists():
        return _empty_manifest()

    try:
        current_mtime = _config.MANIFEST_PATH.stat().st_mtime

        # Return cached version if file hasn't changed
        if _cache is not None and current_mtime == _cache_mtime:
            return _cache

        with open(_config.MANIFEST_PATH, "r") as f:
            data = json.load(f)

        _cache = data
        _cache_mtime = current_mtime
        return data

    except (json.JSONDecodeError, OSError) as e:
        print(f"[TOOLVAULT] Failed to load manifest: {e}")
        return _empty_manifest()


def save_manifest(manifest: Dict[str, Any]) -> None:
    """Atomically write manifest to disk.

    Uses .part temp file + os.replace for crash safety
    (same pattern as volume.py:atomic_write).
    """
    global _cache, _cache_mtime

    _config.TOOLVAULT_DIR.mkdir(parents=True, exist_ok=True)

    # Update meta
    manifest["meta"]["total_tools"] = len(manifest.get("tools", {}))
    manifest["meta"]["last_updated"] = _now_utc()

    tmp = _config.MANIFEST_PATH.with_suffix(".json.part")
    try:
        data = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
        tmp.write_bytes(data)
        os.replace(tmp, _config.MANIFEST_PATH)

        # Update cache
        _cache = manifest
        _cache_mtime = _config.MANIFEST_PATH.stat().st_mtime

    except Exception as e:
        print(f"[TOOLVAULT] Failed to save manifest: {e}")
        if tmp.exists():
            tmp.unlink()
        raise


def invalidate_cache() -> None:
    """Force cache invalidation (useful after external modifications)."""
    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = 0.0


# ---------------------------------------------------------------------------
# Tool CRUD
# ---------------------------------------------------------------------------

def register_tool(
    name: str,
    number: int,
    byte_start: int,
    byte_end: int,
    category: str,
    groups: List[str],
    embedding: Optional[List[float]] = None,
    tier: int = DEFAULT_TIER,
    volume_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Register a tool in the manifest after minting into the volume.

    Args:
        name: Canonical tool name
        number: Tool number (for anchor pairing)
        byte_start: Start byte in volume
        byte_end: End byte in volume
        category: Tool category
        groups: Consumer groups
        embedding: 3072-dim embedding vector (can be added later)
        tier: Access tier (1=always, 2=semantic, 3=approval)
        volume_size: Current volume file size in bytes

    Returns:
        The tool entry that was created.
    """
    manifest = load_manifest()

    entry = {
        "number": number,
        "byte_start": byte_start,
        "byte_end": byte_end,
        "category": category,
        "groups": groups,
        "tier": tier,
        "embedding": embedding,
        "minted_at": _now_utc(),
    }

    manifest["tools"][name] = entry

    # Track highest number for auto-increment
    if number > manifest["meta"]["last_number"]:
        manifest["meta"]["last_number"] = number

    if volume_size is not None:
        manifest["meta"]["volume_size_bytes"] = volume_size

    save_manifest(manifest)
    return entry


def get_tool(name: str) -> Optional[Dict[str, Any]]:
    """Get a single tool entry by name. Returns None if not found."""
    manifest = load_manifest()
    return manifest.get("tools", {}).get(name)


def update_tool_embedding(name: str, embedding: List[float]) -> bool:
    """Update the embedding vector for an existing tool.

    Returns True if updated, False if tool not found.
    """
    manifest = load_manifest()
    if name not in manifest.get("tools", {}):
        return False

    manifest["tools"][name]["embedding"] = embedding
    save_manifest(manifest)
    return True


def list_tools(category: Optional[str] = None, tier: Optional[int] = None) -> List[Dict[str, Any]]:
    """List all tools, optionally filtered by category or tier.

    Returns list of dicts with 'name' added to each entry.
    """
    manifest = load_manifest()
    results = []

    for name, entry in manifest.get("tools", {}).items():
        if category and entry.get("category") != category:
            continue
        if tier is not None and entry.get("tier") != tier:
            continue

        result = {"name": name}
        result.update(entry)
        results.append(result)

    return results


def get_next_number() -> int:
    """Get the next auto-incrementing tool number."""
    manifest = load_manifest()
    return manifest["meta"].get("last_number", 0) + 1


def get_tools_with_embeddings() -> Dict[str, Dict[str, Any]]:
    """Get all tools that have embeddings (for search).

    Returns {name: entry} dict, only for tools with non-null embeddings.
    """
    manifest = load_manifest()
    return {
        name: entry
        for name, entry in manifest.get("tools", {}).items()
        if entry.get("embedding")
    }


def get_manifest_stats() -> Dict[str, Any]:
    """Get manifest statistics."""
    manifest = load_manifest()
    tools = manifest.get("tools", {})

    # Count by tier
    tier_counts = {}
    for entry in tools.values():
        t = entry.get("tier", DEFAULT_TIER)
        tier_counts[t] = tier_counts.get(t, 0) + 1

    # Count by category
    category_counts = {}
    for entry in tools.values():
        c = entry.get("category", "uncategorized")
        category_counts[c] = category_counts.get(c, 0) + 1

    # Count embedded vs not
    embedded = sum(1 for e in tools.values() if e.get("embedding"))

    return {
        "total_tools": len(tools),
        "embedded": embedded,
        "unembedded": len(tools) - embedded,
        "by_tier": tier_counts,
        "by_category": category_counts,
        "last_number": manifest["meta"].get("last_number", 0),
        "last_updated": manifest["meta"].get("last_updated", ""),
        "volume_size_bytes": manifest["meta"].get("volume_size_bytes", 0),
    }
