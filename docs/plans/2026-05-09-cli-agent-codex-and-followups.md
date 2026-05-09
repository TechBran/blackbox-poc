# CLI Agent Codex + Operator-Pill Refactor + Cleanup Quartet

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
>
> **For the operator (Brandon):** Verify each track's assumptions before kickoff (especially Track A's choice of Codex CLI — see Task A.1). Three independent tracks; you can execute in any order.

**Goal:** (1) Add OpenAI Codex CLI as the third provider in the existing PTY/tmux/WebSocket bridge so the Android Portal picker can rotate among Claude, Gemini, and Codex. (2) Replace every hardcoded `top = 96.dp` (and similar) with a measured `LocalOperatorPillHeight` CompositionLocal so the floating operator pill bubble's height becomes a single source of truth. (3) Land a cleanup quartet — DRY the duplicated nvm version-sort logic; commit the unstaged TTS additions in `BlackBoxStore.kt`; commit the unstaged IME additions in `TerminalScreen.kt`; resolve any other "WIP in working tree" smells found during the audit.

**Architecture:** Track A is mostly mechanical — the bridge is provider-agnostic from day one (Phase 10 `POST_ATTACH_HOOKS` table and `_augmented_spawn_path` already handle nvm-installed Node CLIs). Codex slots in at the same level as Gemini did, with at most a per-provider hook entry if Codex needs one. Track B replaces a recurring layout antipattern: today every screen near the top of the picker hardcodes a top offset that matches the operator pill's design size — the new pattern measures the pill once and lets screens consume the height as a CompositionLocal value. Track C is hygiene: extract the duplicated nvm-version-sort logic into a shared helper, and properly commit the local work-in-progress that's been sitting in `BlackBoxStore.kt` (TTS) and `TerminalScreen.kt` (IME) so the `cli-agent` branch becomes self-buildable from any commit.

**Tech Stack:** Python 3 / FastAPI / pytest / ptyprocess / tmux (backend); Kotlin / Jetpack Compose / OkHttp WebSocket / Termux terminal-view AAR / Material3 / DataStore Preferences / CompositionLocal (Android).

**Pre-flight:**
- Phase 10 final state: Android `cli-agent` branch @ `1bee910`, backend cli_agent suite at 59 PASS. Plan: `docs/plans/2026-05-08-gemini-cli-bridge-integration.md`. Snapshot: `SNAP-20260509-6530`.
- Codex CLI is NOT currently installed on this machine (`which codex` returns empty). Track A's first task is choosing + installing it; default assumption is `npm install -g @openai/codex` but the operator should confirm before kickoff.
- `BlackBoxStore.kt` carries unstaged TTS API additions (`getOperatorVoice`, `setOperatorVoice`, `autoTtsEnabled`, `setAutoTtsEnabled`) other files depend on for the build to succeed. `TerminalScreen.kt` carries unstaged IME/soft-keyboard improvements. Both preserved by Phase 10's `git commit --only` discipline; Track C lands them as proper commits.
- Operator pill rendering lives in `app/src/main/java/com/aiblackbox/portal/ui/components/TopBar.kt` (Composable `BlackBoxTopBar`). The pill is a `Column` aligned `Alignment.TopCenter` inside a `Box` with `statusBarsPadding()` — it's a true overlay (no layout space taken), which is why downstream screens must hardcode top offsets.

---

## Track A — Codex CLI Integration (Phase 11)

The bridge is already provider-agnostic. This is mostly an enum entry plus a smoke test.

### Task A.1: Verify and install the Codex CLI binary + complete one-time auth

**Why:** The Phase 10 nvm-aware resolver only finds binaries that exist. We need to install OpenAI's Codex CLI, verify the shebang/PATH plumbing handles it, AND complete the one-time interactive auth flow that the bridge can't do for us. Confirmed `npm view @openai/codex version` → `0.130.0` so the package exists. (Aider and other OpenAI-CLI forks are out of scope per the "Out of Scope" section; if the operator wants one of those instead, fork this plan.)

**Files:** No file edits. Shell installation + interactive auth + verification only.

**Step 1: Install the canonical OpenAI Codex CLI**

```bash
npm install -g @openai/codex
```

Lands at `~/.nvm/versions/node/v<latest>/bin/codex`. Phase 10's `_resolve_provider_bin` extension finds it via the nvm fallback. Phase 10's `_augmented_spawn_path` injects nvm bin dirs into the spawn-time PATH so `#!/usr/bin/env node` (Codex's shebang, same as Gemini's) resolves the Node interpreter.

**Step 2: Verify the binary launches and check shebang**

```bash
which codex && codex --version
head -1 $(which codex)
```

Expected: absolute path to nvm bin, version string, shebang `#!/usr/bin/env node`.

**Step 3: Complete the one-time interactive auth flow**

Codex requires either an `OPENAI_API_KEY` env var OR a stored OAuth token from `codex login`. The bridge can't do interactive OAuth, so this MUST happen on the host before any bridge session will work.

Try API-key first (simpler):

```bash
grep "^OPENAI_API_KEY=" /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/.env || \
  echo "Add OPENAI_API_KEY=sk-... to /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/.env"
```

If the key is present, Codex picks it up via `load_dotenv()` (config.py:115) → `os.environ` → `subprocess.run(env=full_env)` → tmux server's env. Verify the propagation in Step 4 below.

If the key is NOT present and the operator prefers OAuth:

```bash
codex login
```

(Will open a browser — cannot be done from inside the bridge.)

**Step 4: Verify the API key actually reaches a tmux child**

Critical check — dotenv loads keys into Python's `os.environ` but the tmux server inherits env from whoever spawned it. If the tmux server is already running from before the env was loaded, the new sessions inherit the OLD env. To force a clean slate AND verify:

```bash
# Force any existing user-tmux server to die (the orchestrator will respawn cleanly)
tmux kill-server 2>/dev/null || true
sudo systemctl restart blackbox.service
sleep 90  # snapshot index rebuild

# Spawn a probe Codex session via the bridge and verify the key propagated
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
Orchestrator/venv/bin/python -c "
from Orchestrator.cli_agent.session_manager import TmuxSessionManager
from Orchestrator.cli_agent.path_validator import PathValidator
from Orchestrator.cli_agent.operator_config import OperatorConfig
from pathlib import Path
import os, subprocess
mgr = TmuxSessionManager(
    PathValidator(Path('/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Apps')),
    OperatorConfig(Path.home() / '.claude-bbox'),
)
info = mgr.attach_or_create(operator='Brandon', provider='codex', app='', command=['env'])
import time; time.sleep(2)
out = subprocess.run(['tmux','capture-pane','-t',info.name,'-p'], capture_output=True, text=True)
mgr.kill(info.name)
print('OPENAI_API_KEY visible to child:' , 'OPENAI_API_KEY=' in out.stdout)
"
```

