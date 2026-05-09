"""
ToolVault Dynamic Injector — Two-Pass semantic tool injection.

On every user prompt, this module:
  1. Collects Tier 1 tools (always in context) + the meta-tool
  2. Embeds the prompt and searches ToolVault for matching Tier 2 tools
  3. Reads matched tool schemas from the vault via byte-offset
  4. Converts to the target provider format (Anthropic/OpenAI/Gemini/Grok/MCP)
  5. Returns a ready-to-use tools array for the API payload

This replaces the static tools=get_all_tools() pattern with dynamic,
prompt-aware injection. The model only sees tools relevant to THIS turn.

Token economics:
  Old: ~7,450 tokens (all 46 schemas, every request)
  New: ~800-1,500 tokens (meta-tool + Tier 1 + 5-8 relevant tools)
"""

import copy
import json
from typing import Dict, Any, List, Optional, Tuple

from Orchestrator.toolvault.meta_tool import META_TOOL_SCHEMA
from Orchestrator.toolvault.manifest import (
    load_manifest,
    get_tools_with_embeddings,
)
from Orchestrator.toolvault.volume import read_tool_by_offset, parse_tool_block
from Orchestrator.toolvault.embeddings import (
    semantic_search,
    keyword_search,
    hybrid_search,
)
from Orchestrator.toolvault.config import (
    TIER_1,
    TIER_2,
    TIER_3,
    KEYWORD_WEIGHT,
    SEMANTIC_WEIGHT,
    SIMILARITY_THRESHOLD,
)

# Reuse the proven format converters from tool_registry.py
from Orchestrator.tools.tool_registry import (
    _clean_params,
    _strip_for_gemini,
)


# ---------------------------------------------------------------------------
# Provider format registry
# ---------------------------------------------------------------------------

# Maps provider name → (per-tool converter, is_batch)
# is_batch=True means the converter takes a list and wraps it
PROVIDER_FORMATS = {
    "anthropic": "anthropic",
    "openai": "openai_rest",
    "openai_rest": "openai_rest",
    "openai_realtime": "openai_realtime",
    "gemini": "gemini_rest",
    "gemini_rest": "gemini_rest",
    "gemini_live": "gemini_live",
    "grok": "openai_rest",       # Grok REST uses OpenAI format
    "grok_live": "openai_realtime",  # Grok Live uses OpenAI Realtime format
    "mcp": "mcp",
}

# Provider → default consumer group
PROVIDER_DEFAULT_GROUP = {
    "anthropic": "chat",
    "openai": "chat",
    "openai_rest": "chat",
    "openai_realtime": "realtime",
    "gemini": "chat",
    "gemini_rest": "chat",
    "gemini_live": "gemini_live",
    "grok": "chat",
    "grok_live": "grok_live",
    "mcp": "mcp",
}


# ---------------------------------------------------------------------------
# Format converters (mirror tool_registry.py exactly)
# ---------------------------------------------------------------------------

def _to_anthropic(tool: Dict) -> Dict:
    return {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": _clean_params(tool["parameters"]),
    }


def _to_openai_rest(tool: Dict) -> Dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": _clean_params(tool["parameters"]),
        },
    }


def _to_openai_realtime(tool: Dict) -> Dict:
    return {
        "type": "function",
        "name": tool["name"],
        "description": tool["description"],
        "parameters": _clean_params(tool["parameters"]),
    }


def _to_gemini_decl(tool: Dict) -> Dict:
    """Single tool → Gemini function declaration (used inside the wrapper)."""
    return {
        "name": tool["name"],
        "description": tool["description"],
        "parameters": _strip_for_gemini(tool["parameters"]),
    }


