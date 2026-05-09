#!/usr/bin/env python3
"""
UGV Beast YOLO Object Detection Node
Runs YOLOv8n on both cameras simultaneously using OpenCV DNN + CUDA FP16.

Subscribes:
  /camera/image/compressed  (CompressedImage) - Pan-tilt camera (decode JPEG)
  /oak/rgb/image_rect       (Image)           - OAK-D RGB (raw BGR8)
  /oak/stereo/image_raw     (Image)           - OAK-D depth (for distance)

Publishes:
  /yolo/pantilt/detections        (Detection2DArray) - Pan-tilt detections
  /yolo/oak/detections            (Detection2DArray) - OAK-D detections (with distance)
  /yolo/pantilt/image/compressed  (CompressedImage)  - Annotated pan-tilt feed
  /yolo/oak/image/compressed      (CompressedImage)  - Annotated OAK-D feed
  /yolo/markers                   (MarkerArray)      - 3D markers for Foxglove

Single TensorRT engine shared between both camera pipelines.
"""

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage, Image, CameraInfo
from vision_msgs.msg import Detection2DArray, Detection2D, ObjectHypothesisWithPose
from visualization_msgs.msg import MarkerArray, Marker
from geometry_msgs.msg import Pose2D
from std_msgs.msg import ColorRGBA
from cv_bridge import CvBridge
import cv2
import numpy as np
import threading
import time

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coco_names import COCO_NAMES, COCO_COLORS


