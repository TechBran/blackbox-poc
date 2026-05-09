# Interactive Computer Use Viewer — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an interactive modal overlay to the Portal that lets users click, type, and scroll on the Computer Use browser in real time, plus a VNC fallback for complex interactions.

**Architecture:** Three layers — (1) Portal modal with screenshot polling + click/type/scroll event capture, (2) Backend interaction API using xdotool on Xvfb :99, (3) x11vnc for full VNC fallback from the desktop. The CU model stays live during user interaction — user actions appear in the model's next screenshot naturally.

**Tech Stack:** JavaScript (Portal modal), Python/FastAPI (API endpoints), xdotool (X11 input), x11vnc (VNC server), scrot (screenshots)

---

## Task 1: Create xdotool Interaction Module

**Why:** Foundation for all interaction endpoints. Wraps xdotool commands with input validation and error handling.

**Files:**
- Create: `Orchestrator/browser/interaction.py`

### Step 1: Create the interaction module

```python
"""
interaction.py - User interaction with the Xvfb browser display via xdotool.

Provides click, type, key press, and scroll functions that operate on the
virtual display. Used by browser_routes.py to handle Portal interaction API calls.
"""

import subprocess
import shlex
from typing import Optional
from Orchestrator.browser.config import DISPLAY_NUM, DISPLAY_WIDTH, DISPLAY_HEIGHT

DISPLAY = f":{DISPLAY_NUM}"

# xdotool button mapping
BUTTON_MAP = {
    "left": "1",
    "middle": "2",
    "right": "3",
}


def _run_xdotool(*args: str, timeout: float = 5.0) -> dict:
    """Run an xdotool command on the virtual display."""
    env_display = {"DISPLAY": DISPLAY}
    cmd = ["xdotool"] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**subprocess.os.environ, **env_display},
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "xdotool command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def click(x: int, y: int, button: str = "left") -> dict:
    """Click at coordinates on the virtual display.

    Args:
        x: X coordinate (0 to DISPLAY_WIDTH)
        y: Y coordinate (0 to DISPLAY_HEIGHT)
        button: "left", "right", or "middle"
    """
    x = max(0, min(x, DISPLAY_WIDTH))
    y = max(0, min(y, DISPLAY_HEIGHT))
    btn = BUTTON_MAP.get(button, "1")

    # Move mouse then click
    move_result = _run_xdotool("mousemove", str(x), str(y))
    if not move_result["success"]:
        return move_result

    if button == "double":
        # Double click = two rapid left clicks
        _run_xdotool("click", "--repeat", "2", "--delay", "80", "1")
        return {"success": True, "action": "double_click", "x": x, "y": y}

    result = _run_xdotool("click", btn)
    return {"success": result["success"], "action": "click", "x": x, "y": y, "button": button}


def type_text(text: str) -> dict:
    """Type text into the currently focused element.

    Args:
        text: The text string to type
    """
    if not text:
        return {"success": False, "error": "Empty text"}

    # xdotool type handles most printable characters
    result = _run_xdotool("type", "--delay", "12", "--clearmodifiers", text)
    return {"success": result["success"], "action": "type", "length": len(text)}


def press_key(key: str) -> dict:
    """Press a special key or key combination.

    Args:
        key: Key name (Return, Tab, BackSpace, Escape, Up, Down, Left, Right,
             space, ctrl+a, ctrl+c, ctrl+v, alt+Tab, etc.)
    """
    if not key:
        return {"success": False, "error": "Empty key"}

    # Validate key name - allow alphanumeric, plus, underscore
    # xdotool key names: Return, Tab, BackSpace, Escape, Up, Down, Left, Right, etc.
    # Combos: ctrl+a, ctrl+c, ctrl+v, alt+Tab, shift+Tab, etc.
    allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+_")
    if not all(c in allowed_chars for c in key):
        return {"success": False, "error": f"Invalid key name: {key}"}

    result = _run_xdotool("key", "--clearmodifiers", key)
    return {"success": result["success"], "action": "key", "key": key}


def scroll(x: int, y: int, direction: str = "down", clicks: int = 3) -> dict:
    """Scroll at a position on the virtual display.

    Args:
        x: X coordinate to scroll at
        y: Y coordinate to scroll at
        direction: "up" or "down"
        clicks: Number of scroll clicks (1-10)
    """
    x = max(0, min(x, DISPLAY_WIDTH))
    y = max(0, min(y, DISPLAY_HEIGHT))
    clicks = max(1, min(clicks, 10))

    # Button 4 = scroll up, button 5 = scroll down
    btn = "4" if direction == "up" else "5"

    # Move to position first
    move_result = _run_xdotool("mousemove", str(x), str(y))
    if not move_result["success"]:
        return move_result

    result = _run_xdotool("click", "--repeat", str(clicks), "--delay", "50", btn)
    return {"success": result["success"], "action": "scroll", "direction": direction, "clicks": clicks}
```

