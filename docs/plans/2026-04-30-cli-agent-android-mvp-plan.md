# CLI Agent — Android MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship an interactive in-app terminal in the AI BlackBox Android MVP that runs Claude Code on the mini-ITX host through a tmux-anchored, persistent WebSocket session.

**Architecture:** FastAPI WebSocket route in the existing Orchestrator allocates a Python `ptyprocess` PTY around `tmux attach`/`new-session`, forwarding raw bytes both directions to a native Termux `TerminalView` rendered in Compose. tmux gives process persistence across mobile disconnects; per-operator `CLAUDE_CONFIG_DIR` isolates conversation history; path validator confines all sessions to `Apps/`.

**Tech Stack:**
- **Backend:** Python 3.12 (existing Orchestrator venv), FastAPI WebSocket, `ptyprocess`, `tmux` (host), `pytest`, `pytest-asyncio`
- **Android:** Kotlin/Compose, `com.termux:terminal-emulator:0.118` (LGPL dynamic-linked), OkHttp WebSocket, MediaRecorder for Whisper capture
- **Source of architectural truth:** `docs/plans/2026-04-30-cli-agent-android-mvp-design.md` (do not redesign — only execute)

**Pre-flight gaps confirmed during planning:**
- `tmux` not installed on the host → Phase 0 installs it
- `ptyprocess` not in `Orchestrator/venv/` → Phase 0 installs it

---

## Parallelization Map

The work has three independent tracks once Phase 0 lands:

```
Phase 0: pre-flight ─────────────────────────────────────┐
                                                          │
              ┌──── Phase 1: Backend foundation ─────────┤
              │     (path_validator → operator_config →  │
              │      session_manager → pty_bridge)       │
              │                                          │
              ├──── Phase 2: Backend route (depends on 1)│
              │                                          │
              ├──── Phase 3: Backend cron reaper         │
              │     (parallel to Phase 4-7 Android work) │
              │                                          │
              └──── Phase 4: Android prep ───────────────┤
                       │                                  │
                       ├── Phase 5: data layer (parallel)│
                       ├── Phase 6: UI components (3-way │
                       │   parallel: bar, mic, picker)   │
                       │                                  │
                       └── Phase 7: terminal screen      │
                            (depends on 5+6)              │
                                                          │
                          Phase 8: Tools menu wiring     │
                          Phase 9: end-to-end matrix     │
                          Phase 10: snapshot-dev mint    │
```

**Subagent dispatch opportunities** (flagged per task with `[parallelizable]`):
- Phase 1 path_validator + operator_config tests (2 agents)
- Phase 6 ExtraKeysBar + WhisperMicButton + AppFolderPicker (3 agents)
- Phase 1 backend tests vs Phase 4 Android prep (2 agents after Phase 0)

---

## Phase 0 — Pre-flight & Recovery Checkpoint

### Task 0.1: Mint pre-implementation snapshot

**Why:** "Go back to before CLI Agent" is one BlackBox search away if anything goes sideways. Costs 30 seconds, free insurance.

**Step 1: Save context message via /chat/save with auto-mint**

Run:
```bash
curl -sS -X POST http://localhost:9091/chat/save \
  -H "Content-Type: application/json" \
  -d '{
    "operator": "Brandon",
    "user_message": "Pre-implementation checkpoint: about to start CLI Agent Android MVP per docs/plans/2026-04-30-cli-agent-android-mvp-design.md. Tagging fossil so we can restore context if implementation goes sideways.",
    "assistant_message": "Snapshotted as recovery point before CLI Agent build. Design locked in design doc; plan locked in plan doc. Proceeding to Phase 0 pre-flight (tmux install, ptyprocess install, Android branch creation).",
    "turns_threshold": 1
  }'
```

Expected: 200 with `snap_id` in response body.

**Step 2: Verify embedding generated**

Run: `journalctl -u blackbox.service -n 30 --no-pager | grep -i embedding | tail -3`
Expected: line containing `[EMBEDDING] Successfully generated embedding (3072 dimensions)`

If "Failed" appears, flag to the user before continuing.

---

### Task 0.2: Install tmux on the host

**Step 1: Confirm not installed**

Run: `which tmux ; echo $?`
Expected: empty stdout, exit 1.

**Step 2: Install**

Run: `sudo apt-get install -y tmux`
Expected: tmux package installed.

**Step 3: Verify version**

Run: `tmux -V`
Expected: `tmux 3.x` (any 3.x version is fine).

**Step 4: Smoke test**

Run: `tmux new-session -d -s smoketest 'echo hi; sleep 1' && sleep 2 && tmux has-session -t smoketest 2>&1 ; tmux kill-session -t smoketest 2>/dev/null ; echo cleanup_ok`
Expected: `cleanup_ok` printed (session was created and killed cleanly).

---

### Task 0.3: Install ptyprocess in Orchestrator venv

**Step 1: Install**

Run:
```bash
/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/venv/bin/pip install ptyprocess
```

Expected: "Successfully installed ptyprocess-0.7.x"

**Step 2: Verify**

Run:
```bash
/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/venv/bin/python -c "import ptyprocess; print(ptyprocess.__version__)"
```

Expected: version string like `0.7.0`.

**Step 3: Add to requirements (idempotent)**

Modify: `Orchestrator/requirements.txt` — append line `ptyprocess>=0.7.0` if not present.

---

### Task 0.4: Create Android `cli-agent` branch

**Step 1: Switch into Android repo**

Run: `cd "/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal" && git status --short`
Expected: only `.gradle/...` lock files dirty (no real code changes).

**Step 2: Stash gradle locks if present, branch off master**

