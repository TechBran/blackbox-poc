# Onboarding Wizard Foundation Refinements — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to execute this plan task-by-task.

**Goal:** Solidify the onboarding wizard so every step mirrors live system state — making it the foundation that Phase 2.10 manage-mode UI will mirror. After this plan lands, customers can manage every credential the BlackBox uses (LLM keys, OAuth, service-account files, operators) from the wizard with the same UX as manage-mode.

**Architecture:** Each step component fetches `/onboarding/current-config` on mount, dispatches between "configured" (rehydrated, with Replace) and "input" (empty form) variants per item, and POSTs only changed values back. Backend extensions: more validators, a credentials-file management endpoint, a DELETE-operator endpoint.

**Tech Stack:** ES module step components in `Portal/onboarding/steps/`, FastAPI backend in `Orchestrator/routes/`, validators in `Orchestrator/onboarding/validators.py`, secrets writer in `Orchestrator/onboarding/secrets_writer.py` (already exists, extends with file-write helper).

---

## Context

After T2.0.1 → T2.8.1 shipped (Track 2 linear wizard end-to-end), Brandon live-tested the wizard and surfaced 5 UX gaps. Items 1 (tailnet HTTPS URL display) + 4 (Gmail rehydrate) shipped immediately as small fixes (`5e34995`). This plan covers the remaining 3 items, expanded per Brandon's "wizard mirrors live state" foundation philosophy:

> "We need to solidify all of that before moving on to the next step, which is the manage state UI. We should get the foundation right first, which is the onboarding, because the onboarding will just be a mirror image of what we see in the main UI when we click on these things to open them."

**Brandon's design philosophy locked 2026-05-12:**
1. Wizard step UIs are the SOURCE OF TRUTH for credential management UX
2. Manage-mode UI in Phase 2.10 will mirror the wizard one-to-one
3. Each credential the BlackBox uses must be settable from the wizard (no orphaned config)
4. Each step shows live state (rehydrate from `/current-config`) + lets customer edit in real-time

**Twilio decision (open):** Brandon's "all of them" framing in the same conversation might re-include Twilio (currently deferred per earlier "TG200 cellular handles phone" decision). DEFAULT IN THIS PLAN: Twilio stays deferred to v1.1 placeholder card. **If Brandon wants Twilio rehydrated/active, that's a small add-on after T2.4.2.**

---

## Architectural Decisions (locked 2026-05-12)

| # | Decision | Rationale |
|---|---|---|
| 1 | All 5 LLM API keys in api_keys step: OpenAI, Anthropic, Google, **xAI**, **Perplexity** | Brandon reversed earlier "skip Perplexity" decision: "even perplexity as well, all of them" |
| 2 | xAI + Perplexity validators use the `openai` SDK with custom `base_url` | Both providers expose OpenAI-compatible APIs (api.x.ai/v1, api.perplexity.ai). Avoids new SDK dependencies. |
| 3 | Google Cloud service account file (JSON) gets its OWN card in optional_integrations | Distinct UX from API keys (file upload vs paste). Lives next to Gmail card since both relate to Google services. |
| 4 | Service account file management endpoint pattern: `/onboarding/credentials/{filename}` (GET + DELETE) + `/onboarding/credentials/upload` (POST multipart) | RESTful + reusable. Future credential files (TLS certs, etc.) can use the same pattern. |
| 5 | Operator step rehydrates existing operators as read-only rows + [Remove] button + new editable rows below | Edits via remove + re-add (no rename). Simpler UX, matches the operator-list "list of names" mental model. |
| 6 | New `DELETE /operator/{name}` endpoint added to admin_routes.py | Mirror of existing `POST /operator/add`. Idempotent: 200 if removed, 404 if not present. |
| 7 | Real-time persist: per-action commits (Add → POST immediately, Remove → DELETE immediately) NOT batch save | Matches manage-mode mental model. Customer sees their edit reflected on the BlackBox the moment they make it. |
| 8 | Twilio: keep deferred to v1.1 placeholder unless Brandon explicitly says otherwise | Default-honor the earlier "TG200 cellular" decision. |

