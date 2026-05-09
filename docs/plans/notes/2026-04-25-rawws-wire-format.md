# Supervisor Raw-WS Wire Format — Reference & Diff vs SDK Path

> Companion notes file for `docs/plans/2026-04-25-supervisor-rawws-port.md` (Tasks 4, 6).
> Captures the JSON the supervisor sends to Gemini Live after Task 4's port.

## What this is

The Task 4 port replaced the `google.genai` SDK Live transport with hand-crafted JSON over raw `websockets`. This file documents:

1. The exact JSON shape we now send (captured empirically from `build_setup_payload`)
2. What the SDK was sending before the port
3. The deliberate omissions that drove the port (the suspected cycling-bug triggers)

The empirical bench in Task 7 confirms the new payload eliminates the post-`turn_complete` session-cycling pattern observed on `gemini-2.5-flash-native-audio-latest`.

## Setup payload — fresh session (no resumption handle)

Captured by importing `ugv_tools_api.supervisor.raw_session.build_setup_payload` inside the `ugv_waveshare` container with the real `ALL_TOOLS` declarations. System instruction was replaced with a placeholder for brevity; everything else is the production shape.

```json
{
  "setup": {
    "model": "models/gemini-2.5-flash-native-audio-latest",
    "generationConfig": {
      "responseModalities": [
        "AUDIO"
      ],
      "speechConfig": {
        "voiceConfig": {
          "prebuiltVoiceConfig": {
            "voiceName": "Orus"
          }
        }
      }
    },
    "systemInstruction": {
      "parts": [
        {
          "text": "[supervisor system prompt - truncated for the wire-format doc]"
        }
      ]
    },
    "tools": [
      {
        "functionDeclarations": [
          { "name": "get_robot_state", "description": "...", "parameters": { "properties": {}, "required": [], "type": "OBJECT" } },
          { "name": "get_camera_view", "description": "...", "parameters": { "properties": {}, "required": [], "type": "OBJECT" } },
          { "name": "get_costmap_view", "description": "...", "parameters": { "properties": {}, "required": [], "type": "OBJECT" } },
          { "name": "dispatch_er_mission", "description": "...", "parameters": { "...": "..." }, "behavior": "NON_BLOCKING" },
          { "name": "cancel_er_mission", "description": "...", "parameters": { "...": "..." } },
          { "name": "get_er_mission_status", "description": "...", "parameters": { "properties": {}, "required": [], "type": "OBJECT" } },
          { "name": "emergency_stop", "description": "...", "parameters": { "properties": {}, "required": [], "type": "OBJECT" } },
          { "name": "lights_on", "description": "...", "parameters": { "...": "..." } },
          { "name": "lights_off", "description": "...", "parameters": { "...": "..." } },
          { "name": "gimbal_look_at", "description": "...", "parameters": { "...": "..." } },
          { "name": "set_watch_mode", "description": "...", "parameters": { "...": "..." } }
        ]
      }
    ],
    "contextWindowCompression": {
      "slidingWindow": {}
    }
  }
}
```

Top-level keys present in this payload: `model`, `generationConfig`, `systemInstruction`, `tools`, `contextWindowCompression`. Crucially absent: `inputAudioTranscription`, `outputAudioTranscription`, `realtimeInputConfig`, `sessionResumption`. See the diff table below for the rationale.

The full tool list is shown verbatim in the "Sample tools[].functionDeclarations[] entries" section below; descriptions and parameter blocks are elided here only to keep this top-level structure scannable.

## Setup payload — in-wake reconnect (with resumption handle)

Same as above, with one extra top-level key under `setup`:

```json
"sessionResumption": {
  "handle": "SAMPLE_HANDLE_TOKEN"
}
```

The `sessionResumption.handle` field is conditionally sent only when the HandleStore returns a non-empty handle. On fresh-process startup the handle store is cleared so the first session always omits this field. (See `raw_session.build_setup_payload`: the block is gated by `if resume_handle:`.)

## Sample tools[].functionDeclarations[] entries

All 11 tools follow the same pydantic-derived JSON shape via `model_dump(exclude_none=True, by_alias=True, mode="json")`. The `dispatch_er_mission` tool carries `behavior: "NON_BLOCKING"`; other tools omit `behavior`, defaulting to BLOCKING server-side.

`get_robot_state` (parameter-less, BLOCKING):

```json
{
  "description": "Read the robot's current fused pose (x, y, yaw), linear and angular velocity, and 8-sector lidar minimum distances. Use when the operator asks where the robot is, which way it is facing, or whether something is close to it.",
  "name": "get_robot_state",
  "parameters": {
    "properties": {},
    "required": [],
    "type": "OBJECT"
  }
}
```

`dispatch_er_mission` (the NON_BLOCKING tool — note the `behavior` key at the top of the declaration object, peer to `name`/`description`/`parameters`):

