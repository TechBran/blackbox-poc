# ugv_tools_api — UGV Beast Tool Schema HTTP Server

FastAPI server that exposes the UGV Beast's robot capabilities as LLM-callable tools (Anthropic / OpenAI / Gemini formats). Runs inside the `ugv_waveshare` Docker container on the Jetson Orin Nano.

## Quickstart

From any device on the same LAN (or tailnet):

```bash
# Health
curl http://ugv-beast:8080/health
# → {"ok": true, "bridge": true}

# Anthropic-format tool registry
curl "http://ugv-beast:8080/tools?format=anthropic" | jq 'length'
# → 22

# Dispatch a tool
curl -X POST http://ugv-beast:8080/tool/status_get_pose \
  -H 'Content-Type: application/json' -d '{}'
# → {"tool":"status_get_pose","result":{"x":...,"y":...,"yaw_deg":...}}

# Latest camera frame
curl http://ugv-beast:8080/snapshot/oakd > frame.jpg
```

## Endpoints

- `GET /health` — liveness + bridge status
- `GET /tools?format={anthropic,openai,gemini}` — tool registry
- `POST /tool/{name}` — dispatch; JSON body is the tool's args
- `GET /snapshot/{pantilt,oakd}` — latest JPEG

## Architecture

```
LLM (Claude/GPT/Gemini)
    │  HTTP
    ▼
FastAPI (ugv_tools_api.server, port 8080)
    ├─ tool registry (22 tools, 6 domains)
    └─ RosBridge singleton (MultiThreadedExecutor on daemon thread)
            │
            ▼
    ROS2 Humble graph (Nav2, SLAM, ugv_driver, camera publishers)
            │
            ▼
    ESP32 driver board (motors, servos, LEDs, IMU, telemetry)
```

## Tool inventory

Motion (5): `motion_move_forward`, `motion_move_backward`, `motion_rotate_left`, `motion_rotate_right`, `motion_stop`
Gimbal (3): `gimbal_look_at`, `gimbal_reset`, `gimbal_get_state`
Camera (2): `camera_list`, `camera_snapshot`
Status (6): `status_get_pose`, `status_get_odom`, `status_get_lidar_summary`, `status_list_nodes`, `status_list_topics`, `status_health`
Navigation (3): `nav_goto_point`, `nav_cancel`, `nav_status`
System (3): `system_emergency_stop`, `system_servo_center`, `system_servo_release`

## Container prerequisites

On a fresh container, run the idempotent bootstrap:

```bash
bash /home/ws/ugv_ws/ugv_tools_api/scripts/container-bootstrap.sh
```

This installs pinned versions of fastapi, uvicorn, pydantic, opencv-python-headless, numpy 1.x, depthai 2.30.x, pytest, httpx and cleans up the Waveshare base image's stale opencv-contrib-python artifacts.

### Resolved versions (2026-04-16)

fastapi 0.136 · uvicorn 0.44 · pydantic 2.13 · opencv-python-headless 4.10 · numpy 1.26.4 · depthai 2.32 · pytest 6.2.5 · httpx 0.28

## Driver patch

`ugv_tools_api` relies on a small patch to Waveshare's `ugv_driver.py` that adds three additions:

1. `/gimbal/absolute` subscription — forwards to ESP32 `T=134` pan/tilt goto
2. `/gimbal/state` publisher at 10 Hz — mirrors the last commanded pan/tilt (open-loop servos, no real feedback on this firmware)
3. `/ugv/json_cmd` subscription — generic JSON passthrough for system tools (emergency stop, servo center/release)

Patch file: `docs/ugv-beast/setup/ugv_driver_patches/add_gimbal_topic.patch`. Reapply after Waveshare upstream pulls.

## Systemd service

Install with:

```bash
sudo cp deploy/ugv-tools-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ugv-tools-api.service
```

## Tests

```bash
# Inside the container, ROS sourced:
cd /home/ws/ugv_ws/ugv_tools_api
PYTHONPATH=.:$PYTHONPATH python3 -m pytest -v -p no:anyio
# 45/45 passing (last run)
```

## End-to-end demo (Claude tool-use)

```bash
ANTHROPIC_API_KEY=... \
  ./Orchestrator/venv/bin/python scripts/ugv-llm-demo.py
```

## Reachability check

```bash
# LAN
./scripts/test-ugv-tools-remote.sh
# Tailscale (preferred)
UGV_HOST=ugv-beast ./scripts/test-ugv-tools-remote.sh
```

## Voice Interface

The UGV Beast listens continuously for a wake phrase and responds with spoken
actions, routing everything through BlackBox — no API keys on the Jetson.

**Wake phrase:** "Black Box Flight Recorder" *(trained openWakeWord model)*

After the wake word, keep talking — VAD detects the end of your utterance,
Whisper transcribes it, Claude answers (with the 22 UGV tools semantically
injected into its prompt), and the JBL speaks the reply.

### Architecture (3 Jetson systemd services)

```
    USB mic (pantilt cam)              JBL via plughw:CARD=Device
            │                                   ▲
            ▼                                   │
    ┌─────────────────┐                 ┌──────────────────┐
    │   ugv-ears      │                 │   ugv-voice      │
    │ openWakeWord +  │    /speak       │ FastAPI :8081    │
    │ VAD + PyAudio   │────────────────▶│ mpg123 → ALSA    │
    └─────────────────┘                 └──────────────────┘
            │ /stt (multipart WAV)              ▲ /tts
            │ /chat (text + UGV tools)          │
            ▼                                   │
    ┌────────────────────────────────────────────────────┐
    │           BlackBox Orchestrator :9091              │
    │  Whisper · Claude · OpenAI TTS · ToolVault · 22    │
    │  UGV tools auto-injected on semantic match         │
    └────────────────────────────────────────────────────┘
            │ HTTP proxy calls
            ▼
    ┌────────────────────┐
    │  ugv-tools-api     │
    │  FastAPI :8080     │
    │  ROS2 bridge       │
    └────────────────────┘
            │
            ▼
    Nav2 · SLAM · ESP32 · OAK-D · gimbal · lights
```

