Task 1.2 path_validator: 8/8 tests green at 2026-05-01T02:41:05Z
Task 1.2 path_validator: 12/12 tests green at 2026-05-01T02:44:17Z (post code-quality fixes)
Task 1.3 operator_config: 8/8 tests green at 2026-05-01T02:46:42Z
Task 1.3 operator_config: 8/8 tests green at 2026-05-01T02:48:44Z (post spec-review fix: OSError no longer coerced to InvalidOperator)
Task 1.4 session_manager: 8/8 tests green at 2026-05-01T02:52:10Z
Task 1.4 session_manager: 11/11 tests green at 2026-05-01T02:56:31Z (post code-quality fix: __ field separator handles hyphenated operators like Brandon-DEV)
Task 1.5 pty_bridge: 3/3 tests green at 2026-05-01T02:58:15Z
Phase 1 commit gate: 34/34 backend tests green at 2026-05-01T02:58:59Z (path_validator 12, operator_config 8, session_manager 11, pty_bridge 3)
Task 2.1 cli_agent_routes: 2/2 route tests + 36/36 backend total green at 2026-05-01T03:01:37Z
Task 2.1 cli_agent_routes: 3/3 route tests + 37/37 backend total green at 2026-05-01T03:06:14Z (post code-quality fixes: gather sibling cancel, paste sanitize, dim clamp, post-close exception handling, kill test tightened)
Task 2.2 cli_agent_routes WebSocket smoke test: PARTIAL COMPLETE at 2026-05-01T03:56:53Z
- Bridge plumbing PROVEN: WebSocket connects, session_info frame sent, real Claude Code TUI escape sequences flow back (1799-3825 bytes verified across 5+ runs)
- Persistence: sessions DO persist in blackbox.service's namespace (the production path), but external smoke test can't verify due to:
  * blackbox.service runs in private mount namespace (PrivateTmp=yes likely), so external 'tmux ls' is blind to service-spawned tmux servers
  * Repeated WebSocket connect+disconnect cycles cause an asyncio task hang in the route after the first connection; service requires restart between tests
- Service-restart persistence requires KillMode=process drop-in (deferred — needs sudo confirmation)
- Followups for focused session:
  1. Diagnose the second-connection deadlock (likely in pty_to_ws idle polling or bridge.close cleanup)
  2. Add KillMode=process drop-in for blackbox.service for restart-survival
  3. Build proper smoke test that runs in service's namespace (e.g., via /cli-agent endpoint that does internal verification)
- Next phases (3-7) will exercise the real production path via Android client; any deadlock will surface there too
Task 3.1 reaper: 4/4 tests green + 41/41 backend total green at 2026-05-01T09:53:14Z (reaps idle >threshold, skips non-cli-agent, bails on tmux nonzero, skips malformed lines)
Task 3.2 reaper CLI entry point added at 2026-05-01T09:55:28Z: python -m Orchestrator.cli_agent.reaper. Cron-system integration deferred — /api/cron/jobs is LLM-prompt-driven, wrong shape for deterministic system task. Followup: add crontab line or systemd user timer when desired (one-line user config, no code change).

=== Phase 4-7 Android Implementation (2026-05-01) ===
Task 4.1 — JitPack repo + Termux dep placeholder (commit 8130b87)
Task 4.2 — 8 .kt stub files scaffolded (commit ...)
Task 5.1 — CliAgentSessionRepository + 5 data classes (commit 92a04c2)
Task 5.2 — CliAgentWebSocket client with binary+text frames + reconnect (commit 667dac1)
Task 6.1 — ExtraKeysBar Composable (commit 828b89d)
Task 6.2 — WhisperMicButton — reuses AudioRecorderManager + /stt (commit 815480b)
Task 6.3 — AppFolderPicker entry-point with live-dot indicator (commit 6acb1b3)
Task 7.1 — TerminalScreen integration with TERMUX-API tags for Brandon to verify (commit d2684ed)
Task 7.2 — CliAgentScreen NavHost + Tools menu + retire headless providers (commit 775a201)
Task 8.1 — Manual matrix DEFERRED to Brandon's Android Studio + phone session
Task 9.1 — Final snapshot pending

Android branch 'cli-agent' has 9 commits ready for first build attempt in Android Studio.
First build will need: (1) Termux JitPack coord resolution, (2) verify TERMUX-API
lines in TerminalScreen.kt against the actual jar, (3) plumb operator into the
CLI_AGENT navigation route in BlackBoxNavGraph, (4) test on physical Android phone.