def _format_tools(canonical_tools: List[Dict], provider_format: str) -> list:
    """Convert a list of canonical tool dicts to the target provider format.

    Args:
        canonical_tools: List of {name, description, parameters} dicts
        provider_format: One of the format keys

    Returns:
        Provider-formatted tools array, ready for API payload.
    """
    if provider_format == "anthropic":
        return [_to_anthropic(t) for t in canonical_tools]

    elif provider_format == "openai_rest":
        return [_to_openai_rest(t) for t in canonical_tools]

    elif provider_format == "openai_realtime":
        return [_to_openai_realtime(t) for t in canonical_tools]

    elif provider_format == "gemini_rest":
        # Gemini wraps all tools in one function_declarations array
        return [{"function_declarations": [_to_gemini_decl(t) for t in canonical_tools]}]

    elif provider_format == "gemini_live":
        # Gemini Live uses camelCase key
        return [{"functionDeclarations": [_to_gemini_decl(t) for t in canonical_tools]}]

    elif provider_format == "mcp":
        # MCP uses Tool objects — lazy import to avoid dependency
        try:
            from mcp.types import Tool
            return [
                Tool(
                    name=t["name"],
                    description=t["description"],
                    inputSchema=_clean_params(t["parameters"]),
                )
                for t in canonical_tools
            ]
        except ImportError:
            # Fallback: return canonical format if mcp not available
            return canonical_tools

    else:
        # Unknown format — return canonical
        print(f"[TOOLVAULT-INJECT] Unknown format '{provider_format}', returning canonical")
        return canonical_tools


# ---------------------------------------------------------------------------
# Vault → Canonical conversion
# ---------------------------------------------------------------------------

def _vault_entry_to_canonical(name: str, entry: Dict) -> Optional[Dict]:
    """Read a tool from the vault and convert to canonical format.

    Canonical format: {name, description, parameters}
    This is what the format converters expect.
    """
    block_text = read_tool_by_offset(entry["byte_start"], entry["byte_end"])
    if not block_text:
        return None

    parsed = parse_tool_block(block_text)
    schema = parsed.get("JSON_SCHEMA")
    if not schema or not isinstance(schema, dict):
        return None

    return {
        "name": parsed.get("NAME", name),
        "description": parsed.get("DESCRIPTION", ""),
        "parameters": schema,
    }


# ---------------------------------------------------------------------------
# Core injection function
# ---------------------------------------------------------------------------

