# Motion-Aware Watch Stream Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `WatchStream`'s constant-rate JPEG push with a motion-gated push that only emits a frame when the robot's pose has changed since the last push. Stationary periods cost zero JPEG-token budget; motion gets ambient visual coverage at a tunable target FPS. Operator validation 2026-04-26: at 1 FPS the budget rotated at ~80% in 2 minutes during an active mission; this plan reduces JPEG burn by an estimated 5–10× during typical missions where the robot is stopped >50% of the time (waiting at goals, recovery rotations, planner pauses).

**Architecture:** `WatchStream` accepts a `pose_provider: Callable[[], Optional[dict]]` callback at construction. Each iteration polls current pose, compares to the last *successfully pushed* pose, and emits a frame only when the linear or angular delta crosses a threshold. The check runs at the existing FPS clock (e.g., 1 Hz) but the *push* fires only on motion. Stationary robot → zero JPEGs uploaded → zero token charge. The model still has `get_camera_view` as a pull tool for explicit visual checks. Tunable thresholds (`motion_threshold_m`, `motion_threshold_rad`, target FPS) all become env-configurable via SupervisorConfig.

**Tech Stack:**
- Python 3.10 inside the `ugv_waveshare` Docker container on Jetson Orin Nano
- Existing `MissionPoller` already calls `_fetch_pose()` → `tools_api/tool/status_get_pose` (the EKF-fused /odom). We can either reuse this or have WatchStream fetch independently.
- Existing `WatchStream` in `supervisor/watch_stream.py` (post-Task 3 of the raw-WS port — already takes `send_video` callback, transport-agnostic)
- Existing `TokenBudget` `on_frame` hook (charges JPEG tokens per push) — naturally gets called less when motion-gated

