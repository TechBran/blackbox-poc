"""Execute a single Gemini function_call against the real tool API or synthetic handlers."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from . import config
from .mission import Mission
from .tools_decl import PASSTHROUGH_NAMES, SYNTHETIC_NAMES

_http: Optional[httpx.AsyncClient] = None
_http_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        async with _http_lock:
            if _http is None:
                _http = httpx.AsyncClient(timeout=config.TOOLS_HTTP_TIMEOUT_S)
    return _http


async def aclose() -> None:
    global _http
    if _http is not None:
        try:
            await _http.aclose()
        finally:
            _http = None


def _args_of(func_call) -> dict[str, Any]:
    raw = getattr(func_call, "args", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return dict(raw)
    except Exception:
        return {}


async def _speak_once(text: str) -> None:
    if not text:
        return
    try:
        client = await _get_client()
        await client.post(
            config.SPEAK_URL,
            json={"text": text},
            timeout=config.SPEAK_HTTP_TIMEOUT_S,
        )
    except Exception:
        pass


async def _call_passthrough(name: str, args: dict[str, Any]) -> dict[str, Any]:
    url = f"{config.TOOLS_API_URL}/tool/{name}"
    try:
        client = await _get_client()
        resp = await client.post(url, json=args)
        ok = resp.is_success
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
        if not ok:
            return {"ok": False, "error": data if isinstance(data, dict) else {"detail": str(data)},
                    "status_code": resp.status_code}
        return {"ok": True, "data": data}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def execute(func_call, mission: Mission) -> dict[str, Any]:
    name = getattr(func_call, "name", "") or ""
    args = _args_of(func_call)

    if name in SYNTHETIC_NAMES:
        if name == "mission_done":
            reason = str(args.get("reason", "")).strip() or "mission complete"
            mission.status = "completed"
            mission.end_reason = reason
            return {"ok": True, "data": {"status": "completed", "reason": reason}}
        if name == "mission_fail":
            reason = str(args.get("reason", "")).strip() or "mission failed"
            mission.status = "failed"
            mission.end_reason = reason
            return {"ok": True, "data": {"status": "failed", "reason": reason}}
        if name == "ask_user":
            question = str(args.get("question", "")).strip()
            if question:
                asyncio.create_task(_speak_once(question))
            return {"ok": True, "data": {"status": "active", "question": question}}

    if name in PASSTHROUGH_NAMES:
        return await _call_passthrough(name, args)

    return {"ok": False, "error": f"unknown_tool: {name}"}
