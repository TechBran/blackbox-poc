# Android Context Retrieval Pipeline Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restore snapshot/checkpoint/semantic/keyword retrieval to every LLM-facing path on the Android Kotlin MVP so models answer with the same context awareness the web Portal has — and surface that provenance in the Android UI.

**Architecture:** Unify all five backend prompt-dispatch paths (`/chat/stream`, `realtime_routes.py`, `gemini_live_routes.py`, `grok_live_routes.py`, `agent_routes.py` + `gemini_agent_routes.py` + `gemini_cu_routes.py`) through one shared retrieval builder, then upgrade the Android client to parse & render the provenance payload across every transport (SSE and WebSocket).

**Tech Stack:** FastAPI (Python), Kotlin/Android (Jetpack Compose, kotlinx.serialization), SSE + WebSockets, existing retrieval helpers in `Orchestrator/fossils.py`.

---

## Invariant: Per-Operator Scoping Is Mandatory

All retrieval MUST be scoped to the request's operator — identical to how `/chat/stream` does it today. This invariant applies everywhere in this plan:

- Snapshots are stored with an operator tag in the volume file; the `*_for_operator` helpers filter by that tag at read time. Semantic retrieve takes `operator=operator` as a keyword arg.
- **Every call to `build_fossil_context(user_text, operator)` MUST pass a non-empty operator string derived from the request** (WS handshake message, POST body, or SSE query param). Never hard-code "Brandon" in server code. Never default silently — if no operator is provided, fall back to `USERS_DEFAULT` *explicitly* (same as `chat_routes.py:6048, 6134`) and log it.
- Voice/agent routes already read operator from the WS handshake — verify it before retrieval runs.
- The tests in Tasks 2-4 MUST include a cross-operator isolation assertion: a query against operator "Brandon" returns Brandon's SNAP-IDs and does NOT return any other operator's IDs. The volume has ≥12 operators per the service state dump (`[STATE] Saved operator state for 12 operators`), so this is easily testable.

Add this assertion helper to `tests/test_context_builder.py`:
```python
def _no_operator_leak(snap_ids: list[str], operator: str):
    """SNAP IDs carry date-sequence format; operator is inside the body, not the ID.
    Use get_snapshot_by_id() to verify each returned snapshot has the right operator tag."""
    from Orchestrator.fossils import get_snapshot_by_id
    for sid in snap_ids:
        snap = get_snapshot_by_id(sid)
        assert snap is not None, f"{sid} not found"
        assert snap["metadata"]["operator"] == operator, \
            f"{sid} belongs to {snap['metadata']['operator']}, leaked into {operator}'s retrieval"
```

And a positive cross-operator test:
```python
def test_brandon_retrieval_does_not_return_other_operators_snapshots():
    _, prov = build_fossil_context(user_text="tool vault", operator="Brandon")
    all_ids = prov["recent"] + prov["keyword"] + prov["semantic"] + prov["checkpoint"]
    _no_operator_leak(all_ids, "Brandon")
```

---

## Baseline: What We Already Know (Investigation Evidence)

Confirmed via live probes against the running Orchestrator (pid 529593, port 9091) on 2026-04-24:

| Path | Endpoint | Recent | Keyword | Semantic | Checkpoint |
|---|---|---|---|---|---|
| Portal-shape POST | `POST /chat/stream` | ✅ 5 | ✅ 3 | ✅ 5-6 | ✅ 2 |
| Android-shape POST | `POST /chat/stream` | ✅ 5 | ✅ 3 | ✅ 6 | ✅ 2 |
| Voice (Gemini Live) | `WS /gemini-live/...` | ⚠️ 2 | ❌ 0 | ❌ 0 | ⚠️ 1 |
| Voice (OpenAI Realtime) | `WS /realtime/...` | ⚠️ 2 | ❌ 0 | ❌ 0 | ⚠️ 1 |
| Voice (Grok Live) | `WS /grok-live/...` | ⚠️ 2 | ❌ 0 | ❌ 0 | ⚠️ 1 |
| Agent (Claude) | `WS /ws/agent/{sid}` | ❌ 0 | ❌ 0 | ❌ 0 | ❌ 0 |
| Agent (Gemini) | `WS /ws/gemini-agent/{sid}` | ❌ 0 | ❌ 0 | ❌ 0 | ❌ 0 |
| Agent (CU) | Gemini CU routes | ❌ 0 | ❌ 0 | ❌ 0 | ❌ 0 |

