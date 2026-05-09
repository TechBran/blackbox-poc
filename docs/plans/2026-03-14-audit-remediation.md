# Audit Remediation - Complete Action Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all remaining findings from the 8-agent security audit (SNAP-20260311-4301) to bring the codebase from B- to A-grade, GitHub-ready.

**Architecture:** Six tasks targeting XSS protection, Android hardening, deprecated code removal, Chrome CU sandboxing, structured logging foundation, and error response sanitization. Each task is independent and can be parallelized. The .gitignore/PII cleanup (P0) is saved for last per Brandon's instructions — that's the GitHub-specific phase.

**Tech Stack:** Python (FastAPI/Orchestrator), JavaScript (Portal ES6 modules), Kotlin (Android MVP), Chrome flags, DOMPurify CDN

---

## Task 1: DOMPurify XSS Protection (P1 HIGH — AI Safety)

**Why:** `marked.parse()` renders AI responses as raw HTML via `innerHTML` with zero sanitization. A prompt-injected model could execute arbitrary JS in the Portal, which has full system access (phone calls, SMS, CU, snapshot exfil). 11 vulnerable injection points across 4 files.

**Files:**
- Modify: `Portal/index.html` (add DOMPurify CDN script)
- Modify: `Portal/modules/markdown-renderer.js:143-153` (wrap `renderMarkdown()` with DOMPurify)
- Modify: `Portal/modules/agent-handler.js:997,1679` (wrap direct `marked.parse()` calls)
- Modify: `Portal/modules/gemini-agent-handler.js:792` (wrap direct `marked.parse()` call)

**Step 1: Add DOMPurify CDN to index.html**

In `Portal/index.html`, add this script tag right after the existing `marked` script tag (line 14):

```html
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.0.6/dist/purify.min.js"></script>
```

**Step 2: Wrap `renderMarkdown()` in markdown-renderer.js**

In `Portal/modules/markdown-renderer.js`, change the `renderMarkdown()` function (lines 143-153):

```javascript
// BEFORE:
export function renderMarkdown(text) {
    if (typeof marked === 'undefined') return text;
    try {
        const textWithMedia = convertMediaUrlsToHtml(text);
        return marked.parse(textWithMedia);
    } catch (e) {
        console.warn('Markdown parse error:', e);
        return text;
    }
}

// AFTER:
export function renderMarkdown(text) {
    if (typeof marked === 'undefined') return text;
    try {
        const textWithMedia = convertMediaUrlsToHtml(text);
        const html = marked.parse(textWithMedia);
        return typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(html) : html;
    } catch (e) {
        console.warn('Markdown parse error:', e);
        return text;
    }
}
```

This single change protects all 8 `renderMarkdown()` call sites in `chat-bubbles.js` and `chat-send.js`.

**Step 3: Wrap direct `marked.parse()` calls in agent-handler.js**

In `Portal/modules/agent-handler.js`, line ~997:

```javascript
// BEFORE:
if (this.outputBuffer) {
    html += marked.parse(this.outputBuffer);
}

// AFTER:
if (this.outputBuffer) {
    const parsed = marked.parse(this.outputBuffer);
    html += typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(parsed) : parsed;
}
```

Same pattern at line ~1679:

```javascript
// BEFORE:
bubbleText.innerHTML = marked.parse(this.outputBuffer);

// AFTER:
const parsed = marked.parse(this.outputBuffer);
bubbleText.innerHTML = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(parsed) : parsed;
```

**Step 4: Wrap direct `marked.parse()` call in gemini-agent-handler.js**

In `Portal/modules/gemini-agent-handler.js`, line ~792:

```javascript
// BEFORE:
if (this.outputBuffer) {
    html += marked.parse(this.outputBuffer);
}

// AFTER:
if (this.outputBuffer) {
    const parsed = marked.parse(this.outputBuffer);
    html += typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(parsed) : parsed;
}
```

**Step 5: Verify**

- Hard-refresh Portal in browser (Ctrl+Shift+R)
- Open DevTools console, confirm no errors
- Send a chat message, verify markdown still renders correctly (bold, code blocks, links)
- Test: type `<img src=x onerror="alert('xss')">` in a message — it should NOT execute

---

## Task 2: Android WebView Debug Gate (P1 HIGH)

**Why:** `WebView.setWebContentsDebuggingEnabled(true)` is unconditionally enabled. Any USB connection to a customer's phone can inject JavaScript into the WebView, which has native bridge access to 26+ methods across 4 JS interfaces (phone calls, overlay, media projection, file picker, mic, camera).

