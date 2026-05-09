"""
ToolVault Meta-Tool — The tool that finds tools.

This is the ONE tool schema (~100 tokens) that goes into every model context.
Instead of loading all 46+ tool schemas, the model uses this single tool
to discover and retrieve the specific tools it needs.

Actions:
  search  — Find tools by natural language query ("send a text message")
  read    — Get full tool spec by exact name (returns JSON schema for execution)
  list    — List all tools, optionally filtered by category or tier
  mint    — Register a new tool (Tier 3, approval-gated — Phase 7)

Schema (canonical format, ~100 tokens):
  {
    "name": "toolvault",
    "description": "...",
    "parameters": { "action", "query", "tool_name", "category" }
  }

The schema can be converted to any provider format using the existing
format converters in tool_registry.py.
"""

import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Meta-Tool Schema (canonical, provider-agnostic format)
# ---------------------------------------------------------------------------

META_TOOL_SCHEMA = {
    "name": "toolvault",
    "description": (
        "Your tool discovery system. Use this to find and retrieve tools from the ToolVault. "
        "Actions: 'search' finds tools by what you need to do, 'read' gets the full spec "
        "for a specific tool, 'list' shows all available tools by category."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "read", "list"],
                "description": "search=find tools by query, read=get full tool spec by name, list=show all tools by category"
            },
            "query": {
                "type": "string",
                "description": "For search: natural language description of what you need (e.g., 'send a text message', 'generate an image')"
            },
            "tool_name": {
                "type": "string",
                "description": "For read: exact tool name to retrieve (e.g., 'send_sms', 'generate_image')"
            },
            "category": {
                "type": "string",
                "description": "For list: filter by category (e.g., 'communication', 'media_generation', 'email'). Omit for all."
            },
        },
        "required": ["action"],
    },
    # Tier 1: always loaded into every context
    "groups": ["chat", "chat_cu", "realtime", "gemini_live", "grok_live", "phone", "mcp"],
}


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

@dataclass
class MetaToolResult:
    """Result from a meta-tool action."""
    success: bool
    result: str
    data: Optional[Dict[str, Any]] = None


def execute(action: str, **params) -> MetaToolResult:
    """Execute a meta-tool action.

    This is the entry point that blackbox_tools.py will call when
    the model invokes the toolvault tool.

    Args:
        action: One of "search", "read", "list", "mint"
        **params: Action-specific parameters

    Returns:
        MetaToolResult with formatted result string and optional data.
    """
    Y = "\033[33m"
    R = "\033[0m"
    param_str = ", ".join(f"{k}={v!r}" for k, v in params.items() if v)
    print(f"{Y}[TOOLVAULT-TOOL] ▶ Model invoked: toolvault(action={action!r}, {param_str}){R}")

    if action == "search":
        result = _action_search(params.get("query", ""))
        if result.data and result.data.get("matches"):
            for m in result.data["matches"][:5]:
                print(f"{Y}[TOOLVAULT-TOOL]   ├─ {m['name']:30s} score={m['score']:.3f}{R}")
        return result
    elif action == "read":
        result = _action_read(params.get("tool_name", ""))
        print(f"{Y}[TOOLVAULT-TOOL]   └─ Read: {params.get('tool_name', '?')} → {'found' if result.success else 'not found'}{R}")
        return result
    elif action == "list":
        result = _action_list(params.get("category"))
        if result.data:
            print(f"{Y}[TOOLVAULT-TOOL]   └─ Listed: {result.data.get('total', 0)} tools in {len(result.data.get('categories', []))} categories{R}")
        return result
    elif action == "mint":
        return _action_mint(params)
    else:
        return MetaToolResult(
            success=False,
            result=f"Unknown action: '{action}'. Use: search, read, list"
        )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _action_search(query: str) -> MetaToolResult:
    """Search for tools by natural language query.

    Returns ranked results with name, score, category, and description.
    """
    if not query:
        return MetaToolResult(success=False, result="Missing 'query' parameter for search action.")

    from Orchestrator.toolvault import search_tools, read_tool

    results = search_tools(query, limit=8)

    if not results:
        return MetaToolResult(
            success=True,
            result=f"No tools found matching '{query}'. Try different keywords.",
        )

    # Build readable result
    lines = [f"Found {len(results)} tools matching '{query}':\n"]
    for rank, (name, score) in enumerate(results, 1):
        # Get description from vault
        tool = read_tool(name)
        desc = tool.get("DESCRIPTION", "") if tool else ""
        # Truncate long descriptions
        if len(desc) > 120:
            desc = desc[:117] + "..."
        category = tool.get("CATEGORY", "") if tool else ""

        lines.append(f"  {rank}. {name} (score: {score:.2f}, category: {category})")
        lines.append(f"     {desc}")
        lines.append("")

    lines.append("Use toolvault(action='read', tool_name='...') to get the full spec for any tool.")

    return MetaToolResult(
        success=True,
        result="\n".join(lines),
        data={"matches": [{"name": n, "score": s} for n, s in results]},
    )


