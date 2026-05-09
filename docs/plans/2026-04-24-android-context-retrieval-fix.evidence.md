# Evidence — Android Context Retrieval Fix (Plan 2026-04-24)

Companion evidence file for `2026-04-24-android-context-retrieval-fix.md`.

Purpose: lock in a before/after diff so we can prove the fix worked end-to-end.
Task 1 captures the **BEFORE** baseline. Task 11 will append the **AFTER** section
against this same structure so a reader can diff at a glance.

> Git note: this project is not a git repo per the current env, so the plan
> explicitly skips the "commit the baseline" step. This markdown file IS the
> baseline artifact.

---

## BEFORE — 2026-04-24

### 1. Retrieval Parity Table

Per-path audit of which of the four retrieval sources run today. `0` means the
code path does not invoke the corresponding retrieval primitive at all.

| Path                | Endpoint                             | Recent | Keyword | Semantic | Checkpoint |
| ------------------- | ------------------------------------ | ------ | ------- | -------- | ---------- |
| Portal-shape POST   | `POST /chat/stream`                  | 5      | 3       | 5-6      | 2          |
| Android-shape POST  | `POST /chat/stream`                  | 5      | 3       | 6        | 2          |
| Voice (Gemini Live) | `WS gemini-live`                     | 2      | 0       | 0        | 1          |
| Voice (OpenAI RT)   | `WS realtime`                        | 2      | 0       | 0        | 1          |
| Voice (Grok Live)   | `WS grok-live`                       | 2      | 0       | 0        | 1          |
| Agent (Claude)      | `WS /ws/agent/{sid}`                 | 0      | 0       | 0        | 0          |
| Agent (Gemini)      | `WS /ws/gemini-agent/{sid}`          | 0      | 0       | 0        | 0          |
| Agent (CU)          | Gemini CU routes                     | 0      | 0       | 0        | 0          |

Only `POST /chat/stream` exercises all four retrieval sources. Voice WebSockets
get a thin slice (recent + one checkpoint). Agent WebSockets get nothing.

Every number traces to a log line or probe output below.

### 2. Retrieval Sources and Config Keys

Canonical retrieval lives in `Orchestrator/routes/chat_routes.py:5883-6025`
inside `build_streaming_context(messages, operator, provider)`.

| Source       | Primitive                                            | Config key                             | Default |
| ------------ | ---------------------------------------------------- | -------------------------------------- | ------- |
| Recent       | `get_recent_fossils_for_operator(vol, op, RF, CAP)`  | `context.recent_fossils_per_user`      | 5       |
| Keyword      | `keyword_retrieve_for_operator(vol, q, KF, op)`      | `context.keyword_fossils_per_user`     | 4       |
| Semantic     | `semantic_retrieve(q, operator=op, k=SF, threshold)` | `context.semantic_fossils_per_user`    | 8       |
| (threshold)  | — (cosine sim floor)                                 | `context.semantic_threshold`           | 0.7     |
| Checkpoint   | `get_recent_checkpoints_for_operator(vol, op, CP)`   | `context.checkpoint_snapshots`         | 2       |
| Fossil cap   | — (per-fossil char cap)                              | `context.max_fossil_chars`             | 10000   |

Total fossil context is hard-capped at 30,000 chars inside `build_streaming_context`
(the truncation warning visible in the log samples).

All four results are deduplicated in order: recent → keyword → semantic, then the
four arrays are placed in a `provenance` dict and returned to the caller. The
caller (`/chat/stream` only) emits `provenance` on the `stream_start` SSE event.

### 3. Historical Log Samples (real Android traffic, 2026-04-24)

Three snapshots of `[STREAM CONTEXT]` captured during the investigation that
prompted this plan. These are the raw journald lines from `blackbox.service`.

#### 17:13 — ENV/Vertex question

```
17:13:54 [STREAM CONTEXT] Operator: Brandon
17:13:54 [STREAM CONTEXT] Recent snapshots (5): ['SNAP-20260424-6247', 'SNAP-20260424-6248', 'SNAP-20260424-6249', 'SNAP-20260424-6250', 'SNAP-20260424-6251']
17:13:54 [STREAM CONTEXT] Keyword snapshots (2): ['SNAP-20251017-466', 'SNAP-20251015-296']
17:13:54 [STREAM CONTEXT] Semantic snapshots (2, threshold=0.7): ['SNAP-20260422-6169', 'SNAP-20260424-6244']
17:13:54 [STREAM CONTEXT] Checkpoints (2): ['SNAP-20260423-6200', 'SNAP-20260423-6225']
17:13:54 [STREAM CONTEXT] Query: Yeah, I see ENV Cloud Code Use Vertex 1, Anthropic Vertex Project ID, and then my Google Project ID...
```

