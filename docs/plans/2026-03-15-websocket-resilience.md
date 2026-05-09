# WebSocket Resilience Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make all Portal WebSocket connections resilient to server restarts, OOM kills, network hiccups, and transient errors — so users never see permanent "lost signal" without automatic recovery.

**Architecture:** Three layers of defense: (1) Portal-side auto-reconnect with exponential backoff on ALL WS handlers, (2) safe JSON parsing that doesn't break connections, (3) server-side guarded sends that don't crash streaming tasks when clients disconnect. The agent handler (REST chat) gets the same treatment since it's the primary user-facing path.

**Tech Stack:** JavaScript (Portal ES6 modules), Python (FastAPI WebSocket handlers)

---

## Task 1: Portal — Robust Reconnection for Agent Handler

The agent handler (`agent-handler.js`) has ZERO reconnection logic. When the WS closes unexpectedly, it just shows "Disconnected" and gives up. This is the most common user-facing WS connection.

**Files:**
- Modify: `Portal/modules/agent-handler.js`

**What to change:**

### Step 1: Add reconnection state to the class

Near the top of the AgentHandler class (around line 20-30 where other instance vars are), add:

```javascript
this.reconnectAttempts = 0;
this.maxReconnectAttempts = 15;
this.reconnectTimer = null;
```

### Step 2: Add an attemptReconnect method

Add this method to the AgentHandler class (after the `updateBannerStatus` method, around line 295):

```javascript
attemptReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
        console.log('[Agent] Max reconnect attempts reached');
        this.updateBannerStatus('Connection Lost');
        toast('Agent connection lost - click Send to retry');
        return;
    }

    this.reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts - 1), 30000);
    console.log(`[Agent] Reconnecting (${this.reconnectAttempts}/${this.maxReconnectAttempts}) in ${delay}ms...`);
    this.updateBannerStatus(`Reconnecting (${this.reconnectAttempts})...`);

    this.reconnectTimer = setTimeout(() => {
        if (!this.sessionId) return;

        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}/ws/agent/${this.sessionId}`;

        try {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('[Agent] Reconnected successfully');
                this.reconnectAttempts = 0;
                this.updateBannerStatus('Reconnected', true);
                toast('Agent reconnected', 2000);

                this.ws.send(JSON.stringify({
                    type: 'reconnect',
                    operator: this.currentOperator || window.__operator || 'Brandon'
                }));
            };

            this.ws.onmessage = (e) => {
                try {
                    const msg = JSON.parse(e.data);
                    this.handleMessage(msg);
                } catch (err) {
                    console.error('[Agent] Parse error on reconnect:', err);
                }
            };

            this.ws.onclose = (e) => {
                if (e.code !== 1000) {
                    this.attemptReconnect();
                } else {
                    this.isStreaming = false;
                    this.updateBannerStatus('Session Complete');
                    statusLine.endSession();
                }
            };

            this.ws.onerror = () => {
                // onclose will fire after this, which triggers reconnect
            };
        } catch (err) {
            console.error('[Agent] Reconnect WebSocket creation failed:', err);
            this.attemptReconnect();
        }
    }, delay);
}
```

### Step 3: Update the original onclose handler (line ~407-415)

Change the existing `this.ws.onclose` handler to trigger reconnection on unexpected closes:

```javascript
// BEFORE:
this.ws.onclose = (e) => {
    console.log('[Agent] WebSocket closed:', e.code, e.reason);
    this.isStreaming = false;
    this.updateBannerStatus('Disconnected');
    if (this.currentBubble) {
        this.currentBubble.classList.remove('agent-streaming');
    }
    statusLine.endSession();
};

// AFTER:
this.ws.onclose = (e) => {
    console.log('[Agent] WebSocket closed:', e.code, e.reason);
    if (e.code !== 1000 && this.sessionId) {
        // Unexpected close — attempt reconnection
        this.attemptReconnect();
    } else {
        this.isStreaming = false;
        this.updateBannerStatus('Disconnected');
        if (this.currentBubble) {
            this.currentBubble.classList.remove('agent-streaming');
        }
        statusLine.endSession();
    }
};
```

### Step 4: Wrap JSON.parse safely in onmessage (line ~397-405)

The existing handler is already wrapped in try/catch but doesn't recover. Change to:

```javascript
// BEFORE:
this.ws.onmessage = (e) => {
    console.log('[Agent] Received:', e.data);
    try {
        const msg = JSON.parse(e.data);
        this.handleMessage(msg);
    } catch (err) {
        console.error('[Agent] Parse error:', err);
    }
};

// AFTER:
this.ws.onmessage = (e) => {
    try {
        const msg = JSON.parse(e.data);
        this.handleMessage(msg);
    } catch (err) {
        console.error('[Agent] Parse error:', err, 'data:', e.data?.substring?.(0, 200));
    }
};
```

(Also remove the verbose console.log of every received message — it floods the console.)

### Step 5: Fix the follow-up WS (line ~1432-1475) and session reconnect WS (line ~1693-1731)

Both of these create new WebSocket instances but with NO reconnect on unexpected close. Update their `onclose` handlers to the same pattern:

```javascript
// For both follow-up (line ~1462) and session reconnect (line ~1725):
this.ws.onclose = (e) => {
    if (e.code !== 1000 && this.sessionId) {
        this.attemptReconnect();
    } else {
        this.isStreaming = false;
        statusLine.endSession();
    }
};
```

Also wrap their `onmessage` handlers in try/catch if not already.

### Step 6: Reset reconnect counter on successful connection

In the original `onopen` handler (line ~372) and in the follow-up `onopen` (line ~1440):

```javascript
// Add at the start of each onopen:
this.reconnectAttempts = 0;
```

### Step 7: Verify

- Open Portal, start an agent session
- Restart the BlackBox service (`sudo systemctl restart blackbox.service`)
- Portal should show "Reconnecting (1)..." then "Reconnected" within a few seconds
- The agent session should resume streaming

---

## Task 2: Portal — Increase Reconnect Resilience for Live Voice Handlers

The GPT Realtime, Gemini Live, and Grok Live handlers have reconnect logic but cap at 5 attempts with max 15s delay. After a server restart (~15s), 5 attempts aren't enough.

**Files:**
- Modify: `Portal/modules/gpt-realtime.js`
- Modify: `Portal/modules/gemini-live.js`
- Modify: `Portal/modules/grok-live.js`

**What to change in each file:**

### Step 1: Increase MAX_RECONNECT_ATTEMPTS

```javascript
// BEFORE (gpt-realtime.js line 155, similar in gemini/grok):
const MAX_RECONNECT_ATTEMPTS = 5;

