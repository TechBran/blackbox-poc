import asyncio, pytest
from ugv_tools_api.registry import ToolRegistry, tool
from ugv_tools_api.schema import ParamSchema

reg = ToolRegistry()

@reg.register(
    name="math_add",
    description="Add two numbers.",
    parameters={"a": ParamSchema(type="number", description="a"),
                "b": ParamSchema(type="number", description="b")},
    required=["a", "b"],
)
async def _add(a: float, b: float) -> dict:
    return {"sum": a + b}

def test_registry_lists_tool():
    assert "math_add" in reg.names()

def test_registry_dispatches():
    result = asyncio.run(reg.dispatch("math_add", {"a": 2, "b": 3}))
    assert result == {"sum": 5}

def test_registry_rejects_missing_required():
    with pytest.raises(ValueError):
        asyncio.run(reg.dispatch("math_add", {"a": 2}))

def test_registry_rejects_unknown_tool():
    with pytest.raises(KeyError):
        asyncio.run(reg.dispatch("no_such_tool", {}))

def test_registry_sync_handler_rejected():
    reg2 = ToolRegistry()
    with pytest.raises(TypeError):
        @reg2.register(name="sync_bad", description="not async", parameters={}, required=[])
        def _sync_handler():  # no async!
            return {}
