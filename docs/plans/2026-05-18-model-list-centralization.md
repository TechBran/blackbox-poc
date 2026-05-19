# Model List Centralization — Single Source of Truth (audit-revised v2)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task.

**Goal:** Make `GET /models/{provider}` the single authoritative source for chat-model dropdowns across web Portal + Android MVP. Refresh backend fallback lists to current 2026 catalogs (drop deprecated Grok IDs). Add `xai` support to all three (backend handler, web fetch, Android fetch).

**Tech Stack:** FastAPI backend, vanilla JS modules (web), Kotlin Compose (Android), all 4 provider SDKs.

---

## Context

**Why now (Brandon's mandate):** Centralize all chat-model lists, refresh Grok catalog with 2026 IDs.

**Discovery report (verified 2026-05-18, then re-verified after first-draft audit):**

The CORRECTED architecture reality:

| Surface | Today's state | What the audit caught |
|---|---|---|
| Backend `/models/{provider}` | EXISTS at admin_routes.py:487, handles google/anthropic/openai (xai 404) | My original draft was right about this |
| Web Portal | `state-management.js:526` ALREADY has `fetchAvailableModels(provider)`; `app-init.js:133/141` ALREADY calls it on init + provider-switch | **My first-draft "neither frontend calls it" was wrong — keyword search missed the generic fetcher** |
| Android | `Constants.MODEL_CONFIG` (not `MODEL_NAMES` as I'd written) — type `Map<String, List<Pair<String, String>>>` (not `List<ModelInfo>` as I'd specced) — 6 consumers across `Composer.kt`, `CuScreen.kt`, `SettingsSheet.kt`, `SmsInboxScreen.kt`, `NativeMainActivity.kt` | **Android has NO live fetch today; everything baked at compile time** |

**Bugs the audit caught in admin_routes.py + chat_routes.py that this plan now must fix:**

- `admin_routes.py:24` imports `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY` but NOT `XAI_API_KEY` — adding xai branch without fixing this = NameError on first request.
- `chat_routes.py:5947` and `chat_routes.py:6030` hardcode `model = "grok-4-1-fast-reasoning"` (the deprecated default we're bumping). Just bumping `XAI_MODEL_DEFAULT` won't catch these — they'll silently force the deprecated ID for the routes that hit them.
- xAI catalog includes `grok-imagine-image`, `grok-imagine-image-quality`, `grok-imagine-video` (image/video generation, not chat). A naïve `startswith("grok-")` filter would let them into the chat dropdown.
- Anthropic SDK's `ModelInfo` schema does NOT guarantee `max_input_tokens` field — accessing it without `getattr(default=None)` would AttributeError and fall through to fallback unnecessarily.

**The audit also flagged my first draft's `defaultModel` claim is vaporware** — state-management.js encodes "Auto - Latest" via `{id: "", name: "(Auto - Latest)", default: true}` PREPENDED inside the models array, not as a provider-level field. Plan must preserve that prepend.

**Live-API discovery capability per provider (verified):**

| Provider | List endpoint | Status |
|---|---|---|
| OpenAI | `client.models.list()` | ✓ Backend already uses |
| Google | `genai.list_models()` | ✓ Backend already uses |
| Anthropic | `GET /v1/models` (SDK `client.models.list()`) | ✓ **NEWLY documented** — backend code says "doesn't exist", that's stale |
| xAI | OpenAI-compatible `/v1/models` via `base_url="https://api.x.ai/v1"` | ⚠ Documented as OpenAI-compatible; **probe before committing** |

**Current xAI catalog (May 2026, source: docs.x.ai/docs/models, fetched 2026-05-18):**

| Model ID | Notes |
|---|---|
| `grok-4.3` | 1M ctx — new default |
| `grok-4.20-multi-agent-0309` | 2M ctx — powerhouse |
| `grok-4.20-0309-reasoning` | 1M ctx |
| `grok-4.20-0309-non-reasoning` | 1M ctx (instant) |

**Deprecating May 15, 2026** (xAI auto-redirects to `grok-4.3` server-side):
- `grok-4-1-fast` family (including our current `grok-4-1-fast-reasoning` default)
- `grok-4-fast` family
- `grok-4` / `grok-4-0709`
- `grok-code-fast-1`

---

## Architecture (audit-revised)

### Phase A: Backend `/models/{provider}` becomes authoritative + bug-fixed

**Bugs to fix** (from audit C3, M6, M8, M9, I11):
- Add `XAI_API_KEY` to admin_routes.py imports
- Replace `grok-4-1-fast-reasoning` literals at chat_routes.py:5947, 6030 with `XAI_MODEL_DEFAULT` config lookup
- Wrap `anthropic.ModelInfo` field accesses in `getattr(..., default=None)`
- Filter `startswith("grok-")` AND `not startswith("grok-imagine-")` in xai live-fetch
- Add `timeout=5.0` to both anthropic and openai-SDK-for-xai clients

**Cache semantics** (from audit M5):
- Success → cache 10 min
- Failure (network/API error) → cache the fallback for 30s only (negative cache pattern — recovers fast once upstream comes back, doesn't DDoS during outage)
- In-memory only (cold start = first request burst hits upstream; this is acceptable behavior, document it)

**Response shape** (lock the contract — referenced by both frontends):

```json
{
    "provider": "xai",
    "models": [
        {"id": "", "name": "(Auto - Latest)", "default": true},
        {"id": "grok-4.3", "name": "Grok 4.3"},
        ...
    ],
    "source": "live" | "fallback",
    "default_id": "grok-4.3",
    "fetched_iso": "2026-05-18T20:30:00Z"
}
```

The `default_id` field is new (audit I12 fix) — frontends use this for the dropdown's pre-selected option rather than parsing the prepended `default: true` entry.

### Phase B: Web Portal — EXTEND existing `fetchAvailableModels` (not new module)

**The audit's C1 finding changes Phase B fundamentally**: `Portal/modules/state-management.js:526` already exports `fetchAvailableModels(provider)` and `Portal/modules/app-init.js:133/141` already calls it. The work is to:

- Add sessionStorage cache (5 min TTL) around the existing function
- Add fallback to a baked-in minimal list when fetch fails (currently it returns the hardcoded array from state-management.js — that array is what we're trying to delete)
- Make sure the existing `(Auto - Latest)` prepend logic still fires (it's currently inside `fetchAvailableModels`)
- Drop the hardcoded `models[]` arrays from each provider block in MODEL_CONFIG (keep `name`, `capabilities`, voice config — drop only `models`)

No new module needed. Existing event-driven dropdown-update flow already handles async resolution.

### Phase C: Android MVP — add ModelsRepository (Constants becomes fallback-only)

**Symbol corrected per audit C2**: Android uses `Constants.MODEL_CONFIG` (not `MODEL_NAMES`) of type `Map<String, List<Pair<String, String>>>`. 6 consumers must continue to compile.

- New `data/model/ModelInfo.kt` (serializable for backend wire format)
- New `data/repository/ModelsRepository.kt` — fetches `/models/{provider}` per provider, caches 5 min in-memory, falls back to `Constants.MODEL_CONFIG` on failure
- Internal conversion: backend's `[{id, name, default}]` → Kotlin `List<Pair<String, String>>` (id, name) at the repository boundary, preserving the existing consumer contract
- `Constants.MODEL_CONFIG` stays as **immutable fallback** — keep all 7 provider keys (4 chat + 3 voice) intact; chat keys get hydrated dynamically at runtime via repository, voice keys remain compile-time static
- `ChatViewModel` exposes `availableModels: StateFlow<Map<String, List<Pair<String,String>>>>` initialized from `Constants.MODEL_CONFIG` (fallback) then overwritten by repository fetch on init
- `SettingsSheet.kt:512` and other consumers read `availableModels.value[provider] ?: Constants.MODEL_CONFIG[provider]`

This pattern (mutable runtime + immutable fallback) is the cleanest way to add live-fetch without changing the consumer call sites (audit C2 fix without ripple).

### Phase D: Audit pass + hardware validation

Re-audit the implementation diffs. Then end-to-end test: hit all 4 `/models/{provider}` endpoints, confirm `source: "live"` for each. Reload web Portal, verify dropdowns. Rebuild Android APK, install, verify dropdowns. Push to MSO2 via existing update pipeline.

---

## Tasks (executable order)

### T1: Backend `/models/{provider}` xai + anthropic-live + cache + literal-fix
- Add `XAI_API_KEY` to `admin_routes.py:24` imports
- Replace `grok-4-1-fast-reasoning` literals at `chat_routes.py:5947, 6030` with `XAI_MODEL_DEFAULT` config import
- Add `xai` entry to `fallback_models` dict with current 2026 catalog (grok-4.3 + grok-4.20-* + keep grok-3-beta/grok-3-mini-beta for transition)
- Add anthropic live-fetch branch using `anthropic.Anthropic().models.list()` with `getattr` guards
- Add xai live-fetch branch using `openai.OpenAI(base_url="https://api.x.ai/v1", timeout=5.0)` with filter `startswith("grok-") AND not startswith("grok-imagine-")`
- Bump `XAI_MODEL_DEFAULT` in `config.py` from `grok-4-1-fast-reasoning` to `grok-4.3`
- New `Orchestrator/utils/models_cache.py` — TTL cache with positive (10min) + negative (30s) entries
- Return shape per locked contract; add `default_id` field
- Unit test: `pytest tests/test_models_cache.py` for cache + `curl /models/{provider}` for each of 4 providers

### T2: Web Portal — extend fetchAvailableModels in place
- Modify `Portal/modules/state-management.js:526-560`: wrap existing `fetchAvailableModels` with sessionStorage cache (5min TTL, key `models:${provider}`); add baked-in fallback list per provider (3-4 models each) for offline-bootstrap
- Drop hardcoded `models[]` arrays from each provider block in `MODEL_CONFIG` (keep all other fields)
- Verify `(Auto - Latest)` prepend still happens (currently inside the function — preserve it)
- Test: open browser, network tab shows `GET /models/xai` returns 200, dropdown populates with grok-4.3 + family

### T3: Android — ModelsRepository + ChatViewModel hydration
- Create `data/model/ModelInfo.kt` (serializable matching backend `{id, name, default}`)
- Create `data/model/ModelsResponse.kt` matching backend shape
- Create `data/repository/ModelsRepository.kt` — singleton-style per-Activity, 5min in-memory cache, converts to `Pair<String,String>` at boundary
- Keep `Constants.MODEL_CONFIG` as-is (audit C2: don't change symbol name); becomes fallback for offline-bootstrap
- Modify `ChatViewModel.kt`: add `availableModels: StateFlow<Map<String, List<Pair<String,String>>>>` initialized from Constants, hydrated on `init` via repository (background coroutine; non-blocking)
- Modify `SettingsSheet.kt:512`, `Composer.kt:330`, `CuScreen.kt:1030/1032`, `SmsInboxScreen.kt:423/480`, `NativeMainActivity.kt:405/416` to read from `availableModels.value[provider] ?: Constants.MODEL_CONFIG[provider]`
- Test: `./gradlew :app:compileDebugKotlin` clean; install APK, switch to xai provider, dropdown shows Grok 4.3 + family

### T4: Audit pass
- Dispatch adversarial reviewer subagent against the diffs (not the plan — that audit already happened)
- Fix any CRITICAL/MAJOR findings before validation

### T5: End-to-end validation
```bash
# Backend
for p in openai anthropic google xai; do
    curl -s http://localhost:9091/models/$p | python3 -m json.tool | head -15
done
# Expect: each shows source:"live", correct catalogs, default_id present

# Failure paths (audit N20)
curl http://localhost:9091/models/unknown    # → 404
unset XAI_API_KEY; curl http://localhost:9091/models/xai  # → source:"fallback"
```
- Web Portal: open in browser, dropdown populates from live data, no `grok-4-1-fast-reasoning` (deprecated)
- Android APK rebuild + install, repeat dropdown check
- Push to MSO2 via existing update pipeline; verify same on customer hardware

### T6: Commit + push + milestone snapshot
- Separate commits per phase for bisectability
- Push to GitHub
- Mint snapshot via `/chat/save`

---

## Critical Reuse

| Need | Existing pattern | File:line |
|---|---|---|
| `/models/{provider}` endpoint shape | already there | `admin_routes.py:487` |
| OpenAI live-fetch | already there | `admin_routes.py:567-577` |
| Google live-fetch | already there | `admin_routes.py:540-557` |
| **fetchAvailableModels web fetcher** | **already there** | `state-management.js:526` |
| **app-init call sites** | **already wired** | `app-init.js:133, 141` |
| `(Auto - Latest)` default prepend | already in fetchAvailableModels | inside `state-management.js:526` body |
| TTL cache pattern | snapshot index cache | `Orchestrator/fossils.py:269` |
| Android Repository + StateFlow | UpdatesRepository (T6 yesterday) | `data/repository/UpdateRepository.kt` |
| Backend SSE detached restart | onboarding restart pattern | `Orchestrator/routes/onboarding_routes.py:502` |

---

## Verification

**Per-task build:**
```bash
# T1
Orchestrator/venv/bin/python -m py_compile Orchestrator/routes/admin_routes.py Orchestrator/utils/models_cache.py Orchestrator/routes/chat_routes.py
Orchestrator/venv/bin/pytest Orchestrator/tests/test_models_cache.py -v

# T2
python3 -c "import re; c=open('Portal/modules/state-management.js').read(); print(f'braces {c.count(chr(123))}/{c.count(chr(125))}')"

# T3
cd "AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal" && ./gradlew :app:compileDebugKotlin
```

**End-to-end (T5):**
```bash
for p in openai anthropic google xai; do echo === $p ===; curl -s http://localhost:9091/models/$p | python3 -m json.tool | head -15; done
```
Expected: each shows `source: "live"`, current catalogs, `default_id` present.

**MSO2 customer-flow:**
1. Push commits → Portal "Install update" → restart
2. Open chat, switch to xAI → dropdown shows Grok 4.3 + 4.20 family (no deprecated 4-1-* IDs)
3. Switch to Anthropic → dropdown shows live-fetched catalog
4. On Android: rebuild + install, same checks

---

## Out of Scope (Deferred)

- Voice WebSocket model lists (`gpt-realtime`, `gemini-live-*`, `grok-live`) — separate endpoint design
- Computer Use pinned models (Anthropic CU `claude-opus-4-6`, Gemini CU `gemini-2.5-computer-use-preview-10-2025`) — subsystem-pinned, not dropdown choices
- Image/video/music generation model dropdowns — tool-call parameters, separate UI design
- Perplexity as chat-provider option — currently only `web_search` tool
- Per-model capability metadata surfaced in UI (vision/reasoning badges) — separate UI design pass
- Operator-scoped cache key (BYOK era) — flagged as TODO in models_cache.py
- Auth on `/models/{provider}` — assumes loopback/Tailscale-only access (BYOK era will revisit)
- `agents`/`gemini-agents`/`robotics`/`computer-use` provider keys in MODEL_CONFIG (these are not chat-provider entries but agent-system entries) — keep static

---

## Audit Resolutions (v2 incorporates)

All findings from the 2026-05-18 adversarial audit:

| # | Severity | Finding | Resolution |
|---|---|---|---|
| C1 | CRITICAL | Parallel fetcher race | Extend existing `fetchAvailableModels`; no new module |
| C2 | CRITICAL | Wrong Android symbol (`MODEL_NAMES` vs `MODEL_CONFIG`) | Keep `MODEL_CONFIG`, type `Pair<String,String>` preserved at consumer boundary |
| C3 | CRITICAL | Missing `XAI_API_KEY` import | Added to admin_routes.py:24 import in T1 |
| M4 | MAJOR | Fire-and-forget event has no listener | Existing flow is per-provider on-demand (lazy) — keep that, don't fire global event |
| M5 | MAJOR | Cache failure semantics | Positive 10min + negative 30s |
| M6 | MAJOR | Hardcoded grok-4-1-fast-reasoning at chat_routes.py:5947, 6030 | Replace with XAI_MODEL_DEFAULT import in T1 |
| M7 | MAJOR | `defaultModel` field is vaporware | Use existing `(Auto - Latest)` prepend + add `default_id` to backend response |
| M8 | MAJOR | Anthropic SDK field not guaranteed | `getattr(m, 'max_input_tokens', None)` guard |
| M9 | MAJOR | grok-imagine-* would leak into chat dropdown | Filter `startswith("grok-") AND not startswith("grok-imagine-")` |
| I10 | IMPORTANT | Old conversations with deprecated IDs | Keep deprecated IDs in fallback for transition; test reload of old conv in T5 |
| I11 | IMPORTANT | No HTTP timeout | `timeout=5.0` on openai/anthropic clients |
| I12 | IMPORTANT | `defaultModel` field never specified | Added `default_id` to backend response shape |
| I13 | IMPORTANT | T2 + T3 parallel = shape drift | Locked response contract in Phase A; both T2 and T3 reference it |
| I14 | IMPORTANT | Cache not operator-scoped | TODO comment in models_cache.py for BYOK era |
| I15 | IMPORTANT | No auth on /models/{provider} | Documented assumption: loopback/Tailscale-only |
| N16 | NICE | Sloppy deprecation claim re -reasoning suffix | Source link to docs.x.ai cited |
| N17 | NICE | Voice screen MODEL_CONFIG orphan risk | Keep voice keys static in Constants.MODEL_CONFIG; only chat keys hydrate |
| N18 | NICE | `agents`/`robotics` not mentioned | Listed in Out of Scope |
| N19 | NICE | `_humanize_xai_name` undefined | Drop — use `m.id` directly or simple title-case |
| N20 | NICE | No failure-path validation | Added missing-API-key + network-outage tests to T5 |
| N21 | NICE | Fallback drops favorite cheap model | Keep grok-3-mini-beta in frontend fallback |
