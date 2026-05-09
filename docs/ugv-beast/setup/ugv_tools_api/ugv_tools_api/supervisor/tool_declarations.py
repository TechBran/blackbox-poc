"""Gemini Live FunctionDeclaration objects for the UGV Beast supervisor.

Schema-only; execution lives in tool_handlers.py. Keeping these split lets
the supervisor tool surface be inspected and reused (e.g., exported as a
BlackBox tool schema) without importing the handler's runtime deps.

Design principle: every advertised default/range is enforced in the SCHEMA,
not just described in prose. Gemini honors schema defaults (fills in the
value when the model omits the param) and range constraints (reject out-of-
range values at the model). Prose-only "defaults" are a footgun because
the handler receives {} and has to remember to backfill.
"""
from google.genai import types


def _obj(properties: dict, required=None) -> types.Schema:
    return types.Schema(
        type=types.Type.OBJECT,
        properties=properties,
        required=list(required or []),
    )


def _str(desc: str) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=desc)


def _num(desc: str, minimum=None, maximum=None) -> types.Schema:
    kwargs = dict(type=types.Type.NUMBER, description=desc)
    if minimum is not None:
        kwargs["minimum"] = minimum
    if maximum is not None:
        kwargs["maximum"] = maximum
    return types.Schema(**kwargs)


def _bool(desc: str, default=None) -> types.Schema:
    kwargs = dict(type=types.Type.BOOLEAN, description=desc)
    if default is not None:
        kwargs["default"] = default
    return types.Schema(**kwargs)


def _enum(desc: str, values: list, default=None) -> types.Schema:
    kwargs = dict(type=types.Type.STRING, description=desc, enum=values)
    if default is not None:
        kwargs["default"] = default
    return types.Schema(**kwargs)


GET_ROBOT_STATE = types.FunctionDeclaration(
    name="get_robot_state",
    description=(
        "Read the robot's current fused pose (x, y, yaw), linear and "
        "angular velocity, and 8-sector lidar minimum distances. Use when "
        "the operator asks where the robot is, which way it is facing, or "
        "whether something is close to it."
    ),
    parameters=_obj({}),
)

GET_CAMERA_VIEW = types.FunctionDeclaration(
    name="get_camera_view",
    description=(
        "Push a fresh pantilt-camera frame into your perception stream "
        "so you can see it on this turn. Use when you need a higher-"
        "quality look than the ambient ~0.33 FPS feed gives, or to "
        "peek at something specific between watch frames (e.g., to "
        "verify a landmark, check whether a path is clear, or "
        "investigate a stall). The image arrives as realtime video "
        "perception — your vision encoder will incorporate it into "
        "the same response, no extra round-trip needed. The tool "
        "result itself is a small JSON ack; the bytes ride the "
        "realtime_input channel."
    ),
    parameters=_obj({}),
)

GET_SLAM_MAP_VIEW = types.FunctionDeclaration(
    name="get_slam_map_view",
    description=(
        "Fetch a top-down view of the SLAM map (the persistent room "
        "layout the robot has built so far) and push it into your "
        "perception stream. A cyan dot + heading line marks your "
        "current position. Use when the operator asks about cross-"
        "room navigation, when you want to suggest going to an "
        "unmapped area, or when you need to remember whether you have "
        "already visited somewhere — e.g. 'is there another room I "
        "haven't been to?', 'which room is the kitchen in relative "
        "to me?', 'have I mapped the area near the door?'. On-demand "
        "only: the map only changes when SLAM updates it, so there is "
        "no value in calling this repeatedly. The tool result itself "
        "is a small JSON ack with map dimensions and your position; "
        "the actual image arrives as realtime video perception your "
        "vision encoder reads on the same turn."
    ),
    parameters=_obj({}),
)

GET_COSTMAP_VIEW = types.FunctionDeclaration(
    name="get_costmap_view",
    description=(
        "Render the current Nav2 global costmap as an image and return "
        "it for visual inspection. Use when you need to understand where "
        "the robot is on the map, where obstacles have been recorded, or "
        "why a navigation attempt failed."
    ),
    parameters=_obj({}),
)

DISPATCH_ER_MISSION = types.FunctionDeclaration(
    name="dispatch_er_mission",
    description=(
        "Send a natural-language mission to the robot's on-device "
        "execution agent (Gemini Robotics-ER). The ER agent will translate "
        "the mission into Nav2 goals and tool calls and execute one "
        "complete task before returning control. Examples: 'Drive to the "
        "kitchen and stop there', 'Map this room', 'Inspect the charger.' "
        "This call returns immediately; mission progress is streamed back "
        "as it happens. By default, dispatching a new mission ABORTS any "
        "in-flight mission and starts fresh — the operator nearly always "
        "wants the latest instruction to take over."
    ),
    parameters=_obj(
        {
            "mission": _str(
                "Plain-English mission instruction. Be specific about "
                "the goal and any constraints (e.g., 'slow speed', 'stop "
                "and report when you see a cat')."
            ),
            "replace_current": _bool(
                "If true (default), abort any in-flight mission before "
                "dispatching the new one. Set false ONLY if the operator "
                "explicitly says to queue or wait — almost never the case.",
                default=True,
            ),
        },
        required=["mission"],
    ),
    # NON_BLOCKING: the model can keep talking while ER works. Progress
    # streams back via Task 3's mission_poller as additional FunctionResponse
    # parts under the same id (will_continue=True with SILENT/WHEN_IDLE,
    # then will_continue=False on terminal).
    behavior=types.Behavior.NON_BLOCKING,
)