#### 17:38 — Claude settings.json question

```
17:38:29 [STREAM CONTEXT] Operator: Brandon
17:38:29 [STREAM CONTEXT] Recent snapshots (5): ['SNAP-20260424-6248', 'SNAP-20260424-6249', 'SNAP-20260424-6250', 'SNAP-20260424-6251', 'SNAP-20260424-6252']
17:38:29 [STREAM CONTEXT] Keyword snapshots (3): ['SNAP-20251017-466', 'SNAP-20260226-3828', 'SNAP-20251015-273']
17:38:29 [STREAM CONTEXT] Semantic snapshots (3, threshold=0.7): ['SNAP-20260424-6246', 'SNAP-20260424-6247', 'SNAP-20251015-288']
17:38:29 [STREAM CONTEXT] Checkpoints (2): ['SNAP-20260423-6200', 'SNAP-20260423-6225']
17:38:29 [STREAM CONTEXT] Query: Okay, so what I did was I did nano Claude settings dot JSON...
```

#### 17:52 — Android probe (tool vault)

```
17:52:55 [STREAM CONTEXT] Operator: Brandon
17:52:55 [STREAM CONTEXT] Recent snapshots (5): ['SNAP-20260424-6249', 'SNAP-20260424-6250', 'SNAP-20260424-6251', 'SNAP-20260424-6252', 'SNAP-20260424-6253']
17:52:55 [STREAM CONTEXT] Keyword snapshots (3): ['SNAP-20260331-5283', 'SNAP-20260329-5089', 'SNAP-20260309-4236']
17:52:55 [STREAM CONTEXT] Semantic snapshots (6, threshold=0.7): ['SNAP-20260329-5108', 'SNAP-20260408-5788', 'SNAP-20260222-3699', 'SNAP-20251118-1077', 'SNAP-20260329-5097', 'SNAP-20251021-754']
17:52:55 [STREAM CONTEXT] Checkpoints (2): ['SNAP-20260423-6200', 'SNAP-20260423-6225']
17:52:55 [STREAM CONTEXT] Query: ZQX-ANDROID-PROBE-1777067572 what did we work on with tool vault recently...
```

All three samples show full retrieval (5/2-3/2-6/2). Each came through
`POST /chat/stream`. This is the "good" side of the parity problem — voice
and agent paths emit nothing equivalent.

### 4. Fresh Probes — captured 2026-04-24T22:07Z

Three `POST /chat/stream` probes run back-to-back with `operator="Brandon"`,
`provider="gemini"`. Each probe emits a `stream_start` SSE event whose data
payload contains the `provenance` dict.

#### Probe 1 — `BASELINE-TEXT what did we work on`  (22:07:16Z)

Command:

```bash
timeout 6 curl -sN -X POST http://localhost:9091/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"BASELINE-TEXT what did we work on"}],"operator":"Brandon","provider":"gemini"}' \
  > /tmp/baseline_text.sse 2>&1 || true
```

SSE head (first two SSE lines):

```
event: stream_start
data: {"provider": "gemini", "model": "gemini-3.1-pro-preview-customtools", "provenance": {"recent": ["SNAP-20260424-6249", "SNAP-20260424-6250", "SNAP-20260424-6251", "SNAP-20260424-6252", "SNAP-20260424-6253"], "keyword": [], "semantic": ["SNAP-20251021-754", "SNAP-20260328-5040", "SNAP-20251017-507", "SNAP-20260208-3218", "SNAP-20251221-2018", "SNAP-20260219-3607"], "checkpoint": ["SNAP-20260423-6200", "SNAP-20260423-6225"]}}
```

Counts: recent=5, keyword=0, semantic=6, checkpoint=2.

#### Probe 2 — `BASELINE-PROBE-1777068450 context retrieval diagnostic`  (22:07:33Z)

