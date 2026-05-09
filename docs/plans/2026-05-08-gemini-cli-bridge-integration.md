# Gemini CLI Bridge Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Google Gemini CLI as a second provider in the existing Claude Code PTY-bridge stack so the Android MVP can launch `gemini` sessions in tmux and stream them to the phone exactly the way it already does for `claude`.

**Architecture:** The backend bridge (`Orchestrator/cli_agent/*` + `routes/cli_agent_routes.py`) is already provider-agnostic — `PROVIDER_BIN` includes a `gemini` slot, `session_name(op, provider, app)` is provider-keyed, and the WebSocket+PtyBridge stack speaks raw bytes. Two real backend gaps: the binary resolver doesn't search nvm-installed paths, and `_schedule_fullscreen_renderer` is hard-coded to send Claude Code's `/tui fullscreen` slash command (would type literal text into Gemini). Refactor to a per-provider hook table, default Gemini's hook to a no-op. The Android picker hard-codes `provider = "claude"` everywhere — add a small chip-row selector at the top of `AppFolderPicker`, persist it via the existing `BlackBoxStore` DataStore, and plumb it through the picker → terminal state machine. Extend the live-dot logic to be per-(operator, provider) so users can have a Claude Code session AND a Gemini session for the same app and see both correctly.

**Tech Stack:** Python 3 / FastAPI / pytest / ptyprocess / tmux (backend); Kotlin / Jetpack Compose / OkHttp WebSocket / Termux terminal-view AAR / Material3 / DataStore Preferences (Android).

**Pre-flight:**
- Gemini CLI binary verified at `/home/ai-black-box-fc/.nvm/versions/node/v20.19.6/bin/gemini` (v0.41.2). Same Ink-based TUI as Claude Code — all existing tmux options (extended-keys, focus-events, env-wrapped TERM=xterm-256color) apply identically.
- Operator's `~/.gemini/` directory already populated (`settings.json`, `GEMINI.md`, `google_accounts.json`, `projects.json`, `state.json`). No isolation needed by default — Gemini will inherit the operator's real config exactly the way Claude Code does today.
- Gemini CLI uses `GEMINI_DIR = ".gemini"` as a hard-coded constant; there is **no** `GEMINI_CONFIG_DIR` env-var override. Don't try to set one. Per-operator isolation, if ever needed, would require a different mechanism (e.g., wrapping with a per-op `HOME` override) and is **out of scope** for this plan.
- Backend test harness: `pytest Orchestrator/tests/test_cli_agent/` runs the existing 41-test backend suite. Add new tests there.

---

## Phase 1: Backend — Binary Discovery + Per-Provider Post-Attach Hooks

The backend has two real gaps. Fix both with TDD, one commit each.

### Task 1.1: Test that `_resolve_provider_bin` finds nvm-installed binaries

**Why:** systemd's restricted `PATH` doesn't include `~/.nvm/versions/node/<ver>/bin`. The current `_resolve_provider_bin` (cli_agent_routes.py:27-42) only extends with `~/.local/bin`, `/usr/local/bin`, `/usr/bin`. Without a fix, `PROVIDER_BIN["gemini"]` resolves to the bare string `"gemini"` and tmux fails to spawn. The `CLI_AGENT_GEMINI_BIN` env-var override would also work, but we want this to "just work" without the operator hand-pinning a versioned nvm path that bumps weekly.

**Files:**
- Create: `Orchestrator/tests/test_cli_agent/test_provider_bin_resolution.py`
- Modify: `Orchestrator/routes/cli_agent_routes.py:27-42` (next task)

**Step 1: Write the failing test**

```python
# Orchestrator/tests/test_cli_agent/test_provider_bin_resolution.py
import os
from pathlib import Path

import pytest

from Orchestrator.routes.cli_agent_routes import _resolve_provider_bin


def test_resolves_binary_in_default_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_bin = bin_dir / "fakeprov"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _resolve_provider_bin("fakeprov") == str(fake_bin)


def test_falls_back_to_bare_name_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Should return the bare name (tmux will fail loudly later, by design)
    assert _resolve_provider_bin("definitely-not-installed-xyz") == "definitely-not-installed-xyz"


def test_resolves_binary_in_nvm_node_versions(tmp_path, monkeypatch):
    # Simulate nvm layout: ~/.nvm/versions/node/v20.19.6/bin/gemini
    nvm_root = tmp_path / ".nvm" / "versions" / "node"
    nvm_bin = nvm_root / "v20.19.6" / "bin"
    nvm_bin.mkdir(parents=True)
    fake_gemini = nvm_bin / "gemini"
    fake_gemini.write_text("#!/bin/sh\nexit 0\n")
    fake_gemini.chmod(0o755)

    # Empty PATH so resolution MUST come from the nvm fallback search.
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert _resolve_provider_bin("gemini") == str(fake_gemini)


def test_prefers_newest_nvm_node_version_when_multiple_present(tmp_path, monkeypatch):
    # Two node versions installed; resolver should pick the lexicographically
    # last one (sufficient for semver-monotonic nvm dirs).
    base = tmp_path / ".nvm" / "versions" / "node"
    for ver in ("v18.20.0", "v20.19.6", "v22.1.0"):
        bin_dir = base / ver / "bin"
        bin_dir.mkdir(parents=True)
        target = bin_dir / "gemini"
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)

    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    resolved = _resolve_provider_bin("gemini")
    assert resolved.endswith("/v22.1.0/bin/gemini"), resolved
```

