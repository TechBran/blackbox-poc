#!/usr/bin/env python3
"""BNO085 IMU driver node for UGV Beast.

Publishes sensor_msgs/Imu at 100 Hz on /imu/bno085/data using the BNO085's
on-chip Game Rotation Vector fusion (gyro + accel, no magnetometer).

Game RV is preferred over the full Rotation Vector because brushed motors
and the steel chassis create magnetic disturbances that corrupt the
magnetometer-aided yaw estimate. Game RV gives drift-free roll/pitch from
gravity reference and gyro-integrated yaw with bias correction running on
the BNO085's embedded Cortex-M0+.

Wiring (Jetson 40-pin → BNO085):
    Pin 17 (3.3V) -> VIN
    Pin 20 (GND)  -> GND
    Pin 27 (SDA)  -> SDA   (I2C0_SDA, exposed as /dev/i2c-1 on Jetson Orin)
    Pin 28 (SCL)  -> SCL   (I2C0_SCL)

The BNO085 ACKs at 0x4A by default. The Adafruit breakout pulls ADDR low
internally, so no jumper change required for default address.
"""
import os
# Force Blinka to use Jetson Orin Nano board layout — auto-detection has
# been spotty on Orin Nano dev kits. Override via env if a different SBC.
os.environ.setdefault("BLINKA_FORCEBOARD", "JETSON_ORIN_NANO")

import time

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Imu
from geometry_msgs.msg import Vector3, Quaternion

import smbus2
from adafruit_extended_bus import ExtendedI2C as I2C
from adafruit_bno08x.i2c import BNO08X_I2C
from adafruit_bno08x import (
    BNO_REPORT_GAME_ROTATION_VECTOR,
    BNO_REPORT_GYROSCOPE,
    BNO_REPORT_LINEAR_ACCELERATION,
)


def shtp_preflight(bus_num: int, address: int, total_timeout_s: float = 30.0,
                   logger=None) -> bool:
    """Wait for the BNO085 to emit a valid SHTP advertisement before we hand it
    to the Adafruit lib. Aggressively recovers from the stuck state via raw
    SHTP soft-resets if no advertisement appears within a few seconds.

    Returns True if the chip is in a known-good state, False if recovery failed.

    The stuck state (chip ACKs but returns 0x00 for all reads) happens when a
    prior process partial-handshaked the chip and exited mid-flow. Only a
    SHTP soft-reset on channel 1 (executable) clears it — and that only works
    when nothing else is contending the bus. So this preflight should run as
    the first thing to touch /dev/i2c-N after boot.
    """
    log = (logger.info if logger else print)
    warn = (logger.warning if logger else print)

    bus = smbus2.SMBus(bus_num)
    try:
        deadline = time.time() + total_timeout_s
        soft_reset_attempts = 0
        while time.time() < deadline:
            # Try to read the SHTP header. A valid advertisement starts with
            # length 4..1024, channel 0..5, and is non-zero/non-FF.
            try:
                hdr_msg = smbus2.i2c_msg.read(address, 4)
                bus.i2c_rdwr(hdr_msg)
                h = list(hdr_msg)
            except OSError:
                time.sleep(0.05)
                continue

            length = h[0] | ((h[1] & 0x7F) << 8)
            channel = h[2]
            if 4 <= length <= 1024 and 0 <= channel <= 5 and not all(b == 0 for b in h):
                log(f"SHTP preflight: chip ready (len={length} ch={channel})")
                # Drain the rest of this packet so Adafruit gets a clean queue.
                try:
                    rest = smbus2.i2c_msg.read(address, min(length, 256))
                    bus.i2c_rdwr(rest)
                except OSError:
                    pass
                return True

            # All zero or all 0xFF — chip is stuck or not yet ready.
            # Send a SHTP soft-reset (channel 1, opcode 0x01).
            soft_reset_attempts += 1
            try:
                rst = [0x05, 0x00, 0x01, soft_reset_attempts & 0xFF, 0x01]
                bus.i2c_rdwr(smbus2.i2c_msg.write(address, rst))
                warn(f"SHTP preflight: chip not ready (attempt {soft_reset_attempts}), sent soft-reset")
            except OSError as exc:
                warn(f"SHTP preflight: soft-reset write failed: {exc}")
            time.sleep(1.5)
        return False
    finally:
        bus.close()


# Variances per BNO085 datasheet, squared one-sigma deviations.
# Orientation σ ≈ 0.5° (Game RV, gyro/accel only) → 7.6e-5 rad².
ORIENTATION_COVARIANCE = [
    7.6e-5, 0.0, 0.0,
    0.0, 7.6e-5, 0.0,
    0.0, 0.0, 7.6e-5,
]
# Calibrated gyro noise σ ≈ 0.014°/s → 6e-8 (rad/s)².
ANGULAR_VELOCITY_COVARIANCE = [
    6e-8, 0.0, 0.0,
    0.0, 6e-8, 0.0,
    0.0, 0.0, 6e-8,
]
# Linear accel σ ≈ 0.05 m/s² → 2.5e-3 (m/s²)².
LINEAR_ACCEL_COVARIANCE = [
    2.5e-3, 0.0, 0.0,
    0.0, 2.5e-3, 0.0,
    0.0, 0.0, 2.5e-3,
]