class YoloNode(Node):
    def __init__(self):
        super().__init__('ugv_yolo')

        # Parameters
        self.declare_parameter('model_path', '/home/ws/ugv_ws/models/yolov8n.onnx')
        self.declare_parameter('confidence_threshold', 0.45)
        self.declare_parameter('nms_threshold', 0.50)
        self.declare_parameter('enable_pantilt', True)
        self.declare_parameter('enable_oak', True)

        model_path = self.get_parameter('model_path').value
        self.conf_thresh = self.get_parameter('confidence_threshold').value
        self.nms_thresh = self.get_parameter('nms_threshold').value

        # Load OpenCV DNN model with CUDA FP16
        self.get_logger().info(f"Loading YOLO model: {model_path}")
        self.net = cv2.dnn.readNetFromONNX(model_path)
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA_FP16)

        # Warmup GPU
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        blob = cv2.dnn.blobFromImage(dummy, 1 / 255.0, (640, 640), swapRB=True)
        self.net.setInput(blob)
        self.net.forward()
        self.get_logger().info("YOLO model loaded + GPU warmed up")

        # Inference lock (OpenCV DNN is not thread-safe)
        self.net_lock = threading.Lock()

        self.bridge = CvBridge()

        # Depth frame cache (thread-safe via GIL for single assignment)
        self.latest_depth = None
        # Camera intrinsics for 3D projection
        self.fx = 461.0
        self.fy = 461.0
        self.cx_cam = 320.0
        self.cy_cam = 240.0

        # ── Publishers ──
        self.pantilt_det_pub = self.create_publisher(
            Detection2DArray, '/yolo/pantilt/detections', 10)
        self.oak_det_pub = self.create_publisher(
            Detection2DArray, '/yolo/oak/detections', 10)
        self.pantilt_img_pub = self.create_publisher(
            CompressedImage, '/yolo/pantilt/image/compressed', 10)
        self.oak_img_pub = self.create_publisher(
            CompressedImage, '/yolo/oak/image/compressed', 10)
        self.marker_pub = self.create_publisher(
            MarkerArray, '/yolo/markers', 10)

        # ── Subscribers ──
        if self.get_parameter('enable_pantilt').value:
            self.create_subscription(
                CompressedImage, '/camera/image/compressed',
                self.pantilt_callback, 10)
            self.get_logger().info("Pan-tilt camera YOLO enabled")

        if self.get_parameter('enable_oak').value:
            self.create_subscription(
                Image, '/oak/rgb/image_rect',
                self.oak_callback, 10)
            self.create_subscription(
                Image, '/oak/stereo/image_raw',
                self.depth_callback, 10)
            self.create_subscription(
                CameraInfo, '/oak/rgb/camera_info',
                self.camera_info_callback, 10)
            self.get_logger().info("OAK-D camera YOLO enabled")

        # Stats
        self.frame_count = 0
        self.stats_time = time.time()

        self.get_logger().info("YOLO node ready (OpenCV DNN + CUDA FP16)")

    # ── Callbacks ──────────────────────────────────────────────

    def pantilt_callback(self, msg):
        """Process pan-tilt camera (CompressedImage JPEG → decode → YOLO)."""
        np_arr = np.frombuffer(msg.data, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame is None:
            return

        detections = self._run_inference(frame)
        stamp = msg.header.stamp

        # Publish Detection2DArray
        det_msg = self._build_detection_array(detections, stamp, 'pt_camera_link')
        self.pantilt_det_pub.publish(det_msg)

        # Publish annotated image
        annotated = self._draw_boxes(frame, detections)
        self._publish_compressed(annotated, stamp, 'pt_camera_link', self.pantilt_img_pub)

    def oak_callback(self, msg):
        """Process OAK-D camera (raw BGR8 → YOLO + depth lookup)."""
        frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')

        detections = self._run_inference(frame)
        stamp = msg.header.stamp

        # Depth lookup for each detection
        depth_frame = self.latest_depth
        if depth_frame is not None:
            for det in detections:
                cx, cy = int(det['cx']), int(det['cy'])
                h, w = depth_frame.shape[:2]
                if 2 <= cx < w - 2 and 2 <= cy < h - 2:
                    patch = depth_frame[cy - 2:cy + 3, cx - 2:cx + 3]
                    valid = patch[patch > 0]
                    if len(valid) > 0:
                        det['distance_mm'] = int(np.median(valid))

        # Publish Detection2DArray (with distance)
        det_msg = self._build_detection_array(detections, stamp, '3d_camera_link')
        self.oak_det_pub.publish(det_msg)

        # Publish annotated image
        annotated = self._draw_boxes(frame, detections, show_distance=True)
        self._publish_compressed(annotated, stamp, '3d_camera_link', self.oak_img_pub)

        # Publish 3D markers for Foxglove
        if depth_frame is not None:
            self._publish_markers(detections, stamp)

        self._update_stats()

    def depth_callback(self, msg):
        """Cache latest depth frame."""
        self.latest_depth = self.bridge.imgmsg_to_cv2(msg, desired_encoding='16UC1')

    def camera_info_callback(self, msg):
        """Cache camera intrinsics for 3D projection."""
        if len(msg.k) >= 6:
            self.fx = msg.k[0]
            self.fy = msg.k[4]
            self.cx_cam = msg.k[2]
            self.cy_cam = msg.k[5]

    # ── Inference ──────────────────────────────────────────────

    def _run_inference(self, frame):
        """Run YOLOv8n inference on a BGR frame. Returns list of detection dicts."""
        h, w = frame.shape[:2]

        # Letterbox to 640x640
        scale = min(640 / w, 640 / h)
        nw, nh = int(w * scale), int(h * scale)
        dx, dy = (640 - nw) // 2, (640 - nh) // 2

        resized = cv2.resize(frame, (nw, nh))
        padded = np.full((640, 640, 3), 114, dtype=np.uint8)
        padded[dy:dy + nh, dx:dx + nw] = resized

        blob = cv2.dnn.blobFromImage(padded, 1 / 255.0, (640, 640), swapRB=True)

        with self.net_lock:
            self.net.setInput(blob)
            output = self.net.forward()  # shape: (1, 84, 8400)

        # Transpose to (8400, 84)
        preds = output[0].T  # (8400, 84)

        # Extract boxes (xywh) and class scores
        boxes_xywh = preds[:, :4]
        scores = preds[:, 4:]

        # Get best class per prediction
        class_ids = np.argmax(scores, axis=1)
        confidences = scores[np.arange(len(scores)), class_ids]

        # Filter by confidence
        mask = confidences >= self.conf_thresh
        boxes_xywh = boxes_xywh[mask]
        class_ids = class_ids[mask]
        confidences = confidences[mask]

        if len(boxes_xywh) == 0:
            return []

        # Convert xywh to xyxy for NMS
        x1 = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2
        y1 = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2
        x2 = boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2
        y2 = boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2

        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

        # NMS
        indices = cv2.dnn.NMSBoxes(
            boxes_xyxy.tolist(), confidences.tolist(),
            self.conf_thresh, self.nms_thresh
        )

        detections = []
        if len(indices) > 0:
            for i in indices.flatten():
                # Scale from 640x640 letterbox back to original frame
                bx1 = (boxes_xyxy[i, 0] - dx) / scale
                by1 = (boxes_xyxy[i, 1] - dy) / scale
                bx2 = (boxes_xyxy[i, 2] - dx) / scale
                by2 = (boxes_xyxy[i, 3] - dy) / scale

                # Clamp to frame bounds
                bx1 = max(0, min(w, bx1))
                by1 = max(0, min(h, by1))
                bx2 = max(0, min(w, bx2))
                by2 = max(0, min(h, by2))

                detections.append({
                    'class_id': int(class_ids[i]),
                    'class_name': COCO_NAMES[int(class_ids[i])],
                    'confidence': float(confidences[i]),
                    'x1': bx1, 'y1': by1, 'x2': bx2, 'y2': by2,
                    'cx': (bx1 + bx2) / 2, 'cy': (by1 + by2) / 2,
                    'w': bx2 - bx1, 'h': by2 - by1,
                    'distance_mm': 0,
                })

        return detections

    # ── Publishing ─────────────────────────────────────────────

    def _build_detection_array(self, detections, stamp, frame_id):
        """Build a Detection2DArray from detection dicts."""
        msg = Detection2DArray()
        msg.header.stamp = stamp
        msg.header.frame_id = frame_id

        for det in detections:
            d = Detection2D()
            d.bbox.center.position.x = det['cx']
            d.bbox.center.position.y = det['cy']
            d.bbox.size_x = det['w']
            d.bbox.size_y = det['h']

            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = str(det['class_id'])
            hyp.hypothesis.score = det['confidence']
            # Encode distance in pose.position.z (standard convention)
            if det['distance_mm'] > 0:
                hyp.pose.pose.position.z = det['distance_mm'] / 1000.0
            d.results.append(hyp)

            msg.detections.append(d)

        return msg

    def _draw_boxes(self, frame, detections, show_distance=False):
        """Draw bounding boxes with labels on frame."""
        annotated = frame.copy()
        for det in detections:
            color = COCO_COLORS[det['class_id']]
            x1, y1 = int(det['x1']), int(det['y1'])
            x2, y2 = int(det['x2']), int(det['y2'])

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)

            label = f"{det['class_name']} {det['confidence']:.0%}"
            if show_distance and det['distance_mm'] > 0:
                total_inches = det['distance_mm'] / 25.4
                feet = int(total_inches // 12)
                inches = int(total_inches % 12)
                label += f" {feet}'{inches}\""

            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(annotated, (x1, y1 - th - 6), (x1 + tw, y1), color, -1)
            cv2.putText(annotated, label, (x1, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
                        cv2.LINE_AA)

        return annotated

    def _publish_compressed(self, frame, stamp, frame_id, publisher):
        """Publish a compressed JPEG image."""
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        msg = CompressedImage()
        msg.header.stamp = stamp
        msg.header.frame_id = frame_id
        msg.format = 'jpeg'
        msg.data = buf.tobytes()
        publisher.publish(msg)

    def _publish_markers(self, detections, stamp):
        """Publish 3D markers for Foxglove visualization."""
        marker_array = MarkerArray()

        for i, det in enumerate(detections):
            if det['distance_mm'] <= 0:
                continue

            distance_m = det['distance_mm'] / 1000.0
            # Back-project 2D detection center to 3D using camera intrinsics
            x_3d = (det['cx'] - self.cx_cam) * distance_m / self.fx
            y_3d = (det['cy'] - self.cy_cam) * distance_m / self.fy
            z_3d = distance_m

            marker = Marker()
            marker.header.stamp = stamp
            marker.header.frame_id = '3d_camera_link'
            marker.ns = 'yolo'
            marker.id = i
            marker.type = Marker.CUBE
            marker.action = Marker.ADD
            marker.pose.position.x = z_3d   # camera Z = forward
            marker.pose.position.y = -x_3d  # camera X = right → ROS Y = left
            marker.pose.position.z = -y_3d  # camera Y = down → ROS Z = up
            marker.pose.orientation.w = 1.0

            # Size proportional to bounding box
            marker.scale.x = 0.1
            marker.scale.y = max(0.05, det['w'] / 640.0)
            marker.scale.z = max(0.05, det['h'] / 480.0)

            color = COCO_COLORS[det['class_id']]
            marker.color = ColorRGBA(
                r=color[2] / 255.0, g=color[1] / 255.0,
                b=color[0] / 255.0, a=0.7)
            marker.lifetime.sec = 0
            marker.lifetime.nanosec = 500000000  # 0.5s

            total_in = det['distance_mm'] / 25.4
            marker.text = f"{det['class_name']} {int(total_in // 12)}'{int(total_in % 12)}\""

            marker_array.markers.append(marker)

        # Clear old markers
        if not marker_array.markers:
            clear = Marker()
            clear.action = Marker.DELETEALL
            marker_array.markers.append(clear)

        self.marker_pub.publish(marker_array)

    def _update_stats(self):
        """Log FPS stats periodically."""
        self.frame_count += 1
        now = time.time()
        if now - self.stats_time >= 10.0:
            fps = self.frame_count / (now - self.stats_time)
            self.get_logger().info(f"YOLO: {fps:.1f} fps ({self.frame_count} frames in 10s)")
            self.frame_count = 0
            self.stats_time = now


def main(args=None):
    rclpy.init(args=args)
    node = YoloNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
