"""IMU ZUPT preprocessor node.

Implements Zero Velocity Update for the OAK-D IMU stream. Subscribes to
/oak/imu (already bias-corrected by oakd_camera.py boot calibration),
/odom_wheel (EKF wheel input pre-fusion), and /cmd_vel (commanded velocity).
Detects when robot is stationary; when so, republishes IMU with
angular_velocity zeroed and tight covariance, AND updates a slow EMA of
gyro bias to track thermal drift. Otherwise passes through with current
EMA-bias subtracted.

EKF subscribes to /oak/imu_zupt instead of /oak/imu after Task 8.
"""
from __future__ import annotations
from collections import deque

import math
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from sensor_msgs.msg import Imu
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist


class StationaryDetector:
    """Debounced stationary-vs-moving detector.

    Caller feeds (linear, angular) tuples (from cmd_vel + odom_wheel
    combined). After `debounce_frames` consecutive frames below `threshold`,
    is_stationary() returns True. Any single frame above threshold flips
    back to moving immediately (no debounce on the leading edge — protects
    against incorporating motion into the bias estimate).
    """

    def __init__(self, *, threshold: float = 0.02, debounce_frames: int = 5) -> None:
        self._threshold = threshold
        self._debounce_frames = debounce_frames
        self._zero_count = 0  # consecutive below-threshold frames
        self._stationary = False

    def update(self, *, linear: float, angular: float) -> None:
        """Feed one observation. Linear/angular can be cmd_vel.linear.x +
        odom.twist.linear.x (already-fused) or independent — caller's choice."""
        if abs(linear) > self._threshold or abs(angular) > self._threshold:
            self._zero_count = 0
            self._stationary = False
        else:
            self._zero_count += 1
            if self._zero_count >= self._debounce_frames:
                self._stationary = True

    def is_stationary(self) -> bool:
        return self._stationary