**Android UI gaps** (from `ChatViewModel.kt`, `ChatMessage.kt`, `ChatBubble.kt`):
1. `Provenance` data class missing `semantic` field.
2. SSE `"provenance"` event stored as raw JSON string in `UiMessage.provenance: String?` — never deserialized.
3. `ChatBubble` never renders provenance.
4. `saveConversation()` builds `SaveRequest` without passing provenance back.

---

## Task 0: Worktree & branch setup

**Files:** none (project is not a git repo per env note — skip if no git)

**Step 1:** Verify no uncommitted work in Android project that the plan will touch.

Run:
```bash
cd "/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc" && \
  ls "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/" | head
```
Expected: directories listed, no errors. Confirm we have write access.

**Step 2:** If the project is under git in the future, create branch `fix/android-context-retrieval`. For now, proceed without a branch.

---

## Task 1: Capture a reproducible evidence baseline

**Goal:** Lock in a before/after diff so we can prove the fix worked end-to-end.

**Files:**
- Create: `docs/plans/2026-04-24-android-context-retrieval-fix.evidence.md`

**Step 1:** Write `evidence.md` with the timestamped `[STREAM CONTEXT]` samples already captured in this investigation (the 17:13, 17:38, 17:52 entries from the probes) and the per-endpoint retrieval table above.

**Step 2:** Run 3 probes and append results (operator="Brandon", provider="gemini"):

```bash
# Text chat via /chat/stream
timeout 6 curl -sN -X POST http://localhost:9091/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"BASELINE-TEXT what did we work on"}],"operator":"Brandon","provider":"gemini"}' \
  > /tmp/baseline_text.sse 2>&1 || true
grep -a '^event:\|^data:' /tmp/baseline_text.sse | head -5
```
Expected: `stream_start` event carrying a `provenance` dict with all four arrays populated.

```bash
# Sample the server log for STREAM CONTEXT for each mode
sudo journalctl -u blackbox.service --since "1 minute ago" --no-pager | \
  grep -E "STREAM CONTEXT|GEMINI-LIVE|REALTIME|GROK-LIVE|AGENT" | tail -30
```
Expected: see `[STREAM CONTEXT]` lines for `/chat/stream` only. Voice/agent would emit their own markers (added in later tasks).

**Step 3:** Commit the baseline evidence file (skip if no git).

---

## Task 2: Extract a shared retrieval builder in the backend

**Files:**
- Create: `Orchestrator/context_builder.py`
- Modify: `Orchestrator/routes/chat_routes.py:5883-6025` — replace `build_streaming_context` body with call to the shared builder

**Why a new module:** Four other route files already import retrieval helpers directly and re-implement half the pipeline. Extracting one source of truth prevents future drift (cf. the memory note about `chat_routes.py` duplicating `blackbox_tools.py` tool schemas — same anti-pattern).

**Step 1: Write the failing test** — `tests/test_context_builder.py`

```python
import json
from Orchestrator.context_builder import build_fossil_context

def test_build_fossil_context_populates_all_four_sources():
    # Uses the real volume; this is an integration probe
    text_block, provenance = build_fossil_context(
        user_text="tool vault recent work",
        operator="Brandon",
    )
    assert isinstance(provenance, dict)
    assert set(provenance.keys()) == {"recent", "keyword", "semantic", "checkpoint"}
    # Brandon has > 6000 snapshots; all four lists should populate for any prompt
    assert len(provenance["recent"]) >= 1, "recent should not be empty for Brandon"
    assert len(provenance["checkpoint"]) >= 1, "checkpoint should not be empty"
    assert "===" in text_block, "context text must contain at least one section marker"

def test_build_fossil_context_empty_query_still_gives_recent_and_checkpoint():
    _, prov = build_fossil_context(user_text="", operator="Brandon")
    assert prov["keyword"] == []
    assert prov["semantic"] == []
    assert len(prov["recent"]) >= 1
    assert len(prov["checkpoint"]) >= 1
```

**Step 2: Run test to verify it fails**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc && \
  Orchestrator/venv/bin/pytest tests/test_context_builder.py -v