// AFTER:
const MAX_RECONNECT_ATTEMPTS = 15;
```

### Step 2: Increase backoff cap from 15s to 30s

In each file's `attemptReconnect()` function:

```javascript
// BEFORE:
const delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 15000);

// AFTER:
const delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 30000);
```

### Step 3: Verify

- Open a GPT Realtime voice session
- Restart the service
- Should reconnect within 30 seconds instead of permanently dying after 5 attempts

---

## Task 3: Server — Guard All WebSocket Sends in Streaming Tasks

The `streaming_reader_task` in `agent_routes.py` crashes when the client disconnects because it calls `websocket.send_json()` on a closed connection. This leaves the agent process orphaned.

**Files:**
- Modify: `Orchestrator/routes/agent_routes.py`

**What to change:**

### Step 1: Add a safe_send helper at module level

Near the top of agent_routes.py (after imports, before the route handlers), add:

```python
from starlette.websockets import WebSocketState

async def _safe_ws_send(websocket: WebSocket, data: dict) -> bool:
    """Send JSON to WebSocket, return False if connection is dead."""
    try:
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.send_json(data)
            return True
    except Exception:
        pass
    return False
```

### Step 2: Replace all `await websocket.send_json(...)` in streaming_reader_task with `await _safe_ws_send(websocket, ...)`

In the `streaming_reader_task` function (lines 487-710), find every `await websocket.send_json(...)` call and replace with:

```python
# BEFORE:
await websocket.send_json({"type": "raw", "data": line})

# AFTER:
if not await _safe_ws_send(websocket, {"type": "raw", "data": line}):
    print(f"[AGENT] Client disconnected, stopping stream")
    return
```

Apply this pattern to ALL send_json calls in streaming_reader_task. If any send fails, break out of the loop gracefully instead of crashing the task.

### Step 3: Also guard the final "completed" send at the end of streaming_reader_task

```python
# BEFORE:
await websocket.send_json({"type": "completed", "data": None})

# AFTER:
await _safe_ws_send(websocket, {"type": "completed", "data": None})
```

### Step 4: Guard sends in the main WebSocket handler

In the main `agent_websocket` handler (lines 712-1045), any `await websocket.send_json(...)` that happens outside the receive loop should also use `_safe_ws_send`.

### Step 5: Verify

- Start an agent session, begin streaming
- Close the browser tab abruptly (not graceful close)
- Server logs should show "Client disconnected, stopping stream" instead of a traceback
- The agent process should continue running in the background

---

## Task 4: Server — Guard WebSocket Sends in Live Voice Routes

Same issue as Task 3 but for the realtime/gemini/grok voice routes. The `openai_listener`, `gemini_listener`, and `grok_listener` tasks forward messages to `session.portal_ws` without checking if it's still connected.

**Files:**
- Modify: `Orchestrator/routes/realtime_routes.py`
- Modify: `Orchestrator/routes/gemini_live_routes.py`
- Modify: `Orchestrator/routes/grok_live_routes.py`

**What to change in each file:**

### Step 1: Add the same _safe_ws_send helper to each file

```python
from starlette.websockets import WebSocketState

async def _safe_ws_send(websocket, data: dict) -> bool:
    """Send JSON to WebSocket, return False if connection is dead."""
    try:
        if websocket and hasattr(websocket, 'application_state') and websocket.application_state == WebSocketState.CONNECTED:
            await websocket.send_json(data)
            return True
    except Exception:
        pass
    return False
```

### Step 2: In each listener function, guard all portal_ws sends

Find all `await session.portal_ws.send_json(...)` calls and replace with:

```python
# BEFORE:
await session.portal_ws.send_json({"type": "audio", "data": audio_b64})

# AFTER:
if not await _safe_ws_send(session.portal_ws, {"type": "audio", "data": audio_b64}):
    print(f"[REALTIME] Portal disconnected, stopping listener")
    break
```

### Step 3: Verify

- Start a GPT Realtime voice session
- Close the browser tab
- Server should cleanly stop the listener instead of crashing with a traceback

---

## Execution Order

| Task | What | Scope | Effort |
|------|------|-------|--------|
| 1 | Agent handler reconnection | Portal JS (1 file) | 20 min |
| 2 | Live voice reconnect resilience | Portal JS (3 files) | 10 min |
| 3 | Server-side guarded sends (agent) | Python (1 file) | 15 min |
| 4 | Server-side guarded sends (live voice) | Python (3 files) | 15 min |

Tasks 1-2 are Portal-only (no restart needed). Tasks 3-4 are server-side (restart needed after).

**Recommended grouping:**
- **Agent A:** Tasks 1 + 2 (Portal JS changes)
- **Agent B:** Tasks 3 + 4 (Python server changes)
