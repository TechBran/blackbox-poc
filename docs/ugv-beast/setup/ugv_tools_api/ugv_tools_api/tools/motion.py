"""Motion tools: publishes to /cmd_vel via the ROS bridge."""
import asyncio
from geometry_msgs.msg import Twist
from ..ros_bridge import RosBridge
from ..registry import tool
from ..schema import ParamSchema

# Safety clamps (match velocity_smoother limits)
MAX_LIN = 0.15
MAX_ANG = 0.8

def _cmd_vel_pub():
    return RosBridge.instance().node.publisher("/cmd_vel", Twist)

async def _drive(linear: float, angular: float, duration: float):
    pub = _cmd_vel_pub()
    t0 = asyncio.get_event_loop().time()
    twist = Twist(); twist.linear.x = linear; twist.angular.z = angular
    while asyncio.get_event_loop().time() - t0 < duration:
        pub.publish(twist)
        await asyncio.sleep(0.1)
    stop = Twist()
    pub.publish(stop); pub.publish(stop)  # redundant stop for reliability
    return {"executed": {"linear": linear, "angular": angular, "duration_s": duration}}

@tool(
    name="motion_move_forward",
    description="Drive the robot straight forward for a duration.",
    parameters={
        "duration_s": ParamSchema(type="number", minimum=0.1, maximum=10.0,
                                  description="Seconds to drive. Clamped at 10."),
        "speed_m_s": ParamSchema(type="number", minimum=0.02, maximum=MAX_LIN, default=0.1,
                                 description="Linear speed in m/s (max 0.15)."),
    },
    required=["duration_s"],
)
async def motion_move_forward(duration_s: float, speed_m_s: float = 0.1):
    speed = max(0.02, min(MAX_LIN, float(speed_m_s)))
    return await _drive(speed, 0.0, min(float(duration_s), 10.0))

@tool(
    name="motion_move_backward",
    description="Drive the robot straight backward for a duration.",
    parameters={
        "duration_s": ParamSchema(type="number", minimum=0.1, maximum=10.0,
                                  description="Seconds to drive."),
        "speed_m_s": ParamSchema(type="number", minimum=0.02, maximum=MAX_LIN, default=0.08,
                                 description="Linear speed."),
    },
    required=["duration_s"],
)
async def motion_move_backward(duration_s: float, speed_m_s: float = 0.08):
    speed = max(0.02, min(MAX_LIN, float(speed_m_s)))
    return await _drive(-speed, 0.0, min(float(duration_s), 10.0))

@tool(
    name="motion_rotate_left",
    description="Rotate in place counter-clockwise (positive angular velocity).",
    parameters={
        "duration_s": ParamSchema(type="number", minimum=0.1, maximum=5.0,
                                  description="Seconds to rotate."),
        "rate_rad_s": ParamSchema(type="number", minimum=0.1, maximum=MAX_ANG, default=0.5,
                                  description="Angular rate rad/s."),
    },
    required=["duration_s"],
)
async def motion_rotate_left(duration_s: float, rate_rad_s: float = 0.5):
    return await _drive(0.0, min(MAX_ANG, float(rate_rad_s)), min(float(duration_s), 5.0))

@tool(
    name="motion_rotate_right",
    description="Rotate in place clockwise (negative angular velocity).",
    parameters={
        "duration_s": ParamSchema(type="number", minimum=0.1, maximum=5.0, description="Seconds."),
        "rate_rad_s": ParamSchema(type="number", minimum=0.1, maximum=MAX_ANG, default=0.5, description="rad/s."),
    },
    required=["duration_s"],
)
async def motion_rotate_right(duration_s: float, rate_rad_s: float = 0.5):
    return await _drive(0.0, -min(MAX_ANG, float(rate_rad_s)), min(float(duration_s), 5.0))

@tool(
    name="motion_stop",
    description="Immediately stop all motion (zero velocity).",
)
async def motion_stop():
    pub = _cmd_vel_pub()
    z = Twist()
    for _ in range(3):
        pub.publish(z)
        await asyncio.sleep(0.05)
    return {"stopped": True}