```
Expected: `ModuleNotFoundError: No module named 'Orchestrator.context_builder'`.

**Step 3: Write the minimal implementation** — `Orchestrator/context_builder.py`

Copy the body of `build_streaming_context` (`chat_routes.py:5889-5947`), but:
- Signature: `build_fossil_context(user_text: str, operator: str) -> tuple[str, dict]`
- Accept pre-extracted `user_text` (caller's job to extract from messages — keeps this module transport-agnostic).
- Return `(fossil_context_str, provenance_dict)` — do NOT build the final message list; that's caller-specific.
- Keep the same config keys (`RF=5`, `KF=4`, `SF=8`, `ST=0.7`, `CP=2`, `CAP=10000`, `MAX_TOTAL_CONTEXT_CHARS=30000`).
- Keep the same `[STREAM CONTEXT]` prints but behind a `log_prefix` parameter that defaults to `"[CONTEXT]"` so voice/agent can tag their own logs.

**Step 4: Run tests to verify pass**

```bash
Orchestrator/venv/bin/pytest tests/test_context_builder.py -v
```
Expected: both tests pass.

**Step 5: Refactor `build_streaming_context` to delegate**

In `chat_routes.py`, replace lines 5883-5947 so the function:
1. Extracts `user_text` from `messages` (keep existing logic at 5898-5907).
2. Calls `build_fossil_context(user_text, operator)` for `fossil_context + provenance`.
3. Builds the final `context_messages` list (system prompt + fossil block + messages) — unchanged from current behavior.
4. Returns `(context_messages, provenance)`.

**Step 6: Add a regression test for `/chat/stream` via HTTP**

```python
# tests/test_chat_stream_retrieval_regression.py
import httpx, json

def test_chat_stream_emits_populated_provenance():
    body = {
        "messages": [{"role": "user", "content": "regression test query tool vault"}],
        "operator": "Brandon",
        "provider": "gemini",
    }
    with httpx.stream("POST", "http://localhost:9091/chat/stream",
                     json=body, timeout=10) as resp:
        for line in resp.iter_lines():
            if line.startswith("data:") and '"provenance"' in line:
                payload = json.loads(line[5:].strip())
                prov = payload["provenance"]
                assert len(prov["recent"]) >= 1
                assert set(prov) == {"recent", "keyword", "semantic", "checkpoint"}
                return
        raise AssertionError("no stream_start event with provenance received")
```

Run:
```bash
Orchestrator/venv/bin/pytest tests/test_chat_stream_retrieval_regression.py -v
```
Expected: PASS. If it fails, `build_streaming_context` regressed during refactor.

**Step 7: Commit** (skip if no git)
```
feat(context): extract shared build_fossil_context; chat_routes delegates
```

---

## Task 3: Wire the shared builder into the three voice routes

**Files:**
- Modify: `Orchestrator/routes/gemini_live_routes.py` (currently 1 checkpoint + 2 recent only)
- Modify: `Orchestrator/routes/realtime_routes.py` (same)
- Modify: `Orchestrator/routes/grok_live_routes.py` (same)

Each route has a `build_*_context()` or similar helper (e.g. `gemini_live_routes.py:~100` returns `context_parts` for the system message). Today they call only `get_recent_checkpoints_for_operator(count=1)` and `get_recent_fossils_for_operator(count=2)`.

**Step 1: Write the failing test** — `tests/test_voice_route_retrieval.py`

```python
from Orchestrator.routes.gemini_live_routes import build_context_for_operator as build_gemini_ctx
from Orchestrator.routes.realtime_routes import build_context_for_operator as build_realtime_ctx
from Orchestrator.routes.grok_live_routes import build_context_for_operator as build_grok_ctx

# NOTE: rename the existing internal helpers to `build_context_for_operator`
# as part of this task so they have a consistent interface.

def _assert_context_quality(fn, name):
    text, prov = fn(operator="Brandon", user_text="tool vault recent work")
    assert "recent" in prov and "semantic" in prov and "keyword" in prov and "checkpoint" in prov, \
        f"{name} missing provenance keys"
    assert len(prov["recent"]) >= 1, f"{name} missing recent snapshots"
    assert len(prov["keyword"]) + len(prov["semantic"]) >= 1, \
        f"{name} returned zero keyword+semantic — retrieval regression"

def test_gemini_live_context_populated(): _assert_context_quality(build_gemini_ctx, "gemini-live")
def test_realtime_context_populated():    _assert_context_quality(build_realtime_ctx, "realtime")
def test_grok_live_context_populated():   _assert_context_quality(build_grok_ctx, "grok-live")
```

**Step 2: Run — expected to fail** (functions don't return `(text, prov)` tuples yet, and `keyword`/`semantic` never populated).

**Step 3: Refactor each route**

For `gemini_live_routes.py`:
- Rename the internal context helper to `build_context_for_operator(operator, user_text="") -> tuple[str, dict]`.
- Delete lines ~105-145 (the manual checkpoint + recent assembly).
- Replace body with:
  ```python
  from Orchestrator.context_builder import build_fossil_context
  text_block, provenance = build_fossil_context(user_text, operator)
  # Honor existing REALTIME_CONTEXT_MAX_CHARS cap (voice has tighter token budgets)
  if len(text_block) > REALTIME_CONTEXT_MAX_CHARS:
      text_block = text_block[:REALTIME_CONTEXT_MAX_CHARS] + "\n... [context truncated]"
  return text_block, provenance
  ```
- Find the call site (where the old helper was invoked to build the WebSocket session's system message) — it currently only takes the returned string; now also store `provenance` on the session object so we can emit it to the client later (Task 5).
- Use log prefix `[GEMINI-LIVE]` when calling `build_fossil_context(..., log_prefix="[GEMINI-LIVE]")` if we added that param in Task 2.

Repeat for `realtime_routes.py` (log prefix `[REALTIME]`) and `grok_live_routes.py` (`[GROK-LIVE]`).

**Step 4: Run tests — expected to pass.**

**Step 5: Sanity probe against the running service** (don't skip — the tests only cover the helper, not the live WebSocket plumbing):

```bash
# Restart service to pick up changes
sudo systemctl restart blackbox.service
sleep 90  # per CLAUDE.md, boot takes 60-90s (snapshot index rebuild)