Run:
```bash
git stash --include-untracked --keep-index 2>/dev/null
git checkout -b cli-agent
git status
```
Expected: "Switched to a new branch 'cli-agent'", clean tree.

---

## Phase 1 — Backend Foundation

All Phase 1 modules live in `Orchestrator/cli_agent/`. They build the spine that the WebSocket route in Phase 2 will use.

### Task 1.1: Create `cli_agent/` package skeleton

**Files:**
- Create: `Orchestrator/cli_agent/__init__.py` (empty)
- Create: `Orchestrator/tests/__init__.py` (only if missing)
- Create: `Orchestrator/tests/test_cli_agent/__init__.py` (empty)

**Step 1:** `mkdir -p Orchestrator/cli_agent Orchestrator/tests/test_cli_agent && touch Orchestrator/cli_agent/__init__.py Orchestrator/tests/test_cli_agent/__init__.py`

**Step 2:** Confirm `Orchestrator/tests/__init__.py` exists. If not, create empty.

---

### Task 1.2: `path_validator.py` — TDD `[parallelizable with 1.3]`

**Files:**
- Create: `Orchestrator/cli_agent/path_validator.py`
- Test: `Orchestrator/tests/test_cli_agent/test_path_validator.py`

**Step 1: Write the failing tests**

```python
# Orchestrator/tests/test_cli_agent/test_path_validator.py
import os
import tempfile
from pathlib import Path
import pytest
from Orchestrator.cli_agent.path_validator import PathValidator, WorkspaceViolation


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    apps = tmp_path / "Apps"
    (apps / "grocery-store").mkdir(parents=True)
    (apps / "PelvicVibeAndroid").mkdir()
    monkeypatch.setenv("CLI_AGENT_APPS_ROOT", str(apps))
    return PathValidator(apps_root=apps)


def test_validates_real_app_subdir(workspace):
    assert workspace.validate("grocery-store").name == "grocery-store"


def test_apps_root_pickable_via_empty_string(workspace):
    assert workspace.validate("") == workspace.apps_root


def test_apps_root_pickable_via_dot(workspace):
    assert workspace.validate(".") == workspace.apps_root


def test_apps_root_pickable_via_slash(workspace):
    assert workspace.validate("/") == workspace.apps_root


def test_rejects_traversal(workspace):
    with pytest.raises(WorkspaceViolation):
        workspace.validate("../../etc")


def test_rejects_absolute_path_outside(workspace):
    with pytest.raises(WorkspaceViolation):
        workspace.validate("/etc/passwd")


def test_rejects_nonexistent_dir(workspace):
    with pytest.raises(WorkspaceViolation):
        workspace.validate("does-not-exist")


def test_rejects_symlink_escaping_apps(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.apps_root / "evil-link").symlink_to(outside)
    with pytest.raises(WorkspaceViolation):
        workspace.validate("evil-link")
```

**Step 2: Run, verify failure**

Run: `cd Orchestrator && venv/bin/pytest tests/test_cli_agent/test_path_validator.py -v`
Expected: FAILS — module does not exist.

**Step 3: Implement minimal**

```python
# Orchestrator/cli_agent/path_validator.py
from pathlib import Path
from typing import Optional


class WorkspaceViolation(PermissionError):
    """Raised when a requested path escapes the Apps/ workspace."""


class PathValidator:
    def __init__(self, apps_root: Path):
        self.apps_root = Path(apps_root).resolve(strict=True)

    def validate(self, requested: str) -> Path:
        if requested in ("", "/", "."):
            return self.apps_root
        candidate = (self.apps_root / requested).resolve()
        try:
            candidate.relative_to(self.apps_root)
        except ValueError:
            raise WorkspaceViolation(f"{requested} is outside Apps/") from None
        if not candidate.is_dir():
            raise WorkspaceViolation(f"{requested} is not a directory in Apps/")
        return candidate
```

**Step 4: Run tests to verify pass**

Run: `cd Orchestrator && venv/bin/pytest tests/test_cli_agent/test_path_validator.py -v`
Expected: 8/8 PASS.

**Step 5: Manual sanity log** — append note to `docs/plans/2026-04-30-cli-agent-android-mvp-plan.md` execution log: "Task 1.2 complete, all 8 path validator tests green."

---

### Task 1.3: `operator_config.py` — TDD `[parallelizable with 1.2]`

**Files:**
- Create: `Orchestrator/cli_agent/operator_config.py`
- Test: `Orchestrator/tests/test_cli_agent/test_operator_config.py`

**Step 1: Write the failing tests**

```python
# Orchestrator/tests/test_cli_agent/test_operator_config.py
from pathlib import Path
import pytest
from Orchestrator.cli_agent.operator_config import OperatorConfig, InvalidOperator


@pytest.fixture
def cfg_root(tmp_path):
    return tmp_path / ".claude-bbox"


def test_creates_config_dir_for_new_operator(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    path = oc.ensure_dir("Brandon")
    assert path.exists() and path.is_dir()
    assert path == cfg_root / "Brandon"


def test_idempotent(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    oc.ensure_dir("Brandon")
    oc.ensure_dir("Brandon")  # second call must not raise
    assert (cfg_root / "Brandon").is_dir()


def test_isolates_two_operators(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    p1 = oc.ensure_dir("Brandon")
    p2 = oc.ensure_dir("Anna")
    assert p1 != p2
    assert p1.exists() and p2.exists()


def test_rejects_path_traversal_in_operator_name(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    with pytest.raises(InvalidOperator):
        oc.ensure_dir("../escape")


def test_rejects_empty_operator(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    with pytest.raises(InvalidOperator):
        oc.ensure_dir("")


def test_env_dict_returns_claude_config_dir(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    env = oc.env_for("Brandon")
    assert env["CLAUDE_CONFIG_DIR"] == str(cfg_root / "Brandon")
```