Expected: `OPENAI_API_KEY visible to child: True`. If False, the key isn't propagating — see Task A.5's failure modes (likely fix: add `OPENAI_API_KEY` to the env dict in `attach_or_create`).

**Step 5: Resolve via the orchestrator's lookup**

```bash
Orchestrator/venv/bin/python -c "from Orchestrator.routes.cli_agent_routes import _resolve_provider_bin; print(_resolve_provider_bin('codex'))"
```

Expected: an absolute path under `~/.nvm/versions/node/`. If it returns `'codex'` (bare name), set `CLI_AGENT_CODEX_BIN=/abs/path` in `.env`.

---

### Task A.2: Spike-test Codex's TUI behaviors before adding hooks

**Why:** Phase 10 established that Claude needs `/tui fullscreen` injected and Gemini needs nothing. Codex is the third data point — could be either, or could need something different (OpenAI's CLI has historically been less consistent on terminal hygiene than Anthropic's). Run Codex manually under tmux first to characterize, then decide hook entry vs. no-entry.

**Files:** No code edits in this task. Documentation captured in Task A.6's STATUS.md.

**Step 0: Precondition — verify Codex actually runs interactively before characterizing TUI**

If A.1 Step 4 reported `OPENAI_API_KEY visible to child: True`, this should just work. If not, fix auth FIRST or this whole spike characterizes the login UI rather than the actual Codex TUI.

```bash
# Quick sanity in a normal shell — should reach Codex's prompt, not a login screen
codex
# (Type a trivial query like "hi" — confirm it gets a response. Quit with /quit or Ctrl+D)
```

If this falls into a login flow or an error, STOP and complete A.1 Step 3 first.

**Step 1: Launch Codex inside a manual tmux session**

```bash
tmux new-session -s codex-spike "env TERM=xterm-256color codex"
```

**Step 2: From a second terminal, attach and observe**

```bash
tmux attach -t codex-spike
```

Note specifically:
- Does Codex use the **alt-screen buffer**? (Press PgUp inside Codex — if scrollback shows the conversation, no alt-screen; if scrollback shows shell history, alt-screen is on)
- Does Codex respond to **focus events**? (Click outside the terminal window — Codex should detect "unfocused" if it cares; check for any banner about focus-events)
- Does Codex render correctly with `extended-keys on`? (Try Shift+arrow, Ctrl+Arrow — should produce navigation, not literal text)
- Does Codex have a `/tui fullscreen` analog or any other first-run nudge?

**Step 3: Decide the hook entry**

- If Codex is alt-screen + has a fullscreen-renderer slash command: add to `POST_ATTACH_HOOKS["codex"]` mirroring Claude
- If Codex is inline-renderer (like Gemini): leave absent from the table
- If Codex needs a different one-time nudge (e.g., a config command): add the appropriate keystroke list

**Step 4: Detach and kill the spike session**

```bash
tmux kill-session -t codex-spike
```

---

### Task A.3: Add `CliAgentProvider.CODEX` to the Android enum

**Files:**
- Modify: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/data/model/CliAgentModels.kt`

**Step 1: Add the enum value**

Find the existing enum (added in Phase 10 commit `9b3975c`):

```kotlin
enum class CliAgentProvider(val slug: String, val display: String) {
    CLAUDE("claude", "Claude"),
    GEMINI("gemini", "Gemini"),
    ;
    ...
}
```

Add `CODEX("codex", "Codex"),` between `GEMINI` and the trailing `;`:

```kotlin
enum class CliAgentProvider(val slug: String, val display: String) {
    CLAUDE("claude", "Claude"),
    GEMINI("gemini", "Gemini"),
    CODEX("codex", "Codex"),
    ;
    ...
}
```

**Step 2: Build to verify**

```bash
cd "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal"
./gradlew :app:compileDebugKotlin
```

Expected: BUILD SUCCESSFUL. The `AppFolderPicker.kt` chip row iterates `CliAgentProvider.values().forEach` so the third chip appears automatically.

**Step 3: Commit**

```bash
git add app/src/main/java/com/aiblackbox/portal/data/model/CliAgentModels.kt
git commit --only app/src/main/java/com/aiblackbox/portal/data/model/CliAgentModels.kt -m "$(cat <<'EOF'
feat(android/cli-agent): add Codex provider to CliAgentProvider enum

Codex chip appears automatically in AppFolderPicker.kt via the
existing values().forEach iteration. Backend PROVIDER_BIN already
has the slot.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A.4: (Conditional) Add `POST_ATTACH_HOOKS["codex"]` if Task A.2 found Codex needs a hook

**Files:**
- Modify: `Orchestrator/cli_agent/session_manager.py`
- Modify: `Orchestrator/tests/test_cli_agent/test_post_attach_hooks.py`

**SKIP this task if Task A.2 concluded Codex needs no hook.** The `# "codex" deliberately absent — pending real-world verification.` comment in `POST_ATTACH_HOOKS` should then be updated to `# "codex" deliberately absent — Ink renderer / inline output / etc.` (whatever Task A.2 found).

**If Codex DOES need a hook, do these in order:**

**Step 1: Add the failing test**

Append to `test_post_attach_hooks.py`:

```python


def test_codex_post_attach_hook_sends_xxx():
    hook = _post_attach_hook_for("codex")
    assert hook is not None
    assert hook == [<keystroke list from Task A.2>]
```