# Tail logs while a voice session is opened from the Android MVP or Portal
sudo journalctl -u blackbox.service -f | grep -E "GEMINI-LIVE|REALTIME|GROK-LIVE|CONTEXT"
```
Expected: on voice session open you see `[GEMINI-LIVE] [CONTEXT] Recent snapshots (5): [...]`, plus keyword and semantic lists matching the `/chat/stream` baseline.

**Step 6: Commit** — `feat(voice): voice routes now use full fossil retrieval`.

---

## Task 4: Wire the shared builder into the three agent routes

**Files:**
- Modify: `Orchestrator/routes/agent_routes.py` (WS `/ws/agent/{session_id}` at line 749)
- Modify: `Orchestrator/routes/gemini_agent_routes.py` (WS `/ws/gemini-agent/{session_id}` at line 422)
- Modify: `Orchestrator/routes/gemini_cu_routes.py` (if applicable — Android uses Gemini CU for Android-agent flows)

Today these routes inject zero fossil context. They are the worst-case cold-start path.

**Step 1: Write the failing test** — `tests/test_agent_route_retrieval.py`

```python
import pytest, asyncio, websockets, json

@pytest.mark.asyncio
async def test_agent_ws_session_start_emits_provenance():
    uri = "ws://localhost:9091/ws/agent/test-session-plan-task4"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "type": "start",
            "operator": "Brandon",
            "prompt": "tool vault recent work"
        }))
        got_prov = False
        for _ in range(30):
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if msg.get("type") == "provenance":
                got_prov = True
                prov = msg["data"]
                assert len(prov["recent"]) >= 1
                assert set(prov) == {"recent", "keyword", "semantic", "checkpoint"}
                break
        assert got_prov, "agent WS never emitted provenance event"
```

**Step 2: Run — fail** (no retrieval and no provenance event exists).

**Step 3: Identify where the agent WebSocket handler builds its system prompt.** In each of the three files, find where the initial Claude/Gemini conversation is seeded for the session (typically at handshake — search for `system_prompt`, `messages =`, `build_core_system_prompt`, or the first model call). That's where we inject.

**CRITICAL — operator scoping check:** Before injecting retrieval, confirm the handler has access to the request's operator string. It usually comes from either (a) the WebSocket query params (`websocket.query_params.get("operator")`), (b) the first handshake message payload, or (c) a session-manager lookup. If the operator is missing or empty at session start, log a warning and fall back to `USERS_DEFAULT` — **do not skip retrieval silently**. Example guard:
```python
operator = (handshake.get("operator") or "").strip() or USERS_DEFAULT
if operator == USERS_DEFAULT and not handshake.get("operator"):
    print(f"[AGENT] WARNING: no operator in handshake, using default {USERS_DEFAULT}")
```
Add an assertion to the test in Step 1 that `provenance` is populated for a known operator AND that passing a fake operator ("nonexistent-test-user") returns empty lists.

**Step 4: Inject retrieval at session start**

```python
# At the top of the agent WS handler, after we have operator + first user prompt:
from Orchestrator.context_builder import build_fossil_context
fossil_text, provenance = build_fossil_context(
    user_text=first_user_prompt,
    operator=operator,
)
# Prepend a system-role message containing fossil_text to the agent's conversation buffer.
# Send provenance back to the client so the Android UI can render it.
await websocket.send_json({"type": "provenance", "data": provenance})
```

Use the same pattern in all three agent route files. Keep a log prefix — `[AGENT]`, `[GEMINI-AGENT]`, `[GEMINI-CU]` — for traceability.

**Step 5: Run — pass.**

**Step 6: Live WebSocket smoke probe**
```bash
# With service restarted, open an agent session from Android and tail:
sudo journalctl -u blackbox.service -f | grep -E "AGENT|GEMINI-AGENT|GEMINI-CU|CONTEXT"
```
Expected: on session start, see `[AGENT] [CONTEXT] Recent snapshots (5): [...]` etc.

**Step 7: Commit** — `feat(agent): agent WS routes inject fossil retrieval at session start`.

---

## Task 5: Fix Android `Provenance` data model — add `semantic` field

**Files:**
- Modify: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/data/model/ChatMessage.kt:50-55`

