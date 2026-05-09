"""Build Gemini FunctionDeclaration list from the live tool registry + synthetics."""
from __future__ import annotations

from typing import Any

from google.genai import types as genai_types

from ..registry import registry
from ..schema import ToolDescriptor
from ..tools import camera, explore, gimbal, lights, motion, nav, projection, status, system  # noqa: F401  register handlers

SYNTHETIC_NAMES: set[str] = {"mission_done", "mission_fail", "ask_user"}

# Tools the on-device ER agent must NOT have. Gemini Live (the supervisor's
# voice persona) owns the pan-tilt gimbal exclusively so the two layers don't
# fight for the actuator. ER perceives via the fixed OAK-D body camera and
# rotates the chassis to look elsewhere.
EXCLUDED_FROM_ER: set[str] = {
    "gimbal_look_at",
    "gimbal_reset",
    "gimbal_get_state",
}


def _to_param_props(td: ToolDescriptor) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for pname, ps in td.parameters.items():
        entry: dict[str, Any] = {"type": ps.type, "description": ps.description}
        if ps.enum is not None:
            entry["enum"] = ps.enum
        if ps.minimum is not None:
            entry["minimum"] = ps.minimum
        if ps.maximum is not None:
            entry["maximum"] = ps.maximum
        if ps.items is not None:
            entry["items"] = ps.items
        props[pname] = entry
    return props


def _build_passthrough_declarations() -> list[genai_types.FunctionDeclaration]:
    decls: list[genai_types.FunctionDeclaration] = []
    for td in sorted(registry.descriptors(), key=lambda t: t.name):
        if td.name in EXCLUDED_FROM_ER:
            continue
        schema: dict[str, Any] = {
            "type": "object",
            "properties": _to_param_props(td),
        }
        if td.required:
            schema["required"] = list(td.required)
        decls.append(
            genai_types.FunctionDeclaration(
                name=td.name,
                description=td.description,
                parameters=schema,
            )
        )
    return decls


def _build_synthetic_declarations() -> list[genai_types.FunctionDeclaration]:
    return [
        genai_types.FunctionDeclaration(
            name="mission_done",
            description=(
                "Terminate the mission as successfully completed. Call this ONLY when the goal has been "
                "fully achieved. Provide a short human-readable reason describing what was accomplished."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Short natural-language summary of what was accomplished.",
                    }
                },
                "required": ["reason"],
            },
        ),
        genai_types.FunctionDeclaration(
            name="mission_fail",
            description=(
                "Terminate the mission as failed. Call this when the goal is unreachable, unsafe, or "
                "hard-blocked (e.g., path blocked, required perception missing, hardware fault)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Short natural-language explanation of the failure.",
                    }
                },
                "required": ["reason"],
            },
        ),
        genai_types.FunctionDeclaration(
            name="ask_user",
            description=(
                "Ask the operator a clarifying question via the robot's speaker. The mission remains active; "
                "the next user input will continue the mission. Use sparingly — prefer self-directed action "
                "when perception is sufficient."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to speak aloud. Keep it short.",
                    }
                },
                "required": ["question"],
            },
        ),
    ]


_PASSTHROUGH = _build_passthrough_declarations()
_SYNTHETIC = _build_synthetic_declarations()

ALL_DECLARATIONS: list[genai_types.FunctionDeclaration] = [*_PASSTHROUGH, *_SYNTHETIC]

PASSTHROUGH_NAMES: set[str] = {d.name for d in _PASSTHROUGH}

ALL_TOOLS: genai_types.Tool = genai_types.Tool(function_declarations=ALL_DECLARATIONS)

ALL: list[genai_types.Tool] = [ALL_TOOLS]


if __name__ == "__main__":
    print(f"passthrough={len(_PASSTHROUGH)} synthetic={len(_SYNTHETIC)} total={len(ALL_DECLARATIONS)}")
    for d in ALL_DECLARATIONS:
        print("-", d.name)