**Step 2: Run to confirm RED (existing `test_codex_post_attach_hook_is_noop` will fail too — that's expected since we're flipping the behavior)**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
Orchestrator/venv/bin/pytest Orchestrator/tests/test_cli_agent/test_post_attach_hooks.py -v
```

Expected: 2 tests fail (the new one + the existing `test_codex_post_attach_hook_is_noop`).

**Step 3: Update the table**

In `Orchestrator/cli_agent/session_manager.py`, find `POST_ATTACH_HOOKS` and change:

```python
POST_ATTACH_HOOKS: dict[str, list[str]] = {
    "claude": ["/tui fullscreen", "Enter"],
    # "gemini" deliberately absent — Ink renderer manages scroll fine.
    # "codex"  deliberately absent — pending real-world verification.
}
```

to (using whatever keys Task A.2 found):

```python
POST_ATTACH_HOOKS: dict[str, list[str]] = {
    "claude": ["/tui fullscreen", "Enter"],
    "codex":  [<keystroke list from Task A.2>],
    # "gemini" deliberately absent — Ink renderer manages scroll fine.
}
```

**Step 4: Update the existing `test_codex_post_attach_hook_is_noop` test**

Either delete it (if Codex has a real hook now), or leave it but rename to reflect that Codex has a hook. Cleaner: delete and let the new positive test stand.

**Step 5: Run to confirm GREEN**

```bash
Orchestrator/venv/bin/pytest Orchestrator/tests/test_cli_agent/ -v
```

Expected: full suite green at 59 PASS (or 60 if you added a new test without removing the noop one).

---

### Task A.5: Backend smoke test for Codex

**Files:** None to edit. `smoke_test.py` is already provider-configurable from Phase 10.

**Step 1: Restart blackbox.service to pick up any session_manager.py changes**

```bash
sudo systemctl restart blackbox.service
for i in $(seq 1 60); do
    sleep 2
    if curl -sf http://localhost:9091/agent/apps > /dev/null 2>&1; then
        echo "Service responsive after $((i*2))s"
        break
    fi
done
```

**Step 2: Run the Claude regression**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
Orchestrator/venv/bin/python Orchestrator/cli_agent/smoke_test.py --provider claude --app grocery-store
```

Expected: ALL CHECKS PASSED.

**Step 3: Run the Gemini regression**

```bash
Orchestrator/venv/bin/python Orchestrator/cli_agent/smoke_test.py --provider gemini --app ""
```

Expected: ALL CHECKS PASSED.

**Step 4: Run the Codex smoke**

```bash
Orchestrator/venv/bin/python Orchestrator/cli_agent/smoke_test.py --provider codex --app ""
```

Expected: ALL CHECKS PASSED. Phase A byte count is **informational fingerprint, not pass criterion** — pass criterion is `>0 bytes within 10s + all 4 phases pass`. Record the byte count for the snapshot's "verification evidence" section.

**If Codex smoke FAILS,** in order of most likely cause:

1. **`OPENAI_API_KEY` not propagating.** Phase A returns >0 bytes but they're a login-prompt or auth-error message. Verify with `tmux capture-pane -t cli-agent-Brandon__codex___root -p | head -20` while the session is alive. Fix: edit `Orchestrator/cli_agent/session_manager.py` to add the key to the env dict in `attach_or_create`:
   ```python
   env = {
       "TERM": self._DEFAULT_TERM,
       "PATH": _augmented_spawn_path(),
       **({"OPENAI_API_KEY": os.environ["OPENAI_API_KEY"]}
          if os.environ.get("OPENAI_API_KEY") else {}),
       **self.oc.env_for(operator),
   }
   ```
   This is the architectural-followup A1 in the original review — if you do this, file a follow-up plan to add a proper `PROVIDER_ENV_VARS` table covering Claude/Gemini/Codex uniformly.
2. **Binary not found via `_resolve_provider_bin`.** Phase A returns 0 bytes; journalctl shows tmux exit nonzero. Fix: set `CLI_AGENT_CODEX_BIN=/home/ai-black-box-fc/.nvm/versions/node/v<ver>/bin/codex` in `.env`, restart service.
3. **Shebang interpreter not found.** Phase A returns ~13 bytes of `b'no sessions\r\n'` (same symptom we hit with Gemini). `_augmented_spawn_path` already covers nvm-installed Node CLIs but verify `node` is actually in the path it builds with `Orchestrator/venv/bin/python -c "from Orchestrator.cli_agent.session_manager import _augmented_spawn_path; print(_augmented_spawn_path())"`.
4. **Codex first-run prompts/wizards consume our smoke test's input.** Phase A returns lots of bytes but Phases B/C/D fail because Codex is sitting on a "set up your default model" wizard. Fix: complete the wizard manually once via `codex` in a normal shell, then re-run smoke.

---

### Task A.6: Build APK + manual phone smoke + STATUS.md update + snapshot

**Files:**
- Build: `app/build/outputs/apk/debug/app-debug.apk`
- Modify: `Orchestrator/cli_agent/STATUS.md` (append a Phase 11 block)

**Step 1: Build the APK**

```bash
cd "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal"
./gradlew assembleDebug
```

**Step 2: Install on the phone (manual — operator does this) and verify**