**Step 1: Write the failing test** — `app/src/test/java/com/aiblackbox/portal/ProvenanceSerializationTest.kt`

```kotlin
import com.aiblackbox.portal.data.model.Provenance
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test

class ProvenanceSerializationTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun `parses all four fields from backend`() {
        val payload = """{"recent":["A"],"keyword":["B"],"semantic":["C"],"checkpoint":["D"]}"""
        val p = json.decodeFromString(Provenance.serializer(), payload)
        assertEquals(listOf("A"), p.recent)
        assertEquals(listOf("B"), p.keyword)
        assertEquals(listOf("C"), p.semantic)
        assertEquals(listOf("D"), p.checkpoint)
    }
}
```

**Step 2: Run** — expected: FAIL with `Unresolved reference: semantic` or equivalent.

Gradle:
```bash
cd "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal" && \
  ./gradlew :app:testDebugUnitTest --tests "*ProvenanceSerializationTest*"
```

**Step 3: Add the field**

`ChatMessage.kt:51-55` becomes:
```kotlin
@Serializable
data class Provenance(
    val recent: List<String> = emptyList(),
    val keyword: List<String> = emptyList(),
    val semantic: List<String> = emptyList(),
    val checkpoint: List<String> = emptyList()
) {
    fun isEmpty(): Boolean =
        recent.isEmpty() && keyword.isEmpty() && semantic.isEmpty() && checkpoint.isEmpty()
    fun totalCount(): Int =
        recent.size + keyword.size + semantic.size + checkpoint.size
}
```

**Step 4: Run — pass.**

**Step 5: Commit** — `fix(android/model): add semantic field to Provenance`.

---

## Task 6: Change `UiMessage.provenance` from String to Provenance

**Files:**
- Modify: `.../data/model/UiMessage.kt:21`
- Modify: all consumers of the old `String?` signature (ChatViewModel.kt:917, 930; HistoryStore if it persists UiMessage)

**Step 1: Write the failing test** — extend `ProvenanceSerializationTest.kt`:
```kotlin
@Test fun `UiMessage carries typed Provenance not raw string`() {
    val msg = UiMessage(role="assistant", content="hi",
        provenance = Provenance(recent=listOf("SNAP-X")))
    assertNotNull(msg.provenance)
    assertEquals(1, msg.provenance!!.recent.size)
}
```
Expected FAIL: `provenance` is currently `String?` so the types mismatch at compile time.

**Step 2: Flip the type**

`UiMessage.kt:21`:
```kotlin
val provenance: Provenance? = null,
```

**Step 3: Update `ChatViewModel.kt`**
- Line 531 declaration: `var provenance: Provenance? = null`
- Line 917 `updateLastMessage` signature: `provenance: Provenance? = null`
- Line 930 (unchanged logic, just typed now)
- Line 624 `onMeta` callback signature: `(model: String?, tokens: TokenCount?, provenance: Provenance?) -> Unit`

**Step 4: Compile**
```bash
./gradlew :app:assembleDebug
```
Expected: clean build (if not, fix the remaining call sites the compiler flags).

**Step 5: Commit** — `refactor(android/ui): UiMessage.provenance typed as Provenance`.

---

## Task 7: Parse the `provenance` SSE event into Provenance

**Files:**
- Modify: `.../ui/chat/ChatViewModel.kt:703-706`

Currently:
```kotlin
"provenance" -> { onMeta(null, null, event.data) }  // raw String
```

**Step 1: Write the failing test** — in the existing `ProvenanceSerializationTest.kt`:
```kotlin
@Test fun `ChatViewModel parses provenance event into typed Provenance`() {
    val raw = """{"recent":["SNAP-1"],"keyword":[],"semantic":["SNAP-2"],"checkpoint":["SNAP-3"]}"""
    val parsed = ChatViewModel.parseProvenance(raw)  // static/companion helper we will add
    assertNotNull(parsed)
    assertEquals(1, parsed!!.recent.size)
    assertEquals(1, parsed.semantic.size)
    assertEquals(1, parsed.checkpoint.size)
}
```

