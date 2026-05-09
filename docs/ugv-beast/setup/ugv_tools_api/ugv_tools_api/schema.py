from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field

class ParamSchema(BaseModel):
    type: Literal["string", "number", "integer", "boolean", "array", "object"]
    description: str
    default: Any | None = None
    minimum: float | None = None
    maximum: float | None = None
    enum: list[Any] | None = None
    items: dict[str, Any] | None = None  # for type=array

class ToolDescriptor(BaseModel):
    name: str
    description: str
    parameters: dict[str, ParamSchema] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)

def _props_block(tool: ToolDescriptor) -> dict[str, Any]:
    props = {}
    for pname, ps in tool.parameters.items():
        entry: dict[str, Any] = {"type": ps.type, "description": ps.description}
        for opt in ("default", "minimum", "maximum", "enum", "items"):
            v = getattr(ps, opt)
            if v is not None:
                entry[opt] = v
        props[pname] = entry
    return {"type": "object", "properties": props, "required": tool.required}

def render_anthropic(tools: list[ToolDescriptor]) -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "input_schema": _props_block(t)} for t in tools]

def render_openai(tools: list[ToolDescriptor]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {
        "name": t.name, "description": t.description, "parameters": _props_block(t)
    }} for t in tools]

def render_gemini(tools: list[ToolDescriptor]) -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "parameters": _props_block(t)} for t in tools]