### Step 2: Verify xdotool works on :99

Run:
```bash
DISPLAY=:99 xdotool getmouselocation
```
Expected: Something like `x:640 y:360 screen:0 window:12345678`

### Step 3: Commit

```bash
git add Orchestrator/browser/interaction.py
git commit -m "feat(browser): add xdotool interaction module for CU viewer"
```

---

## Task 2: Add Interaction API Endpoints

**Why:** The Portal modal needs HTTP endpoints to send clicks, keystrokes, and scroll events to the virtual display.

**Files:**
- Modify: `Orchestrator/routes/browser_routes.py` — Add 5 new endpoints

### Step 1: Add the interaction endpoints

Add these endpoints to `browser_routes.py`, importing from the new interaction module:

```python
import time
from pathlib import Path
from Orchestrator.browser.interaction import click, type_text, press_key, scroll
from Orchestrator.browser.screenshot import capture_screenshot, save_screenshot_to_uploads
from Orchestrator.config import UPLOADS_DIR


@app.post("/browser/click")
async def browser_click(body: dict = Body(...)):
    """Send a mouse click to the virtual display."""
    x = int(body.get("x", 0))
    y = int(body.get("y", 0))
    button = body.get("button", "left")
    result = click(x, y, button)
    return result


@app.post("/browser/type")
async def browser_type(body: dict = Body(...)):
    """Type text into the focused element on the virtual display."""
    text = body.get("text", "")
    result = type_text(text)
    return result


@app.post("/browser/key")
async def browser_key(body: dict = Body(...)):
    """Press a special key on the virtual display."""
    key = body.get("key", "")
    result = press_key(key)
    return result


@app.post("/browser/scroll")
async def browser_scroll(body: dict = Body(...)):
    """Scroll at a position on the virtual display."""
    x = int(body.get("x", 640))
    y = int(body.get("y", 360))
    direction = body.get("direction", "down")
    clicks = int(body.get("clicks", 3))
    result = scroll(x, y, direction, clicks)
    return result


@app.get("/browser/screenshot/live")
async def browser_screenshot_live():
    """Capture a fresh screenshot for the interactive viewer.
    Returns URL with timestamp for cache-busting.
    Cleans up old live screenshots to prevent disk bloat.
    """
    try:
        png_bytes = capture_screenshot()
        ts = int(time.time() * 1000)
        filename = f"browser_live_{ts}.png"
        save_path = UPLOADS_DIR / filename
        save_path.write_bytes(png_bytes)
        url = f"/ui/uploads/{filename}"

        # Clean up old live screenshots (keep last 5)
        live_files = sorted(UPLOADS_DIR.glob("browser_live_*.png"), key=lambda p: p.stat().st_mtime)
        for old_file in live_files[:-5]:
            try:
                old_file.unlink()
            except OSError:
                pass

        return {"url": url, "timestamp": ts}
    except Exception as e:
        return {"error": str(e), "success": False}
```

### Step 2: Test endpoints with curl

```bash
# Screenshot
curl -s http://localhost:9091/browser/screenshot/live | python3 -m json.tool

# Click center of screen
curl -s -X POST http://localhost:9091/browser/click \
  -H "Content-Type: application/json" \
  -d '{"x": 640, "y": 360, "button": "left"}'

# Type text
curl -s -X POST http://localhost:9091/browser/type \
  -H "Content-Type: application/json" \
  -d '{"text": "hello"}'

# Press Enter
curl -s -X POST http://localhost:9091/browser/key \
  -H "Content-Type: application/json" \
  -d '{"key": "Return"}'

# Scroll down
curl -s -X POST http://localhost:9091/browser/scroll \
  -H "Content-Type: application/json" \
  -d '{"x": 640, "y": 360, "direction": "down", "clicks": 3}'
```