Test checklist:
- Picker shows three chips: `[Claude] [Gemini] [Codex]` — verify they don't wrap on the phone's screen width (Brandon's Samsung Galaxy should be fine; if a future operator uses a smaller phone, may need to switch the chip row to a horizontally-scrollable LazyRow)
- Tap Codex chip → live-dot column updates (clears since no Codex sessions exist)
- Tap an app row → top bar reads "Codex · {App Name}"
- Codex boot banner renders within ~5s
- Try a basic command (e.g. `/help` or whatever Codex's slash-command equivalent is)
- Background, reopen, reattach — scrollback intact
- Switch back to Claude and Gemini — confirm those still work
- If Codex MCP support is wanted (Step 4 below), verify the BlackBox MCP tools are reachable from inside Codex (slash command varies — `/mcp` is conventional but Codex's may differ)

**Step 3: Append a Phase 11 block to `Orchestrator/cli_agent/STATUS.md`** mirroring the Phase 10 format. Include:
- Codex install method + binary path
- `POST_ATTACH_HOOKS` decision (entry added or deliberately absent + WHY from Task A.2)
- Smoke test byte counts (Claude / Gemini / Codex Phase A)
- Any per-provider quirks discovered (auth env-var propagation, first-run wizards, etc.)
- Whether MCP was wired (Step 4 below) and how

**Step 4: (Decision point) Add BlackBox MCP server to Codex config**

Phase 10 added `mcpServers.blackbox` to `~/.gemini/settings.json` so Gemini sees the BlackBox MCP. Codex CLI's MCP support story:

```bash
# Find Codex's MCP config location (likely one of these)
ls ~/.codex/ 2>/dev/null
codex mcp list 2>/dev/null || codex --help | grep -i mcp
```

If Codex supports MCP (it almost certainly does — most modern AI CLIs do), mirror the same pattern from `~/.claude/mcp.json` into Codex's config. Verify with `codex mcp list` reporting the BlackBox server as connected (Gemini's eager-probe behavior may or may not apply).

If Codex does NOT support MCP yet, document the gap and skip — operator can revisit when/if Codex adds it.

**Step 5: Mint a snapshot via `/snapshot-dev`** (operator: `Brandon`). Search hint phrases should include "Codex CLI bridge integration", "third provider added to CLI Agent stack", and "Phase 11".

---

## Track B — Operator Pill → Compose WindowInsets-style CompositionLocal

> **STATUS 2026-05-09:** Track B.1 SHIPPED (commit `afb974e` on branch cli-agent — `LocalOperatorPillHeight` CompositionLocal defined with 96dp default). Tasks B.2-B.5 BLOCKED by substantial unrelated WIP in `TopBar.kt` (367 lines changed; in-flight TopBar UI revamp adding AlertDialog + OutlinedTextField) and `NativeMainActivity.kt` (686 lines changed). These are precisely the two files B.2 needs to edit (BlackBoxTopBar definition + the only call site). Surgical edits with `git commit --only` would still bundle hundreds of lines of WIP into our commits — unsafe. Operator must commit or revert that WIP before B.2-B.5 can land cleanly. Resume in a future session once TopBar/MainActivity baseline is stable.

Replace every hardcoded `top = 96.dp` (and similar pill-clearing offsets) with a measured `LocalOperatorPillHeight` CompositionLocal that's set by `BlackBoxTopBar` and consumed by downstream screens.

### Task B.1: Define `LocalOperatorPillHeight` CompositionLocal

**Files:**
- Create: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/ui/insets/OperatorPillInsets.kt`

**Step 1: Write the CompositionLocal**

```kotlin
package com.aiblackbox.portal.ui.insets

import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * The measured height of the floating operator pill bubble plus a small
 * padding margin. Screens that render content near the top of the
 * picker should consume this value as a top offset so their content
 * sits cleanly below the pill rather than being obscured by it.
 *
 * Default is 96.dp — the legacy hardcoded number. Provided by
 * `BlackBoxTopBar` via `CompositionLocalProvider` once the pill has
 * been measured. Screens that aren't descendants of the TopBar fall
 * back to the default.
 */
val LocalOperatorPillHeight = compositionLocalOf<Dp> { 96.dp }
```

**Step 2: Build to verify**

```bash
./gradlew :app:compileDebugKotlin
```

Expected: BUILD SUCCESSFUL.

**Step 3: Commit**

```bash
git add app/src/main/java/com/aiblackbox/portal/ui/insets/OperatorPillInsets.kt
git commit --only app/src/main/java/com/aiblackbox/portal/ui/insets/OperatorPillInsets.kt -m "$(cat <<'EOF'
feat(android/insets): add LocalOperatorPillHeight CompositionLocal

Default 96dp matches the legacy hardcoded pill-clearing offsets used
by AppFolderPicker (and presumably others). BlackBoxTopBar will
populate it with the measured value in a follow-up commit; screens
will migrate from hardcoded numbers to consuming this local in
subsequent commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B.2: Wire `BlackBoxTopBar` to measure the pill and publish via the CompositionLocal

**Files:**
- Modify: `app/src/main/java/com/aiblackbox/portal/ui/components/TopBar.kt`

**Step 1: Add the measurement and provider**

Find the `Box` modifier at the top of `BlackBoxTopBar` Composable. Wrap the centered pill `Column` with `Modifier.onSizeChanged { ... }` to capture its measured height. Then wrap the *content of the screen below the topbar* with `CompositionLocalProvider(LocalOperatorPillHeight provides …)`.

Important: the TopBar today is rendered as a sibling of the screen content (probably in the navigation root layout), not as a parent. To make `LocalOperatorPillHeight` flow to screens, you'll need to either:
- Move the `CompositionLocalProvider` up to a common ancestor (likely `PortalActivity` or wherever `setContent { ... }` is) and have `BlackBoxTopBar` push its measured height into a `mutableStateOf` that the provider reads, OR
- Restructure so `BlackBoxTopBar` and screens are both children of a `Scaffold`-like wrapper that owns the `CompositionLocalProvider`

**Spike before committing.** Run a quick experiment with `Box { TopBar(); Spacer; Picker { Text("Pill height: $LocalOperatorPillHeight.current") } }` and confirm the value flows. If it doesn't, restructure first, then proceed.

**Step 2: Add a small margin to the published value**

The pill should be COMPLETELY clear, not just kissing its bottom edge. Publish `measuredPillHeightDp + 8.dp` so screens have a comfortable gap.

**Step 3: Build + commit**

The diff and exact code depend on the spike result in Step 1. Commit with message:

```
feat(android/insets): publish measured operator pill height via CompositionLocal

BlackBoxTopBar now measures its center pill via Modifier.onSizeChanged
and publishes the height (plus an 8dp comfort margin) through
LocalOperatorPillHeight. Screens consuming the local will get the
true pill height instead of the legacy 96dp hardcoded fallback.
```

---

### Task B.3: Migrate `AppFolderPicker.AgentProviderChips` from hardcoded 96dp to `LocalOperatorPillHeight.current`

**Files:**
- Modify: `app/src/main/java/com/aiblackbox/portal/ui/cli_agent/AppFolderPicker.kt`

**Step 1: Replace the hardcoded padding**

Find:

```kotlin
.padding(start = 12.dp, end = 12.dp, top = 96.dp, bottom = 8.dp),
```

Replace with:

```kotlin
.padding(start = 12.dp, end = 12.dp, top = LocalOperatorPillHeight.current, bottom = 8.dp),
```

Add the import:

```kotlin
import com.aiblackbox.portal.ui.insets.LocalOperatorPillHeight
```

**Step 2: Build + visual verify on phone**

`./gradlew assembleDebug`, sideload, open the picker. Chips should sit just below the pill exactly as before — but if you change the pill content (e.g., switch to a longer operator name), the offset auto-adjusts.

**Step 3: Commit**

```
refactor(android/cli-agent): consume LocalOperatorPillHeight instead of hardcoded 96dp

Picker chip row now reads the measured pill height from the CompositionLocal
populated by BlackBoxTopBar. Visual result identical at the current pill
size; future pill resizes (longer operator names, accessibility text scale)
are handled automatically.
```

---

### Task B.4: Audit and migrate other screens that clear the operator pill

**Files:** TBD by audit.

**Step 1: Find every hardcoded top offset that's "approximately the pill height"**

```bash
cd "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal"
grep -rnE "top\s*=\s*(60|64|72|80|88|96|100|112)\.dp" app/src/main/java --include="*.kt"
```

Some hits will be unrelated (e.g., a header inside a feature screen). Triage manually — if the comment / context suggests "clear the operator pill," migrate to `LocalOperatorPillHeight.current`. If it's something else (e.g., specific to a nested UI element), leave it.

**Step 2: For each migration, do a small commit**

Pattern same as B.3. One commit per file unless they're tightly coupled.

**Step 3: Final visual sweep on phone**

Open every migrated screen, confirm content sits below the pill the same as before.

---

### Task B.5: Document the new pattern in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (root project CLAUDE.md, NOT the per-tool one)

Add a brief section under "Creating Web Apps" or similar:

```markdown
### Compose UI: Operator Pill Insets

Screens that render content near the top should consume
`LocalOperatorPillHeight` (in `ui/insets/OperatorPillInsets.kt`) as
top padding instead of hardcoding a number. The pill's measured
height (plus an 8dp comfort margin) is published by `BlackBoxTopBar`
via `CompositionLocalProvider`; screens that aren't descendants of
the TopBar fall back to a 96dp default.

WRONG: `Modifier.padding(top = 96.dp)` to clear the operator pill.
RIGHT: `Modifier.padding(top = LocalOperatorPillHeight.current)`.
```

Commit:

```
docs(android): document LocalOperatorPillHeight insets pattern
```

---

## Track C — Cleanup Quartet

Hygiene work that the CLI Agent stack accumulated during Phase 10. None of these change runtime behavior; they just make the code easier to maintain.

### Task C.1: Extract shared `_extended_path_dirs()` helper

**Why:** Phase 10 left two copies of the nvm-version-sort logic — one in `cli_agent_routes._resolve_provider_bin`, one in `session_manager._augmented_spawn_path`. Roughly 10 lines each. Worth DRYing now that it has two callers; will only get worse if a third consumer appears.

**Files:**
- Create: `Orchestrator/cli_agent/path_extension.py`
- Modify: `Orchestrator/routes/cli_agent_routes.py` (consume shared helper)
- Modify: `Orchestrator/cli_agent/session_manager.py` (consume shared helper)
- Modify: `Orchestrator/tests/test_cli_agent/test_provider_bin_resolution.py` (still passes)
- Modify: `Orchestrator/tests/test_cli_agent/test_post_attach_hooks.py` (still passes)

**Step 1: Write the shared helper**

```python
# Orchestrator/cli_agent/path_extension.py
"""Shared helpers for building PATH search dirs that include
nvm-installed Node version dirs. Used by both the binary resolver
(routes) and the spawn-time PATH augmentation (session manager)."""
import os
from pathlib import Path


def _ver_key(p: Path) -> tuple:
    name = p.name.lstrip("v")
    parts = name.split(".")
    try:
        return tuple(int(x) for x in parts)
    except ValueError:
        return (-1,)


def nvm_node_bin_dirs() -> list[str]:
    """Return every existing ~/.nvm/versions/node/*/bin dir in
    descending semver order (newest first). Empty list if no nvm
    install present."""
    home = Path.home()
    nvm_versions = home / ".nvm" / "versions" / "node"
    if not nvm_versions.is_dir():
        return []
    try:
        entries = list(nvm_versions.iterdir())
    except OSError:
        return []
    return [
        str(p / "bin")
        for p in sorted(entries, key=_ver_key, reverse=True)
        if (p / "bin").is_dir()
    ]


def extended_path_dirs() -> list[str]:
    """Standard PATH extension list: ~/.local/bin, /usr/local/bin,
    /usr/bin, then nvm node bin dirs in descending order. Returned as
    a list of strings so callers can compose with `os.pathsep.join`."""
    home = Path.home()
    return [
        str(home / ".local" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
        *nvm_node_bin_dirs(),
    ]
```

**Step 2: Update `_resolve_provider_bin` to consume it**

In `Orchestrator/routes/cli_agent_routes.py`, replace the entire body of `_resolve_provider_bin` with:

```python
def _resolve_provider_bin(name: str) -> str:
    from Orchestrator.cli_agent.path_extension import extended_path_dirs
    extended_path = os.pathsep.join([
        os.environ.get("PATH", ""),
        *extended_path_dirs(),
    ])
    found = shutil.which(name, path=extended_path)
    return found or name
```

(Keep the docstring above; just shrink the body.)

**Step 3: Update `_augmented_spawn_path` to consume it**

In `Orchestrator/cli_agent/session_manager.py`, replace the body of `_augmented_spawn_path` with:

```python
def _augmented_spawn_path() -> str:
    from .path_extension import extended_path_dirs
    return os.pathsep.join([os.environ.get("PATH", ""), *extended_path_dirs()])
```

**Step 4: Run the full backend suite**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
Orchestrator/venv/bin/pytest Orchestrator/tests/test_cli_agent/ -v
```

Expected: 59 PASS unchanged. The DRY refactor must be behavior-preserving. If any test fails, the helper signature drifted from one of the original implementations.

**Step 5: No commit needed (Orchestrator/ is not a git repo)**

Just leave the files in place and verify in person.

---

### Task C.2: Properly commit the unstaged TTS additions in `BlackBoxStore.kt`

**Why:** Phase 10 noted that `BlackBoxStore.kt` carries unstaged TTS API additions (`getOperatorVoice`, `setOperatorVoice`, `autoTtsEnabled`, `setAutoTtsEnabled`) that other files (`NativeMainActivity.kt`, `SettingsSheet.kt`, `SettingsViewModel.kt`) depend on for the build to succeed. Each Phase 10 commit was correct in isolation but the `cli-agent` branch is not buildable from any commit between `076c623` and the tip — only the working tree builds. This task lands those additions as a proper commit.

**Files:**
- Modify (commit): `BlackBoxStore.kt` — the unstaged content already in the working tree

**Step 1: Diff the unstaged changes to confirm they're TTS-only and not contaminated by other in-flight work**

```bash
cd "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal"
git diff app/src/main/java/com/aiblackbox/portal/data/store/BlackBoxStore.kt
```

Expected: ~20 insertions adding `getOperatorVoice`, `setOperatorVoice`, `autoTtsEnabled`, `setAutoTtsEnabled` accessors. If the diff is bigger or contains non-TTS work, STOP and ask the operator.

**Step 2: Commit just those additions**

```bash
git add app/src/main/java/com/aiblackbox/portal/data/store/BlackBoxStore.kt
git commit --only app/src/main/java/com/aiblackbox/portal/data/store/BlackBoxStore.kt -m "$(cat <<'EOF'
feat(android/store): land TTS accessors that NativeMainActivity already references

These accessors (getOperatorVoice, setOperatorVoice, autoTtsEnabled,
setAutoTtsEnabled) were sitting unstaged in the working tree;
NativeMainActivity, SettingsSheet, and SettingsViewModel reference
them at compile time. Landing them as a proper commit so the cli-agent
branch is buildable from any commit between this one and the tip.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Step 3: Verify the branch builds clean from the new tip**

```bash
git stash --keep-index || true  # stash any other unrelated working-tree noise
./gradlew clean assembleDebug
git stash pop || true            # restore
```

Expected: BUILD SUCCESSFUL.

---

### Task C.3: Properly commit the unstaged IME additions in `TerminalScreen.kt`

**Files:**
- Modify (commit): `TerminalScreen.kt` — unstaged content already in the working tree

**Step 1: Diff to characterize**

```bash
git diff app/src/main/java/com/aiblackbox/portal/ui/cli_agent/TerminalScreen.kt
```

Expected: soft-keyboard / IME / focus-handling additions. If the diff has non-IME work mixed in (unlikely but possible), STOP.

**Step 2: Commit**

```bash
git add app/src/main/java/com/aiblackbox/portal/ui/cli_agent/TerminalScreen.kt
git commit --only app/src/main/java/com/aiblackbox/portal/ui/cli_agent/TerminalScreen.kt -m "$(cat <<'EOF'
feat(android/cli-agent): land in-flight IME / soft-keyboard handling improvements

These changes were sitting unstaged in the working tree across
Phase 10; landing as a proper commit. Visual / behavioral effects:
[describe based on the actual diff]

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Fill in the `[describe based on the actual diff]` line based on what the diff actually changes.

**Step 3: Build + verify**

```bash
./gradlew assembleDebug
```

Expected: BUILD SUCCESSFUL.

---

### Task C.4: Final sweep — any other "WIP in working tree" files?

**Files:** TBD by audit.

**Step 1: Run a wider audit**

```bash
cd "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal"
git status --porcelain | grep -E "\.kt$|\.gradle$|\.xml$" | grep -v "build/" | grep -v ".gradle/"
```

This filters out the noisy build/cache pollution and surfaces any genuine source-file WIP that wasn't covered by C.2 or C.3.

**Step 2: For each surfaced file, decide:**
- Real WIP → commit per the C.2 / C.3 pattern
- Genuinely accidental modification → revert with `git checkout -- <file>`
- Ambiguous → ask the operator

**Step 3: Re-run the build from clean to confirm self-buildability**

```bash
git stash --keep-index || true
./gradlew clean assembleDebug
git stash pop || true
```

If BUILD SUCCESSFUL, the `cli-agent` branch is now self-buildable from its tip.

---

---

## Track D — Touch-to-scroll on alt-screen CLIs (added 2026-05-09 mid-session, DEFERRED to next session)

> **STATUS 2026-05-09:** Track D was investigated mid-session but deferred. Findings preserved here as design context for the next session that picks this up. Codex's `--no-alt-screen` flag was wired into a new `PROVIDER_ARGS` table in `cli_agent_routes.py` (commit not made — Orchestrator/ is not git-tracked) and verified to spawn cleanly, BUT testing on phone showed PgUp/PgDn still doesn't scroll Codex (Codex doesn't bind those keys to scroll its inline conversation). The fundamental constraint is that **Gemini and Codex don't implement keyboard scroll for their conversation history at all** — Path 1 alone helps nobody (Claude already scrolls; Gemini/Codex refuse mouse and don't bind PgUp). Path 2 has no clean win for Gemini (no flag exists) and `--no-alt-screen` for Codex is necessary but not sufficient (no key bindings on the Codex side). The Path 3 (Compose-side scrollback proxy) is the only universal fix and deserves a dedicated session with a proper ANSI parser library (e.g., investigate `com.github.bertus:android-ansi-parser` or similar). Estimated 4-6 hours of focused work for Path 3. **Next session should write a fresh dedicated plan for Path 3 implementation** — this Track D content is reference material only.



**Why this exists:** During Phase 11 phone testing, Brandon confirmed that PgUp/PgDn scrolls Claude's conversation history (because of Claude's `/tui fullscreen` hook) but does NOT scroll Gemini or Codex. Mouse-mode research showed both Gemini and Codex explicitly DISABLE mouse modes (`\x1b[?1000l \x1b[?1006l` etc), so touch-to-mouse-wheel translation won't work — they actively refuse mouse events. Phones need scroll; this is a real UX gap that affects two of three providers.

**Strategy — cascade three approaches, take whichever lands first:**

1. **Path 2 (per-provider alt-screen disable hook):** If Gemini or Codex has a built-in command/flag that disables alt-screen mode (the way Claude's `/tui fullscreen` does), add it to `POST_ATTACH_HOOKS` and PgUp/PgDn becomes the universal scroll mechanism. Cheapest fix. Investigated in D.1 + D.2.
2. **Path 1 (touch-to-PgUp gesture):** Wire Termux's `GestureAndScaleRecognizer` (already in the AAR — `com.termux.view.GestureAndScaleRecognizer`) to detect vertical swipes and send PgUp/PgDn bytes via the WebSocket. Provider-agnostic input mechanism. Works for whoever the inner CLI listens. Implemented in D.4.
3. **Path 3 (Compose-side scrollback proxy):** If Gemini or Codex still don't scroll after D.3 + D.4, intercept incoming bytes in `CliAgentWebSocket.onBytes`, maintain a separate Compose-side scrollback buffer, swap rendering to a scroll view on swipe. Provider-agnostic but invasive. Strip ANSI escapes with a real parser (don't roll your own — use a library). Implemented in D.6 if needed.

**Pre-flight:**
- Mouse-mode posture confirmed via byte-stream capture during Phase 11; documented in `Orchestrator/cli_agent/STATUS.md` Phase 11 block.
- Termux's `terminal-view-v0.118.0.aar` ships with `com.termux.view.GestureAndScaleRecognizer` for gesture detection.
- Touch handling currently in `TerminalScreen.kt` `viewClient` only handles `onSingleTapUp` (focus + show IME).

### Task D.1: Investigate Gemini's scroll options

**Files:** No code edits. Research-only.

**Step 1: Search the Gemini bundle for scroll-related code/strings**

```bash
cd /home/ai-black-box-fc/.nvm/versions/node/v20.19.6/lib/node_modules/@google/gemini-cli/bundle
grep -lE "scroll|altScreen|alternateScreen|alt-screen|noAltscreen|\\?1049" *.js | head -5
# Then inspect the most-promising file for scroll-related bindings
```

**Step 2: Try interactive scroll keybindings inside Gemini under tmux**

Manually launch `gemini` in a tmux session, type a few queries to get conversation history, then try:
- `Up arrow` and `Down arrow` (when input is empty)
- `Ctrl-B` / `Ctrl-F` (vim-style page back/forward)
- `Ctrl-U` / `Ctrl-D` (vim-style half-page)
- `Esc` then `j` / `k` (vim Normal mode)
- `Ctrl-Home` / `Ctrl-End`
- Any slash command starting with `/scroll` or `/history` (try `/help` first to enumerate)

Note which (if any) scroll the conversation.

**Step 3: Check if Gemini has a CLI flag for inline mode**

```bash
gemini --help | grep -iE "altscreen|inline|fullscreen|tui"
```

**Step 4: Document findings in D.3 prep notes**

If a slash command or CLI flag exists, that's a clean Path 2 fix. If only Ctrl-B style works, that's a Path 1 fix (just add to ExtraKeysBar + swipe-translation). If nothing works, fall through to Path 3.

### Task D.2: Investigate Codex's scroll options

Same shape as D.1 but for Codex. Bundle path:

```
/home/ai-black-box-fc/.nvm/versions/node/v20.19.6/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/codex/codex
```

Note: Codex is a native binary, so `strings` not `grep`:

```bash
strings /home/.../codex/codex | grep -iE "scroll|altscreen|alternateScreen" | head -20
codex --help | grep -iE "altscreen|inline|fullscreen|tui"
```

Try the same interactive keybindings under tmux.

### Task D.3: Add POST_ATTACH_HOOKS entries for any Path 2 wins

For each provider where D.1/D.2 found a disable-altscreen command:
- Add to `POST_ATTACH_HOOKS` in `Orchestrator/cli_agent/session_manager.py` (TDD red/green pattern from Phase 10 Task 1.3/1.4)
- Test totals stay green
- Restart blackbox.service, smoke-test the affected provider, verify PgUp now scrolls

If no command exists for either provider, skip this task and proceed to D.4.

### Task D.4: Wire touch swipe to PgUp/PgDn keystroke forwarding

**Why:** Provider-agnostic input mechanism. Works for Claude (already scrolls via PgUp), benefits any future provider that listens to PgUp, AND lays the groundwork for D.6 if Path 3 is needed.

**Files:**
- Modify: `app/src/main/java/com/aiblackbox/portal/ui/cli_agent/TerminalScreen.kt`

**Step 1: Add a vertical-swipe detector via Termux's GestureAndScaleRecognizer**

Inside `TerminalScreen`'s `viewClient: TerminalViewClient`, override `onLongPress` and friends to compose with gesture detection. The cleanest pattern: install a `GestureDetector` on the AndroidView that wraps the TerminalView, detect `onScroll(distanceY)` events.

Sketch:

```kotlin
// In the AndroidView factory block, after creating `view: TerminalView`:
val gd = GestureDetector(ctx, object : GestureDetector.SimpleOnGestureListener() {
    override fun onScroll(e1: MotionEvent?, e2: MotionEvent, distanceX: Float, distanceY: Float): Boolean {
        // Threshold scroll into discrete PgUp/PgDn pulses
        // distanceY > 0 = swiping up = scroll content down (forward in time)
        // distanceY < 0 = swiping down = scroll content up (backward in time)
        scrollAccumulator += distanceY
        val ROW_PIXELS = 50f  // approximate; calibrate per device
        while (scrollAccumulator > ROW_PIXELS) {
            ws.sendBytes(byteArrayOf(0x1b, '['.code.toByte(), '6'.code.toByte(), '~'.code.toByte()))  // PgDn
            scrollAccumulator -= ROW_PIXELS
        }
        while (scrollAccumulator < -ROW_PIXELS) {
            ws.sendBytes(byteArrayOf(0x1b, '['.code.toByte(), '5'.code.toByte(), '~'.code.toByte()))  // PgUp
            scrollAccumulator += ROW_PIXELS
        }
        return true
    }
})
view.setOnTouchListener { _, ev -> gd.onTouchEvent(ev); false }  // false = let TerminalView still handle taps
```

Two important details:
- Don't fire continuously — accumulate distance, fire one PgUp/PgDn per ~half-screen of swipe
- Return `false` from setOnTouchListener so single taps still reach `onSingleTapUp` (focus + IME)
- May need to consume the touch only when `emu.isAlternateBufferActive` so it doesn't conflict with the existing main-screen local-transcript scroll

**Step 2: Build, install, test**

For Claude: swipe down on the conversation should reveal older messages (PgUp goes back). Confirm.
For Gemini and Codex: depends on D.3 outcome — if hook applied, swipe scrolls; if not, swipe is silent.

**Step 3: Commit**

### Task D.5: Decide if Path 3 (Compose proxy) is needed

After D.3 + D.4:
- If Gemini AND Codex both scroll cleanly → Track D done, skip D.6.
- If either still doesn't scroll → proceed to D.6.

### Task D.6: (Conditional) Implement Compose-side scrollback proxy for non-scrolling providers

**Files:**
- Modify: `app/src/main/java/com/aiblackbox/portal/ui/cli_agent/CliAgentWebSocket.kt` — capture incoming bytes into a ring buffer
- Create: `app/src/main/java/com/aiblackbox/portal/ui/cli_agent/ScrollbackProxy.kt` — strip ANSI escapes, render text-only history
- Modify: `TerminalScreen.kt` — overlay scrollback view on swipe-up when current provider is in the "no-native-scroll" set

Use a real ANSI parser library (e.g., `com.github.bertus.android-ansi:ansi-parser` or similar Compose-friendly option). Don't roll your own — ANSI is bigger than people expect.

Detail design TBD pending D.5 outcome.

### Task D.7: APK build + phone test (Brandon)

Same shape as A.6 step 1+2.

Test checklist:
- Swipe up on Claude conversation → older messages scroll into view (already works via PgUp; this verifies swipe translation)
- Swipe up on Gemini → either older messages scroll (Path 2 success) OR Compose-side scrollback overlay appears (Path 3 success) OR nothing happens (failure mode — file follow-up)
- Same for Codex
- Single tap still focuses + opens IME (regression check)
- Long-press still triggers Termux's text-selection mode (regression check)

### Task D.8: STATUS.md update + snapshot

Same shape as A.6 step 3+5. Append a Phase 12 block documenting Track D's outcome including which path each provider landed on.

### Track D Estimated Effort

| Task | Est. time | Risk |
|---|---|---|
| D.1 + D.2 (investigation) | 30 min | Low — research |
| D.3 (Path 2 hooks if found) | 15 min/provider × 2 max = 30 min | Low |
| D.4 (touch-to-PgUp) | 60 min | Medium — gesture handling on Android can fight with TerminalView's existing handlers |
| D.5 (decision) | 5 min | — |
| D.6 (Compose proxy if needed) | 3-5 hours | High — ANSI stripping + scroll view + state management |
| D.7 + D.8 (test + snapshot) | 30 min | Low |
| **Total best case (no D.6)** | **~2.5 hours** | — |
| **Total worst case (D.6 required)** | **~5-7 hours** | — |

---

## Out of Scope

- **CLI Agent provider auth UX:** today operators just inherit `~/.claude/`, `~/.gemini/`, etc. If we ever want per-operator auth isolation (e.g., different Brandon-DEV vs Brandon Anthropic accounts), that's a separate plan.
- **Codex hook table additions for non-OpenAI Codex CLIs (aider, etc.):** Track A only adds the canonical Codex. Adapting for aider would be Codex's enum slug renaming + a different smoke test.
- **Operator pill insets across non-CLI-Agent screens** (e.g., chat, browser, robotics, media): Track B.4 audits and migrates as found, but doesn't enumerate every screen up front. If the audit surfaces 10+ screens, file as a follow-up rather than bundle here.
- **Backend `Orchestrator/` git initialization:** the operator chose "skip backend commits, edit + test in place" for Phase 10. That holds. Track C is Android-only commits + behavior-preserving backend refactor (no commits possible).

---

## Estimated Effort

| Track | Tasks | Est. time | Risk |
|---|---|---|---|
| A — Codex CLI Integration | 6 | ~70 min | **Medium** — bridge is provider-agnostic but Codex requires interactive auth (A.1) AND the OPENAI_API_KEY propagation through tmux is unverified — A.5's failure-mode #1 is the most-likely outcome and the plan now includes the patch shape if it triggers. Per-Codex TUI quirks mitigated by spike in A.2. |
| B — Operator Pill Insets | 5 | ~90 min | Medium — CompositionLocal flow needs a spike (B.2) to confirm the value reaches descendants |
| C — Cleanup Quartet | 4 | ~30 min | Low — refactor + landing existing WIP |
| **Total** | **15** | **~3.2 hours** | — |

Tracks are independent and can execute in any order. Recommended order if executing back-to-back:
1. **Track C first** (lowest risk; cleans the slate so subsequent commits land on a self-buildable tip)
2. **Track A second** (small surface, builds on Phase 10 patterns)
3. **Track B last** (biggest behavioral change, benefits from a clean tip)

---

## Verification Checklist (operator-confirmable before kickoff)

- [ ] **Track A — Codex install:** `npm install -g @openai/codex` is the default. Confirmed `npm view @openai/codex version` → 0.130.0 exists. Operator confirms this is the right Codex (not aider, not gh-copilot, not something else).
- [ ] **Track A — Auth method:** API key (`OPENAI_API_KEY` in `.env`) OR OAuth (`codex login` browser flow). Operator picks before A.1 starts. API key path is faster.
- [ ] **Track A — `OPENAI_API_KEY` propagation risk:** A.1 Step 4 includes a probe that explicitly verifies the key reaches a child of the bridge's tmux session. If it returns `False`, the plan documents the patch in A.5 failure-mode #1 — this is the most likely Codex-specific gotcha.
- [ ] **Track A — Codex MCP support:** A.6 Step 4 raises whether to mirror BlackBox MCP into Codex's config. Decision deferred until Codex's actual MCP CLI is checked.
- [ ] **Track B — comfort margin:** Default 8dp on top of measured pill height. Phase 10 final = 96dp total (~88dp pill + 8dp margin). Should land identical visually. Larger if more breathing room wanted.
- [ ] **Track C — TTS / IME WIP intent:** Confirm the unstaged additions in `BlackBoxStore.kt` (TTS APIs) and `TerminalScreen.kt` (IME work) are intentional and worth committing as-is, vs. abandoned experiments to revert instead.
- [ ] **Execution mode:** subagent-driven (this session, fresh subagent per task + two-stage review) OR parallel session (open separate session in worktree, batch-execute).

## Review Trail

This plan went through self-review on 2026-05-09 against the actual project state (orchestrator runtime env, npm package availability, dotenv loader path, existing config dir conventions). Findings folded back in:
- **C1** — `OPENAI_API_KEY` propagation gap → A.1 Step 4 (verification probe) + A.5 failure-mode #1 (patch shape if it fails)
- **I1** — broken cross-reference in original A.1 → removed
- **I2** — A.2 spike would silently fail without auth → A.2 Step 0 precondition added
- **I3** — Codex MCP setup → A.6 Step 4 raises as decision point
- **I4** — One-time login flow → A.1 Step 3 makes explicit
- **M1** — Loose byte-count expectation → A.5 makes "informational fingerprint, not pass criterion" explicit
- **M2** — STATUS.md path unspecified → A.6 Step 3 fully qualified to `Orchestrator/cli_agent/STATUS.md`
- **N1** — Three-chip wrap risk → A.6 Step 2 checklist now includes the verification

Architectural follow-up A1 (per-provider env-var table) deliberately deferred — only worth the refactor if Codex's auth surfaces as the unblocker AND we want to harmonize Claude/Gemini/Codex env-var handling. File a separate plan if so.
