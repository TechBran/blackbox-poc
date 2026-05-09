"""FastAPI HTTP surface for the UGV Beast tool schema."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel
from typing import Any
import base64, time

# Import tool modules to register handlers before app starts
from .registry import registry
from .ros_bridge import RosBridge
from .tools import motion, gimbal, camera, status, nav, system, lights, explore, projection  # noqa: F401

@asynccontextmanager
async def lifespan(app: FastAPI):
    RosBridge.instance().start()
    yield
    RosBridge.instance().stop()

app = FastAPI(title="UGV Beast Tool Schema API", version="0.1.0", lifespan=lifespan)

@app.get("/health")
def health():
    return {"ok": True, "bridge": RosBridge.instance().is_running()}

@app.get("/tools")
def list_tools(format: str = Query("anthropic", pattern="^(anthropic|openai|gemini)$")):
    if format == "anthropic": return registry.as_anthropic()
    if format == "openai":    return registry.as_openai()
    return registry.as_gemini()

class ToolCall(BaseModel):
    class Config: extra = "allow"

@app.post("/tool/{name}")
async def call_tool(name: str, body: dict[str, Any] | None = None):
    body = body or {}
    if name not in registry.names():
        raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
    try:
        result = await registry.dispatch(name, body)
        return {"tool": name, "result": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return JSONResponse(status_code=500, content={"tool": name, "error": str(e)})

@app.get("/snapshot/{camera_name}")
def snapshot(camera_name: str):
    from .tools.camera import CAMERA_TOPICS
    topic = CAMERA_TOPICS.get(camera_name)
    if not topic:
        raise HTTPException(404, f"unknown camera {camera_name}")
    cached = RosBridge.instance().node.get_latest(topic)
    if cached is None:
        raise HTTPException(503, f"no frames on {topic} yet")
    _, msg = cached
    return Response(content=bytes(msg.data), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})
