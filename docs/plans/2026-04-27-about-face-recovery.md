# About-Face Recovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Nav2's recovery branch escape 3-sided wall traps reliably by replacing the gentle ±45° spins with full ±π (about-face) rotations, and increase the BackUp from 25 cm @ 5 cm/s to 40 cm @ 10 cm/s. This is a *cheap* test of the hypothesis "180° spin lets the robot find free space behind it" before committing to the more expensive full LIDAR-aware BT plugin (separate plan, deferred).

**Architecture:** The Nav2 `bt_navigator` loads `gentle_recovery.xml` (made non-volatile by the **YAML drift fix** plan, which MUST run first). The recovery `RoundRobin` currently rotates +45° → backs up 25 cm → rotates -45° → waits 2 s. We swap these literals for ±π spins and a longer/faster BackUp. The BT itself, the trigger conditions, and the outer NavigateRecovery node are unchanged. This is a 3-line XML edit.

**Tech Stack:** Nav2 BehaviorTree.CPP XML, sed/python for in-place edit, `colcon build --symlink-install` to keep src and install in sync, operator-led bench at the robot for verification.

**Pre-requisites (HARD blocker):**
- Plan F (`docs/plans/2026-04-27-bt-recovery-yaml-drift.md`) has been completed AND `gentle_recovery.xml` now exists at `src/ugv_main/ugv_nav/param/gentle_recovery.xml` on the Jetson. If not, this plan halts at Task 1.
- Plan A (e-stop fan-out) ideally completed first so a runaway test can be aborted safely.

**Out of scope:**
- LIDAR-aware "find largest free angle" BT condition node (separate plan, deferred until we see whether about-face alone fixes the corner trap).
- Tuning Nav2 stuck-detection thresholds (e.g., `progress_checker.required_movement_radius`).
- Adding "rotate-to-yaw-from-blackboard" Spin variants.
- Modifying the outer `NavigateRecovery number_of_retries="6"` count.

---

### Task 1: Verify Plan F has been completed (hard gate)

**Step 1: Confirm src copy of gentle_recovery.xml exists**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "ls /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml && echo OK"'
```

Expected: `OK` printed (file lists).

**HALT condition:** if "No such file or directory" — abort this plan; run Plan F first.

**Step 2: Confirm src yaml has BT pointer uncommented**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "grep -E \"^[^#]*default_nav_to_pose_bt_xml\" /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/slam_nav.yaml"'
```

Expected: one match.

**HALT condition:** if no match — abort, complete Plan F first.

---

### Task 2: Backup current XML for instant rollback

**Step 1: Backup**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "cp /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml.bak.aboutface && ls -la /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml*"'
```

Expected: 2 files (current + .bak.aboutface).

---

### Task 3: Edit XML — three literal substitutions

**Files:**
- Modify: `/home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml`

**Step 1: Apply the substitutions via Python (avoid sed line-anchored fragility)**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "python3 - <<\"PY\"
path = \"/home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml\"
with open(path) as f:
    text = f.read()

# Three literal replacements
replacements = [
    (\"<Spin spin_dist=\\\"0.785\\\"/>\",  \"<Spin spin_dist=\\\"3.14\\\"/>\"),
    (\"<Spin spin_dist=\\\"-0.785\\\"/>\", \"<Spin spin_dist=\\\"-3.14\\\"/>\"),
    (\"<BackUp backup_dist=\\\"0.25\\\" backup_speed=\\\"0.05\\\"/>\",
     \"<BackUp backup_dist=\\\"0.40\\\" backup_speed=\\\"0.10\\\"/>\"),
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f\"PRE-EDIT FAILED: literal not found: {old}\")
    text = text.replace(old, new, 1)

with open(path, \"w\") as f:
    f.write(text)
print(\"3 substitutions applied\")
PY
"'
```

Expected: `3 substitutions applied`.

**Step 2: Diff against backup to confirm exactly 3 lines changed**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "diff /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml.bak.aboutface /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml | head -20"'
```

Expected: 3 lines changed, all in the `RoundRobin` block.

---

### Task 4: Rebuild + verify install reflects new XML

**Step 1: Colcon build ugv_nav**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "cd /home/ws/ugv_ws && source /opt/ros/humble/setup.bash && colcon build --packages-select ugv_nav --symlink-install 2>&1 | tail -10"'
```

