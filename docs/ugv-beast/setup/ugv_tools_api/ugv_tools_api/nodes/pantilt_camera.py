"""Publishes /dev/videoN frames as sensor_msgs/CompressedImage on /camera/image/compressed."""
from pathlib import Path
import cv2
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage

def resolve_v4l2_device(search_root: str = "/dev") -> str:
    def _key(p: Path) -> int:
        try: return int(p.name.removeprefix("video"))
        except ValueError: return 1 << 30
    candidates = sorted(Path(search_root).glob("video*"), key=_key)
    if not candidates:
        raise FileNotFoundError(f"No /dev/video* devices under {search_root}")
    return str(candidates[0])

class PantiltCameraNode(Node):
    def __init__(self, device: str | None = None, width: int = 640, height: int = 480, fps: int = 15):
        super().__init__("ugv_pantilt_camera")
        self.device = device or resolve_v4l2_device()
        self.cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if not self.cap.isOpened():
            raise RuntimeError(f"Failed to open {self.device} (busy or invalid?)")
        self.pub = self.create_publisher(CompressedImage, "/camera/image/compressed", qos_profile_sensor_data)
        self.timer = self.create_timer(1.0 / fps, self._tick)
        self._fail_count = 0
        self.get_logger().info(f"pan-tilt camera: {self.device} {width}x{height}@{fps}")

    def _tick(self):
        try:
            ok, frame = self.cap.read()
            if not ok:
                self._fail_count += 1
                if self._fail_count % 30 == 1:
                    self.get_logger().warn(f"cap.read() failed {self._fail_count}x on {self.device}")
                return
            self._fail_count = 0
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                return
            msg = CompressedImage()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = "pantilt_camera"
            msg.format = "jpeg"
            msg.data = buf.tobytes()
            self.pub.publish(msg)
        except Exception as e:
            self.get_logger().warn(f"_tick error: {e}", throttle_duration_sec=2.0)

def main():
    rclpy.init()
    node = PantiltCameraNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.cap.release()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == "__main__":
    main()
