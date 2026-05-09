# PipeWire-Pulse Preflight Diagnosis — 2026-04-26

**Run by:** preflight subagent under subagent-driven-development for plan `docs/plans/2026-04-26-system-audio-via-pipewire.md`

**Verdict:** FAIL — pulseaudio.service is flapping (start timeout) and `pipewire-pulse` package is not installed; no working Pulse server is reachable.

## P-1: Audio daemon state

```
$ systemctl --user list-units --type=service --state=running | grep -E "(pipe|pulse|audio)"
  pipewire-media-session.service loaded active running PipeWire Media Session Manager
  pipewire.service               loaded active running PipeWire Multimedia Service
```

Per-unit checks (`systemctl --user is-active <unit>` and `--no-pager status`):

```
pulseaudio.service           -> deactivating  (state observed flipping: activating(start) -> deactivating(stop-sigterm))
pipewire.service             -> active
pipewire-media-session.service -> active
pipewire-pulse.service       -> Unit could not be found.
```

`systemctl --user status pulseaudio.service` excerpt at +90s after boot-stage trigger:
```
Active: deactivating (stop-sigterm) (Result: timeout)
Apr 26 16:03:58 ...: Starting Sound Service...
Apr 26 16:05:28 ...: pulseaudio.service: start operation timed out. Terminating.
```

`systemctl --user list-unit-files | grep -E "(pipewire|pulse)"`:
```
pipewire-media-session.service     enabled
pipewire.service                   enabled
pulseaudio-x11.service             static
pulseaudio.service                 enabled
pipewire.socket                    enabled
pulseaudio.socket                  enabled
```

Installed packages (`dpkg -l | grep -E "(pipewire|pulse)"`):
- `pipewire 0.3.48-1ubuntu3` — installed
- `pipewire-bin 0.3.48-1ubuntu3` — installed
- `pipewire-media-session 0.4.1-2ubuntu1` — installed
- `pulseaudio 1:15.99.1+dfsg1-1ubuntu2.2` — installed
- **`pipewire-pulse` — NOT installed** (this is the Pulse-compat layer; without it there is no `pipewire-pulse.service`)

**Findings:**
- pipewire.service: **running**
- pipewire-media-session.service: **running** (session manager present; wireplumber not in use)
- pipewire-pulse.service: **does not exist** (package `pipewire-pulse` not installed)
- pulseaudio.service: **flapping** — `Active: activating (start)` then 90s later `deactivating (stop-sigterm) (Result: timeout)`. Triggered by `pulseaudio.socket` (also enabled). Will re-fire indefinitely.

## P-2: Pulse socket + cookie

```
$ ls -la /run/user/1000/pulse/native /home/jetson/.config/pulse/cookie
-rw------- 1 jetson jetson 256 Jan  1  1970 /home/jetson/.config/pulse/cookie
srw-rw-rw- 1 jetson jetson   0 Apr 26 16:03 /run/user/1000/pulse/native
```

**Findings:**
- `/run/user/1000/pulse/native`: **exists as Unix socket** (`s` in mode bits, jetson:jetson, 666). However, this socket is created by `pulseaudio.socket` (systemd socket activation) — it is bound but no live daemon is accepting on it because `pulseaudio.service` keeps timing out before it can attach. Result: clients connect, then time out.
- `/home/jetson/.config/pulse/cookie`: **exists, 256B, 0600 jetson:jetson** — correct perms. Mtime is epoch (Jan 1 1970) which is a separate Jetson clock-skew artifact (see MEMORY.md `ugv_jetson_clock_skew.md`); content is fine.

## P-3: pactl introspection

```
$ XDG_RUNTIME_DIR=/run/user/1000 pactl info
Connection failure: Timeout

$ XDG_RUNTIME_DIR=/run/user/1000 pactl list short sources
Connection failure: Timeout

$ XDG_RUNTIME_DIR=/run/user/1000 pactl list short sinks
Connection failure: Timeout
```

(Same result without `XDG_RUNTIME_DIR` prefix.)

USB hardware sanity check (independent of pulse stack):
```
$ lsusb | grep -i emeet
Bus 001 Device 009: ID 328f:007d EMEET EMEET OfficeCore M0 Plus
```
EMEET is electrically present; the failure is purely at the audio-server layer.

**Findings:**
- pactl info Server Name: **(timeout — no server reachable)**
- Sources count: **0 (cannot enumerate; EMEET not visible to clients)**
- Sinks count: **0 (cannot enumerate; EMEET not visible to clients)**
- Default source / Default sink: **N/A**

## P-4: Verdict

**Gate decision:**
- [ ] PASS — Task 1 may simplify (verify-only, no remediation)
- [ ] PASS-WITH-CAVEAT — describe caveat, Task 1 must address
- [x] **FAIL — Task 1 must remediate before any other task proceeds**

**Root cause:** Two competing audio daemons are configured to start under the user session:

1. `pipewire.service` + `pipewire-media-session.service` start cleanly and grab the audio hardware via the kernel ALSA layer.
2. `pulseaudio.socket` is also enabled and listening on `/run/user/1000/pulse/native`. When any client touches that socket, systemd activates `pulseaudio.service`, which tries to open the same audio devices PipeWire already holds, fails to make progress, and times out at 90s with `start operation timed out. Terminating.` The socket immediately re-arms, so the next client connection re-triggers the same 90s flap. Net result: the Pulse socket exists but nothing is ever serving on it.

The intended bridge — `pipewire-pulse.service` — does not exist on this system because the `pipewire-pulse` package is not installed. Without it, PipeWire offers a native PipeWire socket (`/run/user/1000/pipewire-0`) but no Pulse-protocol endpoint, and nothing fills the `/run/user/1000/pulse/native` socket. ALSA-only clients (and our containers) that talk Pulse therefore have no working server.

**Caveats / follow-ups for Task 1 (mandatory remediation list):**

1. **Install `pipewire-pulse`** (Ubuntu package). This pulls in `/usr/lib/systemd/user/pipewire-pulse.service` and `pipewire-pulse.socket`, which will provide the Pulse-protocol endpoint backed by PipeWire.
2. **Disable + mask `pulseaudio.service` and `pulseaudio.socket`** under the user manager (`systemctl --user disable --now pulseaudio.socket pulseaudio.service`, then `systemctl --user mask pulseaudio.service pulseaudio.socket`). Without masking, any package upgrade or login session can re-enable the socket and reintroduce the flap.
3. **Verify `pipewire-pulse.socket` owns `/run/user/1000/pulse/native` afterward**. The path is the same; the listener has to switch from pulseaudio to pipewire-pulse cleanly. If the file is held open by a zombie pulseaudio process at remediation time, the socket activation may fail until the process is reaped — confirm `pgrep -a pulseaudio` returns nothing before declaring success.
4. **Cookie file mtime is epoch (1970-01-01)** because of the known Jetson RTC/clock-skew issue. Pulse does not validate cookie mtime, only the bytes, so this is informational — does not need to be fixed by Task 1.
5. **Do NOT touch any ALSA device directly during remediation.** No `arecord`, no `aplay`, no `amixer` against the EMEET card — the 2026-04-26 USB-cascade rule still applies. Verification must go through `pactl list short sources/sinks` only, after `pipewire-pulse` is up.
6. **Wireplumber vs media-session:** the system is currently using the older `pipewire-media-session` (0.4.1). It is functional but deprecated upstream. Task 1 does not need to migrate to wireplumber; flag for future work only.
7. **`journalctl --user`** returned "No journal files were found." for `pulseaudio.service` — user journals appear not to be persisted on this Jetson. Task 1 verification should use `journalctl --user -u pipewire-pulse --no-pager -n 50` after remediation, but expect possibly empty journals; rely on `systemctl --user is-active` and `pactl info` as the primary success checks.