def get_tools_for_prompt(
    prompt: str,
    provider: str,
    group: Optional[str] = None,
    max_semantic_tools: int = 8,
    include_meta_tool: bool = True,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
) -> list:
    """Dynamically select and format tools for a specific user prompt.

    This is the primary entry point that replaces static tool arrays.
    Instead of tools=get_all_tools(), use tools=get_tools_for_prompt(prompt, provider).

    Args:
        prompt: The user's message text
        provider: Target provider ("anthropic", "openai", "gemini", "grok", etc.)
        group: Consumer group filter (default: inferred from provider)
        max_semantic_tools: Maximum Tier 2 tools to inject (default: 8)
        include_meta_tool: Always include the toolvault meta-tool (default: True)
        similarity_threshold: Minimum score for semantic matches (default: 0.5)

    Returns:
        Provider-formatted tools array, ready for the API payload.
    """
    # Resolve provider format and default group
    provider_format = PROVIDER_FORMATS.get(provider, "openai_rest")
    if group is None:
        group = PROVIDER_DEFAULT_GROUP.get(provider, "chat")

    # Load manifest
    manifest = load_manifest()
    all_tools = manifest.get("tools", {})

    # Track which tools we've selected (avoid duplicates)
    selected: Dict[str, Dict] = {}  # name → canonical dict

    # --- Step 1: Always include meta-tool ---
    if include_meta_tool:
        selected["toolvault"] = {
            "name": META_TOOL_SCHEMA["name"],
            "description": META_TOOL_SCHEMA["description"],
            "parameters": META_TOOL_SCHEMA["parameters"],
        }

    # --- Step 2: Always include Tier 1 tools (if in this group) ---
    for name, entry in all_tools.items():
        if entry.get("tier") == TIER_1:
            if group in entry.get("groups", []):
                canonical = _vault_entry_to_canonical(name, entry)
                if canonical and name not in selected:
                    selected[name] = canonical

    # --- Step 3: Semantic search for Tier 2 tools ---
    if prompt.strip():
        # Get tools with embeddings for search
        tools_with_emb = get_tools_with_embeddings()

        # Filter to Tier 2 tools in this group
        searchable = {
            name: entry for name, entry in tools_with_emb.items()
            if entry.get("tier") == TIER_2
            and group in entry.get("groups", [])
            and name not in selected  # Skip already-selected
        }

        if searchable:
            # Build description map for keyword search
            descriptions = {}
            for name, entry in searchable.items():
                block = read_tool_by_offset(entry["byte_start"], entry["byte_end"])
                if block:
                    parsed = parse_tool_block(block)
                    descriptions[name] = parsed.get("DESCRIPTION", name)
                else:
                    descriptions[name] = name

            # Hybrid search
            results = hybrid_search(
                query=prompt,
                tools_with_embeddings=searchable,
                tool_descriptions=descriptions,
                limit=max_semantic_tools,
                threshold=similarity_threshold,
            )

            # Add matched tools
            for name, score in results:
                if name not in selected:
                    entry = all_tools.get(name)
                    if entry:
                        canonical = _vault_entry_to_canonical(name, entry)
                        if canonical:
                            selected[name] = canonical

    # --- Full UGV expansion: any ugv_* match → inject ALL ugv_* tools ---
    if any(n.startswith("ugv_") for n in selected):
        all_ugv_names = [
            name for name, entry in all_tools.items()
            if name.startswith("ugv_") and group in entry.get("groups", [])
        ]
        missing_ugv = [n for n in all_ugv_names if n not in selected]
        for name in missing_ugv:
            entry = all_tools.get(name)
            if entry:
                canonical = _vault_entry_to_canonical(name, entry)
                if canonical:
                    selected[name] = canonical
        if missing_ugv:
            print(f"[TOOLVAULT-INJECT] ugv_* expansion: added {len(missing_ugv)} more ugv tools (full robot API)")

    # --- Step 4: Convert to provider format ---
    canonical_list = list(selected.values())

    formatted = _format_tools(canonical_list, provider_format)

    # Log injection summary
    Y = "\033[33m"
    R = "\033[0m"
    tier1_count = sum(1 for n in selected if all_tools.get(n, {}).get("tier") == TIER_1)
    tier2_count = sum(1 for n in selected if all_tools.get(n, {}).get("tier") == TIER_2)
    meta_count = 1 if "toolvault" in selected else 0
    prompt_preview = prompt[:80].replace('\n', ' ') if prompt else "(empty)"
    print(f"{Y}[TOOLVAULT-INJECT] ═══════════════════════════════════════════{R}")
    print(f"{Y}[TOOLVAULT-INJECT] Provider: {provider}/{group} → {provider_format}{R}")
    print(f"{Y}[TOOLVAULT-INJECT] Prompt: \"{prompt_preview}\"{R}")
    print(f"{Y}[TOOLVAULT-INJECT] Injected: {len(selected)} tools ({meta_count} meta + {tier1_count} T1 + {tier2_count} T2){R}")
    for name in selected:
        tier = all_tools.get(name, {}).get("tier", "?")
        tier_label = {1: "T1-always", 2: "T2-semantic", 3: "T3-internal"}.get(tier, "meta")
        print(f"{Y}[TOOLVAULT-INJECT]   ├─ {name:30s} [{tier_label}]{R}")
    print(f"{Y}[TOOLVAULT-INJECT] ═══════════════════════════════════════════{R}")

    return formatted


