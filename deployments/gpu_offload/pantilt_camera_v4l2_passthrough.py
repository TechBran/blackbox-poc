"""Pan-tilt camera publisher — raw MJPEG passthrough.

The previous implementation used cv2.VideoCapture which DECODES the camera's
native MJPEG, then cv2.imencode which RE-ENCODES it to JPEG before publishing.
That's two CPU-heavy operations per frame for no net benefit.

This implementation reads raw MJPEG buffers directly from V4L2 and publishes
them as sensor_msgs/CompressedImage with format='jpeg'. CPU cost drops from
~23% to <3% (just memcpy + DDS publish overhead).

Tested on UGV Beast Jetson Orin Nano: Xitech UVC camera at /dev/video0,
640x480 MJPG. Camera supports up to 30 fps natively; we publish at 15 to
match prior cadence.
"""
from __future__ import annotations
from pathlib import Path

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage

from linuxpy.video.device import Device, VideoCapture, BufferType, PixelFormat


def resolve_v4l2_device(search_root: str = "/dev") -> str:
    def _key(p: Path) -> int:
        try:
            return int(p.name.removeprefix("video"))
        except ValueError:
            return 1 << 30
    candidates = sorted(Path(search_root).glob("video*"), key=_key)
    if not candidates:
        raise FileNotFoundError(f"No /dev/video* devices under {search_root}")
    return str(candidates[0])


class PantiltCameraNode(Node):
    def __init__(self, device: str | None = None, width: int = 640,
                 height: int = 480, fps: int = 15):
        super().__init__("ugv_pantilt_camera")
        self.device_path = device or resolve_v4l2_device()
        self.width = width
        self.height = height
        self.fps = fps

        self.dev = Device(self.device_path)
        self.dev.open()
        try:
            self.dev.set_format(BufferType.VIDEO_CAPTURE,
                                width, height, PixelFormat.MJPEG)
            self.dev.set_fps(BufferType.VIDEO_CAPTURE, fps)
            # linuxpy 0.24.0: VideoCapture is a separate class wrapping Device.
            # capture.open() arms the buffer (mmap) and calls stream_on.
            self.stream = VideoCapture(self.dev)
            self.stream.open()
            self._frame_iter = iter(self.stream)
        except Exception:
            self.dev.close()
            raise

        self.pub = self.create_publisher(
            CompressedImage, "/camera/image/compressed",
            qos_profile_sensor_data,
        )
        # V4L2 iterator blocks on select() until a frame is ready, so the timer
        # period only sets the worst-case wakeup rate. Run at fps (not 2x fps)
        # to avoid starving other rclpy callbacks (services/lifecycle/etc).
        self.timer = self.create_timer(1.0 / fps, self._tick)
        self.get_logger().info(
            f"pan-tilt camera (MJPEG passthrough): "
            f"{self.device_path} {width}x{height}@{fps}"
        )

    def _tick(self):
        try:
            frame = next(self._frame_iter)
        except StopIteration:
            # V4L2 iterators don't end in steady state — only on stream stop or
            # device removal (camera unplug, USB error). Re-raising lets systemd
            # restart the node, which re-enumerates /dev/video* fresh.
            self.get_logger().error(
                f"V4L2 stream ended on {self.device_path} — re-raising for restart"
            )
            raise
        except Exception as e:
            self.get_logger().warn(
                f"_tick error: {type(e).__name__}: {e}",
                throttle_duration_sec=2.0,
            )
            return

        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "pantilt_camera"
        msg.format = "jpeg"
        msg.data = bytes(frame.data)
        self.pub.publish(msg)


def main():
    rclpy.init()
    node = PantiltCameraNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        try:
            node.stream.close()
            node.dev.close()
        except Exception:
            pass
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