CANCEL_ER_MISSION = types.FunctionDeclaration(
    name="cancel_er_mission",
    description=(
        "Abort the currently running ER mission. Use when the operator "
        "says 'stop' or 'cancel', or when you observe the mission is "
        "going wrong (e.g., robot is stuck, lost, or heading somewhere "
        "it shouldn't)."
    ),
    parameters=_obj(
        {"reason": _str("Short explanation of why the mission is being canceled.")},
        required=["reason"],
    ),
)

GET_ER_MISSION_STATUS = types.FunctionDeclaration(
    name="get_er_mission_status",
    description=(
        "Read the state of the currently running ER mission: id, status, "
        "last text the ER agent said, and recent events. Use when the "
        "operator asks how the mission is going."
    ),
    parameters=_obj({}),
)

EMERGENCY_STOP = types.FunctionDeclaration(
    name="emergency_stop",
    description=(
        "Immediately halt all robot motion at the firmware level. "
        "Bypasses ER. Use if you see imminent collision, the robot is "
        "doing something dangerous, or the operator shouts 'stop!'. "
        "After this, the ER mission is NOT canceled — call "
        "cancel_er_mission separately if you want to end the task."
    ),
    parameters=_obj({}),
)

LIGHTS_ON = types.FunctionDeclaration(
    name="lights_on",
    description=(
        "Turn on the robot's LEDs. Useful in dark environments or for "
        "visual acknowledgement when asked to 'light up' or 'say hi.'"
    ),
    parameters=_obj(
        {"which": _enum(
            "Which LEDs to illuminate.",
            ["gimbal", "bottom", "both"],
            default="both",
        )},
    ),
)

LIGHTS_OFF = types.FunctionDeclaration(
    name="lights_off",
    description=(
        "Turn off the robot's LEDs. Use to conserve battery when the "
        "lights are no longer needed, when the operator asks to 'go "
        "dark' or dim down, or before a mission concludes."
    ),
    parameters=_obj(
        {"which": _enum(
            "Which LEDs to turn off.",
            ["gimbal", "bottom", "both"],
            default="both",
        )},
    ),
)

GIMBAL_LOOK_AT = types.FunctionDeclaration(
    name="gimbal_look_at",
    description=(
        "Point the pan-tilt gimbal to an absolute pan/tilt angle in "
        "degrees. Pan: -180..180 (negative=right, positive=left, 0=forward). "
        "Tilt: -45..90 (negative=down, positive=up). Use when you want to "
        "look around before taking a camera_view."
    ),
    parameters=_obj(
        {
            "pan_deg": _num(
                "Pan angle in degrees (negative=right, 0=forward, positive=left).",
                minimum=-180, maximum=180,
            ),
            "tilt_deg": _num(
                "Tilt angle in degrees (negative=down, 0=level, positive=up).",
                minimum=-45, maximum=90,
            ),
        },
        required=["pan_deg", "tilt_deg"],
    ),
)

SET_WATCH_MODE = types.FunctionDeclaration(
    name="set_watch_mode",
    description=(
        "Turn ambient pantilt-camera push on or off, and optionally "
        "retune its frame rate. Watch mode is on by default at session "
        "start (ambient ~0.33 FPS, i.e. one frame every ~3 seconds). "
        "Use this only when the operator explicitly asks you to silence "
        "the feed, or to speed it up / slow it down for a specific "
        "task. Operator-tunable, not model-self-tunable: the fps "
        "parameter is clamped to [0.1, 1.0] (one frame every 10 s "
        "to one frame per second)."
    ),
    parameters=_obj(
        {
            "on": _bool("Enable (true) or disable (false) ambient camera push."),
            "source": _enum(
                "Which camera to stream from.",
                ["pantilt"],  # OAK-D not yet wired; future
                default="pantilt",
            ),
            "fps": _num(
                "Optional. Frames per second for the ambient feed. "
                "Clamped to [0.1, 1.0]. Omit to leave the current "
                "cadence unchanged.",
                minimum=0.1, maximum=1.0,
            ),
        },
        required=["on"],
    ),
)


ALL_TOOLS = (
    # Perception
    GET_ROBOT_STATE, GET_CAMERA_VIEW, GET_SLAM_MAP_VIEW, GET_COSTMAP_VIEW,
    # Mission control
    DISPATCH_ER_MISSION, CANCEL_ER_MISSION, GET_ER_MISSION_STATUS,
    # Safety
    EMERGENCY_STOP,
    # Aesthetic / auxiliary
    LIGHTS_ON, LIGHTS_OFF, GIMBAL_LOOK_AT, SET_WATCH_MODE,
)


def tool_names() -> tuple:
    """Return the ordered tool names matching ALL_TOOLS order.

    Tuple, not set, so iteration order is stable for logging/UIs.
    """
    return tuple(t.name for t in ALL_TOOLS)


def all_tools_json() -> list[dict]:
    """Return ALL_TOOLS as plain JSON-serializable dicts.

    The underlying ALL_TOOLS tuple is shaped as pydantic
    google.genai.types.FunctionDeclaration objects (kept that way so
    test_tool_declarations.py can validate the SDK shape). The
    raw-WS transport in session.py needs the JSON shape — call this
    helper rather than reaching into pydantic internals at the call site.
    """
    return [
        t.model_dump(exclude_none=True, by_alias=True, mode="json")
        for t in ALL_TOOLS
    ]
