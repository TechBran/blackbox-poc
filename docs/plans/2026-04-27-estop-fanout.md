# E-Stop Fan-Out Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `system_emergency_stop` (HTTP tool) actually stop the robot by fanning out to (1) Nav2 goal cancel, (2) `/explore/stop` service trigger, (3) sustained zero-Twist publishes, in addition to the current firmware T=0 cutoff. Same fan-out is used by `cancel_er_mission` so a supervisor mission abort also halts exploration.

**Architecture:** Today, `system_emergency_stop` publishes ONE zero `/cmd_vel` and one firmware T=0. Within ~50 ms `controller_server` overwrites the zero with a fresh velocity, and the explorer thread queues the next frontier goal. We extend the tool to (a) cancel the active Nav2 `navigate_to_pose` goal handle (already tracked in `tools/nav.py:_state["handle"]`), (b) call `/explore/stop` if the explorer is in `EXPLORING` state, (c) keep publishing zero `Twist` for 1.5 s at 20 Hz to outlive any controller_server spike, then (d) fire the firmware T=0. Failures in any one branch must not block the others (each branch is best-effort, wrapped in `try/except`). Returns a structured dict so the caller can verify which branches fired.

**Tech Stack:** Python 3.10, ROS 2 Humble (`rclpy`), `geometry_msgs/Twist`, `std_msgs/String`, `std_srvs/Trigger`, pytest with `unittest.mock`. Source-of-truth files live at `docs/ugv-beast/setup/ugv_tools_api/` on the laptop and rsync to `/home/ws/ugv_ws/ugv_tools_api/` on the Jetson. **DO NOT** use `scripts/sync-ugv-tools.sh` — it clobbers `GOOGLE_API_KEY` in `supervisor.env`. Use targeted `rsync` of `tools/system.py` only.

**Out of scope:**
- Replacing the cheat sheet's incorrect `/system/estop` ROS-service line (separate doc fix).
- Adding a continuous (always-on) e-stop watchdog node — the tool-call fan-out is sufficient for now.
- Modifying `cancel_er_mission` itself; we make `system_emergency_stop` correct, then the supervisor should call BOTH (already does — see `supervisor/session.py:1173`).

---

### Task 1: Write the failing test for `nav_cancel` branch

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/tests/test_tools_system.py`

**Context:** The existing test file at line 11 asserts the three system tools are registered. We need a NEW test that verifies `system_emergency_stop` calls `cancel_goal_async()` on the active Nav2 goal handle when one exists.

**Step 1: Read the existing test file to understand the registry pattern**

Run: `sed -n '1,50p' docs/ugv-beast/setup/ugv_tools_api/tests/test_tools_system.py`

Note the `RosBridge` mock pattern and how publishers are stubbed.

**Step 2: Add the failing test**

Append to `tests/test_tools_system.py`:

```python
import asyncio
from unittest.mock import MagicMock, patch
from ugv_tools_api.tools import system as system_tools


def test_emergency_stop_cancels_active_nav_goal():
    """When a Nav2 goal handle is active in nav._state, e-stop must cancel it."""
    from ugv_tools_api.tools import nav as nav_tools

    fake_handle = MagicMock()
    fake_bridge = MagicMock()
    fake_bridge.node.publisher.return_value = MagicMock()

    with patch.object(nav_tools, "_state", {"handle": fake_handle, "client": None,
                                              "status": "navigating", "distance_remaining": None}), \
         patch.object(nav_tools, "_lock", MagicMock()), \
         patch("ugv_tools_api.tools.system.RosBridge") as br:
        br.instance.return_value = fake_bridge
        result = asyncio.run(system_tools.system_emergency_stop())

    fake_handle.cancel_goal_async.assert_called_once()
    assert result["estopped"] is True
    assert "nav_cancel" in result.get("fanout", [])
```

**Step 3: Run the test to verify it FAILS**

Run: `cd docs/ugv-beast/setup/ugv_tools_api && python3 -m pytest tests/test_tools_system.py::test_emergency_stop_cancels_active_nav_goal -v`

Expected: FAIL — current `system_emergency_stop` does not import `nav._state` or call `cancel_goal_async`. Also `fanout` key not in return.

**Step 4: Commit the failing test**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
git add docs/ugv-beast/setup/ugv_tools_api/tests/test_tools_system.py
git -c user.email="claude@noreply.anthropic.com" -c user.name="Claude" \
  commit -m "test(estop): add failing nav-goal-cancel branch test"
```

---

