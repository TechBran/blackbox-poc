# ALSA `dsnoop` Mic Fan-Out — Eliminate USB Altsetting Cascade

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the supervisor↔ears mic handoff (which forces a USB altsetting renegotiation on every session boundary) with an ALSA `dsnoop` capture fan-out that opens the EMEET's underlying USB Audio interface ONCE per supervisor lifetime and lets multiple software clients (ears, supervisor's `arecord`) attach to that single open. A new `ugv-mic-keepalive.service` holds the dsnoop open continuously so the underlying `hw:` never closes, even between client opens. This prevents the bandwidth-cascade failure that took the entire Jetson USB tree down on 2026-04-26.

**Architecture:** Today both processes open the EMEET capture device directly via `plughw:CARD=Plus,DEV=0` (or `hint='EMEET'` for ears' PyAudio). Each `open()` call traverses ALSA's plug → snd-usb-audio → USB Audio Class altsetting negotiation. Under bandwidth contention with the Xitech UVC camera on the same xHCI controller, the renegotiation can fail with `usb_set_interface failed (-19); Not enough bandwidth for altsetting`, cascading into a host-controller hang. The fix: a `/etc/asound.conf` (inside the `ugv_waveshare` container) defines `dsnoop_emeet` as a fan-out PCM whose underlying slave is `hw:CARD=Plus,DEV=0` opened at fixed parameters (S16_LE / 16000 Hz / mono). All clients open `dsnoop_emeet` instead of `plughw:`. A keep-alive process reads-and-discards from `dsnoop_emeet` continuously so the underlying `hw:` open is permanent. No further altsetting renegotiation occurs after first boot.

**Tech Stack:**
- ALSA `dsnoop` plugin (kernel-shipped, no install needed) — capture fan-out
- `arecord` (existing) for both keep-alive holder and supervisor's MicStream subprocess
- PyAudio (existing) for ears — substring-matches the dsnoop PCM name
- systemd unit dependency ordering (`ugv-mic-keepalive.service` → `ugv-supervisor.service` Before/After)
- pytest + pytest-asyncio (existing) for the cascade-regression test

**Out of scope (deferred):**
- `dmix` for output (speaker fan-out) — supervisor currently owns speaker exclusively, no contention
- Reverse-engineering EMEET's vendor-specific HID reports 7/8 (would need EMEET Windows app traffic capture)
- Hardware mitigations: separate USB controller for EMEET, USB-3 hub power isolation
- Merging ears into the supervisor process (Option B1d — bigger refactor for later)
- Restoring the prior camera mic + JMTek speaker setup (we're committed to EMEET as the production audio path)

---

## Empirical Knowledge Carried Forward

These regression guards from prior plans MUST NOT be reintroduced by this work:

| # | Lesson | Touched by |
|---|---|---|
| 1 | EMEET appears as `card 2: Plus [EMEET OfficeCore M0 Plus]` (`hw:CARD=Plus,DEV=0`) — substring "EMEET" or card name "Plus" both work for hint matching | Task 1 (dsnoop slave references `hw:CARD=Plus,DEV=0` literal), Task 4 (ears hint) |
| 2 | EMEET native rate is 48 kHz; supervisor + ears both want 16 kHz mono. ALSA's `plug` layer (or in our case the `dsnoop` slave config with explicit format/rate) handles the conversion | Task 1 (slave PCM at 16k via plug, dsnoop at 16k) |
| 3 | The container has NO `/etc/asound.conf` today — we're starting from a clean slate. Host has a Tegra-specific config symlink we leave alone | Task 1 |
| 4 | Supervisor runs `arecord` as a subprocess inside `MicStream.start` (`audio_io.py:188`); device name comes from `cfg.mic_device` env-resolved at startup | Task 3 |
| 5 | Ears uses PyAudio with substring-matched device hint (`MIC_DEVICE_HINT` in `ears.env`); the device name PyAudio sees is the ALSA pcm.NAME, e.g. `dsnoop_emeet` would match hint `dsnoop_emeet` | Task 4 |
| 6 | `MUTE_FLAG_PATH=/tmp/ugv_ears_muted` handoff was originally needed because two processes can't simultaneously open the same `hw:` device. With dsnoop the handoff becomes unnecessary — clients can coexist. **Keep the MUTE_FLAG mechanism intact for now** (don't rip it out in this plan) — it's a no-op overhead with dsnoop, and removing it is a separate cleanup | Task 3, Task 4 (no change to MUTE_FLAG logic) |
| 7 | Container `start_supervisor.sh` and `start_ears.sh` `unset HTTPS_PROXY HTTP_PROXY` to bypass a stale baked-in proxy (regression guard from `eb22ea4`); preserve | Untouched |
| 8 | systemd unit's `docker exec -e` allowlist must include any new env vars or they get silently dropped (per `eb22ea4` lesson) | Task 3 (no new vars), Task 2 (new keepalive service) |
| 9 | `scripts/sync-ugv-tools.sh` clobbers `supervisor.env` GOOGLE_API_KEY with template placeholder — post-sync ritual required | Task 7 (cleanup ritual reminder in operator README) |
| 10 | Diagnostic `[diag] mic upload sess#N` instrumentation in `pump_mic` is load-bearing for benches; preserve | Untouched (mic source is upstream; pump_mic doesn't care) |

---

## Pre-flight: Verify dsnoop works on this hardware/kernel/container combo

Before writing config, prove dsnoop fan-out is feasible. This is a 5-minute manual smoke test, not a code change.

**Step P-1: Stop ears + supervisor temporarily so we have a clean device.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S systemctl stop ugv-ears.service ugv-supervisor.service'
```

**Step P-2: Inside the container, write a temporary `/tmp/asound-test.conf`** with a dsnoop config (do NOT touch `/etc/asound.conf` yet):

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -c "cat > /tmp/asound-test.conf << ASEOF
pcm.dsnoop_emeet_test {
    type dsnoop
    ipc_key 1024
    slave {
        pcm \"hw:CARD=Plus,DEV=0\"
        rate 16000
        format S16_LE
        channels 1
        period_size 320
        buffer_size 1920
    }
}
ASEOF
"'
```

**Step P-3: Open TWO simultaneous arecord readers** against `dsnoop_emeet_test` and confirm both stream:

```bash
# First reader (background) — drains 5 seconds, writes byte count
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -c "ALSA_CONFIG_PATH=/tmp/asound-test.conf timeout 5 arecord -D dsnoop_emeet_test -f S16_LE -r 16000 -c 1 -t raw 2>/dev/null | wc -c"' &
sleep 1
# Second reader simultaneously — also drains 5 seconds
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -c "ALSA_CONFIG_PATH=/tmp/asound-test.conf timeout 5 arecord -D dsnoop_emeet_test -f S16_LE -r 16000 -c 1 -t raw 2>/dev/null | wc -c"'
wait
```

Expected: BOTH readers print byte counts ≈ `5 * 16000 * 2 = 160000` bytes (5 seconds of 16-bit mono at 16 kHz). If only one reader gets bytes and the other reports `Device or resource busy`, dsnoop isn't fanning out — investigate `ipc_key` collision or kernel ALSA version.

**Step P-4: Restart the production services and document the result.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S systemctl start ugv-supervisor.service ugv-ears.service'
```

Save the result (PASS/FAIL with byte counts) to `docs/plans/notes/2026-04-26-dsnoop-preflight.md`. **Only proceed to Task 1 if the preflight PASSES.** If it FAILS, the dsnoop architecture won't work on this kernel — escalate to Option B1d (merge ears into supervisor) before any code changes.

---

## Target Repository Layout (after this plan)

```
docs/ugv-beast/setup/ugv_tools_api/
├── deploy/
│   ├── asound.conf                 # NEW: ALSA fan-out config, mounted into container
│   ├── start_keepalive.sh          # NEW: arecord-discard loop wrapper
│   ├── ugv-mic-keepalive.service   # NEW: systemd unit for the keepalive holder
│   ├── ugv-supervisor.service      # MODIFIED: After=ugv-mic-keepalive.service + bind-mount asound.conf
│   ├── ugv-ears.service            # MODIFIED: After=ugv-mic-keepalive.service
│   ├── supervisor.env              # MODIFIED: SUPERVISOR_MIC=dsnoop_emeet
│   └── ears.env                    # MODIFIED: MIC_DEVICE_HINT=dsnoop_emeet
└── tests/
    └── supervisor/
        └── test_dsnoop_resilience.py # NEW: cascade-regression stress test
```

`docs/plans/notes/2026-04-26-dsnoop-preflight.md` — preflight smoke test record.
`docs/plans/notes/2026-04-26-dsnoop-bench.md` — Task 6 operator-led bench record.

---

## Implementation Tasks

### Task 1: Author the ALSA dsnoop config

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/deploy/asound.conf`

**Step 1: Write the config.**

```
# ALSA fan-out config for the EMEET conferencing speakerphone.
#
# Defines a `dsnoop_emeet` PCM that wraps the EMEET's underlying USB
# capture interface and lets multiple processes share a single open.
# This eliminates the per-session USB altsetting renegotiation that
# triggered a bandwidth-cascade USB-tree collapse on 2026-04-26.
#
# Slave parameters:
#   pcm        — direct hw: open (no plug layer; we set rate/format here)
#   rate       — 16000: matches what supervisor + ears both request
#   format     — S16_LE: standard PCM, what the wake-word + Gemini Live want
#   channels   — 1: mono. EMEET native is stereo; ALSA does the L/R mixdown
#   period_size 320 — 20 ms at 16 kHz; matches supervisor chunk_ms
#   buffer_size 1920 — ~120 ms; large enough to absorb scheduler jitter
#
# All clients (ears, supervisor's arecord, ugv-mic-keepalive) open
# `dsnoop_emeet` and inherit these parameters.

pcm.dsnoop_emeet {
    type dsnoop
    ipc_key 1024
    slave {
        pcm "hw:CARD=Plus,DEV=0"
        rate 16000
        format S16_LE
        channels 1
        period_size 320
        buffer_size 1920
    }
}

# Convenience aliases so tools like `arecord -D plug:dsnoop_emeet` work
# if a client requests rate-conversion (none should today).
pcm.!default {
    type plug
    slave.pcm "dsnoop_emeet"
}
```

**Step 2: No tests at this step (config-only).** Verified empirically in the preflight. Move to Task 2.

**Step 3: Commit.**

```bash
# On Jetson (laptop is not a git repo)
sshpass -p 'jetson' rsync ... deploy/asound.conf jetson@.../ugv_tools_api/deploy/asound.conf
sshpass -p 'jetson' ssh jetson@192.168.1.155 'cd /home/jetson/ugv_ws_waveshare && git add ugv_tools_api/deploy/asound.conf && git commit -m "feat(deploy): add ALSA dsnoop config for EMEET fan-out"'
```

---

### Task 2: Create `ugv-mic-keepalive` systemd service + start script

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/deploy/start_keepalive.sh`
- Create: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-mic-keepalive.service`

**Step 1: Author the start script.**

```bash
#!/bin/bash
# ugv-mic-keepalive: holds the dsnoop_emeet capture stream open
# continuously, reads-and-discards. Without this, the underlying hw:
# open closes whenever neither ears nor supervisor has it, and the
# next reopen triggers altsetting renegotiation we want to avoid.
#
# arecord with discard-to-/dev/null is the simplest implementation —
# kernel does the work; we just need a process that keeps the FD open.

set -eo pipefail
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy

# Bind-mount our asound.conf so dsnoop_emeet is defined.
# Container path: /etc/asound.conf (the systemd unit handles the bind).

# Restart-on-failure handled by systemd (see unit file). If arecord exits
# unexpectedly (USB device disappears), let systemd restart us.
exec arecord \
    -D dsnoop_emeet \
    -f S16_LE \
    -r 16000 \
    -c 1 \
    -t raw \
    --quiet \
    > /dev/null 2>> /tmp/ugv-mic-keepalive.stderr
```

Make it executable:
```bash
chmod +x deploy/start_keepalive.sh
```

**Step 2: Author the systemd unit.**

```ini
[Unit]
Description=UGV Mic Keepalive — holds dsnoop_emeet open so the EMEET USB
  altsetting never re-negotiates between ears/supervisor session boundaries.
  Prevents the 2026-04-26 USB-cascade failure.
After=docker.service
Requires=docker.service
# Supervisor + ears both depend on this — see their unit files.

[Service]
Type=simple
Restart=always
RestartSec=2
# Mount the asound.conf into the container so dsnoop_emeet is visible.
ExecStart=/usr/bin/docker exec \
    -e PYTHONUNBUFFERED=1 \
    ugv_waveshare /bin/bash /home/ws/ugv_ws/ugv_tools_api/deploy/start_keepalive.sh

# StandardOutput=journal so journalctl -u ugv-mic-keepalive picks it up
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Step 3: No unit tests at this step (it's a systemd-managed wrapper).** Verified live in Task 6.

**Step 4: Commit.**

```bash
git add deploy/start_keepalive.sh deploy/ugv-mic-keepalive.service
git commit -m "feat(deploy): add ugv-mic-keepalive service holding dsnoop open"
```

---

### Task 3: Update supervisor service + env to use `dsnoop_emeet`

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/supervisor.env`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-supervisor.service`
- (No code change to `audio_io.py` — `MicStream._spawn_arecord` reads `cfg.mic_device` which is env-resolved)

**Step 1: Write the failing test** (cascade-regression-stress, defined fully in Task 5; for this task, just verify env routing).

Append to `tests/supervisor/test_config.py`:
```python
def test_supervisor_mic_default_uses_dsnoop_alias_after_b1(monkeypatch):
    """After Option B1, the production default for SUPERVISOR_MIC is the
    ALSA dsnoop alias, not a direct hw: device. This test pins the new
    default so accidental reverts to plughw:CARD=Plus get caught."""
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    # Clear any inherited SUPERVISOR_MIC override
    monkeypatch.delenv("SUPERVISOR_MIC", raising=False)
    from ugv_tools_api.supervisor.config import load
    cfg = load()
    assert cfg.mic_device == "dsnoop_emeet", (
        f"expected dsnoop_emeet, got {cfg.mic_device!r}; "
        "did someone revert the Task 3 default?"
    )
```

**Step 2: Run, confirm RED** (current default is `plughw:CARD=Plus,DEV=0`):
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && python3 -m pytest tests/supervisor/test_config.py::test_supervisor_mic_default_uses_dsnoop_alias_after_b1 -v 2>&1 | tail -10"'
```
Expected: FAIL with `assert 'plughw:CARD=Plus,DEV=0' == 'dsnoop_emeet'`.

**Step 3: Update `config.py` default.**

In `ugv_tools_api/supervisor/config.py`, change the `load()` line:
```python
# Before:
mic_device=os.environ.get("SUPERVISOR_MIC", "plughw:CARD=Plus,DEV=0"),
# After:
mic_device=os.environ.get("SUPERVISOR_MIC", "dsnoop_emeet"),
```

**Step 4: Update `supervisor.env`.**
```
SUPERVISOR_MIC=dsnoop_emeet
```

**Step 5: Update `ugv-supervisor.service`.**

Add bind-mount of asound.conf into the container — actually, the container already has writable `/etc/asound.conf` if we install it there once. Two options:

**Option A**: Install `asound.conf` into the container's `/etc/` once (manual one-time step), referenced from the systemd unit's `ExecStartPre`:

```ini
ExecStartPre=/usr/bin/docker exec ugv_waveshare bash -c "cp /home/ws/ugv_ws/ugv_tools_api/deploy/asound.conf /etc/asound.conf"
```

**Option B**: Bind-mount via docker stop/start of the container (heavier; requires container restart).

**Use Option A.** It's idempotent, runs every supervisor start, and doesn't require touching the running container's bind-mounts.

Add to the unit file:
```ini
[Unit]
After=ugv-mic-keepalive.service docker.service
Requires=ugv-mic-keepalive.service

[Service]
ExecStartPre=/usr/bin/docker exec ugv_waveshare bash -c "cp /home/ws/ugv_ws/ugv_tools_api/deploy/asound.conf /etc/asound.conf"
ExecStart=...(existing)
```

**Step 6: Run tests, confirm GREEN.**

After laptop edits + rsync to Jetson + container's `/etc/asound.conf` populated:
```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && python3 -m pytest tests/supervisor/ -v 2>&1 | tail -20"'
```
Expected: ALL pass, including the new `test_supervisor_mic_default_uses_dsnoop_alias_after_b1`.

**Step 7: Commit.**

```bash
git add ugv_tools_api/supervisor/config.py deploy/supervisor.env deploy/ugv-supervisor.service tests/supervisor/test_config.py
git commit -m "feat(supervisor): route MicStream arecord through dsnoop_emeet"
```

---

### Task 4: Update ears env to match against `dsnoop_emeet`

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/ears.env` (Jetson source-of-truth — laptop has no copy)
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-ears.service`

**Step 1: Update `ears.env` (Jetson):**
```
MIC_DEVICE_HINT=dsnoop_emeet
UGV_ALSA_DEVICE=dsnoop_emeet
```

(The hint substring-matches against PyAudio's enumerated device names. PyAudio sees ALSA PCM names; with our config, `dsnoop_emeet` will be enumerable.)

**Step 2: Update `ugv-ears.service` to depend on keepalive.**

Add to the unit:
```ini
[Unit]
After=ugv-mic-keepalive.service docker.service
Requires=ugv-mic-keepalive.service
```

Also add the same `ExecStartPre` to copy asound.conf (so ears comes up with the right config even if started independently):
```ini
[Service]
ExecStartPre=/usr/bin/docker exec ugv_waveshare bash -c "test -f /etc/asound.conf || cp /home/ws/ugv_ws/ugv_tools_api/deploy/asound.conf /etc/asound.conf"
```

**Step 3: No unit-test for env file content** (it's just a deploy artifact). Verified in Task 6 live bench.

**Step 4: Sync, install, daemon-reload.**
```bash
sshpass -p 'jetson' rsync ... deploy/ugv-ears.service jetson@.../tmp/
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'echo jetson | sudo -S cp /tmp/ugv-ears.service /etc/systemd/system/ && sudo systemctl daemon-reload'
```

**Step 5: Commit.**

```bash
git add deploy/ears.env deploy/ugv-ears.service
git commit -m "feat(ears): route PyAudio open through dsnoop_emeet"
```

---

### Task 5: Cascade-regression stress test

**Files:**
- Create: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_dsnoop_resilience.py`

**Goal:** an automated test that simulates the failure pattern (rapid open/close cycles) and asserts no USB cascade is triggered. Without dsnoop, this would have caught the 2026-04-26 cascade earlier.

**Step 1: Write the failing test.**

```python
"""Dsnoop fan-out resilience test.

Asserts that opening + closing the dsnoop_emeet PCM 10 times in rapid
succession does NOT trigger a USB altsetting cascade. Pre-Option-B1, a
similar pattern with direct hw: opens crashed the entire Jetson USB tree
on 2026-04-26.

Skipped on hosts without /etc/asound.conf or without a PCM named
dsnoop_emeet (e.g. local laptop dev). Runs full only inside the
ugv_waveshare container or any environment with the dsnoop config installed.
"""
import os
import shutil
import subprocess
import time
import pytest


def _dsnoop_available() -> bool:
    """True if dsnoop_emeet appears in `arecord -L` output AND arecord is
    on PATH. Skip the test on hosts that don't have the deploy installed."""
    if shutil.which("arecord") is None:
        return False
    try:
        out = subprocess.check_output(
            ["arecord", "-L"], stderr=subprocess.DEVNULL, timeout=5,
        ).decode("ascii", errors="replace")
        return "dsnoop_emeet" in out
    except (subprocess.SubprocessError, OSError):
        return False


pytestmark = pytest.mark.skipif(
    not _dsnoop_available(),
    reason="dsnoop_emeet not configured on this host (run inside ugv_waveshare container)",
)


def _read_dmesg_usb_errors() -> int:
    """Count `usb_set_interface failed` errors in dmesg.
    A non-decreasing count across the test run = no cascade was triggered.
    Best-effort; returns 0 if dmesg is not accessible.
    """
    try:
        out = subprocess.check_output(
            ["dmesg", "-T"], stderr=subprocess.DEVNULL, timeout=5,
        ).decode("ascii", errors="replace")
        return out.count("usb_set_interface failed")
    except (subprocess.SubprocessError, OSError, PermissionError):
        return 0


def test_open_close_dsnoop_10x_no_cascade():
    """Stress: 10 short opens + closes against dsnoop_emeet. Pre-cascade
    error count should equal post-cascade error count (no new failures)."""
    pre_errors = _read_dmesg_usb_errors()

    for i in range(10):
        # 0.3 s capture, write to /dev/null. Tight loop simulates rapid
        # ears restart / PyAudio reopen pattern.
        rc = subprocess.call(
            ["arecord", "-D", "dsnoop_emeet",
             "-f", "S16_LE", "-r", "16000", "-c", "1",
             "-d", "1", "-t", "raw"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert rc == 0, f"iteration {i}: arecord on dsnoop_emeet failed rc={rc}"
        time.sleep(0.1)  # brief settle

    post_errors = _read_dmesg_usb_errors()
    assert post_errors == pre_errors, (
        f"USB altsetting errors increased during stress: "
        f"pre={pre_errors} post={post_errors}. "
        f"dsnoop fan-out is supposed to keep the underlying hw: open "
        f"continuously so altsetting renegotiation never fires."
    )


def test_two_simultaneous_readers_both_get_audio():
    """Fan-out integrity: two readers running concurrently must both
    receive non-empty audio. Without proper dsnoop config, the second
    open returns Device or resource busy."""
    procs = []
    for _ in range(2):
        p = subprocess.Popen(
            ["arecord", "-D", "dsnoop_emeet",
             "-f", "S16_LE", "-r", "16000", "-c", "1",
             "-d", "2", "-t", "raw"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        procs.append(p)
        time.sleep(0.2)

    bytes_read = []
    for p in procs:
        out, _ = p.communicate(timeout=5)
        bytes_read.append(len(out))

    # 2 s × 16000 Hz × 2 bytes/sample = 64000 bytes. Allow ±25% jitter.
    assert all(48000 <= n <= 80000 for n in bytes_read), (
        f"both readers should receive ~64000 bytes, got {bytes_read}"
    )
```

**Step 2: Run, confirm RED on a host without dsnoop installed yet.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && python3 -m pytest tests/supervisor/test_dsnoop_resilience.py -v 2>&1 | tail -20"'
```
Expected: SKIPPED with "dsnoop_emeet not configured" — that's the right red state pre-config-install. Once Task 1's config is installed, the test should run and PASS.

**Step 3: After Tasks 1-4 are landed, install asound.conf + restart services + re-run.**

```bash
# After laptop → Jetson sync of asound.conf:
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cp /home/ws/ugv_ws/ugv_tools_api/deploy/asound.conf /etc/asound.conf"'

# Restart services in dependency order:
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'echo jetson | sudo -S systemctl restart ugv-mic-keepalive.service && sleep 2 && \
   sudo systemctl restart ugv-supervisor.service ugv-ears.service'

# Re-run the test — should now PASS:
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -c "cd /home/ws/ugv_ws/ugv_tools_api && python3 -m pytest tests/supervisor/test_dsnoop_resilience.py -v 2>&1 | tail -20"'
```
Expected: 2 PASS.

**Step 4: Commit.**

```bash
git add tests/supervisor/test_dsnoop_resilience.py
git commit -m "test(supervisor): cascade-regression stress test on dsnoop_emeet"
```

---

### Task 6: Live bench — operator-led

**Files:**
- Create: `docs/plans/notes/2026-04-26-dsnoop-bench.md`

This is the dispositive test. The unit tests prove dsnoop *plumbing* works; only the operator bench proves the end-to-end **wake-word → session → wake-word** cycle is reliable across many iterations without a USB cascade.

**Step 1: Pre-bench checks.**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'systemctl is-active ugv-mic-keepalive.service ugv-supervisor.service ugv-ears.service; \
   echo "---"; \
   docker exec ugv_waveshare arecord -L 2>&1 | grep -E "dsnoop_emeet"; \
   echo "---"; \
   echo jetson | sudo -S journalctl -u ugv-mic-keepalive.service -n 5 --no-pager 2>&1 | grep -v password'
```

Expected: all three services `active`, `dsnoop_emeet` listed in arecord -L, keepalive journal shows clean startup.

**Step 2: Operator runs N=5 wake/speak/end cycles.**

For each cycle:
1. Operator says "Black Box Flight Recorder, [a question]"
2. Verify session opens (`[supervisor] session opened (raw-WS)` in journal)
3. Operator continues conversation for ~30 seconds
4. Wait for session to end (budget rotate or operator says "stop")
5. Verify ears reacquires (`MUTE_FLAG cleared — reacquired mic` in ears journal)
6. Wait 5 seconds
7. **Repeat from step 1** — wake word should fire reliably the next time

**Verification matrix:**

| C# | Criterion | How to verify |
|---|---|---|
| C1 | Wake fires on every cycle (5/5) | grep ears journal for `WAKE` events; expect 5 |
| C2 | No `usb_set_interface failed` in dmesg during the bench | `dmesg -T \| grep usb_set_interface` count before/after — must match |
| C3 | Keepalive service stays `active` throughout | `systemctl is-active ugv-mic-keepalive` between cycles |
| C4 | Session opens with `[supervisor] AEC mode=passthrough` (full duplex still works through dsnoop) | grep supervisor journal |
| C5 | Mic upload health: `aec=0, halfduplex=0, passthrough=N` (passthrough mode, no AEC) | grep `[diag] mic upload sess#` |
| C6 | No EMEET disconnect events in dmesg (`usb 1-2.4: USB disconnect`) | dmesg -T grep |
| C7 | Wake threshold can stay at production setting (0.30 or back to 0.50?) | observe wake reliability |

**Step 3: Save bench record** to `docs/plans/notes/2026-04-26-dsnoop-bench.md` with the C1-C7 results.

**Step 4: Commit.**

```bash
git add docs/plans/notes/2026-04-26-dsnoop-bench.md
git commit -m "docs(supervisor): dsnoop fan-out bench record"
```

---

### Task 7: Cleanup + memory + snapshot

**Files:**
- Update: `~/.claude/projects/.../memory/ugv_supervisor_2_5_async.md` — add dsnoop fan-out section
- Mint snapshot via `/snapshot-dev`

**Step 1: Update memory entry.**

Append a new section to `ugv_supervisor_2_5_async.md`:
```markdown
## Dsnoop fan-out for EMEET capture (2026-04-26 — production audio resilience)

**Plan:** `docs/plans/2026-04-26-dsnoop-mic-fanout.md`. Snapshot: `SNAP-20260426-NNNN`.

**Problem solved:** On 2026-04-26 the EMEET's USB Audio interface had its altsetting renegotiated when ears restarted while the supervisor was using a different audio profile. The renegotiation lost a bandwidth fight with the Xitech UVC camera on the same xHCI controller, and the USB host controller hung — taking down ALL USB devices (audio, camera, BT, motors). Required full Jetson reboot to recover.

**Architecture:**
- `/etc/asound.conf` (inside container) defines `pcm.dsnoop_emeet` wrapping `hw:CARD=Plus,DEV=0` at fixed S16_LE/16000/mono parameters.
- `ugv-mic-keepalive.service` runs `arecord -D dsnoop_emeet ... > /dev/null` continuously; this is the always-on owner of the dsnoop fan-out.
- ears (`MIC_DEVICE_HINT=dsnoop_emeet`) and supervisor (`SUPERVISOR_MIC=dsnoop_emeet`) both attach to the dsnoop alias instead of opening hw: directly.
- Result: ONE physical USB altsetting negotiation per supervisor lifetime. No reopens, no renegotiation, no cascade.

**MUTE_FLAG handoff** — left in place. With dsnoop, multiple processes can hold open simultaneously, so the handoff is now a no-op overhead. Removing it is a separate cleanup task.

**Rollback path:** `SUPERVISOR_MIC=plughw:CARD=Plus,DEV=0` + `MIC_DEVICE_HINT=EMEET` env override reverts to direct hw: opens. Stop `ugv-mic-keepalive.service`. Same audio path as pre-B1.
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
- [ ] All Task 1-7 tasks committed on Jetson `ros2-humble-develop`
- [ ] `tests/supervisor/test_dsnoop_resilience.py` PASSES inside the container after `/etc/asound.conf` install
- [ ] `tests/supervisor/test_config.py::test_supervisor_mic_default_uses_dsnoop_alias_after_b1` PASSES
- [ ] Full supervisor test suite count stays at current + 2 new = N+2 passing (no regressions)
- [ ] Operator bench (Task 6) shows 5/5 wake-word fires across multiple cycles
- [ ] Zero new `usb_set_interface failed` dmesg events during the bench
- [ ] EMEET stays enumerated throughout the bench (no `usb 1-2.4: USB disconnect`)
- [ ] Memory entry updated; snapshot minted with embedding

## Rollback Path

If the dsnoop config breaks audio capture for any reason mid-bench:

```bash
# Stop keepalive (releases the dsnoop)
sshpass -p 'jetson' ssh jetson@192.168.1.155 'echo jetson | sudo -S systemctl stop ugv-mic-keepalive.service'

# Revert env to direct hw: (same as pre-B1)
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'cd /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy && \
   sed -i "s|^SUPERVISOR_MIC=.*|SUPERVISOR_MIC=plughw:CARD=Plus,DEV=0|" supervisor.env && \
   sed -i "s|^MIC_DEVICE_HINT=.*|MIC_DEVICE_HINT=EMEET|" ears.env'

# Restart services in old config
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'echo jetson | sudo -S systemctl restart ugv-supervisor.service ugv-ears.service'
```

5-minute recovery to pre-B1 state. Document any rollback reason in the bench notes.

## Out-of-Plan Follow-Ups

After this lands and proves stable:

1. **Remove the MUTE_FLAG handoff entirely** — with dsnoop it's no-op overhead. Separate cleanup plan; trivial diff.
2. **`dmix` for output** — if we later want a separate process to play to the EMEET speaker (e.g. the supervisor adding a chime path that doesn't conflict with Gemini audio), mirror this plan for output.
3. **Cascade-regression test in CI** — currently the test runs only when `dsnoop_emeet` is configured (i.e. on Jetson). Consider mocking arecord to run on laptop.
4. **Hardware mitigation** — even with dsnoop, the EMEET still shares xHCI bandwidth with Xitech UVC. If we ever see a cascade despite dsnoop, plug EMEET into a different USB controller.
5. **EMEET vendor HID protocol RE** — if there's ever a need to programmatically send call-state commands (e.g. proper hangup signal at session end as a belt-and-suspenders), capture EMEET's Windows app traffic and reverse-engineer reports 7/8.