### Step 3: Commit

```bash
git add Orchestrator/routes/browser_routes.py
git commit -m "feat(browser): add click/type/key/scroll/live-screenshot API endpoints"
```

---

## Task 3: Create Interactive Modal (Portal JS)

**Why:** The main UI component. A fullscreen modal overlay that displays the live screenshot and captures user input events.

**Files:**
- Create: `Portal/modules/cu-interact.js`

### Step 1: Create the interactive modal module

```javascript
/**
 * cu-interact.js
 * Interactive Computer Use viewer modal.
 * Opens as a fullscreen overlay when user clicks the CU Live View screenshot.
 * Captures click, keyboard, and scroll events and sends them to the backend.
 */

import { $ } from './core-utils.js';

// =============================================================================
// State
// =============================================================================

let modal = null;
let pollingInterval = null;
let isOpen = false;
const POLL_MS = 500;
const DISPLAY_WIDTH = 1280;
const DISPLAY_HEIGHT = 720;

// =============================================================================
// Modal Creation
// =============================================================================

function createModal() {
    if (modal) return modal;

    modal = document.createElement('div');
    modal.className = 'cu-interact-overlay';
    modal.innerHTML = `
        <div class="cu-interact-modal">
            <div class="cu-interact-header">
                <div class="cu-interact-header-left">
                    <span class="cu-interact-dot"></span>
                    <span class="cu-interact-title">Interactive Browser</span>
                    <span class="cu-interact-step"></span>
                </div>
                <div class="cu-interact-header-right">
                    <button class="cu-interact-btn cu-interact-refresh" title="Force refresh">↻</button>
                    <button class="cu-interact-btn cu-interact-close" title="Close (ESC)">✕</button>
                </div>
            </div>
            <div class="cu-interact-body">
                <div class="cu-interact-screen-wrap">
                    <img class="cu-interact-screen" draggable="false" />
                    <div class="cu-interact-click-indicator"></div>
                </div>
            </div>
            <div class="cu-interact-status">
                <span class="cu-interact-status-text">Click to interact</span>
                <span class="cu-interact-hint">ESC to close</span>
            </div>
        </div>
    `;

    document.body.appendChild(modal);

    // Wire up events
    const closeBtn = modal.querySelector('.cu-interact-close');
    const refreshBtn = modal.querySelector('.cu-interact-refresh');
    const screen = modal.querySelector('.cu-interact-screen');

    closeBtn.addEventListener('click', close);
    refreshBtn.addEventListener('click', refreshScreenshot);
    screen.addEventListener('click', handleScreenClick);
    screen.addEventListener('wheel', handleScreenScroll, { passive: false });
    modal.addEventListener('keydown', handleKeyDown);

    // Prevent clicks on backdrop from propagating
    modal.addEventListener('click', (e) => {
        if (e.target === modal) close();
    });

    return modal;
}

// =============================================================================
// Open / Close
// =============================================================================

export function open(initialScreenshotUrl) {
    createModal();
    modal.classList.add('open');
    isOpen = true;

    // Set initial screenshot if provided
    if (initialScreenshotUrl) {
        modal.querySelector('.cu-interact-screen').src = initialScreenshotUrl;
    }

    // Focus modal for keyboard capture
    modal.setAttribute('tabindex', '-1');
    modal.focus();

    // Start polling for fresh screenshots
    refreshScreenshot();
    pollingInterval = setInterval(refreshScreenshot, POLL_MS);

    // Global ESC handler
    document.addEventListener('keydown', globalEscHandler);
}

export function close() {
    if (!isOpen) return;
    isOpen = false;
    modal.classList.remove('open');

    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }

    document.removeEventListener('keydown', globalEscHandler);
}

function globalEscHandler(e) {
    if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close();
    }
}

// =============================================================================
// Screenshot Polling
// =============================================================================

async function refreshScreenshot() {
    if (!isOpen) return;
    try {
        const res = await fetch('/browser/screenshot/live');
        const data = await res.json();
        if (data.url) {
            const screen = modal.querySelector('.cu-interact-screen');
            // Cache-bust with timestamp
            screen.src = data.url + '?t=' + data.timestamp;
        }
    } catch (e) {
        // Silently ignore polling errors
    }
}

// =============================================================================
// Input Handlers
// =============================================================================

function handleScreenClick(e) {
    e.preventDefault();
    const screen = modal.querySelector('.cu-interact-screen');
    const rect = screen.getBoundingClientRect();

    // Scale click position to Xvfb coordinates
    const x = Math.round(((e.clientX - rect.left) / rect.width) * DISPLAY_WIDTH);
    const y = Math.round(((e.clientY - rect.top) / rect.height) * DISPLAY_HEIGHT);

    // Show click indicator
    showClickIndicator(e.clientX - rect.left, e.clientY - rect.top, rect);

    // Send to backend
    fetch('/browser/click', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x, y, button: 'left' })
    });

    setStatus(`Clicked at ${x}, ${y}`);

    // Refresh screenshot shortly after click to show result
    setTimeout(refreshScreenshot, 200);
}

function handleScreenScroll(e) {
    e.preventDefault();
    const screen = modal.querySelector('.cu-interact-screen');
    const rect = screen.getBoundingClientRect();

    const x = Math.round(((e.clientX - rect.left) / rect.width) * DISPLAY_WIDTH);
    const y = Math.round(((e.clientY - rect.top) / rect.height) * DISPLAY_HEIGHT);
    const direction = e.deltaY > 0 ? 'down' : 'up';
    const clicks = Math.min(Math.ceil(Math.abs(e.deltaY) / 50), 5);

    fetch('/browser/scroll', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x, y, direction, clicks })
    });

    setStatus(`Scrolled ${direction}`);
    setTimeout(refreshScreenshot, 200);
}

function handleKeyDown(e) {
    // ESC is handled by globalEscHandler
    if (e.key === 'Escape') return;

    e.preventDefault();
    e.stopPropagation();

    // Map browser key names to xdotool key names
    const keyMap = {
        'Enter': 'Return',
        'Backspace': 'BackSpace',
        'Tab': 'Tab',
        'ArrowUp': 'Up',
        'ArrowDown': 'Down',
        'ArrowLeft': 'Left',
        'ArrowRight': 'Right',
        'Delete': 'Delete',
        'Home': 'Home',
        'End': 'End',
        'PageUp': 'Prior',
        'PageDown': 'Next',
        ' ': 'space',
    };

    // Handle modifier combos (ctrl+a, ctrl+c, ctrl+v, etc.)
    if (e.ctrlKey || e.metaKey) {
        const combo = 'ctrl+' + e.key.toLowerCase();
        fetch('/browser/key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: combo })
        });
        setStatus(`Key: ${combo}`);
        setTimeout(refreshScreenshot, 200);
        return;
    }

    if (e.altKey) {
        const combo = 'alt+' + e.key;
        fetch('/browser/key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: combo })
        });
        setStatus(`Key: ${combo}`);
        setTimeout(refreshScreenshot, 200);
        return;
    }

    // Special keys
    if (keyMap[e.key]) {
        fetch('/browser/key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: keyMap[e.key] })
        });
        setStatus(`Key: ${e.key}`);
        setTimeout(refreshScreenshot, 200);
        return;
    }

    // Printable characters — type them
    if (e.key.length === 1) {
        fetch('/browser/type', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: e.key })
        });
        setStatus(`Typed: ${e.key}`);
        setTimeout(refreshScreenshot, 150);
    }
}

// =============================================================================
// UI Helpers
// =============================================================================

function showClickIndicator(localX, localY, rect) {
    const indicator = modal.querySelector('.cu-interact-click-indicator');
    const wrap = modal.querySelector('.cu-interact-screen-wrap');
    const wrapRect = wrap.getBoundingClientRect();

    // Position relative to screen-wrap
    indicator.style.left = (localX + (rect.left - wrapRect.left)) + 'px';
    indicator.style.top = (localY + (rect.top - wrapRect.top)) + 'px';
    indicator.classList.remove('active');
    // Trigger reflow for animation restart
    void indicator.offsetWidth;
    indicator.classList.add('active');
}

function setStatus(text) {
    const statusEl = modal.querySelector('.cu-interact-status-text');
    if (statusEl) statusEl.textContent = text;
}

/**
 * Update the screenshot from an external SSE event (during active CU model run).
 * Called from chat-send.js when cu_screenshot events arrive.
 */
export function updateScreenshot(url, step) {
    if (!isOpen || !modal) return;
    const screen = modal.querySelector('.cu-interact-screen');
    if (screen) {
        screen.src = url + '?t=' + Date.now();
    }
    const stepEl = modal.querySelector('.cu-interact-step');
    if (stepEl) stepEl.textContent = `Step ${step}`;

    // Show active dot
    const dot = modal.querySelector('.cu-interact-dot');
    if (dot) dot.classList.add('pulse');
}

export function isInteractOpen() {
    return isOpen;
}
```