class BiasEma:
    """Exponential-moving-average bias estimator.

    Updates only when caller decides — not coupled to stationary detector
    (caller composes them). new = alpha * sample + (1 - alpha) * current.
    Lower alpha = slower tracking = more noise immunity. alpha=0.01 at
    100 Hz means time constant ~1 second; over a 30-minute mission, the
    estimate adapts smoothly to thermal drift without absorbing transient
    motion noise.
    """

    def __init__(
        self,
        *,
        alpha: float = 0.01,
        seed: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        if not 0.0 <= alpha <= 1.0:
            raise ValueError(f"alpha must be in [0, 1], got {alpha}")
        self._alpha = alpha
        self._x, self._y, self._z = seed

    def update(self, sample: tuple[float, float, float]) -> None:
        """Pull one (gx, gy, gz) toward sample by alpha."""
        sx, sy, sz = sample
        a = self._alpha
        self._x = a * sx + (1.0 - a) * self._x
        self._y = a * sy + (1.0 - a) * self._y
        self._z = a * sz + (1.0 - a) * self._z

    def current(self) -> tuple[float, float, float]:
        return (self._x, self._y, self._z)


class ImuPreprocessor:
    """Composes StationaryDetector + BiasEma + IMU pass-through logic.

    Pure logic, no ROS. Caller drives it via observe_motion() (combined
    cmd_vel + odom_wheel signal) and process_gyro() (raw gyro tuple).
    Returns (gyro_output, locked) — gyro_output is what the node should
    publish; locked is True iff stationary (caller sets tight covariance).
    """

    def __init__(
        self,
        *,
        threshold: float = 0.02,
        debounce_frames: int = 5,
        alpha: float = 0.01,
        seed_bias: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        self._detector = StationaryDetector(
            threshold=threshold, debounce_frames=debounce_frames,
        )
        self._bias = BiasEma(alpha=alpha, seed=seed_bias)

    def observe_motion(self, *, linear: float, angular: float) -> None:
        """Caller passes combined motion signal."""
        self._detector.update(linear=linear, angular=angular)

    def process_gyro(
        self, sample: tuple[float, float, float]
    ) -> tuple[tuple[float, float, float], bool]:
        """Returns ((gx, gy, gz), locked). locked=True means downstream
        consumer should use tight covariance. When stationary, also updates
        bias EMA from this sample."""
        if self._detector.is_stationary():
            self._bias.update(sample)
            return ((0.0, 0.0, 0.0), True)
        bx, by, bz = self._bias.current()
        sx, sy, sz = sample
        return ((sx - bx, sy - by, sz - bz), False)

    def current_bias(self) -> tuple[float, float, float]:
        return self._bias.current()


# Tight covariance to advertise when locked (stationary). 1e-6 rad²/s² is
# below sensor floor; EKF will trust the (0,0,0) yaw rate fully and not
# integrate any bias residual. When moving, the input's covariance is
# preserved verbatim.
_LOCKED_COV_DIAG = 1e-6


class ImuZuptNode(Node):
    def __init__(self) -> None:
        super().__init__("imu_zupt_node")
        # Parameters with defaults tuned for UGV Beast (see plan notes).
        self.declare_parameter("threshold", 0.02)
        self.declare_parameter("debounce_frames", 5)
        self.declare_parameter("alpha", 0.01)
        self.declare_parameter("imu_in_topic", "/oak/imu")
        self.declare_parameter("imu_out_topic", "/oak/imu_zupt")
        self.declare_parameter("odom_topic", "/odom_wheel")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")

        threshold = self.get_parameter("threshold").value
        debounce = self.get_parameter("debounce_frames").value
        alpha = self.get_parameter("alpha").value
        in_topic = self.get_parameter("imu_in_topic").value
        out_topic = self.get_parameter("imu_out_topic").value
        odom_topic = self.get_parameter("odom_topic").value
        cmd_vel_topic = self.get_parameter("cmd_vel_topic").value

        self._pre = ImuPreprocessor(
            threshold=threshold, debounce_frames=debounce, alpha=alpha,
        )
        # Cache last-seen motion signals; aggregate into one observe_motion
        # per IMU sample so detector cadence matches IMU rate.
        self._last_cmd_vel = (0.0, 0.0)   # (linear, angular)
        self._last_wheel = (0.0, 0.0)

        # /oak/imu uses BEST_EFFORT QoS — must match.
        sensor_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        # /odom_wheel and /cmd_vel are RELIABLE/VOLATILE (default).
        default_qos = 10

        self._pub = self.create_publisher(Imu, out_topic, sensor_qos)
        self.create_subscription(Imu, in_topic, self._on_imu, sensor_qos)
        self.create_subscription(
            Odometry, odom_topic, self._on_odom, default_qos,
        )
        self.create_subscription(
            Twist, cmd_vel_topic, self._on_cmd_vel, default_qos,
        )

        # Diagnostic ticker — log bias estimate every 10 seconds.
        self._diag_timer = self.create_timer(10.0, self._log_diag)
        self._stationary_since = None

        self.get_logger().info(
            f"imu_zupt_node started: in={in_topic} out={out_topic} "
            f"threshold={threshold} debounce={debounce} alpha={alpha}"
        )

    def _on_cmd_vel(self, msg: Twist) -> None:
        self._last_cmd_vel = (msg.linear.x, msg.angular.z)

    def _on_odom(self, msg: Odometry) -> None:
        self._last_wheel = (msg.twist.twist.linear.x, msg.twist.twist.angular.z)

    def _on_imu(self, msg: Imu) -> None:
        # Aggregate motion: max-magnitude across cmd_vel + wheel for each axis.
        linear = max(abs(self._last_cmd_vel[0]), abs(self._last_wheel[0]))
        angular = max(abs(self._last_cmd_vel[1]), abs(self._last_wheel[1]))
        self._pre.observe_motion(linear=linear, angular=angular)

        sample = (
            msg.angular_velocity.x,
            msg.angular_velocity.y,
            msg.angular_velocity.z,
        )
        (gx, gy, gz), locked = self._pre.process_gyro(sample)

        out = Imu()
        out.header = msg.header
        out.orientation = msg.orientation
        out.orientation_covariance = msg.orientation_covariance
        out.angular_velocity.x = gx
        out.angular_velocity.y = gy
        out.angular_velocity.z = gz
        if locked:
            out.angular_velocity_covariance = [
                _LOCKED_COV_DIAG, 0.0, 0.0,
                0.0, _LOCKED_COV_DIAG, 0.0,
                0.0, 0.0, _LOCKED_COV_DIAG,
            ]
        else:
            out.angular_velocity_covariance = msg.angular_velocity_covariance
        out.linear_acceleration = msg.linear_acceleration
        out.linear_acceleration_covariance = msg.linear_acceleration_covariance
        self._pub.publish(out)

    def _log_diag(self) -> None:
        bx, by, bz = self._pre.current_bias()
        stat = self._pre._detector.is_stationary()
        self.get_logger().info(
            f"[zupt] state={'STATIONARY' if stat else 'MOVING'} "
            f"bias=({bx:+.5f}, {by:+.5f}, {bz:+.5f}) rad/s "
            f"yaw_axis={by * 57.3:+.3f} deg/s"
        )


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ImuZuptNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
