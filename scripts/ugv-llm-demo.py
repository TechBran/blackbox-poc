#!/usr/bin/env python3
"""End-to-end demo: Claude drives the UGV Beast via the Tailscale tool schema API.

Multi-turn tool-use loop: tool results are fed back to Claude until it stops
calling tools (or we hit the turn cap). Includes a safety guard that aborts the
session and triggers emergency_stop + gimbal_reset if Claude requests an unsafe
tool call (e.g. driving the wheels for too long, or commanding navigation to a
distant goal). Exit code is non-zero if an unsafe call is intercepted.
"""
from __future__ import annotations

import base64
import json
import math
import os
import sys
import time
from pathlib import Path

# ---- Load .env (Orchestrator venv has python-dotenv) -----------------------
try:
    from dotenv import load_dotenv

    REPO = Path(__file__).resolve().parent.parent
    load_dotenv(REPO / ".env")
except ImportError:
    pass

import requests
from anthropic import Anthropic

# ---- Config ----------------------------------------------------------------
UGV = os.environ.get("UGV_URL", "http://ugv-beast:8080")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
PROMPT = os.environ.get(
    "UGV_PROMPT",
    "Do a full scene awareness check: "
    "(1) get the robot pose, "
    "(2) summarize the LiDAR and tell me the closest obstacle, "
    "(3) pan the gimbal left 30 degrees, "
    "(4) take a snapshot from the OAK-D camera, "
    "(5) pan the gimbal back to center, "
    "(6) report what you observed and a brief scene assessment.",
)
MAX_TURNS = int(os.environ.get("UGV_MAX_TURNS", "10"))

# Safety thresholds
MAX_WHEEL_DURATION_SEC = 2.0          # any motion_* duration above this is unsafe
MAX_NAV_GOAL_RADIUS_M = 0.5           # any nav_goto_point further than this is unsafe
UNSAFE_TOOLS_HARD_BLOCK: set[str] = set()  # add tool names to forbid outright


def fetch_tools() -> list[dict]:
    r = requests.get(f"{UGV}/tools", params={"format": "anthropic"}, timeout=5)
    r.raise_for_status()
    return r.json()


def call_tool(name: str, args: dict) -> dict:
    try:
        r = requests.post(f"{UGV}/tool/{name}", json=args, timeout=30)
    except requests.RequestException as e:
        return {"error": f"request failed: {e}"}
    try:
        return r.json()
    except ValueError:
        return {"error": f"non-JSON response (status {r.status_code})", "text": r.text[:400]}


def trigger_failsafe(reason: str) -> None:
    """Stop wheels + center gimbal regardless of what Claude was doing."""
    print(f"\n!!! SAFETY ABORT: {reason}")
    print("    → calling system_emergency_stop ...")
    try:
        print("      ", call_tool("system_emergency_stop", {}))
    except Exception as e:
        print(f"      emergency_stop failed: {e}")
    print("    → calling gimbal_reset ...")
    try:
        print("      ", call_tool("gimbal_reset", {}))
    except Exception as e:
        print(f"      gimbal_reset failed: {e}")


def is_unsafe(name: str, args: dict) -> str | None:
    """Return a reason string if the call should be blocked, else None."""
    if name in UNSAFE_TOOLS_HARD_BLOCK:
        return f"tool {name!r} is hard-blocked"
    if name.startswith("motion_") and name != "motion_stop":
        # Anything driving the wheels with a duration above the threshold
        # (or no duration field at all, which would mean "drive until told to stop")
        dur = args.get("duration") or args.get("duration_sec") or args.get("seconds")
        if dur is None:
            return f"{name} requested with no explicit duration (open-ended drive)"
        try:
            if float(dur) > MAX_WHEEL_DURATION_SEC:
                return f"{name} requested with duration={dur}s > {MAX_WHEEL_DURATION_SEC}s safety cap"
        except (TypeError, ValueError):
            return f"{name} requested with non-numeric duration={dur!r}"
    if name == "nav_goto_point":
        x = args.get("x") or args.get("goal_x") or 0.0
        y = args.get("y") or args.get("goal_y") or 0.0
        try:
            r = math.hypot(float(x), float(y))
            if r > MAX_NAV_GOAL_RADIUS_M:
                return (
                    f"nav_goto_point goal radius {r:.2f}m > {MAX_NAV_GOAL_RADIUS_M}m safety cap"
                )
        except (TypeError, ValueError):
            return f"nav_goto_point with non-numeric goal x={x!r}, y={y!r}"
    return None


