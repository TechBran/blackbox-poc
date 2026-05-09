# Gmail Integration — Per-Operator Email Tools

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Full Gmail integration with per-operator OAuth, persistent tokens, and AI-accessible email tools (read, search, send, reply, labels).

**Architecture:** Google OAuth 2.0 with PKCE + refresh tokens stored in operator_preferences.json. Gmail API accessed via `google-api-python-client` (already installed). Tools registered in the unified tool_registry.py so all models (Gemini, Anthropic, OpenAI, xAI) and all interfaces (chat, voice, phone, MCP) can use them. Android Portal gets a Gmail section in the system menu for OAuth login.

**Tech Stack:** google-api-python-client 2.187.0, google-auth 2.48.0, FastAPI OAuth routes, tool_registry.py pattern, operator_preferences.json for token persistence.

---

## Prerequisites

Before starting, you need a Google Cloud OAuth 2.0 Client ID:
1. Go to https://console.cloud.google.com/apis/credentials
2. Create OAuth 2.0 Client ID (type: Web application)
3. Add authorized redirect URI: `http://localhost:9091/auth/gmail/callback`
4. Also add your Tailscale URI: `https://{hostname}/auth/gmail/callback`
5. Enable the Gmail API in the project
6. Save the client ID and secret to `.env`

---

### Task 1: Environment Config + Gmail Service Module

**Files:**
- Modify: `.env` (add 2 variables)
- Modify: `Orchestrator/config.py` (add 2 config vars)
- Create: `Orchestrator/gmail/__init__.py`
- Create: `Orchestrator/gmail/service.py`

**What to do:**

**Step 1:** Add to `.env`:
```
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
```

**Step 2:** Add to `Orchestrator/config.py` after existing Google vars:
```python
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
```

**Step 3:** Create `Orchestrator/gmail/__init__.py` (empty).

**Step 4:** Create `Orchestrator/gmail/service.py` — the core Gmail service class:

```python
"""Gmail API service — per-operator OAuth token management + API calls."""

import base64
import json
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional, Dict, List, Any

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from Orchestrator.config import GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]
TOKEN_DIR = Path("Manifest/gmail_tokens")
TOKEN_DIR.mkdir(parents=True, exist_ok=True)


def _token_path(operator: str) -> Path:
    """Get token file path for an operator."""
    safe_name = operator.replace("/", "_").replace("\\", "_")
    return TOKEN_DIR / f"{safe_name}.json"


def save_tokens(operator: str, token_data: dict):
    """Save OAuth tokens to disk for an operator."""
    path = _token_path(operator)
    path.write_text(json.dumps(token_data, indent=2))


def load_tokens(operator: str) -> Optional[dict]:
    """Load OAuth tokens from disk for an operator."""
    path = _token_path(operator)
    if path.exists():
        return json.loads(path.read_text())
    return None


def delete_tokens(operator: str):
    """Remove stored tokens (logout)."""
    path = _token_path(operator)
    if path.exists():
        path.unlink()


def is_connected(operator: str) -> bool:
    """Check if an operator has valid Gmail tokens."""
    tokens = load_tokens(operator)
    return tokens is not None and "refresh_token" in tokens


def get_gmail_service(operator: str):
    """Build an authenticated Gmail API service for an operator.
    Returns None if not connected or tokens are invalid.
    """
    tokens = load_tokens(operator)
    if not tokens or "refresh_token" not in tokens:
        return None

    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_OAUTH_CLIENT_ID,
        client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=SCOPES
    )

    # Refresh if expired
    if creds.expired or not creds.valid:
        try:
            creds.refresh(Request())
            # Save refreshed tokens
            tokens["access_token"] = creds.token
            save_tokens(operator, tokens)
        except Exception as e:
            print(f"[Gmail] Token refresh failed for {operator}: {e}")
            return None

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def list_messages(operator: str, query: str = "", max_results: int = 10) -> List[Dict]:
    """List/search Gmail messages. Returns list of message summaries."""
    service = get_gmail_service(operator)
    if not service:
        return [{"error": "Gmail not connected. Ask the operator to connect Gmail in the system menu."}]

    try:
        params = {"userId": "me", "maxResults": max_results}
        if query:
            params["q"] = query
        result = service.users().messages().list(**params).execute()
        messages = result.get("messages", [])

        summaries = []
        for msg_ref in messages:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"]
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            summaries.append({
                "id": msg_ref["id"],
                "threadId": msg.get("threadId"),
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
                "labelIds": msg.get("labelIds", []),
                "unread": "UNREAD" in msg.get("labelIds", [])
            })
        return summaries
    except Exception as e:
        return [{"error": f"Gmail API error: {str(e)}"}]


def get_message(operator: str, message_id: str) -> Dict:
    """Get full email content by ID."""
    service = get_gmail_service(operator)
    if not service:
        return {"error": "Gmail not connected."}

    try:
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        # Extract body
        body = ""
        payload = msg.get("payload", {})
        if "body" in payload and payload["body"].get("data"):
            body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        elif "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                    break
            if not body:
                for part in payload["parts"]:
                    if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                        body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                        break

        return {
            "id": msg["id"],
            "threadId": msg.get("threadId"),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "cc": headers.get("Cc", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body[:5000],  # Cap at 5K chars
            "snippet": msg.get("snippet", ""),
            "labelIds": msg.get("labelIds", [])
        }
    except Exception as e:
        return {"error": f"Gmail API error: {str(e)}"}


def send_email(operator: str, to: str, subject: str, body: str,
               cc: str = "", reply_to_message_id: str = "", thread_id: str = "") -> Dict:
    """Send an email or reply to a thread."""
    service = get_gmail_service(operator)
    if not service:
        return {"error": "Gmail not connected."}

    try:
        message = MIMEMultipart()
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        if reply_to_message_id:
            message["In-Reply-To"] = reply_to_message_id
            message["References"] = reply_to_message_id
        message.attach(MIMEText(body, "plain"))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        send_body = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id

        result = service.users().messages().send(userId="me", body=send_body).execute()
        return {"success": True, "messageId": result.get("id"), "threadId": result.get("threadId")}
    except Exception as e:
        return {"error": f"Send failed: {str(e)}"}


def get_labels(operator: str) -> List[Dict]:
    """Get all Gmail labels for an operator."""
    service = get_gmail_service(operator)
    if not service:
        return [{"error": "Gmail not connected."}]

    try:
        result = service.users().labels().list(userId="me").execute()
        return [{"id": l["id"], "name": l["name"], "type": l.get("type", "")}
                for l in result.get("labels", [])]
    except Exception as e:
        return [{"error": f"Gmail API error: {str(e)}"}]


def modify_message(operator: str, message_id: str, add_labels: List[str] = None,
                   remove_labels: List[str] = None) -> Dict:
    """Modify labels on a message (mark read/unread, archive, star, etc)."""
    service = get_gmail_service(operator)
    if not service:
        return {"error": "Gmail not connected."}

    try:
        body = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels
        result = service.users().messages().modify(userId="me", id=message_id, body=body).execute()
        return {"success": True, "labelIds": result.get("labelIds", [])}
    except Exception as e:
        return {"error": f"Gmail API error: {str(e)}"}
```

