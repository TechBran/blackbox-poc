# UGV Beast — Path B (TCP variant) Bench Record

**Plan:** `docs/plans/2026-04-26-system-audio-via-pipewire.md`
**Pivot rationale:** Original plan called for `/run/pulse:/run/pulse:ro` bind-mount + pipewire-pulse. Mid-execution discovered (a) this Jetson runs a custom `/etc/systemd/system/pulseaudio.service` (uid `pulse`) that has been quietly owning the EMEET all along, and (b) docker container recreate (required for adding the bind-mount) ITSELF triggers the USB altsetting cascade we were trying to prevent. Pivoted to TCP loopback listener (`127.0.0.1:4714`, `auth-anonymous=1`) on system pulseaudio. Avoids future container recreates entirely.

## Architecture

```
ears (PyAudio)  ─┐
                 ├──► PULSE_SERVER=tcp:127.0.0.1:4714 ──► system pulseaudio (PID 530, uid pulse)
supervisor       ─┘                                       ──► EMEET (USB 1-2.4, 328f:007d) — hardware AEC
  (arecord)
```

- System pulse holds EMEET continuously (single altsetting negotiation at boot)
- Container processes connect via TCP loopback (no bind-mount, no recreate ever needed)
- `/etc/pulse/system.pa.d/blackbox.pa` persists the TCP module across system pulse restarts
- `module-native-protocol-tcp port=4714 listen=127.0.0.1 auth-anonymous=1` (loopback-only, anonymous auth acceptable since not network-exposed)

## Bench Verification Matrix

| # | Criterion | Result |
|---|---|---|
| C1 | Wake fires reliably | ✅ Multiple wake/session cycles confirmed in journal — Brandon reports "audio pipeline solid, holding for a long time, able to see and interact with the bot" |
| C2 | Zero new `usb_set_interface failed` in dmesg | ✅ **0 events since uptime** (verified post-bench) |
| C3 | EMEET stays enumerated | ✅ `lsusb` confirms `328f:007d` stable on `1-2.4` throughout |
| C4 | Pulse holds EMEET continuously | ✅ System pulse has held EMEET source `RUNNING` state across multiple supervisor wake cycles |
| C5 | AEC mode=passthrough (full-duplex through pulse) | ✅ Journal confirms `[supervisor] AEC mode=passthrough (hardware AEC on device, full-duplex)` |
| C6 | Mic upload health | ✅ Steady ~32 KB/s (`256 chunks, 31956–32094 B/s`), `aec_failures=0` throughout |
| C7 | EMEET hardware AEC engaging during TTS | ✅ Halfduplex chunk count rises when supervisor speaks (e.g. 18:43:35 `passthrough=141, halfduplex=115` during TTS), back to all-passthrough when silent — exactly the expected behavior |

## Sample Session (18:42:59 → 18:44:36+, 100+ seconds continuous)

```
18:42:59 mic sess#1: 256 chunks, 32006 B/s (passthrough=242, halfduplex=14, aec_failures=0)
18:43:04 mic sess#1: 256 chunks, 32044 B/s (passthrough=256, halfduplex=0)   ← steady silence-side
18:43:35 mic sess#1: 256 chunks, 31981 B/s (passthrough=141, halfduplex=115) ← supervisor speaking
18:44:00 mic sess#1: 256 chunks, 31975 B/s (passthrough=18,  halfduplex=238) ← supervisor mid-utterance
18:44:11 mic sess#1: 252 chunks, 32256 B/s (passthrough=252, halfduplex=0)   ← silence resumes
[continuing cleanly at time of bench close]
```

The halfduplex/passthrough oscillation tracks supervisor speech vs silence — EMEET's hardware AEC is correctly handing off the mic stream during TTS playback to prevent self-feedback.

## Operator confirmation

> "audio pipeline solid... holding for a long time, able to see and interact with the bot. If you stop it, everything seems to be working there."

Live evidence captured in this transcript: supervisor TTS `"Great! Is there anything else the robot can do for him?"` came back through the EMEET speaker mid-conversation, picked up by Brandon's voice during a separate exchange — the round-trip (pulse → EMEET capture → supervisor → Gemini Live → TTS → pulse → EMEET sink) is fully wired.

## Verdict

**HEALTHY.** Cascade-class structurally closed for ALSA-open trigger via system-pulse routing. Architecture stable across multiple multi-turn sessions. Brandon authorized closing the bench.

## Out-of-Scope Issues Caught (Deferred to Later Sessions)

1. **`alsa-utils` was missing from container** — supervisor calls `arecord` as subprocess; ENOENT crashed wake cycles silently. Fixed via apt install + `container-bootstrap.sh` update (commit `b449c26`).
2. **`ros-humble-robot-localization` missing from container** — slam_nav.launch.py crashes at launch-time on `package_share_directory()` lookup, taking down the entire Nav2 stack (no costmap publishers). Fixed via direct .deb fetch from `packages.ros.org` + `container-bootstrap.sh` update (commit `01fbe55`). Brandon authorized `docker restart ugv_waveshare` whenever convenient to bring Nav2 back up cleanly.
3. **OAK-D X_LINK_ERROR cascade on USB 1-2.3** (NOT EMEET on 1-2.4) — separate hardware/cable issue affecting the camera. Will affect EKF input quality once Nav2 is back up. Out of scope for this plan; noted for next session.
4. **Container apt config points at `mirrors.ustc.edu.cn`** with expired GPG key + 404s on current ROS2 packages — bypass works (direct .deb fetch) but underlying mirror should be replaced or repointed at official `packages.ros.org`. Future ticket.

## Commits This Plan Landed (Jetson `ros2-humble-develop`)

| SHA | What |
|---|---|
| `686c73c` | feat(deploy): plumb system-pulse env vars to supervisor + ears |
| `11370d1` | feat(supervisor): pre-stage Path B config + tests + container inspect archive |
| `df748fe` | feat(deploy): pivot from /run/pulse bind-mount to TCP loopback for system pulse |
| `6ac5c74` | test(supervisor): accept tcp:* PULSE_SERVER + archive Path B preflight diary |
| `b449c26` | fix(bootstrap): apt install alsa-utils + pulse audio tools |
| `01fbe55` | fix(bootstrap): install ros-humble-robot-localization for slam_nav.launch.py |

Plus container writable-layer changes (apt installs) made persistent via the bootstrap script — automatically reapplied on any future container recreate.