**Safety footnote — commands NOT run during preflight (per safety constraints):** no `arecord`, no `aplay`, no `pactl` state-modifying subcommands, no `systemctl --user start/stop/restart/mask/unmask/enable/disable`, no `pulseaudio -k/--start`. All findings above are from read-only introspection only.

---

## Task 1 Remediation (2026-04-26 12:16:20 local) — DEGRADED, BLOCKED PENDING DECISION

**Verdict:** DEGRADED — pulseaudio.service is fully neutralized and pipewire-pulse owns the socket cleanly, BUT EMEET is still not enumerated as a Pulse source/sink. Root cause: the legacy session manager `pipewire-media-session` (0.4.1) is not loading the ALSA monitor for the host cards. Remediation halted before installing `wireplumber` (which is the standard fix) per safety policy — this changes the session-manager architecture and warrants an explicit operator OK.

### What was done

1. **DNS/connectivity hiccup:** First `apt-get install` failed with "Temporary failure resolving 'ports.ubuntu.com'" via the Tailscale stub resolver (100.100.100.100). Direct `host ports.ubuntu.com 8.8.8.8` succeeded; second attempt against the Tailscale resolver succeeded ~1 minute later (resolver flap, not DNS misconfig). No `/etc/resolv.conf` edit was applied — Tailscale immediately rewrote any change. Worth noting because the apt step may flap again on future runs.

2. **Installed `pipewire-pulse 0.3.48-1ubuntu3`** cleanly. 6.6KB package, no extra deps. Created the systemd-user symlinks:
   - `/etc/systemd/user/default.target.wants/pipewire-pulse.service`
   - `/etc/systemd/user/sockets.target.wants/pipewire-pulse.socket`

3. **Disabled + masked legacy pulseaudio:**
   ```
   $ systemctl --user mask pulseaudio.service pulseaudio.socket
   Created symlink /home/jetson/.config/systemd/user/pulseaudio.service → /dev/null
   Created symlink /home/jetson/.config/systemd/user/pulseaudio.socket → /dev/null

   $ systemctl --user is-enabled pulseaudio.service pulseaudio.socket
   masked
   masked
   ```
   `disable --now` returned exit 1 (service was already in a transitional state from the flap loop), but the subsequent mask operations completed cleanly. `systemctl --user reset-failed pulseaudio.service` cleared the flap counter.

4. **Confirmed zero zombie pulseaudio PIDs:** `pgrep -au jetson pulseaudio` → empty.

5. **Started pipewire-pulse:**
   ```
   $ systemctl --user --now enable pipewire-pulse.socket pipewire-pulse.service
   $ systemctl --user is-active pipewire-pulse.service pipewire-pulse.socket
   active
   active
   ```

6. **`pactl info` confirms pipewire-pulse owns the socket:**
   ```
   Server String: /run/user/1000/pulse/native
   Server Name: PulseAudio (on PipeWire 0.3.48)
   Default Sample Specification: float32le 2ch 48000Hz
   Default Sink: @DEFAULT_SINK@
   Default Source: @DEFAULT_SOURCE@
   ```
   No more Connection Timeout. Pulse-API path is healthy.

### What is NOT working

`pactl list short sources` and `pactl list short sinks` return ONLY the `auto_null` placeholder — no ALSA cards are enumerated:
```
$ pactl list short sources
39  auto_null.monitor   PipeWire    float32le 2ch 48000Hz   SUSPENDED

$ pactl list short sinks
39  auto_null   PipeWire    float32le 2ch 48000Hz   SUSPENDED
```

PipeWire's native introspection (`pw-cli list-objects`) confirms the same — only the V4L2 USB Camera node and the auto_null Dummy are present. No `alsa_input.*` / `alsa_output.*` nodes for any of the 5 host cards (Camera, Device, EMEET-Plus, HDA, APE).

### Diagnostic detail (root cause)

ALSA cards are visible at the kernel level:
```
$ cat /proc/asound/cards
 0 [Camera ]: USB-Audio - USB Camera
 1 [Device ]: USB-Audio - USB PnP Audio Device
 2 [Plus   ]: USB-Audio - EMEET OfficeCore M0 Plus
 3 [HDA    ]: tegra-hda - NVIDIA Jetson Orin Nano HDA
 4 [APE    ]: tegra-ape - NVIDIA Jetson Orin Nano APE
```

User `jetson` is in group `audio` (29) with read perms on every `/dev/snd/pcm*` (verified `/dev/snd/pcmC2D0c` is readable). PipeWire daemon runs as uid 1000 with the `audio` group in its supplementary list. So permissions are NOT the blocker.

`pipewire-media-session.conf` has `alsa-monitor` enabled (uncommented in the spa-libs section), and `alsa-monitor.conf` is present at `/usr/share/pipewire/media-session.d/alsa-monitor.conf`. Restarting `pipewire-media-session.service` did not cause any ALSA card to appear.

`journalctl --user -u pipewire-media-session.service` returns "No journal files were found." (user journals are not persisted on this Jetson — known limitation noted in preflight P-1).

There are non-fatal RTKit warnings in the system journal:
```
pipewire-media-session: mod.rt: RTKit error: org.freedesktop.DBus.Error.AccessDenied
pipewire-media-session: could not make thread realtime using RTKit: Permission denied
```
These are cosmetic (PipeWire degrades to non-RT scheduling); they don't prevent ALSA enumeration.