**Files:**
- Modify: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/PortalActivity.kt:278`

**Step 1: Gate behind BuildConfig.DEBUG**

```kotlin
// BEFORE (line 278):
WebView.setWebContentsDebuggingEnabled(true)

// AFTER:
if (BuildConfig.DEBUG) {
    WebView.setWebContentsDebuggingEnabled(true)
}
```

**Step 2: Verify**

- Build debug APK: `./gradlew assembleDebug` — WebView debugging should still work
- Build release APK: `./gradlew assembleRelease` — WebView debugging disabled

---

## Task 3: Android ProGuard/R8 Enablement (P1 HIGH)

**Why:** Release APK ships with readable source code. `minifyEnabled false` means anyone can decompile and read all Kotlin source, including JS bridge interfaces, API endpoints, and app logic.

**Files:**
- Modify: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/build.gradle:21-24`
- Modify: `AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/proguard-rules.pro` (currently empty)

**Step 1: Enable R8 minification in build.gradle**

```gradle
// BEFORE:
buildTypes {
    release {
        minifyEnabled false
        proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
    }
}

// AFTER:
buildTypes {
    release {
        minifyEnabled true
        shrinkResources true
        proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
    }
}
```

**Step 2: Write ProGuard rules to preserve JS bridge interfaces**

The `@JavascriptInterface` annotated methods MUST be preserved — R8 will strip them otherwise, breaking all JS→Android communication. Write to `proguard-rules.pro`:

```proguard
# Keep JavaScript bridge interfaces (Portal WebView communication)
-keepclassmembers class com.aiblackbox.portal.PortalActivity$WebAppInterface {
    @android.webkit.JavascriptInterface <methods>;
}
-keepclassmembers class com.aiblackbox.portal.PortalActivity$FilePickerInterface {
    @android.webkit.JavascriptInterface <methods>;
}

# Keep any class with @JavascriptInterface methods (future-proof)
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}

# Keep XR overlay classes (uses reflection for Spatial APIs)
-keep class com.aiblackbox.portal.overlay.** { *; }

# Keep data classes used in JSON serialization
-keep class com.aiblackbox.portal.models.** { *; }
```

**Step 3: Verify**

- Build release APK: `./gradlew assembleRelease`
- Verify APK size decreased (R8 strips unused code)
- Install release APK on device, verify all JS bridges still work (notifications, overlay, file picker)

---

## Task 4: Remove Deprecated Cellular AT Command Code (P2 MEDIUM)

**Why:** The old SIM7600 AT command cellular module is deprecated — telephony runs on Asterisk/PJSIP now. The AT command code contains an SMS body injection vulnerability (`\x1a` can terminate message and execute AT commands on the modem). Removing it eliminates the attack vector AND reduces codebase size by ~130KB.