**Step 2: Run test to verify it fails**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
Orchestrator/venv/bin/pytest Orchestrator/tests/test_cli_agent/test_provider_bin_resolution.py -v
```

Expected: 2 PASS (default-path + bare-name fallback), 2 FAIL (nvm tests — current resolver doesn't search nvm dirs).

**Step 3: Commit the failing tests**

```bash
git add Orchestrator/tests/test_cli_agent/test_provider_bin_resolution.py
git commit -m "test(cli-agent): add nvm-aware provider bin resolution tests (red)"
```

---

### Task 1.2: Extend `_resolve_provider_bin` to search nvm node-version dirs

**Files:**
- Modify: `Orchestrator/routes/cli_agent_routes.py:27-42`

**Step 1: Replace `_resolve_provider_bin` with nvm-aware version**

Replace the function body (lines 27-42) with:

```python
def _resolve_provider_bin(name: str) -> str:
    """Resolve a CLI agent binary to an absolute path.

    systemd's restricted PATH excludes user-local install dirs like
    ~/.local/bin/, and per-version nvm dirs like
    ~/.nvm/versions/node/<ver>/bin. We search an extended PATH that
    includes both. Falls back to the bare name (which will fail loudly
    via tmux if the binary truly cannot be found).

    For nvm we add every existing ~/.nvm/versions/node/*/bin in
    descending lexicographic order so the newest installed Node version
    wins (sufficient because nvm version dirs are semver-monotonic).
    """
    home = Path.home()
    nvm_versions = home / ".nvm" / "versions" / "node"
    nvm_bin_dirs: list[str] = []
    if nvm_versions.is_dir():
        try:
            nvm_bin_dirs = [
                str(p / "bin")
                for p in sorted(nvm_versions.iterdir(), reverse=True)
                if (p / "bin").is_dir()
            ]
        except OSError:
            nvm_bin_dirs = []

    extended_path = os.pathsep.join([
        os.environ.get("PATH", ""),
        str(home / ".local" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
        *nvm_bin_dirs,
    ])
    found = shutil.which(name, path=extended_path)
    return found or name
```

**Step 2: Run the tests to verify they pass**

```bash
Orchestrator/venv/bin/pytest Orchestrator/tests/test_cli_agent/test_provider_bin_resolution.py -v
```

Expected: 4 PASS.

**Step 3: Run the full cli_agent backend suite to confirm no regressions**

```bash
Orchestrator/venv/bin/pytest Orchestrator/tests/test_cli_agent/ -v
```

Expected: all previously-green tests still green; total = 41 + 4 = 45 PASS.

**Step 4: Commit**

```bash
git add Orchestrator/routes/cli_agent_routes.py
git commit -m "feat(cli-agent): resolve provider binaries from nvm node version dirs"
```

---

### Task 1.3: Test that `_schedule_fullscreen_renderer` is provider-scoped

**Why:** `session_manager.py:153` unconditionally schedules `tmux send-keys "/tui fullscreen" Enter` for every new session. This is a Claude-Code-specific slash command. For Gemini, those bytes get typed as literal characters into Gemini's input box and either trigger an unrelated slash menu or get sent to the model as garbage. The behavior must be per-provider.

**Files:**
- Create: `Orchestrator/tests/test_cli_agent/test_post_attach_hooks.py`
- Modify: `Orchestrator/cli_agent/session_manager.py` (next task)

**Step 1: Write the failing test**

```python
# Orchestrator/tests/test_cli_agent/test_post_attach_hooks.py
from unittest.mock import MagicMock, patch

import pytest

from Orchestrator.cli_agent.session_manager import (
    POST_ATTACH_HOOKS,
    _post_attach_hook_for,
)


def test_claude_post_attach_hook_sends_tui_fullscreen():
    hook = _post_attach_hook_for("claude")
    assert hook is not None
    # Hook is the list of strings to pass to `tmux send-keys`.
    assert hook == ["/tui fullscreen", "Enter"]


def test_gemini_post_attach_hook_is_noop():
    assert _post_attach_hook_for("gemini") is None


def test_codex_post_attach_hook_is_noop():
    assert _post_attach_hook_for("codex") is None


def test_unknown_provider_post_attach_hook_is_noop():
    assert _post_attach_hook_for("definitely-unknown") is None


def test_post_attach_hooks_table_only_contains_known_providers():
    # Defensive: any future hook we add should be for a real provider.
    assert set(POST_ATTACH_HOOKS.keys()) <= {"claude", "gemini", "codex"}
```

**Step 2: Run tests to verify they fail**

```bash
Orchestrator/venv/bin/pytest Orchestrator/tests/test_cli_agent/test_post_attach_hooks.py -v
```

Expected: FAIL with ImportError on `POST_ATTACH_HOOKS` / `_post_attach_hook_for` (don't exist yet).

**Step 3: Commit the failing tests**

```bash
git add Orchestrator/tests/test_cli_agent/test_post_attach_hooks.py
git commit -m "test(cli-agent): add per-provider post-attach hook tests (red)"
```

---

### Task 1.4: Refactor `_schedule_fullscreen_renderer` into a per-provider hook table

**Files:**
- Modify: `Orchestrator/cli_agent/session_manager.py`

**Step 1: Add the hook table near the top of session_manager.py**

After the existing module-level constants (`_APPS_ROOT_SLUG`, `_FIELD_SEP`, ~line 14), add:

```python
# --- Per-provider post-attach hooks --------------------------------------
#
# After a tmux session is created and the inner CLI has had time to come
# online, we may need to send a one-time keystroke sequence to nudge the
# CLI into the configuration we want. The classic example is Claude Code,
# which boots in alt-screen mode by default — PgUp/PgDn cannot scroll the
# conversation history in that mode because the bytes never get retained
# outside Claude's process. The fullscreen renderer (Claude's `/tui
# fullscreen` slash command) manages its own scroll buffer instead.
#
# Each value is a list of strings that gets passed verbatim to
# `tmux send-keys -t <name> ...`. tmux interprets bare strings as text
# to type and bracketed names like "Enter" / "C-c" / "Escape" as keys.
#
# Adding a new provider? Two rules:
#   1. Only add an entry if the provider has a real first-run hook.
#     A provider that needs no hook should NOT appear in this table —
#     `None` (i.e., absence) is the default and that is correct.
#   2. The hook must be idempotent: it gets queued onto the inner pty
#     even if the CLI hasn't finished booting, and runs whenever it
#     drains. Don't put state-mutating commands here.
#
POST_ATTACH_HOOKS: dict[str, list[str]] = {
    "claude": ["/tui fullscreen", "Enter"],
    # "gemini" deliberately absent — Ink renderer manages scroll fine.
    # "codex"  deliberately absent — pending real-world verification.
}


def _post_attach_hook_for(provider: str) -> list[str] | None:
    """Return the post-attach send-keys sequence for `provider`, or None."""
    return POST_ATTACH_HOOKS.get(provider)
```

**Step 2: Modify `attach_or_create` to use the per-provider hook**

Find the call site (currently `self._schedule_fullscreen_renderer(name)` at session_manager.py:153) and replace it with:

```python
hook_keys = _post_attach_hook_for(provider)
if hook_keys is not None:
    self._schedule_send_keys(name, hook_keys)
return SessionInfo(name=name, created=True, cwd=cwd)
```

**Step 3: Generalize `_schedule_fullscreen_renderer` into `_schedule_send_keys`**

Replace the existing method (currently session_manager.py:156-178) with a generic version:

```python
def _schedule_send_keys(self, session_name: str,
                         keys: list[str],
                         delay_s: float = 5.0) -> None:
    """Send a keystroke sequence to a freshly-created session after a
    short delay so the inner CLI has had time to boot.

    Runs in a daemon thread; failures are silent. The bytes get queued
    on the inner pty if the CLI isn't quite ready yet, and are processed
    once it is.
    """
    cmd = self._tmux_cmd("send-keys", "-t", session_name, *keys)

    def _go() -> None:
        try:
            time.sleep(delay_s)
            subprocess.run(
                cmd, capture_output=True, text=True, timeout=5,
            )
        except Exception:
            pass

    threading.Thread(target=_go, daemon=True).start()
```

**Step 4: Add an existing-behavior test for `attach_or_create` to lock in regression coverage**

Append to `Orchestrator/tests/test_cli_agent/test_post_attach_hooks.py`:

```python
def test_attach_or_create_schedules_send_keys_for_claude(tmp_path, monkeypatch):
    """attach_or_create should call _schedule_send_keys for claude provider."""
    from Orchestrator.cli_agent.path_validator import PathValidator
    from Orchestrator.cli_agent.operator_config import OperatorConfig
    from Orchestrator.cli_agent.session_manager import TmuxSessionManager

    apps_root = tmp_path / "Apps"
    apps_root.mkdir()
    cfg_root = tmp_path / "cfg"
    pv = PathValidator(apps_root=apps_root)
    oc = OperatorConfig(root=cfg_root)
    mgr = TmuxSessionManager(path_validator=pv, operator_config=oc)

    with patch.object(mgr, "_schedule_send_keys") as mock_schedule, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        # has_session() returns False so we go down the create path.
        with patch.object(mgr, "has_session", return_value=False):
            mgr.attach_or_create(
                operator="testop", provider="claude",
                app="", command=["/bin/true"],
            )
    mock_schedule.assert_called_once()
    args, _ = mock_schedule.call_args
    # First positional is session_name; second is the key list.
    assert args[1] == ["/tui fullscreen", "Enter"]


def test_attach_or_create_does_not_send_keys_for_gemini(tmp_path):
    from Orchestrator.cli_agent.path_validator import PathValidator
    from Orchestrator.cli_agent.operator_config import OperatorConfig
    from Orchestrator.cli_agent.session_manager import TmuxSessionManager

    apps_root = tmp_path / "Apps"
    apps_root.mkdir()
    cfg_root = tmp_path / "cfg"
    pv = PathValidator(apps_root=apps_root)
    oc = OperatorConfig(root=cfg_root)
    mgr = TmuxSessionManager(path_validator=pv, operator_config=oc)

    with patch.object(mgr, "_schedule_send_keys") as mock_schedule, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(mgr, "has_session", return_value=False):
            mgr.attach_or_create(
                operator="testop", provider="gemini",
                app="", command=["/bin/true"],
            )
    mock_schedule.assert_not_called()
```

**Step 5: Run the tests**

```bash
Orchestrator/venv/bin/pytest Orchestrator/tests/test_cli_agent/test_post_attach_hooks.py -v
```

Expected: 7 PASS.

**Step 6: Run the full backend suite for regression check**

```bash
Orchestrator/venv/bin/pytest Orchestrator/tests/test_cli_agent/ -v
```

Expected: 45 + 7 = 52 PASS, 0 FAIL.

**Step 7: Commit**

```bash
git add Orchestrator/cli_agent/session_manager.py Orchestrator/tests/test_cli_agent/test_post_attach_hooks.py
git commit -m "refactor(cli-agent): per-provider post-attach hooks; gemini gets none"
```

---

### Task 1.5: Backend smoke test — spawn a real Gemini session via the WebSocket

**Why:** Unit tests prove the logic; only an end-to-end smoke confirms the integration. Reuses the existing `Orchestrator/cli_agent/smoke_test.py` pattern.

**Files:**
- Modify: `Orchestrator/cli_agent/smoke_test.py` (add a `--provider` flag if missing)

**Step 1: Read the existing smoke_test.py to understand its CLI**

```bash
cat Orchestrator/cli_agent/smoke_test.py | head -60
```

If it already takes `--provider`, skip to Step 3. Otherwise add a `--provider` argument that defaults to `claude`.

**Step 2: Restart the BlackBox service so the new code is loaded**

```bash
sudo systemctl restart blackbox.service
sleep 90  # snapshot index rebuild
sudo systemctl status blackbox.service --no-pager | head -15
```

Expected: `active (running)`.

**Step 3: Run the smoke test against a real Gemini session**

```bash
Orchestrator/venv/bin/python -m Orchestrator.cli_agent.smoke_test \
    --operator Brandon --provider gemini --app ""
```

Expected: connection succeeds; `session_info` text frame received with `state=created`; some bytes flow back from the Gemini boot banner within ~3-5 seconds.

**Step 4: Verify the session exists in tmux**

```bash
sudo journalctl -u blackbox.service --since "5 minutes ago" | grep -E "cli-agent|tmux" | tail -20
```

Expected: log lines mentioning a `cli-agent-Brandon__gemini___root` session.

**Step 5: Manually verify tmux session if accessible (sudo, into service namespace)**

```bash
sudo nsenter -t $(pidof uvicorn | awk '{print $1}') -m -p -u tmux list-sessions 2>/dev/null || \
  echo "Service runs in a private namespace; verification via Android client in Phase 4 instead."
```

**Step 6: Commit any smoke_test.py changes**

```bash
git add Orchestrator/cli_agent/smoke_test.py
git commit -m "chore(cli-agent): add --provider flag to smoke test"
```

---

## Phase 2: Android — Provider Selector + State Plumbing

The Android picker hard-codes `provider = "claude"`. Add a provider chip row at the top of `AppFolderPicker`, persist the selection in `BlackBoxStore`, and thread the chosen provider through the picker → terminal state machine.

### Task 2.1: Add `KEY_CLI_AGENT_PROVIDER` to `BlackBoxStore`

**Why:** Selected provider should persist across app restarts. `BlackBoxStore` already encapsulates the DataStore preferences pattern with `KEY_PROVIDER` (chat provider). Adding a CLI-agent-scoped twin follows the same shape with one accessor pair.

**Files:**
- Modify: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/data/store/BlackBoxStore.kt`

**Step 1: Read the existing `BlackBoxStore.kt` end-to-end first**

```bash
cat "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/data/store/BlackBoxStore.kt"
```

Note the existing pattern: `val KEY_xxx = stringPreferencesKey("xxx")` near the top, `val xxxFlow: Flow<String> = ...`, `suspend fun setXxx(value: String) { ... }`.

**Step 2: Add the new key alongside `KEY_PROVIDER`**

Inside the companion object (around `BlackBoxStore.kt:18-24`), add:

```kotlin
val KEY_CLI_AGENT_PROVIDER = stringPreferencesKey("cli_agent_provider")
```

**Step 3: Add the accessor pair next to the existing `providerFlow`/`setProvider` pair**

```kotlin
val cliAgentProviderFlow: Flow<String> =
    context.dataStore.data.map { it[KEY_CLI_AGENT_PROVIDER] ?: "claude" }
suspend fun setCliAgentProvider(value: String) {
    context.dataStore.edit { it[KEY_CLI_AGENT_PROVIDER] = value }
}
```

**Step 4: Build to verify the Kotlin compiles**

```bash
cd "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal"
./gradlew :app:compileDebugKotlin
```

Expected: BUILD SUCCESSFUL.

**Step 5: Commit**

```bash
git add app/src/main/java/com/aiblackbox/portal/data/store/BlackBoxStore.kt
git commit -m "feat(android/cli-agent): persist selected CLI Agent provider in BlackBoxStore"
```

---

### Task 2.2: Add `CliAgentProvider` enum to the data model

**Why:** Stringly-typed providers ("claude" / "gemini") are fragile. An enum with display name + slug + accent color centralizes the list and lets the Compose UI render chips off the same source of truth.

**Files:**
- Modify: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/data/model/CliAgentModels.kt`

**Step 1: Append the enum to `CliAgentModels.kt`**

```kotlin
/**
 * The CLI Agent providers the orchestrator can launch behind the PTY
 * bridge. Slug values must match the keys of `PROVIDER_BIN` in
 * Orchestrator/routes/cli_agent_routes.py — the orchestrator rejects any
 * unknown provider with WebSocket close code 4003.
 *
 * Display names are short on purpose: chip rows are width-constrained on
 * phone screens.
 */
enum class CliAgentProvider(val slug: String, val display: String) {
    CLAUDE("claude", "Claude"),
    GEMINI("gemini", "Gemini"),
    // CODEX("codex", "Codex"),  // enable in a follow-up plan once tested
    ;

    companion object {
        fun fromSlug(slug: String): CliAgentProvider =
            values().firstOrNull { it.slug == slug } ?: CLAUDE
    }
}
```

**Step 2: Build to verify**

```bash
./gradlew :app:compileDebugKotlin
```

Expected: BUILD SUCCESSFUL.

**Step 3: Commit**

```bash
git add app/src/main/java/com/aiblackbox/portal/data/model/CliAgentModels.kt
git commit -m "feat(android/cli-agent): add CliAgentProvider enum (Claude + Gemini)"
```

---

### Task 2.3: Add `AgentProviderChips` Composable to `AppFolderPicker.kt`

**Why:** A single horizontal segmented-control row at the top of the picker. Tap a chip → that provider becomes the active one for the whole picker (live-dot + onAppSelected + kill all use it). Mirrors the "select provider, then select folder" UX framing the user sketched in SNAP-20260501-6376.

**Files:**
- Modify: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/ui/cli_agent/AppFolderPicker.kt`

**Step 1: Add the chip-row Composable at the bottom of the file**

```kotlin
/**
 * Horizontal segmented-control row of available CLI Agent providers.
 * Tap a chip to switch the active provider. Selection is fully owned by
 * the caller (hoisted state).
 */
@Composable
internal fun AgentProviderChips(
    selected: CliAgentProvider,
    onSelect: (CliAgentProvider) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        CliAgentProvider.values().forEach { provider ->
            val isSelected = provider == selected
            Surface(
                shape = RoundedCornerShape(20.dp),
                color = if (isSelected) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.surfaceVariant,
                tonalElevation = if (isSelected) 4.dp else 0.dp,
                modifier = Modifier
                    .height(36.dp)
                    .pointerInput(provider) {
                        detectTapGestures(onTap = { onSelect(provider) })
                    },
            ) {
                Box(
                    modifier = Modifier.padding(horizontal = 16.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = provider.display,
                        color = if (isSelected) MaterialTheme.colorScheme.onPrimary
                                else MaterialTheme.colorScheme.onSurfaceVariant,
                        fontWeight = if (isSelected) FontWeight.SemiBold else FontWeight.Medium,
                        style = MaterialTheme.typography.labelLarge,
                    )
                }
            }
        }
    }
}
```

You will also need to add the matching imports at the top of the file:

```kotlin
import com.aiblackbox.portal.data.model.CliAgentProvider
```

**Step 2: Replace the hard-coded `DEFAULT_PROVIDER`**

Delete `AppFolderPicker.kt:71` (`private const val DEFAULT_PROVIDER = "claude"`) — it'll be supplied by the caller from now on.

**Step 3: Add a `provider` parameter to `AppFolderPicker`**

Update the function signature (`AppFolderPicker.kt:122-129`):

```kotlin
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppFolderPicker(
    repository: CliAgentSessionRepository,
    operator: String,
    selectedProvider: CliAgentProvider,
    onProviderSelected: (CliAgentProvider) -> Unit,
    onAppSelected: (appSlug: String, appName: String) -> Unit,
    onAppsRootSelected: () -> Unit = { onAppSelected("", "Apps root") },
    modifier: Modifier = Modifier,
) {
```

**Step 4: Use the new provider in live-dot computation and kill flow**

Inside the `LaunchedEffect(operator, refreshTick)` block, change the `liveSlugs` line so live-dot is per-provider:

```kotlin
// Filter sessions to ones for the currently-selected provider so the
// dot reflects "is THIS provider active for this app", not "is anyone".
val liveSlugs: Set<String> = sessions
    .filter { it.sessionId.contains("__${selectedProvider.slug}__") }
    .mapNotNull { extractAppSlug(it.sessionId) }
    .toSet()
```

The `LaunchedEffect` key must also include the provider so the list refreshes when the user flips the chip:

```kotlin
LaunchedEffect(operator, selectedProvider, refreshTick) {
```

In the kill confirmation `confirmButton` `onClick`, replace the hard-coded `DEFAULT_PROVIDER`:

```kotlin
val sessionId = cliAgentSessionId(
    operator = operator,
    provider = selectedProvider.slug,
    appSlug = toKill.slug,
)
```

**Step 5: Render the chip row at the top of the `Surface`**

Inside the existing `Surface` (around `AppFolderPicker.kt:176-189`), wrap the contents in a `Column` and prepend the chip row:

```kotlin
Surface(
    modifier = modifier.fillMaxSize(),
    color = MaterialTheme.colorScheme.background,
) {
    Column(modifier = Modifier.fillMaxSize()) {
        AgentProviderChips(
            selected = selectedProvider,
            onSelect = onProviderSelected,
        )
        HorizontalDivider(
            color = MaterialTheme.colorScheme.outlineVariant,
            thickness = 0.5.dp,
        )
        when {
            loading && rows.isEmpty() -> LoadingState()
            else -> PickerList(
                rows = rows,
                onAppsRootSelected = onAppsRootSelected,
                onAppSelected = onAppSelected,
                onLongPressLive = { row -> actionTarget = row },
            )
        }
    }
}
```

**Step 6: Update the `onAppSelected` callback to pass `appName` correctly when long-press → Reconnect fires**

The action sheet's "Reconnect" branch should also use the active provider — no change needed in that file because it already routes through `onAppSelected(slug, name)`, and the caller (CliAgentScreen) decides which provider to use. Just confirm.

**Step 7: Build to verify Kotlin compiles**

```bash
./gradlew :app:compileDebugKotlin
```

Expected: BUILD SUCCESSFUL. Compile errors will surface unresolved callers — those get fixed in Task 2.4.

**Step 8: Commit (the file currently won't link until 2.4, that's OK — TDD-style red phase)**

```bash
git add app/src/main/java/com/aiblackbox/portal/ui/cli_agent/AppFolderPicker.kt
git commit -m "feat(android/cli-agent): provider chip row + per-provider live dots"
```

---

### Task 2.4: Wire the provider state into `CliAgentScreen`

**Files:**
- Modify: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/ui/cli_agent/CliAgentScreen.kt`

**Step 1: Add the dependencies and DataStore load at the top of `CliAgentScreen`**

Replace the entire file body (`CliAgentScreen.kt:30-72`) with:

```kotlin
@Composable
fun CliAgentScreen(
    origin: String,
    operator: String,
    onBackToTools: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val store = remember { BlackBoxStore(context) }
    val scope = rememberCoroutineScope()

    val api: BlackBoxApi = remember(origin) { BlackBoxApi(origin) }
    val repository = remember(api) { CliAgentSessionRepository(api) }

    // Load persisted provider; default = Claude until DataStore returns.
    val providerSlug by store.cliAgentProviderFlow
        .collectAsState(initial = CliAgentProvider.CLAUDE.slug)
    val selectedProvider = remember(providerSlug) {
        CliAgentProvider.fromSlug(providerSlug)
    }

    var state by remember { mutableStateOf<CliAgentInternalState>(CliAgentInternalState.Picker) }

    when (val s = state) {
        CliAgentInternalState.Picker -> {
            BackHandler(enabled = true) { onBackToTools() }
            AppFolderPicker(
                repository = repository,
                operator = operator,
                selectedProvider = selectedProvider,
                onProviderSelected = { p ->
                    scope.launch { store.setCliAgentProvider(p.slug) }
                },
                onAppSelected = { slug, name ->
                    state = CliAgentInternalState.Terminal(slug, name, selectedProvider.slug)
                },
                modifier = modifier,
            )
        }
        is CliAgentInternalState.Terminal -> {
            TerminalScreen(
                api = api,
                operator = operator,
                appSlug = s.appSlug,
                appName = s.appName,
                provider = s.provider,
                onBack = { state = CliAgentInternalState.Picker },
                modifier = modifier,
            )
        }
    }
}

private sealed class CliAgentInternalState {
    data object Picker : CliAgentInternalState()
    data class Terminal(
        val appSlug: String,
        val appName: String,
        val provider: String,
    ) : CliAgentInternalState()
}
```

Add the new imports at the top of the file:

```kotlin
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.platform.LocalContext
import com.aiblackbox.portal.data.model.CliAgentProvider
import com.aiblackbox.portal.data.store.BlackBoxStore
import kotlinx.coroutines.launch
```

**Step 2: Build to verify**

```bash
./gradlew :app:compileDebugKotlin
```

Expected: BUILD SUCCESSFUL.

**Step 3: Commit**

```bash
git add app/src/main/java/com/aiblackbox/portal/ui/cli_agent/CliAgentScreen.kt
git commit -m "feat(android/cli-agent): plumb selected provider through picker→terminal state"
```

---

### Task 2.5: Drop the default provider on `TerminalScreen` so callers must be explicit

**Why:** `TerminalScreen.kt:89` still has `provider: String = "claude"`. This silently masks a missing-arg bug. Make it required.

**Files:**
- Modify: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/ui/cli_agent/TerminalScreen.kt`

**Step 1: Remove the `= "claude"` default**

Change `TerminalScreen.kt:89`:

```kotlin
provider: String,
```

**Step 2: Update the top bar to display which provider is running**

This is a small but important UX nicety — when the user taps an app, they should see "Claude · App Name" or "Gemini · App Name" so they know which CLI is active. Modify `TerminalTopBar` (`TerminalScreen.kt:617-658`) so the title formats as:

```kotlin
text = if (title.isBlank()) "Apps root" else title,
```

Add a small subtitle line under the title text inside the `Text(modifier = Modifier.weight(1f, fill = true)...)` Composable. Easier path: change the call site to interpolate provider into the title string in `TerminalScreen`:

In `TerminalScreen.kt` find the call to `TerminalTopBar` (~line 227) and change:

```kotlin
TerminalTopBar(
    title = "${provider.replaceFirstChar { it.uppercase() }} · $appName",
    onBack = { ... },
    onShowKeyboard = { ... },
)
```

This stays inside the existing single-line top bar — no layout changes needed.

**Step 3: Build**

```bash
./gradlew :app:compileDebugKotlin
```

Expected: BUILD SUCCESSFUL. (`CliAgentScreen` is the only caller of `TerminalScreen` and was already updated in Task 2.4.)

**Step 4: Commit**

```bash
git add app/src/main/java/com/aiblackbox/portal/ui/cli_agent/TerminalScreen.kt
git commit -m "refactor(android/cli-agent): require provider explicitly + show in top bar"
```

---

## Phase 3: Manual Smoke Test on Phone

Now verify it actually works with a real Gemini session, real WebSocket, real terminal.

### Task 3.1: Build the APK

**Step 1: Clean build**

```bash
cd "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal"
./gradlew clean assembleDebug
```

Expected: BUILD SUCCESSFUL. Output APK at `app/build/outputs/apk/debug/app-debug.apk`.

**Step 2: Install on the phone**

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Expected: `Success`.

---

### Task 3.2: End-to-end Gemini session test

**Step 1: Open the BlackBox Portal app on the phone**

Navigate: Settings → CLI Agent.

**Step 2: Verify the chip row renders**

- Picker should show two chips at the top: `[Claude] [Gemini]`
- "Claude" should be selected by default (first launch)
- Live-dot column should reflect any existing claude sessions

**Step 3: Tap the `Gemini` chip**

- Chip becomes selected (filled with primary color)
- Live-dot column updates — should clear (no Gemini sessions exist yet)
- The "active" label disappears from rows that previously showed it

**Step 4: Tap any registered app row**

- Navigates to TerminalScreen
- Top bar reads: "Gemini · {app name}"
- "Connecting…" banner appears
- Within ~3-5 seconds: Gemini boot banner renders ("✦ Welcome to Gemini CLI" or similar)
- Soft keyboard pops up on tap; ExtraKeysBar visible

**Step 5: Type a basic command**

- Type `/help` → press Enter via the ↵ extra key
- Expected: Gemini's slash-command help renders in the terminal (verifies bidirectional flow + slash menu rendering)

**Step 6: Background the app, kill, reopen**

- Press home button
- Reopen the app, navigate back to CLI Agent → picker
- Live-dot should be GREEN on the row you just used (Gemini still selected)
- Tap the row → reconnect to the same Gemini session, scrollback intact

**Step 7: Switch back to Claude chip**

- Tap `Claude` chip
- Live-dot column updates — Claude sessions become visible, Gemini ones grey out
- Confirms per-provider live-dot logic works

**Step 8: Long-press on the live Gemini row**

- Action sheet appears
- Tap "Kill session" → confirm
- Toast: "{app name} session terminated"
- Live-dot becomes grey

If any of Steps 2-8 fail, capture the symptom and stop. Do NOT continue to Phase 4.

---

## Phase 4: Cleanup, Docs, and Snapshot

### Task 4.1: Update `Orchestrator/cli_agent/STATUS.md`

**Step 1: Append a Phase 10 block at the bottom of the file**

```text
=== Phase 10 — Gemini CLI Provider Added 2026-05-08 ===
- Backend nvm-aware bin resolver (cli_agent_routes.py)
- Per-provider POST_ATTACH_HOOKS table (session_manager.py); claude=/tui fullscreen, gemini=none
- Android provider chip selector + per-provider live dots
- Persistence via BlackBoxStore.KEY_CLI_AGENT_PROVIDER
- Smoke-tested end-to-end on phone
- Backend tests: 52/52 PASS (was 41, +11 for nvm + post-attach-hooks)
- Codex provider added to PROVIDER_BIN but NOT enabled in Android UI yet — pending follow-up plan
```

**Step 2: Commit**

```bash
git add Orchestrator/cli_agent/STATUS.md
git commit -m "docs(cli-agent): document Gemini provider integration (Phase 10)"
```

---

### Task 4.2: Snapshot the work

**Step 1: Invoke `/snapshot-dev` slash command**

Run as the **Brandon** operator (NOT Brandon-DEV — completed work lands under the user's primary operator so future searches surface it). The snapshot should describe:
- The PTY-bridge-over-headless inversion was extended to a second provider with no architectural change
- The two real backend gaps fixed (nvm path, per-provider hooks)
- The Android plumbing pattern (provider enum + chip row + DataStore key) is the template for adding Codex next
- The `_resolve_provider_bin` nvm fallback also makes any future Node-based CLI work for free

**Step 2: Verify the embedding generated**

```bash
sudo journalctl -u blackbox.service --since "2 minutes ago" | grep -iE "embedding|3072"
```

Expected: `[EMBEDDING] Successfully generated embedding (3072 dimensions)`.

If you see `Failed`, flag to the user — the snapshot will be search-blind otherwise.

---

## Out of Scope — Save for Codex Follow-Up Plan

These were considered and deliberately deferred so the Gemini-only ship is small and verifiable:

1. **Codex provider** — Same pattern as this plan (one enum entry, one PROVIDER_BIN entry — already there). Skipping until Gemini is shaken out for a few real sessions and any provider-specific quirks surface.
2. **Per-provider terminal accent color or icon** — Cosmetic. Adds enum field + a `Color` lookup in TerminalTopBar. Defer until visual feedback indicates it's needed.
3. **Multiple-provider-per-app sessions** (Claude AND Gemini for the same folder) — Backend already supports this via the session_id tuple, but the picker shows one row per app, not one per (app, provider). The current "switch chip → see that provider's sessions" pattern keeps the picker simple. Multi-row design is its own UX problem, not blocking.
4. **Per-operator config isolation for Gemini** — Gemini CLI has no `GEMINI_CONFIG_DIR` env var; isolation would require per-operator `HOME` overrides. Real but invasive. Not needed for the single-operator Brandon use case.
5. **Provider chip in TerminalScreen's top bar** — Today the user has to back out to picker to switch providers. A future improvement is an in-terminal switcher; deferred until friction is observed.

---

## Estimated Effort

- Phase 1 (backend): ~45 min, ~4 commits, ~50 LOC + 11 new tests
- Phase 2 (Android): ~60 min, ~5 commits, ~80 LOC across 4 files
- Phase 3 (smoke test on phone): ~15 min, no commits
- Phase 4 (docs + snapshot): ~10 min, 1 commit + 1 snapshot

**Total:** ~2-2.5 hours for a tight loop.
