# Portal Performance & Concurrency Overhaul

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the BlackBox Portal snappy and responsive for 6-7 concurrent users over Tailscale by eliminating event loop blocking, adding compression, and maximizing hardware utilization on the dedicated Mini-ITX (12 cores, 30GB RAM).

**Architecture:** The root cause is blocking synchronous `requests.post/get()` calls inside async FastAPI handlers — when one user's LLM call blocks, ALL other users freeze (WebSocket pings, page loads, health checks). Fix by offloading blocking calls to a thread pool via `run_in_executor()`, add GzipMiddleware for static files over Tailscale, enable uvloop for 20-30% faster event loop, and raise system limits to utilize the dedicated hardware.

**Tech Stack:** Python asyncio + `run_in_executor()`, uvloop, FastAPI GzipMiddleware, httpx (async HTTP), systemd resource limits

---

## System Profile

- **Hardware**: Mini-ITX, 12 CPU cores, 30GB RAM (dedicated to BlackBox)
- **OS overhead**: ~2GB RAM, leaving ~28GB for BlackBox
- **Current usage**: Single uvicorn worker, 2.3GB RSS, MemoryMax=8GB
- **Snapshot index**: 256MB JSON (loaded per-process)
- **Concurrent users**: 4-7 over Tailscale (phone, PC, wife's device, localhost)

## Why NOT multi-worker (yet)

Multi-worker uvicorn (`--workers 4`) would use all 12 cores, but the codebase has in-memory shared state (agent sessions, app registry, task queue, operator state) that would break across processes. That's a larger refactor for the future. The thread pool approach gives us concurrency within a single process — good enough for 6-7 users.

---

### Task 1: System config — uvloop, memory, CPU priority

**Files:**
- Modify: `/etc/systemd/system/blackbox.service`

**What this does:** Enable uvloop (20-30% faster event loop), raise memory limit from 8GB to 24GB, give BlackBox high CPU priority since this is a dedicated machine.

**Step 1: Update the systemd service file**

```ini
[Unit]
Description=AI BlackBox Flight Recorder
After=network.target

[Service]
Type=simple
User=ai-black-box-fc
WorkingDirectory=/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
ExecStart=/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/venv/bin/python -m uvicorn Orchestrator.app:app --host 0.0.0.0 --port 9091 --timeout-keep-alive 120 --limit-max-requests 10000 --loop uvloop
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Dedicated machine — give BlackBox maximum resources
MemoryMax=24G
CPUWeight=200
Nice=-5

# Security hardening
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

**Changes from current:**
- Added `--loop uvloop` (uses libuv-based event loop, already installed in venv)
- `MemoryMax=8G` → `MemoryMax=24G` (machine has 30GB, OS needs ~2GB, leaves ~4GB buffer)
- Added `CPUWeight=200` (higher priority than default 100)
- Added `Nice=-5` (mild priority boost)

**Step 2: Apply and restart**

```bash
sudo cp /etc/systemd/system/blackbox.service /etc/systemd/system/blackbox.service.bak
# (edit the file)
sudo systemctl daemon-reload
sudo systemctl restart blackbox.service
```

**Step 3: Verify uvloop is active**

```bash
journalctl -u blackbox.service --since "1 min ago" | grep -i "uvloop\|started"
```

Expected: Service starts successfully. uvloop is used automatically when `--loop uvloop` is passed.

---

### Task 2: GzipMiddleware + static file cache headers

**Files:**
- Modify: `Orchestrator/checkpoint.py` (where middleware is added, around line 384)
- Modify: `Orchestrator/startup.py` (where static mount is, around line 351)

**What this does:** Compress all HTTP responses (HTML, JS, CSS, JSON) with gzip. Over Tailscale, this reduces transfer sizes by 60-80%. Also add proper cache headers for static assets so browsers don't re-download on every page load.

**Step 1: Add GzipMiddleware in checkpoint.py**

Find the existing CORS middleware block (around line 384):
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    ...
)
```

Add GzipMiddleware BEFORE the CORS middleware (middleware runs in reverse order of addition, so add Gzip first so it runs last, compressing the final response):

```python
from starlette.middleware.gzip import GzipMiddleware

# Gzip compress all responses > 500 bytes (huge win for Tailscale connections)
app.add_middleware(GzipMiddleware, minimum_size=500)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Step 2: Add cache-aware static file serving in startup.py**

Replace the static mount (line 351):

```python
app.mount("/ui", StaticFiles(directory="Portal", html=True), name="ui")
```

With a custom middleware that adds Cache-Control headers for static assets:

```python
from starlette.middleware.base import BaseHTTPMiddleware

class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Add Cache-Control headers for static assets served from /ui/."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/ui/"):
            # CSS/JS/images: cache for 1 hour, revalidate
            if any(path.endswith(ext) for ext in ('.css', '.js', '.png', '.jpg', '.svg', '.woff2', '.ico')):
                response.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
            # HTML: no cache (always fresh)
            elif path.endswith('.html') or path == "/ui/" or path == "/ui":
                response.headers["Cache-Control"] = "no-cache"
        return response