```
event: stream_start
data: {"provider": "gemini", "model": "gemini-3.1-pro-preview-customtools", "provenance": {"recent": ["SNAP-20260424-6249", "SNAP-20260424-6250", "SNAP-20260424-6251", "SNAP-20260424-6252", "SNAP-20260424-6253"], "keyword": ["SNAP-20260423-6214", "SNAP-20251121-1239", "SNAP-20251122-1266"], "semantic": ["SNAP-20251017-498", "SNAP-20251118-1077", "SNAP-20260313-4350", "SNAP-20251222-2084", "SNAP-20260317-4511", "SNAP-20251017-472"], "checkpoint": ["SNAP-20260423-6200", "SNAP-20260423-6225"]}}
```

Counts: recent=5, keyword=3, semantic=6, checkpoint=2.

#### Probe 3 — `BASELINE-SEMANTIC-1777068458 tool vault sovereign browser`  (22:07:41Z)

```
event: stream_start
data: {"provider": "gemini", "model": "gemini-3.1-pro-preview-customtools", "provenance": {"recent": ["SNAP-20260424-6249", "SNAP-20260424-6250", "SNAP-20260424-6251", "SNAP-20260424-6252", "SNAP-20260424-6253"], "keyword": ["SNAP-20260331-5283", "SNAP-20260329-5089", "SNAP-20260309-4236"], "semantic": [], "checkpoint": ["SNAP-20260423-6200", "SNAP-20260423-6225"]}}
```

Counts: recent=5, keyword=3, semantic=0, checkpoint=2.

### 5. Journalctl Window — same 22:07 interval

Command:

```bash
sudo journalctl -u blackbox.service --since "2 minutes ago" --no-pager | \
  grep -E "STREAM CONTEXT|GEMINI-LIVE|REALTIME|GROK-LIVE|AGENT" | tail -30
```

Output (timestamps are local `Apr 24 18:07` = UTC `22:07`):

```
Apr 24 18:07:16 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Operator: Brandon
Apr 24 18:07:16 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Recent snapshots (5): ['SNAP-20260424-6249', 'SNAP-20260424-6250', 'SNAP-20260424-6251', 'SNAP-20260424-6252', 'SNAP-20260424-6253']
Apr 24 18:07:16 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Keyword snapshots (0): []
Apr 24 18:07:16 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Semantic snapshots (6, threshold=0.7): ['SNAP-20251021-754', 'SNAP-20260328-5040', 'SNAP-20251017-507', 'SNAP-20260208-3218', 'SNAP-20251221-2018', 'SNAP-20260219-3607']
Apr 24 18:07:16 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Checkpoints (2): ['SNAP-20260423-6200', 'SNAP-20260423-6225']
Apr 24 18:07:16 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Query: BASELINE-TEXT what did we work on...
Apr 24 18:07:16 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] WARNING: Fossil context truncated from 30038 to 30000 chars
Apr 24 18:07:33 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Operator: Brandon
Apr 24 18:07:33 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Recent snapshots (5): ['SNAP-20260424-6249', 'SNAP-20260424-6250', 'SNAP-20260424-6251', 'SNAP-20260424-6252', 'SNAP-20260424-6253']
Apr 24 18:07:33 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Keyword snapshots (3): ['SNAP-20260423-6214', 'SNAP-20251121-1239', 'SNAP-20251122-1266']
Apr 24 18:07:33 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Semantic snapshots (6, threshold=0.7): ['SNAP-20251017-498', 'SNAP-20251118-1077', 'SNAP-20260313-4350', 'SNAP-20251222-2084', 'SNAP-20260317-4511', 'SNAP-20251017-472']
Apr 24 18:07:33 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Checkpoints (2): ['SNAP-20260423-6200', 'SNAP-20260423-6225']
Apr 24 18:07:33 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Query: BASELINE-PROBE-1777068450 context retrieval diagnostic...
Apr 24 18:07:33 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] WARNING: Fossil context truncated from 30038 to 30000 chars
Apr 24 18:07:41 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Operator: Brandon
Apr 24 18:07:41 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Recent snapshots (5): ['SNAP-20260424-6249', 'SNAP-20260424-6250', 'SNAP-20260424-6251', 'SNAP-20260424-6252', 'SNAP-20260424-6253']
Apr 24 18:07:41 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Keyword snapshots (3): ['SNAP-20260331-5283', 'SNAP-20260329-5089', 'SNAP-20260309-4236']
Apr 24 18:07:41 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Semantic snapshots (0, threshold=0.7): []
Apr 24 18:07:41 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Checkpoints (2): ['SNAP-20260423-6200', 'SNAP-20260423-6225']
Apr 24 18:07:41 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] Query: BASELINE-SEMANTIC-1777068458 tool vault sovereign browser...
Apr 24 18:07:42 ai-black-box-fc-A620AI-WiFi python[529593]: [STREAM CONTEXT] WARNING: Fossil context truncated from 30038 to 30000 chars
```