### Task 2: Write failing tests for `/explore/stop` and zero-cmd_vel-pin branches

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/tests/test_tools_system.py`

**Step 1: Add two more failing tests**

Append:

```python
def test_emergency_stop_calls_explore_stop_service():
    """E-stop must trigger /explore/stop service if the client is reachable."""
    fake_bridge = MagicMock()
    fake_bridge.node.publisher.return_value = MagicMock()
    fake_client = MagicMock()
    fake_client.wait_for_service.return_value = True
    fake_bridge.node.create_client.return_value = fake_client

    with patch("ugv_tools_api.tools.system.RosBridge") as br:
        br.instance.return_value = fake_bridge
        result = asyncio.run(system_tools.system_emergency_stop())

    fake_bridge.node.create_client.assert_called()
    args, _ = fake_bridge.node.create_client.call_args
    # std_srvs.srv.Trigger + topic name
    assert any("/explore/stop" in str(a) for a in args)
    fake_client.call_async.assert_called_once()
    assert "explore_stop" in result.get("fanout", [])


def test_emergency_stop_pins_zero_cmd_vel_multiple_times():
    """E-stop must publish zero Twist repeatedly (>10 times) to outlive controller_server burst."""
    fake_bridge = MagicMock()
    fake_pub = MagicMock()
    fake_bridge.node.publisher.return_value = fake_pub

    with patch("ugv_tools_api.tools.system.RosBridge") as br:
        br.instance.return_value = fake_bridge
        # Speed up the test: monkeypatch the duration constant to 0.1s
        with patch.object(system_tools, "_ESTOP_PIN_SECONDS", 0.1, create=True), \
             patch.object(system_tools, "_ESTOP_PIN_HZ", 100, create=True):
            asyncio.run(system_tools.system_emergency_stop())

    # We expect roughly 10+ publishes during the 0.1 s @ 100 Hz pin.
    # /cmd_vel is published; firmware T=0 is published; +explore/stop client created.
    # Filter calls by argument type — Twist on one publisher, String on another.
    pub_calls = fake_pub.publish.call_count
    assert pub_calls >= 10, f"Expected >=10 zero-Twist publishes, got {pub_calls}"
```

**Step 2: Run both tests to verify they FAIL**

Run: `python3 -m pytest tests/test_tools_system.py -v -k "explore_stop or pins_zero"`

Expected: both FAIL.

**Step 3: Commit**

```bash
git add docs/ugv-beast/setup/ugv_tools_api/tests/test_tools_system.py
git -c user.email="claude@noreply.anthropic.com" -c user.name="Claude" \
  commit -m "test(estop): add failing explore_stop + zero-pin branch tests"
```

---

### Task 3: Implement the fan-out in `system.py`

**Files:**
- Modify: `docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/tools/system.py`

**Step 1: Read current `system.py`**

Read the file to confirm imports and structure. Note: `_cmd_vel_pub()` returns a publisher.

**Step 2: Replace `system_emergency_stop` with the fan-out version**

Edit the file:

- Add at top of file (after existing imports):

```python
import asyncio
import time
from std_srvs.srv import Trigger

# Tunables — overridable by tests
_ESTOP_PIN_SECONDS = 1.5
_ESTOP_PIN_HZ = 20
```

- Replace the existing `async def system_emergency_stop():` body with:

```python
@tool(
    name="system_emergency_stop",
    description=(
        "Emergency stop fan-out. Cancels active Nav2 goal, triggers "
        "/explore/stop, publishes zero geometry_msgs/Twist to /cmd_vel for "
        f"{_ESTOP_PIN_SECONDS}s @ {_ESTOP_PIN_HZ}Hz to outlive controller_server, "
        "and sends firmware-level T=0 cutoff to /ugv/json_cmd. Each branch is "
        "best-effort -- failures in one do not block the others."
    ),
)
async def system_emergency_stop():
    fanout = []

    # 1. Cancel any active Nav2 goal (BT/controller stops on next tick)
    try:
        from . import nav as nav_tools
        with nav_tools._lock:
            handle = nav_tools._state.get("handle")
        if handle is not None:
            handle.cancel_goal_async()
            fanout.append("nav_cancel")
    except Exception:
        pass

    # 2. Trigger /explore/stop if explorer is running
    try:
        node = RosBridge.instance().node
        cli = node.create_client(Trigger, "/explore/stop")
        if cli.wait_for_service(timeout_sec=0.3):
            cli.call_async(Trigger.Request())
            fanout.append("explore_stop")
    except Exception:
        pass

    # 3. Pin /cmd_vel at zero for _ESTOP_PIN_SECONDS @ _ESTOP_PIN_HZ
    twist = Twist()
    twist.linear.x = 0.0; twist.linear.y = 0.0; twist.linear.z = 0.0
    twist.angular.x = 0.0; twist.angular.y = 0.0; twist.angular.z = 0.0
    pub = _cmd_vel_pub()
    period = 1.0 / max(1, _ESTOP_PIN_HZ)
    deadline = time.monotonic() + _ESTOP_PIN_SECONDS
    while time.monotonic() < deadline:
        pub.publish(twist)
        await asyncio.sleep(period)
    fanout.append("cmd_vel_pin")

    # 4. Belt-and-suspenders firmware-level cutoff
    _json_cmd_pub().publish(String(data=json.dumps({"T": 0})))
    fanout.append("fw_T0")

    return {"estopped": True, "fanout": fanout}
