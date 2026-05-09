# CLI Agents (Claude Code + Gemini CLI) — Android Native Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire both CLI agent providers (Claude Code + Gemini CLI) into the Android app with full WebSocket communication, identical to the web Portal.

**Architecture:** The existing `ClaudeAgentClient` already handles WebSocket communication for Claude Code via `/ws/agent/{session_id}`. Since Gemini CLI uses an identical message protocol (same JSON types, same event structure) via `/ws/gemini-agent/{session_id}`, we make the client endpoint-configurable rather than duplicating it. The `AgentViewModel` switches endpoints based on provider. The `AgentChatScreen` already renders all message types (content, thinking, tool_use, tool_result, permissions, etc.) — it just needs provider-aware banners.

**Tech Stack:** Kotlin, Jetpack Compose, OkHttp WebSocket, StateFlow, kotlinx.serialization

---

### Task 1: Make ClaudeAgentClient Endpoint-Configurable

**Files:**
- Modify: `app/src/main/java/com/aiblackbox/portal/data/agent/ClaudeAgentClient.kt`

**What to do:**
The client currently hardcodes `/ws/agent/` as the WebSocket path. Add a `wsPath` constructor parameter that defaults to `/ws/agent/` for Claude Code but can be set to `/ws/gemini-agent/` for Gemini CLI.

**Step 1:** Find the WebSocket URL construction in `ClaudeAgentClient.kt`. It looks like:
```kotlin
val wsUrl = "$wsBase/ws/agent/$sessionId"
```
Change to:
```kotlin
class ClaudeAgentClient(
    private val wsBase: String,
    private val wsPath: String = "/ws/agent"  // or "/ws/gemini-agent"
) {
    // ...
    val wsUrl = "$wsBase$wsPath/$sessionId"
}
```

**Step 2:** Verify `sendPrompt()` message format works for both. The Gemini agent handler accepts the same `{"type": "prompt", "text": "...", "operator": "...", "model": "..."}` format. No changes needed to message format.

**Step 3:** Verify all `parseMessage()` event types work for both. Both handlers emit the same types: content, thinking, tool_use, tool_result, completed, status_update, permission_request, error, etc. No changes needed.

---

### Task 2: Update AgentViewModel for Dual-Provider Support

**Files:**
- Modify: `app/src/main/java/com/aiblackbox/portal/ui/chat/AgentViewModel.kt`

**What to do:**
The ViewModel needs to know which provider is active (agents vs gemini-agents) and create the client with the correct WebSocket path. It also needs to use the correct HTTP endpoint for session management (`/agent/session/` vs `/gemini-agent/session/`).

**Step 1:** Add a `provider` parameter to `initialize()`:
```kotlin
private var currentProvider = "agents"  // "agents" or "gemini-agents"

fun initialize(origin: String, provider: String = "agents") {
    currentProvider = provider
    val wsPath = if (provider == "gemini-agents") "/ws/gemini-agent" else "/ws/agent"
    // Pass wsPath to ClaudeAgentClient constructor
}
```

**Step 2:** Update session endpoint:
```kotlin
val sessionEndpoint = if (currentProvider == "gemini-agents")
    "/gemini-agent/session/$operator"
else
    "/agent/session/$operator"
```

**Step 3:** Update `startSession()` to use the correct endpoint.

**Step 4:** Add a `switchProvider(provider: String)` method that tears down the current WebSocket and reinitializes with the new path.

---

### Task 3: Update AgentChatScreen Banner for Provider Awareness

**Files:**
- Modify: `app/src/main/java/com/aiblackbox/portal/ui/chat/AgentChatScreen.kt`

**What to do:**
The screen needs to show "Claude Code" or "Gemini CLI" in the banner based on the active provider. It also needs to pass the provider to the ViewModel on init.

**Step 1:** Add `provider` parameter to `AgentChatScreen`:
```kotlin
@Composable
fun AgentChatScreen(
    origin: String,
    operator: String,
    provider: String = "agents",  // "agents" or "gemini-agents"
    onSend: (String) -> Unit = {}
)
```

