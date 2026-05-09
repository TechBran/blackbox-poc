"""System tools: emergency stop + servo housekeeping.

These tools all route through the new `/ugv/json_cmd` topic added to the
patched `ugv_driver` (see docs/ugv-beast/setup/ugv_driver_patches/). Payloads
are `std_msgs/String` containing a JSON object that the ESP32 firmware will
interpret as a T-coded command.

Firmware command numbers
------------------------
Confirmed (from existing driver call sites):
    T='13'  -- cmd_vel (string!)         driver.cmd_vel_callback
    T=132   -- LED control               driver.led_ctrl_callback
    T=134   -- pan-tilt servo goto       driver._on_gimbal_absolute

Best-guess (NOT verified against firmware -- see notes below):
    T=0     -- all-motor cutoff / e-stop
    T=134   -- the plan originally suggested this for "servo center", but
              T=134 is already pan-tilt goto. The wrapper here still sends
              T=134 with X=Y=0, SX=SY=600, which centers the gimbal as a
              side-effect. If firmware exposes a dedicated "center / mid-
              point all servos" code, swap it in here.
    T=135   -- servo release / torque-off (plan guess; not verified)

If a T-number is wrong, the ESP32 should silently ignore the unknown command
(observed behavior on the Waveshare general driver firmware). The tools will
still publish to /ugv/json_cmd in either case, so calling them is safe.

Tools
-----
- system_emergency_stop: belt-and-suspenders stop -- zeroes /cmd_vel AND
  publishes the firmware T=0 cutoff. The cmd_vel zero is the authoritative
  brake; the firmware command is a best-effort hardware-level cutoff.
- system_servo_center: park gimbal pan/tilt at 0/0 (workaround using T=134
  pan-tilt goto since a dedicated "center all servos" code is not known).
- system_servo_release: limp servo torque (T=135 -- unverified guess; safe
  to publish but may be a no-op until firmware mapping is confirmed).
"""
import asyncio
import json
import os
import time

import httpx
from std_msgs.msg import String
from std_srvs.srv import Trigger
from geometry_msgs.msg import Twist
from ..ros_bridge import RosBridge
from ..registry import tool

import logging

_log = logging.getLogger(__name__)

# Tunables -- overridable by tests
_ESTOP_PIN_SECONDS = 1.5
_ESTOP_PIN_HZ = 20
_ESTOP_ER_CANCEL_TIMEOUT_S = 1.0
_ER_URL_DEFAULT = "http://localhost:8082"


def _json_cmd_pub():
    return RosBridge.instance().node.publisher("/ugv/json_cmd", String)


def _cmd_vel_pub():
    return RosBridge.instance().node.publisher("/cmd_vel", Twist)


@tool(
    name="system_emergency_stop",
    description=(
        "Emergency stop fan-out. Cancels active Nav2 goal, triggers "
        "/explore/stop, cancels any active ER mission so the agent loop stops "
        "issuing new actions, publishes zero geometry_msgs/Twist to /cmd_vel "
        f"for {_ESTOP_PIN_SECONDS}s @ {_ESTOP_PIN_HZ}Hz to outlive controller_server, "
        "and sends firmware-level T=0 cutoff to /ugv/json_cmd. Each branch is "
        "best-effort -- failures in one do not block the others."
    ),
)
async def system_emergency_stop():
    fanout = []

    # 1. Cancel any active Nav2 goal (BT/controller stops on next tick)
    try:
        from . import nav as nav_tools
        with nav_tools._lock:
            handle = nav_tools._state.get("handle")
        if handle is not None:
            handle.cancel_goal_async()
            fanout.append("nav_cancel")
    except Exception as exc:
        _log.warning("estop nav_cancel branch failed: %s", exc)

    # 2. Trigger /explore/stop if explorer is running
    try:
        node = RosBridge.instance().node
        cli = node.create_client(Trigger, "/explore/stop")
        if cli.wait_for_service(timeout_sec=0.3):
            cli.call_async(Trigger.Request())
            fanout.append("explore_stop")
    except Exception as exc:
        _log.warning("estop explore_stop branch failed: %s", exc)

    # 3. Cancel any active ER mission so the agent loop stops issuing actions.
    # Fire BEFORE cmd_vel_pin so ER doesn't try to push a new motion command
    # in the middle of our zero-Twist pin window.
    try:
        url = os.environ.get("ER_URL", _ER_URL_DEFAULT) + "/mission/abort_active"
        async with httpx.AsyncClient(timeout=_ESTOP_ER_CANCEL_TIMEOUT_S) as c:
            r = await c.post(url)
        if r.status_code in (200, 204):
            fanout.append("er_cancel")
        else:
            _log.warning("estop er_cancel got HTTP %s from %s", r.status_code, url)
    except Exception as exc:
        _log.warning("estop er_cancel branch failed: %s", exc)

    # 4. Pin /cmd_vel at zero for _ESTOP_PIN_SECONDS @ _ESTOP_PIN_HZ
    twist = Twist()
    twist.linear.x = 0.0; twist.linear.y = 0.0; twist.linear.z = 0.0
    twist.angular.x = 0.0; twist.angular.y = 0.0; twist.angular.z = 0.0
    pub = _cmd_vel_pub()
    period = 1.0 / max(1, _ESTOP_PIN_HZ)
    deadline = time.monotonic() + _ESTOP_PIN_SECONDS
    while time.monotonic() < deadline:
        pub.publish(twist)
        await asyncio.sleep(period)
    fanout.append("cmd_vel_pin")

    # 5. Belt-and-suspenders firmware-level cutoff
    _json_cmd_pub().publish(String(data=json.dumps({"T": 0})))
    fanout.append("fw_T0")

    return {"estopped": True, "fanout": fanout}


@tool(
    name="system_servo_center",
    description=(
        "Center the pan-tilt servos to (pan=0, tilt=0). Publishes T=134 "
        "(pan-tilt goto) with X=Y=0, SX=SY=600 via /ugv/json_cmd. Use with "
        "the robot at rest. Firmware does not expose a separate 'center all "
        "servos' code; this is the same path /gimbal/absolute uses."
    ),
)
async def system_servo_center():
    cmd = {"T": 134, "X": 0, "Y": 0, "SX": 600, "SY": 600}
    _json_cmd_pub().publish(String(data=json.dumps(cmd)))
    return {"centered": True}


@tool(
    name="system_servo_release",
    description=(
        "Release servo torque (limp mode -- robot will not resist movement). "
        "Publishes T=135 to /ugv/json_cmd. Firmware command number is NOT "
        "verified -- the ESP32 will silently ignore if unsupported. CAUTION: "
        "if it works, the gimbal will physically droop until torque is "
        "re-asserted by another command."
    ),
)
async def system_servo_release():
    _json_cmd_pub().publish(String(data=json.dumps({"T": 135})))
    return {"released": True}