def _action_read(tool_name: str) -> MetaToolResult:
    """Read a tool's full specification by name.

    Returns the complete tool block including parameters and JSON schema.
    This is what the model needs to actually use the tool.
    """
    if not tool_name:
        return MetaToolResult(success=False, result="Missing 'tool_name' parameter for read action.")

    from Orchestrator.toolvault import read_tool
    from Orchestrator.toolvault.manifest import get_tool

    parsed = read_tool(tool_name)
    if not parsed:
        return MetaToolResult(
            success=False,
            result=f"Tool '{tool_name}' not found in vault. Use toolvault(action='search', query='...') to find tools.",
        )

    # Build the response with everything the model needs
    entry = get_tool(tool_name)
    tier = entry.get("tier", 2) if entry else 2

    lines = [
        f"=== Tool: {parsed['NAME']} ===",
        f"Description: {parsed.get('DESCRIPTION', '')}",
        f"Category: {parsed.get('CATEGORY', '')}",
        f"Tier: {tier}",
        "",
        "Parameters:",
    ]

    # Include human-readable parameters
    param_text = parsed.get("PARAMETERS", "")
    if param_text:
        for line in param_text.split("\n"):
            lines.append(f"  {line.strip()}")

    if parsed.get("RETURNS"):
        lines.append(f"\nReturns: {parsed['RETURNS']}")
    if parsed.get("EXAMPLE"):
        lines.append(f"Example: {parsed['EXAMPLE']}")
    if parsed.get("NOTES"):
        lines.append(f"Notes: {parsed['NOTES']}")

    # Include the JSON schema (machine-readable, for format conversion)
    schema = parsed.get("JSON_SCHEMA")
    if schema:
        lines.append(f"\nJSON Schema: {json.dumps(schema)}")

    return MetaToolResult(
        success=True,
        result="\n".join(lines),
        data={
            "name": tool_name,
            "schema": schema,
            "groups": parsed.get("GROUPS", []),
            "tier": tier,
        },
    )


def _action_list(category: Optional[str] = None) -> MetaToolResult:
    """List all tools, optionally filtered by category.

    Returns a categorized summary with tool names and brief descriptions.
    """
    from Orchestrator.toolvault import list_all_tools, read_tool

    tools = list_all_tools()

    if category:
        tools = [t for t in tools if t.get("category") == category]

    if not tools:
        if category:
            return MetaToolResult(
                success=True,
                result=f"No tools in category '{category}'. Use toolvault(action='list') to see all categories.",
            )
        return MetaToolResult(success=True, result="No tools in vault.")

    # Group by category
    by_category: Dict[str, List[Dict]] = {}
    for t in tools:
        cat = t.get("category", "uncategorized")
        by_category.setdefault(cat, []).append(t)

    lines = [f"ToolVault: {len(tools)} tools across {len(by_category)} categories\n"]

    for cat in sorted(by_category.keys()):
        cat_tools = by_category[cat]
        tier_label = {1: "T1", 2: "T2", 3: "T3"}
        lines.append(f"[{cat}] ({len(cat_tools)} tools)")
        for t in cat_tools:
            tier = tier_label.get(t.get("tier", 2), "T2")
            lines.append(f"  - {t['name']} [{tier}]")
        lines.append("")

    lines.append("Use toolvault(action='read', tool_name='...') to get full details for any tool.")

    return MetaToolResult(
        success=True,
        result="\n".join(lines),
        data={"total": len(tools), "categories": list(by_category.keys())},
    )


def _action_mint(params: Dict) -> MetaToolResult:
    """Mint a new tool into the vault (Tier 3, approval-gated).

    Phase 7 feature — stubbed for now.
    """
    return MetaToolResult(
        success=False,
        result="Tool minting is not yet available. This will be enabled in a future update with human-in-the-loop approval.",
    )


# ---------------------------------------------------------------------------
# Schema helpers (for registration in tool_registry.py)
# ---------------------------------------------------------------------------

def get_meta_tool_schema() -> Dict[str, Any]:
    """Return the meta-tool in canonical (provider-agnostic) format.

    This can be passed directly to the format converters in tool_registry.py:
      to_anthropic(get_meta_tool_schema())
      to_openai_rest(get_meta_tool_schema())
      etc.
    """
    return META_TOOL_SCHEMA.copy()


def get_meta_tool_token_estimate() -> int:
    """Estimate token count for the meta-tool schema.

    Rough estimate: ~4 chars per token for JSON.
    """
    schema_json = json.dumps(META_TOOL_SCHEMA)
    return len(schema_json) // 4
