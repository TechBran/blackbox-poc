# CLI Agent — Android MVP Design

**Date:** 2026-04-30
**Operator:** Brandon
**Phase 1:** Native Android MVP (Kotlin/Compose)
**Phase 2 (deferred):** Portal/WebView (xterm.js)
**Branch target:** TBD (worktree to be created on implementation)

---

## Goal

Add a **CLI Agent** button to the BlackBox Portal Tools menu that opens a fully interactive terminal interface to run Claude Code (Phase 1), Gemini CLI, or Codex CLI (Phase 2) on the mini-ITX BlackBox host. Operator picks an app workspace from `Apps/` (or the `Apps/` root itself for new-app scaffolding), gets a real PTY-backed terminal with full TUI fidelity — identical to running Claude Code on the host directly.

Existing headless CLI provider entries in the chat dropdown are **retired** in the same release.

---

## Architectural decisions (locked via brainstorming session 2026-04-30)

| # | Decision | Choice |
|---|---|---|
| Q1 | Renderer | **Termux native `terminal-emulator` library** in Compose (`AndroidView(TerminalView)`). xterm.js reserved for Phase 2 Portal only. |
| Q2 | Session persistence | **tmux-anchored, multi-session-per-operator.** Process survives disconnects. |
| Q3 | Folder picker scope | **Workspace allowlist = `Apps/` only.** Picker rows are registered apps from `/agent/apps`. BlackBox internals invisible/unreachable. Path validator enforced server-side. |
| Q4 | Existing headless CLI providers | **Retire** in the same release the Tools button ships. |
| Q5 | Mobile keyboard surface | **Termux-style fixed extra-keys bar:** `Esc | Tab | Ctrl | Alt | ← ↓ ↑ → | / | @ | 🎤 | -`. Long-press for sticky modifier. Auto-collapse on Bluetooth keyboard. |
| Q6 | Entry-point UX | Folder picker is the entry point. **No custom session list** — Claude Code's own `/resume` handles conversation history. tmux handles process persistence. |
| Q7 | Android back button | **Detach, never kill.** Kill is an explicit long-press in picker with confirmation. |
| Q8 | Operator visibility | **All operators** see the button. Backend session naming includes operator. |
| Q8b | Per-operator config isolation | **`CLAUDE_CONFIG_DIR=~/.claude-bbox/<operator>/`** per session. Each operator does `claude login` once, transcripts isolated. |
| ✨ | Whisper mic | 🎤 button in extra-keys bar. Tap-to-record, tap-to-send, transcript injected as **bracketed paste** (`\e[200~...\e[201~`). Reuses existing `/api/whisper` endpoint. |
| Q9 | Backend stack | **Python ptyprocess** in-process inside the FastAPI Orchestrator. No Node sidecar. |
| Q10 | Provider picker | **Env-var-controlled** (`CLI_AGENT_PROVIDERS`). Day 1 = single provider, picker hidden. Multi-provider auto-renders when var lists 2+. |
| ✨ | Apps root pick | Picker has a special top row "✦ + New app workspace" that points Claude Code at `Apps/` itself for scaffolding new apps. |
| Phase 2 | Cross-device handoff | **Yes.** Deterministic session_id + shared tmux means start on phone, continue on desktop browser, scrollback intact. |

---

## System architecture

```
┌─────────────────────────────┐         ┌───────────────────────────────────┐
│  Android MVP (Kotlin/Compose)│        │  Mini-ITX BlackBox host           │
│                             │         │                                   │
│  ┌────────────────────────┐ │         │  ┌──────────────────────────────┐ │
│  │ Tools menu             │ │         │  │ FastAPI Orchestrator (9091)  │ │
│  │  [CU] [Voice] [CLI ←]  │ │         │  │                              │ │
│  └────────────────────────┘ │         │  │  /agent/apps        (REST)   │ │
│           │                 │         │  │  /api/whisper       (REST)   │ │
│  ┌────────────────────────┐ │         │  │  /ws/cli-agent/{sid}(WS) ◄──┼─┐│
│  │ App folder picker      │ │ Tailscale│  │     │                        │ ││
│  │ ✦ + New app workspace  │ │ HTTPS+WS │  │     │ Python ptyprocess     │ ││
│  │ ● grocery-store ●live  │◄┼─────────┼──┤     ▼                        │ ││
│  │ ○ PelvicVibeAndroid    │ │         │  │  /usr/bin/tmux               │ ││
│  └────────────────────────┘ │         │  │   ├─ cli-agent-Brandon-      │ ││
│           │                 │         │  │   │   claude-grocery-store  │ ││
│  ┌────────────────────────┐ │         │  │   │     └─ claude          │ ││
│  │ Terminal screen         │ │         │  │   └─ cli-agent-Anna-...     │ ││
│  │  ┌──────────────────┐  │ │         │  │                              │ ││
│  │  │ Termux           │  │◄┼─────────┼──┤  CLAUDE_CONFIG_DIR per op   │ ││
│  │  │ TerminalView     │  │ │         │  │   ~/.claude-bbox/<op>/      │ ││
│  │  └──────────────────┘  │ │         │  └──────────────────────────────┘ │
│  │  [Esc Tab Ctrl ⌃ ⇄ /@🎤]│ │         │                                   │
│  └────────────────────────┘ │         └───────────────────────────────────┘
└─────────────────────────────┘
```

