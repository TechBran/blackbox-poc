"""Gimbal tools: pan-tilt absolute control + state queries.

- `gimbal_look_at`: publish geometry_msgs/Point to /gimbal/absolute (Task 2.1)
  with x=pan_deg, y=tilt_deg, z=speed.
- `gimbal_reset`: convenience wrapper that returns to (0, 0).
- `gimbal_get_state`: read latest cached /gimbal/state (Task 2.2 publishes at 10Hz).
"""
from geometry_msgs.msg import Point, PointStamped  # noqa: F401 - PointStamped used by bridge
from ..ros_bridge import RosBridge
from ..registry import tool
from ..schema import ParamSchema


def _clamp(v, lo, hi): return max(lo, min(hi, v))


@tool(
    name="gimbal_look_at",
    description="Point the pan-tilt gimbal to an absolute pan/tilt angle in degrees.",
    parameters={
        "pan_deg": ParamSchema(type="number", minimum=-180, maximum=180,
                               description="Pan angle. Negative=right, positive=left. Zero=forward."),
        "tilt_deg": ParamSchema(type="number", minimum=-45, maximum=90,
                                description="Tilt angle. Negative=down, positive=up."),
        "speed": ParamSchema(type="integer", minimum=1, maximum=300, default=100,
                             description="Servo speed 1-300. 100 is a gentle default."),
    },
    required=["pan_deg", "tilt_deg"],
)
async def gimbal_look_at(pan_deg: float, tilt_deg: float, speed: int = 100):
    pub = RosBridge.instance().node.publisher("/gimbal/absolute", Point)
    pan_clamped = _clamp(float(pan_deg), -180.0, 180.0)
    tilt_clamped = _clamp(float(tilt_deg), -45.0, 90.0)
    speed_clamped = _clamp(int(speed), 1, 300)
    m = Point()
    # Hardware sign flip: this Beast's pan servo is wired such that positive
    # rotation moves physically RIGHT, opposite of the ROS right-hand-rule
    # convention exposed in the tool docstring (positive=left). Negate at this
    # publishing boundary so the model's logical "+30 = look left" produces
    # actual left motion. Tilt isn't flipped — its convention happens to match.
    m.x = float(-pan_clamped)
    m.y = float(tilt_clamped)
    m.z = float(speed_clamped)
    pub.publish(m)
    return {"commanded": {"pan_deg": pan_clamped, "tilt_deg": tilt_clamped, "speed": speed_clamped}}


@tool(
    name="gimbal_reset",
    description="Return the gimbal to the forward-center home position (pan=0, tilt=0).",
)
async def gimbal_reset():
    return await gimbal_look_at(0.0, 0.0, 150)


@tool(
    name="gimbal_get_state",
    description="Get the current pan and tilt angles of the gimbal.",
)
async def gimbal_get_state():
    cached = RosBridge.instance().node.get_latest("/gimbal/state")
    if cached is None:
        return {"error": "no gimbal state received yet"}
    ts, msg = cached
    # Mirror the publish-side sign flip so reported pan stays in the docstring's
    # logical convention (positive=left). /gimbal/state is published in raw
    # servo space by the same C++ node that consumes /gimbal/absolute, so its
    # sign matches the hardware not the API contract.
    return {"pan_deg": -msg.point.x, "tilt_deg": msg.point.y, "age_s": round(__import__('time').time() - ts, 3)}