Expected: "Finished <<< ugv_nav".

**Step 2: Confirm install copy matches src**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "diff /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml; echo exit=\$?"'
```

Expected: `exit=0`.

**Step 3: Grep install for the new spin distances**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "grep -E \"spin_dist|BackUp\" /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml"'
```

Expected: spin_dist values are 3.14 and -3.14; BackUp shows 0.40 / 0.10.

---

### Task 5: Restart Nav2 stack

**Step 1: Restart bringup**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "supervisorctl restart bringup_lidar 2>&1 | tail -3 || (pkill -f bringup_lidar.launch.py; sleep 5; echo restarted)"'
```

Wait ~20 s for lifecycle nodes.

**Step 2: Confirm bt_navigator + behavior_server alive**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "source /opt/ros/humble/setup.bash; ros2 node list | grep -E \"bt_navigator|behavior_server|controller_server|planner_server\""'
```

Expected: all four nodes listed.

---

### Task 6: Operator-led bench — corner trap verification (Brandon in-the-loop)

**This is a HUMAN-EXECUTED task — do not attempt to run unattended.**

**Setup:**
1. Brandon physically arranges three boxes/walls forming a 3-sided pocket (~80 cm wide, ~80 cm deep) around 2 m from the robot's start pose.
2. Brandon sends a Nav2 goal that requires the robot to enter the pocket (e.g., a point on the far side of the pocket via Vizanti or `nav_goto_point`).

**Watch loop (in a second terminal):**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "source /opt/ros/humble/setup.bash; ros2 topic echo /behavior_server/transition_event 2>/dev/null | head -50"'
```

This shows BT recovery actions (`Spin`, `BackUp`, `Wait`) firing in real time.

**Acceptance criteria:**
- ✅ Robot enters pocket, declares stuck, recovery fires.
- ✅ First `Spin` is +π (~180°) — verify by watching `/odom` orientation flip.
- ✅ After about-face, robot has a clear path out (LIDAR shows >0.6 m in front).
- ✅ Robot exits pocket within 1-2 recovery iterations.
- ❌ FAILURE: robot still stuck after 6 iterations, or robot collides with rear wall during BackUp 40 cm.

**If FAILURE on collision:** revert immediately:

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "cp /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml.bak.aboutface /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml && cd /home/ws/ugv_ws && source /opt/ros/humble/setup.bash && colcon build --packages-select ugv_nav --symlink-install 2>&1 | tail -3 && supervisorctl restart bringup_lidar"'
```

Then escalate to a custom LIDAR-aware BT plan.

**If SUCCESS:** continue to Task 7.

---

### Task 7: Snapshot + memory

**Step 1: Mint a snapshot**

Capture:
- Bug: ±45° spins + 25 cm BackUp insufficient for 3-sided traps.
- Fix: ±π spins + 40 cm @ 10 cm/s BackUp.
- Verification: bench succeeded with N attempts, recovery exited pocket within K iterations.
- Search hints: "BT recovery about-face spin pi 3.14 corner trap"

**Step 2: Update memory**

Add or update memory entry (`feedback_ugv_bt_recovery.md` or similar) noting:
- Rule: prefer about-face (±π) Spin over gentle ±45° for tracked robots in confined spaces.
- Why: 45° lands robot still facing wall; 180° lets the LIDAR see the entire opposite hemisphere.
- How to apply: when authoring custom Nav2 BT recoveries, default to ±π unless the platform has strafe/holonomic motion.

**If the bench identified an edge case** (e.g., ±π is too aggressive for narrow hallways), record it.

---

## Remember
- **HARD PREREQUISITE:** Plan F must be done first.
- **One change per restart cycle.** Don't bundle this with anything else.
- **Bench is operator-led.** Do not attempt the corner-trap test autonomously — collision risk.
- **Backup file is your safety net.** If bench fails on collision, use it.
- **Per feedback memory** (Don't bundle YAML/launch changes): this plan is one logical change. Stop after Task 7.
