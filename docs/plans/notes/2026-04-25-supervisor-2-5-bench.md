# Supervisor 2.5 GA + Async + Watch + AEC3 — Bench Record

**Date:** 2026-04-25 (initial automated pass + env fixes)
**Supervisor SHA:** `eb22ea454707279f36deb7e01d4fdaa7e99ef6d5` (Jetson, branch: `ros2-humble-develop`; Tasks 0-7 + env-fix follow-ups landed)
**Backend:** Jetson Orin Nano, container `ugv_waveshare`, model `gemini-2.5-flash-native-audio-latest`
**AEC backend:** speexdsp (aarch64; `webrtc_audio_processing` unavailable — `ModuleNotFoundError` confirmed at runtime)

## Status

- ✅ Automated logic verification (M1, M4 deterministic + unit tests)
- ✅ **Findings A and B fixed in commit `eb22ea4`** — container proxy unset at start_supervisor.sh top; unit file `-e` allowlist extended with all Tasks-5/6 env vars. Live network path is now unblocked from the supervisor's perspective.
- ✅ **2026-04-25 live confirmation** — first successful production Live session post-AEC-deploy. `[supervisor] session opened` followed immediately by `[aec] webrtc-audio-processing unavailable; trying speexdsp` then `[supervisor] AEC3 enabled`. Closes the long-standing Task 5 live-verification gap (the `[supervisor] AEC3 enabled` line was finally observed in production journalctl).
- ⚠️ **M4 partial** — set `SUPERVISOR_BUDGET_THRESHOLD=0.05` + opened session; calculated rotation should fire at ~64 s (1600 tok / 25 tok-s mic charge rate). Network dropped before the journal could be captured. Robot recovered, threshold restored to 0.8. Rotation event very likely did fire but is undocumented.
- ⏸️ Operator-led measurements (M2, M3, M5): pending bench session with operator at robot. Live session opens are now PROVEN (not just possible) — only operator audio is missing.

## Pre-bench environmental findings (RESOLVED)

Two issues surfaced during the automated pass. Both fixed in commit `eb22ea4`:

### Finding A — Container has stale `https_proxy` env vars pointing to a dead proxy

The `ugv_waveshare` container has `https_proxy=http://192.168.10.185:10809` baked into its env. All outbound HTTPS connections (including the Gemini Live websocket handshake) try to route through this proxy and time out:

```
$ docker exec ugv_waveshare curl -v -4 -m 5 https://1.1.1.1
* Uses proxy env variable https_proxy == 'http://192.168.10.185:10809'
*   Trying 192.168.10.185:10809...
* Connection timed out after 5001 milliseconds
```

Symptom in supervisor logs after `/open_session`:
```
[supervisor] session error, reconnect in 2.0s: timed out during opening handshake
[supervisor] session error, reconnect in 4.0s: timed out during opening handshake
```

Until this is resolved (unset `https_proxy`/`http_proxy` in the container, or fix the proxy server), **no live Gemini Live session can open from inside the container.** Operator-led bench cannot proceed without this fix.

### Finding B — `ugv-supervisor.service` unit does NOT propagate the new env knobs

The systemd unit file at `/etc/systemd/system/ugv-supervisor.service` has an explicit `-e VAR` allowlist on its `docker exec` line:

```
ExecStart=/usr/bin/docker exec \
  -e GOOGLE_API_KEY \
  -e SUPERVISOR_MODEL -e SUPERVISOR_VOICE -e SUPERVISOR_LANG \
  -e SUPERVISOR_MIC -e SUPERVISOR_SPK -e SUPERVISOR_MIC_GAIN \
  -e TOOLS_API_URL -e ER_URL \
  -e SUPERVISOR_CAMERA_TOPIC -e SUPERVISOR_COSTMAP_TOPIC \
  -e SUPERVISOR_HANDLE_STORE \
  -e SUPERVISOR_HOST -e SUPERVISOR_PORT -e SUPERVISOR_LOG_LEVEL \
  ...
```

These are MISSING from the allowlist:
- `SUPERVISOR_AEC_MODE` (Task 5)
- `SUPERVISOR_AEC_DELAY_MS` (Task 5)
- `SUPERVISOR_BUDGET_WINDOW_TOKENS` (Task 6)
- `SUPERVISOR_BUDGET_THRESHOLD` (Task 6)