```json
{
  "description": "Send a natural-language mission to the robot's on-device execution agent (Gemini Robotics-ER). The ER agent will translate the mission into Nav2 goals and tool calls and execute one complete task before returning control. Examples: 'Drive to the kitchen and stop there', 'Map this room', 'Inspect the charger.' This call returns immediately; mission progress is streamed back as it happens.",
  "name": "dispatch_er_mission",
  "parameters": {
    "properties": {
      "mission": {
        "description": "Plain-English mission instruction. Be specific about the goal and any constraints (e.g., 'slow speed', 'stop and report when you see a cat').",
        "type": "STRING"
      },
      "replace_current": {
        "default": false,
        "description": "If true, abort any in-flight mission before dispatching. If false (default), and a mission is already running, the call returns an error so the operator can decide.",
        "type": "BOOLEAN"
      }
    },
    "required": [
      "mission"
    ],
    "type": "OBJECT"
  },
  "behavior": "NON_BLOCKING"
}
```

Notes on the pydantic dump:

- Schema types come out UPPERCASE (`"STRING"`, `"OBJECT"`, `"BOOLEAN"`, `"NUMBER"`) — the Live API's preferred enum casing. Matches the Orchestrator's reference shape.
- `by_alias=True` produced no camelCase rewrites in this sample because no field aliases are set; field names are already lowercase singulars matching wire-format expectations.
- `exclude_none=True` is what keeps `behavior` off non-NON_BLOCKING tools and keeps `default` off required parameters.

## NON_BLOCKING tool response shape

```json
{
  "toolResponse": {
    "functionResponses": [
      {
        "id": "fc_abc123",
        "name": "dispatch_er_mission",
        "response": {
          "mission_id": "mid_xyz",
          "status": "accepted"
        },
        "willContinue": true,
        "scheduling": "SILENT"
      }
    ]
  }
}
```

The `willContinue` (camelCase) and `scheduling` keys are emitted by `RawLiveSession.send_tool_response` when the snake_case kwargs `will_continue` / `scheduling` are passed. These map to the documented Live API enum strings: `"SILENT"`, `"WHEN_IDLE"`, `"INTERRUPT"`.

## Audio realtime input

```json
{
  "realtimeInput": {
    "mediaChunks": [
      {
        "mimeType": "audio/pcm;rate=16000",
        "data": "<base64-encoded PCM16 16kHz mono>"
      }
    ]
  }
}
```

## Video realtime input (watch mode + vision tools)

```json
{
  "realtimeInput": {
    "mediaChunks": [
      {
        "mimeType": "image/jpeg",
        "data": "<base64-encoded JPEG>"
      }
    ]
  }
}
```

## What we removed vs the prior SDK path

These fields appeared in the SDK-built `LiveConnectConfig` and serialized into the `setup` payload. They are NOT sent by `build_setup_payload` and their omission is intentional:

| Field | Prior SDK setup | Reason for omission |
|---|---|---|
| `inputAudioTranscription` | `{}` (empty config opted us in) | Suspected cycling-bug trigger; Orchestrator's bridge omits it. The supervisor previously logged `[user] ...` from the resulting transcripts; that log is now silent (intentional). |
| `outputAudioTranscription` | `{}` | Same — suspected trigger. The `[model] ...` log line is also silent now. The model's own `text` parts under `modelTurn.parts` may still arrive (native-audio reasoning trace) — see `pump_responses` in `session.py` for the JSON path that catches them. |
| `realtimeInputConfig` | `{"automaticActivityDetection": {"endOfSpeechSensitivity": "END_SENSITIVITY_LOW"}}` | Orchestrator only sets this in `phone_mode`; default VAD works in non-phone path. Removing it eliminates one possible cycling-bug trigger. |
| `sessionResumption` (when handle is None) | `{}` always | Orchestrator only sends when handle is non-empty. Sending an empty config may have been opting us into a "single-turn-close-with-resumption" mode server-side. Now: omitted on fresh-process; emitted as `{"handle": "..."}` only when the HandleStore has a real handle. |
| `speechConfig.languageCode` | `"en-US"` | Orchestrator doesn't set it. Server-side language detection on the native-audio model handles English fine in practice. |

## Reference

- Plan: `docs/plans/2026-04-25-supervisor-rawws-port.md`
- Bug context: BlackBox snapshot SNAP-20260425-6280 (43 sessions in 2 minutes)
- Upstream: python-genai issue #1224 (SDK aio.live unresponsive after first turn_complete)
- Production-proven raw-WS reference: `Orchestrator/routes/gemini_live_routes.py:365-407`

## Empirical bench results

See `docs/plans/notes/2026-04-25-rawws-bench.md` (Task 7) for journalctl captures confirming:

- Sessions stay open across multiple `turn_complete` events
- `[diag] pump_responses sess# exit reason=natural-iterator-end (goAway-driven, will reconnect)` only fires when Gemini sends an explicit goAway
- `[diag] pump_responses sess# exit reason=natural-iterator-end (no goAway, session ended)` does NOT trigger reopens (regression guard)