**Step 2: Run, verify failure** — `pytest tests/test_cli_agent/test_operator_config.py -v` → FAIL.

**Step 3: Implement minimal**

```python
# Orchestrator/cli_agent/operator_config.py
import re
from pathlib import Path


_OPERATOR_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidOperator(ValueError):
    """Raised when an operator name is empty or contains invalid characters."""


class OperatorConfig:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _check(self, operator: str) -> None:
        if not operator or not _OPERATOR_RE.match(operator):
            raise InvalidOperator(f"Invalid operator name: {operator!r}")

    def ensure_dir(self, operator: str) -> Path:
        self._check(operator)
        path = self.root / operator
        path.mkdir(parents=True, exist_ok=True)
        return path

    def env_for(self, operator: str) -> dict:
        path = self.ensure_dir(operator)
        return {"CLAUDE_CONFIG_DIR": str(path)}
```

**Step 4: Run tests to verify pass** — 6/6 PASS.

**Step 5: Log** — append "Task 1.3 complete."

---

### Task 1.4: `session_manager.py` — TDD

**Depends on:** 1.2, 1.3.

**Files:**
- Create: `Orchestrator/cli_agent/session_manager.py`
- Test: `Orchestrator/tests/test_cli_agent/test_session_manager.py`

**Step 1: Write the failing tests** (using a real tmux server in a test-isolated socket dir)

```python
# Orchestrator/tests/test_cli_agent/test_session_manager.py
import os
import subprocess
import time
from pathlib import Path
import pytest
from Orchestrator.cli_agent.session_manager import (
    TmuxSessionManager, session_name, parse_session_name
)
from Orchestrator.cli_agent.path_validator import PathValidator
from Orchestrator.cli_agent.operator_config import OperatorConfig


TMUX_TMP_SOCK = Path("/tmp/test-cli-agent-tmux.sock")


@pytest.fixture
def workspace(tmp_path):
    apps = tmp_path / "Apps"
    (apps / "grocery-store").mkdir(parents=True)
    return apps


@pytest.fixture
def manager(workspace, tmp_path):
    if TMUX_TMP_SOCK.exists():
        subprocess.run(["tmux", "-S", str(TMUX_TMP_SOCK), "kill-server"],
                       capture_output=True)
        TMUX_TMP_SOCK.unlink(missing_ok=True)
    pv = PathValidator(apps_root=workspace)
    oc = OperatorConfig(root=tmp_path / ".claude-bbox")
    mgr = TmuxSessionManager(
        path_validator=pv, operator_config=oc, tmux_socket=TMUX_TMP_SOCK
    )
    yield mgr
    subprocess.run(["tmux", "-S", str(TMUX_TMP_SOCK), "kill-server"],
                   capture_output=True)


def test_session_name_deterministic():
    n1 = session_name("Brandon", "claude", "grocery-store")
    n2 = session_name("Brandon", "claude", "grocery-store")
    assert n1 == n2 == "cli-agent-Brandon-claude-grocery-store"


def test_session_name_apps_root_uses_underscore_root():
    assert session_name("Brandon", "claude", "") == "cli-agent-Brandon-claude-_root"


def test_parse_session_name_round_trips():
    name = "cli-agent-Brandon-claude-grocery-store"
    op, prov, app = parse_session_name(name)
    assert (op, prov, app) == ("Brandon", "claude", "grocery-store")


def test_attach_or_create_creates_new(manager, workspace):
    info = manager.attach_or_create(
        operator="Brandon", provider="claude", app="grocery-store",
        command=["bash", "-c", "echo hello; sleep 600"]
    )
    assert info.created is True
    assert info.name == "cli-agent-Brandon-claude-grocery-store"
    # verify it exists
    out = subprocess.run(
        ["tmux", "-S", str(TMUX_TMP_SOCK), "has-session", "-t", info.name],
        capture_output=True
    )
    assert out.returncode == 0


def test_attach_or_create_idempotent(manager, workspace):
    a = manager.attach_or_create(
        operator="Brandon", provider="claude", app="grocery-store",
        command=["bash", "-c", "sleep 600"]
    )
    b = manager.attach_or_create(
        operator="Brandon", provider="claude", app="grocery-store",
        command=["bash", "-c", "sleep 600"]
    )
    assert a.name == b.name
    assert a.created is True
    assert b.created is False  # second call attached, didn't recreate


def test_kill_session_works(manager, workspace):
    manager.attach_or_create(
        operator="Brandon", provider="claude", app="grocery-store",
        command=["bash", "-c", "sleep 600"]
    )
    name = "cli-agent-Brandon-claude-grocery-store"
    manager.kill(name)
    out = subprocess.run(
        ["tmux", "-S", str(TMUX_TMP_SOCK), "has-session", "-t", name],
        capture_output=True
    )
    assert out.returncode != 0


def test_list_sessions_for_operator(manager, workspace):
    manager.attach_or_create(operator="Brandon", provider="claude",
                              app="grocery-store",
                              command=["bash", "-c", "sleep 600"])
    sessions = manager.list_for_operator("Brandon")
    names = [s.name for s in sessions]
    assert "cli-agent-Brandon-claude-grocery-store" in names


def test_path_violation_rejects_before_tmux(manager):
    from Orchestrator.cli_agent.path_validator import WorkspaceViolation
    with pytest.raises(WorkspaceViolation):
        manager.attach_or_create(
            operator="Brandon", provider="claude", app="../../etc",
            command=["bash"]
        )
```

**Step 2: Run, verify failure** — module not found.

**Step 3: Implement minimal**

