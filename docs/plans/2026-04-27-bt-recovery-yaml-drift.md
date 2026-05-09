# BT Recovery YAML Drift Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the silent footgun where the custom `gentle_recovery.xml` Behavior Tree only exists in Nav2's `install/` tree (not `src/`) AND the source-side `slam_nav.yaml:72` line that points Nav2 at this BT is **commented out**. Next `colcon build --packages-select ugv_nav` regenerates `install/` from `src/`, silently losing both the file and the pointer, and Nav2 falls back to its default BT (no custom recovery, no 45° spins, no special back-up).

**Architecture:** `ugv_main/ugv_nav/CMakeLists.txt` line 24 already has `install(DIRECTORY launch param maps rviz ...)`. So if we drop the BT XML into `src/.../param/`, colcon installs it automatically on next build. Then we uncomment the BT pointer in `src/.../slam_nav.yaml`. After one rebuild, src and install are perfectly synchronized — no drift, no surprise loss, and the existing recovery behavior is preserved exactly as-is.

**Tech Stack:** Bash, ROS 2 colcon, ament_cmake. Files live ONLY on the Jetson container — there is no laptop source-of-truth for `ugv_main` (it's a clone of Waveshare's stock repo on the Jetson). All edits happen via `docker exec` + `sed`/`tee`/`cp`.

**Out of scope:**
- Modifying the BT contents themselves (that's Plan B).
- Changing other YAMLs in `param/` (e.g., `rtabmap_*.yaml`).
- Adding a CI guard against future drift (separate idea — could be a `pre-build` check that errors if `gentle_recovery.xml` exists in `install/` but not `src/`).

---

### Task 1: Snapshot the current install-side state for rollback

**Files:**
- Backup target: `/home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml` (Jetson)
- Backup location: `/home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml.bak.20260427`

**Step 1: Confirm the file exists in install only**

Run on Jetson:

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "ls -la /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml; ls -la /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml 2>&1"'
```

Expected: install path lists OK, src path returns "No such file or directory".

**Step 2: Backup the install copy**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "cp /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml.bak.20260427 && ls -la /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml*"'
```

Expected: two files listed (current + .bak).

---

### Task 2: Copy the XML from install to src

**Step 1: Copy the file**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "cp /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml && ls -la /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml"'
```

Expected: file copied; size matches install copy.

**Step 2: Diff to verify byte-identical**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "diff /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml; echo exit=\$?"'
```

Expected: `exit=0` (no differences).

---

### Task 3: Uncomment the BT pointer in src `slam_nav.yaml`

**Files:**
- Modify: `/home/ws/ugv_ws/src/ugv_main/ugv_nav/param/slam_nav.yaml` line 72 (Jetson)

**Step 1: Read context around line 72**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "sed -n \"65,80p\" /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/slam_nav.yaml"'
```

Expected output should show a commented `# default_nav_to_pose_bt_xml:` line near line 72.

**Step 2: What the install copy says (line 70)**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "sed -n \"65,75p\" /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/slam_nav.yaml"'
```

Expected: line 70 is uncommented, with the absolute path to the install copy.

**Step 3: Apply the edit to src — replace the install path with `\$(find-pkg-share ugv_nav)/...` style relative path**

Use a Python heredoc to do an exact-match line replacement (sed across YAML is fragile):

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "python3 - <<\"PY\"
import re
path = \"/home/ws/ugv_ws/src/ugv_main/ugv_nav/param/slam_nav.yaml\"
with open(path) as f:
    text = f.read()

# Old line is commented; replacement is uncommented and points to the install path
# (Nav2 RewrittenYaml resolves absolute paths fine — keeping it absolute is simplest)
old_block = re.compile(r\"^\\s*# *default_nav_to_pose_bt_xml:.*$\", re.MULTILINE)
new_line = \"    default_nav_to_pose_bt_xml: /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml\"
new_text, n = old_block.subn(new_line, text, count=1)
assert n == 1, f\"Expected exactly 1 replacement, got {n}\"

with open(path, \"w\") as f:
    f.write(new_text)
print(f\"replaced {n} line\")
PY
"'
```

Expected: `replaced 1 line`.

**Step 4: Verify the edit**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "grep -n default_nav_to_pose_bt_xml /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/slam_nav.yaml"'
```

Expected: one uncommented match.

---

### Task 4: Rebuild ugv_nav and verify install matches src

**Step 1: Run colcon build for ugv_nav only**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "cd /home/ws/ugv_ws && source /opt/ros/humble/setup.bash && colcon build --packages-select ugv_nav --symlink-install 2>&1 | tail -10"'
```

Expected: "Finished <<< ugv_nav".

**Step 2: Verify install/.../gentle_recovery.xml still matches src**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "diff /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml /home/ws/ugv_ws/src/ugv_main/ugv_nav/param/gentle_recovery.xml; echo exit=\$?"'
```

Expected: `exit=0`.

**Step 3: Verify install yaml has the BT pointer**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "grep -n default_nav_to_pose_bt_xml /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/slam_nav.yaml"'
```

Expected: an uncommented match (probably more than one due to the .pre-nvblox / .orig backup files).

---

### Task 5: Restart Nav2 stack and verify BT loads

**Step 1: Restart the Nav2 launch**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "supervisorctl restart bringup_lidar 2>&1 | tail -3 || pkill -f bringup_lidar.launch.py"'
```

Wait ~20 s for lifecycle nodes to come back up.

**Step 2: Verify bt_navigator picked up the custom XML**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "source /opt/ros/humble/setup.bash; ros2 param get /bt_navigator default_nav_to_pose_bt_xml 2>&1 | tail -1"'
```

Expected: `String value is: /home/ws/ugv_ws/install/ugv_nav/share/ugv_nav/param/gentle_recovery.xml`

**Step 3: Confirm Nav2 is operational**

```bash
sshpass -p 'jetson' ssh jetson@192.168.1.155 'docker exec ugv_waveshare bash -lc "source /opt/ros/humble/setup.bash; ros2 node list | grep -E \"bt_navigator|controller_server|behavior_server\""'
```

Expected: all three nodes listed.

---

### Task 6: Snapshot + memory

**Step 1: Mint a snapshot**

Capture: drift discovered, src lacked XML + yaml comment, fix copied XML to src + uncommented yaml + colcon-built. No behavioral change to the BT itself — only persistence.

**Step 2: Add memory entry — drift footgun**

```yaml
name: ugv_nav BT XML drift footgun
description: gentle_recovery.xml lived only in install/ before 2026-04-27; next colcon build would have wiped the custom recovery silently
type: project
```

Body: explain that any custom file installed only into install/ but not in src/ is fragile because colcon regenerates install/ from src/. Lesson: any artifact Nav2 references (BT XML, custom plugin params) must live in src/.../param/ or src/.../config/ and be picked up by `install(DIRECTORY ...)` in CMakeLists.txt.

---

## Remember
- **No XML changes in this plan.** This plan ONLY persists the existing file. Plan B (about-face spins) is the next plan.
- **One restart only**: after Task 4. Do not restart between steps.
- **Backup first** (Task 1): if anything goes sideways, `cp gentle_recovery.xml.bak.20260427 gentle_recovery.xml` restores instantly.
- **Confirm exit=0 on the diffs** at Task 2 step 2 and Task 4 step 2 before proceeding.