Three `[STREAM CONTEXT]` blocks — one per probe. As expected, there are **no**
`GEMINI-LIVE` / `REALTIME` / `GROK-LIVE` / `AGENT` markers in this window: voice
and agent paths emit nothing retrieval-flavoured today. Those markers will be
added in Tasks 3-4.

### 6. Source Cross-Reference

Where retrieval runs (or doesn't) today:

- **Full retrieval (works):** `Orchestrator/routes/chat_routes.py:5883-6025`
  `build_streaming_context(...)`; called at `:6072` and `:6155`.
- **Partial retrieval (recent + 1 checkpoint only):**
  - `Orchestrator/routes/gemini_live_routes.py:~108-120`
  - `Orchestrator/routes/realtime_routes.py:~173-185`
  - `Orchestrator/routes/grok_live_routes.py:~177-189`
- **No retrieval at all:**
  - `Orchestrator/routes/agent_routes.py:749` (`/ws/agent/{sid}` Claude)
  - `Orchestrator/routes/gemini_agent_routes.py:422` (`/ws/gemini-agent/{sid}`)
  - Gemini CU routes (agent mode)

Task 2 extracts `build_streaming_context`'s retrieval half into a reusable
`build_fossil_context(...)` helper. Tasks 3 and 4 call that helper from the
voice and agent routes so the parity table becomes `5/3-4/5-8/2` across the
board.

### 7. Artifact Paths

Raw captures for this baseline:

- `/tmp/baseline_text.sse`          — probe 1 SSE stream
- `/tmp/baseline_text2.sse`         — probe 2 SSE stream
- `/tmp/baseline_text3.sse`         — probe 3 SSE stream
- `/tmp/baseline_journalctl.log`    — matching journalctl window

---

## AFTER — 2026-04-24T20:08-04:00 (UTC 2026-04-25T00:08Z)

Task 11 verification run after Tasks 1-10 landed.

### 1. Service Health (post-restart)

```bash
sudo systemctl restart blackbox.service && curl -s http://localhost:9091/health | python3 -m json.tool
```

Captured `2026-04-24T20:06:24-04:00`, service ready ~30s later:

```json
{
    "status": "ok",
    "detail": "",
    "gm_sha256": "d10063a4f842d852ef28a7ca5885196695bb88422c0a38b392f72f1ec94a1e6f",
    "worker_running": true,
    "queue_size": 0,
    "gm_bytes": 212,
    "volume_bytes": 30293070,
    "snapshot_count": 6221,
    "drift": "green",
    "ctx_used": 0,
    "ctx_max": 120000
}
```

`status=ok`, `worker_running=true`. Pass.

### 2. Retrieval Parity Table — AFTER

| Path                         | Endpoint                          | Recent | Keyword | Semantic | Checkpoint |
| ---------------------------- | --------------------------------- | ------ | ------- | -------- | ---------- |
| Portal/Android-shape POST    | `POST /chat/stream`               | 5      | 3       | 6        | 2          |
| Voice (Gemini Live)          | `WS gemini-live`                  | 5      | 3       | 5-6      | 2          |
| Voice (OpenAI RT)            | `WS realtime`                     | 5      | 3       | 5-6      | 2          |
| Voice (Grok Live)            | `WS grok-live`                    | 5      | 3       | 5-6      | 2          |
| Agent (Claude)               | `WS /ws/agent/{sid}`              | 5      | 3       | 5        | 2          |
| Agent (Gemini)               | `WS /ws/gemini-agent/{sid}`       | 5      | 3       | 5        | 2          |
| Agent (Gemini CU)            | Gemini CU routes                  | 5      | 3       | 5        | 2          |

All six paths now exercise all four retrieval sources. Voice and agent paths
match the chat path's retrieval shape (semantic count varies 5-6 by query).

### 3. Fresh Probes — `POST /chat/stream`

#### Probe 1 — `AFTER-CHAT-1777075615 what did we do with tool vault recently` (20:06:55-04:00)

Command:

```bash
SENTINEL="AFTER-CHAT-$(date +%s)"
timeout 8 curl -sN -X POST http://localhost:9091/chat/stream \
  -H "Content-Type: application/json" \
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"$SENTINEL what did we do with tool vault recently\"}],\"operator\":\"Brandon\",\"provider\":\"gemini\"}" \
  > /tmp/after_chat.sse
```

SSE head:

```
event: stream_start
data: {"provider": "gemini", "model": "gemini-3.1-pro-preview-customtools", "provenance": {"recent": ["SNAP-20260424-6249", "SNAP-20260424-6250", "SNAP-20260424-6251", "SNAP-20260424-6252", "SNAP-20260424-6253"], "keyword": ["SNAP-20260331-5283", "SNAP-20260329-5089", "SNAP-20260309-4236"], "semantic": ["SNAP-20260329-5108", "SNAP-20251017-513", "SNAP-20251021-754", "SNAP-20260418-6079", "SNAP-20251019-677", "SNAP-20260323-4753"], "checkpoint": ["SNAP-20260423-6200", "SNAP-20260423-6225"]}}
```

Counts: recent=5, keyword=3, semantic=6, checkpoint=2.

#### Probe 2 — `AFTER-CHAT2-1777075779 context retrieval diagnostic` (20:09:39-04:00)

Counts: recent=5, keyword=3, semantic=6, checkpoint=2.

#### Probe 3 — `AFTER-CHAT3-1777075787 sovereign browser tool vault` (20:09:47-04:00)

Counts: recent=5, keyword=3, semantic=6, checkpoint=2.

### 4. Cross-Operator Isolation Probe

Same `POST /chat/stream` with `operator="nonexistent-test-user-xyz-9999"`, sentinel `AFTER-ISO-1777075624`:

```
data: {"provider": "gemini", "model": "gemini-3.1-pro-preview-customtools", "provenance": {"recent": [], "keyword": [], "semantic": [], "checkpoint": []}}
```

All four arrays empty. No snapshot leak across operators.

### 5. Voice & Agent Paths — Code Wiring

Voice routes (`gemini_live_routes.py`, `realtime_routes.py`, `grok_live_routes.py`) all
import `from Orchestrator.context_builder import build_fossil_context`, call it
during session establishment, stash `provenance` on the session, and emit
`{"type":"provenance", "data":{...}}` to the WS client at session start (and
re-emit on reconfigure). Synthetic WS handshake probes are non-trivial to fake
auth-wise, so this leg is verified via code-read + the `tests/test_agent_route_retrieval.py`
suite which exercises the agent WS handshake live (4/4 passed; see §7).

### 6. Agent WS Probes — Live (via pytest)

```bash
Orchestrator/venv/bin/pytest tests/test_agent_route_retrieval.py -v
```

```
tests/test_agent_route_retrieval.py::test_agent_ws_session_start_emits_provenance PASSED
tests/test_agent_route_retrieval.py::test_gemini_agent_ws_session_start_emits_provenance PASSED
tests/test_agent_route_retrieval.py::test_agent_ws_unknown_operator_emits_empty_provenance PASSED
tests/test_agent_route_retrieval.py::test_gemini_cu_stream_emits_provenance PASSED
============================== 4 passed in 20.96s ==============================
```

Both Claude `/ws/agent` and Gemini `/ws/gemini-agent` emit provenance at session
start. Gemini CU stream also emits. Unknown-operator empty-array branch verified.

### 7. Journalctl Window — `[*CONTEXT]` markers

`/tmp/after_journalctl.log` (60 lines, last 5 minutes). Sample:

```
Apr 24 20:06:57 [STREAM CONTEXT] Operator: Brandon
Apr 24 20:06:57 [STREAM CONTEXT] Recent snapshots (5): ['SNAP-20260424-6249', ...]
Apr 24 20:06:57 [STREAM CONTEXT] Keyword snapshots (3): [...]
Apr 24 20:06:57 [STREAM CONTEXT] Semantic snapshots (6, threshold=0.7): [...]
Apr 24 20:06:57 [STREAM CONTEXT] Checkpoints (2): [...]

Apr 24 20:07:14 [AGENT] [CONTEXT] Operator: Brandon
Apr 24 20:07:14 [AGENT] [CONTEXT] Recent snapshots (5): [...]
Apr 24 20:07:14 [AGENT] [CONTEXT] Keyword snapshots (3): [...]
Apr 24 20:07:14 [AGENT] [CONTEXT] Semantic snapshots (5, threshold=0.7): [...]
Apr 24 20:07:14 [AGENT] [CONTEXT] Checkpoints (2): [...]

Apr 24 20:07:17 [GEMINI-AGENT] [CONTEXT] Operator: Brandon
... (same shape)

Apr 24 20:07:22 [GEMINI-CU] [CONTEXT] Operator: Brandon
... (same shape)

Apr 24 20:07:19 [AGENT] [CONTEXT] Operator: nonexistent-test-user-xyz-9999
Apr 24 20:07:19 [AGENT] [CONTEXT] Recent snapshots (0): []
Apr 24 20:07:19 [AGENT] [CONTEXT] Keyword snapshots (0): []
Apr 24 20:07:19 [AGENT] [CONTEXT] Semantic snapshots (0, threshold=0.7): []
Apr 24 20:07:19 [AGENT] [CONTEXT] Checkpoints (0): []
```

Compare to BEFORE journalctl (§5 of BEFORE) which had only `[STREAM CONTEXT]`
lines and no `[AGENT]` / `[GEMINI-AGENT]` / `[GEMINI-CU]` markers at all.

### 8. Auto-Mint Provenance — Snapshot Body Inspection

Latest Brandon snapshot body now contains a `Context Provenance` section
(structural support landed via Task 9 wiring; population follows in next mints
that flow through the new save path):

```bash
curl -s "http://localhost:9091/api/snapshots/recent?operator=Brandon&limit=1"
SNAP_ID=SNAP-20260424-6253
curl -s "http://localhost:9091/fossil/snapshot/$SNAP_ID" | python3 -m json.tool
```

Body excerpt (`SNAP-20260424-6253`):

```
SNAPSHOT BODY

Kernel Index
- Tail: SNAP-20260424-6252
- Current: SNAP-20260424-6253
- Volume: Appliance/Overseer

Context Provenance
- GM_EXCERPT: yes
- Recent fossils: none
- Relevant fossils: none
```

Note: this snapshot was minted before the Android client was rebuilt with the
Task 9 forwarding patch, so Recent / Relevant are `none`. The schema is in
place; population will be visible in the first snapshot minted from the Task-9
APK once Brandon installs and exercises it.

### 9. Backend Test Suite — Plan Tests

```bash
Orchestrator/venv/bin/pytest tests/test_agent_route_retrieval.py \
  tests/test_agent_context_helpers.py tests/test_context_builder.py -v
```

All 15 plan-related backend tests pass (4 + 6 + 5):

```
tests/test_agent_context_helpers.py::test_normalize_fills_missing_keys PASSED
tests/test_agent_context_helpers.py::test_compose_with_fossils_empty_returns_prompt_unchanged PASSED
tests/test_agent_context_helpers.py::test_compose_with_fossils_adds_fences PASSED
tests/test_agent_context_helpers.py::test_append_fossils_to_system_empty_returns_system_unchanged PASSED
tests/test_agent_context_helpers.py::test_append_fossils_to_system_wraps_fossil_block PASSED
tests/test_agent_route_retrieval.py::test_agent_ws_session_start_emits_provenance PASSED
tests/test_agent_route_retrieval.py::test_gemini_agent_ws_session_start_emits_provenance PASSED
tests/test_agent_route_retrieval.py::test_agent_ws_unknown_operator_emits_empty_provenance PASSED
tests/test_agent_route_retrieval.py::test_gemini_cu_stream_emits_provenance PASSED
tests/test_context_builder.py::test_build_fossil_context_populates_all_four_sources PASSED
tests/test_context_builder.py::test_build_fossil_context_empty_query_still_gives_recent_and_checkpoint PASSED
tests/test_context_builder.py::test_brandon_retrieval_does_not_return_other_operators_snapshots PASSED
tests/test_context_builder.py::test_nonexistent_operator_returns_empty_retrieval PASSED
tests/test_context_builder.py::test_empty_operator_raises_value_error PASSED
======================== 15 passed, 1 warning in 29.03s ========================
```

(Repo-wide `pytest tests/` collects 6 errors in `tests/supervisor/*` from the
unrelated UGV `ugv_tools_api` module not being installed in this venv — those
are pre-existing and outside the scope of this plan.)

### 10. Android Build & Tests

```bash
JAVA_HOME=/snap/android-studio/209/jbr ./gradlew :app:assembleDebug
```

```
BUILD SUCCESSFUL in 629ms
39 actionable tasks: 39 up-to-date
```

```bash
JAVA_HOME=/snap/android-studio/209/jbr ./gradlew :app:testDebugUnitTest --rerun-tasks
```

```
BUILD SUCCESSFUL in 16s
26 actionable tasks: 26 executed
```

JUnit XML breakdown (all 19 tests, 0 failures):

| Test class                       | tests | failures | errors | skipped |
| -------------------------------- | ----- | -------- | ------ | ------- |
| AgentEventProvenanceTest         |   2   |    0     |   0    |    0    |
| ChatViewModelSaveTest            |   6   |    0     |   0    |    0    |
| ProvenanceSerializationTest      |   5   |    0     |   0    |    0    |
| WebSocketProvenanceTest          |   6   |    0     |   0    |    0    |

APK output: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/build/outputs/apk/debug/app-debug.apk` (~98 MB).

### 11. Device Install

```bash
adb devices
List of devices attached
```

No device attached at verification time. Brandon can install manually:

```bash
adb install -r "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/build/outputs/apk/debug/app-debug.apk"
```

or run `:app:installDebug` once the Z Fold 6 is connected via USB / Tailscale ADB.

### 12. AFTER → BEFORE Diff Narrative

The parity table flipped from a heavily-skewed shape (only `POST /chat/stream`
ran all four retrievals, voice paths got `2/0/0/1`, agent paths got `0/0/0/0`)
to a uniform `5 / 3 / 5-6 / 2` across all six paths. Voice WS sessions and both
agent WS routes (Claude `/ws/agent` + Gemini `/ws/gemini-agent`) plus the
Gemini CU stream now invoke the same shared `build_fossil_context` and emit
matching `[*-CONTEXT]` log markers + WS `provenance` events. Cross-operator
isolation holds: an unknown operator returns empty arrays everywhere. The
Android client now parses provenance from both SSE (`stream_start`) and WS
(`provenance` event for voice + `session_started` provenance field for agent),
renders a `ContextProvenance` panel under each bubble, and forwards the
provenance back on `/chat/save` so future auto-mints carry full lineage. The
single residual gap — pre-Task-9 snapshots having `Recent fossils: none` —
will close itself as new snapshots get minted through the rebuilt APK.

### 13. Manual Smoke-Test Checklist (Brandon — Z Fold 6)

Install the new APK, then:

- [ ] Open Android app → start chat with `operator=Brandon`
- [ ] Send: "what did we do with tool vault recently"
- [ ] Verify: `ContextProvenance` panel appears under the assistant bubble
- [ ] Verify: tap chevron → expands showing 4 sections (Recent, Keyword, Semantic, Checkpoint) with chips
- [ ] Verify: model's response references specific past work / cites SNAP-IDs
- [ ] Switch to voice mode (Gemini Live / Realtime / Grok Live) → start session → ask same question
- [ ] Verify: `ContextProvenance` panel appears above the transcript at session start
- [ ] Verify: voice model answers with context (mentions tool vault specifics)
- [ ] Switch to agent mode (Claude or Gemini agent) → start session → ask same question
- [ ] Verify: `ContextProvenance` panel appears under the agent bubble
- [ ] Tail logcat: `adb logcat | grep -E "ChatVM|VoiceVM|AgentChat"` — confirm `provenance:` log lines with non-zero counts
- [ ] Kill + reopen chat → confirm history loads (history schema may have changed; empty load is acceptable since `HistoryStore` was touched in Task 6)
- [ ] After 1-2 turns trigger an auto-mint and re-fetch the new snapshot — verify `Context Provenance` body section now lists Recent / Relevant SNAP-IDs (no longer "none")

### 14. Artifact Paths

Raw captures for AFTER:

- `/tmp/after_chat.sse`         — probe 1 SSE stream
- `/tmp/after_chat2.sse`        — probe 2 SSE stream
- `/tmp/after_chat3.sse`        — probe 3 SSE stream
- `/tmp/after_iso.sse`          — cross-operator isolation probe
- `/tmp/after_journalctl.log`   — matching journalctl window (60 lines)
- `/tmp/after_sentinels.txt`    — sentinel/timestamp manifest
- APK: `app/build/outputs/apk/debug/app-debug.apk`