---

## Backend (Python in-process)

### Files added

```
Orchestrator/
├── cli_agent/
│   ├── __init__.py
│   ├── session_manager.py    # tmux session lifecycle + naming + reaping
│   ├── pty_bridge.py          # ptyprocess wrapper, byte forwarding
│   ├── path_validator.py      # workspace allowlist enforcement
│   └── operator_config.py     # per-operator CLAUDE_CONFIG_DIR setup
└── routes/
    └── cli_agent_routes.py    # /ws/cli-agent/{session_id} + REST helpers
```

### Session naming (deterministic)

**Format:** `cli-agent-<operator>__<provider>__<app_slug>` — e.g. `cli-agent-Brandon__claude__grocery-store`.

Field separator is `__` (double underscore) between `<operator>`, `<provider>`, and `<app_slug>` so operator names containing hyphens (e.g. `Brandon-DEV`) round-trip cleanly through `parse_session_name`. The `cli-agent-` prefix is preserved as a cosmetic marker for grep-ability in `tmux ls` output.

Apps root special slug: `_root` (leading underscore reserved, can't collide).
Operator/provider names containing `__` are rejected at `session_name()` construction time.

**Updated 2026-05-01** during Phase 1 implementation (Task 1.4 code-quality review caught the `Brandon-DEV` parse ambiguity in the original `-`-separated scheme).

### Spawn sequence

```python
env = {**os.environ, "CLAUDE_CONFIG_DIR": f"{HOME}/.claude-bbox/{operator}"}
ensure_dir(env["CLAUDE_CONFIG_DIR"])
# Idempotent: only create if not present
if not tmux_has_session(name):
    subprocess.run(["tmux", "new-session", "-d", "-s", name,
                    "-c", validated_app_path,
                    "claude"],
                   env=env, check=True)
pty = ptyprocess.PtyProcess.spawn(["tmux", "attach", "-t", name])
```

### Path validator

```python
APPS_ROOT = Path("/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Apps").resolve()

def validate(requested: str) -> Path:
    if requested in ("", "/", "."):
        return APPS_ROOT  # apps root pick
    p = (APPS_ROOT / requested).resolve()
    if not (p == APPS_ROOT or p.is_relative_to(APPS_ROOT)) or not p.is_dir():
        raise PermissionError(f"Path {requested} outside Apps/ workspace")
    return p
```

Symlinks resolved before check. `..` impossible. Anything outside `Apps/` rejected before tmux is touched.

### Byte forwarding

asyncio task pulls `pty.read()` → forwards to WebSocket as binary frames.
Second task pulls WebSocket binary frames → writes to `pty.write()`.
Text frames carry control messages (`resize`, `kill`, `paste`, `session_info`, `error`).

### Idle reaping (cron, daily)

```bash
tmux list-sessions -F "#{session_name} #{session_activity}" \
  | awk -v t=$(date -d '7 days ago' +%s) '$2 < t {print $1}' \
  | xargs -I{} tmux kill-session -t {}
```

Threshold tunable via `CLI_AGENT_IDLE_DAYS` env var (default 7).

---

## Android frontend (Kotlin/Compose)

### Dependency

```gradle
implementation("com.termux:terminal-emulator:0.118")  // LGPL, dynamic-linked
```

### Files added

```
app/src/main/.../ui/cli_agent/
├── CliAgentToolsButton.kt        # entry-point button in ToolsMenu
├── CliAgentScreen.kt              # NavHost destination
├── AppFolderPicker.kt             # Compose list with live-dot indicator
├── TerminalScreen.kt              # AndroidView(TerminalView) + extra-keys bar
├── ExtraKeysBar.kt                # Termux-pattern key bar with /, @, 🎤
├── WhisperMicButton.kt            # tap-to-record state machine
├── CliAgentWebSocket.kt           # OkHttp WebSocket client + reconnect
└── CliAgentSessionRepository.kt   # apps list cache, deep-link helpers
```

### State flow

1. `Picker` (default) — folder picker visible, fetches `/agent/apps` + `/cli-agent/sessions?op=<op>`
2. Tap row → `Connecting(app)` — opens WebSocket
3. WebSocket open → `Connected(app, session)` — TerminalView allocated, byte stream active
4. Back button → `Picker` (WebSocket closes, tmux survives)
5. Long-press in picker → `KillConfirm(app)` → DELETE → tmux killed → list refreshes

### TerminalView wiring

```kotlin
val emulator = TerminalEmulator(termiosCallback, cols=80, rows=24, transcriptRows=2000)
val terminalView = TerminalView(context, null).apply { setTerminal(/*session*/term) }
// WebSocket onMessage(bytes): term.write(bytes)
// On TerminalView keypress: term.processInput(byte) → ws.send(bytes)
```

`TerminalEmulator` handles all VT100/ANSI parsing — we never parse escape sequences ourselves.

### Extra-keys bar

Default keys: `Esc | Tab | Ctrl | Alt | ← ↓ ↑ → | / | @ | 🎤 | -`. Swipe right exposes `Home | End | PgUp | PgDn`. Long-press on `Ctrl`/`Alt` makes the modifier sticky for the next combo.

Auto-collapse when `LocalConfiguration.current.keyboard != KEYBOARD_NOKEYS` (Bluetooth keyboard).

---

## Wire protocol

### Endpoint

`wss://<host>:9091/ws/cli-agent/{session_id}?op=<operator>&provider=<claude|gemini|codex>&app=<app_slug>&cols=<n>&rows=<n>`

`session_id` is the deterministic tmux name. Mismatched query params → close with code 4003.

### Frame types

**Binary frames** = raw PTY bytes, both directions.
**Text frames** = JSON control messages.

| Direction | Type | Payload | Purpose |
|---|---|---|---|
| ↔ | `resize` | `{cols, rows}` | Soft-keyboard up/down, rotation, BT keyboard |
| → server | `kill` | `{}` | Operator-confirmed kill from picker |
| → server | `paste` | `{text}` | Server wraps in `\e[200~...\e[201~` and writes to PTY |
| ← client | `session_info` | `{state: "attaching"|"created", scrollback_bytes}` | Sent once on open |
| ← client | `error` | `{code, message}` | Non-fatal warnings |

### Reconnect strategy

OkHttp `WebSocket.Listener.onFailure` triggers exponential backoff: 250ms → 500ms → 1s → 2s, capped. "Reconnecting…" banner during gaps. tmux holds the buffer; no message loss.

---

## Folder/app picker UX

### Data sources

- `GET /agent/apps` (existing) — registered apps under `Apps/`, sorted by recent activity
- `GET /cli-agent/sessions?op=<op>` (new) — `[{session_id, app, last_activity}]` for the operator

Cross-reference produces the green-dot indicator and "active Xm ago" timestamps.

### Layout

```
┌──────────────────────────────────────────────────┐
│ ✦ + New app workspace             /Apps          │
│   scaffold a new app, work across apps           │
├──────────────────────────────────────────────────┤
│ ● grocery-store                  active 4m ago   │
│ ○ PelvicVibeAndroid                              │
│ ● Orchestrator-fix              active 2h ago   │
└──────────────────────────────────────────────────┘
```

### Interactions

- Tap row → connect (attach if green dot, create if not)
- Long-press green-dot row → action sheet: `Reconnect | Kill session`
- Kill → confirmation: *"Kill grocery-store? In-flight commands will terminate."*
- Long-press grey-dot row → no actions
- Empty state → helper card pointing to `/register-app` slash command

### Provider stripe

Hidden when `CLI_AGENT_PROVIDERS` lists 1 provider. Visible as `[Claude] [Gemini] [Codex]` toggle bar when 2+.

---

## Whisper mic integration

State machine: `idle 🎤 → recording 🔴 → transcribing ⏳ → idle 🎤`. Long-press during recording cancels.

Recording: `MediaRecorder` AAC/M4A 64kbps mono 16kHz, 60s cap with 50s warning toast.

On stop: POST blob to `/api/whisper?operator=<op>` (existing endpoint), receive transcript, send as text frame:

```json
{"type": "paste", "text": "<transcript>"}
```

Server wraps in bracketed paste before writing to PTY:

```python
async def handle_paste(pty, text: str):
    framed = b"\x1b[200~" + text.encode("utf-8") + b"\x1b[201~"
    await pty.write(framed)
```

Edge cases: queue transcript locally if WebSocket disconnects mid-transcribe; toast on Whisper API failure; typing during recording remains independent.

---

## Phase 2 — Portal/WebView

### Files added (Portal browser)

```
Portal/
├── modules/cli-agent.js          # entry-point, lazy-loaded
├── styles/features/_cli-agent.css
└── vendor/
    ├── xterm.min.js
    └── xterm-addon-fit.min.js
```

### Backend changes

**None.** Same `/ws/cli-agent/{session_id}` route handles both Android and Portal clients.

### Renderer integration

```javascript
const term = new Terminal({fontFamily: 'monospace', fontSize: 14, scrollback: 5000});
const fit = new FitAddon();
term.loadAddon(fit); term.open(container); fit.fit();

const ws = new WebSocket(`wss://.../ws/cli-agent/${sid}?op=...&app=...&provider=claude`);
ws.binaryType = 'arraybuffer';
ws.onmessage = (e) => {
    if (typeof e.data === 'string') handleControl(JSON.parse(e.data));
    else term.write(new Uint8Array(e.data));
};
term.onData((data) => ws.send(new TextEncoder().encode(data)));
term.onResize(({cols, rows}) => ws.send(JSON.stringify({type: 'resize', cols, rows})));
```

### Cross-device handoff

Deterministic `session_id` + shared tmux = start on Android, continue on desktop browser. Same scrollback, in-flight commands intact.

---

## Testing & validation

### Backend tests (`Orchestrator/tests/test_cli_agent.py`)

- `test_path_validator_rejects_traversal`
- `test_path_validator_allows_apps_root`
- `test_session_naming_deterministic`
- `test_attach_or_create_idempotent`
- `test_operator_config_dir_isolated`
- `test_kill_session_removes_tmux`
- `test_idle_reaper_kills_old_sessions`
- `test_websocket_validates_query_params`

### Manual validation matrix (real Android device)

| Scenario | Expected |
|---|---|
| Cold open → CLI Agent → grocery-store | Terminal in <1.5s, Claude Code prompt visible |
| `ls` + Enter | Output renders with colors and box-drawing |
| Long command + Whisper paste mid-line | Bracketed paste lands as single block |
| Esc on extra-keys bar | Claude Code interrupts |
| Background 30s, return | Scrollback repaints, session intact |
| Wi-Fi off mid-Bash, restore | Reconnect banner, output during gap visible |
| Long-press → Kill | Confirmation, row → grey dot |
| Switch operator → first launch | `claude login` flow inside terminal |
| Portal desktop → same session | Cross-device handoff, scrollback intact |
| Bluetooth keyboard paired | Extra-keys bar auto-collapses |

### Pre-merge gates

- All backend tests pass
- Manual matrix checked off on real Android phone (not emulator)
- One end-to-end "create new app from `Apps/` root → register → appears in Portal" workflow validated

### Snapshot mint

`/snapshot-dev Brandon` documents the full feature for future-Claude memory.

---

## Out of scope (Phase 1)

- Gemini CLI / Codex CLI providers (env-var-gated for Phase 2+)
- Portal/WebView frontend (Phase 2)
- Per-operator API budgeting / quota enforcement
- Workspace expansion beyond `Apps/` (e.g., Beast `ugv_ws/`) — future config addition
- Voice control of terminal beyond Whisper-to-paste (e.g., Gemini Live on terminal output)
- Session sharing between operators
- Session bookmarks / pinned sessions in picker

---

## Risks & open questions

| Risk | Mitigation |
|---|---|
| `claude login` flow inside Termux TerminalView might fail to render OAuth URL cleanly | Validate during manual matrix; fallback is to log in via host directly and copy auth files into per-operator `CLAUDE_CONFIG_DIR` |
| tmux server crash kills all sessions simultaneously | systemd unit for `tmux server` if it becomes a recurring issue; not pre-emptively built |
| LGPL terminal-emulator licensing on Play Store | Standard dynamic-link discipline + About-screen credit; Termux itself ships on F-Droid for the same reason — follow their distribution model if Play becomes a concern |
| Long Whisper transcripts (>500 chars) could overwhelm the bracketed-paste handling in Claude Code's input | Cap transcript size at 2000 chars; warn operator if hit |
| Multi-operator conversation cross-pollution if `CLAUDE_CONFIG_DIR` is misconfigured | Backend always sets the env var; never inherits from process env. Test covers this. |