```python
# Orchestrator/cli_agent/session_manager.py
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from .path_validator import PathValidator
from .operator_config import OperatorConfig


_APPS_ROOT_SLUG = "_root"


def session_name(operator: str, provider: str, app: str) -> str:
    slug = app if app else _APPS_ROOT_SLUG
    return f"cli-agent-{operator}-{provider}-{slug}"


def parse_session_name(name: str) -> tuple:
    if not name.startswith("cli-agent-"):
        raise ValueError(f"Not a CLI Agent session name: {name}")
    parts = name[len("cli-agent-"):].split("-", 2)
    if len(parts) != 3:
        raise ValueError(f"Malformed session name: {name}")
    op, prov, app = parts
    if app == _APPS_ROOT_SLUG:
        app = ""
    return op, prov, app


@dataclass
class SessionInfo:
    name: str
    created: bool
    cwd: Path


class TmuxSessionManager:
    def __init__(self, path_validator: PathValidator,
                 operator_config: OperatorConfig,
                 tmux_socket: Optional[Path] = None):
        self.pv = path_validator
        self.oc = operator_config
        self.socket = tmux_socket

    def _tmux(self, *args) -> subprocess.CompletedProcess:
        cmd = ["tmux"]
        if self.socket:
            cmd += ["-S", str(self.socket)]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def has_session(self, name: str) -> bool:
        return self._tmux("has-session", "-t", name).returncode == 0

    def attach_or_create(self, *, operator: str, provider: str,
                          app: str, command: Sequence[str]) -> SessionInfo:
        cwd = self.pv.validate(app)  # may raise WorkspaceViolation
        env = self.oc.env_for(operator)
        name = session_name(operator, provider, app)
        if self.has_session(name):
            return SessionInfo(name=name, created=False, cwd=cwd)
        full_env = {**self._inherit_env(), **env}
        result = subprocess.run(
            self._tmux_cmd("new-session", "-d", "-s", name, "-c", str(cwd),
                            *command),
            capture_output=True, text=True, env=full_env
        )
        if result.returncode != 0:
            raise RuntimeError(f"tmux new-session failed: {result.stderr}")
        return SessionInfo(name=name, created=True, cwd=cwd)

    def kill(self, name: str) -> None:
        self._tmux("kill-session", "-t", name)

    def list_for_operator(self, operator: str) -> List[SessionInfo]:
        prefix = f"cli-agent-{operator}-"
        result = self._tmux("list-sessions", "-F", "#{session_name}")
        if result.returncode != 0:
            return []
        sessions = []
        for line in result.stdout.splitlines():
            if line.startswith(prefix):
                op, prov, app = parse_session_name(line)
                cwd = self.pv.apps_root if app == "" else self.pv.apps_root / app
                sessions.append(SessionInfo(name=line, created=False, cwd=cwd))
        return sessions

    def _tmux_cmd(self, *args) -> List[str]:
        cmd = ["tmux"]
        if self.socket:
            cmd += ["-S", str(self.socket)]
        return cmd + list(args)

    def _inherit_env(self) -> dict:
        import os
        return dict(os.environ)
```

**Step 4: Run tests** — 8/8 PASS.

**Step 5: Log** — "Task 1.4 complete."

---

### Task 1.5: `pty_bridge.py` — TDD

**Depends on:** 1.4.

**Files:**
- Create: `Orchestrator/cli_agent/pty_bridge.py`
- Test: `Orchestrator/tests/test_cli_agent/test_pty_bridge.py`

**Step 1: Write the failing tests**

```python
# Orchestrator/tests/test_cli_agent/test_pty_bridge.py
import asyncio
import pytest
from Orchestrator.cli_agent.pty_bridge import PtyBridge


@pytest.mark.asyncio
async def test_echoes_typed_bytes():
    bridge = PtyBridge.spawn(["bash", "--noprofile", "--norc", "-c",
                              "while read line; do echo got:$line; done"])
    try:
        await bridge.write(b"hello\n")
        out = b""
        for _ in range(20):
            chunk = await bridge.read(timeout=0.5)
            if chunk:
                out += chunk
            if b"got:hello" in out:
                break
        assert b"got:hello" in out
    finally:
        bridge.close()


@pytest.mark.asyncio
async def test_resize_no_throw():
    bridge = PtyBridge.spawn(["bash", "--noprofile", "--norc", "-c",
                              "sleep 5"])
    try:
        bridge.resize(cols=120, rows=40)
    finally:
        bridge.close()


@pytest.mark.asyncio
async def test_close_terminates_child():
    bridge = PtyBridge.spawn(["bash", "--noprofile", "--norc", "-c",
                              "sleep 60"])
    bridge.close()
    # After close, isalive should be False within ~1 second
    await asyncio.sleep(1)
    assert not bridge.isalive()
```

**Step 2: Run, verify failure** — module not found.

**Step 3: Implement minimal**

```python
# Orchestrator/cli_agent/pty_bridge.py
import asyncio
import os
import ptyprocess
from typing import Optional, Sequence


class PtyBridge:
    def __init__(self, proc: ptyprocess.PtyProcess):
        self._proc = proc

    @classmethod
    def spawn(cls, command: Sequence[str], *,
              env: Optional[dict] = None,
              cwd: Optional[str] = None,
              cols: int = 80, rows: int = 24) -> "PtyBridge":
        proc = ptyprocess.PtyProcess.spawn(
            list(command),
            env=env or os.environ.copy(),
            cwd=cwd,
            dimensions=(rows, cols),
        )
        return cls(proc)

    async def read(self, *, timeout: float = 0.05,
                    chunk_size: int = 4096) -> bytes:
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._proc.read, chunk_size),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return b""
        except EOFError:
            return b""

    async def write(self, data: bytes) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._proc.write, data)

    def resize(self, *, cols: int, rows: int) -> None:
        try:
            self._proc.setwinsize(rows, cols)
        except Exception:
            pass

    def isalive(self) -> bool:
        return self._proc.isalive()

    def close(self) -> None:
        try:
            self._proc.close(force=True)
        except Exception:
            pass
```