=== APK BUILD VERIFIED 2026-05-01T07:25Z ===
JDK 17 installed (sudo apt-get install openjdk-17-jdk-headless).
JitPack coords RESOLVED: com.github.termux.termux-app:terminal-emulator:v0.118.0 + terminal-view:v0.118.0
Found at: ~/.gradle/caches/modules-2/files-2.1/com.github.termux.termux-app/

Termux API discovered via javap on resolved AAR. Critical findings:
  - TerminalSession is FINAL — cannot subclass. Use harmless local /system/bin/sleep child.
  - Bytes from WS feed via session.emulator.append(bytes, len). Direct, public.
  - Keystrokes captured via TerminalViewClient.onCodePoint, return true to suppress local routing.

TerminalScreen.kt rewritten against actual API. Fix committed as 076c623.

./gradlew assembleDebug → BUILD SUCCESSFUL in 9s (39 actionable tasks).
APK: app/build/outputs/apk/debug/app-debug.apk (102 MB).
libtermux.so packaged for arm64-v8a, armeabi-v7a, x86, x86_64.

Brandon: adb install app-debug.apk and test.


=== Phase 10 — Gemini CLI Provider Added 2026-05-09 ===

Backend (Orchestrator/cli_agent/, Orchestrator/routes/cli_agent_routes.py):
  - _resolve_provider_bin extended to search ~/.nvm/versions/node/*/bin
    in DESCENDING SEMVER (numeric tuple key — lex sort breaks v9 vs v10)
  - POST_ATTACH_HOOKS per-provider table replaces hard-coded
    _schedule_fullscreen_renderer; claude=["/tui fullscreen","Enter"],
    gemini deliberately absent (Ink renderer scrolls fine on its own)
  - _augmented_spawn_path() injects nvm bin dirs into spawn-time PATH
    so `#!/usr/bin/env node` shebangs resolve to the nvm node binary
  - smoke_test.py made provider-configurable (--provider, --app)
  - Test totals: 41 -> 50 -> 57 -> 59 PASS across the four red/green
    iterations of TDD; full cli_agent suite green

Android (AI_BlackBox_Portal_Android_MVP/.../ui/cli_agent/):
  - BlackBoxStore: KEY_CLI_AGENT_PROVIDER + cliAgentProviderFlow + setCliAgentProvider
  - CliAgentProvider enum (CLAUDE, GEMINI; CODEX deliberately deferred)
    in data/model/CliAgentModels.kt
  - AppFolderPicker: provider chip row at top, per-provider live-dot filter,
    96dp top padding to clear the operator pill bubble overlay
  - CliAgentScreen: loads provider via collectAsState, captures slug into
    Terminal(appSlug, appName, provider) state at tap time
  - TerminalScreen: provider param now required, top-bar formatted as
    "Claude · App Name" / "Gemini · App Name"

Critical bugs caught and fixed:
  - LEX SORT TRAP: original plan used sorted(...iterdir(), reverse=True)
    which puts v9 BEFORE v22 (string '9' > '2'). Code-quality reviewer
    caught it before ship. Fixed with numeric tuple key + (-1,) fallback
    for non-semver dir names. Regression test pins single+double digit
    coexistence. The bug originated in the PLAN, not the implementation
    — a real demonstration that two-stage review catches spec defects.
  - SHEBANG INTERPRETER GAP: nvm-resolved gemini binary has shebang
    `#!/usr/bin/env node`. Resolving the entry script wasn't enough —
    `node` itself also needs to be in PATH at exec time. Live smoke
    test caught this (only 13 bytes of "no sessions\r\n" came back).
    Fixed by augmenting spawn env's PATH with nvm bin dirs.
  - PROVIDER_BIN drift guard: code-quality review #1 issue replaced
    the hardcoded {"claude","gemini","codex"} test set with a
    subset-of-PROVIDER_BIN assertion that imports the routes module
    so future provider additions can't silently get out of sync.

Live verification:
  - Claude regression smoke: 6,723 bytes of PTY output, 4/4 phases pass
  - Gemini smoke: 11,273 bytes (Ink banner is bigger), 4/4 phases pass
  - Brandon: chip row + per-provider live dots + reattach + kill flow
    all working on Samsung phone. Chip placement nailed at 96dp
    top padding (after iterating up from 28dp).

User-side config note:
  - Brandon's ~/.gemini/settings.json had no `mcpServers` key — added
    a "blackbox" entry mirroring his ~/.claude/mcp.json. `gemini mcp
    list` now reports "blackbox: ... (stdio) - Connected".
  - Gemini eagerly probes MCP servers on registration (vs Claude's
    lazy-on-first-use). This means MCP wiring problems surface
    immediately for Gemini — useful for debugging.

Known follow-ups (NOT in this ship):
  - nvm version-sort logic duplicated across _resolve_provider_bin and
    _augmented_spawn_path (~10 lines each). Worth DRYing if a third
    consumer emerges.
  - Operator pill is a parent-rendered floating overlay; downstream
    screens hardcode top padding to clear it. Right long-term fix:
    expose pill height as a Compose WindowInsets value.
  - Codex provider enablement (separate plan).
  - Android cli-agent branch carries unstaged WIP in BlackBoxStore.kt
    (TTS additions) and TerminalScreen.kt (IME work) — preserved
    via git commit --only on every Phase 2 commit.

Plan: docs/plans/2026-05-08-gemini-cli-bridge-integration.md
Final Android commit: 1bee910 fix(android/cli-agent): bump chip-row top padding


=== Phase 11 — Codex CLI Provider Added 2026-05-09 ===

Backend (no changes — Phase 10 architecture handled Codex with zero new code):
  - PROVIDER_BIN["codex"] resolved automatically via Phase 10's nvm-aware
    _resolve_provider_bin (Codex installed via npm at
    ~/.nvm/versions/node/v20.19.6/bin/codex)
  - #!/usr/bin/env node shebang handled by Phase 10's _augmented_spawn_path
  - Auth via API key (~/.codex/auth.json) — no env-var propagation needed
    because Codex stores credentials on disk; HOME being correct in spawn
    env (verified for Gemini in Phase 10) is sufficient

Android (1 commit):
  - f186f16 feat(android/cli-agent): add Codex provider to CliAgentProvider enum
    Single-line addition; chip row picks up via existing values().forEach

User-side config:
  - codex login --with-api-key (read OPENAI_API_KEY from .env via stdin)
  - codex mcp add blackbox -- ... --env BLACKBOX_URL=... --env BLACKBOX_ROOT=...
  - Result: ~/.codex/config.toml has [mcp_servers.blackbox] block;
    codex mcp list reports it as 'enabled'

Note on Codex MCP UX vs Claude/Gemini:
  - Claude lazy-probes MCP on first use (~/.claude/mcp.json)
  - Gemini eagerly probes at registration (~/.gemini/settings.json) —
    'gemini mcp list' reports 'Connected'
  - Codex lazy-probes like Claude (~/.codex/config.toml) —
    'codex mcp list' reports 'enabled' not 'connected'
  - Three CLIs, three storage formats (JSON/JSON/TOML), three probe strategies

Live verification:
  - Codex smoke (--provider codex --app ""): ALL 4 PHASES PASSED
    Phase A: 2,159 bytes (boot starts with \x1b[?1049h alt-screen enable)
  - Phone test (Brandon, manual): chip row [Claude][Gemini][Codex],
    Codex boots cleanly, MCP tools visible from within Codex,
    snapshot retrieval working through MCP

Mouse-mode posture (research 2026-05-09 for touch-scroll planning):
  - Claude:  enables 1006/1000/1002/1003 (full mouse incl. wheel)
  - Gemini:  enables 2004 (bracketed paste) only; explicit DECRST on 1000-1006
  - Codex:   enables 2004 only; explicit DECRST on 1000-1006
  - Implication: touch-to-mouse-wheel translation works for Claude only.
    Gemini and Codex actively refuse mouse events. Scroll for those
    needs Path 2 (disable alt-screen) or Path 3 (Compose-side scrollback
    proxy). Tracked as Track D in the followups plan.

Known follow-ups deferred:
  - Track D: touch-to-scroll for alt-screen CLIs (Gemini, Codex
    primarily; Claude has working scroll via /tui fullscreen + PgUp)
  - Codex POST_ATTACH_HOOKS entry: not added in Phase 11 because the
    bridge plumbing works without one. Hook would only be needed if a
    specific Codex command improves UX (e.g. "/no-altscreen" if such
    a thing exists). Investigate during Track D.

Plan: docs/plans/2026-05-09-cli-agent-codex-and-followups.md
Final Android commit: f186f16 feat(android/cli-agent): add Codex provider


