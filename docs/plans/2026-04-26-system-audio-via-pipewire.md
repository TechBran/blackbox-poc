# System Audio via PipeWire/Pulse — Eliminate Direct ALSA Hardware Opens

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop opening raw ALSA hardware (`plughw:CARD=Plus,DEV=0`) from the supervisor's `arecord` and ears' PyAudio. Instead, route both processes through the host's PipeWire audio daemon via its pulse-compatible socket. PipeWire owns the EMEET continuously (and any future Bluetooth speakerphone), multiplexes our clients transparently, and our app code never triggers USB altsetting renegotiation again. Eliminates the 2026-04-26 cascade-failure class architecturally.

**Architecture:** Today the supervisor's `arecord` subprocess and ears' PyAudio each open the EMEET via ALSA's `plughw:CARD=Plus,DEV=0`, which traverses the kernel's `snd-usb-audio` driver and triggers a USB `Set_Interface` request on every open. Under bandwidth contention with the Xitech UVC camera on the same xHCI controller, that request can fail and cascade into a host-controller hang. The fix: a system-managed audio daemon (PipeWire on this Ubuntu) opens the underlying USB audio device once at boot and holds it permanently. Our processes talk to PipeWire via its PulseAudio compatibility socket (`/run/user/1000/pulse/native`). ALSA's `pulse` PCM type and PyAudio's pulse hostapi route capture/playback through PipeWire's mixing layer. App code unchanged — only env vars change. This is the canonical "Linux audio for app developers" model and works the same way for USB and Bluetooth devices.

**Tech Stack:**
- PipeWire 0.3.x (the audio daemon — already installed on this Jetson Ubuntu 22.04)
- pipewire-pulse (the Pulse-compat layer — provides the socket clients use)
- ALSA `pcm.pulse` plugin (`libasound2-plugins`) — lets `arecord -D pulse` route through Pulse/PipeWire
- PyAudio (already in use by ears) — uses ALSA's pulse PCM through portaudio's ALSA hostapi
- Docker bind-mounts: `/run/user/1000/pulse/native` (Unix socket) + `~jetson/.config/pulse/cookie` (auth token)
- pytest for any unit-testable bits (most of this plan is config + integration-test territory)

