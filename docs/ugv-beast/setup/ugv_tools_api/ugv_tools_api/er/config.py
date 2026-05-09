"""Environment-resolved constants for the on-device ER agent.

All values are read once at import time. Missing required vars raise at import
so systemd surfaces the failure immediately rather than on first mission.
"""
import os


def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


GOOGLE_API_KEY: str = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_APPLICATION_CREDENTIALS: str = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS",
    "/home/ws/ugv_ws/ugv_tools_api/credentials/gcp.json",
)
GOOGLE_CLOUD_PROJECT: str = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION: str = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
USE_VERTEX: bool = os.environ.get("ER_USE_VERTEX", "").lower() in ("1", "true", "yes")

ER_MODEL_ID: str = os.environ.get("ER_MODEL_ID", "gemini-robotics-er-1.6-preview")
ER_MAX_STEPS: int = int(os.environ.get("ER_MAX_STEPS", "60"))
ER_PORT: int = int(os.environ.get("ER_PORT", "8082"))
ER_HOST: str = os.environ.get("ER_HOST", "0.0.0.0")

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
OPENAI_TTS_MODEL: str = os.environ.get("OPENAI_TTS_MODEL", "tts-1-hd")
OPENAI_TTS_VOICE: str = os.environ.get("OPENAI_TTS_VOICE", "onyx")
OPENAI_STT_MODEL: str = os.environ.get("OPENAI_STT_MODEL", "whisper-1")

TOOLS_API_URL: str = os.environ.get("TOOLS_API_URL", "http://localhost:8080")
SPEAK_URL: str = os.environ.get("SPEAK_URL", "http://localhost:8081/speak")

VERTEX_THINKING_BUDGET: int = int(os.environ.get("ER_THINKING_BUDGET", "8192"))
VERTEX_RPC_TIMEOUT_S: float = float(os.environ.get("ER_RPC_TIMEOUT_S", "60"))
TOOLS_HTTP_TIMEOUT_S: float = float(os.environ.get("ER_TOOLS_HTTP_TIMEOUT_S", "30"))
SPEAK_HTTP_TIMEOUT_S: float = float(os.environ.get("ER_SPEAK_HTTP_TIMEOUT_S", "5"))

RATE_LIMIT_BACKOFF_S: float = float(os.environ.get("ER_RATE_LIMIT_BACKOFF_S", "15"))
RATE_LIMIT_MAX_RETRIES: int = int(os.environ.get("ER_RATE_LIMIT_MAX_RETRIES", "3"))

# Embodied-wait: after nav_goto_point is accepted, the agent loop sleeps
# (no Vertex calls, no step burn) until nav.status is terminal or timeout.
NAV_WAIT_TIMEOUT_S: float = float(os.environ.get("ER_NAV_WAIT_TIMEOUT_S", "120"))
NAV_POLL_INTERVAL_S: float = float(os.environ.get("ER_NAV_POLL_INTERVAL_S", "2"))

# Embodied-wait for auto-explore: dormant while orchestrator state is EXPLORING.
# Wake on transition to IDLE/RETURNING/SAVING or timeout (default 10 min — typical
# small-room mapping takes 3-8 min; large spaces may need the operator to bump this).
EXPLORE_WAIT_TIMEOUT_S: float = float(os.environ.get("ER_EXPLORE_WAIT_TIMEOUT_S", "600"))
EXPLORE_POLL_INTERVAL_S: float = float(os.environ.get("ER_EXPLORE_POLL_INTERVAL_S", "10"))

SAFETY_FRONT_MIN_M: float = float(os.environ.get("ER_SAFETY_FRONT_MIN_M", "0.4"))
SAFETY_MAX_LINEAR: float = float(os.environ.get("ER_SAFETY_MAX_LINEAR", "0.15"))
SAFETY_MAX_ANGULAR: float = float(os.environ.get("ER_SAFETY_MAX_ANGULAR", "0.8"))

MISSION_GC_SECONDS: float = float(os.environ.get("ER_MISSION_GC_SECONDS", "3600"))
EVENTS_RING_SIZE: int = int(os.environ.get("ER_EVENTS_RING_SIZE", "50"))

RGB_WIDTH: int = 640
RGB_HEIGHT: int = 480
RGB_JPEG_QUALITY: int = 80

DEPTH_WIDTH: int = 640
DEPTH_HEIGHT: int = 400
DEPTH_JPEG_QUALITY: int = 80
DEPTH_CLIP_MIN_M: float = 0.3
DEPTH_CLIP_MAX_M: float = 5.0
DEPTH_RERENDER_MIN_AGE_MS: float = 300.0

LIDAR_IMAGE_SIZE: int = 512
LIDAR_METERS_PER_CELL: float = 0.04

# ER reads RGB from the FIXED OAK-D body camera (no pan/tilt). The pantilt
# camera at /camera/image/compressed is owned by Gemini Live exclusively;
# ER must rotate the chassis if it needs to look elsewhere.
RGB_TOPIC: str = "/oak/rgb/image_rect/compressed"
DEPTH_TOPIC: str = "/oak/stereo/depth"
SCAN_TOPIC: str = "/scan"

COSTMAP_TOPIC: str = "/global_costmap/costmap"
COSTMAP_IMAGE_SIZE: int = 512             # output PNG side in pixels
COSTMAP_METERS_PER_CELL: float = 0.08     # output image scale: 41m x 41m window
COSTMAP_RERENDER_MIN_AGE_MS: float = 500  # cache output if source unchanged

# Local costmap (Nav2 rolling window, ~5m on a side at chassis height) — what is
# IMMEDIATELY blocking the robot. Window matched roughly to typical local-costmap
# extent so most of the rendered image lands inside the source costmap.
LOCAL_COSTMAP_TOPIC: str = "/local_costmap/costmap"
LOCAL_COSTMAP_IMAGE_SIZE: int = 256       # output PNG side in pixels
LOCAL_COSTMAP_METERS_PER_CELL: float = 0.04  # 256 * 0.04 = ~10m window (covers 5m rolling costmap with margin)
LOCAL_COSTMAP_RERENDER_MIN_AGE_MS: float = 250  # local costmap updates fast, re-render often

# SLAM occupancy map from slam_toolbox — persistent room layout for spatial
# reasoning ("which room next?"). Rendered as the full map, downscaled to fit.
SLAM_MAP_TOPIC: str = "/map"
SLAM_MAP_MAX_IMAGE_SIZE: int = 512        # cap output side in pixels (preserve aspect)
SLAM_MAP_RERENDER_MIN_AGE_MS: float = 1500  # SLAM map updates rarely — coarser cache OK
