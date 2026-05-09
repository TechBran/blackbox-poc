"""
BlackBox Tools Package

Unified tool definitions and executors for all AI backends.
Tool definitions live in tool_registry.py (single source of truth).
"""

from .blackbox_tools import (
    BLACKBOX_TOOLS_ANTHROPIC,
    BLACKBOX_TOOLS_OPENAI,
    BLACKBOX_TOOLS_GEMINI,
    BlackBoxToolExecutor,
    ToolResult,
    get_tools_for_backend,
    execute_tool,
)

from .tool_registry import (
    TOOL_DEFINITIONS,
    get_tools_by_group,
    get_tool_by_name,
    resolve_alias,
    resolve_executor_name,
    get_anthropic_tools,
    get_openai_rest_tools,
    get_openai_realtime_tools,
    get_gemini_rest_tools,
    get_gemini_live_tools,
    get_mcp_tools,
    get_all_tool_names,
    get_group_tool_names,
)

__all__ = [
    # Legacy exports (from blackbox_tools.py)
    "BLACKBOX_TOOLS_ANTHROPIC",
    "BLACKBOX_TOOLS_OPENAI",
    "BLACKBOX_TOOLS_GEMINI",
    "BlackBoxToolExecutor",
    "ToolResult",
    "get_tools_for_backend",
    "execute_tool",
    # Registry exports (from tool_registry.py)
    "TOOL_DEFINITIONS",
    "get_tools_by_group",
    "get_tool_by_name",
    "resolve_alias",
    "resolve_executor_name",
    "get_anthropic_tools",
    "get_openai_rest_tools",
    "get_openai_realtime_tools",
    "get_gemini_rest_tools",
    "get_gemini_live_tools",
    "get_mcp_tools",
    "get_all_tool_names",
    "get_group_tool_names",
]