```

**Step 3: Run all three tests to verify they PASS**

Run: `python3 -m pytest tests/test_tools_system.py -v`

Expected: all tests pass (the registry test from line 11 still passes; the three new tests pass).

**Step 4: Run the full test file to confirm nothing else broke**

Run: `python3 -m pytest tests/ -v -x --timeout=30`

Expected: all tests pass (or any pre-existing failures unchanged).

**Step 5: Commit**

```bash
git add docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/tools/system.py
git -c user.email="claude@noreply.anthropic.com" -c user.name="Claude" \
  commit -m "feat(estop): fan out emergency_stop to nav cancel + explore stop + cmd_vel pin"
```

---

### Task 4: Deploy to Jetson via targeted rsync

**Step 1: Targeted rsync of just `tools/system.py`**

```bash
sshpass -p 'jetson' rsync -av \
  /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/docs/ugv-beast/setup/ugv_tools_api/ugv_tools_api/tools/system.py \
  jetson@192.168.1.155:/home/ws/ugv_ws/ugv_tools_api/ugv_tools_api/tools/system.py
```

**DO NOT** run `scripts/sync-ugv-tools.sh` — it overwrites supervisor.env and loses GOOGLE_API_KEY.

**Step 2: Restart the tools API service**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -lc "supervisorctl restart ugv_tools_api_bridge 2>&1 | tail -3"'
```

If supervisorctl unavailable, fall back to:

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 \
  'docker exec ugv_waveshare bash -lc "pkill -f tools_api && sleep 2 && ros2 node list | grep tools_api"'
```

**Step 3: Verify the new tool description appears**

```bash
curl -s http://192.168.1.155:8080/tools?format=anthropic | python3 -c "import sys, json; d=json.load(sys.stdin); print(next(t for t in d if t['name']=='system_emergency_stop')['description'])"
```

Expected output should mention "fan-out", "explore/stop", and "cmd_vel for 1.5s".

---

### Task 5: Operator-led bench verification (Brandon in the loop)

**Step 1: Brandon places robot in safe area, supervisor runs an active mission, then issues e-stop**

Telephoned/coordinated procedure (no agent action — Brandon drives):

1. With robot driving (e.g., `ros2 service call /explore/start std_srvs/srv/Trigger`), open a second SSH and watch:
   ```bash
   ros2 topic echo /cmd_vel
   ```
2. Brandon issues e-stop via UI (or `curl -X POST http://192.168.1.155:8080/tool/system_emergency_stop -d '{}'`).
3. Watch `/cmd_vel` — should hold all-zero for ~1.5 s without any mid-stream non-zero values.
4. Robot motors stop within ~100 ms and stay stopped.
5. `ros2 topic echo /explore/status --once` should show `state: IDLE`.
6. Reissuing a normal `/cmd_vel` (e.g., teleop) works again after the pin window.

**Step 2: Verify cancel-mission path also halts explorer**

If `cancel_er_mission` is invoked via the supervisor (voice or HTTP), confirm `/explore/status` reports IDLE within 2 s.

**Step 3: Commit nothing — bench is verification only.**

---

### Task 6: Snapshot + memory

**Step 1: Mint a snapshot capturing the fix**

Use the `/snapshot-dev` procedure with a payload describing:
- Bug found: e-stop only halted briefly, BT/controller_server resumed within 50 ms
- Fix: 4-branch fan-out (nav cancel + explore stop + 1.5 s zero pin + fw T=0)
- Files: `tools/system.py` + `tests/test_tools_system.py`
- Commits: 3 (test + test + impl)
- Search hint: "estop fan-out controller_server cmd_vel pin"

**Step 2: Add a memory entry if a non-obvious lesson emerged**

(e.g., "controller_server overwrites cmd_vel within 50 ms — single zero publishes are insufficient for stop")

---

## Remember
- **One change per restart cycle.** This entire plan is one logical change ("e-stop fan-out") — do not bundle B or F into the same restart.
- **Do not use `scripts/sync-ugv-tools.sh`.** Targeted rsync only.
- **Each branch is best-effort.** If `nav_cancel` raises, the other three must still execute.
- **TDD — tests fail first, then pass.**