**Out of scope (deferred):**
- Restoring the camera mic + JMTek combo (we're committed to EMEET as production audio)
- Hardware mitigations (separate USB controller for EMEET, powered USB hub)
- Reverse-engineering EMEET's vendor HID protocol
- Migrating from `pipewire-media-session` to WireGuard's `wireplumber` (not installed on this Jetson; pipewire-media-session is fine)
- ALSA `dsnoop` (superseded by this plan — see `docs/plans/2026-04-26-dsnoop-mic-fanout.md` for the abandoned alternative)

---

## Empirical Knowledge Carried Forward

These regression guards from prior work MUST NOT be reintroduced:

| # | Lesson | Touched by |
|---|---|---|
| 1 | EMEET cascade can be triggered by ANY direct ALSA `arecord -D plughw:CARD=Plus,DEV=0` even from a debug shell. Avoid all such probes during this plan's preflight | Pre-flight (read pulse/pipewire state only — never arecord/aplay on EMEET) |
| 2 | Container has stale baked-in `https_proxy` that breaks Gemini Live; `start_supervisor.sh` and `start_ears.sh` both `unset HTTP_PROXY HTTPS_PROXY ...` at the top; preserve | Untouched (no script edits in this plan) |
| 3 | `scripts/sync-ugv-tools.sh` clobbers `supervisor.env` GOOGLE_API_KEY with template placeholder; post-sync ritual required. We use TARGETED rsync, never the full sync script | Task 5 (env update via direct edit, not sync script) |
| 4 | systemd unit `docker exec -e` allowlist must include any new env vars or they get silently dropped (per `eb22ea4`) | Tasks 3, 5 (new PULSE_SERVER + XDG_RUNTIME_DIR vars) |
| 5 | Diagnostic `[diag] mic upload sess#N` instrumentation in `pump_mic` is load-bearing for benches; preserve | Untouched (mic source path is upstream of pump_mic) |
| 6 | `aec_mode=passthrough` is the production audio mode (EMEET hardware AEC); preserve | Untouched (aec mode is independent of mic source) |
| 7 | Watch FPS=0.25, jpeg_tokens_per_frame=100 (tuned 2026-04-26); preserve | Untouched |
| 8 | Two services on Jetson host: `ugv-supervisor` + `ugv-ears`. The MUTE_FLAG handoff at `/tmp/ugv_ears_muted` synchronizes them today. With Pulse, both processes can hold open simultaneously — handoff becomes a no-op overhead. **Leave the MUTE_FLAG mechanism in place for now**; removing it is a separate cleanup (DRY/YAGNI) | Tasks 3, 4 (no MUTE_FLAG changes) |

---

## Pre-flight: Audio daemon stabilization (READ-ONLY)

**This is a hard gate. Do NOT proceed to Task 1 until preflight passes.** The preflight is read-only — uses `pactl`, `pw-cli`, `systemctl status` only. NO `arecord`, NO `aplay` on the EMEET (those would risk re-triggering the cascade we're trying to prevent).

**Step P-1: Verify PipeWire is the audio daemon.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'systemctl --user list-units --type=service --state=running | grep -aE "(pipe|pulse|audio)"'
```

Expected output should include `pipewire.service` and `pipewire-media-session.service` running. If `pulseaudio.service` is also running and stable (not flapping), we have a competing-daemon situation that Task 1 fixes.

**Step P-2: Verify the Pulse-compat socket exists and is owned by the jetson user.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'ls -la /run/user/1000/pulse/native /home/jetson/.config/pulse/cookie'
```

Expected:
- `/run/user/1000/pulse/native` is a Unix socket, jetson:jetson, world rw
- `/home/jetson/.config/pulse/cookie` exists, 256 bytes, jetson-only readable

**Step P-3: Wait at least 30 seconds post-boot for pipewire-media-session to enumerate ALSA cards, then query `pactl`.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'sleep 30; pactl list short sources 2>&1; echo "---"; pactl list short sinks 2>&1; echo "---"; pactl info 2>&1 | grep -aE "(Server Name|Default Source|Default Sink)"'
```

Expected: at least one source AND one sink listed. The EMEET should appear as `alsa_input.usb-EMEET_*` (source) and `alsa_output.usb-EMEET_*` (sink). If pactl returns empty, the audio daemon is broken — Task 1 must fix it before any other task proceeds.

**Step P-4: Save preflight result to `docs/plans/notes/2026-04-26-pulse-preflight.md`.**

Record: which audio daemon is running, whether pactl returns sources/sinks, EMEET source/sink names if visible. **If preflight FAILS (no sources/sinks visible), Task 1's work is on the critical path and must execute first.** If preflight PASSES, Task 1 may simplify (just verify, no remediation needed).

---

## Target Repository Layout (after this plan)

```
docs/ugv-beast/setup/ugv_tools_api/
├── deploy/
│   ├── ugv-supervisor.service      # MODIFIED: + bind-mount pulse socket + cookie, + PULSE_SERVER env
│   ├── ugv-ears.service            # MODIFIED: same bind-mount + env
│   ├── supervisor.env              # MODIFIED: SUPERVISOR_MIC=pulse, SUPERVISOR_SPK=pulse
│   └── ears.env                    # MODIFIED: MIC_DEVICE_HINT=pulse, UGV_ALSA_DEVICE=pulse
└── tests/
    └── supervisor/
        └── test_pulse_routing.py   # NEW: container-level test that arecord -D pulse works
```

`docs/plans/notes/2026-04-26-pulse-preflight.md` — preflight diagnosis record.
`docs/plans/notes/2026-04-26-pulse-bench.md` — Task 7 operator-led bench record.

---

## Implementation Tasks

### Task 1: Stabilize the audio daemon (mask pulseaudio, confirm pipewire-pulse owns the socket)

**Goal:** ensure ONE stable audio daemon on the host, without competing pulseaudio.service flapping.

**Files:**
- No code changes — operates on Jetson's user systemd services

**Step 1: If preflight P-1 showed `pulseaudio.service` flapping (activating→failing→activating), mask it.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'systemctl --user stop pulseaudio.service 2>/dev/null; \
   systemctl --user mask pulseaudio.service; \
   systemctl --user mask pulseaudio.socket'
```

This prevents systemd from auto-restarting it. PipeWire continues running.

**Step 2: Ensure `pipewire-pulse.service` is enabled and running.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'systemctl --user --now enable pipewire-pulse.service; \
   systemctl --user status pipewire-pulse.service | grep -aE "(Active|Loaded)" | head'
```

Expected: `Active: active (running)`.

**Step 3: Wait 30 seconds, then re-query `pactl` (read-only).**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'sleep 30; \
   pactl info | grep -aE "Server Name"; \
   pactl list short sources | head; \
   pactl list short sinks | head'
```

Expected: `Server Name: PulseAudio (on PipeWire 0.3.x)` and at least one EMEET source + sink visible.

**Step 4: If sources/sinks STILL empty after Step 3, fall back to manual `module-alsa-card` load.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'pactl load-module module-alsa-card device_id=Plus name=emeet card_name=alsa_card.emeet'
```

Re-query pactl. If EMEET appears now, the auto-detect (`module-udev-detect`) was misconfigured — file an issue note in `docs/plans/notes/2026-04-26-pulse-preflight.md` for follow-up but proceed with the manual load path.

**Step 5: Save final state to preflight notes file. No commit needed (host-side systemd state, not repo content).**

---

### Task 2: Add container plumbing — bind-mounts + env vars

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-supervisor.service`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-ears.service`

**Critical context:** the container `ugv_waveshare` is ALREADY RUNNING. Adding bind-mounts to a running container requires `docker stop ugv_waveshare && docker run ...` with new mounts — heavy. Instead, this plan uses `docker exec` with file/socket access via the systemd unit's existing exec, plus env vars passed via `-e`. The Pulse socket is read by the in-container client via the bind-mount path; we'll mount it via the existing container's filesystem if the docker run command had `-v /run/user:/run/user` (likely already does for systemd).

**Step 1: Verify whether the running container already has `/run/user/1000` accessible.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare ls -la /run/user/1000/pulse/native 2>&1 | head'
```

If the socket is visible: bind-mount already exists, proceed to Step 3. If "No such file or directory": Step 2 is required.

**Step 2: If the bind-mount is missing, add it via container restart.**

```bash
# This requires docker stop + docker run with new mounts. The existing
# container creation lives in the robot's deploy scripts (likely
# docker-compose.yml or a setup script). Find and modify:
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'find /home/jetson -name "docker-compose*.yml" -o -name "*.sh" | xargs grep -l "ugv_waveshare" 2>/dev/null | head'
```

Modify the docker-compose.yml or run script to add:
```yaml
volumes:
  - /run/user/1000/pulse/native:/run/user/1000/pulse/native:ro
  - /home/jetson/.config/pulse/cookie:/root/.config/pulse/cookie:ro
```

Then `docker stop ugv_waveshare && docker compose up -d` (or equivalent).

**Step 3: Modify `ugv-supervisor.service` to set PULSE_SERVER env.**

In the existing `ExecStart=/usr/bin/docker exec -e ... ugv_waveshare /bin/bash ...`, add three new env-pass entries to the `-e` allowlist:
```
-e PULSE_SERVER \
-e PULSE_COOKIE \
-e XDG_RUNTIME_DIR \
```

The systemd unit already has an `EnvironmentFile=` reading from `supervisor.env`; we'll set the values there in Task 5. The `-e` flag tells docker exec to FORWARD env vars from the systemd unit's environment into the container.

**Step 4: Same modification for `ugv-ears.service` `-e` allowlist.**

**Step 5: Sync to Jetson, daemon-reload, restart services.**

```bash
sshpass -p 'jetson' rsync -e 'ssh -o StrictHostKeyChecking=no' \
  /home/ai-black-box-fc/.../deploy/ugv-supervisor.service \
  /home/ai-black-box-fc/.../deploy/ugv-ears.service \
  jetson@192.168.1.155:/tmp/

sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'echo jetson | sudo -S cp /tmp/ugv-supervisor.service /tmp/ugv-ears.service /etc/systemd/system/ && \
   sudo systemctl daemon-reload'
```

Don't restart services yet — wait until env file is updated in Task 5 so the new envs take effect.

**Step 6: Commit on Jetson.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'cd /home/jetson/ugv_ws_waveshare && \
   git add ugv_tools_api/deploy/ugv-supervisor.service ugv_tools_api/deploy/ugv-ears.service && \
   git commit -m "feat(deploy): plumb pipewire-pulse socket env vars to supervisor + ears"'
```

---

### Task 3: Verify Pulse routing works inside container (read-only test)

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_pulse_routing.py`

**Goal:** an automated test that confirms `pactl info` works from inside the container with the new bind-mount + env, and that the EMEET source is visible. Read-only — no audio capture/playback. Skip-if-not-configured guard for non-Jetson hosts.

**Step 1: Write the failing test.**

```python
"""Pulse routing smoke test (container-side).

Confirms the ugv_waveshare container can talk to the host's PipeWire
audio daemon via the pulse-compat socket. Read-only: queries `pactl info`
and `pactl list short sources` — never opens an audio stream.

Skipped on hosts without the bind-mount (i.e. local laptop dev).
"""
import os
import subprocess
import shutil
import pytest


def _pulse_env_present() -> bool:
    return (
        os.environ.get("PULSE_SERVER", "").startswith("unix:")
        and shutil.which("pactl") is not None
    )


pytestmark = pytest.mark.skipif(
    not _pulse_env_present(),
    reason="PULSE_SERVER not set or pactl unavailable (run inside container)",
)


def test_pactl_info_responds():
    """pactl info should return server name within 5 seconds."""
    out = subprocess.check_output(
        ["pactl", "info"], stderr=subprocess.STDOUT, timeout=5,
    ).decode("ascii", errors="replace")
    assert "Server Name" in out, f"pactl info missing Server Name: {out[:300]}"
    # PipeWire-Pulse identifies as 'PulseAudio (on PipeWire X.Y.Z)'
    assert "PipeWire" in out or "PulseAudio" in out, f"unexpected server: {out[:300]}"


def test_emeet_source_visible():
    """EMEET should appear in `pactl list short sources` output.
    On this Jetson the EMEET source name pattern is alsa_input.usb-EMEET_*."""
    out = subprocess.check_output(
        ["pactl", "list", "short", "sources"], stderr=subprocess.STDOUT, timeout=5,
    ).decode("ascii", errors="replace")
    assert "EMEET" in out or "OfficeCore" in out or "Plus" in out, (
        f"EMEET source not found in pactl output: {out[:500]}"
    )


def test_emeet_sink_visible():
    """EMEET should appear in `pactl list short sinks` output."""
    out = subprocess.check_output(
        ["pactl", "list", "short", "sinks"], stderr=subprocess.STDOUT, timeout=5,
    ).decode("ascii", errors="replace")
    assert "EMEET" in out or "OfficeCore" in out or "Plus" in out, (
        f"EMEET sink not found in pactl output: {out[:500]}"
    )
```

**Step 2: Run inside container, confirm SKIPPED (env not yet wired).**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && python3 -m pytest tests/supervisor/test_pulse_routing.py -v 2>&1 | tail -10"'
```

Expected: 3 SKIPPED (PULSE_SERVER not set yet — that's the right red state). After Task 5 wires env, these turn GREEN.

**Step 3: Commit.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'cd /home/jetson/ugv_ws_waveshare && \
   git add ugv_tools_api/tests/supervisor/test_pulse_routing.py && \
   git commit -m "test(supervisor): pulse routing smoke test (skipped until env wired)"'
```

---

### Task 4: Verify `pactl` is installed in the container

**Files:**
- (No file changes — install only if missing)

**Goal:** ensure `pactl` (from `pulseaudio-utils` package) is installed inside the container so Task 3's tests can execute and we can debug audio from inside.

**Step 1: Check.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare which pactl 2>&1'
```

If found: skip remaining steps.

**Step 2: Install if missing.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "apt-get update && apt-get install -y pulseaudio-utils libasound2-plugins"'
```

`libasound2-plugins` provides ALSA's `pcm.pulse` plugin so `arecord -D pulse` and `aplay -D pulse` work inside the container.

**Step 3: Verify installation.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "pactl --version && cat /usr/share/alsa/alsa.conf.d/99-pulseaudio-default.conf 2>/dev/null | head"'
```

Expected: pactl version printed; ALSA pulse-default config exists (proves libasound2-plugins is loadable).

**Step 4: No commit needed** (container-state change, not repo content). Document in preflight notes that pulseaudio-utils + libasound2-plugins were installed at HH:MM:SS.

---

### Task 5: App config update — point mic/spk env vars at `pulse`

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/supervisor.env` (laptop + Jetson)
- Modify: `/home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/ears.env` (Jetson only — laptop has no copy)

**Step 1: Write the failing test (config defaults).**

Append to `tests/supervisor/test_config.py`:

```python
def test_supervisor_mic_default_uses_pulse_after_pipewire_routing(monkeypatch):
    """After Option 'pulse-routing', the production default for SUPERVISOR_MIC
    is `pulse` (the ALSA PCM plugin that routes through PipeWire-Pulse), not
    a direct hw: device. Pins the new default."""
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    monkeypatch.delenv("SUPERVISOR_MIC", raising=False)
    monkeypatch.delenv("SUPERVISOR_SPK", raising=False)
    from ugv_tools_api.supervisor.config import load
    cfg = load()
    assert cfg.mic_device == "pulse", f"mic default reverted: {cfg.mic_device!r}"
    assert cfg.spk_device == "pulse", f"spk default reverted: {cfg.spk_device!r}"
```

**Step 2: Run, confirm RED.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && python3 -m pytest tests/supervisor/test_config.py::test_supervisor_mic_default_uses_pulse_after_pipewire_routing -v 2>&1 | tail -10"'
```

Expected: FAIL — current default is `plughw:CARD=Plus,DEV=0`.

**Step 3: Update `config.py` defaults.**

Edit `ugv_tools_api/supervisor/config.py:load()`:

```python
# Before:
mic_device=os.environ.get("SUPERVISOR_MIC", "plughw:CARD=Plus,DEV=0"),
spk_device=os.environ.get("SUPERVISOR_SPK", "plughw:CARD=Plus,DEV=0"),
# After:
mic_device=os.environ.get("SUPERVISOR_MIC", "pulse"),
spk_device=os.environ.get("SUPERVISOR_SPK", "pulse"),
```

**Step 4: Update `supervisor.env` (laptop + Jetson):**

```
SUPERVISOR_MIC=pulse
SUPERVISOR_SPK=pulse
PULSE_SERVER=unix:/run/user/1000/pulse/native
PULSE_COOKIE=/root/.config/pulse/cookie
XDG_RUNTIME_DIR=/run/user/1000
```

**Step 5: Update `ears.env` (Jetson only):**

```
MIC_DEVICE_HINT=pulse
UGV_ALSA_DEVICE=pulse
PULSE_SERVER=unix:/run/user/1000/pulse/native
PULSE_COOKIE=/root/.config/pulse/cookie
XDG_RUNTIME_DIR=/run/user/1000
```

**Step 6: Sync, restart services in dependency order.**

```bash
# Sync env files + config.py
sshpass -p 'jetson' rsync -e 'ssh -o StrictHostKeyChecking=no' \
  /home/ai-black-box-fc/.../deploy/supervisor.env \
  /home/ai-black-box-fc/.../ugv_tools_api/supervisor/config.py \
  jetson@192.168.1.155:/home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/  # adjust paths

# Restart supervisor first (which restarts the existing arecord with new env)
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'echo jetson | sudo -S systemctl restart ugv-supervisor.service ugv-ears.service'

# Wait 10s, verify both alive
sleep 10
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'systemctl is-active ugv-supervisor.service ugv-ears.service'
```

**Step 7: Re-run Task 3's pulse routing test — should now GREEN.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && python3 -m pytest tests/supervisor/test_pulse_routing.py tests/supervisor/test_config.py -v 2>&1 | tail -20"'
```

Expected: 3 + 1 PASS (3 pulse routing + 1 config default).

**Step 8: Commit.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'cd /home/jetson/ugv_ws_waveshare && \
   git add ugv_tools_api/supervisor/config.py ugv_tools_api/deploy/supervisor.env tests/supervisor/test_config.py && \
   git commit -m "feat(supervisor): route arecord+aplay through pipewire-pulse via PCM=pulse"'
```

---

### Task 6: Live operator-led bench

**Files:**
- Create: `docs/plans/notes/2026-04-26-pulse-bench.md`

This is the dispositive test. The unit tests prove pulse plumbing reaches the container; only the live bench proves end-to-end audio works AND the cascade-class failure is gone.

**Step 1: Pre-bench checks (all read-only).**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "pactl info 2>&1 | grep Server"; \
   docker exec ugv_waveshare bash -c "env | grep -aE PULSE"; \
   systemctl is-active ugv-supervisor.service ugv-ears.service'
```

Expected: pactl returns server name; PULSE_SERVER + cookie set in container env; both services active.

**Step 2: Operator runs N=5 wake/speak/end cycles.**

For each cycle:
1. Operator says "Black Box Flight Recorder, [a question]"
2. Verify session opens (`[supervisor] session opened (raw-WS)`, `AEC mode=passthrough`)
3. Operator continues for ~30s (multi-turn)
4. Wait for budget rotate or operator says "stop"
5. Verify ears reacquires and is listening
6. Wait 10 seconds idle
7. **Repeat from step 1**

**Verification matrix:**

| C# | Criterion | How to verify |
|---|---|---|
| C1 | Wake fires every cycle (5/5) | grep ears journal for `WAKE` events; expect 5 |
| C2 | Zero new `usb_set_interface failed` in dmesg during the bench | `dmesg -T \| grep usb_set_interface` count before/after — must match |
| C3 | EMEET stays enumerated throughout (no `usb 1-2.4: USB disconnect`) | dmesg grep |
| C4 | Pulse holds the EMEET continuously across all 5 cycles | `fuser /dev/snd/pcmC2D0c` shows pipewire-pulse PID, never empty |
| C5 | Session diag shows `[supervisor] AEC mode=passthrough` (full-duplex still works through pulse) | grep supervisor journal |
| C6 | Mic upload health: `aec=0, halfduplex=0, passthrough=N` (passthrough mode, raw mic data) | grep `[diag] mic upload sess#` |
| C7 | No regressions in existing tests | `pytest tests/supervisor/` count matches pre-plan + new tests |

**Step 3: Save bench record** to `docs/plans/notes/2026-04-26-pulse-bench.md` with C1-C7 results.

**Step 4: Commit.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'cd /home/jetson/ugv_ws_waveshare && \
   git add docs/plans/notes/2026-04-26-pulse-bench.md && \
   git commit -m "docs(supervisor): pipewire-pulse routing bench record"'
```

---

### Task 7: Cleanup + memory + snapshot

**Files:**
- Update: `~/.claude/projects/.../memory/ugv_supervisor_2_5_async.md`
- Mint snapshot via `/chat/save`

**Step 1: Update memory entry.**

Append to `ugv_supervisor_2_5_async.md`:

```markdown
## System audio via PipeWire-Pulse (2026-04-26 — production audio architecture)

**Plan:** `docs/plans/2026-04-26-system-audio-via-pipewire.md`. Snapshot: `SNAP-20260426-NNNN`.

**Problem solved:** Direct ALSA `plughw:CARD=Plus,DEV=0` opens by both supervisor and ears triggered USB altsetting renegotiation on every session boundary. Under bandwidth contention with the Xitech UVC camera, that occasionally cascade-failed and took the entire Jetson USB tree down (2026-04-26 events). Required full reboots to recover.

**Architecture:**
- Host's PipeWire (audio daemon) + pipewire-pulse (compat layer) own the EMEET continuously.
- Container `ugv_waveshare` bind-mounts `/run/user/1000/pulse/native` (socket) and `~jetson/.config/pulse/cookie` (auth token).
- `PULSE_SERVER=unix:/run/user/1000/pulse/native` env var routes app code through pipewire-pulse.
- Both supervisor (arecord -D pulse) and ears (PyAudio with pulse hostapi) open `pulse`. PipeWire mixes their access.
- ONE USB altsetting negotiation at boot when pipewire's `module-udev-detect` claims the card. Never renegotiated until reboot.

**Production env (both supervisor.env + ears.env):**
- `SUPERVISOR_MIC=pulse` / `SUPERVISOR_SPK=pulse`
- `MIC_DEVICE_HINT=pulse` / `UGV_ALSA_DEVICE=pulse`
- `PULSE_SERVER=unix:/run/user/1000/pulse/native`
- `PULSE_COOKIE=/root/.config/pulse/cookie`
- `XDG_RUNTIME_DIR=/run/user/1000`

**Key gotcha — pulseaudio.service competes with pipewire.** On this Jetson the user-session `pulseaudio.service` was flapping (activating/failing every 268ms) because PipeWire owned the audio devices. Fix: `systemctl --user mask pulseaudio.service pulseaudio.socket`. Then `pipewire-pulse.service` is the sole pulse-compat provider.

**Bonus side-effect:** Bluetooth speakerphones now Just Work via the same path. Pair through Ubuntu's normal BT setup, PipeWire adopts the device as default sink/source, our app code keeps working unchanged.

**MUTE_FLAG handoff at `/tmp/ugv_ears_muted`:** preserved as no-op overhead. Removing it is a separate cleanup.

**Rollback path:** `SUPERVISOR_MIC=plughw:CARD=Plus,DEV=0` + `MIC_DEVICE_HINT=EMEET` env override reverts to direct hw: opens. Restart services. Same audio path as pre-pulse-routing. PipeWire keeps holding the device but our processes ignore it.
```

**Step 2: Mint snapshot via `/snapshot-dev`** capturing the bench results + architecture.

**Step 3: Commit memory + verify snapshot indexed.**

```bash
# Memory file lives in ~/.claude — not in git. Just save.
# Snapshot is auto-indexed by BlackBox.
```

---

## Post-Implementation Verification

Before considering this plan done:

- [ ] Preflight (P-1 to P-4) passed before any code change landed
- [ ] All Tasks 1-7 committed on Jetson `ros2-humble-develop`
- [ ] `tests/supervisor/test_pulse_routing.py` PASSES inside container (3 tests)
- [ ] `tests/supervisor/test_config.py::test_supervisor_mic_default_uses_pulse_after_pipewire_routing` PASSES
- [ ] Full supervisor test suite count stays at current + 4 new = N+4 passing (no regressions)
- [ ] Operator bench (Task 6) shows 5/5 wake-word fires across multiple cycles
- [ ] Zero new `usb_set_interface failed` dmesg events during the bench
- [ ] EMEET stays enumerated throughout the bench (no `usb 1-2.4: USB disconnect`)
- [ ] Memory entry updated; snapshot minted with embedding

## Rollback Path

If pulse routing breaks audio for any reason mid-bench:

```bash
# Revert env to direct hw: (same as pre-this-plan)
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'cd /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy && \
   sed -i "s|^SUPERVISOR_MIC=.*|SUPERVISOR_MIC=plughw:CARD=Plus,DEV=0|" supervisor.env && \
   sed -i "s|^SUPERVISOR_SPK=.*|SUPERVISOR_SPK=plughw:CARD=Plus,DEV=0|" supervisor.env && \
   sed -i "s|^MIC_DEVICE_HINT=.*|MIC_DEVICE_HINT=EMEET|" ears.env'

# Restart services in old config
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'echo jetson | sudo -S systemctl restart ugv-supervisor.service ugv-ears.service'
```

5-minute recovery to pre-pulse-routing state. PipeWire keeps owning the device; our processes just ignore it and open hw: directly (same as before the plan).

## Out-of-Plan Follow-Ups

After this lands and proves stable:

1. **Remove the MUTE_FLAG handoff entirely** — with pulse, multi-client is native. Trivial cleanup.
2. **Document Bluetooth speakerphone path** — once we test a BT device, write a doc on how to pair + how PipeWire handles it (env vars don't change, just `pactl set-default-source/sink` selects it).
3. **`pactl set-card-profile` for EMEET** — the EMEET supports multiple Pulse profiles (HSP, HFP, A2DP for BT-mode; analog-input/output for USB-mode). May want to lock it to a specific profile for predictability.
4. **Cascade-stress test in CI** — currently no automated test that proves the cascade doesn't recur. After bench validates the architecture, port the bench's grep-dmesg-for-failures pattern into a pytest stress test.
5. **Move EMEET to dedicated USB controller** — PipeWire eliminates app-level cascade exposure but the underlying USB bandwidth fight with the Xitech UVC still exists. If we ever see PipeWire-side errors, hardware mitigation is the next escalation.
