#!/usr/bin/env python3
"""
UGV Beast OAK-D Lite Depth Node (RTAB-Map Compatible, Native Resolution)
Uses depthai 3.5 Python SDK directly.

Resolution: 640x480 (OV7251 mono native max), depth aligned to RGB.
USB 2.0: ~184 Mbps at 15fps raw — pipeline auto-throttles to fit bandwidth.

Publishes (Vizanti viewing - downscaled compressed):
  /depth/image/compressed  (CompressedImage) - Colorized depth map (JET colormap)
  /depth/rgb/compressed    (CompressedImage) - RGB color from OAK-D front camera

Publishes (RTAB-Map 3D SLAM - full resolution raw):
  /oak/rgb/image_rect      (Image)       - Raw RGB 640x480
  /oak/stereo/image_raw    (Image)       - Raw 16UC1 depth 640x480 in millimeters
  /oak/rgb/camera_info     (CameraInfo)  - Intrinsic calibration from EEPROM
"""

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage, Image, CameraInfo
from cv_bridge import CvBridge
import depthai as dai
import cv2
import numpy as np
import time
import threading
import os

# Native mono resolution (OV7251 max)
IMG_W = 640
IMG_H = 480


class DepthNode(Node):
    def __init__(self):
        super().__init__('ugv_depth')

        # Compressed publishers (Vizanti viewing)
        self.depth_pub = self.create_publisher(CompressedImage, '/depth/image/compressed', 10)
        self.rgb_pub = self.create_publisher(CompressedImage, '/depth/rgb/compressed', 10)

        # Raw publishers (RTAB-Map 3D SLAM)
        self.rgb_raw_pub = self.create_publisher(Image, '/oak/rgb/image_rect', 10)
        self.depth_raw_pub = self.create_publisher(Image, '/oak/stereo/image_raw', 10)
        self.camera_info_pub = self.create_publisher(CameraInfo, '/oak/rgb/camera_info', 10)
        self.depth_camera_info_pub = self.create_publisher(CameraInfo, '/oak/stereo/camera_info', 10)

        self.bridge = CvBridge()

        # Clear stale locks
        os.system("rm -rf /root/.cache/depthai/crashdumps/ 2>/dev/null")
        os.system("rm -rf /home/ws/ugv_ws/.cache/depthai/crashdumps/ 2>/dev/null")

        # Read calibration from device EEPROM
        self.camera_info_template = None
        self._read_calibration()

        self.running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        self.get_logger().info(
            f"OAK-D Lite depth node starting ({IMG_W}x{IMG_H} native, RTAB-Map compatible)..."
        )

    def _read_calibration(self):
        """Read intrinsic calibration from OAK-D Lite EEPROM before pipeline starts."""
        try:
            with dai.Device() as device:
                calib = device.readCalibration()
                K = calib.getCameraIntrinsics(dai.CameraBoardSocket.CAM_A, IMG_W, IMG_H)
                D = calib.getDistortionCoefficients(dai.CameraBoardSocket.CAM_A)

                info = CameraInfo()
                info.width = IMG_W
                info.height = IMG_H
                info.distortion_model = 'plumb_bob'

                K_flat = [float(K[i][j]) for i in range(3) for j in range(3)]
                info.k = K_flat
                info.d = [float(d) for d in D[:5]]
                info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]

                fx, fy = K_flat[0], K_flat[4]
                cx, cy = K_flat[2], K_flat[5]
                info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]

                self.camera_info_template = info
                self.get_logger().info(
                    f"Calibration at {IMG_W}x{IMG_H}: "
                    f"fx={fx:.1f} fy={fy:.1f} cx={cx:.1f} cy={cy:.1f}"
                )

        except Exception as e:
            self.get_logger().error(f"Failed to read calibration: {e}")
            self.get_logger().warn(f"Using approximate calibration (73° HFOV at {IMG_W}x{IMG_H})")
            info = CameraInfo()
            info.width = IMG_W
            info.height = IMG_H
            info.distortion_model = 'plumb_bob'
            fx = fy = IMG_W * 0.72  # ~461 at 640
            cx, cy = IMG_W / 2.0, IMG_H / 2.0
            info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
            info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
            info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
            info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
            self.camera_info_template = info

    def _capture_loop(self):
        """Main capture loop — builds depthai pipeline at native resolution."""
        time.sleep(1.5)  # Let USB settle after calibration read released device

        try:
            pipeline = dai.Pipeline()

            cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
            mono_l = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B)
            mono_r = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)

            stereo = pipeline.create(dai.node.StereoDepth)
            stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.ROBOTICS)
            stereo.setLeftRightCheck(True)
            stereo.initialConfig.setMedianFilter(dai.MedianFilter.MEDIAN_OFF)
            # Align depth to RGB camera for RTAB-Map pixel correspondence
            stereo.setDepthAlign(dai.CameraBoardSocket.CAM_A)
            # Must be multiple of 16 (640/16=40, 480/16=30 ✓)
            stereo.setOutputSize(IMG_W, IMG_H)

            # OV7251 native: 640x480 (mono cameras)
            mono_l.requestOutput((IMG_W, IMG_H)).link(stereo.left)
            mono_r.requestOutput((IMG_W, IMG_H)).link(stereo.right)

            # RGB at same resolution as depth for alignment
            rgb_out = cam.requestOutput((IMG_W, IMG_H), dai.ImgFrame.Type.BGR888p)
            depth_out = stereo.depth

            # Create queues BEFORE start (depthai 3.5 requirement)
            # Non-blocking queues drop frames if USB can't keep up — natural throttling
            rgb_q = rgb_out.createOutputQueue(maxSize=4, blocking=False)
            depth_q = depth_out.createOutputQueue(maxSize=4, blocking=False)

            pipeline.start()
            self.get_logger().info(
                f"OAK-D pipeline started (RGB {IMG_W}x{IMG_H} + "
                f"Depth {IMG_W}x{IMG_H} aligned to RGB)"
            )

            frame_count = 0
            fps_time = time.time()
            fps_count = 0

            while self.running and rclpy.ok():
                stamp = self.get_clock().now().to_msg()

                # ── RGB ──
                rgb_msg = rgb_q.tryGet()
                if rgb_msg is not None:
                    rgb_frame = rgb_msg.getCvFrame()

                    # Compressed for Vizanti (downscale to 320x240 for bandwidth)
                    rgb_small = cv2.resize(rgb_frame, (320, 240))
                    self._publish_compressed(self.rgb_pub, rgb_small, '3d_camera_link', 60)

                    # Raw full-res for RTAB-Map
                    raw_img = self.bridge.cv2_to_imgmsg(rgb_frame, encoding='bgr8')
                    raw_img.header.stamp = stamp
                    raw_img.header.frame_id = '3d_camera_link'
                    self.rgb_raw_pub.publish(raw_img)

                    # CameraInfo synced with RGB
                    if self.camera_info_template is not None:
                        info = self.camera_info_template
                        info.header.stamp = stamp
                        info.header.frame_id = '3d_camera_link'
                        self.camera_info_pub.publish(info)
                        self.depth_camera_info_pub.publish(info)

                # ── Depth ──
                depth_msg = depth_q.tryGet()
                if depth_msg is not None:
                    depth_frame = depth_msg.getFrame()

                    # Compressed colorized for Vizanti (downscale)
                    depth_color = cv2.normalize(depth_frame, None, 0, 255, cv2.NORM_MINMAX)
                    depth_color = depth_color.astype(np.uint8)
                    depth_color = cv2.applyColorMap(depth_color, cv2.COLORMAP_JET)
                    depth_small = cv2.resize(depth_color, (320, 240))
                    self._publish_compressed(self.depth_pub, depth_small, '3d_camera_link', 50)

                    # Raw full-res 16UC1 depth for RTAB-Map
                    depth_raw = self.bridge.cv2_to_imgmsg(depth_frame, encoding='16UC1')
                    depth_raw.header.stamp = stamp
                    depth_raw.header.frame_id = '3d_camera_link'
                    self.depth_raw_pub.publish(depth_raw)

                    frame_count += 1
                    fps_count += 1

                    # Stats every ~5 seconds
                    now = time.time()
                    if now - fps_time >= 5.0:
                        fps = fps_count / (now - fps_time)
                        v = depth_frame[depth_frame > 0]
                        depth_coverage = (np.count_nonzero(depth_frame) / depth_frame.size) * 100
                        if len(v) > 0:
                            self.get_logger().info(
                                f"{IMG_W}x{IMG_H} @ {fps:.1f}fps | "
                                f"Depth: {v.min()}-{v.max()}mm (mean {int(v.mean())}mm) | "
                                f"Coverage: {depth_coverage:.0f}% | frames: {frame_count}"
                            )
                        fps_time = now
                        fps_count = 0

                time.sleep(0.005)

            pipeline.stop()

        except Exception as e:
            self.get_logger().error(f"OAK-D Lite error: {e}")

    def _publish_compressed(self, publisher, frame, frame_id, quality):
        """Publish a compressed JPEG image (Vizanti path)."""
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = frame_id
        msg.format = 'jpeg'
        msg.data = buf.tobytes()
        publisher.publish(msg)

    def destroy_node(self):
        self.running = False
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = DepthNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