### Step 2: Commit

```bash
git add Portal/modules/cu-interact.js
git commit -m "feat(portal): add interactive CU viewer modal module"
```

---

## Task 4: Add Modal CSS Styles

**Why:** Styles for the interactive overlay modal matching the existing CU dark aesthetic.

**Files:**
- Modify: `Portal/styles/features/_browser.css` — Append `.cu-interact-*` styles

### Step 1: Add the interactive modal styles

Append to the end of `_browser.css`:

```css
/* =============================================================================
   Interactive CU Viewer Modal
   ============================================================================= */

.cu-interact-overlay {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 7500;
    background: rgba(0, 0, 0, 0.85);
    backdrop-filter: blur(4px);
    align-items: center;
    justify-content: center;
}

.cu-interact-overlay.open {
    display: flex;
}

.cu-interact-modal {
    display: flex;
    flex-direction: column;
    width: 95vw;
    max-width: 1360px;
    max-height: 95vh;
    background: var(--neutral-100, #1a1a1a);
    border-radius: var(--radius-lg, 12px);
    border: 1px solid var(--neutral-200, #2a2a2a);
    overflow: hidden;
    box-shadow: 0 24px 80px rgba(0, 0, 0, 0.6);
}

/* Header */
.cu-interact-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 16px;
    background: var(--neutral-100, #1a1a1a);
    border-bottom: 1px solid var(--neutral-200, #2a2a2a);
}

.cu-interact-header-left {
    display: flex;
    align-items: center;
    gap: 8px;
}

.cu-interact-header-right {
    display: flex;
    gap: 4px;
}

.cu-interact-dot {
    width: 8px;
    height: 8px;
    border-radius: 9999px;
    background: #6b7280;
}

.cu-interact-dot.pulse {
    background: #4ade80;
    animation: cu-dot-pulse 1.5s ease-in-out infinite;
}

.cu-interact-title {
    font-weight: 600;
    font-size: 14px;
    color: #e0e0e0;
}

.cu-interact-step {
    font-size: 12px;
    color: #888;
    font-family: var(--font-mono, monospace);
}

.cu-interact-btn {
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.12);
    color: #ccc;
    border-radius: var(--radius-sm, 6px);
    padding: 4px 10px;
    cursor: pointer;
    font-size: 16px;
    transition: background var(--transition-fast, 0.15s);
}

.cu-interact-btn:hover {
    background: rgba(255, 255, 255, 0.15);
    color: #fff;
}

/* Screen area */
.cu-interact-body {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 12px;
    overflow: hidden;
    background: #000;
}

.cu-interact-screen-wrap {
    position: relative;
    display: inline-block;
    max-width: 100%;
    max-height: 100%;
}

.cu-interact-screen {
    display: block;
    max-width: 100%;
    max-height: calc(95vh - 100px);
    object-fit: contain;
    border-radius: var(--radius-sm, 6px);
    cursor: crosshair;
    user-select: none;
    -webkit-user-select: none;
}

/* Click indicator */
.cu-interact-click-indicator {
    position: absolute;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    border: 2px solid #f97316;
    background: rgba(249, 115, 22, 0.25);
    transform: translate(-50%, -50%);
    pointer-events: none;
    opacity: 0;
    transition: none;
}

.cu-interact-click-indicator.active {
    opacity: 1;
    animation: cu-interact-click-fade 0.6s ease-out forwards;
}

@keyframes cu-interact-click-fade {
    0% {
        opacity: 1;
        transform: translate(-50%, -50%) scale(0.5);
    }
    50% {
        opacity: 0.8;
        transform: translate(-50%, -50%) scale(1.2);
    }
    100% {
        opacity: 0;
        transform: translate(-50%, -50%) scale(1.5);
    }
}

/* Status bar */
.cu-interact-status {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 16px;
    background: var(--neutral-100, #1a1a1a);
    border-top: 1px solid var(--neutral-200, #2a2a2a);
    font-size: 12px;
    color: #888;
    font-family: var(--font-mono, monospace);
}

.cu-interact-status-text {
    color: #aaa;
}

.cu-interact-hint {
    color: #666;
}
```