### Service responsibilities

| Service          | Port | Responsibility                                    |
|------------------|------|---------------------------------------------------|
| `ugv-tools-api`  | 8080 | HTTP→ROS2 bridge, 22 tool dispatchers, snapshots  |
| `ugv-voice`      | 8081 | `POST /speak` → BlackBox `/tts` → JBL via mpg123  |
| `ugv-ears`       | —    | mic loop: PyAudio→openWakeWord→VAD→/stt→/chat     |

All three run `docker exec ugv_waveshare …`. `ugv-ears` requires `ugv-voice`
and `ugv-tools-api` via systemd `Requires=` / `After=`, so cold-boot order is
guaranteed.

### Environment (per-service)

`ugv-ears` (`deploy/ears.env`):

| Var                    | Default                                        | Notes                                    |
|------------------------|------------------------------------------------|------------------------------------------|
| `BLACKBOX_URL`         | — *(required)*                                 | e.g. `http://100.74.17.54:9091`          |
| `BLACKBOX_OPERATOR`    | `Brandon`                                      | Attribution for BlackBox snapshots       |
| `BLACKBOX_MODEL`       | `claude-sonnet-4-6`                            | Any provider configured in BlackBox      |
| `BLACKBOX_PROVIDER`    | `anthropic`                                    | `anthropic` \| `openai` \| `gemini`      |
| `MIC_DEVICE_HINT`      | `USB Camera`                                   | Substring match; `""` = PyAudio default  |
| `WAKEWORD_MODEL`       | `…/voice_models/black_box_flight_recorder.onnx`| Trained openWakeWord ONNX                |
| `WAKEWORD_THRESHOLD`   | `0.5`                                          | Lower → more sensitive                   |
| `VAD_AGGRESSIVENESS`   | `2`                                            | webrtcvad 0..3                           |
| `MUTE_FLAG_PATH`       | `/tmp/ugv_ears_muted`                          | Shared lock with `ugv-voice`             |

`ugv-voice` (`deploy/voice.env`):

| Var                 | Default              | Notes                                  |
|---------------------|----------------------|----------------------------------------|
| `BLACKBOX_URL`      | — *(required)*       | Same URL as ears                       |
| `UGV_VOICE_PORT`    | `8081`               |                                        |
| `UGV_ALSA_DEVICE`   | `plughw:CARD=Device` | JBL via USB PnP card; pin by name      |
| `UGV_VOICE`         | `onyx`               | Any OpenAI TTS voice                   |
| `UGV_TTS_MODEL`     | `tts-1-hd`           | (via BlackBox `/tts`)                  |
| `MUTE_FLAG_PATH`    | `/tmp/ugv_ears_muted`| Shared lock with `ugv-ears`            |

### Restart a single service

```bash
sudo systemctl restart ugv-ears.service
sudo systemctl restart ugv-voice.service
sudo systemctl restart ugv-tools-api.service
# Full stack:
sudo systemctl restart ugv-tools-api.service ugv-voice.service ugv-ears.service
```

### End-to-end demo

```bash
# From the dev machine (uses sshpass+ssh to the Jetson):
./scripts/ugv-voice-demo.sh
```

Verifies the three services are active, BlackBox is reachable, `/tools` returns
22 UGV tools, fires a direct `/speak` round-trip, then tails
`journalctl -u ugv-ears -f` for 60 s so you can speak the wake phrase and
watch the wake → STT → `/chat` → auto-speak chain on one screen. Pre-wake
checks run in ~15 s; skip the interactive tail with `SKIP_WAKE=1`.

### Troubleshooting

| Symptom                                          | Fix                                                                                               |
|--------------------------------------------------|---------------------------------------------------------------------------------------------------|
| **Wake word never fires**                         | `arecord -l` inside container → check `MIC_DEVICE_HINT` matches an actual card; lower `WAKEWORD_THRESHOLD` to `0.3` |
| **JBL silent but `/speak` returns 200**           | Confirm `UGV_ALSA_DEVICE=plughw:CARD=Device`; `aplay -L` to list cards; physical volume wheel on JBL |
| **`/speak` returns 500 "mpg123 exit …"**          | ALSA device busy (mid-playback from prior call) — the demo script retries automatically once      |
| **`/health` shows `blackbox_reachable:false`**    | Tailscale still establishing — wait 30 s; verify `BLACKBOX_URL` in env; `tailscale status` on host |
| **USB camera not enumerated at boot**             | Cold-boot race; wait ~30 s then `sudo systemctl restart ugv-ears.service` (systemd retries 5×)    |
| **Ears self-triggers on its own TTS**             | `MUTE_FLAG_PATH` must be identical in both `ears.env` and `voice.env` (default `/tmp/ugv_ears_muted`) |
| **`/chat` responds but no speech**                | Auto-speak hook disabled in Orchestrator, OR `ugv-voice` unreachable from BlackBox — check `curl http://<ugv>:8081/health` from the BlackBox host |
| **`/speak` HTTP 502 "BlackBox /tts unreachable"** | Orchestrator restart mid-call — `systemctl status blackbox.service` on the BlackBox host          |

Live logs:

```bash
sudo journalctl -u ugv-ears.service -f          # wake + STT + chat
sudo journalctl -u ugv-voice.service -f         # /speak + mpg123
sudo journalctl -u ugv-tools-api.service -f     # tool dispatches
```