app.add_middleware(StaticCacheMiddleware)
app.mount("/ui", StaticFiles(directory="Portal", html=True), name="ui")
```

**IMPORTANT**: Add the `StaticCacheMiddleware` class and `app.add_middleware(StaticCacheMiddleware)` call BEFORE the `app.mount("/ui", ...)` line in startup.py. The middleware must be registered before the mount.

**Step 3: Verify**

```bash
# Check gzip is working
curl -s -H "Accept-Encoding: gzip" -D - http://localhost:9091/ui/ 2>&1 | grep -i "content-encoding"
# Expected: content-encoding: gzip

# Check cache headers for JS
curl -s -D - http://localhost:9091/ui/app.js 2>&1 | grep -i "cache-control"
# Expected: Cache-Control: public, max-age=3600, must-revalidate
```

---

### Task 3: Create async thread pool helper

**Files:**
- Create: `Orchestrator/utils/__init__.py` (empty, make it a package)
- Create: `Orchestrator/utils/async_helpers.py`

**What this does:** Provides a utility to run blocking synchronous functions in a thread pool without blocking the event loop. This is the foundation for fixing all the blocking LLM calls.

**Step 1: Create the utils package**

```bash
mkdir -p Orchestrator/utils
touch Orchestrator/utils/__init__.py
```

**Step 2: Create async_helpers.py**

```python
"""Async utilities for offloading blocking I/O to thread pools.

The BlackBox Orchestrator uses synchronous `requests` library for LLM API calls.
These block the uvicorn event loop, freezing all concurrent users.
These helpers offload blocking calls to a dedicated thread pool.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

# Dedicated thread pool for LLM API calls (8 threads = 8 concurrent LLM calls)
# Separate from uvicorn's default pool to prevent starvation
_llm_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm-io")

# Lighter pool for web tools, TTS, and other I/O
_io_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="misc-io")


async def run_blocking(func, *args, executor=None, **kwargs):
    """Run a blocking function in a thread pool without blocking the event loop.

    Usage:
        # Instead of:
        result = requests.post(url, json=payload, timeout=200)

        # Do:
        result = await run_blocking(requests.post, url, json=payload, timeout=200)

    Args:
        func: Synchronous callable
        *args: Positional arguments
        executor: ThreadPoolExecutor (default: _llm_executor)
        **kwargs: Keyword arguments

    Returns:
        Whatever func returns
    """
    loop = asyncio.get_running_loop()
    pool = executor or _llm_executor
    return await loop.run_in_executor(pool, lambda: func(*args, **kwargs))
```

**Step 3: Verify import works**

```bash
cd /home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc
python3 -c "from Orchestrator.utils.async_helpers import run_blocking, _llm_executor; print('OK')"
```

---

### Task 4: Unblock LLM streaming — OpenAI handler

**Files:**
- Modify: `Orchestrator/routes/chat_routes.py`

**What this does:** The OpenAI streaming handler calls `requests.post()` synchronously, blocking the event loop for 1-5 seconds during connection. We wrap it in `run_blocking()` so other users aren't frozen.

**Step 1: Add import at top of chat_routes.py**

Near the other imports at the top of the file, add:

```python
from Orchestrator.utils.async_helpers import run_blocking
```

**Step 2: Find and fix call_openai() call in streaming handler**

The `call_openai()` function (line ~56) is synchronous and does `requests.post()` at line ~167. It's called from the async `event_generator()` inside `chat_stream_post()`.

Find every place in chat_routes.py where `call_openai(` is called inside an async function/generator. For each one, change:

```python
# BEFORE (blocks event loop):
r = call_openai(messages, model, stream=True, ...)

# AFTER (runs in thread pool):
r = await run_blocking(call_openai, messages, model, stream=True, ...)
```

**Step 3: Add yield points in streaming loops**

After the `requests.post()` fix, find the streaming iteration loops (`for line in r.iter_lines()`) that follow each OpenAI call. After each `yield` statement inside these loops, add:

```python
yield f"data: {json.dumps(chunk)}\n\n"
await asyncio.sleep(0)  # Yield control to event loop between chunks
```

The `await asyncio.sleep(0)` costs nearly nothing but gives the event loop a chance to handle other connections (WebSocket pings, health checks, page loads) between streaming chunks.

**Step 4: Verify**

Restart service, open Portal, send a chat message with OpenAI provider. Confirm:
1. Response streams normally
2. Other Portal tabs remain responsive during streaming
3. No errors in journalctl

---

### Task 5: Unblock LLM streaming — Anthropic, Gemini, xAI handlers

**Files:**
- Modify: `Orchestrator/routes/chat_routes.py`

**What this does:** Same fix as Task 4, applied to the remaining 3 LLM providers.

**Step 1: Fix call_anthropic() calls**

`call_anthropic()` is at line ~227, does `requests.post()` at line ~320. Find every async call site and wrap:

```python
r = await run_blocking(call_anthropic, messages, model, stream=True, ...)
```

**Step 2: Fix call_gemini() calls**

`call_gemini()` is at line ~823, does `requests.post()` at line ~952. Find every async call site and wrap:

```python
r = await run_blocking(call_gemini, messages, model, ...)
```

**Step 3: Fix call_xai() calls**

`call_xai()` is at line ~1356, does `requests.post()` at line ~1486. Find every async call site and wrap:

```python
r = await run_blocking(call_xai, messages, model, stream=True, ...)
```

**Step 4: Add yield points to ALL streaming loops**

For each provider's streaming iteration, add `await asyncio.sleep(0)` after each yield.

**Step 5: Fix inline blocking calls**

Search for any `requests.post("http://localhost:9091/` calls in async generators (line ~640, ~1207 — internal phone call triggers). Wrap these too:

```python
await run_blocking(requests.post, "http://localhost:9091/twilio/call", json=payload, timeout=30)
```

**Step 6: Verify all providers**

Test chat with each provider (OpenAI, Anthropic, Gemini, Grok). Confirm streaming works and other tabs stay responsive.

---

### Task 6: Unblock tool execution — web search and web fetch

**Files:**
- Modify: `Orchestrator/web_tools.py`

**What this does:** Web search (Perplexity API) and web fetch are called during tool use in chat. They use synchronous `requests` and block the event loop for up to 30 seconds.

**Step 1: Make perform_web_search async-aware**

The `perform_web_search()` function (line ~165) calls `requests.post()` to Perplexity. Since it's called from within the chat streaming generators (which we're already running in threads from Tasks 4-5), this should now be automatically non-blocking.

However, if it's called from any OTHER async context, add the same pattern. Check all call sites:

```bash
grep -n "perform_web_search\|perform_web_fetch" Orchestrator/routes/chat_routes.py
```

If any call site is in an async function that's NOT already wrapped in `run_blocking`, wrap it:

```python
result = await run_blocking(perform_web_search, query, search_recency_filter=recency)
```

**Step 2: Check web_fetch**

Same for `perform_web_fetch()` — if called from async context outside the threaded LLM handlers, wrap it.

**Step 3: Verify**

Send a chat message that triggers web search (e.g., "search for today's news"). Confirm other tabs stay responsive during the search.

---

### Task 7: Reduce polling frequencies

**Files:**
- Modify: `Portal/modules/app-init.js` (health check interval)
- Modify: `Orchestrator/routes/agent_routes.py` (agent output polling)

**What this does:** Reduce unnecessary HTTP request spam. With 7 users, aggressive polling creates 100+ spurious requests/min that compete with real traffic.

**Step 1: Reduce health check from 15s to 60s**

In `Portal/modules/app-init.js`, find the health check interval (around line 712):

```javascript
// BEFORE:
setInterval(refreshHealth, 15000);

// AFTER:
setInterval(refreshHealth, 60000);
```

**Step 2: Reduce agent output polling from 20ms to 100ms**

In `Orchestrator/routes/agent_routes.py`, find `asyncio.sleep(0.02)` calls in the output polling loops (lines ~374, ~428, ~736). Change to:

```python
# BEFORE:
await asyncio.sleep(0.02)   # 50Hz — way too aggressive

# AFTER:
await asyncio.sleep(0.1)    # 10Hz — still fast enough for real-time feel
```

This reduces CPU context switches by 5x while still feeling responsive (100ms latency is imperceptible for text streaming).

**Step 3: Reduce gateway polling from 5s to 30s**

In `Portal/modules/telephony-manager.js`, find the gateway poll interval (around line 468):

```javascript
// BEFORE:
_pollInterval = setInterval(fetchGateways, 5000);

// AFTER:
_pollInterval = setInterval(fetchGateways, 30000);
```

**Step 4: Verify**

Open browser devtools Network tab. Confirm polling requests are less frequent. System should feel the same responsiveness but with much less background traffic.

---

### Task 8: Optimize SQLite task database

**Files:**
- Modify: wherever the SQLite connection is opened for tasks.db

**What this does:** The 91MB tasks.db uses default SQLite settings. WAL mode + PRAGMA optimizations make concurrent reads non-blocking and writes faster.

**Step 1: Find the SQLite connection setup**

Search for the tasks.db connection:

```bash
grep -rn "tasks.db\|sqlite3\|connect(" Orchestrator/models.py Orchestrator/state.py Orchestrator/tasks.py
```

**Step 2: Add PRAGMA optimizations**

At the connection creation point, add:

```python
conn = sqlite3.connect("Portal/tasks.db")
conn.execute("PRAGMA journal_mode=WAL")         # Write-ahead logging (concurrent reads during writes)
conn.execute("PRAGMA synchronous=NORMAL")        # Faster writes, still safe
conn.execute("PRAGMA cache_size=-64000")          # 64MB cache (default 2MB)
conn.execute("PRAGMA busy_timeout=5000")          # Wait 5s instead of failing on lock
conn.execute("PRAGMA temp_store=MEMORY")          # Temp tables in RAM
```

**Step 3: Vacuum the database**

One-time cleanup to reclaim space:

```bash
sqlite3 Portal/tasks.db "VACUUM; ANALYZE;"
```

**Step 4: Verify**

```bash
sqlite3 Portal/tasks.db "PRAGMA journal_mode; PRAGMA cache_size;"
# Expected: wal, -64000
```

---

### Task 9: Restart and integration test

**Step 1: Restart service**

```bash
sudo systemctl daemon-reload
sudo systemctl restart blackbox.service
```

**Step 2: Verify startup**

```bash
# Wait for startup (60-90s for index rebuild)
sleep 90
systemctl status blackbox.service
curl -s -o /dev/null -w "%{http_code}" http://localhost:9091/agent/apps
```

**Step 3: Test gzip compression**

```bash
# Uncompressed size
curl -s http://localhost:9091/ui/app.js | wc -c

# Compressed size
curl -s -H "Accept-Encoding: gzip" http://localhost:9091/ui/app.js --compressed | wc -c
```

Expected: Compressed size is 60-80% smaller.

**Step 4: Test concurrent load**

Open 4 Portal tabs simultaneously. In tab 1, send a long chat message. Verify:
- Tab 2, 3, 4 remain responsive (can type, scroll, click)
- Health check doesn't stall
- "Connected" status stays green
- WebSocket doesn't disconnect

**Step 5: Test Tailscale access**

From phone/another PC over Tailscale:
- Portal loads within 2-3 seconds (vs 5-10+ before)
- "Connected" shows quickly
- Chat streaming works concurrently with other users

**Step 6: Memory check**

```bash
# Verify service is using resources properly
ps aux | grep uvicorn | grep -v grep
# RSS should be < 4GB with room to grow
free -h
# Available should be > 10GB
```

---

## Expected Performance Improvements

| Metric | Before | After | Why |
|--------|--------|-------|-----|
| Portal load (Tailscale) | 5-10s | 1-3s | Gzip + cache headers |
| "Connected" status | 5-15s | 1-2s | Event loop not blocked |
| Chat during another user's chat | Frozen 1-5s | Instant | run_in_executor frees loop |
| Event loop throughput | ~70% | ~100% | uvloop |
| Background requests/min | 100+ | ~20 | Reduced polling |
| Memory headroom | 5.7GB | 21.7GB | MemoryMax 8G → 24G |
| SQLite reads during write | Blocked | Non-blocking | WAL mode |

## File Change Summary

| File | Changes |
|------|---------|
| `/etc/systemd/system/blackbox.service` | uvloop, MemoryMax=24G, CPUWeight, Nice |
| `Orchestrator/checkpoint.py` | Add GzipMiddleware |
| `Orchestrator/startup.py` | Add StaticCacheMiddleware |
| `Orchestrator/utils/__init__.py` | New (empty package init) |
| `Orchestrator/utils/async_helpers.py` | New (run_blocking helper + thread pools) |
| `Orchestrator/routes/chat_routes.py` | Wrap all call_*/requests.post in run_blocking, add yield points |
| `Orchestrator/web_tools.py` | Wrap blocking calls if in async context |
| `Portal/modules/app-init.js` | Health check 15s → 60s |
| `Orchestrator/routes/agent_routes.py` | Agent polling 20ms → 100ms |
| `Portal/modules/telephony-manager.js` | Gateway poll 5s → 30s |
| `Portal/tasks.db` | SQLite PRAGMA optimizations |
