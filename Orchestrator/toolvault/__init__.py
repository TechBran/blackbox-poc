"""
ToolVault - Immutable, byte-offset indexed tool definitions with semantic search.

Architecture:
  Volume (append-only file)  →  Manifest (byte-offset index)  →  Embeddings (3072-dim vectors)
                                                                        ↓
  User prompt  →  Embed query  →  Cosine similarity  →  Retrieve relevant tools  →  Inject into context

Public API:
  mint_tool()           - Register a new tool into the vault (full pipeline)
  search_tools()        - Find tools by natural language query
  read_tool()           - Read a tool block by name (byte-offset seek)
  list_all_tools()      - List all registered tools
  get_vault_stats()     - Get vault statistics
  rebuild_index()       - Rebuild manifest from volume (recovery)
"""

from typing import Dict, Any, Optional, List, Tuple

import Orchestrator.toolvault.config as _config
from Orchestrator.toolvault.config import (
    TIER_1,
    TIER_2,
    TIER_3,
    DEFAULT_TIER,
)
from Orchestrator.toolvault.volume import (
    format_tool_block,
    append_tool_block,
    read_tool_by_offset,
    parse_tool_block,
    rebuild_volume_index,
)
from Orchestrator.toolvault.manifest import (
    load_manifest,
    save_manifest,
    register_tool,
    get_tool,
    update_tool_embedding,
    list_tools,
    get_next_number,
    get_tools_with_embeddings,
    get_manifest_stats,
    invalidate_cache,
)
from Orchestrator.toolvault.embeddings import (
    embed_tool_description,
    embed_query,
    semantic_search,
    keyword_search,
    hybrid_search,
    cosine_similarity,
)


# ---------------------------------------------------------------------------
# High-Level API
# ---------------------------------------------------------------------------

def mint_tool(
    name: str,
    description: str,
    category: str,
    groups: list,
    parameters: dict,
    returns: str = "",
    example: str = "",
    notes: str = "",
    tier: int = DEFAULT_TIER,
    generate_embedding: bool = True,
) -> Dict[str, Any]:
    """Mint a new tool into the ToolVault (full pipeline).

    This is the primary entry point for adding tools. It:
    1. Assigns the next auto-incrementing number
    2. Formats the tool block
    3. Appends to the volume file (immutable)
    4. Records byte offsets in the manifest
    5. Generates and stores the embedding (optional)
    6. Returns the complete tool entry

    Args:
        name: Canonical tool name (e.g., "web_search")
        description: Tool description (becomes the embedding target)
        category: Category (e.g., "web", "media_generation")
        groups: Consumer groups (e.g., ["chat", "realtime", "mcp"])
        parameters: Canonical JSON schema dict
        returns: Human-readable return description
        example: Example usage string
        notes: Edge cases, gotchas
        tier: Access tier (1=always, 2=semantic, 3=approval)
        generate_embedding: Whether to embed the description now

    Returns:
        Dict with: name, number, byte_start, byte_end, category,
        groups, tier, embedding, minted_at
    """
    number = get_next_number()

    # 1. Format the block
    block = format_tool_block(
        number=number,
        name=name,
        description=description,
        category=category,
        groups=groups,
        parameters=parameters,
        returns=returns,
        example=example,
        notes=notes,
    )

    # 2. Append to volume
    byte_start, byte_end = append_tool_block(block)

    # 3. Generate embedding
    embedding = None
    if generate_embedding:
        embedding = embed_tool_description(description)
        if embedding:
            print(f"[TOOLVAULT] Embedded '{name}' ({len(embedding)}-dim)")
        else:
            print(f"[TOOLVAULT] Warning: embedding failed for '{name}'")

    # 4. Register in manifest
    volume_size = _config.VOLUME_PATH.stat().st_size if _config.VOLUME_PATH.exists() else 0
    entry = register_tool(
        name=name,
        number=number,
        byte_start=byte_start,
        byte_end=byte_end,
        category=category,
        groups=groups,
        embedding=embedding,
        tier=tier,
        volume_size=volume_size,
    )

    print(f"[TOOLVAULT] Minted TOOL {number:03d} — {name} "
          f"[{byte_start}:{byte_end}] ({byte_end - byte_start} bytes)")

    return {"name": name, **entry}


def search_tools(query: str, limit: int = 10) -> List[Tuple[str, float]]:
    """Search the vault for tools matching a natural language query.

    Uses hybrid search (40% keyword + 60% semantic), the same
    approach proven on 1,600+ snapshots.

    Args:
        query: Natural language query (e.g., "send a text message")
        limit: Maximum results

    Returns:
        List of (tool_name, relevance_score) sorted by relevance.
    """
    tools = get_tools_with_embeddings()
    if not tools:
        print("[TOOLVAULT] No tools with embeddings in vault")
        return []

    # Build description map for keyword search
    # Read descriptions from volume blocks
    descriptions = {}
    for name, entry in tools.items():
        block_text = read_tool_by_offset(entry["byte_start"], entry["byte_end"])
        if block_text:
            parsed = parse_tool_block(block_text)
            descriptions[name] = parsed.get("DESCRIPTION", name)
        else:
            descriptions[name] = name

    return hybrid_search(query, tools, descriptions, limit=limit)


def read_tool(name: str) -> Optional[Dict[str, Any]]:
    """Read a tool's full block from the vault by name.

    O(1) retrieval via byte-offset seek.

    Returns parsed tool block dict with fields:
    NAME, DESCRIPTION, CATEGORY, GROUPS, PARAMETERS,
    RETURNS, EXAMPLE, NOTES, JSON_SCHEMA
    """
    entry = get_tool(name)
    if not entry:
        return None

    block_text = read_tool_by_offset(entry["byte_start"], entry["byte_end"])
    if not block_text:
        return None

    parsed = parse_tool_block(block_text)
    # Add manifest metadata
    parsed["_number"] = entry["number"]
    parsed["_byte_start"] = entry["byte_start"]
    parsed["_byte_end"] = entry["byte_end"]
    parsed["_tier"] = entry.get("tier", DEFAULT_TIER)
    parsed["_minted_at"] = entry.get("minted_at", "")

    return parsed


def list_all_tools() -> List[Dict[str, Any]]:
    """List all tools in the vault with metadata."""
    return list_tools()


def get_vault_stats() -> Dict[str, Any]:
    """Get vault statistics (tools, embeddings, categories, tiers)."""
    return get_manifest_stats()


def rebuild_index() -> Dict[str, Any]:
    """Rebuild the manifest from the volume file.

    Recovery path: if the manifest is lost or corrupted, this
    rescans the volume using byte-level regex to find all
    START/END anchors and rebuilds the index.

    Note: Embeddings will be lost and need to be regenerated.
    """
    tools = rebuild_volume_index()
    if not tools:
        print("[TOOLVAULT] No tools found in volume during rebuild")
        return {}

    manifest = {
        "tools": {},
        "meta": {
            "total_tools": len(tools),
            "last_number": max(t["number"] for t in tools.values()),
            "last_updated": "",
            "volume_size_bytes": _config.VOLUME_PATH.stat().st_size if _config.VOLUME_PATH.exists() else 0,
        },
    }

    for name, data in tools.items():
        manifest["tools"][name] = {
            "number": data["number"],
            "byte_start": data["byte_start"],
            "byte_end": data["byte_end"],
            "category": "",
            "groups": [],
            "tier": DEFAULT_TIER,
            "embedding": None,
            "minted_at": "",
        }

    save_manifest(manifest)
    invalidate_cache()

    print(f"[TOOLVAULT] Rebuilt index: {len(tools)} tools recovered")
    return manifest
