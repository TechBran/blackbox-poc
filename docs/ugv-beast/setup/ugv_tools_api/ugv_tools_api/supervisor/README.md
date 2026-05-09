# UGV Beast Supervisor — Operator Notes

Operator-facing reference for the wake-gated Gemini Live supervisor that
owns the robot's mic + speaker. If you're on-call, this is the page that
explains how the thing actually behaves *now*, after Tasks 1-6 of the
2.5 GA + async + AEC plan landed.

## 1. What the supervisor is

A single long-running Python service (`ugv-supervisor.service`) running
inside the `ugv_waveshare` container on the Jetson. It:

- Owns the USB mic and USB speaker (held only while a session is live —
  `ugv-ears` owns the mic the rest of the time for wake-word detection).
  With AEC3 active, the mic stays open during model speech so the operator
  can interrupt mid-sentence — see §4.
- Talks to the operator over Gemini Live with the
  `gemini-2.5-flash-native-audio-latest` model (GA-track evergreen
  alias, voice `Orus`).
- Commands the on-device ER agent (`gemini-2.5-flash-robotics-er-1.6`,
  port `:8082`) on demand via the `dispatch_er_mission` tool.

It does **not** speak Nav2 directly — that's the ER agent's job. The
supervisor is the conversational layer; ER is the planning + execution
layer.

```bash
# Service file
/etc/systemd/system/ugv-supervisor.service

# Logs
journalctl -u ugv-supervisor.service -f

# HTTP control plane
http://localhost:8083/health
http://localhost:8083/open_session   # POST — usually called by ugv-ears
http://localhost:8083/stop_session   # POST — operator says "goodbye"
```

## 2. Wake / sleep cycle

1. **Idle.** No WebSocket, no mic. `ugv-ears` listens on the USB mic for
   the wake phrase **"Black Box Flight Recorder"**.
2. **Wake.** Ears writes `MUTE_FLAG`, POSTs `/open_session`. The
   supervisor claims the mic, opens Gemini Live, and plays the
   **880 Hz ready chime** (~180 ms). The mic is hot the moment that
   tone finishes.
3. **Talk.** Operator speaks; the model speaks back; tools fire as
   needed. Barge-in works (see §4).
4. **Close.** Operator says "goodbye" → model calls `/stop_session`, or
   on-call hits it manually. Supervisor plays the **660 Hz close chime**
   (~300 ms), tears down the WebSocket, releases the mic, clears
   `MUTE_FLAG`. Ears reacquires.
5. **Idle timeout.** If 600 s elapse with no model activity (audio in
   *or* out), the watchdog auto-closes as if `/stop_session` fired.
   Override:

   ```bash
   # supervisor.env
   SUPERVISOR_IDLE_TIMEOUT_S=900   # 15 min
   ```

The supervisor *process* stays up across all of this. `/stop_session`
is "goodbye, see you next wake word" — not a service stop. Use
`sudo systemctl stop ugv-supervisor.service` if you actually want the
daemon down.

## 3. Watch mode (Task 4)

Ambient camera push: a 1 FPS pantilt JPEG stream into the live session
so the model sees what the robot sees during a mission.

| Trigger                                | Effect                                    |
|----------------------------------------|-------------------------------------------|
| Session opens                          | OFF (default)                             |
| `dispatch_er_mission` succeeds         | Auto-ON                                   |
| All ER missions terminate              | Auto-OFF (unless operator manually toggled) |
| Operator says "turn on watch mode"     | Model calls `set_watch_mode(on=true)`     |
| Operator says "turn off watch mode"    | Model calls `set_watch_mode(on=false)`    |

If the operator manually turned watch on/off during a mission, that
manual override sticks — the auto-off when missions finish won't
flip it back. (Manual intent wins over heuristic.)

Token cost: ~30-80 KB per JPEG, 1 fps. Tracked by the budget (§7) and
contributes to session rotation.

Source is `pantilt` only for now (the schema enum has just one value).
OAK-D enable is a separate plan.

## 4. Barge-in (changed in Task 5)

**Barge-in works.** You can interrupt the model mid-sentence and it
will hear you and stop talking. This was broken in the half-duplex
silent-wait era; AEC fixed it.

