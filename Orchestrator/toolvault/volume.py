"""
ToolVault Volume - Append-only tool definition storage.

The volume is a single monolithic text file where each tool definition
is delimited by numbered START/END anchors. Tool content is retrieved
by exact byte-offset slicing, giving O(1) access to any tool.

Mirrors the proven pattern from Orchestrator/volume.py (snapshot volume).
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import Orchestrator.toolvault.config as _config
from Orchestrator.toolvault.config import (
    tool_start_anchor,
    tool_end_anchor,
    START_RX_B,
    END_RX_B,
    BLOCK_FIELDS,
    EM_DASH,
)


# ---------------------------------------------------------------------------
# Tool Block Formatting
# ---------------------------------------------------------------------------

def format_tool_block(
    number: int,
    name: str,
    description: str,
    category: str,
    groups: list,
    parameters: dict,
    returns: str = "",
    example: str = "",
    notes: str = "",
) -> str:
    """Format a tool definition into a volume block.

    The block is human-readable AND machine-parseable:
    - DESCRIPTION is the embedding target for semantic search
    - JSON_SCHEMA is the raw canonical schema for provider format conversion
    - PARAMETERS is a human-readable summary for model context injection

    Args:
        number: Auto-incrementing tool number (for anchor pairing)
        name: Canonical tool name (e.g., "web_search")
        description: Tool description (becomes the embedding target)
        category: Tool category (e.g., "web", "media_generation")
        groups: Consumer groups (e.g., ["chat", "realtime", "mcp"])
        parameters: Canonical JSON schema dict (provider-agnostic)
        returns: Human-readable return description
        example: Example usage string
        notes: Edge cases, gotchas, tips

    Returns:
        Formatted tool block string ready for volume append.
    """
    # Build human-readable parameter summary
    param_lines = _format_parameters_human(parameters)

    # Build the block
    lines = [
        tool_start_anchor(number, name),
        f"NAME: {name}",
        f"DESCRIPTION: {description}",
        f"CATEGORY: {category}",
        f"GROUPS: {', '.join(groups)}",
        f"PARAMETERS:",
    ]
    lines.extend(f"  {line}" for line in param_lines)

    if returns:
        lines.append(f"RETURNS: {returns}")
    if example:
        lines.append(f"EXAMPLE: {example}")
    if notes:
        lines.append(f"NOTES: {notes}")

    # JSON_SCHEMA: the machine-readable canonical schema (single line for easy parsing)
    lines.append(f"JSON_SCHEMA:")
    lines.append(json.dumps(parameters, separators=(",", ":")))

    lines.append(tool_end_anchor(number, name))
    lines.append("")  # trailing newline

    return "\n".join(lines)


def _format_parameters_human(parameters: dict) -> list:
    """Convert JSON schema to human-readable parameter lines.

    Example output:
      query (string, required): The search query to look up
      max_chars (integer, optional, default=80000): Maximum characters
    """
    props = parameters.get("properties", {})
    required = set(parameters.get("required", []))
    lines = []

    for param_name, param_spec in props.items():
        ptype = param_spec.get("type", "any")
        desc = param_spec.get("description", "")
        is_required = param_name in required

        # Build qualifier string
        qualifiers = [ptype, "required" if is_required else "optional"]

        if "default" in param_spec:
            qualifiers.append(f'default="{param_spec["default"]}"')
        if "enum" in param_spec:
            enum_str = ",".join(str(v) for v in param_spec["enum"])
            qualifiers.append(f"enum=[{enum_str}]")

        qualifier_str = ", ".join(qualifiers)
        lines.append(f"{param_name} ({qualifier_str}): {desc}")

    if not lines:
        lines.append("(no parameters)")

    return lines


# ---------------------------------------------------------------------------
# Volume I/O
# ---------------------------------------------------------------------------

def read_volume_bytes() -> bytes:
    """Read the entire volume file as raw bytes.

    Returns raw bytes for proper byte-offset slicing.
    Matches pattern from Orchestrator/volume.py:read_volume_bytes().
    """
    if not _config.VOLUME_PATH.exists():
        return b""

    try:
        with open(_config.VOLUME_PATH, "rb") as f:
            raw = f.read()

        expected = _config.VOLUME_PATH.stat().st_size
        if len(raw) != expected:
            print(f"[TOOLVAULT] Incomplete read! Expected {expected} bytes, got {len(raw)}")

        return raw
    except Exception as e:
        print(f"[TOOLVAULT] Failed to read volume: {e}")
        return b""


def append_tool_block(block_text: str) -> Tuple[int, int]:
    """Append a tool block to the volume file.

    Returns (byte_start, byte_end) for the appended block.
    Uses fsync for durability (matches snapshot volume pattern).
    """
    _config.TOOLVAULT_DIR.mkdir(parents=True, exist_ok=True)

    encoded = block_text.encode("utf-8")

    try:
        with open(_config.VOLUME_PATH, "rb+") as f:
            f.seek(0, os.SEEK_END)
            byte_start = f.tell()

            # Ensure we start on a new line
            if byte_start > 0:
                f.seek(-1, os.SEEK_END)
                last = f.read(1)
                if last != b"\n":
                    f.seek(0, os.SEEK_END)
                    f.write(b"\n")
                    byte_start += 1

            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())

            byte_end = f.tell()

    except FileNotFoundError:
        byte_start = 0
        with open(_config.VOLUME_PATH, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
            byte_end = f.tell()

    return byte_start, byte_end


def read_tool_by_offset(byte_start: int, byte_end: int) -> str:
    """Read a single tool block from the volume by byte offset.

    O(1) retrieval regardless of volume size.
    """
    vol_bytes = read_volume_bytes()
    if not vol_bytes:
        return ""

    if byte_start >= len(vol_bytes) or byte_end > len(vol_bytes):
        print(f"[TOOLVAULT] Offset out of range: {byte_start}:{byte_end} (volume is {len(vol_bytes)} bytes)")
        return ""

    return vol_bytes[byte_start:byte_end].decode("utf-8", errors="replace")


def parse_tool_block(block_text: str) -> Dict[str, Any]:
    """Parse a tool block into its constituent fields.

    Returns dict with keys: NAME, DESCRIPTION, CATEGORY, GROUPS,
    PARAMETERS, RETURNS, EXAMPLE, NOTES, JSON_SCHEMA.

    JSON_SCHEMA is parsed from JSON string back to dict.
    """
    result = {}

    lines = block_text.split("\n")
    current_field = None
    current_value = []

    for line in lines:
        # Skip anchors
        if line.startswith("=== START TOOL") or line.startswith("=== END TOOL"):
            continue

        # Check if this line starts a new field
        field_match = None
        for field_name in BLOCK_FIELDS:
            if line.startswith(f"{field_name}:"):
                field_match = field_name
                break

        if field_match:
            # Save previous field
            if current_field:
                result[current_field] = "\n".join(current_value).strip()

            # Start new field
            current_field = field_match
            # Value is everything after "FIELD: " (or "FIELD:" with content on next lines)
            value_part = line[len(field_match) + 1:].strip()
            current_value = [value_part] if value_part else []
        elif current_field:
            current_value.append(line)

    # Save last field
    if current_field:
        result[current_field] = "\n".join(current_value).strip()

    # Parse JSON_SCHEMA back to dict
    if "JSON_SCHEMA" in result and result["JSON_SCHEMA"]:
        try:
            result["JSON_SCHEMA"] = json.loads(result["JSON_SCHEMA"])
        except json.JSONDecodeError:
            print(f"[TOOLVAULT] Failed to parse JSON_SCHEMA for {result.get('NAME', '?')}")

    # Parse GROUPS back to list
    if "GROUPS" in result and isinstance(result["GROUPS"], str):
        result["GROUPS"] = [g.strip() for g in result["GROUPS"].split(",") if g.strip()]

    return result


def rebuild_volume_index() -> Dict[str, Dict[str, Any]]:
    """Scan the volume file and rebuild the index from scratch.

    Uses byte-level regex to find all START/END anchors and extract
    tool metadata. This is the recovery path if the manifest is lost.

    Returns dict of {tool_name: {number, byte_start, byte_end}}.
    """
    vol_bytes = read_volume_bytes()
    if not vol_bytes:
        return {}

    # Find all START anchors
    starts = {}
    for m in START_RX_B.finditer(vol_bytes):
        number = int(m.group(1))
        name = m.group(2).decode("utf-8", errors="replace")
        starts[name] = {"number": number, "byte_start": m.start()}

    # Find all END anchors
    tools = {}
    for m in END_RX_B.finditer(vol_bytes):
        number = int(m.group(1))
        name = m.group(2).decode("utf-8", errors="replace")

        if name in starts and starts[name]["number"] == number:
            # byte_end includes the END anchor line + trailing newline
            end_pos = m.end()
            # Include trailing newline if present
            if end_pos < len(vol_bytes) and vol_bytes[end_pos:end_pos + 1] == b"\n":
                end_pos += 1

            tools[name] = {
                "number": number,
                "byte_start": starts[name]["byte_start"],
                "byte_end": end_pos,
            }
        else:
            print(f"[TOOLVAULT] Mismatched END anchor: TOOL {number:03d} {EM_DASH} {name}")

    return tools
