"""Camera tools: list cameras + capture latest cached JPEG frame.

- `camera_list`: report all known cameras and whether each is currently
  publishing fresh frames (last frame age < 2.0s).
- `camera_snapshot`: return the latest cached JPEG frame from a named
  camera, either as base64 or a URL placeholder.

Both tools rely on the canonical subscriptions registered by RosBridge
(Task 3.2):
  - /camera/image/compressed       (pantilt camera)
  - /oak/rgb/image_rect/compressed (OAK-D RGB)
"""
import base64
import time

from ..ros_bridge import RosBridge
from ..registry import tool
from ..schema import ParamSchema


CAMERA_TOPICS = {
    "pantilt": "/camera/image/compressed",
    "oakd":    "/oak/rgb/image_rect/compressed",
}


@tool(
    name="camera_list",
    description="List all cameras available on the robot and whether each is currently streaming.",
)
async def camera_list():
    node = RosBridge.instance().node
    now = time.time()
    cams = {}
    for name, topic in CAMERA_TOPICS.items():
        c = node.get_latest(topic)
        cams[name] = {
            "topic": topic,
            "streaming": c is not None and (now - c[0]) < 2.0,
            "last_frame_age_s": round(now - c[0], 2) if c else None,
        }
    return {"cameras": list(cams.keys()), "details": cams}


@tool(
    name="camera_snapshot",
    description="Capture the latest JPEG frame from a camera, return base64 or a URL path.",
    parameters={
        "camera": ParamSchema(type="string", enum=["pantilt", "oakd"],
                              description="Which camera to snapshot."),
        "as_url": ParamSchema(type="boolean", default=False,
                              description="If true, return a GET URL like /snapshot/pantilt instead of base64."),
    },
    required=["camera"],
)
async def camera_snapshot(camera: str, as_url: bool = False):
    topic = CAMERA_TOPICS.get(camera)
    if not topic:
        return {"error": f"unknown camera {camera}"}
    cached = RosBridge.instance().node.get_latest(topic)
    if cached is None:
        return {"error": f"no frames yet on {topic}"}
    ts, msg = cached
    if as_url:
        return {"camera": camera, "url": f"/snapshot/{camera}", "age_s": round(time.time() - ts, 3)}
    return {
        "camera": camera,
        "format": "jpeg",
        "age_s": round(time.time() - ts, 3),
        "image_b64": base64.b64encode(bytes(msg.data)).decode("ascii"),
        "size_bytes": len(msg.data),
    }