Effect: setting any of these in `deploy/supervisor.env` is a **silent no-op**. The container falls back to the `config.py` defaults (`aec3`, `50`, `32000`, `0.8`), which happen to be the documented defaults so production behavior is correct — but operator override (`SUPERVISOR_AEC_MODE=halfduplex` for rollback, threshold tweaks for measurement) does not work as documented in the README.

This is a follow-up item — flag for separate plan/issue. Not in scope for Task 8 itself. Operator README was right that `SUPERVISOR_AEC_MODE=halfduplex` is the rollback knob; the unit file just doesn't honor it yet.

---

## M1 — Tokens/min idle baseline

**Plan estimate:** ~1500 tok/min

**Status:** ✅ verified deterministically; ⏸️ live confirmation deferred (container proxy issue)

### Deterministic derivation

The supervisor's `TokenBudget` is **locally estimated**, not derived from any API counter (Gemini Live does not expose a live token counter). Charging happens in two places (`session.py`):

1. **pump_mic** (every outgoing chunk): `budget.audio_seconds(self._cfg.chunk_ms / 1000.0)`
   - `chunk_ms = 20` (config.py default)
   - 50 chunks/sec × 0.020 s × 25 tok/s = **25 tok/sec from upstream mic** (always charged, even if AEC-cancelled or half-duplex silenced — the bytes still go on the wire)

2. **pump_responses** (every downstream audio chunk): `budget.audio_seconds(len(audio) / 48000.0)`
   - At idle (no model speech), this is 0.

**Idle total:** 25 tok/sec × 60 = **1500 tok/min**. Matches the plan estimate exactly.

This number is determined by `chunk_ms` and `audio_tokens_per_s` constants alone; it does not require a live session to verify because no API call influences it. The same value will appear in the `[supervisor] budget rotate:` log line if rotation fires.

### Why no live verification

Container proxy issue (Finding A) prevents the Gemini Live websocket from connecting. The supervisor enters an exponential-backoff reconnect loop the moment a session is requested. Mic capture starts on `/open_session`, but pump_mic does not actually run until the websocket session opens, so no live ticks accumulate against the budget.

**Operator confirmation protocol** (once Finding A is fixed):

1. Open a Live session (wake word OR `/open_session`), say nothing.
2. Wait 5 minutes.
3. Run `journalctl -u ugv-supervisor.service --since "6 minutes ago" | grep budget`. Each watch_budget tick is silent (every 2s); rotation log only fires at threshold. To get an exposed counter without rotation, operator can temporarily lower `SUPERVISOR_BUDGET_THRESHOLD=0.05` (after fixing Finding B), force rotation, and read the `usage=X.X% (Y/Z)` from the rotate log.
4. With threshold=0.05 and 25 tok/s, expect rotation at `0.05 * 32000 / 25 = 64 s` after session open. Log: `[supervisor] budget rotate: usage=5.0% (1600/32000); ...`.
5. Use that as the empirical floor for `audio_tokens_per_s`. If the wall-clock elapsed differs from 64 s, the constant needs adjustment.

## M2 — Tokens/min during mission with watch mode on

**Plan estimate:** ~3500 tok/min

**Status:** ⏸️ pending operator-led bench

**Protocol for operator** (run with proxy issue fixed):

1. Wake the supervisor: speak the wake phrase ("Black Box Flight Recorder") OR `wget -q -O - --post-data="" http://localhost:8083/open_session` from the Jetson host.
2. Wait for the ready chime and verify `/health` shows `session_active: true`.
3. Speak: **"Drive forward 50 centimeters"**.
4. Verify in journalctl that:
   - `dispatch_er_mission` tool call fires
   - Watch mode auto-on (look for `[watch_stream]` activity)
   - Mission poller starts (`[mission_poller]` ticks at ~1 Hz)
5. While the mission is running (~30 s), DO NOT speak. Let the robot finish.
6. After mission completes, wait 30 s, then `/stop_session`.
7. To extract token rate during mission window:
   - Lower threshold temporarily: `SUPERVISOR_BUDGET_THRESHOLD=0.05` (requires Finding B fix to actually take effect)
   - Rotation will fire mid-mission; rotate log gives `usage_pct` and `used_tokens`
   - Time-since-session-open = `journalctl` timestamp delta from `[supervisor] connecting to gemini` to `[supervisor] budget rotate`
   - Tok/min = `used_tokens / minutes`