def preview_result(result) -> str:
    """Pretty-print a tool result, but truncate giant base64 image blobs."""
    if not isinstance(result, dict):
        preview = {"result": result}
    else:
        preview = dict(result)

    inner = preview.get("result")
    if isinstance(inner, dict) and "image_b64" in inner:
        b64 = inner["image_b64"] or ""
        try:
            head = base64.b64decode(b64[:32])[:4].hex()
        except Exception:
            head = "??"
        inner = dict(inner)
        inner["image_b64"] = f"<{len(b64)} chars base64, header={head}>"
        preview["result"] = inner

    s = json.dumps(preview, default=str)
    return s if len(s) < 500 else s[:500] + "..."


def main() -> int:
    print(f"UGV:    {UGV}")
    print(f"Model:  {MODEL}")
    print(f"Prompt: {PROMPT}")
    print(f"Fetching tools from {UGV}/tools ...")
    tools = fetch_tools()
    print(f"  loaded {len(tools)} tools: {[t['name'] for t in tools]}")

    client = Anthropic()
    messages: list[dict] = [{"role": "user", "content": PROMPT}]
    invoked: list[str] = []
    turn = 0
    aborted = False
    t0 = time.time()

    while turn < MAX_TURNS:
        turn += 1
        print(f"\n=== Claude turn {turn} ===")
        msg = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=(
                "You are controlling a UGV Beast tracked robot in a staging area. "
                "Use tools carefully — do NOT drive the wheels unless the user "
                "explicitly asks you to. Prefer sensors and the gimbal for "
                "exploration. When you are done, respond with a text summary and "
                "do not call any more tools."
            ),
            tools=tools,
            messages=messages,
        )
        print(f"[stop_reason: {msg.stop_reason}]")

        # Print any text blocks Claude produced this turn
        for block in msg.content:
            if block.type == "text" and block.text.strip():
                print(f"[text] {block.text}")

        tool_uses = [b for b in msg.content if b.type == "tool_use"]
        if not tool_uses:
            print("[no tool calls this turn — done]")
            break

        # Persist this assistant turn before sending tool_results back
        messages.append({"role": "assistant", "content": msg.content})

        tool_results = []
        for tu in tool_uses:
            print(f"\n[tool_use] {tu.name}({json.dumps(tu.input)})")
            invoked.append(tu.name)

            unsafe_reason = is_unsafe(tu.name, tu.input or {})
            if unsafe_reason:
                trigger_failsafe(unsafe_reason)
                aborted = True
                # Send a synthetic tool_result so the conversation stays valid
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(
                            {"ok": False, "error": "blocked by safety guard", "reason": unsafe_reason}
                        ),
                        "is_error": True,
                    }
                )
                continue

            result = call_tool(tu.name, tu.input or {})
            print(f"[tool_result] {preview_result(result)}")
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                }
            )

        messages.append({"role": "user", "content": tool_results})

        if aborted:
            print("\n[aborting loop after safety abort]")
            break
        if msg.stop_reason != "tool_use":
            break

    # ---- Post-check: confirm gimbal is centered -------------------------
    print("\n=== Post-check: gimbal_get_state ===")
    state = call_tool("gimbal_get_state", {})
    print(f"  {json.dumps(state)}")

    elapsed = time.time() - t0
    print("\n=== Done ===")
    print(f"Turns:           {turn}")
    print(f"Tool invocations: {len(invoked)} ({sorted(set(invoked))})")
    print(f"Aborted:         {aborted}")
    print(f"Elapsed:         {elapsed:.1f}s")
    return 1 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