**Step 4: Run tests** — 3/3 PASS.

**Step 5: Log** — "Task 1.5 complete."

---

### Task 1.6: Phase 1 commit gate

After 1.1–1.5 land, manually verify:

Run: `cd Orchestrator && venv/bin/pytest tests/test_cli_agent/ -v`
Expected: **all 25 tests pass** (8 path + 6 operator + 8 session + 3 pty).

Note: backend code lives in non-git directory. No `git commit` for backend. Append "Phase 1 complete — 25/25 tests green" to a note file `Orchestrator/cli_agent/STATUS.md`.

---

## Phase 2 — Backend Route

### Task 2.1: REST endpoints — TDD

**Files:**
- Create: `Orchestrator/routes/cli_agent_routes.py`
- Modify: `Orchestrator/app.py` — register the new router (find existing `app.include_router(...)` calls and add one for `cli_agent_routes.router`).
- Test: `Orchestrator/tests/test_cli_agent/test_cli_agent_routes.py`

**Step 1: Write the failing tests** (REST surface only — WebSocket comes in 2.2)

```python
# Orchestrator/tests/test_cli_agent/test_cli_agent_routes.py
import pytest
from fastapi.testclient import TestClient
# Import lazy to avoid pulling full app at collection time:
def _client():
    from Orchestrator.app import app
    return TestClient(app)


def test_list_sessions_empty_returns_empty_list():
    c = _client()
    r = c.get("/cli-agent/sessions", params={"op": "TestOp"})
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_kill_nonexistent_session_returns_404():
    c = _client()
    r = c.delete("/cli-agent/sessions/cli-agent-TestOp-claude-fake")
    assert r.status_code in (200, 404)  # tolerate idempotent variant
```

**Step 2: Run, verify failure**

Run: `cd Orchestrator && venv/bin/pytest tests/test_cli_agent/test_cli_agent_routes.py -v`
Expected: FAIL — endpoints don't exist.

**Step 3: Implement minimal route module**

```python
# Orchestrator/routes/cli_agent_routes.py
import asyncio
import json
import os
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from Orchestrator.cli_agent.operator_config import OperatorConfig
from Orchestrator.cli_agent.path_validator import PathValidator, WorkspaceViolation
from Orchestrator.cli_agent.session_manager import (
    TmuxSessionManager, session_name, parse_session_name,
)
from Orchestrator.cli_agent.pty_bridge import PtyBridge


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPS_ROOT = Path(os.getenv("CLI_AGENT_APPS_ROOT",
                           str(PROJECT_ROOT / "Apps")))
CLAUDE_CFG_ROOT = Path(os.getenv("CLI_AGENT_CONFIG_ROOT",
                                  str(Path.home() / ".claude-bbox")))
ALLOWED_OPS = set(filter(None,
    os.getenv("CLI_AGENT_OPERATORS", "").split(","))) or None  # None = any


def _manager() -> TmuxSessionManager:
    pv = PathValidator(apps_root=APPS_ROOT)
    oc = OperatorConfig(root=CLAUDE_CFG_ROOT)
    return TmuxSessionManager(path_validator=pv, operator_config=oc)


router = APIRouter(prefix="/cli-agent", tags=["cli-agent"])


@router.get("/sessions")
def list_sessions(op: str = Query(...)):
    if ALLOWED_OPS is not None and op not in ALLOWED_OPS:
        raise HTTPException(403, f"Operator {op} not allowed")
    sessions = _manager().list_for_operator(op)
    return {"sessions": [
        {"session_id": s.name, "cwd": str(s.cwd)} for s in sessions
    ]}


@router.delete("/sessions/{session_id}")
def kill_session(session_id: str):
    if not session_id.startswith("cli-agent-"):
        raise HTTPException(400, "Invalid session id")
    mgr = _manager()
    if not mgr.has_session(session_id):
        return {"killed": False, "reason": "not-found"}
    mgr.kill(session_id)
    return {"killed": True}


@router.websocket("/ws/{session_id}")
async def ws_cli_agent(
    websocket: WebSocket,
    session_id: str,
    op: str,
    provider: str,
    app: str,
    cols: int = 80,
    rows: int = 24,
):
    if ALLOWED_OPS is not None and op not in ALLOWED_OPS:
        await websocket.close(code=4003)
        return

    expected = session_name(op, provider, app)
    if session_id != expected:
        await websocket.close(code=4003)
        return

    mgr = _manager()
    try:
        info = mgr.attach_or_create(
            operator=op, provider=provider, app=app,
            command=["claude"],  # spawned inside tmux's wrapper
        )
    except WorkspaceViolation as e:
        await websocket.close(code=4003)
        return

    await websocket.accept()
    await websocket.send_text(json.dumps({
        "type": "session_info",
        "state": "created" if info.created else "attaching",
    }))

    env = {**os.environ, **OperatorConfig(root=CLAUDE_CFG_ROOT).env_for(op)}
    bridge = PtyBridge.spawn(
        ["tmux", "attach", "-t", session_id],
        env=env, cols=cols, rows=rows,
    )

    async def pty_to_ws():
        while bridge.isalive():
            data = await bridge.read(timeout=0.1)
            if data:
                await websocket.send_bytes(data)

    async def ws_to_pty():
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            if "bytes" in msg and msg["bytes"] is not None:
                await bridge.write(msg["bytes"])
            elif "text" in msg and msg["text"]:
                try:
                    ctrl = json.loads(msg["text"])
                except Exception:
                    continue
                t = ctrl.get("type")
                if t == "resize":
                    bridge.resize(cols=int(ctrl["cols"]), rows=int(ctrl["rows"]))
                elif t == "paste":
                    framed = b"\x1b[200~" + ctrl["text"].encode("utf-8") + b"\x1b[201~"
                    await bridge.write(framed)
                elif t == "kill":
                    mgr.kill(session_id)
                    return

    try:
        await asyncio.gather(pty_to_ws(), ws_to_pty())
    except WebSocketDisconnect:
        pass
    finally:
        bridge.close()
```