---

### Task 2: OAuth Routes

**Files:**
- Create: `Orchestrator/routes/gmail_routes.py`
- Modify: `Orchestrator/app.py` (add import)

**What to do:**

Create `Orchestrator/routes/gmail_routes.py` with:
- `GET /auth/gmail/authorize?operator={op}` — generates Google OAuth URL, redirects user
- `GET /auth/gmail/callback` — handles OAuth redirect, exchanges code for tokens, saves per-operator
- `GET /gmail/status/{operator}` — check if operator is connected
- `POST /gmail/disconnect/{operator}` — remove tokens (logout)

Key implementation details:
- Use `google_auth_oauthlib.flow.Flow` for OAuth
- Store `state` param with operator name for callback identification
- Redirect URI from config (supports both localhost and Tailscale)
- After successful auth, redirect to a simple HTML page that says "Gmail connected! You can close this window."

Register in `app.py` by adding the import alongside other route imports.

---

### Task 3: Tool Definitions in Registry

**Files:**
- Modify: `Orchestrator/tools/tool_registry.py`

**What to do:**

Add 5 Gmail tools to the registry following the existing pattern. Each tool needs:
- `name`, `description`, `parameters` (JSON Schema), `group` assignment

Tools to add:
1. **`gmail_search`** — Search/list emails. Params: `query` (string, Gmail search syntax), `max_results` (int, default 10), `operator` (string)
2. **`gmail_read`** — Read full email by ID. Params: `message_id` (string, required), `operator` (string)
3. **`gmail_send`** — Send new email. Params: `to` (string, required), `subject` (string, required), `body` (string, required), `cc` (string, optional), `operator` (string)
4. **`gmail_reply`** — Reply to an email thread. Params: `message_id` (string, required), `thread_id` (string, required), `body` (string, required), `operator` (string)
5. **`gmail_labels`** — List labels / modify message labels. Params: `action` (enum: "list", "mark_read", "mark_unread", "archive", "star"), `message_id` (string, optional), `operator` (string)

Add all 5 to the `"chat"` group (so they appear in all chat models).

---

### Task 4: Tool Executor Methods

**Files:**
- Modify: `Orchestrator/tools/blackbox_tools.py`