8. Expected: ~3500 tok/min (idle 1500 + watch ~250 tok/frame × 60 fpm = 15000 ⚠️ NOTE: at 1 fps × 250 tok/frame = 15000 tok/min for watch alone, FAR exceeds the plan estimate; either the 250 tok/frame constant is wildly high, or the plan's 3500 tok/min number assumes lower JPEG frequency. **This is the most important calibration the operator bench will produce.**)

⚠️ **Calibration note:** The current `jpeg_tokens_per_frame=250` is a stub. With watch mode at 1 FPS that's 15k tok/min from JPEGs alone, which would trip the 0.8 threshold of a 32k window in ~100 s. This may be intentionally conservative (rotate aggressively during missions) or may be an over-estimate. **Operator must record the actual rotation cadence during a live mission and back-calculate the real per-frame token cost.**

## M3 — AEC3 dB suppression on model's voice

**Plan target:** ≥ 25 dB on Chrome AEC3 (x86 dev); ≥ 12-18 dB on speexdsp (aarch64 prod)

**Synthetic test result (re-confirmed during this bench):** **-36.67 dB** below input (3× the 12 dB threshold). Backend selected: `speexdsp` (webrtc-audio-processing not installed on aarch64 image, which matches the plan note).

```
$ docker exec ugv_waveshare python3 -m pytest tests/supervisor/test_aec.py -v
PASSED test_residual_at_least_12db_below_input  (residual -36.67 dB)
PASSED test_passes_through_when_no_reference
PASSED test_fallback_on_init_failure
```

**Status:** ⏸️ real-world bench pending operator

The synthetic test uses a 440 Hz sine echo with 50 ms delay — clean, stationary, ideal for AEC convergence. Real world (model voice through aplay → USB camera mic → arecord) is far harder: nonstationary speech, ALSA/USB jitter, room reverb, AGC artifacts. The 12-18 dB target on speexdsp is the realistic floor; getting below -25 dB on real audio with speexdsp is unlikely without webrtc-audio-processing.

**Protocol for operator** (run with proxy issue fixed):

1. **Capture mic during model speech (AEC3 ON):**
   - Set `SUPERVISOR_AEC_MODE=aec3` in `deploy/supervisor.env` (default; verify after Finding B fix)
   - In a separate ssh session, before opening the supervisor session, start a parallel `arecord`:
     ```
     arecord -D plughw:1,0 -f S16_LE -r 16000 -c 1 -d 30 /tmp/aec_on.wav
     ```
     (use the same `SUPERVISOR_MIC` device the supervisor is configured for)
   - In the first session, immediately wake the supervisor and prompt it to talk for ~30 s ("Tell me a 30 second story about robotics safety").
   - Save `aec_on.wav`.

2. **Capture baseline (HALFDUPLEX, model speech):**
   - Set `SUPERVISOR_AEC_MODE=halfduplex` (after Finding B fix), restart service.
   - Repeat the capture, save as `aec_off.wav`. Note: in halfduplex, the supervisor sends silent_chunk upstream while the speaker is playing — but the parallel `arecord` captures the raw mic signal, including the speaker bleed. That's what we want.

3. **Capture quiet baseline:**
   - Stop the supervisor session entirely. Capture 30 s of room-tone with the speaker silent: `aec_quiet.wav`.

4. **Compute suppression:**
   ```python
   import soundfile as sf, numpy as np
   for name in ['aec_on', 'aec_off', 'aec_quiet']:
       x, sr = sf.read(f'/tmp/{name}.wav')
       rms = 20*np.log10(np.sqrt(np.mean(x.astype(np.float64)**2)) + 1e-9)
       print(f'{name}: {rms:.1f} dBFS')
   ```
   Suppression = `dBFS(aec_off) - dBFS(aec_on)`. Target ≥ 12 dB.

5. **MOST IMPORTANT real-world signal (qualitative):** While the model is speaking, can the operator interrupt mid-sentence? AEC3 enables true full-duplex barge-in. If yes → AEC is doing its job. If no → AEC silently fell back to half-duplex; check `journalctl | grep "AEC3 enabled"` or `grep "AEC.*fallback"`.

6. If AEC3 is suppressing < 12 dB on the live signal, options are:
   - Tune `SUPERVISOR_AEC_DELAY_MS` (current default 50 ms; speaker-to-mic latency on the actual hardware may be different)
   - Lower mic gain
   - Fall back to `halfduplex` until webrtc-audio-processing builds for aarch64

## M4 — Session rotation fires at threshold

**Plan: rotation triggers within 30 s of crossing 80% threshold**

**Status:** ✅ logic verified by unit tests; ⏸️ live observation deferred (container proxy issue + Finding B)

### Unit-test evidence (re-confirmed during this bench)

```
$ docker exec ugv_waveshare python3 -m pytest tests/supervisor/test_budget.py -v
tests/supervisor/test_budget.py::test_no_rotation_below_threshold PASSED
tests/supervisor/test_budget.py::test_rotation_at_threshold       PASSED
tests/supervisor/test_budget.py::test_reset_after_rotation         PASSED
tests/supervisor/test_budget.py::test_zero_window_doesnt_divide_by_zero PASSED
4 passed in 0.04s
```

The `should_rotate` predicate fires deterministically at threshold. The `watch_budget` task in `session.py:776` polls every 2 s and trips `session_stop` on threshold crossing — `wait_for(session_stop.wait(), timeout=2.0)` is the cadence guarantee, so worst-case observation lag is 2 s, well inside the plan's "within 30 s" target.

### What I attempted to live-verify

- Lowered `SUPERVISOR_BUDGET_THRESHOLD=0.1` in `deploy/supervisor.env`.
- Restarted supervisor; verified service `active`.
- Posted `/open_session`.
- Watched journalctl: only `[supervisor] session error, reconnect in N.Ns: timed out during opening handshake` repeating. No session ever opened, so the watch_budget task never started ticking.

**Two compounding issues:**
- Finding A blocks the websocket from connecting (no upstream→ no charging→ no rotation).
- Finding B means even if A were fixed, the threshold change in `supervisor.env` wouldn't propagate into the container — it would still see `0.8` from the config default. The unit file would need `-e SUPERVISOR_BUDGET_THRESHOLD` added.

### Restored state

Threshold restored to `0.8` in `deploy/supervisor.env`. Service restarted. `/health` returns OK. Real `GOOGLE_API_KEY` re-verified intact in the env file. Service is in steady wake-wait state, ready for operator.

### Operator confirmation protocol (after Findings A + B fixed)

1. Lower `SUPERVISOR_BUDGET_THRESHOLD=0.1` in `deploy/supervisor.env`. Restart service.
2. Open a Live session (operator can stay silent — pump_mic charges 25 tok/s regardless).
3. Note timestamp at `[supervisor] connected to gemini` log line.
4. Watch for `[supervisor] budget rotate: usage=10.X% (3200+/32000); closing session for resumption-handle reconnect`.
5. Expected wall-clock to rotate: 32000 × 0.1 / 25 ≈ **128 seconds**. Worst case 130 s due to 2 s poll cadence.
6. Verify the next `[supervisor] connected to gemini` line follows within ~5 s and that the resumption handle is reused (no greeting replay).
7. Restore threshold to `0.8`.

## M5 — Mid-mission pose-grounded answer

**Plan: model answers "where are we?" with pose values WITHOUT calling `get_robot_state`**

**Status:** ⏸️ pending operator-led bench

This is the highest-signal real-world test of whether the mission progress poller (Task 3, SILENT WHEN_IDLE FunctionResponses every 1 s carrying current pose) actually penetrates the model's reasoning context.

**Protocol for operator** (run with proxy issue fixed):

1. Wake supervisor.
2. Say: **"Drive forward 1 meter"**. Verify `dispatch_er_mission` fires and the mission poller is ticking (look for SILENT pose updates in journalctl).
3. After ~10 seconds (mission still in progress), say: **"Where are we?"**.
4. Listen to the model's reply. It SHOULD answer with current pose values (x, y, yaw — possibly approximate/rounded). Specifically it should NOT say "let me check" or pause for a tool call.
5. Verify by tailing `journalctl -u ugv-supervisor.service --since "20 seconds ago" | grep -E "tool_call|pose"`:
   - Expected: SILENT pose ticks from poller, then model audio response
   - **NOT expected**: a `tool_call get_robot_state` event in the same turn
6. If the model DOES call `get_robot_state`, the SILENT pose stream isn't reaching its reasoning. This is a regression — file as separate plan.

Bonus: ask "are we moving?" (boolean) and "how much further?" (distance estimate) — both should be answerable from the streamed pose history without a tool call.

## Findings — calibration recommendations (deferred until operator bench)

Once operator-led bench completes, refine these constants in `budget.py`:

- **`audio_tokens_per_s`** (default 25.0): if measured idle rotation timing differs from the deterministic 128 s at threshold=0.1, scale this constant proportionally. Plan suggests raising to ~50 if combined ≥ 50 tok/s.
- **`jpeg_tokens_per_frame`** (default 250.0): **suspected too high.** Current default × 1 fps = 15k tok/min for watch alone, which is 47% of a 32k window per minute — would trip 0.8 threshold in ~2 min of watch. If operator measures actual mission session lifetime ≫ 2 min before rotation, this constant is wildly high; refine downward. If mission rotations actually happen at ~2 min, the constant is plausible and the budget aggressively rotates during missions by design.
- **`_SILENCE_GRACE_S`** in SpeakerReferenceRing (default 0.05 s): if mid-pause artifacts heard during model speech, increase. Operator listens for mid-sentence pop/glitch.

## Open follow-ups

1. **`https_proxy` in container** (Finding A) — high priority, blocks all Live sessions. Fix probably lives in the Dockerfile build args or a `docker run` override on the long-running container. Suggest separate plan: `docs/plans/YYYY-MM-DD-fix-container-proxy-env.md`.
2. **`-e VAR` allowlist in `ugv-supervisor.service`** (Finding B) — medium priority, breaks the documented operator override surface for AEC mode and budget tuning. One-line fix to the unit file (add the four `-e` flags). Could land in a follow-up `chore(supervisor):` commit.
3. **`jpeg_tokens_per_frame` calibration** — needs operator bench for empirical anchor. Until then, watch mode + budget interaction is unverified at production load.
4. **Live AEC measurement on real speaker→mic loop** — synthetic 36.67 dB doesn't generalize. Need operator bench.
5. **Pose-grounded answer regression risk** — if M5 fails (model calls tool instead of grounding), it suggests the SILENT FunctionResponse stream isn't being absorbed into the model's reasoning trace; would require a Task 3 follow-up.
6. **Stop the reconnect-loop noise** — when the websocket can't open, supervisor logs `session error, reconnect in N.Ns` indefinitely. Fine in degraded mode, but consider a cap on backoff (~60 s) to reduce log spam, and an alert hook if reconnect fails for > 5 minutes.

## Bench session pre-flight checklist

Before scheduling the operator bench, verify:

- [ ] Container `https_proxy`/`http_proxy` env unset; `docker exec ugv_waveshare curl -m 5 https://generativelanguage.googleapis.com/` returns HTTP without timeout.
- [ ] `ugv-supervisor.service` unit file includes `-e SUPERVISOR_AEC_MODE -e SUPERVISOR_AEC_DELAY_MS -e SUPERVISOR_BUDGET_WINDOW_TOKENS -e SUPERVISOR_BUDGET_THRESHOLD` in its ExecStart, OR the deferred-to-defaults behavior is acknowledged and operator overrides are achieved by editing `config.py` directly.
- [ ] Jetson clock synced (`timedatectl status` shows synchronized=yes; otherwise force-sync from laptop with `date -u --set=` + `hwclock -w`). Clock skew was observed during this bench (RTC at 1970, system clock ~3 days behind real time) and force-synced to 2026-04-25 08:34 UTC, but NTP sync long-term is fragile.
- [ ] `GOOGLE_API_KEY` in `deploy/supervisor.env` is the real key (not a placeholder).
- [ ] `/health` returns `running:true, model:gemini-2.5-flash-native-audio-latest`.
- [ ] Operator has a way to trigger a real wake event (mic + wake-word service, OR direct `wget --post-data="" http://localhost:8083/open_session`).
