from __future__ import annotations
import inspect
from typing import Any, Awaitable, Callable
from .schema import ToolDescriptor, ParamSchema, render_anthropic, render_openai, render_gemini

ToolHandler = Callable[..., Awaitable[Any]]

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, tuple[ToolDescriptor, ToolHandler]] = {}

    def register(self, *, name: str, description: str,
                 parameters: dict[str, ParamSchema] | None = None,
                 required: list[str] | None = None):
        td = ToolDescriptor(name=name, description=description,
                            parameters=parameters or {}, required=required or [])
        def deco(fn: ToolHandler):
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"Tool {name} handler must be async")
            self._tools[name] = (td, fn)
            return fn
        return deco

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def descriptors(self) -> list[ToolDescriptor]:
        return [td for td, _ in self._tools.values()]

    async def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        td, handler = self._tools[name]
        missing = [r for r in td.required if r not in args]
        if missing:
            raise ValueError(f"Missing required params for {name}: {missing}")
        return await handler(**args)

    # Convenience renderers
    def as_anthropic(self): return render_anthropic(self.descriptors())
    def as_openai(self): return render_openai(self.descriptors())
    def as_gemini(self): return render_gemini(self.descriptors())

# Module-level singleton used by tool modules
registry = ToolRegistry()
tool = registry.register  # shorthand: @tool(...)