**Step 2: Run — fail** (`parseProvenance` not defined).

**Step 3: Add the companion helper** in `ChatViewModel.kt`:
```kotlin
companion object {
    private val provJson = Json { ignoreUnknownKeys = true; isLenient = true }
    fun parseProvenance(raw: String): Provenance? = try {
        provJson.decodeFromString(Provenance.serializer(), raw.trim())
    } catch (_: Exception) { null }
}
```

And replace lines 703-706:
```kotlin
"provenance" -> {
    val parsed = parseProvenance(event.data)
    if (parsed != null) {
        onMeta(null, null, parsed)
        Log.d(TAG, "provenance: recent=${parsed.recent.size} " +
              "keyword=${parsed.keyword.size} semantic=${parsed.semantic.size} " +
              "checkpoint=${parsed.checkpoint.size}")
    } else {
        Log.w(TAG, "provenance event unparseable: ${event.data.take(200)}")
    }
}
```

**Step 4: Run — pass.**

**Step 5: Commit** — `fix(android/chat): parse provenance SSE event into typed Provenance`.

---

## Task 8: Render a ContextProvenance panel in ChatBubble

**Files:**
- Create: `.../ui/components/ContextProvenance.kt`
- Modify: `.../ui/components/ChatBubble.kt:312-328` (insert provenance row before the timestamp row)

**Design:** A collapsed chip row by default (e.g. "📚 5+3+6+2 context snapshots" with a chevron), expanding to four labeled sections ("Recent", "Keyword", "Semantic", "Checkpoint") showing compact pill chips of SNAP-IDs. Use existing theme tokens (`Neutral500`, `RadiusMd`, `GlassBorder`). Do NOT make SNAP chips clickable yet — keep scope tight. That navigation can be a follow-up.

**Step 1: Write the failing Compose test** — `app/src/androidTest/java/.../ContextProvenanceUiTest.kt`

```kotlin
@Test fun contextProvenance_empty_renders_nothing() {
    composeTestRule.setContent {
        ContextProvenance(provenance = Provenance(), expanded = false, onToggle = {})
    }
    composeTestRule.onNodeWithTag("context-provenance-root")
        .assertDoesNotExist()
}

@Test fun contextProvenance_populated_shows_count_label() {
    val p = Provenance(recent=listOf("SNAP-A"), semantic=listOf("SNAP-B"))
    composeTestRule.setContent {
        ContextProvenance(provenance = p, expanded = false, onToggle = {})
    }
    composeTestRule.onNodeWithText("2 context snapshots", substring = true).assertIsDisplayed()
}

@Test fun contextProvenance_expanded_shows_sections() {
    val p = Provenance(recent=listOf("SNAP-R"), semantic=listOf("SNAP-S"), checkpoint=listOf("SNAP-C"))
    composeTestRule.setContent {
        ContextProvenance(provenance = p, expanded = true, onToggle = {})
    }
    composeTestRule.onNodeWithText("Recent").assertIsDisplayed()
    composeTestRule.onNodeWithText("Semantic").assertIsDisplayed()
    composeTestRule.onNodeWithText("Checkpoint").assertIsDisplayed()
    composeTestRule.onNodeWithText("SNAP-R").assertIsDisplayed()
}
```

**Step 2: Run — fail** (composable not defined).

**Step 3: Implement `ContextProvenance.kt`**

```kotlin
@Composable
fun ContextProvenance(
    provenance: Provenance,
    expanded: Boolean,
    onToggle: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (provenance.isEmpty()) return
    Column(modifier = modifier.testTag("context-provenance-root")) {
        Row(
            modifier = Modifier.clickable { onToggle() },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(if (expanded) "▼" else "▶",
                 style = MaterialTheme.typography.labelSmall,
                 color = Neutral500)
            Spacer(Modifier.width(4.dp))
            Text("${provenance.totalCount()} context snapshots",
                 style = MaterialTheme.typography.labelSmall,
                 color = Neutral500)
        }
        AnimatedVisibility(expanded) {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp),
                   modifier = Modifier.padding(top = 4.dp)) {
                Section("Recent",     provenance.recent)
                Section("Keyword",    provenance.keyword)
                Section("Semantic",   provenance.semantic)
                Section("Checkpoint", provenance.checkpoint)
            }
        }
    }
}

@Composable
private fun Section(label: String, ids: List<String>) {
    if (ids.isEmpty()) return
    Column {
        Text(label, style = MaterialTheme.typography.labelSmall,
             color = Neutral600, fontWeight = FontWeight.Bold)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            ids.forEach { id ->
                Box(
                    Modifier
                        .background(Color(0x14FFFFFF), RoundedCornerShape(RadiusMd))
                        .border(1.dp, GlassBorder, RoundedCornerShape(RadiusMd))
                        .padding(horizontal = 6.dp, vertical = 2.dp)
                ) {
                    Text(id.removePrefix("SNAP-"),
                         style = MaterialTheme.typography.labelSmall.copy(fontSize = 9.sp),
                         color = BbxDim)
                }
            }
        }
    }
}
```