def get_injected_tool_names(
    prompt: str,
    group: str = "chat",
    max_semantic_tools: int = 8,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
) -> List[Tuple[str, str]]:
    """Preview which tools would be injected (without format conversion).

    Returns list of (tool_name, reason) tuples where reason is
    "meta", "tier1", or "semantic(score)".

    Useful for debugging and testing injection logic.
    """
    manifest = load_manifest()
    all_tools = manifest.get("tools", {})
    results = [("toolvault", "meta")]

    # Tier 1
    for name, entry in all_tools.items():
        if entry.get("tier") == TIER_1 and group in entry.get("groups", []):
            results.append((name, "tier1"))

    # Semantic search
    if prompt.strip():
        tools_with_emb = get_tools_with_embeddings()
        already = {r[0] for r in results}

        searchable = {
            name: entry for name, entry in tools_with_emb.items()
            if entry.get("tier") == TIER_2
            and group in entry.get("groups", [])
            and name not in already
        }

        if searchable:
            descriptions = {}
            for name, entry in searchable.items():
                block = read_tool_by_offset(entry["byte_start"], entry["byte_end"])
                if block:
                    parsed = parse_tool_block(block)
                    descriptions[name] = parsed.get("DESCRIPTION", name)
                else:
                    descriptions[name] = name

            matches = hybrid_search(
                query=prompt,
                tools_with_embeddings=searchable,
                tool_descriptions=descriptions,
                limit=max_semantic_tools,
                threshold=similarity_threshold,
            )

            for name, score in matches:
                results.append((name, f"semantic({score:.2f})"))

    return results


# ---------------------------------------------------------------------------
# Dynamic System Prompt — Tool Instructions
# ---------------------------------------------------------------------------

def build_tool_instructions(tool_names: List[str]) -> str:
    """Generate the human-readable tool instructions for the system prompt.

    Takes the list of tool names (from the same search that populates the
    tools=[] array) and generates a formatted section describing each tool's
    purpose, parameters, usage, and notes.

    This replaces hardcoded tool descriptions in system prompts. When a
    new tool is minted into the vault, it automatically appears here.

    Args:
        tool_names: List of tool names to generate instructions for.
                    Typically the same set returned by get_tools_for_prompt().

    Returns:
        Formatted string for injection into the system prompt.
    """
    from Orchestrator.toolvault.manifest import load_manifest

    if not tool_names:
        return ""

    manifest = load_manifest()
    all_tools = manifest.get("tools", {})

    sections = []

    for name in tool_names:
        # Skip meta-tool — it has its own fixed description
        if name == "toolvault":
            continue

        entry = all_tools.get(name)
        if not entry:
            continue

        block_text = read_tool_by_offset(entry["byte_start"], entry["byte_end"])
        if not block_text:
            continue

        parsed = parse_tool_block(block_text)
        desc = parsed.get("DESCRIPTION", "")
        params = parsed.get("PARAMETERS", "")
        example = parsed.get("EXAMPLE", "")
        notes = parsed.get("NOTES", "")
        category = parsed.get("CATEGORY", "")

        # Build the instruction block for this tool
        lines = [f"  Tool: {name}"]
        if desc:
            lines.append(f"  Description: {desc}")
        if params:
            lines.append(f"  Parameters:")
            for param_line in params.strip().split("\n"):
                lines.append(f"    {param_line.strip()}")
        if example:
            lines.append(f"  Example: {example}")
        if notes:
            lines.append(f"  Notes: {notes}")

        sections.append("\n".join(lines))

    if not sections:
        return ""

    header = (
        "AVAILABLE TOOLS:\n"
        "You have access to the following tools. Call them by name with the required parameters.\n"
        "Use the toolvault tool to discover additional tools not listed here.\n"
    )

    return header + "\n\n".join(sections) + "\n"