**Out of scope (deferred):**
- Predictive lookahead (push frames ahead of motion based on planner trajectory)
- Multi-source watch (OAK-D + pantilt simultaneously)
- Variable-rate FPS based on motion magnitude (currently: motion = on, stationary = off; not "fast motion = 1 FPS, slow = 0.5 FPS")
- Pose-jitter filtering (we'll see in bench whether `motion_threshold_m=0.05` is too sensitive to encoder noise)

---

## Empirical Knowledge Carried Forward

| # | Lesson | Touched by |
|---|---|---|
| 1 | `WatchStream` is transport-agnostic via `send_video` callback (Task 3 of 2026-04-25 raw-WS port) | Task 2 (preserve callback signature) |
| 2 | Pose comes from EKF-fused `/odom` via `tools_api/tool/status_get_pose`, NOT raw encoders (regression guard #11 from 2026-04-24 plan) | Task 1 (use the tools_api endpoint, same as `MissionPoller._fetch_pose`) |
| 3 | `on_frame` callback fires AFTER successful `send_video`, charges TokenBudget. Motion-gating must keep this contract: only call `on_frame` when we actually emit a frame | Task 3 |
| 4 | `WatchStream.run_with_callback` raises RuntimeError if no `send_video` was set; the same guard should not apply to `pose_provider` (it's optional — falls back to constant-rate push) | Task 2 |
| 5 | Tests use a factory `_make_recording_send_video()` for easy assertion | Task 4 (extend with motion-test helpers) |

---

## Target Repository Layout (after this plan)

```
docs/ugv-beast/setup/ugv_tools_api/
├── ugv_tools_api/
│   └── supervisor/
│       ├── watch_stream.py     # MODIFIED: + pose_provider, + motion gating
│       ├── config.py           # MODIFIED: + watch_fps, motion_threshold_m, motion_threshold_rad
│       ├── session.py          # MODIFIED: pass pose_provider lambda + cfg knobs to WatchStream
│       └── (everything else untouched)
└── tests/
    └── supervisor/
        └── test_watch_stream.py # MODIFIED: + 4 motion-gated tests
```

---

## Implementation Tasks

### Task 1: Add motion-aware fields to SupervisorConfig

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/config.py`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_config.py`

**Step 1: Write failing tests**

Append to `test_config.py`:
```python
def test_watch_motion_defaults(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    from ugv_tools_api.supervisor.config import load
    s = load()
    assert s.watch_fps == 0.5
    assert s.motion_threshold_m == 0.05
    assert s.motion_threshold_rad == 0.05
    assert s.motion_aware_watch is True


def test_watch_motion_env_overrides(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    monkeypatch.setenv("SUPERVISOR_WATCH_FPS", "1.0")
    monkeypatch.setenv("SUPERVISOR_MOTION_THRESHOLD_M", "0.10")
    monkeypatch.setenv("SUPERVISOR_MOTION_THRESHOLD_RAD", "0.20")
    monkeypatch.setenv("SUPERVISOR_MOTION_AWARE_WATCH", "false")
    from ugv_tools_api.supervisor.config import load
    s = load()
    assert s.watch_fps == 1.0
    assert s.motion_threshold_m == 0.10
    assert s.motion_threshold_rad == 0.20
    assert s.motion_aware_watch is False
```

**Step 2: Run, confirm RED.**

**Step 3: Implement** — add fields to `SupervisorConfig` dataclass:
```python
    # Watch-mode tuning. fps is the polling rate (motion checks per
    # second); motion thresholds gate which polls actually push a frame.
    # When motion_aware_watch=False, pushes happen at every poll
    # regardless of motion (constant-rate fallback for debugging).
    watch_fps: float = 0.5
    motion_threshold_m: float = 0.05      # 5 cm linear delta minimum
    motion_threshold_rad: float = 0.05    # ~2.86° rotational delta
    motion_aware_watch: bool = True
```

And in `load()`:
```python
        watch_fps=float(os.environ.get("SUPERVISOR_WATCH_FPS", "0.5")),
        motion_threshold_m=float(os.environ.get("SUPERVISOR_MOTION_THRESHOLD_M", "0.05")),
        motion_threshold_rad=float(os.environ.get("SUPERVISOR_MOTION_THRESHOLD_RAD", "0.05")),
        motion_aware_watch=os.environ.get("SUPERVISOR_MOTION_AWARE_WATCH", "true").lower() in ("true", "1", "yes"),
```

**Step 4: Run tests, confirm GREEN.**

**Step 5: Commit (controller).**

---

### Task 2: Add `pose_provider` + motion gating to WatchStream

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/watch_stream.py`
- Modify: `docs/ugv-beast/setup/ugv_tools_api/tests/supervisor/test_watch_stream.py`

**Step 1: Write failing tests**

Append to `test_watch_stream.py`:
```python
def _pose(x: float = 0.0, y: float = 0.0, yaw: float = 0.0) -> dict:
    return {"x": x, "y": y, "yaw": yaw}


@pytest.mark.asyncio
async def test_motion_gated_pushes_only_when_pose_changes_beyond_threshold():
    send_video, calls = _make_recording_send_video()
    cam = _FakeCam()
    pose_state = {"value": _pose(0.0, 0.0, 0.0)}
    w = WatchStream(
        cam, fps=20.0,
        send_video=send_video,
        pose_provider=lambda: pose_state["value"],
        motion_threshold_m=0.05,
        motion_threshold_rad=0.05,
    )
    w.set(on=True, source="pantilt")
    task = asyncio.create_task(w.run_with_callback())
    await asyncio.sleep(0.15)  # robot stationary — no pushes
    assert len(calls) == 0, f"stationary robot should not push, got {len(calls)}"
    pose_state["value"] = _pose(0.10, 0.0, 0.0)  # moved 10 cm
    await asyncio.sleep(0.15)
    pre_count = len(calls)
    assert pre_count >= 1, f"motion should trigger push, got {pre_count}"
    pose_state["value"] = _pose(0.10, 0.0, 0.0)  # stationary again at new pose
    await asyncio.sleep(0.15)
    w.stop()
    await task
    # No more pushes after returning to stationary
    assert len(calls) == pre_count or len(calls) == pre_count + 1  # tolerance for one in-flight


@pytest.mark.asyncio
async def test_motion_gated_pushes_on_yaw_rotation():
    send_video, calls = _make_recording_send_video()
    cam = _FakeCam()
    pose_state = {"value": _pose(0.0, 0.0, 0.0)}
    w = WatchStream(
        cam, fps=20.0,
        send_video=send_video,
        pose_provider=lambda: pose_state["value"],
        motion_threshold_rad=0.05,
    )
    w.set(on=True, source="pantilt")
    task = asyncio.create_task(w.run_with_callback())
    await asyncio.sleep(0.1)
    pose_state["value"] = _pose(0.0, 0.0, 0.10)  # rotated 0.1 rad
    await asyncio.sleep(0.15)
    w.stop()
    await task
    assert len(calls) >= 1, "yaw rotation should trigger push"


@pytest.mark.asyncio
async def test_motion_gated_falls_back_to_constant_rate_when_pose_provider_returns_none():
    """If pose_provider raises or returns None, default to pushing
    every iteration (fail-open for safety; we'd rather waste a few
    tokens than have the model lose visual context entirely)."""
    send_video, calls = _make_recording_send_video()
    cam = _FakeCam()
    w = WatchStream(
        cam, fps=20.0,
        send_video=send_video,
        pose_provider=lambda: None,  # no pose available
    )
    w.set(on=True, source="pantilt")
    task = asyncio.create_task(w.run_with_callback())
    await asyncio.sleep(0.25)
    w.stop()
    await task
    assert len(calls) >= 2, "fail-open: missing pose should not silence pushes"


@pytest.mark.asyncio
async def test_no_pose_provider_means_constant_rate():
    """Backward compat: pose_provider=None (default) preserves the
    pre-motion-aware behavior — push every iteration."""
    send_video, calls = _make_recording_send_video()
    cam = _FakeCam()
    w = WatchStream(cam, fps=20.0, send_video=send_video)  # no pose_provider
    w.set(on=True, source="pantilt")
    task = asyncio.create_task(w.run_with_callback())
    await asyncio.sleep(0.25)
    w.stop()
    await task
    assert len(calls) >= 2
```

**Step 2: Run, confirm RED.**

**Step 3: Implement** — modify `WatchStream`:

```python
def __init__(
    self,
    camera: _CameraLike,
    fps: float = 1.0,
    on_frame: Optional[Callable[[], None]] = None,
    send_video: Optional[Callable[[bytes, str], Awaitable[None]]] = None,
    pose_provider: Optional[Callable[[], Optional[dict]]] = None,
    motion_threshold_m: float = 0.05,
    motion_threshold_rad: float = 0.05,
) -> None:
    # ... existing fields ...
    self._pose_provider = pose_provider
    self._motion_thresh_m = motion_threshold_m
    self._motion_thresh_rad = motion_threshold_rad
    self._last_pushed_pose: Optional[dict] = None
    self._diag_motion_skipped = 0
    self._diag_motion_pushed = 0
```

Add motion-check helper:
```python
def _has_moved(self, current: dict) -> bool:
    """True if linear or angular delta from last pushed pose exceeds
    threshold. None last-pose (first frame) always returns True so
    the first frame after on=True pushes immediately."""
    if self._last_pushed_pose is None:
        return True
    dx = current["x"] - self._last_pushed_pose["x"]
    dy = current["y"] - self._last_pushed_pose["y"]
    linear = (dx * dx + dy * dy) ** 0.5
    if linear >= self._motion_thresh_m:
        return True
    angular = abs(current["yaw"] - self._last_pushed_pose["yaw"])
    # Wrap-around: 359° - 1° = 2°, not 358°. Mod 2π.
    import math
    angular = min(angular, 2 * math.pi - angular)
    return angular >= self._motion_thresh_rad
```

Modify `run_with_callback` push block:
```python
if self._on:
    jpeg = self._cam.get_camera_jpeg() if self._source == "pantilt" else None
    if jpeg:
        # Motion gate (fail-open: if pose_provider is None or returns
        # None, push as if motion-aware were off — better to waste
        # tokens than lose visual context).
        should_push = True
        current_pose = None
        if self._pose_provider is not None:
            try:
                current_pose = self._pose_provider()
            except Exception:
                current_pose = None
            if current_pose is not None:
                should_push = self._has_moved(current_pose)

        if should_push:
            try:
                await self._send_video(jpeg, "image/jpeg")
                self._bytes_sent += len(jpeg)
                self._frames_sent += 1
                self._diag_motion_pushed += 1
                if current_pose is not None:
                    self._last_pushed_pose = current_pose
                if self._on_frame is not None:
                    try: self._on_frame()
                    except Exception: pass
            except Exception as e:
                print(f"[watch] send error swallowed: {type(e).__name__}: {e}")
        else:
            self._diag_motion_skipped += 1
```

**Step 4: Run tests, confirm GREEN.**

**Step 5: Commit (controller).**

---

### Task 3: Plumb pose_provider + cfg knobs from session.py

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/supervisor/session.py`

**Step 1: Locate the WatchStream construction block** (currently around line 947-957 after the EMEET-passthrough commit).

**Step 2: Build the pose_provider closure** — reuse `MissionPoller._fetch_pose` pattern. Add a helper at module scope:
```python
async def _fetch_pose_async(cfg: SupervisorConfig) -> Optional[dict]:
    """Call tools_api/tool/status_get_pose and return the pose dict
    or None on failure. Mirrors mission_poller._fetch_pose."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as c:
            r = await c.post(f"{cfg.tools_api_url}/tool/status_get_pose", json={})
            if r.status_code >= 400:
                return None
            return r.json().get("result", {}) or None
    except Exception:
        return None
```

**Step 3: WatchStream's pose_provider has a sync API** (`Callable[[], Optional[dict]]`), so we need to bridge async-fetch to sync-callable. Use a cached pose with a refresh task:

```python
# Inside _run_one_session, before constructing WatchStream:
_cached_pose: dict[str, Optional[dict]] = {"pose": None}

async def _refresh_pose_loop():
    while not session_stop.is_set():
        try:
            _cached_pose["pose"] = await _fetch_pose_async(self._cfg)
        except Exception:
            pass
        try:
            await asyncio.wait_for(session_stop.wait(),
                                    timeout=1.0 / max(self._cfg.watch_fps, 0.1))
            return
        except asyncio.TimeoutError:
            pass

pose_refresh_task = asyncio.create_task(_refresh_pose_loop())

watch = WatchStream(
    self._cam,
    fps=self._cfg.watch_fps,
    on_frame=lambda: budget.jpeg_frames(1),
    send_video=_send_video,
    pose_provider=(lambda: _cached_pose["pose"]) if self._cfg.motion_aware_watch else None,
    motion_threshold_m=self._cfg.motion_threshold_m,
    motion_threshold_rad=self._cfg.motion_threshold_rad,
)
```

**Step 4: Cancel the pose-refresh task in the finally block** alongside the other tasks.

**Step 5: Sync, run full test suite, confirm GREEN.**

**Step 6: Commit (controller).**

---

### Task 4: Update systemd unit `-e` allowlist for new env vars

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/deploy/ugv-supervisor.service`
- Manually copy to `/etc/systemd/system/` on Jetson + `daemon-reload` (no automated systemctl reload during test)

**Step 1: Add to `-e` allowlist (the long docker-exec command):**
```
-e SUPERVISOR_WATCH_FPS \
-e SUPERVISOR_MOTION_THRESHOLD_M \
-e SUPERVISOR_MOTION_THRESHOLD_RAD \
-e SUPERVISOR_MOTION_AWARE_WATCH \
```

**Step 2: Sync to Jetson, install, reload:**
```bash
sshpass -p 'jetson' rsync ... deploy/ugv-supervisor.service jetson@.../tmp/
sshpass -p 'jetson' ssh ... 'echo jetson | sudo -S cp /tmp/ugv-supervisor.service /etc/systemd/system/ugv-supervisor.service && sudo systemctl daemon-reload && sudo systemctl restart ugv-supervisor.service'
```

**Step 3: Verify env propagated:**
```bash
sshpass -p 'jetson' ssh ... 'PID=$(pgrep -f supervisor.main); cat /proc/$PID/environ | tr "\0" "\n" | grep -E "SUPERVISOR_(WATCH|MOTION)"'
```

**Step 4: Commit (controller).**

---

### Task 5: Live bench — operator-led

**Operator validates:**
1. **Stationary**: wake supervisor, sit idle. Watch journal for `[watch]` skipped vs pushed counts. Expectation: `pushed=0, skipped=N` while stationary.
2. **Moving**: dispatch `dispatch_er_mission(drive forward 30 cm)`. During motion, expect `pushed > 0` (frames emitted). After mission completes (robot stopped), `pushed` increments stop.
3. **Mid-rotation**: dispatch `dispatch_er_mission(turn 90 degrees)`. Yaw delta should trigger pushes throughout rotation.
4. **Budget impact**: track `budget rotate` line — should fire much later (or not at all) compared to pre-motion-aware bench. Target: 5–10× improvement in time-to-rotate during typical missions.

**Save bench record** to `docs/plans/notes/2026-04-26-motion-aware-bench.md`.

---

### Task 6: Cleanup + memory update

**Step 1: Update memory entry** at `~/.claude/projects/.../memory/ugv_supervisor_2_5_async.md` with motion-aware section.

**Step 2: Mint a snapshot** capturing the bench results + decision to keep/tune motion thresholds.

---

## Post-Implementation Verification

- [ ] All 4 new tests in `test_config.py` + 4 new tests in `test_watch_stream.py` pass
- [ ] Full supervisor suite stays at current count + 8 new = 89 passed
- [ ] Live bench shows stationary period with zero `[watch]` pushes
- [ ] Live bench shows motion period with `pushed > 0`
- [ ] Budget rotate fires noticeably later (or not within typical session length) compared to baseline
- [ ] Motion thresholds (`0.05 m / 0.05 rad`) feel right at bench — not over-pushing during pose jitter, not under-pushing during slow turns

## Rollback Path

`SUPERVISOR_MOTION_AWARE_WATCH=false` reverts to constant-rate push at `watch_fps`. No code change needed. Effective immediately on next session.