---

## Task summary

| Task | Phase | Surface | Est |
|---|---|---|---|
| T2.4.2 | API keys expansion | validators.py + onboarding_routes.py + api_keys.js + onboarding-tokens.css | ~1.5h |
| T2.5.2 | Google Cloud service account file management | new credentials_routes.py + secrets_writer.py extension + optional_integrations.js + new CSS | ~2.5h |
| T2.7.2 | Operators live sync | admin_routes.py (DELETE endpoint) + operator.js (rehydrate) + onboarding.css | ~1.5h |

Total: ~5-6 hours focused work. Can be done in one focused session or spread across two.

---

### Task T2.4.2: All 5 LLM API keys in api_keys step

**Files:**
- Modify: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/onboarding/validators.py`
- Modify: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/routes/onboarding_routes.py`
- Modify: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Portal/onboarding/steps/api_keys.js`

**Step 1: Add `validate_xai` + `validate_perplexity` to validators.py**

```python
def validate_xai(api_key: str) -> ValidationResult:
    """Validate xAI key via cheapest-possible chat completion (1-token completion).
    
    xAI exposes an OpenAI-compatible API at api.x.ai/v1, so we reuse the openai SDK
    with a custom base_url. Avoids adding a new SDK dependency.
    """
    def _fn():
        from openai import OpenAI
        with OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
            timeout=10.0,
            max_retries=0,
        ) as client:
            resp = client.chat.completions.create(
                model="grok-2-latest",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            return {"model": resp.model, "id": resp.id}
    return _measure(_fn)


def validate_perplexity(api_key: str) -> ValidationResult:
    """Validate Perplexity key via cheapest-possible chat completion (1-token).
    
    Perplexity exposes an OpenAI-compatible API at api.perplexity.ai. Same SDK
    reuse pattern as xAI.
    """
    def _fn():
        from openai import OpenAI
        with OpenAI(
            api_key=api_key,
            base_url="https://api.perplexity.ai",
            timeout=10.0,
            max_retries=0,
        ) as client:
            resp = client.chat.completions.create(
                model="llama-3.1-sonar-small-128k-online",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            return {"model": resp.model, "id": resp.id}
    return _measure(_fn)
```

Update the module docstring's Tier-1 / Tier-2 lists. Both providers move to Tier-1 (5 → 7 LLM providers). Twilio + ElevenLabs + Asterisk still Tier-2.

**Step 2: Update `/onboarding/validate` route + ALLOWED_REVEAL_KEYS + /current-config**

In `Orchestrator/routes/onboarding_routes.py`:

```python
class ValidateRequest(BaseModel):
    provider: Literal["openai", "anthropic", "google", "xai", "perplexity", "tailscale", "gmail"]
    credentials: dict[str, str] = {}
```

Add to the validate dispatch:
```python
elif req.provider == "xai":
    result = validators.validate_xai(creds["api_key"])
elif req.provider == "perplexity":
    result = validators.validate_perplexity(creds["api_key"])
```

Add to `ALLOWED_REVEAL_KEYS`:
```python
"XAI_API_KEY",
"PERPLEXITY_API_KEY",
```

Add to `current_config` providers dict:
```python
from Orchestrator.config import (
    OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY,
    XAI_API_KEY, PERPLEXITY_API_KEY,
    GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET,
)
# ...
providers["xai"] = {
    "present": bool(XAI_API_KEY),
    "last4": _redact(XAI_API_KEY),
    "validated_at": val_at.get("xai"),
}
providers["perplexity"] = {
    "present": bool(PERPLEXITY_API_KEY),
    "last4": _redact(PERPLEXITY_API_KEY),
    "validated_at": val_at.get("perplexity"),
}
```

**Step 3: Add to api_keys.js PROVIDERS array**

```javascript
const PROVIDERS = [
    { id: "openai", label: "OpenAI", envVar: "OPENAI_API_KEY", keyUrl: "https://platform.openai.com/api-keys", keyHint: "sk-proj-…" },
    { id: "anthropic", label: "Anthropic", envVar: "ANTHROPIC_API_KEY", keyUrl: "https://console.anthropic.com/settings/keys", keyHint: "sk-ant-…" },
    { id: "google", label: "Google AI", envVar: "GOOGLE_API_KEY", keyUrl: "https://aistudio.google.com/apikey", keyHint: "AIza…" },
    { id: "xai", label: "xAI (Grok)", envVar: "XAI_API_KEY", keyUrl: "https://console.x.ai", keyHint: "xai-…" },
    { id: "perplexity", label: "Perplexity", envVar: "PERPLEXITY_API_KEY", keyUrl: "https://www.perplexity.ai/settings/api", keyHint: "pplx-…" },
];
```

The existing `formatDetail()` helper handles xAI + Perplexity since both return `{model, id}` (same as Anthropic). Just add to the switch:
```javascript
if (providerId === "anthropic" || providerId === "xai" || providerId === "perplexity") {
    return detail.model || "";
}
```

**Step 4: Test live**

Restart, advance to api_keys step, verify all 5 cards render. For Brandon's box: OpenAI/Anthropic/Google should show "Already configured" rehydrate (existing keys); xAI + Perplexity should also rehydrate since they're in his .env. Test validate on each (Anthropic costs ~$0.0000007; xAI/Perplexity similar; OpenAI/Google free metadata calls).

**Step 5: Commit**

```bash
git add Orchestrator/onboarding/validators.py Orchestrator/routes/onboarding_routes.py Portal/onboarding/steps/api_keys.js
git commit -m "feat(onboarding): expand api_keys step to all 5 LLM providers (xAI + Perplexity)

Both xAI and Perplexity expose OpenAI-compatible APIs — validators reuse
the openai SDK with custom base_url (api.x.ai/v1, api.perplexity.ai).
No new SDK dependencies.

Routes: ValidateRequest provider Literal expanded; ALLOWED_REVEAL_KEYS
gains XAI_API_KEY + PERPLEXITY_API_KEY; /current-config providers dict
includes both. Manage-mode reveal/delete works for them automatically.

Frontend: api_keys.js PROVIDERS extended; rehydrate works the same as
the original 3 providers since the contract is identical."
```

---

### Task T2.5.2: Google Cloud service account file (drag-drop)

**Files:**
- Create: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/routes/credentials_routes.py`
- Modify: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/onboarding/secrets_writer.py` (add file-write helper)
- Modify: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/app.py` (router include)
- Modify: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Portal/onboarding/steps/optional_integrations.js` (new card + drag-drop UI)
- Modify: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Portal/onboarding/onboarding.css` (drop-zone styles)

**Step 1: Create `Orchestrator/routes/credentials_routes.py`**

```python
"""Credentials file management for the onboarding wizard.

Manages JSON credential files (Google Cloud service accounts, future TLS
certs, etc.) that live in the credentials/ folder. Pattern:
- GET /onboarding/credentials → list all files with metadata (no contents)
- GET /onboarding/credentials/{filename} → file metadata only (NEVER contents)
- POST /onboarding/credentials/upload (multipart) → write to credentials/
- DELETE /onboarding/credentials/{filename} → remove + clear env if linked

Loopback-only by design — same trust model as /onboarding/config/{key}?reveal=1.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel

from Orchestrator.utils.paths import resolve
from Orchestrator.onboarding.secrets_writer import update_env, remove_env_keys

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding/credentials", tags=["onboarding-credentials"])

CREDS_DIR = resolve("credentials")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.json$")  # alphanumeric + dots + dashes + underscores; .json required


class CredentialFileMeta(BaseModel):
    filename: str
    size_bytes: int
    modified_at: float
    is_google_service_account: bool


class CredentialsListResponse(BaseModel):
    files: list[CredentialFileMeta]
    google_application_credentials: str | None  # the active GOOGLE_APPLICATION_CREDENTIALS env var path, if any


def _is_google_service_account(path: Path) -> bool:
    """Inspect file contents to confirm it's a Google service-account JSON.
    
    Doesn't return the content — just a boolean for UI hint.
    """
    try:
        with path.open() as f:
            data = json.load(f)
        return data.get("type") == "service_account" and "client_email" in data
    except (json.JSONDecodeError, OSError):
        return False


def _file_meta(path: Path) -> CredentialFileMeta:
    stat = path.stat()
    return CredentialFileMeta(
        filename=path.name,
        size_bytes=stat.st_size,
        modified_at=stat.st_mtime,
        is_google_service_account=_is_google_service_account(path),
    )


@router.get("", response_model=CredentialsListResponse)
def list_credentials() -> CredentialsListResponse:
    """List all .json credential files in credentials/ + the currently-active GOOGLE_APPLICATION_CREDENTIALS path."""
    if not CREDS_DIR.exists():
        CREDS_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(CREDS_DIR, 0o700)
    files = []
    for p in sorted(CREDS_DIR.glob("*.json")):
        if p.is_file():
            files.append(_file_meta(p))
    return CredentialsListResponse(
        files=files,
        google_application_credentials=os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or None,
    )


@router.post("/upload")
async def upload_credential(file: UploadFile = File(...), set_as_google_app_creds: bool = True) -> dict:
    """Upload a .json credential file. Validates JSON shape + filename. Optionally
    updates GOOGLE_APPLICATION_CREDENTIALS env var to point at it.
    """
    filename = (file.filename or "").strip()
    if not _FILENAME_RE.match(filename):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid filename {filename!r}. Must match {_FILENAME_RE.pattern}",
        )
    contents = await file.read()
    if len(contents) > 64 * 1024:  # 64KB ceiling — service-account JSONs are ~2-3KB
        raise HTTPException(status_code=413, detail="File too large (64KB max)")
    try:
        parsed = json.loads(contents.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDS_DIR, 0o700)
    target = CREDS_DIR / filename
    # Atomic write
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(contents)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)

    result = {"filename": filename, "size_bytes": len(contents), "is_service_account": _is_google_service_account(target)}

    if set_as_google_app_creds:
        update_env({"GOOGLE_APPLICATION_CREDENTIALS": str(target)})
        result["env_updated"] = "GOOGLE_APPLICATION_CREDENTIALS"

    logger.info("credentials uploaded: %s (%d bytes, sa=%s)", filename, len(contents), result["is_service_account"])
    return {"ok": True, **result}


@router.delete("/{filename}")
def delete_credential(filename: str) -> dict:
    """Remove a credential file. If GOOGLE_APPLICATION_CREDENTIALS pointed at it, also clear the env var."""
    if not _FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail=f"Invalid filename {filename!r}")
    target = CREDS_DIR / filename
    if not target.exists():
        return {"ok": True, "removed": False, "reason": "not present"}
    target.unlink()
    # Clear env var if it pointed here
    current_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if current_creds == str(target):
        remove_env_keys(["GOOGLE_APPLICATION_CREDENTIALS"])
        env_cleared = True
    else:
        env_cleared = False
    logger.info("credentials deleted: %s (env_cleared=%s)", filename, env_cleared)
    return {"ok": True, "removed": True, "env_cleared": env_cleared}
```

**Step 2: Wire the new router into `Orchestrator/app.py`**

Append after the existing onboarding_router block:
```python
from Orchestrator.routes.credentials_routes import router as credentials_router
app.include_router(credentials_router)
```

**Step 3: Frontend — drag-drop card in optional_integrations.js**

Add a new card alongside Gmail (between Gmail and Twilio placeholder). Needs:
- File picker (click to open) AND drag-drop zone
- Visual states: empty, dragging-over, uploading, configured (with filename + size + service-account indicator), error
- Replace flow when file exists
- DELETE button when file exists

State shape:
```javascript
const credState = {
    files: [],  // from GET /onboarding/credentials
    activeCreds: null,  // GOOGLE_APPLICATION_CREDENTIALS path
    uploading: false,
    error: null,
};
```

Pattern matches existing rehydrate flow: fetch on mount, render based on state, allow Replace/Remove.

Drag-drop UX:
```javascript
dropZone.addEventListener("dragover", e => { e.preventDefault(); dropZone.classList.add("ob-drop-active"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("ob-drop-active"));
dropZone.addEventListener("drop", async e => {
    e.preventDefault();
    dropZone.classList.remove("ob-drop-active");
    const file = e.dataTransfer.files[0];
    if (!file) return;
    if (!file.name.endsWith(".json")) {
        showError("Only .json files accepted");
        return;
    }
    await uploadCredentialFile(file);
});
```

Upload via FormData:
```javascript
async function uploadCredentialFile(file) {
    credState.uploading = true;
    rerender();
    const formData = new FormData();
    formData.append("file", file);
    const r = await fetch("/onboarding/credentials/upload", { method: "POST", body: formData });
    const result = await r.json();
    credState.uploading = false;
    if (!r.ok) {
        credState.error = result.detail || "Upload failed";
    } else {
        await reloadCredentials();
    }
    rerender();
}
```

CSS: drop zone with dashed border, hover/dragover state with red accent + glow, configured state shows filename + service-account ✓ pip + Replace/Remove buttons.

**Step 4: Test live**

Test upload via curl:
```bash
curl -s -X POST http://localhost:9091/onboarding/credentials/upload \
  -F "file=@credentials/gen-lang-client-0808228253-37e326fde5fd.json" | python3 -m json.tool
# Expected: {ok: true, filename: "...", is_service_account: true, env_updated: "GOOGLE_APPLICATION_CREDENTIALS"}

# List
curl -s http://localhost:9091/onboarding/credentials | python3 -m json.tool

# Delete
curl -s -X DELETE http://localhost:9091/onboarding/credentials/test.json
```

Then visual test in browser at /onboarding/ → optional_integrations step → drag a JSON file onto the card → confirm upload + display.

**Step 5: Commit**

```bash
git add Orchestrator/routes/credentials_routes.py Orchestrator/app.py Portal/onboarding/steps/optional_integrations.js Portal/onboarding/onboarding.css
git commit -m "feat(onboarding): credential file management — drag-drop service account JSON

New /onboarding/credentials/* routes (GET list / POST upload / DELETE)
manage .json credential files in credentials/ folder. Service-account
JSON detection inspects type:'service_account' field. Upload optionally
updates GOOGLE_APPLICATION_CREDENTIALS env var atomically.

Frontend: new card in optional_integrations with drag-drop zone +
file picker. Configured state shows filename + size + service-account
✓ pip. Replace + Remove buttons mirror api_keys/Gmail rehydrate UX.

64KB upload ceiling, atomic write via .tmp+os.replace, chmod 0600 on
both tmp and final, credentials/ dir chmod 0700 if absent."
```

---

### Task T2.7.2: Operators live sync

**Files:**
- Modify: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/routes/admin_routes.py` (DELETE endpoint)
- Modify: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Portal/onboarding/steps/operator.js` (rehydrate + per-row actions)
- Modify: `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Portal/onboarding/onboarding.css` (existing-row styling)

**Step 1: Add `DELETE /operator/{name}` to admin_routes.py**

```python
@app.delete("/operator/{name}")
def remove_operator(name: str):
    """Remove an operator from config.ini and reload USERS_LIST.
    
    Idempotent: returns 200 with status='removed' if found, status='not_present' if absent.
    Refuses to remove the last operator (must always have at least one).
    """
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Operator name required")
    name = name.strip()
    
    global USERS_LIST, CFG
    current_list = USERS_LIST.copy()
    
    if name not in current_list:
        return {"status": "not_present", "operators": current_list}
    if len(current_list) == 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last operator")
    
    current_list.remove(name)
    
    config_path = Path("config.ini")
    CFG.set("users", "list", ", ".join(current_list))
    with open(config_path, "w") as f:
        CFG.write(f)
    CFG.read(config_path)
    USERS_LIST = [u.strip() for u in CFG.get("users","list",fallback="Brandon").split(",") if u.strip()]
    
    return {"status": "removed", "operators": USERS_LIST}
```

**Step 2: Refactor `operator.js` — rehydrate + per-row actions**

Replace the empty-form-on-mount with:
1. Fetch `/onboarding/current-config` on render mount
2. Build state: `existing` array (read-only rows for current operators) + `pending` array (new editable rows)
3. Render existing rows with [Remove] button (× glyph), pending rows with input + remove
4. "+ Add another operator" button appends to `pending`
5. Per-row REMOVE: immediately DELETE /operator/{name}, optimistic UI update
6. Save & continue: registers all `pending` rows via /operator/add (unchanged), persists DEFAULT_OPERATOR (first valid name from `existing` ∪ `pending`)

State shape:
```javascript
const state = {
    existing: [],  // [{name: "Brandon", removing: false}, ...] from /current-config
    pending: [{ id: 0, name: "" }],  // new rows being typed
};
```

Render existing rows:
```javascript
function renderExistingRow(op) {
    return `
        <div class="ob-operator-row ob-operator-row-existing" data-name="${escapeHtml(op.name)}">
            <span class="ob-operator-name-existing">${escapeHtml(op.name)}</span>
            <button type="button" class="ob-row-remove ob-row-remove-existing" data-name="${escapeHtml(op.name)}" aria-label="Remove operator ${escapeHtml(op.name)}">×</button>
        </div>
    `;
}
```

Remove handler:
```javascript
async function removeExistingOperator(name, container, state) {
    if (!confirm(`Remove operator "${name}"? Their conversation history stays in memory but they won't appear in the operator dropdown.`)) return;
    try {
        const r = await fetch(`/operator/${encodeURIComponent(name)}`, { method: "DELETE" });
        const result = await r.json();
        if (r.ok && (result.status === "removed" || result.status === "not_present")) {
            state.existing = state.existing.filter(o => o.name !== name);
            rerender(container, state);
        } else {
            showError(container, result.detail || "Couldn't remove operator");
        }
    } catch (e) {
        showError(container, `Network error: ${e.message}`);
    }
}
```

Save logic (unchanged behavior — just registers PENDING rows):
- Existing operators are already in the system; don't re-POST them
- For each valid pending name, POST /operator/add (idempotent — re-add returns "exists" which is fine)
- DEFAULT_OPERATOR: first valid name (existing[0] if any, else pending[0])

**Step 3: CSS for existing-row styling**

Existing rows render differently from input rows:
```css
.ob-operator-row-existing {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: var(--ob-space-3);
    align-items: center;
    padding: var(--ob-space-3) var(--ob-space-4);
    background: var(--ob-surface-elevated);
    border: 1px solid var(--ob-surface-border);
}

.ob-operator-name-existing {
    font-family: var(--ob-font-body);
    font-size: var(--ob-text-sm);
    color: var(--ob-text-primary);
}
```

**Step 4: Test live**

```bash
# 1. Verify DELETE endpoint
curl -s -X DELETE http://localhost:9091/operator/test_temp -w "%{http_code}\n"
# Expected: 200 with status:not_present (since test_temp doesn't exist)

# Add a temp operator, then delete it
curl -s -X POST http://localhost:9091/operator/add -H "Content-Type: application/json" -d '{"name": "T272_temp"}' >/dev/null
curl -s -X DELETE http://localhost:9091/operator/T272_temp | python3 -m json.tool
# Expected: status: removed

# 2. Verify last-operator guard
# (Don't actually delete all operators — would lock you out. Skip this test.)

# 3. Visual: advance to operator step, see existing operators rendered
curl -s -X POST http://localhost:9091/onboarding/reset -d '' >/dev/null
for step in welcome tailscale api_keys optional_integrations pair_phone; do
    curl -s -X POST http://localhost:9091/onboarding/step/complete -H "Content-Type: application/json" -d "{\"step\":\"$step\"}" >/dev/null
done
google-chrome --headless --disable-gpu --no-sandbox --window-size=1920,1080 \
  --virtual-time-budget=4000 --screenshot=/tmp/ob_operator_rehydrate.png http://localhost:9091/onboarding/ 2>/dev/null
ls -la /tmp/ob_operator_rehydrate.png
```

**Step 5: Commit**

```bash
git add Orchestrator/routes/admin_routes.py Portal/onboarding/steps/operator.js Portal/onboarding/onboarding.css
git commit -m "feat(onboarding): operator step live sync — show existing + per-row remove

Rehydrates existing operators from /onboarding/current-config (which
reads admin_routes USERS_LIST). Existing rows render read-only with [×]
remove buttons that fire DELETE /operator/{name} immediately. New rows
below remain editable input fields + Save & continue posts only those.

DELETE /operator/{name} added to admin_routes.py:
- Idempotent: 200 with status:removed | not_present
- 400 if attempting to remove the last operator (must always have one)
- Updates config.ini + reloads USERS_LIST in same transaction"
```

---

## Verification (after all 3 tasks land)

End-to-end customer flow on a fresh Ubuntu 24.04 VM:

1. Reset onboarding state. Visit `/onboarding/`.
2. Welcome → Tailscale (Branch A with URL display) → API keys (5 providers, all rehydrate) → Optional integrations (Gmail rehydrate + drag-drop service-account JSON works) → Pair phone → Operator (existing operators visible + new can be added + remove works) → Done (summary reflects everything).
3. Click Open Portal → handoff works → Portal opens with the operator from the wizard selected as default.
4. Re-enter wizard via System Menu → Manage Setup (when Phase 2.10 lands, will mirror the wizard).

Acceptance: Brandon can manage every credential the BlackBox uses from the wizard, and the wizard mirrors the actual live state of `.env` + `config.ini` + `credentials/` folder.

---

## Out of Scope (Deferred)

- **Twilio**: per current decision, stays as v1.1 placeholder card. If Brandon flips during execution, add a small T2.4.3 follow-up to add Twilio rehydrate alongside xAI/Perplexity (different shape — needs SID + Auth Token + Phone Number, not just one key).
- **Operator rename**: customer can remove + re-add. Skipping rename keeps scope tight.
- **TLS certificate management**: same pattern as service-account JSON, but not a v1 customer need. Future credentials_routes.py extension.
- **Operator avatar / display name vs internal name**: out of scope. Operators are bare strings.
- **xAI / Perplexity model selection in wizard**: customer just provides the key; model defaults are in code. Future enhancement.

---

## Execution Handoff

After this plan saved, controller decides execution mode:

- **Subagent-Driven (this session, recommended for momentum)** — dispatch each task with full context per `superpowers:subagent-driven-development`. Run review pipeline after each commit. Push when all 3 tasks land + Brandon visually approves.
- **Parallel Session (next session)** — open a fresh session, read this plan + the latest snapshot, dispatch as a focused 5-6 hour batch.

REQUIRED SUB-SKILL for execution: `superpowers:subagent-driven-development`.