**Step 4: Register router in `app.py`**

Modify `Orchestrator/app.py` — locate the block where other routers are included (search for `include_router(`). Add:
```python
from Orchestrator.routes import cli_agent_routes
app.include_router(cli_agent_routes.router)
```

**Step 5: Run tests** — 2/2 PASS.

**Step 6: Log** — "Task 2.1 complete; REST surface ships."

---

### Task 2.2: Manual WebSocket smoke test

**Step 1: Restart the service**

Run: `sudo systemctl restart blackbox.service && sleep 90`
Expected: service comes up healthy. (60-90s warm-up is normal per project notes.)

**Step 2: Verify route registered**

Run: `curl -s http://localhost:9091/openapi.json | python3 -c "import json,sys; data=json.load(sys.stdin); print([p for p in data['paths'] if 'cli-agent' in p])"`
Expected: `['/cli-agent/sessions', '/cli-agent/sessions/{session_id}', '/cli-agent/ws/{session_id}']`

**Step 3: REST list-sessions sanity**

Run: `curl -s http://localhost:9091/cli-agent/sessions?op=Brandon`
Expected: `{"sessions": []}` (or list of any pre-existing sessions).

**Step 4: WebSocket smoke with `wscat`**

Install if missing: `sudo apt-get install -y node-ws-tools 2>/dev/null || npm install -g wscat`

Run (in one terminal):
```bash
APP=grocery-store
SID="cli-agent-Brandon-claude-${APP}"
wscat -c "ws://localhost:9091/cli-agent/ws/${SID}?op=Brandon&provider=claude&app=${APP}&cols=80&rows=24"
```
Expected: Initial JSON `{"type":"session_info","state":"created"}` then binary frames carrying the Claude Code TUI rendering.

**Step 5: Type and verify**

Type `hello` in wscat and hit Enter. Expected: bytes echo back, Claude Code reacts.

**Step 6: Verify tmux session persistence**

Disconnect wscat (Ctrl-C). In another terminal: `tmux ls | grep cli-agent`. Expected: session still running.

**Step 7: Reconnect and verify scrollback**

Reconnect with same wscat command. Expected: session reattaches, prior scrollback re-rendered.

**Step 8: Cleanup**

Run: `tmux kill-session -t cli-agent-Brandon-claude-grocery-store`

**If any step fails:** flag, do not proceed to Phase 3.

---

## Phase 3 — Idle Reaper Cron `[parallelizable with Phase 4-7]`

### Task 3.1: Reaper script

**Files:**
- Create: `Orchestrator/cli_agent/reaper.py`
- Test: `Orchestrator/tests/test_cli_agent/test_reaper.py`

**Step 1: Write the failing tests**

```python
# Orchestrator/tests/test_cli_agent/test_reaper.py
import time
from unittest.mock import MagicMock
from Orchestrator.cli_agent.reaper import reap_idle_sessions


def test_reaps_sessions_idle_beyond_threshold():
    mgr = MagicMock()
    now = int(time.time())
    mgr._tmux.return_value.stdout = (
        f"cli-agent-Brandon-claude-grocery-store {now - 8 * 86400}\n"
        f"cli-agent-Brandon-claude-fresh {now - 3600}\n"
    )
    mgr._tmux.return_value.returncode = 0
    killed = reap_idle_sessions(mgr, idle_seconds=7 * 86400)
    assert killed == ["cli-agent-Brandon-claude-grocery-store"]
    mgr.kill.assert_called_once_with("cli-agent-Brandon-claude-grocery-store")


def test_skips_non_cli_agent_sessions():
    mgr = MagicMock()
    now = int(time.time())
    mgr._tmux.return_value.stdout = (
        f"some-other-session {now - 99 * 86400}\n"
    )
    mgr._tmux.return_value.returncode = 0
    killed = reap_idle_sessions(mgr, idle_seconds=7 * 86400)
    assert killed == []
    mgr.kill.assert_not_called()
```

**Step 2: Run, FAIL.**

**Step 3: Implement**

```python
# Orchestrator/cli_agent/reaper.py
import time
from typing import List


def reap_idle_sessions(manager, idle_seconds: int = 7 * 86400) -> List[str]:
    res = manager._tmux("list-sessions",
                         "-F", "#{session_name} #{session_activity}")
    if res.returncode != 0:
        return []
    cutoff = int(time.time()) - idle_seconds
    killed = []
    for line in res.stdout.splitlines():
        try:
            name, activity = line.rsplit(" ", 1)
            activity = int(activity)
        except (ValueError, IndexError):
            continue
        if not name.startswith("cli-agent-"):
            continue
        if activity < cutoff:
            manager.kill(name)
            killed.append(name)
    return killed
```

**Step 4: Run, PASS.**

---

### Task 3.2: Cron registration

**Step 1: Register reaper as a daily BlackBox cron** via the existing `/cron/jobs` REST API.