def inject_for_prompt(
    prompt: str,
    provider: str,
    group: Optional[str] = None,
    max_semantic_tools: int = 8,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
) -> Tuple[list, str]:
    """Single search pass that returns BOTH tool schemas AND system prompt instructions.

    This is the efficient entry point — one embedding, one search, two outputs:
      1. Tool schemas (formatted for the provider's tools=[] array)
      2. Tool instructions (human-readable text for the system prompt)

    Args:
        prompt: User message text
        provider: Target provider
        group: Consumer group (default: inferred from provider)
        max_semantic_tools: Max Tier 2 tools to inject
        similarity_threshold: Minimum score for semantic matches

    Returns:
        Tuple of (formatted_tools_array, tool_instructions_text)
    """
    # Resolve provider format and default group
    provider_format = PROVIDER_FORMATS.get(provider, "openai_rest")
    if group is None:
        group = PROVIDER_DEFAULT_GROUP.get(provider, "chat")

    manifest = load_manifest()
    all_tools = manifest.get("tools", {})

    # Track selected tools
    selected: Dict[str, Dict] = {}  # name → canonical dict
    selected_names: List[str] = []  # ordered list for instructions

    # --- Always include meta-tool ---
    selected["toolvault"] = {
        "name": META_TOOL_SCHEMA["name"],
        "description": META_TOOL_SCHEMA["description"],
        "parameters": META_TOOL_SCHEMA["parameters"],
    }
    selected_names.append("toolvault")

    # --- Tier 1 tools ---
    for name, entry in all_tools.items():
        if entry.get("tier") == TIER_1 and group in entry.get("groups", []):
            canonical = _vault_entry_to_canonical(name, entry)
            if canonical and name not in selected:
                selected[name] = canonical
                selected_names.append(name)

    # --- Semantic search for Tier 2 ---
    if prompt.strip():
        tools_with_emb = get_tools_with_embeddings()
        searchable = {
            name: entry for name, entry in tools_with_emb.items()
            if entry.get("tier") == TIER_2
            and group in entry.get("groups", [])
            and name not in selected
        }

        if searchable:
            descriptions = {}
            for name, entry in searchable.items():
                block = read_tool_by_offset(entry["byte_start"], entry["byte_end"])
                if block:
                    parsed = parse_tool_block(block)
                    descriptions[name] = parsed.get("DESCRIPTION", name)
                else:
                    descriptions[name] = name

            results = hybrid_search(
                query=prompt,
                tools_with_embeddings=searchable,
                tool_descriptions=descriptions,
                limit=max_semantic_tools,
                threshold=similarity_threshold,
            )

            for name, score in results:
                if name not in selected:
                    entry = all_tools.get(name)
                    if entry:
                        canonical = _vault_entry_to_canonical(name, entry)
                        if canonical:
                            selected[name] = canonical
                            selected_names.append(name)

    # --- Full UGV expansion: robot context = full robot capability ---
    # If any ugv_* tool matched via semantic search, pull ALL ugv_* tools
    # from the manifest. Partial tool injection breaks robot tool-use flows
    # (model can't compose multi-step actions when half the API is hidden).
    if any(n.startswith("ugv_") for n in selected):
        all_ugv_names = [
            name for name, entry in all_tools.items()
            if name.startswith("ugv_") and group in entry.get("groups", [])
        ]
        missing_ugv = [n for n in all_ugv_names if n not in selected]
        for name in missing_ugv:
            entry = all_tools.get(name)
            if entry:
                canonical = _vault_entry_to_canonical(name, entry)
                if canonical:
                    selected[name] = canonical
                    selected_names.append(name)
        if missing_ugv:
            Y_ = "\033[33m"
            R_ = "\033[0m"
            print(f"{Y_}[TOOLVAULT-INJECT] ugv_* expansion: added {len(missing_ugv)} more ugv tools (full robot API){R_}")

    # --- Output 1: Format tool schemas for API payload ---
    canonical_list = list(selected.values())
    formatted_tools = _format_tools(canonical_list, provider_format)

    # --- Output 2: Generate system prompt instructions ---
    tool_instructions = build_tool_instructions(selected_names)

    Y = "\033[33m"
    R = "\033[0m"
    tier1_count = sum(1 for n in selected if all_tools.get(n, {}).get("tier") == TIER_1)
    tier2_count = sum(1 for n in selected if all_tools.get(n, {}).get("tier") == TIER_2)
    print(f"{Y}[TOOLVAULT-INJECT] {provider}/{group}: "
          f"{len(selected)} tools + system prompt instructions "
          f"({tier1_count} T1 + {tier2_count} T2){R}")

    return formatted_tools, tool_instructions
