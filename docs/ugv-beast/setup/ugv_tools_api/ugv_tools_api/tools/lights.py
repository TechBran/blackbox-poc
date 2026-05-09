"""LED control tools — T=132 IO4/IO5 via /ugv/json_cmd.

Mapping calibrated live on this robot (2026-04-18):
    IO4 -> bottom chassis lights (floodlight)
    IO5 -> gimbal head light (camera spotlight)

Defaults (2026-04-30): gimbal light disabled by default — the OAK-D body camera
and pantilt camera both handle low light well enough that the gimbal spotlight
is rarely needed and just drains battery. Bottom lights dropped to 30/255 (~12%)
which is still enough to see "both feet" of floor-level area without burning
the pack on long missions. Override either via env (UGV_LIGHT_GIMBAL_PWM /
UGV_LIGHT_BOTTOM_PWM) when a specific mission needs more illumination.

Safety: LIGHT_MAX_PWM caps every outbound value at 200/255, so even ugv_lights_set
cannot accidentally drive the LEDs to full power and drain the pack.
"""
import json
import os

from std_msgs.msg import String

from ..registry import tool
from ..ros_bridge import RosBridge
from ..schema import ParamSchema

BOTTOM_IO = 4
GIMBAL_IO = 5

# Gimbal head light defaults to OFF — the cameras (OAK-D body + pantilt) see
# fine in low light without it, and it's the largest battery drain among the
# LEDs. Set UGV_LIGHT_GIMBAL_PWM=50 if a mission really needs the spotlight.
LIGHT_GIMBAL_PWM = int(os.environ.get("UGV_LIGHT_GIMBAL_PWM", "0"))
# Bottom floodlight at 30/255 (~12%) — enough to see immediate floor area,
# minimal battery cost. Override via UGV_LIGHT_BOTTOM_PWM for darker rooms.
LIGHT_BOTTOM_PWM = int(os.environ.get("UGV_LIGHT_BOTTOM_PWM", "30"))
LIGHT_MAX_PWM = 200

# Preserve cross-call state so turning one side on/off doesn't stomp the other.
_state = {"IO4": 0, "IO5": 0}


def _json_cmd_pub():
    return RosBridge.instance().node.publisher("/ugv/json_cmd", String)


def _publish_t132(io4: int, io5: int) -> None:
    io4 = max(0, min(int(io4), LIGHT_MAX_PWM))
    io5 = max(0, min(int(io5), LIGHT_MAX_PWM))
    payload = json.dumps({"T": 132, "IO4": io4, "IO5": io5})
    _json_cmd_pub().publish(String(data=payload))
    _state["IO4"] = io4
    _state["IO5"] = io5


@tool(
    name="ugv_lights_on",
    description=(
        "Turn ON the UGV Beast's onboard LEDs at the calibrated brightness. "
        "Use when the operator asks, or proactively when perception is hindered "
        "by low light. 'bottom' is the default — the bottom floodlight at the "
        "calibrated low PWM (~12%) gives enough floor illumination without "
        "wasting battery. The gimbal head spotlight is intentionally disabled "
        "by default (the OAK-D and pantilt cameras handle low light without "
        "it); 'gimbal' or 'both' will be no-ops unless the operator has set "
        "UGV_LIGHT_GIMBAL_PWM>0 in env. IMPORTANT: call ugv_lights_off before "
        "mission_done to conserve battery."
    ),
    parameters={
        "which": ParamSchema(
            type="string",
            enum=["gimbal", "bottom", "both"],
            description="Which LEDs to turn on. Default 'bottom'.",
        ),
    },
)
async def ugv_lights_on(which: str = "bottom"):
    want_gimbal = which in ("gimbal", "both")
    want_bottom = which in ("bottom", "both")
    io4 = LIGHT_BOTTOM_PWM if want_bottom else _state["IO4"]
    io5 = LIGHT_GIMBAL_PWM if want_gimbal else _state["IO5"]
    _publish_t132(io4, io5)
    return {"which": which, "gimbal_pwm": io5, "bottom_pwm": io4}


@tool(
    name="ugv_lights_off",
    description=(
        "Turn OFF the UGV Beast's onboard LEDs. ALWAYS call this before "
        "mission_done to conserve battery. Can selectively turn off only the "
        "gimbal or bottom lights if one is still useful."
    ),
    parameters={
        "which": ParamSchema(
            type="string",
            enum=["gimbal", "bottom", "both"],
            description="Which LEDs to turn off. Default 'both'.",
        ),
    },
)
async def ugv_lights_off(which: str = "both"):
    off_gimbal = which in ("gimbal", "both")
    off_bottom = which in ("bottom", "both")
    io4 = 0 if off_bottom else _state["IO4"]
    io5 = 0 if off_gimbal else _state["IO5"]
    _publish_t132(io4, io5)
    return {"which": which, "gimbal_pwm": io5, "bottom_pwm": io4}


@tool(
    name="ugv_lights_set",
    description=(
        "Manually set the UGV Beast's LED PWM values (0-200 each, hard clamped). "
        "Used for calibration or explicit brightness control. Prefer ugv_lights_on "
        "and ugv_lights_off for normal use — those respect the calibrated defaults "
        "and keep the lights at sensible brightness."
    ),
    parameters={
        "gimbal_pwm": ParamSchema(
            type="integer",
            minimum=0,
            maximum=200,
            description="Gimbal LED PWM 0-200 (hard clamp at 200).",
        ),
        "bottom_pwm": ParamSchema(
            type="integer",
            minimum=0,
            maximum=200,
            description="Bottom LED PWM 0-200 (hard clamp at 200).",
        ),
    },
    required=["gimbal_pwm", "bottom_pwm"],
)
async def ugv_lights_set(gimbal_pwm: int, bottom_pwm: int):
    _publish_t132(bottom_pwm, gimbal_pwm)
    return {"gimbal_pwm": int(_state["IO5"]), "bottom_pwm": int(_state["IO4"])}