How: WebRTC AEC3 (x86 dev) or speexdsp (aarch64 production) subtracts
the speaker reference from the mic so the model doesn't hear its own
voice through the room. The **silent-wait protocol** referenced in
older supervisor docs is **no longer required**. If a script or doc
tells you to "wait for the model to finish before speaking," that
guidance is stale.

If barge-in *feels* broken — model keeps talking over you, or seems
deaf during its own speech — AEC has likely fallen back to halfduplex
(see §5 Debugging AEC).

## 5. Debugging AEC (Task 5)

### Verify AEC is active

```bash
journalctl -u ugv-supervisor.service | grep -E "AEC3 enabled|halfduplex"
```

Look for `AEC3 enabled` near a session-open log line. If you see
`falling back to halfduplex`, AEC init or `process()` raised and the
session is running in legacy half-duplex mode.

### Hard rollback (<10 s)

```bash
sudo sed -i 's|^SUPERVISOR_AEC_MODE=.*|SUPERVISOR_AEC_MODE=halfduplex|' \
  /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/supervisor.env
sudo systemctl restart ugv-supervisor.service
```

Set back to `aec3` (or remove the line — it's the default) when ready.

### Tune delay

```bash
# supervisor.env — accounts for USB DAC + ALSA buffer + speaker→mic travel
SUPERVISOR_AEC_DELAY_MS=50
```

Default is 50 ms. If residual echo bleeds through to the operator's
end, try the 30-80 ms range. Restart the service after each change.

### Expected suppression

| Backend          | Platform   | Target on synthetic test | Real-world target |
|------------------|------------|--------------------------|-------------------|
| WebRTC AEC3      | x86 dev    | ≥12 dB (we hit -36.67 dB) | ≥25 dB           |
| speexdsp         | aarch64 prod | ≥12 dB                  | ≥12-18 dB         |

(Aarch64 prod uses speexdsp because WebRTC's AEC3 build doesn't ship
for ARM in the wheel chain we're using. Acceptable trade-off.)

## 6. Mission progress streaming (Task 3)

`dispatch_er_mission` is `NON_BLOCKING`. The model can keep talking to
the operator while ER does its thing. While the mission runs, the
supervisor streams progress back as additional `FunctionResponse` parts
under the **same call id**:

| Event                          | Scheduling   | Frequency |
|--------------------------------|--------------|-----------|
| Pose tick                      | `SILENT`     | 1 Hz      |
| Nav2 state transition          | `WHEN_IDLE`  | event     |
| Terminal (completed/failed/aborted/cancelled) | (`will_continue=False`) | once |

`SILENT` means the model receives the data without speaking — context
only. `WHEN_IDLE` means the model is allowed to speak about it once
its current utterance finishes. Terminal is the regular function
response that closes out the call.

**Operator-visible behavior:** ask the model "where are we?" mid-mission
and it answers from the live feed without an extra `get_robot_state`
call. Ask "did we get there?" and it knows because the terminal event
already arrived.

## 7. Session rotation (Task 6)

The supervisor tracks an **approximate** token budget per session and
proactively rotates before Gemini's `GoAway` would force a hard cut.

- **Tracked inputs:** audio seconds (input + output combined,
  `audio_tokens_per_s=25`) and JPEG frame count
  (`jpeg_tokens_per_frame=250`).
- **Trigger:** at `SUPERVISOR_BUDGET_THRESHOLD` × `SUPERVISOR_BUDGET_WINDOW_TOKENS`
  used (default 0.8 × 32000 = 25 600 tokens), the supervisor closes the
  current session and reconnects with the persisted `session_resumption`
  handle.
- **Operator-visible:** ~3-5 s audio gap, then conversation resumes
  with context intact. The operator may not even notice mid-utterance
  rotation — Gemini fills in continuity from the resumption handle.

```bash
# supervisor.env
SUPERVISOR_BUDGET_WINDOW_TOKENS=32000   # context-window estimate
SUPERVISOR_BUDGET_THRESHOLD=0.8         # rotate at 80% used

# Disable rotation entirely (debugging context-fill behavior)
SUPERVISOR_BUDGET_WINDOW_TOKENS=0
```

Lower `THRESHOLD` → rotate more often, less risk of `GoAway`, more
audio gaps. Higher → fewer gaps but closer to the wire. The
`window_tokens=0` zero-window guard in `budget.py` short-circuits
`should_rotate` so rotation never fires — useful for reproducing a
context-starvation issue.

The constants `audio_tokens_per_s` and `jpeg_tokens_per_frame` in
`budget.py` are conservative documented estimates. Task 8's bench
session is the formal calibration.

## 8. Post-sync ritual (don't skip this)

`scripts/sync-ugv-tools.sh` overwrites
`/home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/supervisor.env`
with the laptop template. The laptop template intentionally has

```
GOOGLE_API_KEY=REPLACE_ME_FROM_er.env
```

so the real key never lives in git. After **every** sync, re-substitute
the key from `er.env` and restart the service:

```bash
sudo bash -c '
KEY=$(grep -E "^GOOGLE_API_KEY=" /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/er.env | cut -d= -f2-)
sed -i "s|^GOOGLE_API_KEY=.*|GOOGLE_API_KEY=$KEY|" /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/supervisor.env
'
sudo systemctl restart ugv-supervisor.service
```

If you forget: the service fails fast at startup with `RuntimeError:
GOOGLE_API_KEY is required but not set` (see `config.py` — by design;
silent fallback on first tool call would be worse).

## 9. Health check

```bash
systemctl is-active ugv-supervisor.service
curl http://localhost:8083/health
```

Expected:

```
active
{"status":"ok","running":true,"model":"gemini-2.5-flash-native-audio-latest","active_mission_id":null,"session_active":false}
```

`session_active:true` between a wake and a goodbye. `active_mission_id`
is non-null while ER is running a dispatched mission.

## 10. Tool surface (11 tools)

Defined in `tool_declarations.py` (`ALL_TOOLS`). Implementations in
`tool_handlers.py`.

**Perception**

| Tool                | Purpose                                                     |
|---------------------|-------------------------------------------------------------|
| `get_robot_state`   | Fused pose (x, y, yaw), linear+angular velocity, 8-sector lidar minimums. |
| `get_camera_view`   | One pantilt JPEG on demand.                                 |
| `get_costmap_view`  | Nav2 global costmap rendered as a PNG.                      |

**Mission control**

| Tool                  | Purpose                                                       |
|-----------------------|---------------------------------------------------------------|
| `dispatch_er_mission` | NON_BLOCKING; sends a plain-English mission to ER. Progress streams under same id (§6). |
| `cancel_er_mission`   | Abort the active ER mission (must be acknowledged — BLOCKING). |
| `get_er_mission_status` | Read mission id, status, last text, recent events.          |

**Safety**

| Tool              | Purpose                                                            |
|-------------------|--------------------------------------------------------------------|
| `emergency_stop`  | Firmware-level halt. BLOCKING. Bypasses ER. Does **not** cancel an ER mission — call `cancel_er_mission` separately. |

**Aesthetic / auxiliary**

| Tool             | Purpose                                                            |
|------------------|--------------------------------------------------------------------|
| `lights_on`      | Toggle LEDs on (`gimbal` / `bottom` / `both`, default `both`).     |
| `lights_off`     | Toggle LEDs off (same enum).                                       |
| `gimbal_look_at` | Absolute pan (-180..180) + tilt (-45..90) in degrees.              |
| `set_watch_mode` | Toggle ambient camera push (§3). Source is `pantilt` only for now. |

## 11. Known limitations

- **AEC3 production verification** wasn't completed live at deploy
  time. Task 8 bench is the formal validation; until then, treat the
  speexdsp suppression numbers as bench-confirmed but not field-confirmed.
- **Budget rate constants** in `budget.py` (audio_tokens_per_s=25,
  jpeg_tokens_per_frame=250) are conservative documented estimates.
  Refine after Task 8 records actual rates per minute on this hardware.
- **Backend asymmetry.** Aarch64 prod runs speexdsp; x86 dev runs
  WebRTC AEC3. dB suppression numbers differ; behavior is the same
  (echo subtracted, barge-in works), quality differs.
- **Watch mode source** is `pantilt` only. OAK-D enabling is a separate
  plan; the schema enum will grow when that lands.