**Step 2:** Pass provider to ViewModel initialization:
```kotlin
LaunchedEffect(origin, provider) {
    viewModel.initialize(origin, provider)
}
```

**Step 3:** Update the ProviderBanner title:
- `provider == "agents"` → "Claude Code" with Anthropic purple accent
- `provider == "gemini-agents"` → "Gemini CLI" with Google blue accent

**Step 4:** Update model selector:
- Claude Code: Sonnet, Opus, Haiku options
- Gemini CLI: "Gemini CLI" single option (or model choices from Constants)

---

### Task 4: Wire Provider-Based Navigation in NavGraph

**Files:**
- Modify: `app/src/main/java/com/aiblackbox/portal/navigation/NavGraph.kt`
- Modify: `app/src/main/java/com/aiblackbox/portal/NativeMainActivity.kt`

**What to do:**
Currently `Routes.AGENT` always opens Claude Code. We need Gemini CLI to also route here but with a different provider parameter.

**Step 1:** Add a Gemini agent route:
```kotlin
object Routes {
    // existing...
    const val AGENT = "agent"
    const val GEMINI_AGENT = "gemini_agent"
}
```

**Step 2:** Add the composable in NavGraph:
```kotlin
composable(Routes.AGENT) {
    AgentChatScreen(origin = origin, operator = operator, provider = "agents")
}
composable(Routes.GEMINI_AGENT) {
    AgentChatScreen(origin = origin, operator = operator, provider = "gemini-agents")
}
```

**Step 3:** Update NativeMainActivity's auto-navigation:
```kotlin
"agents" -> Routes.AGENT
"gemini-agents" -> Routes.GEMINI_AGENT
```
This is already partially done — just verify the `gemini-agents` case routes correctly.

**Step 4:** Update SettingsSheet provider dropdown to also route `gemini-agents`:
```kotlin
"gemini-agents" -> "gemini_agent"
```

---

### Task 5: Verify and Test End-to-End

**Steps:**
1. Select "Claude Code" provider in Composer → should auto-navigate to Agent screen → WebSocket connects to `/ws/agent/{session_id}` → type prompt → see streaming response with thinking, tool_use, content
2. Select "Gemini CLI" provider in Composer → should auto-navigate to Agent screen → WebSocket connects to `/ws/gemini-agent/{session_id}` → type prompt → see streaming response
3. Test permission dialog: Set mode to "Normal", trigger a tool → permission dialog appears → approve → tool executes
4. Test reconnect: Background the app briefly → come back → session resumes
5. Test session switching: Switch between Claude and Gemini agents → each maintains its own session
6. Test from System Menu: Select "Claude Code" or "Gemini CLI" from provider dropdown → navigates correctly

---

### Task 6: Add Top Padding + X Close Button Support

**Files:**
- Modify: `AgentChatScreen.kt`

**What to do:**
The agent screen needs the same 100dp top padding for the operator pill and bottom padding for the Composer, matching all other sub-screens. The X close button is already handled globally in NativeMainActivity.

**Step 1:** Add top padding to the main Column/layout in AgentChatScreen (same pattern as other screens).

---

## Summary of Changes

| File | Change |
|------|--------|
| `ClaudeAgentClient.kt` | Add `wsPath` constructor param |
| `AgentViewModel.kt` | Provider-aware init, session endpoints, switchProvider() |
| `AgentChatScreen.kt` | Provider param, banner labels, model options, padding |
| `NavGraph.kt` | Add `GEMINI_AGENT` route |
| `NativeMainActivity.kt` | Route `gemini-agents` → `GEMINI_AGENT` |
| `SettingsSheet.kt` | Route `gemini-agents` in provider dropdown |

**Total estimated scope:** ~100-150 lines of changes across 6 files. No new files needed — the existing agent architecture is solid, just needs the Gemini endpoint wired in.