**Files:**
- Delete: `Orchestrator/cellular/modem.py` (28K, 22 AT command refs)
- Delete: `Orchestrator/cellular/sms.py` (8.3K, 8 AT command refs)
- Delete: `Orchestrator/cellular/voice.py` (6.5K, voice calls via AT)
- Delete: `Orchestrator/cellular/audio_test.py` (7.8K)
- Delete: `Orchestrator/cellular/echo_test.py` (2.4K)
- Delete: `Orchestrator/cellular/AT_COMMAND_REFERENCE.md` (5.8K)
- **KEEP**: `Orchestrator/cellular/hotplug.py` (USB hot-plug monitor — used for internet failover)
- **KEEP**: `Orchestrator/cellular/internet_manager.py` (SIM8260G internet failover — active)
- **KEEP**: `Orchestrator/cellular/audio_stream.py` (may be referenced by Asterisk audio bridge)
- **KEEP**: `Orchestrator/cellular/ivr.py` (IVR handling — check if used by Asterisk path)
- Modify: `Orchestrator/cellular/__init__.py` (remove imports of deleted modules)
- Verify: `Orchestrator/routes/cellular_routes.py` (ensure no routes reference deleted modem/sms functions)
- Verify: `Orchestrator/startup.py` (ensure cellular init doesn't import deleted modules)
- Verify: `Orchestrator/phone/bridge.py` (ensure no direct modem/sms imports)

**Step 1: Audit imports before deleting**

Search for all imports of the modules being deleted:
```bash
grep -rn "from.*cellular.*import\|from.*cellular\.modem\|from.*cellular\.sms\|from.*cellular\.voice" Orchestrator/ --include="*.py" | grep -v venv | grep -v __pycache__
```

Document every import site. Each one needs a corresponding cleanup.

**Step 2: Delete the deprecated files**

```bash
rm Orchestrator/cellular/modem.py
rm Orchestrator/cellular/sms.py
rm Orchestrator/cellular/voice.py
rm Orchestrator/cellular/audio_test.py
rm Orchestrator/cellular/echo_test.py
rm Orchestrator/cellular/AT_COMMAND_REFERENCE.md
```

**Step 3: Update `__init__.py`**

Remove any imports of deleted modules. Keep imports for `hotplug`, `internet_manager`, and any other retained modules.

**Step 4: Update cellular_routes.py**

Remove or disable any route handlers that call deleted modem/sms functions. Keep routes for internet failover and hotplug status.

**Step 5: Update startup.py**

Remove cellular modem initialization. Keep internet manager and hotplug monitor initialization.

**Step 6: Update phone/bridge.py**

Remove any fallback paths that reference the old cellular modem for calls/SMS. The Asterisk path should be the only telephony path now.

**Step 7: Verify**

```bash
cd Orchestrator && python3 -c "import cellular" && echo "OK"
sudo systemctl restart blackbox.service
sleep 90  # Service takes 60-90s to start
curl http://localhost:9091/health
```

Confirm no import errors, service starts clean, health endpoint responds.

---

## Task 5: Chrome Computer Use Sandbox Hardening (P2 MEDIUM)

**Why:** Chrome runs with `--no-sandbox` and in NATIVE_MODE the domain blocklist is empty. If the CU agent navigates to a malicious website, a renderer exploit has unrestricted host access. The AI choosing to visit a bad URL is the attack vector.

**Files:**
- Modify: `Orchestrator/browser/config.py:128-142` (add blocklist entries for native mode)
- Modify: `Orchestrator/browser/chrome.py:53` (add safety comment, keep --no-sandbox since it's required for headless root)

**Step 1: Add a sensible native-mode blocklist in config.py**

The full blocklist should always be active. In native mode, we still want to block known-dangerous domains while allowing localhost (which the agent needs for BlackBox access):

```python
# BEFORE:
DOMAIN_BLOCKLIST = [] if NATIVE_MODE else [
    "localhost",
    "127.0.0.1",
    ...
]

# AFTER:
# Always block dangerous domains. In native mode, allow localhost but block everything else risky.
_COMMON_BLOCKLIST = [
    "169.254.",           # Link-local / cloud metadata
    "metadata.google.internal",
    "metadata.google",
    "metadata.aws",       # AWS IMDS
]

_PRIVATE_NETWORK_BLOCKLIST = [
    "10.",                # Private class A
    "192.168.",           # Private class C
    "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
]

if NATIVE_MODE:
    # Native mode: allow localhost (agent needs it) but block cloud metadata
    DOMAIN_BLOCKLIST = _COMMON_BLOCKLIST
else:
    # Headless mode: block all private networks and localhost
    DOMAIN_BLOCKLIST = _COMMON_BLOCKLIST + _PRIVATE_NETWORK_BLOCKLIST + [
        "localhost", "127.0.0.1", "0.0.0.0", "::1",
    ]
```

**Step 2: Add Chrome sandbox documentation comment**

In `Orchestrator/browser/chrome.py`, line 53 — keep `--no-sandbox` but add a clear comment:

```python
# SECURITY NOTE: --no-sandbox is required when running Chrome as root or in
# headless environments. The domain blocklist in config.py provides URL-level
# protection against the CU agent visiting malicious sites.
"--no-sandbox",
```

**Step 3: Verify**

```bash
sudo systemctl restart blackbox.service
sleep 90
# Test that CU agent can still browse normally
curl http://localhost:9091/health
```

---

## Task 6: Error Response Sanitization (P3)

**Why:** Route handlers return `traceback.format_exc()` and raw `str(e)` in HTTP error responses, exposing code structure, file paths, and internal state to users. Even trusted Tailscale users don't need stack traces.

**Files:**
- Modify: `Orchestrator/routes/admin_routes.py` (4 `traceback.format_exc()` occurrences)
- Modify: `Orchestrator/routes/agent_routes.py` (3 `traceback.format_exc()` occurrences)
- Modify: `Orchestrator/routes/asterisk_routes.py` (2 `traceback.format_exc()` occurrences)
- Modify: `Orchestrator/routes/cellular_routes.py` (2 `traceback.format_exc()` occurrences)

**Step 1: Replace `traceback.format_exc()` in error responses**

For each file, find patterns like:

```python
# BEFORE:
except Exception as e:
    return JSONResponse({"error": traceback.format_exc()}, status_code=500)

# AFTER:
except Exception as e:
    print(f"[ERROR] {request.url.path}: {traceback.format_exc()}")  # Log full trace
    return JSONResponse({"error": str(e)}, status_code=500)  # Return only message
```

The traceback still gets printed to server logs (where the operator can see it), but the HTTP response only contains the exception message.

**Step 2: Verify**

- Trigger an error on an admin route (e.g., bad request body)
- Confirm response contains only the error message, not a full stack trace
- Confirm full traceback appears in server logs / systemd journal

---

## Task 7: Structured Logging Foundation (P2)

**Why:** ~1,800+ `print()` statements across the Orchestrator make log management impossible for customers. No log levels, no structured format, no way to filter by severity. The top offenders: chat_routes.py (292), bridge.py (217), grok_live_routes.py (98).

**NOTE:** This is a large migration that should NOT be done all at once. This task sets up the logging infrastructure and migrates the highest-value file (startup.py) as the template. Remaining files are migrated incrementally in future sessions.

**Files:**
- Create: `Orchestrator/logging_config.py` (new — centralized logging setup)
- Modify: `Orchestrator/app.py` (initialize logging on startup)
- Modify: `Orchestrator/startup.py` (migrate as template — 51 print statements)

**Step 1: Create logging_config.py**

```python
"""Centralized logging configuration for BlackBox Orchestrator."""
import logging
import sys

def setup_logging(level=logging.INFO):
    """Configure structured logging for the Orchestrator."""
    fmt = "[%(asctime)s] %(levelname)-7s %(name)-25s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        stream=sys.stdout,
        force=True,
    )

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
```

**Step 2: Initialize in app.py**

At the top of `app.py`, before FastAPI app creation:

```python
from logging_config import setup_logging
setup_logging()
```

**Step 3: Migrate startup.py as the template**

Replace `print()` calls with proper logger:

```python
import logging
logger = logging.getLogger("blackbox.startup")

# BEFORE:
print(f"[STARTUP] Loading snapshot index...")

# AFTER:
logger.info("Loading snapshot index...")

# BEFORE:
print(f"[ERROR] Failed to load index: {e}")

# AFTER:
logger.error("Failed to load index: %s", e)

# BEFORE:
print(f"[STARTUP] Index loaded: {count} snapshots in {elapsed:.1f}s")

# AFTER:
logger.info("Index loaded: %d snapshots in %.1fs", count, elapsed)
```

**Pattern for future migrations** (one file per session):
```python
import logging
logger = logging.getLogger("blackbox.<module_name>")
# Replace print(f"[TAG] ...") with logger.info/warning/error(...)
```

**Step 4: Verify**

```bash
sudo systemctl restart blackbox.service
sleep 90
journalctl -u blackbox.service --since "1 min ago" --no-pager | head -30
```

Confirm startup logs now show structured format: `[2026-03-14 12:00:00] INFO    blackbox.startup          | Loading snapshot index...`

---

## Execution Order & Dependencies

Tasks are independent and can be parallelized:

| Task | Priority | Effort | Can Parallelize |
|------|----------|--------|-----------------|
| 1. DOMPurify XSS | P1 | 15 min | Yes |
| 2. WebView Debug Gate | P1 | 5 min | Yes |
| 3. ProGuard Enable | P1 | 15 min | Yes (with Task 2) |
| 4. Remove Cellular Code | P2 | 30 min | Yes |
| 5. Chrome CU Sandbox | P2 | 15 min | Yes |
| 6. Error Response Sanitization | P3 | 20 min | Yes |
| 7. Structured Logging Foundation | P2 | 20 min | Yes (after Task 6) |

**Recommended grouping for parallel agents:**
- **Agent A:** Tasks 1 (DOMPurify) — Portal JS changes
- **Agent B:** Tasks 2 + 3 (Android hardening) — Kotlin/Gradle changes
- **Agent C:** Task 4 (Cellular removal) — Python backend, needs careful import auditing
- **Agent D:** Tasks 5 + 6 + 7 (Chrome sandbox + logging + error responses) — Python backend config

**After all tasks:** Restart service, verify health, run manual smoke test through Portal.

---

## Grade Impact

| Current | After Tasks 1-3 | After Tasks 4-5 | After Tasks 6-7 | After .gitignore (future) |
|---------|-----------------|-----------------|-----------------|---------------------------|
| B- | B+ | A- | A- | A |