class BNO085Node(Node):
    def __init__(self):
        super().__init__("bno085_imu")

        self.declare_parameter("frame_id", "base_imu_link")
        self.declare_parameter("i2c_bus", 1)
        self.declare_parameter("i2c_address", 0x4A)
        self.declare_parameter("publish_rate", 100.0)
        self.declare_parameter("topic", "/imu/bno085/data")
        # Set to a Jetson GPIO pin number (e.g., 12 for pin 32 = GPIO12) when
        # the BNO085's RESET line is physically wired. -1 = not wired.
        self.declare_parameter("reset_gpio", -1)
        # Watchdog: if no successful sensor read in this many seconds, exit.
        # The respawn wrapper in start_waveshare.sh restarts us, and the
        # SHTP preflight recovers the chip. 0 = disabled.
        self.declare_parameter("watchdog_s", 3.0)

        self.frame_id = self.get_parameter("frame_id").value
        self.bus_num = int(self.get_parameter("i2c_bus").value)
        self.addr = self.get_parameter("i2c_address").value
        self.rate = float(self.get_parameter("publish_rate").value)
        self.topic = self.get_parameter("topic").value
        self.reset_gpio = int(self.get_parameter("reset_gpio").value)
        self.watchdog_s = float(self.get_parameter("watchdog_s").value)

        self.get_logger().info(
            f"Opening /dev/i2c-{self.bus_num}, BNO085 @ 0x{self.addr:02x}"
        )

        # Pre-flight: wait for the chip to advertise itself BEFORE we hand the
        # bus to the Adafruit lib. This avoids the race where Adafruit's probe
        # and SHTP handshake fire while the chip is mid-boot, which puts the
        # chip into a stuck state that survives across our driver restarts.
        if not shtp_preflight(self.bus_num, self.addr, total_timeout_s=30.0,
                              logger=self.get_logger()):
            raise RuntimeError(
                "BNO085 did not produce a valid SHTP advertisement after 30s "
                "of recovery attempts. Chip likely needs hardware power cycle."
            )

        i2c = I2C(self.bus_num)
        # If RESET is wired, hand it to the lib so it can hard-reset on init
        # — eliminates the SHTP-stuck failure mode entirely.
        if self.reset_gpio >= 0:
            try:
                from digitalio import DigitalInOut
                import board as _board
                pin_attr = getattr(_board, f"D{self.reset_gpio}")
                rst = DigitalInOut(pin_attr)
                self.bno = BNO08X_I2C(i2c, address=self.addr, reset=rst)
                self.get_logger().info(
                    f"BNO085 opened with hardware RESET on GPIO{self.reset_gpio}"
                )
            except Exception as exc:
                self.get_logger().warning(
                    f"reset_gpio={self.reset_gpio} requested but failed ({exc}); "
                    f"falling back to no-RESET init"
                )
                self.bno = BNO08X_I2C(i2c, address=self.addr)
        else:
            self.bno = BNO08X_I2C(i2c, address=self.addr)
        time.sleep(0.5)  # let Adafruit lib finish its own init

        for feat_name, feat_id in (
            ("Game Rotation Vector", BNO_REPORT_GAME_ROTATION_VECTOR),
            ("Gyroscope", BNO_REPORT_GYROSCOPE),
            ("Linear Acceleration", BNO_REPORT_LINEAR_ACCELERATION),
        ):
            self._enable_feature_with_retry(feat_name, feat_id)
            time.sleep(0.2)

        qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
        )
        self.pub = self.create_publisher(Imu, self.topic, qos)

        period = 1.0 / self.rate
        self.timer = self.create_timer(period, self.tick)
        self._read_failures = 0
        self._last_good_read = time.monotonic()
        self.get_logger().info(
            f"Publishing on {self.topic} at {self.rate:.1f} Hz, "
            f"frame_id={self.frame_id}, watchdog={self.watchdog_s}s"
        )

    def _enable_feature_with_retry(self, name, feature_id, attempts=6):
        last_exc = None
        for i in range(1, attempts + 1):
            try:
                self.bno.enable_feature(feature_id)
                self.get_logger().info(f"Enabled {name} (attempt {i})")
                return
            except Exception as exc:
                last_exc = exc
                self.get_logger().warning(
                    f"Enable {name} failed (attempt {i}/{attempts}): {exc}"
                )
                time.sleep(0.4)
        raise RuntimeError(f"Could not enable {name} after {attempts} tries: {last_exc}")

    def tick(self):
        try:
            qi, qj, qk, qr = self.bno.game_quaternion
            wx, wy, wz = self.bno.gyro
            ax, ay, az = self.bno.linear_acceleration
            self._read_failures = 0
            self._last_good_read = time.monotonic()
        except Exception as exc:
            self._read_failures += 1
            self.get_logger().warning(
                f"BNO085 read failed ({self._read_failures}): {exc}",
                throttle_duration_sec=2.0,
            )
            # Watchdog: if no successful read for watchdog_s seconds, the
            # chip is dead/stuck — exit so the respawn wrapper restarts us
            # with a fresh SHTP preflight (which can recover the chip).
            if self.watchdog_s > 0 and \
                    (time.monotonic() - self._last_good_read) > self.watchdog_s:
                self.get_logger().error(
                    f"Watchdog: no successful read in {self.watchdog_s}s. "
                    f"Exiting so respawn wrapper can re-run preflight."
                )
                raise SystemExit(2)
            return

        msg = Imu()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        msg.orientation = Quaternion(
            x=float(qi), y=float(qj), z=float(qk), w=float(qr)
        )
        msg.orientation_covariance = ORIENTATION_COVARIANCE
        msg.angular_velocity = Vector3(x=float(wx), y=float(wy), z=float(wz))
        msg.angular_velocity_covariance = ANGULAR_VELOCITY_COVARIANCE
        msg.linear_acceleration = Vector3(x=float(ax), y=float(ay), z=float(az))
        msg.linear_acceleration_covariance = LINEAR_ACCEL_COVARIANCE
        self.pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = BNO085Node()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