`pactl load-module module-alsa-card device_id=Plus name=emeet card_name=alsa_card.emeet` (the plan's last-resort fallback) returns:
```
Failure: No such entity
```
pipewire-pulse 0.3.48 does NOT expose the legacy `module-alsa-card` over the Pulse protocol — that path is dead-end on this version. (Newer pipewire-pulse versions add it; ours predates that.)

### Suspected fix (NOT applied — wants operator OK)

Install `wireplumber 0.4.8-4` (in the `jammy/universe` repo, candidate confirmed via `apt-cache policy`). WirePlumber is the modern session manager, replaces `pipewire-media-session`, and reliably loads ALSA cards via its built-in monitor. Standard migration:

```
sudo apt-get install -y wireplumber
systemctl --user --now disable pipewire-media-session.service
systemctl --user mask pipewire-media-session.service
systemctl --user --now enable wireplumber.service
```

This is a minor architectural change (swap one session manager for another). It's the upstream-recommended path on Ubuntu 22.04+ and the same swap Ubuntu 23.10 made automatically. Risk is low but it's not strictly verification-only — flagging for explicit go/no-go before proceeding.

### Final state

- pipewire-pulse: **active, owns the socket, serves Pulse protocol cleanly**
- legacy pulseaudio: **disabled + masked + zero zombies, fully neutralized**
- ALSA enumeration: **absent — `auto_null` only**
- EMEET source name: **NOT YET KNOWN** (would be `alsa_input.usb-EMEET_OfficeCore_M0_Plus_*` once enumeration is fixed)
- EMEET sink name: **NOT YET KNOWN** (would be `alsa_output.usb-EMEET_OfficeCore_M0_Plus_*`)

### Safety footnote (Task 1 remediation)

No ALSA devices were touched directly. No `arecord`, no `aplay`, no `pactl set-card-profile`, no `pactl set-default-source/sink`, no `pactl move-source-output`, no `pactl suspend-*` against EMEET. The only `pactl load-module` attempt failed harmlessly with "No such entity". `pactl info` and `pactl list short` are read-only.

**Decision needed from Brandon before continuing:** install `wireplumber` to replace `pipewire-media-session`, OR investigate why the existing media-session is not loading the ALSA monitor.

---

## Task 1 Continuation — Wireplumber Swap (2026-04-26 16:38:00 UTC) — DEGRADED, BLOCKED

**Verdict:** DEGRADED — wireplumber 0.4.8-4 installed, pipewire-media-session removed, wireplumber is `active` and DOES enumerate ALSA cards correctly when run in the foreground with debug logging. BUT `pactl list short sources/sinks` still returns nothing, and `pw-cli list-objects 0` still returns ONLY the core. The new blocker is upstream of wireplumber: a system-mode `pulseaudio.service` (running as user `pulse`, PID 530) plus a session-level pulseaudio re-spawned by `systemd --user` are holding all 5 ALSA card control devices, blocking wireplumber from claiming them at runtime. Halted before stopping the system pulseaudio because that service is the active backend for `ugv_tools_api.voice.ears` (PID 2116) and the bt-robot-speaker pipeline.

### What was done

1. **apt lock** initially held by another `apt-get` on the host (PID 8932). Waited; the lock cleared on its own. Retried install successfully.

2. **Installed `wireplumber 0.4.8-4`** (and `libwireplumber-0.4-0 0.4.8-4`, 296KB total). apt **auto-removed `pipewire-media-session 0.4.1-2ubuntu1`** as a conflict — this is the expected upstream resolution path, no manual `--purge` needed.
   - One non-fatal `dpkg` warning: `Failed to preset unit, file /etc/systemd/user/pipewire-session-manager.service already exists and is a symlink to /usr/lib/systemd/user/pipewire-media-session.service.` — repaired in step 4.

3. **Stopped the lingering `pipewire-media-session` (PID 8938) for user 1000** via `systemctl --user stop`. (Note: the binary file at `/usr/lib/systemd/user/pipewire-media-session.service` was removed by apt, but the running process kept executing in memory until killed.) `gdm`'s pipewire-media-session (PID 3113, uid 128) is NOT user 1000's concern and was left alone.

4. **Repointed the session-manager alias** so wireplumber inherits the role:
   ```
   sudo ln -sf /usr/lib/systemd/user/wireplumber.service \
               /etc/systemd/user/pipewire-session-manager.service
   ```

5. **Enabled + started wireplumber.service.** `is-active` reports `active`. systemd-user logs (queried via `sudo journalctl _UID=1000`) show the start sequence completing, with cosmetic RTKit AccessDenied warnings (same as media-session had) and a Bluetooth SPA warning (`PipeWire's BlueZ SPA missing or broken`) which is irrelevant to ALSA enumeration.

### What is NOT working

After the wireplumber swap:

```
$ pactl info
Server Name: PulseAudio (on PipeWire 0.3.48)
$ pactl list short sources
(empty)
$ pactl list short sinks
(empty)
$ pw-cli list-objects 0
        id 0, type PipeWire:Interface:Core/3
                object.serial = "0"
                core.name = "pipewire-0"
```

PipeWire's graph contains only the core. No nodes, no devices, no factories registered.

A `pactl info` call from within the SSH session also reproducibly **hangs after protocol handshake**: SHM negotiation completes (`Protocol version: remote 35, local 35`, `Negotiated SHM type: private`), then the client never gets a reply to its first AUTH message. This pattern matches a session-manager that has not yet populated default-device metadata — wireplumber appears connected but isn't producing devices for pipewire-pulse to surface.

### Root cause (foreground wireplumber run with `WIREPLUMBER_DEBUG=I`)

When wireplumber is run by hand (after stopping `wireplumber.service`), it does the right thing: loads all its Lua configs, activates the ALSA monitor, and starts inotify on `/dev/snd`. The relevant lines:

```
script/alsa alsa.lua:347:createMonitor: Activating ALSA monitor
spa.alsa  alsa-udev.c:708:start_inotify: start inotify
script/alsa alsa.lua:266:chunk: Enabling the use of ACP on alsa_card.platform-3510000.hda
script/alsa alsa.lua:266:chunk: Enabling the use of ACP on alsa_card.usb-Solid_State_System_Co._Ltd._USB_PnP_Audio_Device_000000000000-00
script/alsa alsa.lua:266:chunk: Enabling the use of ACP on alsa_card.usb-Xitech_USB_Camera_20250606105-02
spa.alsa  alsa-udev.c:583:process_device: ALSA card 2 unavailable (Device or resource busy): wait for it
script/alsa alsa.lua:266:chunk: Enabling the use of ACP on alsa_card.platform-sound
```

Wireplumber sees all 5 cards. Card 0/1/3/4 it fails to claim with `Unknown PCM front:3`, `Unknown PCM surround51:3`, etc. — those errors are because some other userland process is already holding the controlC* and pcm* device nodes:

```
$ sudo fuser -v /dev/snd/controlC0..C4 /dev/snd/pcm*
/dev/snd/controlC0:  pulse  530  pulseaudio
                      gdm   3114 pulseaudio
/dev/snd/controlC1:  pulse  530  pulseaudio
                      gdm   3114 pulseaudio
/dev/snd/controlC2:  pulse  530  pulseaudio
/dev/snd/controlC3:  pulse  530  pulseaudio
                      gdm   3114 pulseaudio
                      jetson 11590 wireplumber
/dev/snd/controlC4:  pulse  530  pulseaudio
                      gdm   3114 pulseaudio
/dev/snd/pcmC1D0c:   jetson 11590 wireplumber
/dev/snd/pcmC2D0c:   root   2116 python3   <-- ugv_tools_api.voice.ears holding EMEET capture
```

Two pulseaudio instances I did **not** previously know about are locking everything:

- **System-mode pulseaudio (PID 530, uid `pulse`)** runs from `/etc/systemd/system/pulseaudio.service` (custom unit, NOT the default Ubuntu user-session service). Command line: `/usr/bin/pulseaudio --daemonize=no --system --realtime --log-target=journal`. Status: `enabled` + `active`. Started at 15:48 alongside the rest of the boot. This is NOT the unit the previous subagent masked — that was the **user**-level `pulseaudio.service`, which IS still masked.

- **GDM session pulseaudio (PID 3114, uid `gdm`)** is the login screen's audio session. Started at 15:48. Holds 4 of 5 cards in addition to the system pulseaudio.

There is also a **third autospawn vector** I traced live: `/etc/xdg/autostart/pulseaudio.desktop` causes `systemd --user` to launch `pulseaudio --start --exit-idle-time=-1` for any logged-in graphical session, even though the user `pulseaudio.service` unit is masked. We saw PID 11847 (uid jetson) appear at 16:34 from this path — `~/.config/pulse/client.conf` autospawn=no does NOT block the .desktop autostart.

### Why pactl hangs (now explained)

`pactl info` connects through `pipewire-pulse` (which IS running, owns `/run/user/1000/pulse/native`, and identifies as `PulseAudio (on PipeWire 0.3.48)`). pipewire-pulse handshakes successfully, then asks wireplumber for default device info. Wireplumber has no default to report because it hasn't been able to claim any ALSA card (system pulseaudio + gdm pulseaudio have all the controls). The metadata response stalls, and the client read times out.

### Final state

- wireplumber 0.4.8-4: **active, ALSA monitor running, sees all 5 cards, fails to claim them**
- pipewire-media-session: **uninstalled** (apt removed; mask-symlink in `~/.config/systemd/user/` is now a dangling reference to /dev/null but harmless)
- pipewire-pulse: **active, owns Pulse socket, but reports no devices because wireplumber has none**
- system-mode pulseaudio.service (`/etc/systemd/system/pulseaudio.service`): **active, holding ALSA cards** (NOT touched by this run)
- gdm session pulseaudio (PID 3114): **active, holding ALSA cards** (NOT touched)
- `/etc/xdg/autostart/pulseaudio.desktop`: **present, will keep re-spawning a user pulseaudio at every graphical login** (NOT touched)
- `ugv_tools_api.voice.ears` (PID 2116, root): **alive, actively reading from `pcmC2D0c` (EMEET capture)** — this is the production voice ear pipeline. It is currently going through whatever audio path it was using before (likely the system pulseaudio at PID 530) and would break if that pulseaudio is stopped without replacement.
- EMEET source name: **NOT YET KNOWN** — wireplumber sees the card but cannot claim it; will be `alsa_input.usb-EMEET_OfficeCore_M0_Plus_*` once the lock conflict is resolved.
- EMEET sink name: **NOT YET KNOWN** — same.
- Default Source / Default Sink: **N/A** (none registered).

### Why I stopped here

The next step — stopping the system-mode pulseaudio.service and the gdm session pulseaudio, or removing the XDG autostart — is **not in the authorized command list** and crosses three thresholds the brief explicitly forbade:

1. The system-mode pulseaudio is a **shared-system daemon** (`/etc/systemd/system/pulseaudio.service`) that was deliberately installed at the system level. Stopping it likely breaks `ugv_tools_api.voice.ears` (PID 2116), `bt-robot-speaker.sh` (Bluetooth speaker pipeline observed in the journal), and any other root-owned consumers I haven't audited.
2. The gdm session pulseaudio is owned by the display manager — stopping or reconfiguring the gdm session is well beyond `systemctl --user disable/mask pipewire-media-session`.
3. EMEET is currently in active use by the ears pipeline (`pcmC2D0c` is open by PID 2116). Forcing the audio backend to swap out from under a running capture is exactly the failure mode that triggered the 2026-04-26 USB cascade events.

### Suspected fix (NOT applied — needs Brandon's call)

Two architectural options, and Brandon needs to pick one:

- **Option A — kill the system-mode pulseaudio entirely.** Disable + mask `/etc/systemd/system/pulseaudio.service`, kill PID 530, kill PID 3114 (or reconfigure gdm), and remove `/etc/xdg/autostart/pulseaudio.desktop` (or replace with a no-op). Then either restart `ugv_tools_api.voice.ears` and `bt-robot-speaker.sh` to repick up audio through pipewire-pulse, or audit them for direct-ALSA hardcoding. This is the "PipeWire is the only audio daemon" world.

- **Option B — leave the system pulseaudio in place; route only Orchestrator containers to pipewire-pulse.** Containers would need their own `PULSE_SERVER` env that points at the user-1000 pipewire-pulse socket, and we'd accept that the host-side audio remains served by the legacy pulseaudio. This means the EMEET will remain visible only to the system pulseaudio, and any app that expects to find it via wireplumber/pipewire-pulse won't see it. This effectively contradicts the plan's goal of unifying on PipeWire.

Option A is the correct architectural move but is risky to do live with the ears pipeline running. Option B is safer but probably not what the plan author intended.

### Safety footnote (Task 1 continuation)

No ALSA device opened directly by this run. No `arecord`, no `aplay`, no `pactl set-card-profile`, no `pactl set-default-source/sink`, no `pactl suspend-*`, no `/dev/snd/*` access. All findings above are from `pactl info`, `pw-cli list-objects`, `cat /proc/asound/cards`, `sudo fuser -v` (which only reports holders, doesn't open devices), `ps`, and `systemctl ... is-active/is-enabled/status`.

### Pw-cli list-objects 0 capture (final state)

```
        id 0, type PipeWire:Interface:Core/3
                object.serial = "0"
                core.name = "pipewire-0"
```

(Three lines total. No nodes, no factories, no devices.)

---

## Path B — System Pulseaudio Discovery (2026-04-26 12:45:00 EDT)

**Verdict:** **REACHABLE-AS-ROOT** (with cookie). System-mode pulseaudio is healthy, owns all 5 ALSA cards including the EMEET, and root processes can connect to it via `unix:/run/pulse/native` provided they pass `PULSE_COOKIE=/run/pulse/.config/pulse/cookie`. EMEET is enumerated as both source and sink with the expected `alsa_input.usb-EMEET_*` / `alsa_output.usb-EMEET_*` naming. No surprises in `system.pa` — it's the stock Ubuntu config that auto-detects cards via `module-udev-detect`.

### Step 1 — System pulseaudio.service unit (verbatim)

```
# /etc/systemd/system/pulseaudio.service
[Unit]
Description=PulseAudio system server
# DO NOT ADD ConditionUser=!root

[Service]
Type=notify
ExecStart=/usr/bin/pulseaudio --daemonize=no --system --realtime --log-target=journal
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Notes on the unit:
- `--system` → system-mode (runs as `pulse:pulse`, listens on `/run/pulse/native` not `/run/user/<uid>/pulse/native`)
- `--daemonize=no` + `Type=notify` → systemd waits for sd_notify before considering it READY
- `--realtime` → asks for SCHED_FIFO (RTKit, may emit cosmetic AccessDenied warnings — not relevant)
- No `User=`/`Group=` overrides — pulseaudio drops to uid `pulse` itself when given `--system`
- Comment "DO NOT ADD ConditionUser=!root" hints whoever installed it tripped on that rule before; we keep it intact.
- `WantedBy=multi-user.target` → starts at boot regardless of GDM/login state. This is why it's been quietly running all along.

### Step 2 — /etc/pulse/system.pa (relevant excerpts)

```
load-module module-device-restore
load-module module-stream-restore
load-module module-card-restore

.ifexists module-udev-detect.so
load-module module-udev-detect          # <-- this is what claims the cards
.else
load-module module-detect
.endif

.ifexists module-esound-protocol-unix.so
load-module module-esound-protocol-unix
.endif
load-module module-native-protocol-unix # <-- this is the IPC socket; default path /run/pulse/native

load-module module-default-device-restore
load-module module-always-sink
load-module module-suspend-on-idle
load-module module-position-event-sounds

.nofail
.include /etc/pulse/system.pa.d         # <-- snippet dir, currently empty
```

Key observations:
- `module-native-protocol-unix` is loaded with **no arguments** — that means defaults: socket at `/run/pulse/native`, no `auth-anonymous=1`, no `auth-group=` override. Auth therefore falls back to **cookie-based** authentication.
- `module-udev-detect` is what enumerates and claims the 5 ALSA cards. Same module wireplumber was fighting with in the previous remediation.
- `/etc/pulse/system.pa.d/` exists but contains **no snippet files**, so nothing is overlaying the defaults.
- `module-always-sink` ensures a fallback null sink exists if hardware disappears — useful when EMEET hot-unplugs.
- `module-suspend-on-idle` is loaded → idle sources/sinks SUSPEND, which matches what we saw in `pactl list short` output (every card returned `SUSPENDED`). Not a problem; clients un-suspend on first I/O.

### Step 3 — Discovered socket path

```
$ sudo find /var/run /run /tmp -maxdepth 4 -name native -type s
/run/pulse/native              <-- system pulseaudio socket (the one we want)
/run/user/1000/pulse/native    <-- user-session pipewire-pulse socket (separate, idle)
/run/user/128/pulse/native     <-- gdm uid 128 pulseaudio socket (separate)
```

`/var/run/pulse` is the standard symlink to `/run/pulse` on this system. Both paths resolve to the same socket inode.

### Step 4 — Socket permissions, group, cookie

Socket:
```
$ sudo stat /run/pulse/native
  File: /run/pulse/native
  Size: 0  Blocks: 0  IO Block: 4096   socket
  Access: (0777/srwxrwxrwx)  Uid: (116/pulse)  Gid: (125/pulse)
```
Socket mode is **0777 (world rw)** — kernel-level access is unrestricted, but PulseAudio enforces auth at the protocol layer.

`pulse-access` group:
```
$ getent group pulse-access
pulse-access:x:126:        # group exists, NO members
```
Empty membership list. The socket is not protected by group access; pulseaudio is using **cookie auth** instead.

Cookie file:
```
$ sudo stat /run/pulse/.config/pulse/cookie
  File: /run/pulse/.config/pulse/cookie
  Size: 256   Blocks: 8   IO Block: 4096   regular file
  Access: (0600/-rw-------)  Uid: (116/pulse)  Gid: (125/pulse)
```
Standard 256-byte cookie, 0600, owned by `pulse:pulse`. **Root can read it directly via the filesystem**; non-root readers would need either pulse uid, group access, or world-read.

Pulse runtime tree:
```
$ sudo ls -la /run/pulse/.config/pulse/
total 0
drwxr-xr-x 2 pulse pulse 60 Apr 26 15:46 .
drwxr-xr-x 3 pulse pulse 60 Apr 26 15:46 ..
-rw------- 1 pulse pulse 256 Apr 26 15:46 cookie

$ sudo ls -la /var/lib/pulse
drwx------ 2 pulse pulse 4096 Apr  2 00:57 .
-rw------- pulse pulse  20480  card-database.tdb
-rw------- pulse pulse      1  default-sink     # contains "1\n" → EMEET sink picked as default at some point
-rw------- pulse pulse      1  default-source
-rw------- pulse pulse  16384  device-volumes.tdb
-rw------- pulse pulse    696  stream-volumes.tdb
```
`/var/lib/pulse/` is the persistent state directory (volumes, default-device choices). Read-only relevance to our task — we're not touching these.

### Step 5 — Connectivity test from root

First attempt **without** cookie:
```
$ sudo pactl --server unix:/run/pulse/native info
Connection failure: Access denied
```
Confirms: socket is reachable, but pulseaudio rejects unauthenticated clients.

Second attempt **with** explicit `PULSE_COOKIE`:
```
$ sudo env PULSE_COOKIE=/run/pulse/.config/pulse/cookie \
        pactl --server unix:/run/pulse/native info
Server String: unix:/run/pulse/native
Library Protocol Version: 35
Server Protocol Version: 35
Is Local: yes
Client Index: 4
Tile Size: 65472
User Name: pulse
Host Name: tegra-ubuntu
Server Name: pulseaudio
Server Version: 15.99.1
Default Sample Specification: s16le 2ch 44100Hz
Default Channel Map: front-left,front-right
Default Sink: alsa_output.usb-Solid_State_System_Co._Ltd._USB_PnP_Audio_Device_000000000000-00.analog-stereo
Default Source: alsa_input.usb-Solid_State_System_Co._Ltd._USB_PnP_Audio_Device_000000000000-00.analog-stereo
Cookie: dd26:41b1
```
**Server Name: `pulseaudio` (NOT "PulseAudio (on PipeWire X)")** — confirms this is the real upstream pulseaudio 15.99.1, not pipewire-pulse impersonating it. Important distinction: the system pulseaudio's `module-alsa-card`, `module-loopback`, `module-echo-cancel`, etc., all work; pipewire-pulse 0.3.48 famously omits some.

**Note:** the current "Default Sink/Source" is the **Solid State System USB PnP** mic+speaker (kernel card index 1), NOT the EMEET. Both are loaded; the Solid State just won the default-restore race at boot. Picking the EMEET will be a Task-5 concern (`pactl set-default-sink/source` in app config), not a discovery blocker.

### Step 6 — Source/sink enumeration (from system pulse, with cookie)

```
$ sudo env PULSE_COOKIE=/run/pulse/.config/pulse/cookie \
        pactl --server unix:/run/pulse/native list short sources
0  alsa_output.usb-Solid_State_System_Co._Ltd._USB_PnP_Audio_Device_000000000000-00.analog-stereo.monitor   module-alsa-card.c  s16le 2ch 48000Hz   SUSPENDED
1  alsa_input.usb-Solid_State_System_Co._Ltd._USB_PnP_Audio_Device_000000000000-00.analog-stereo            module-alsa-card.c  s16le 2ch 48000Hz   SUSPENDED
2  alsa_input.usb-Xitech_USB_Camera_20250606105-02.analog-stereo                                            module-alsa-card.c  s16le 2ch 48000Hz   SUSPENDED
3  alsa_output.usb-EMEET_EMEET_OfficeCore_M0_Plus_654d656574202020da30b6b3caae294M0PLUS-00.analog-stereo.monitor  module-alsa-card.c  s16le 2ch 48000Hz   SUSPENDED
4  alsa_input.usb-EMEET_EMEET_OfficeCore_M0_Plus_654d656574202020da30b6b3caae294M0PLUS-00.analog-stereo     module-alsa-card.c  s16le 2ch 16000Hz   SUSPENDED
5  alsa_output.platform-sound.analog-stereo.monitor                                                         module-alsa-card.c  s16le 2ch 44100Hz   SUSPENDED
6  alsa_input.platform-sound.analog-stereo                                                                  module-alsa-card.c  s16le 2ch 44100Hz   SUSPENDED

$ sudo env PULSE_COOKIE=/run/pulse/.config/pulse/cookie \
        pactl --server unix:/run/pulse/native list short sinks
0  alsa_output.usb-Solid_State_System_Co._Ltd._USB_PnP_Audio_Device_000000000000-00.analog-stereo  module-alsa-card.c  s16le 2ch 48000Hz   SUSPENDED
1  alsa_output.usb-EMEET_EMEET_OfficeCore_M0_Plus_654d656574202020da30b6b3caae294M0PLUS-00.analog-stereo  module-alsa-card.c  s16le 2ch 48000Hz   SUSPENDED
2  alsa_output.platform-sound.analog-stereo  module-alsa-card.c  s16le 2ch 44100Hz   SUSPENDED
```

**EMEET source name (verbatim, exact string for env vars / pactl set-default-source):**
```
alsa_input.usb-EMEET_EMEET_OfficeCore_M0_Plus_654d656574202020da30b6b3caae294M0PLUS-00.analog-stereo
```

**EMEET sink name (verbatim, exact string for env vars / pactl set-default-sink):**
```
alsa_output.usb-EMEET_EMEET_OfficeCore_M0_Plus_654d656574202020da30b6b3caae294M0PLUS-00.analog-stereo
```

Other card names (recorded for later cross-reference):
- Solid State System USB PnP source: `alsa_input.usb-Solid_State_System_Co._Ltd._USB_PnP_Audio_Device_000000000000-00.analog-stereo`
- Solid State System USB PnP sink: `alsa_output.usb-Solid_State_System_Co._Ltd._USB_PnP_Audio_Device_000000000000-00.analog-stereo`
- Xitech USB Camera (mic): `alsa_input.usb-Xitech_USB_Camera_20250606105-02.analog-stereo`
- Tegra HDA / platform-sound: `alsa_input.platform-sound.analog-stereo` + `alsa_output.platform-sound.analog-stereo`
- Tegra APE: not enumerated by pulseaudio (no playback/capture profile available — kernel card 4 is the audio processing engine, not a PCM endpoint)

EMEET capture is correctly probed at **16000 Hz** (telephony-grade 16k mic — matches our previous bench observations). Playback at 48000 Hz.

The `_654d656574202020da30b6b3caae294M0PLUS_` substring is the EMEET's **USB serial bytes** (hex) baked into the udev-derived name. This name is **stable across reboots and unplug/replug** as long as the EMEET firmware doesn't change. If a different EMEET unit were swapped in, the serial portion would change and we'd need to update env vars. Fine for one-robot deployments; flag for fleet.

### Step 7 — XDG autostart (read-only inspection, NOT removed)

```
$ cat /etc/xdg/autostart/pulseaudio.desktop
Name=PulseAudio Sound System
Comment=Start the PulseAudio Sound System
Exec=start-pulseaudio-x11
Terminal=false
Type=Application
X-GNOME-Autostart-Phase=Initialization
X-GNOME-HiddenUnderSystemd=true       # <-- key flag
X-KDE-autostart-phase=1
NoDisplay=true
```

**Path B impact analysis:** `start-pulseaudio-x11` invokes a **user-session** pulseaudio (uid 1000), which lands on `/run/user/1000/pulse/native` — a totally separate socket from `/run/pulse/native`. The system pulseaudio at PID 530 owning the EMEET is **untouched** by this autostart. So for Path B we genuinely do not need to remove this .desktop file; it's noise on a different socket.

The `X-GNOME-HiddenUnderSystemd=true` line means GNOME suppresses this autostart when systemd-user already manages a `pulseaudio.service` unit. Since the user-level `pulseaudio.service` is currently masked (from the previous Task-1 attempt), GNOME falls back to running this .desktop directly. Cosmetic for our purposes; ignore.

### Bind-mount + env-var recommendations for Tasks 2 & 5

**Task 2 (container plumbing):**

Required bind-mounts into the supervisor + ears containers:
- `/run/pulse/native` → `/run/pulse/native` (read-write — Pulse protocol needs full duplex)
- `/run/pulse/.config/pulse/cookie` → `/run/pulse/.config/pulse/cookie` (read-only is fine)

These two are sufficient. We do **not** need `/var/lib/pulse` (it's persistent-state for the daemon, not the client) and we do **not** need to bind-mount `/dev/snd` (the whole point of Path B is the container talks Pulse, not raw ALSA).

A tighter alternative: copy the cookie file into the container at deploy time and bind-mount only the socket. Less filesystem coupling; small cost is having to re-deploy the container if the cookie ever rotates (it doesn't, on this system pulseaudio doesn't auto-rotate cookies).

**Task 5 (app config / supervisor.env):**

Environment variables for the running processes (supervisor + ears, both root):
```bash
PULSE_SERVER=unix:/run/pulse/native
PULSE_COOKIE=/run/pulse/.config/pulse/cookie
# Optional but useful — pins device choice without depending on pulseaudio's default-restore:
PULSE_SOURCE=alsa_input.usb-EMEET_EMEET_OfficeCore_M0_Plus_654d656574202020da30b6b3caae294M0PLUS-00.analog-stereo
PULSE_SINK=alsa_output.usb-EMEET_EMEET_OfficeCore_M0_Plus_654d656574202020da30b6b3caae294M0PLUS-00.analog-stereo
```

`PULSE_SOURCE` / `PULSE_SINK` are honored by `libpulse` for default-device selection. Setting them avoids the issue noted in Step 5 above where the Solid State PnP currently wins the default-restore race.

Optional fallback pattern for resilience: if a process loses connection (system pulseaudio restart), `libpulse` reconnects on next API call as long as `PULSE_SERVER` env is intact. No special supervisor logic needed.

### Anything unexpected (potential cascade triggers — flagged for awareness)

1. **The system pulseaudio config is the stock Ubuntu file**, which means any future `apt-get upgrade` of the `pulseaudio` package may overwrite `/etc/pulse/system.pa` with a new vendor version. If we ever add custom modules (e.g., `module-echo-cancel` for AEC), they should go in `/etc/pulse/system.pa.d/*.pa` snippets, NOT in `system.pa` itself, to survive package upgrades. (Path B does NOT need any such snippet right now — flag for future tuning only.)

2. **`module-suspend-on-idle` is active.** Every source/sink SUSPENDS after idle (~5s default). On first capture/playback request, ALSA does a fresh open of the underlying PCM device. **This is the documented USB-cascade trigger from 2026-04-26** when ALSA opens collide with concurrent xrun handling. Mitigation: keep the EMEET source un-suspended via a long-lived libpulse stream from one of the consumers (ears keeps a stream open; that's already protective). Worth noting because a refactor that removes ears' continuous capture loop could re-expose the cascade.

3. **GDM's pulseaudio (PID 3114, uid `gdm`)** still holds 4 of 5 cards in addition to the system pulseaudio (per Task-1 finding). It's contributing nothing to Path B (talks to `/run/user/128/pulse/native`, which we ignore). It's not a cascade trigger as long as we don't restart it. Just note it exists.

4. **Cookie file lives in `/run/pulse/.config/pulse/cookie` (tmpfs)** — it is regenerated on every system pulseaudio start. If we hardcode the file's bytes into a config, and the daemon is ever cold-restarted, the cached bytes won't match the new cookie and clients will get "Access denied". Bind-mount the path (not the contents) to follow rotation automatically.

5. **`Default Sample Specification: s16le 2ch 44100Hz`** in pactl info is misleading — actual capture happens at the device's native rate (16000 Hz for EMEET). Pulseaudio resamples between client and device. If a client opens a stream at 44.1kHz, pulse will pull from EMEET at 16k and resample. This is fine but worth knowing if anyone debugs why a 44.1k stream looks different from a 16k stream.

6. **`Server Version: 15.99.1`** — pulseaudio 16-prerelease (Ubuntu 22.04's shipped version). **Has all the modules we'd ever want for AEC, loopback, virtual sinks, etc.** Significantly less limited than pipewire-pulse 0.3.48. This is actually a side-benefit of Path B if we ever want server-side echo cancellation.

7. **The EMEET name string contains the literal substring `EMEET_EMEET_`** (vendor name appears twice — once as USB manufacturer, once as USB product). This is what udev hands to PulseAudio. Our env vars must include both occurrences exactly. Easy to mistype — quote in `supervisor.env` and verify byte-for-byte from this notes file.

### Final verdict

**REACHABLE-AS-ROOT** — system pulseaudio is the right backend for Path B. Two bind-mounts + two (or four) env vars get supervisor + ears talking Pulse to the same daemon already serving `bt-robot-speaker.sh` and the existing voice ears pipeline. Everyone shares one daemon, one EMEET claim, no contention.

Notes file: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/docs/plans/notes/2026-04-26-pulse-preflight.md`

---

## Path B — Container Recreate Command (proposed, awaiting authorization)

Generated by Task 2 reconstruction at 2026-04-26 17:01:42 UTC.

### Source

- `docker inspect ugv_waveshare` output: `/tmp/ugv_waveshare_inspect.json` (laptop, pulled from Jetson `/tmp/ugv_waveshare_inspect.json`)
- Launch script: **none found in repo**. Container is a persistent stateful resource — `ugv-waveshare.service` only does `docker start` + `docker exec`, never `docker run`. The original `docker run` invocation is not tracked in any tracked or untracked script. The recreate command below is reverse-engineered from `docker inspect` + `docker image inspect`.
- Image baked-env compared against running env to identify which `-e` flags were passed at run time.

### Current container state (from inspect)

- **Container ID:** `676752f64263…`
- **Name:** `/ugv_waveshare`
- **Image:** `dudulrx0601/ugv_jetson_ros_humble:v1028` (sha256 `80cfdba5f09e…`)
- **Created:** 2026-04-15T17:54:23Z
- **Hostname:** `tegra-ubuntu`
- **Cmd:** `["sleep","infinity"]`
- **Entrypoint:** `["/opt/nvidia/nvidia_entrypoint.sh"]` (from image; do NOT override)
- **WorkingDir:** `/home/ws`
- **NetworkMode:** `host`
- **Privileged:** `true`
- **Runtime:** `nvidia`
- **RestartPolicy:** `unless-stopped`
- **IpcMode:** `private` (Docker default; no flag needed)
- **PidMode:** `""` (Docker default; no flag needed)
- **UTSMode:** `""` (Docker default; no flag needed)
- **CapAdd / CapDrop:** `null` (privileged=true makes this moot)
- **Devices:** `[]` (privileged=true exposes everything)
- **SecurityOpt:** `["label=disable"]`
- **ShmSize:** `67108864` (64 MiB — Docker default; no flag needed)

### Current bind-mounts (from inspect)

1. `/dev` → `/dev` (rw)
2. `/home/jetson/ugv_ws_waveshare` → `/home/ws/ugv_ws` (rw)
3. `/tmp/.X11-unix` → `/tmp/.X11-unix` (rw)
4. **Anonymous volume** `8042a403c0398061173172f8fc1219d55724db7b3a2a70ab761cf1a0dde3e2bd` → `:` (literal colon, malformed `VOLUME` declaration in upstream image; volume is **EMPTY** — verified `4.0K` total, only `.` and `..` directories, owner `root:root`, ctime Oct 25 2024 = creation-time only). Safe to discard.

### Runtime-overridden env vars

Compared `docker inspect` (running container) against `docker image inspect` (baked-in image env). All but one entry match the image. The single runtime override:

- **`DISPLAY=:0`** (image bakes `DISPLAY=localhost:12.0`; running container has `:0`)

All proxy vars (`HTTP_PROXY`, `https_proxy`, `NO_PROXY`, etc.), `XAUTHORITY=""`, `LIBGL_ALWAYS_SOFTWARE=1` are **already baked into the image** — no `-e` flag needed.

### Proposed new command

```bash
# Stop + remove old container (will lose anonymous volume 8042a403…, which is empty)
docker stop ugv_waveshare
docker rm ugv_waveshare

# Recreate with /run/pulse bind-mount added (the ONLY new flag vs. original)
docker run -d \
  --name ugv_waveshare \
  --hostname tegra-ubuntu \
  --restart unless-stopped \
  --network host \
  --privileged \
  --runtime nvidia \
  --security-opt label=disable \
  --workdir /home/ws \
  -e DISPLAY=:0 \
  -v /dev:/dev \
  -v /home/jetson/ugv_ws_waveshare:/home/ws/ugv_ws \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v /run/pulse:/run/pulse:ro \
  dudulrx0601/ugv_jetson_ros_humble:v1028 \
  sleep infinity
```

### What changes

- ONE new flag: `-v /run/pulse:/run/pulse:ro` — exposes system pulseaudio's socket (`/run/pulse/native`) and cookie subdirectory (`/run/pulse/.config/pulse/cookie`) to the container, read-only.
- Anonymous volume `8042a403…` (mounted at literal `:`) is intentionally NOT recreated — it is empty and the original mount path is malformed. Adding `-v 8042a403…:` would be malformed; reproducing it requires either keeping the broken upstream image's `VOLUME` directive (which Docker re-creates a fresh anonymous volume for on `docker run`, automatically — so the flag is implicit) or omitting the line entirely. **Verified empty, safe to drop.**

### Risks

- ~30–60 s downtime during stop+rm+run. All five `ugv-*.service` units (tools-api, er, voice, ears, supervisor) will fail-and-restart loop until container is back; systemd will reconverge.
- If the new command is missing any original flag, the container comes back wrong; rollback below.
- The original `docker run` invocation is **not in any tracked script**, so the only ground truth is `docker inspect` from BEFORE we recreate. Save `/tmp/ugv_waveshare_inspect.json` to a permanent location before running stop+rm.
- Anonymous volume `8042a403…` will be removed on `docker rm`. **Verified empty (4 KB, only `.` and `..`)** — no production data at risk.
- Image-level `DISPLAY=localhost:12.0` baked-in; we're overriding to `:0` to match the current running state. If something inside the container parses `DISPLAY` weirdly on first boot, it might behave differently — but `start_waveshare.sh` does not reference `DISPLAY`, and the X socket is at `/tmp/.X11-unix/X0` (DISPLAY=:0), so this is the correct override.

### Rollback

If the recreate misfires, restore via the same template minus the new mount:

```bash
docker stop ugv_waveshare
docker rm ugv_waveshare
docker run -d \
  --name ugv_waveshare \
  --hostname tegra-ubuntu \
  --restart unless-stopped \
  --network host \
  --privileged \
  --runtime nvidia \
  --security-opt label=disable \
  --workdir /home/ws \
  -e DISPLAY=:0 \
  -v /dev:/dev \
  -v /home/jetson/ugv_ws_waveshare:/home/ws/ugv_ws \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  dudulrx0601/ugv_jetson_ros_humble:v1028 \
  sleep infinity
```

After either run+rollback path, all dependent systemd units restart automatically via their `Requires=` chain, but a manual `systemctl start ugv-waveshare.service` may be needed if the service was stopped while the container was missing. The bootstrap script `ugv_tools_api/scripts/container-bootstrap.sh` is **idempotent** and re-installs pip deps — run it once after recreate to be safe.

---

## Path B — Container Recreate + Verification (2026-04-26 ~17:30 UTC) — STOPPED EARLY, NOT YET HEALTHY

### Recreate
- Old container ID: not captured (docker stop printed name only); image was running before
- New container ID: `256ed77ab61f44246b04002a083e184faffc74f7522d17c15e21a12c58b18bb0`
- Bind-mounts now (4 named + 1 anonymous volume reappeared from image VOLUME directive):
  - `/dev:/dev`
  - `/home/jetson/ugv_ws_waveshare:/home/ws/ugv_ws`
  - `/tmp/.X11-unix:/tmp/.X11-unix`
  - **`/run/pulse:/run/pulse:ro` ← NEW**
  - `/var/lib/docker/volumes/c6edc25217ad…/_data` (anonymous, from image)

### In-container packages installed
- `pulseaudio-utils` 1:15.99.1+dfsg1-1ubuntu2.2
- `libasound2-plugins:arm64` 1.2.6-1
- `libpulsedsp:arm64` 1:15.99.1+dfsg1-1ubuntu2.2 (pulled in as dependency)

### Bootstrap script (Step 6) — pip deps NOT installed
- Script exists at `/home/jetson/ugv_ws_waveshare/ugv_tools_api/scripts/container-bootstrap.sh`
- Failed: image has baked-in `HTTP_PROXY=http://192.168.10.185:10809` which is unreachable on the current network
- Same proxy config existed in the prior container — pre-existing condition, NOT caused by recreate
- Workaround for Steps 7+: passed `-e http_proxy= -e https_proxy=` to `docker exec`, then `apt-get` worked
- **Outstanding issue:** the supervisor + tools-api Python deps (fastapi, uvicorn, pydantic, opencv-python-headless, depthai, pytest>=7) are NOT installed in the new container. Either:
  - The previous container had them installed manually before; or
  - The unit files override the proxy env when they exec inside; or
  - Some other mechanism. Needs investigation before starting services.

### Pactl from inside container
- `Server Name: pulseaudio` (system PulseAudio v15.99.1) — confirmed reachable
- **EMEET source: NOT VISIBLE** — only `alsa_output.platform-sound.analog-stereo.monitor` and `alsa_input.platform-sound.analog-stereo` (on-board HDA) are listed
- **EMEET sink: NOT VISIBLE** — only `alsa_output.platform-sound.analog-stereo`
- Reason: EMEET microphone is no longer attached to the system (see USB cascade below)

### Cookie auth — unexpected behavior
- `PULSE_COOKIE=/run/pulse/.config/pulse/cookie` env var **does NOT** make libpulse use the bind-mounted host cookie
- libpulse instead auto-generated its own cookie at `/root/.config/pulse/cookie` (different MD5)
- Result: first `pactl info` attempt failed with `Connection failure: Access denied`
- Workaround used: `cp /run/pulse/.config/pulse/cookie /root/.config/pulse/cookie` once — then pactl worked
- **Implication for Task 5/6:** the supervisor.env / ears.env `PULSE_COOKIE=...` line is INERT. We need a different mechanism:
  - Option A: container-bootstrap.sh copies the cookie at every container start (`cp /run/pulse/.config/pulse/cookie ~/.config/pulse/cookie`)
  - Option B: pre-bake `~/.config/pulse/cookie` as a symlink to the bind-mount path (only works if writable, which it isn't with `:ro`)
  - Option C: bind-mount /run/pulse read-write OR specifically the cookie at `/root/.config/pulse/cookie`
  - Option D: set `XDG_CONFIG_HOME=/run/pulse` so libpulse reads cookie from `$XDG_CONFIG_HOME/pulse/cookie` (this is the cleanest — needs verification)

### USB cascade triggered by docker stop/recreate (CRITICAL)
At 17:27:18 (the moment of docker stop+rm+run), kernel logged:
- `usb 1-2.2: 3:0: usb_set_interface failed (-19)` (28 lines of ENODEV rapid-fire)
- `usb 1-2.3: USB disconnect, device number 17`
- `usb 1-2.4: USB disconnect, device number 9` ← this was the EMEET
- `usb 1-3: USB disconnect, device number 3`
- `rtk_btusb: btusb_disconnect` (Bluetooth radio dropped)

After cascade, `lsusb` shows ONLY root hubs — every USB device is gone. `lsusb -t` is empty except the two root hubs. The on-board HDA + tegra APE ALSA cards are still present (not USB), which is why Pulse still works for those cards.

**This was triggered by the container recreate itself**, not by any ALSA call. The `--privileged + -v /dev:/dev` container holding USB devices — when stopped/removed — appears to disrupt the USB tree on this kernel.

**Compared to pre-recreate:** the pre snapshot showed 20 entries on `1-2.1.1` (different bus path) from earlier in the day. The post snapshot shows 28+ entries on `1-2.2`/`1-2.3`/`1-2.4` plus a Bluetooth disconnect, all timestamped to the docker recreate moment. That's a NEW cascade signature, not just historical noise.

### Tests (Step 9) — NOT RUN
- Skipped because the in-container Python deps weren't installed and pytest wouldn't import the supervisor module

### Service restart (Step 10) — NOT ATTEMPTED
- Stopped before starting services per safety rule (mid-sequence surprise)
- Brandon needed in the loop on:
  1. Whether to plug EMEET back in and continue, or roll back first
  2. Whether the docker-recreate-USB-cascade is acceptable behavior
  3. How to provision Python deps inside the container (proxy issue)
  4. Cookie auth mechanism (PULSE_COOKIE env doesn't work — see above)

### Current state
- Container `256ed77ab61f` is running with `/run/pulse:ro` mount
- pactl + libasound2-plugins installed inside container
- All 5 ugv-* services: **inactive** (left stopped)
- System PulseAudio (PID 530): healthy, 1h45m uptime, unaffected
- USB tree: every USB device disconnected (EMEET, BT, others)
- Supervisor.env / ears.env: still configured for pulse routing per pre-stage

### Verdict
DEGRADED — container side of the plumbing works (mount + pactl + auth via cookie copy) but operational verification cannot proceed because (a) EMEET is gone after the docker-induced USB cascade and (b) PULSE_COOKIE env var doesn't actually work, requiring a different cookie-provisioning mechanism. **Stopped before starting services** to await Brandon's call.


## Path B (TCP variant) — Pivot Result (2026-04-26 17:49 UTC)

### Why pivot from bind-mount to TCP
- `docker stop+rm+run` of `--privileged -v /dev:/dev` containers itself triggers USB cascade on Jetson xHCI (separate from the cascade triggered by ALSA against EMEET — confirmed today).
- Every future container update would re-trigger that cascade and require Brandon-in-the-loop replug.
- TCP loopback listener avoids all future container recreates: env-only changes stay live, listener persists via `/etc/pulse/system.pa.d/blackbox.pa`, takes effect at next pulse start.
- Auth-anonymous=1 acceptable because `listen=127.0.0.1` binds loopback only (no external exposure).

### TCP listener
- Live-loaded via `pactl --server unix:/run/pulse/native load-module`, module-id `17`.
- Persist file: `/etc/pulse/system.pa.d/blackbox.pa`.
- Listener: `127.0.0.1:4714` (NOT default `4713` — user-session pulseaudio PID 12255 already holds 4713; using 4714 avoids conflict and keeps both daemons working).
- `ss -tlnp` confirms `pulseaudio` (system, PID 525) bound to `127.0.0.1:4714 LISTEN`.

### Verification
- `pactl --server tcp:127.0.0.1:4714 info` from inside `ugv_waveshare`: `Server Name: pulseaudio`.
- EMEET source visible inside container: yes (`alsa_input.usb-EMEET_*`).
- EMEET sink visible inside container: yes (`alsa_output.usb-EMEET_*`).
- `tests/supervisor/test_pulse_routing.py` updated to accept both `unix:` and `tcp:` PULSE_SERVER prefixes.
- `tests/supervisor/test_pulse_routing.py`: 3 PASSED (was previously SKIP).
- `tests/supervisor/test_config.py`: 9 PASSED (incl. `test_supervisor_mic_default_uses_pulse_after_pipewire_routing`).

### Unblocked side issues
- pytest 6.2.5 → 9.0.3 upgrade in container (anyio plugin demanded `_pytest.scope`).
- `openwakeword` resource models (`melspectrogram.onnx`, `embedding_model.onnx`, etc.) downloaded inside container (bootstrap script doesn't fetch them).
- `google-genai` installed inside container (ER server.py imports it; bootstrap script omitted).

### Services post-pivot
- All 5 ugv-* services: `active` (after start-limit reset + dependency installs).
- Supervisor evidence: `INFO: Uvicorn running on http://0.0.0.0:8083`.
- Ears evidence:
  - `mic: device 31 (pulse) matched hint 'pulse'`
  - `mic: opened dev=31 rate=16000 channels=2`
  - `ears v0.6.0: listening for wake word`

### dmesg
- `usb_set_interface failed` since recovery reboot: **0** (target met).
- Only `usb 1-2.4: current rate 48000 is different from the runtime rate 16000` rate-advisory messages — benign, mic opens at 16kHz on a device whose hardware default is 48kHz.

### Verdict
HEALTHY — TCP pivot succeeded. EMEET reaches container via system pulse over `tcp:127.0.0.1:4714`. All 5 ugv services active and stable. Zero new USB cascade events since the recovery reboot. Container audio plumbing is now insensitive to future container recreates.

### Commit
`df748fe` on `ros2-humble-develop` (Jetson): `feat(deploy): pivot from /run/pulse bind-mount to TCP loopback for system pulse`. Includes both env files, both unit files. Test file change (`test_pulse_routing.py` accepting `tcp:`) synced to Jetson but not yet committed — ready for Brandon to amend or follow-up commit.