**Step 4: Wire into `ChatBubble.kt`** — after line 311 (after the TTS audio player, before the timestamp row):

```kotlin
// Expansion state per-bubble
var provenanceExpanded by remember { mutableStateOf(false) }
message.provenance?.let { prov ->
    if (!prov.isEmpty()) {
        ContextProvenance(
            provenance = prov,
            expanded = provenanceExpanded,
            onToggle = { provenanceExpanded = !provenanceExpanded },
        )
    }
}
```

**Step 5: Run — pass.**

**Step 6: Commit** — `feat(android/ui): render ContextProvenance panel in chat bubble`.

---

## Task 9: Wire provenance into `/chat/save` so auto-mint records it

**Files:**
- Modify: `.../data/api/ChatRepository.kt` (SaveRequest construction)
- Modify: `.../ui/chat/ChatViewModel.kt:950-974` (`saveConversation`)

The Portal includes `provenance: result.provenance` on save (chat-send.js:2204); Android drops it.

**Step 1: Write failing test** — add to an existing save-request test file (create `ChatViewModelSaveTest.kt` if none exists):

```kotlin
@Test fun `saveConversation forwards provenance when present`() = runTest {
    val captured = mutableListOf<SaveRequest>()
    val fakeRepo = object : ChatRepository(FakeApi()) {
        override suspend fun saveConversation(r: SaveRequest) = captured.add(r).let { "ok" }
    }
    val vm = ChatViewModel().also { it.setRepositoryForTest(fakeRepo) }
    vm.saveConversationForTest(
        userMessage = "hi",
        assistantResponse = "hello",
        reasoning = "",
        model = "gemini",
        tokens = null,
        provenance = Provenance(recent = listOf("SNAP-Z"))
    )
    assertEquals(1, captured.size)
    assertEquals(listOf("SNAP-Z"), captured.first().provenance?.recent)
}
```

*(Note: This test requires exposing `setRepositoryForTest` and `saveConversationForTest` as `@VisibleForTesting internal`. Use an `@VisibleForTesting` annotation — this is cheaper than mocking.)*

**Step 2: Run — fail** (provenance parameter missing).

**Step 3: Add provenance to the save path**

`ChatViewModel.kt:950`:
```kotlin
private fun saveConversation(
    userMessage: String,
    assistantResponse: String,
    reasoning: String,
    model: String?,
    tokens: TokenCount?,
    provenance: Provenance? = null
) {
    val repo = repository ?: return
    viewModelScope.launch {
        try {
            repo.saveConversation(SaveRequest(
                operator = currentOperator,
                userMessage = userMessage,
                assistantResponse = assistantResponse,
                reasoning = reasoning.ifBlank { null },
                model = model ?: currentModel,
                tokens = tokens,
                provenance = provenance,
            ))
        } catch (e: Exception) {
            Log.w(TAG, "saveConversation failed (non-critical): ${e.message}")
        }
    }
}
```

Update the one call site (around line 591-593 in the stream completion block) to pass the captured `provenance` local through.

**Step 4: Run — pass.**

**Step 5: Commit** — `fix(android/chat): forward provenance on /chat/save`.

---

## Task 10: Parse provenance over WebSocket transports (voice + agent)

**Files:**
- Modify: `.../ui/chat/AgentChatScreen.kt` (where the agent WS events are handled)
- Modify: voice WebSocket handlers (search in AI_BlackBox_Portal_Android_MVP for `WebSocketListener` + `gemini-live` | `grok-live` | `realtime`)

The SSE path now parses provenance correctly (Task 7). The WS paths don't know about the `type: "provenance"` event we added in Tasks 3-4.

**Step 1: Failing test** — at minimum a unit test that verifies the WS message dispatcher routes `{"type":"provenance","data":...}` into the same `parseProvenance` + state update used by SSE.

**Step 2: Add the branch** to the WS message handler:
```kotlin
when (msg.optString("type")) {
    "provenance" -> {
        val raw = msg.optJSONObject("data")?.toString() ?: return
        ChatViewModel.parseProvenance(raw)?.let { prov ->
            // apply to current streaming bubble
            updateLastMessage(provenance = prov)
        }
    }
    // ... existing cases
}
```

