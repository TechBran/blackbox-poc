"""FastAPI surface for the on-device ER mission agent."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import agent_loop, config, mission_manager, tools_decl, tools_exec, vertex_client

log = logging.getLogger("ugv.er.server")

_health_state: dict = {"ok": False, "error": None, "model_count": 0}


async def _probe_vertex() -> None:
    try:
        models = await vertex_client.list_models_async()
        _health_state["ok"] = True
        _health_state["model_count"] = len(models)
        _health_state["error"] = None
        log.info("Vertex probe ok (%d models visible)", len(models))
    except Exception as e:
        _health_state["ok"] = False
        _health_state["error"] = f"{type(e).__name__}: {e}"
        log.exception("Vertex probe failed")


async def _gc_loop() -> None:
    try:
        while True:
            await asyncio.sleep(300)
            try:
                await mission_manager.prune()
            except Exception:
                log.exception("mission prune failed")
    except asyncio.CancelledError:
        return


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "ER agent starting: project=%s location=%s model=%s tools=%d port=%d",
        config.GOOGLE_CLOUD_PROJECT or "<ADC-default>",
        config.GOOGLE_CLOUD_LOCATION,
        config.ER_MODEL_ID,
        len(tools_decl.ALL_DECLARATIONS),
        config.ER_PORT,
    )
    probe_task = asyncio.create_task(_probe_vertex())
    gc_task = asyncio.create_task(_gc_loop())
    try:
        yield
    finally:
        gc_task.cancel()
        probe_task.cancel()
        for t in (gc_task, probe_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await tools_exec.aclose()


app = FastAPI(title="UGV Beast ER Agent", version="0.1.0", lifespan=lifespan)


class MissionRequest(BaseModel):
    operator: str = Field(default="Brandon")
    mission: str


class MissionResponse(BaseModel):
    mission_id: str
    status: str


@app.get("/health")
async def health():
    if _health_state["ok"]:
        return {
            "ok": True,
            "model": config.ER_MODEL_ID,
            "models_visible": _health_state["model_count"],
            "tools": len(tools_decl.ALL_DECLARATIONS),
        }
    raise HTTPException(status_code=503, detail={
        "ok": False,
        "error": _health_state["error"] or "vertex_probe_pending",
    })


@app.post("/mission", response_model=MissionResponse)
async def start_mission(req: MissionRequest):
    if not req.mission or not req.mission.strip():
        raise HTTPException(status_code=400, detail="mission text required")
    m = await mission_manager.create(req.operator, req.mission.strip())
    task = asyncio.create_task(agent_loop.run_mission(m))
    await mission_manager.set_task(m.id, task)
    return MissionResponse(mission_id=m.id, status=m.status)


@app.get("/mission/{mission_id}")
async def get_mission(mission_id: str):
    m = await mission_manager.get(mission_id)
    if m is None:
        raise HTTPException(status_code=404, detail="unknown mission_id")
    return m.to_status_dict()


@app.post("/mission/{mission_id}/abort")
async def abort_mission(mission_id: str):
    m = await mission_manager.abort(mission_id)
    if m is None:
        raise HTTPException(status_code=404, detail="unknown mission_id")
    return m.to_status_dict()


@app.post("/mission/abort_active")
async def abort_active_missions():
    """Abort every currently-active mission without needing an ID. Used by
    supervisor's emergency_stop fan-out — the supervisor does not track ER
    mission IDs, but it must be able to halt ER's loop on e-stop. Returns
    the list of aborted missions ([] if none were active)."""
    aborted = await mission_manager.abort_active()
    return {"aborted": [m.to_status_dict() for m in aborted], "count": len(aborted)}


@app.get("/missions")
async def list_all_missions():
    return {"missions": await mission_manager.list_missions()}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        "ugv_tools_api.er.server:app",
        host=config.ER_HOST,
        port=config.ER_PORT,
        log_level="info",
        workers=1,
    )


if __name__ == "__main__":
    main()
