"""Gemini Computer Use configuration."""

# Models that support Computer Use
GEMINI_CU_MODEL = "gemini-2.5-computer-use-preview-10-2025"
GEMINI_CU_MODEL_PRO = "gemini-3-pro-preview"
GEMINI_CU_MODEL_FLASH = "gemini-3-flash-preview"

# Default model for CU tasks
DEFAULT_CU_MODEL = GEMINI_CU_MODEL

# Coordinate system — Gemini CU uses normalized coordinates 0-999
GEMINI_COORD_MAX = 999

# Agent loop limits
MAX_ITERATIONS = 50
SESSION_TIMEOUT = 300  # seconds
MAX_WALL_CLOCK = 1800  # 30 minutes

# Predefined browser functions to exclude in Android mode
BROWSER_ONLY_FUNCTIONS = [
    "open_web_browser", "navigate", "go_back", "go_forward",
    "search", "scroll_document"
]

# Screenshot settings
SCREENSHOT_MIME_TYPE = "image/png"
RECOMMENDED_RESOLUTION = (1440, 900)

# Gemini CU display dimensions — what the model sees
# This must match the screenshot resolution sent to the API
GEMINI_CU_WIDTH = 1440
GEMINI_CU_HEIGHT = 900