**What to do:**

Add 5 executor methods to `BlackBoxToolExecutor`:
- `_execute_gmail_search(self, params)` — calls `gmail.service.list_messages()`
- `_execute_gmail_read(self, params)` — calls `gmail.service.get_message()`
- `_execute_gmail_send(self, params)` — calls `gmail.service.send_email()`
- `_execute_gmail_reply(self, params)` — calls `gmail.service.send_email()` with reply params
- `_execute_gmail_labels(self, params)` — calls `gmail.service.get_labels()` or `modify_message()`

Each method:
1. Gets `operator` from params (required)
2. Calls the corresponding `gmail.service` function
3. Returns `ToolResult(success=True/False, result=json_string)`

---

### Task 5: Chat Route Handlers (Gemini + Anthropic/OpenAI/xAI)

**Files:**
- Modify: `Orchestrator/routes/chat_routes.py`

**What to do:**

Add `elif` handlers for the 5 Gmail tools in the Gemini tool execution chain (the `call_gemini()` function's if/elif block around line 1300). Pattern:

```python
elif func_name in ("gmail_search", "gmail_read", "gmail_send", "gmail_reply", "gmail_labels"):
    from Orchestrator.tools import BlackBoxToolExecutor
    executor = BlackBoxToolExecutor(operator=operator)
    import asyncio
    tool_result = asyncio.run(executor.execute(func_name, func_args))
    result = tool_result.result
    print(f"[GEMINI] {func_name}: {result[:100]}")
```

The Anthropic/OpenAI/xAI streaming handlers already use the unified `BlackBoxToolExecutor`, so they should pick up the new tools automatically from the registry. Verify by checking the streaming handler patterns.

---

### Task 6: Android Portal — Gmail Section in System Menu

**Files:**
- Modify: `AI_BlackBox_Portal_Android_MVP/.../ui/settings/SettingsSheet.kt`

**What to do:**

Add a "Gmail" section to the system menu between "Running Apps" and "Voice Preferences":
- Header: "📧 Gmail" with blue accent
- Status indicator: "Connected as user@gmail.com" (green) or "Not connected" (gray)
- Connect button: Opens the OAuth URL in the system browser (Chrome)
- Disconnect button: Calls `POST /gmail/disconnect/{operator}`
- Status check: Calls `GET /gmail/status/{operator}` on load

The OAuth flow happens in the external browser:
1. User taps "Connect Gmail"
2. App opens `{origin}/auth/gmail/authorize?operator={currentOperator}` in Chrome
3. User logs in to Google, grants permission
4. Google redirects to callback URL
5. Backend saves tokens
6. User returns to app, status shows "Connected"

---

### Task 7: MCP Tool Registration

**Files:**
- Modify: MCP server tool list (if separate from tool_registry)

**What to do:**

Ensure the 5 Gmail tools are available via MCP so external agents (Claude Code, etc.) can also access Gmail. If the tool_registry `"chat"` group automatically includes MCP, no changes needed. Verify by checking the MCP tool list.

---

### Task 8: Phone Bridge Tool Registration

**Files:**
- Modify: `Orchestrator/phone/bridge.py` (if it has a separate tool map)

**What to do:**

Add the 5 Gmail tools to the phone bridge's `unified_tool_map` in `_execute_tool()` so voice agents can also access Gmail. Follow the same pattern as existing tools.

---

## Summary

| Task | Files | Scope |
|------|-------|-------|
| 1. Gmail Service | gmail/service.py (new), .env, config.py | ~200 lines |
| 2. OAuth Routes | gmail_routes.py (new), app.py | ~120 lines |
| 3. Tool Registry | tool_registry.py | ~80 lines |
| 4. Tool Executor | blackbox_tools.py | ~150 lines |
| 5. Chat Handlers | chat_routes.py | ~20 lines |
| 6. Android UI | SettingsSheet.kt | ~60 lines |
| 7. MCP Registration | Verify only | ~0 lines |
| 8. Phone Bridge | bridge.py | ~10 lines |

**Total: ~640 lines of new code across 8 tasks.**

## Testing Checklist

1. Set up OAuth credentials in Google Cloud Console
2. Add client ID/secret to `.env`
3. Restart service
4. Open System Menu > Gmail > Connect
5. Complete OAuth flow in browser
6. Return to app — status shows "Connected"
7. Ask Gemini: "Check my latest 5 emails"
8. Ask Gemini: "Send an email to test@example.com with subject 'Hello' and body 'Test message'"
9. Ask Gemini: "Search my email for receipts from Amazon"
10. Switch operator — verify separate Gmail accounts
11. Restart service — verify tokens persist
12. Test disconnect — verify tokens removed
