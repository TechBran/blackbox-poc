"""OpenAI Computer Use Agent configuration."""

# Model
OPENAI_CUA_MODEL = "computer-use-preview"

# Supported environments
OPENAI_CUA_ENVIRONMENTS = ["browser", "mac", "windows", "ubuntu"]

# Agent loop limits
MAX_ITERATIONS = 50
SESSION_TIMEOUT = 300
MAX_WALL_CLOCK = 1800

# Tool type version
COMPUTER_USE_TOOL_TYPE = "computer_use_preview"