**Step 3: Live verification** — open an Android voice session, confirm the provenance panel populates in the bubble.

**Step 4: Commit** — `feat(android/voice+agent): parse provenance events over WS transports`.

---

## Task 11: End-to-end verification & regression check

**Files:** none (verification only)

**Step 1:** Restart service.
```bash
sudo systemctl restart blackbox.service && sleep 90
curl -s http://localhost:9091/health | python3 -m json.tool | head -10
```
Expected: `status: "ok"`, `worker_running: true`.

**Step 2:** Repeat the baseline probes from Task 1 against each of the five paths. Compare against the baseline table. Expected new state:

| Path | Recent | Keyword | Semantic | Checkpoint |
|---|---|---|---|---|
| `/chat/stream` | ≥1 | ≥0 | ≥0 | ≥1 |
| gemini-live | ≥1 | ≥0 | ≥0 | ≥1 |
| realtime | ≥1 | ≥0 | ≥0 | ≥1 |
| grok-live | ≥1 | ≥0 | ≥0 | ≥1 |
| ws/agent | ≥1 | ≥0 | ≥0 | ≥1 |
| ws/gemini-agent | ≥1 | ≥0 | ≥0 | ≥1 |

**Step 3:** Install the Android APK on a physical device (per `CLAUDE.md` the Android MVP uses Gradle build):
```bash
cd "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal" && \
  ./gradlew :app:installDebug
```
Expected: `BUILD SUCCESSFUL`, APK installed.

**Step 4:** Manual smoke tests (capture screenshots):
1. Open chat, ask a question that should hit semantic recall (e.g., "what did we do with tool vault recently"). Expected: model cites at least one SNAP-ID, provenance panel shows 4 sections populated.
2. Start voice session. Ask same question. Expected: model answers with context; logcat shows `provenance: recent=N keyword=N semantic=N checkpoint=N`.
3. Start agent session. Same. Expected: same.
4. Kill app, reopen chat — snapshot lineage persists (via `/chat/save` provenance wiring from Task 9).

**Step 5:** Query the BlackBox to confirm auto-mint stored provenance:
```bash
curl -s "http://localhost:9091/api/snapshots/recent?operator=Brandon&limit=3" | python3 -m json.tool | head -50
```
Expected: most recent 3 snapshots have a non-empty provenance field in their body.

**Step 6:** Create a dev snapshot documenting the fix (per `CLAUDE.md` workflow):
```bash
# Use /snapshot-dev via Portal or direct chat with operator Brandon-DEV
```

**Step 7:** Commit final verification notes into `docs/plans/2026-04-24-android-context-retrieval-fix.evidence.md` (append "AFTER" section with timestamps and probe output).

---

## Risk & Rollback

**Risk A — retrieval adds latency to voice routes.** Voice has tight timing budgets; full retrieval adds ~100-300ms. Mitigation: keep `REALTIME_CONTEXT_MAX_CHARS` cap, and time the call with a logged ms count. If it exceeds 500ms p95, reduce `SF` (semantic fossils) from 8 to 4 specifically for voice via a `mode="voice"` param to `build_fossil_context`.

**Risk B — agent routes may choke on 30k-char system prompts.** Agent mode has more token pressure than chat (longer tool loops). Mitigation: the existing 30k cap stays; monitor token budgets. If tool loops fail, add per-mode caps.

**Risk C — breaking the existing working `/chat/stream` path.** Mitigation: Task 2 Step 6 adds an HTTP regression test that runs before/after the refactor.

**Rollback:** all changes are additive per-file; reverting the five backend route files and two Android model files restores prior behavior.

---

## Out of Scope

- Making SNAP-ID chips tappable to open the snapshot detail screen (follow-up).
- Rendering provenance for history-restored messages (only new streaming turns show it).
- Tool-vault injection parity between SSE and WS (TOOLVAULT_ENABLED path is chat-only today; a separate plan).
- The `buildApiHistory(): List<ChatMessage> = emptyList()` choice in Android — server-side retrieval makes this correct, don't re-add history.

---

## Completion Criteria

- [ ] All six unit/integration tests added in Tasks 2/3/4/5/6/7/8/9/10 pass.
- [ ] All five backend paths emit a populated provenance on a Brandon query.
- [ ] Android chat bubble visually shows a collapsed "N context snapshots" label that expands into 4 sections.
- [ ] `/chat/save` requests from Android include a non-null `provenance` field (verify via `journalctl` or a quick logging patch).
- [ ] Model responses via voice and agent modes cite specific prior SNAP-IDs when asked about recent work.
- [ ] Evidence file appended with AFTER probe results showing before/after parity.