Run:
```bash
curl -X POST http://localhost:9091/cron/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CLI Agent Idle Reaper",
    "operator": "system",
    "schedule": "0 4 * * *",
    "command_type": "python",
    "command": "from Orchestrator.cli_agent.reaper import reap_idle_sessions; from Orchestrator.cli_agent.session_manager import TmuxSessionManager; from Orchestrator.cli_agent.path_validator import PathValidator; from Orchestrator.cli_agent.operator_config import OperatorConfig; from pathlib import Path; import os; mgr = TmuxSessionManager(path_validator=PathValidator(apps_root=Path(os.getenv(\"CLI_AGENT_APPS_ROOT\"))), operator_config=OperatorConfig(root=Path.home() / \".claude-bbox\")); print(reap_idle_sessions(mgr))",
    "enabled": true
  }'
```

Expected: 200 + cron job id.

**Step 2: Verify schedule**

Run: `curl -s http://localhost:9091/cron/jobs | python3 -c "import json,sys; print([j for j in json.load(sys.stdin) if 'Reaper' in j['name']])"`
Expected: one job present, enabled, scheduled `0 4 * * *`.

---

## Phase 4 — Android Prep

### Task 4.1: Add Termux dependency

**Files:**
- Modify: `AI_BlackBox_Portal/app/build.gradle.kts` (or `.gradle` — whichever exists)

**Step 1: Add dependency block**

Append to `dependencies { ... }`:
```kotlin
implementation("com.termux:terminal-emulator:0.118")
```

If Maven repo `com.termux` is not declared, add to `settings.gradle.kts` (root) under `dependencyResolutionManagement.repositories`:
```kotlin
maven { url = uri("https://androidx.dev/storage/compose-compiler/repository/") }
maven { url = uri("https://jitpack.io") }
```