### Step 2: Commit

```bash
git add Portal/styles/features/_browser.css
git commit -m "feat(portal): add interactive CU viewer modal styles"
```

---

## Task 5: Wire Up Live View to Interactive Modal

**Why:** Connect the existing CU Live View screenshot click to open the new interactive modal, and pipe SSE screenshot events into the modal when it's open.

**Files:**
- Modify: `Portal/modules/chat-send.js` — Change Live View image onclick, pipe SSE events
- Modify: `Portal/index.html` — Import cu-interact.js module

### Step 1: Add import to chat-send.js

At the top of `chat-send.js`, add the import alongside existing imports:

```javascript
import { open as openCUInteract, updateScreenshot as updateCUInteractScreenshot, isInteractOpen } from './cu-interact.js';
```

### Step 2: Change Live View image onclick

Find where `liveImg.onclick` is set (inside the `cu_screenshot` SSE handler). Change from:

```javascript
liveImg.onclick = () => window.open(ssUrl, '_blank');
```

To:

```javascript
liveImg.onclick = () => openCUInteract(ssUrl);
```

There may be multiple places this is set (initial creation and SSE updates). Change all of them.

### Step 3: Pipe SSE screenshot events to the modal

In the `cu_screenshot` SSE handler, after updating the Live View image, add:

```javascript
// Also update the interactive modal if it's open
if (isInteractOpen()) {
    updateCUInteractScreenshot(ssUrl, ssStep);
}
```

### Step 4: Add script import to index.html

In `Portal/index.html`, the modules are loaded via script tags. Add the import for `cu-interact.js`. This module is imported by `chat-send.js` so it may be picked up automatically by the ES module loader, but verify the module import chain works.

### Step 5: Commit

```bash
git add Portal/modules/chat-send.js Portal/index.html
git commit -m "feat(portal): wire Live View screenshot to interactive CU modal"
```

---

## Task 6: VNC Integration in Display Startup

**Why:** Auto-launch x11vnc when the Xvfb display starts, providing VNC fallback for complex interactions.

**Files:**
- Modify: `Orchestrator/browser/display.py` — Launch x11vnc after Xvfb

### Step 1: Add x11vnc launch to display startup

In `display.py`, after the Xvfb process is confirmed running, add:

```python
import shutil

def start_vnc_server():
    """Start x11vnc on the virtual display for remote viewing.
    Optional - logs a warning if x11vnc is not installed.
    """
    if not shutil.which("x11vnc"):
        print("[DISPLAY] x11vnc not installed - VNC access unavailable. Install with: sudo apt-get install x11vnc")
        return None

    try:
        vnc_proc = subprocess.Popen(
            [
                "x11vnc",
                "-display", f":{DISPLAY_NUM}",
                "-forever",
                "-shared",
                "-nopw",
                "-listen", "127.0.0.1",
                "-rfbport", "5900",
                "-noxdamage",
                "-quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[DISPLAY] x11vnc started on localhost:5900 (PID {vnc_proc.pid})")
        return vnc_proc
    except Exception as e:
        print(f"[DISPLAY] Failed to start x11vnc: {e}")
        return None
```

Call `start_vnc_server()` after `start_display()` succeeds.

### Step 2: Commit

```bash
git add Orchestrator/browser/display.py
git commit -m "feat(browser): auto-launch x11vnc for VNC fallback access"
```

---

## Summary of All Changes

| Task | File | Change |
|------|------|--------|
| 1 | `Orchestrator/browser/interaction.py` (NEW) | xdotool wrapper: click, type, key, scroll |
| 2 | `Orchestrator/routes/browser_routes.py` | 5 new endpoints: click, type, key, scroll, screenshot/live |
| 3 | `Portal/modules/cu-interact.js` (NEW) | Interactive modal: screenshot polling, event capture, coordinate scaling |
| 4 | `Portal/styles/features/_browser.css` | `.cu-interact-*` modal styles, click indicator animation |
| 5 | `Portal/modules/chat-send.js` + `Portal/index.html` | Wire Live View click to modal, pipe SSE events |
| 6 | `Orchestrator/browser/display.py` | Auto-launch x11vnc on startup |