(The Termux package is available on JitPack via `com.github.termux:terminal-emulator` if the direct Maven coordinate doesn't resolve. The plan executor MUST verify which coordinate works during this step and document the chosen one.)

**Step 2: Sync gradle**

Run: `cd <android-project-root> && ./gradlew --refresh-dependencies app:dependencies | grep -i terminal-emulator`
Expected: dependency resolved.

**Step 3: Commit**

Run: `git add app/build.gradle.kts settings.gradle.kts && git commit -m "feat(cli-agent): add Termux terminal-emulator dep"`

---

### Task 4.2: Source layout

**Files (create empty .kt stubs to prepare for parallel work):**
```
app/src/main/.../ui/cli_agent/
├── CliAgentScreen.kt
├── AppFolderPicker.kt
├── TerminalScreen.kt
├── ExtraKeysBar.kt
├── WhisperMicButton.kt
├── CliAgentWebSocket.kt
├── CliAgentSessionRepository.kt
└── CliAgentToolsButton.kt
```

Each file gets a one-line stub `package <existing>.ui.cli_agent` so the Kotlin compiler picks them up.

**Step 1:** Create files with package declarations only.
**Step 2:** Verify `./gradlew assembleDebug` still compiles green.
**Step 3:** Commit `feat(cli-agent): scaffold ui/cli_agent module stubs`.

---

## Phase 5 — Android Data Layer `[2 parallelizable tasks]`

### Task 5.1: `CliAgentSessionRepository`

**Files:**
- Modify: `app/src/main/.../ui/cli_agent/CliAgentSessionRepository.kt`

**Step 1: Implement**

```kotlin
package <pkg>.ui.cli_agent

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.*

@Serializable
data class AppEntry(val name: String, val directory: String, val port: Int? = null)

@Serializable
data class SessionEntry(val session_id: String, val cwd: String)

class CliAgentSessionRepository(
    private val client: OkHttpClient,
    private val baseUrl: String,
) {
    suspend fun listApps(): List<AppEntry> { /* GET /agent/apps, filter Apps/ subdir */ }
    suspend fun listSessions(operator: String): List<SessionEntry> { /* GET /cli-agent/sessions?op=*/ }
    suspend fun killSession(sessionId: String): Boolean { /* DELETE /cli-agent/sessions/{id} */ }
}
```

(Full implementation: standard Retrofit-or-OkHttp+kotlinx.serialization. See existing repository patterns in the Android project — match their idiom.)

**Step 2:** Compile.
**Step 3:** Commit `feat(cli-agent): apps + sessions repository`.

---

### Task 5.2: `CliAgentWebSocket` `[parallelizable with 5.1]`

**Files:**
- Modify: `app/src/main/.../ui/cli_agent/CliAgentWebSocket.kt`

**Step 1: Implement using OkHttp WebSocket with binary + text frame handling, exponential backoff reconnect (250→500→1000→2000ms cap).**

Key API:
```kotlin
class CliAgentWebSocket(
    private val baseUrl: String,
    private val sessionId: String,
    params: Map<String, String>,
    private val callbacks: Callbacks,
) {
    interface Callbacks {
        fun onOpen()
        fun onBytes(bytes: ByteArray)
        fun onControl(message: JsonObject)
        fun onClosed(code: Int, reason: String)
        fun onReconnecting()
    }
    fun connect()
    fun sendBytes(bytes: ByteArray)
    fun sendResize(cols: Int, rows: Int)
    fun sendPaste(text: String)
    fun close()
}
```

**Step 2:** Unit-test with a mock server (use OkHttp `MockWebServer.enqueue(MockResponse().withWebSocketUpgrade(...))`).

**Step 3:** Commit `feat(cli-agent): WebSocket client with reconnect`.

---

## Phase 6 — Android UI Components `[3 parallelizable tasks]`

### Task 6.1: `ExtraKeysBar` `[parallelizable]`

Composable rendering a horizontal row with `Esc | Tab | Ctrl | Alt | ← ↓ ↑ → | / | @ | 🎤 | -`. Tap dispatches a `KeyEvent`. Long-press on Ctrl/Alt sets sticky modifier.

**Step 1:** Implement.
**Step 2:** Compose preview.
**Step 3:** Commit.

---

### Task 6.2: `WhisperMicButton` `[parallelizable]`

State machine `idle → recording → transcribing → idle` driving `MediaRecorder`. POSTs blob to existing `/api/whisper`. Surfaces transcript via `onTranscript: (String) -> Unit` callback.

**Step 1:** Implement.
**Step 2:** Manual record-and-transcribe smoke (against running BlackBox).
**Step 3:** Commit.

---

### Task 6.3: `AppFolderPicker` `[parallelizable]`

LazyColumn rendering registered apps. Special "✦ + New app workspace" row at top. Live-dot indicator from `listSessions()` cross-reference. Long-press → action sheet with Reconnect/Kill.

**Step 1:** Implement.
**Step 2:** Compose preview with mock data.
**Step 3:** Commit.

---

## Phase 7 — Terminal Screen Integration

### Task 7.1: `TerminalScreen` Compose host

**Depends on:** 5.2 (WebSocket), 6.1 (bar), 6.2 (mic), Termux `TerminalView`.

Wraps `TerminalView` via `AndroidView`. Wires:
- WebSocket bytes → `term.write(bytes)`
- `TerminalView` keypress → `ws.sendBytes(bytes)`
- `term.onResize` → `ws.sendResize(cols, rows)`
- ExtraKeysBar dispatch → synthetic key events into the emulator
- WhisperMicButton transcript → `ws.sendPaste(text)`
- Reconnect banner overlay during gaps

**Step 1:** Implement.
**Step 2:** Manual: launch on physical Android device pointed at a running BlackBox; see Claude Code render.
**Step 3:** Commit.

---

### Task 7.2: `CliAgentScreen` NavHost destination + `CliAgentToolsButton`

Adds the screen entry to navigation graph; adds the new button to the existing Tools menu module (mirror Computer Use button code path). Removes (or marks deprecated) headless CLI provider chat entries per the design doc decision Q4.A.

**Step 1:** Locate Tools menu Composable (likely in `ui/tools/` or similar). Add CliAgentToolsButton row.
**Step 2:** Wire navigation: tap → push `cli-agent` route → `CliAgentScreen`.
**Step 3:** Modify chat provider dropdown source to remove headless `cli_agent_claude` / `_gemini` / `_codex` entries.
**Step 4:** Compile, run, manually verify the menu shows the new button and the dropdown no longer lists headless CLI providers.
**Step 5:** Commit.

---

## Phase 8 — End-to-End Verification

### Task 8.1: Manual matrix on a real Android device

Run through every row of the design doc's manual matrix. Tick each ✅ in this checklist as it passes.

- [ ] Cold open → CLI Agent → grocery-store → terminal in <1.5s
- [ ] `ls` + Enter → output renders with colors and box-drawing
- [ ] Long command + Whisper paste mid-line → bracketed paste lands as single block
- [ ] Esc on extra-keys bar → Claude Code interrupts
- [ ] Background app 30s → return → scrollback repaints, session intact
- [ ] Wi-Fi off mid-Bash, restore → reconnect banner, output during gap visible
- [ ] Long-press → Kill → confirmation, row → grey dot
- [ ] Switch to Anna operator → first launch → `claude login` flow inside terminal works
- [ ] Bluetooth keyboard paired → extra-keys bar auto-collapses
- [ ] Apps root pick (`✦ + New app workspace`) → can `mkdir test-app` from inside Claude Code → register via curl → appears in picker on next refresh

**Pass gate:** all 10 rows green.

**If any row fails:** triage via `journalctl -u blackbox.service` + `adb logcat`. Fix → re-run failing row → continue.

---

## Phase 9 — Snapshot Mint

### Task 9.1: Snapshot the implementation

Run `/snapshot-dev Brandon` (or the inline curl per CLAUDE.md). Body should reference:
- design doc path
- plan doc path
- summary of locked decisions (cite Q1–Q10)
- list of files added/modified
- manual-matrix outcome

**Verify:** `journalctl ... | grep "[EMBEDDING] Successfully generated embedding (3072 dimensions)"` confirms the snapshot is searchable.

---

## Status / Progress Notes (append-only — executor fills as it goes)

```
Phase 0: ⏳
Phase 1: ⏳
Phase 2: ⏳
Phase 3: ⏳
Phase 4: ⏳
Phase 5: ⏳
Phase 6: ⏳
Phase 7: ⏳
Phase 8: ⏳
Phase 9: ⏳
```

---

## Risk Register

| Risk | Detection | Response |
|---|---|---|
| `claude login` OAuth URL line-wraps in Termux TerminalView, breaks first-time auth | Manual matrix row "Switch to Anna operator → first launch" | Fall back to host-side `claude login` then `cp -r ~/.claude/auth* ~/.claude-bbox/<op>/` |
| Termux Maven coordinate `com.termux:terminal-emulator:0.118` not resolvable | Phase 4.1 gradle sync fails | Switch to JitPack `com.github.termux:terminal-emulator:<tag>` (documented in 4.1) |
| tmux server crash invalidates all sessions | `tmux ls` returns "no server running" mid-session | Catch error, prompt user to retry; create dedicated tmux server with `-L cli-agent` socket name later if recurrent |
| `ptyprocess` async-readiness flake on idle PTYs (timeouts during read) | Pty bridge tests intermittently fail | The `read(timeout=...)` wrapper returns `b""` on timeout; verify behavior is correct under load before treating as bug |
| Phase 7 device test reveals SDK keyboard handling bug for soft IME + TerminalView | Manual matrix typing tests | Fall back to passing soft-IME events through ExtraKeysBar's input layer rather than directly to TerminalView |

---
