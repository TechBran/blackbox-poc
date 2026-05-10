# AI BlackBox Onboarding — Discovery Notes & Cheat Sheet

**Status:** Discovery complete · Planning not yet started
**Date:** 2026-05-10
**Author:** Claude Opus 4.7 (with Brandon)
**Purpose:** Single reference for the upcoming onboarding-UI build. Captures everything discovered about the existing install/secrets/dependencies/pairing surface so we don't have to rediscover when planning starts.
**Companion snapshots:** SNAP-20260509-6533 (mono-repo migration); this session ID for the discovery sweep itself.

---

## TL;DR — Executive Summary

1. **There is no onboarding flow today.** No first-run detection, no setup wizard, no welcome modal. Fresh installs boot directly into the chat UI with `Brandon` as the default operator.
2. **The infrastructure to build on is partial.** Half-built pieces: `setup.sh` (stale port refs), `install_enhanced_service.sh` + `blackbox-enhanced.service` (production-grade unit, never deployed), `/pair/start` (mints tokens but no `/pair/claim` validates them), Android `PairingActivity` (works), `cellular/hotplug.py` (good template for other peripherals).
3. **Three blocker classes** must be solved before a useful onboarding UI can ship:
   - **Secrets sprawl** — 16 files hold credential material; 17 distinct secret env-vars; `OPENAI_API_KEY` and `GOOGLE_API_KEY` each duplicated across 11 files.
   - **Path portability** — 194 hits of hardcoded `/home/ai-black-box-fc/...` and Tailscale FQDN baked into checked-in code (systemd units, app HTML, MCP config, docs).
   - **Dependency reproducibility** — `requirements.txt` declares 11 packages but the live venv has ~120 installed (transitive). A fresh `pip install -r requirements.txt` does NOT recreate the working environment.

---

## Brandon's Stated Onboarding Requirements → Mapped to Findings

| Requirement (your words) | Current state | Onboarding implication |
|---|---|---|
| **File path resolution — every system needs to work with proper path resolution** | 194 hardcoded `/home/ai-black-box-fc/...` hits in checked-in code | Need `BLACKBOX_ROOT` env var + a path-resolver utility used everywhere. Audit every hit, replace with `os.path.join(BLACKBOX_ROOT, ...)` or relative paths. |
| **Tailscale install + verification** | Code assumes Tailscale exists in 2 places (`device_registry/registry.py:168`, Twilio webhook URL). LAN works without it. Hardcoded FQDN `ai-black-box-fc-a620ai-wifi.tail401fb3.ts.net` in 30+ files. | Install step calls `curl -fsSL https://tailscale.com/install.sh \| sh`. Verify via `tailscale status --json`. Discover hostname, write to a single config var. |
| **All dependencies (FastAPI/uvicorn server)** | `requirements.txt` declares 11 deps, venv has ~120. `setup.sh` exists but stale. Missing system packages: `tmux`, `python3-dbus`, `python3-gi`. | Pin `requirements.txt` from `pip freeze`. Maintain explicit system-package list. Onboarding script: `apt install` → `python -m venv` → `pip install -r`. |
| **Single source of truth for all secrets + JSON keys + service accounts** | 16 files hold secrets. Two duplicate `Secrets/.env` files (orphans). `GEMINI_API_KEY` bypasses central config. Asterisk/FreeSWITCH passwords baked into both `.env` AND config files. | Consolidate to one canonical location: root `.env` for env vars, `credentials/` for service-account JSONs. Refactor 9 stragglers to import from `Orchestrator.config`. Add per-provider validation endpoint. |
| **Test each API key with a few tokens (like Claude Code does)** | No validation endpoints exist today. | Build `/onboarding/validate/{provider}` endpoint per provider. Each does a single cheap token call (`models.list`, `messages.create max_tokens=1`, `genai.list_models`, etc.). |
| **QR pairing for adding devices** | Backend `/pair/start` mints tokens (in wrong file: `tts_routes.py:308`). No `/pair/claim` validates redemption. Web Portal UI uses external `api.qrserver.com`. Android side (`PairingActivity.kt`) works well. | Move `/pair/start` to proper `pairing_routes.py`. Add `/pair/claim`. Replace external QR service with local generation (already done in Android via ZXing — port that pattern to Portal). |
| **Initial local page auto-opens BlackBox UI on mini-PC for QR pairing** | No autostart `.desktop` file. No kiosk wrapper. User must manually open `http://localhost:9091/ui/index.html`. | Add `~/.config/autostart/blackbox-portal.desktop` that runs `xdg-open http://localhost:9091/ui` (or `chromium --app=...` for kiosk feel). Onboarding step generates and installs this. |
| **Modernized UI walking through every step** | No UI exists. Portal index.html has settings modal but no wizard. | Build a fresh `Portal/onboarding/` module. Step-by-step wizard with progress indicator, can resume mid-flow. |
| **Gmail OAuth — Google Cloud setup needed, code is ready** | `Orchestrator/gmail/service.py` works via `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` env vars. `credentials/gmail_oauth_client.json` is dead weight (not loaded by current code). | Onboarding step: text + screenshots walking user through Google Cloud Console (create project → enable Gmail API → create OAuth client → download JSON → paste client_id + client_secret into wizard form). Validation: trigger OAuth flow, verify `Manifest/gmail_tokens/{operator}.json` written. |

---

## Six Pillars of the Onboarding Build

### Pillar A — Single Source of Truth for Secrets

**Problem:** 17 secret-bearing env-var names referenced across 16 files; `OPENAI_API_KEY` and `GOOGLE_API_KEY` each in 11 files. Two orphan `Secrets/.env` files exist (likely deletable). One env var (`GEMINI_API_KEY`) bypasses central config.

**Already-good baseline:** `Orchestrator/config.py:115` calls `load_dotenv()` exactly once at startup; lines 322–452 export every secret as a module-level constant. The pattern is correct. The duplication is *re-reads via `os.getenv` after import*.

**To build:**
1. Audit + delete orphans (`./Secrets/.env`, `./Orchestrator/Secrets/.env`, `./credentials/gmail_oauth_client.json` if confirmed unused).
2. Refactor 9 stragglers (listed in cheat-sheet appendix) from `os.getenv("X")` to `from Orchestrator.config import X`.
3. Add `GEMINI_API_KEY` to `config.py` with `GOOGLE_API_KEY` fallback.
4. Build `Orchestrator/onboarding/secrets_writer.py` — atomic `.env` write with backup + validation.
5. Build `Orchestrator/onboarding/validators.py` — one validator per provider:
   - `validate_openai(key)` → `client.models.list()` (1 token cost)
   - `validate_anthropic(key)` → `client.messages.create(model="claude-haiku-4-5", max_tokens=1, messages=[{role:"user",content:"hi"}])`
   - `validate_google(key)` → `genai.list_models()`
   - `validate_xai(key)` → `client.models.list()`
   - `validate_twilio(sid, token)` → `client.api.accounts(sid).fetch()`
   - `validate_asterisk(host, password)` → `GET /asterisk/info` via ARI
   - `validate_google_service_account(json_path)` → `service_account.Credentials.from_service_account_file(...).refresh()` to verify signature
6. Build `Orchestrator/routes/onboarding_routes.py` — `POST /onboarding/validate` per provider, returns `{ok, latency_ms, error?}`.

**Files holding secret material (the canonical list):**
| Type | Count | Files |
|---|---|---|
| `.env` files | 8 | `./.env`, `./.env.template`, `./Secrets/.env` (orphan?), `./Orchestrator/Secrets/.env` (orphan?), `./Docker/phone.env.example`, `./docs/ugv-beast/setup/ugv_tools_api/deploy/{supervisor.env,er.env.example,ears.env.example}` |
| credentials/ | 2 | `gen-lang-client-0808228253-37e326fde5fd.json` (Google service account, used), `gmail_oauth_client.json` (dead weight) |
| Per-operator OAuth tokens | 2+ | `./Manifest/gmail_tokens/{operator}.json` (auto-minted by Gmail flow) |
| Service config files | 4 | `./Orchestrator/asterisk/configs/{ari.conf,pjsip.conf}`, `./Docker/freeswitch/config/freeswitch.xml`, `./Docker/drachtio/config/drachtio.conf.xml` |

**Severity findings:**
- **Zero hardcoded API keys in checked-in code.** GitHub migration is clean. ✅
- **Medium severity:** weak fallback defaults in source — `config.py:381 "cymru"` (Drachtio), `config.py:386 "ClueCon"` (FreeSWITCH default), `asterisk/config.py:51 "password"` (TG200). Onboarding should rotate + remove the defaults so missing env fails loudly.

---

### Pillar B — Dependencies & System Packages

**Problem:** `requirements.txt` declares 11 packages, live venv has ~120. Fresh `pip install -r requirements.txt` will not reproduce the working environment.

**Bucket classification (THE planning artifact):**

**MUST_HAVE — system won't start without:**
- Ubuntu base + `python3.12 python3.12-venv python3-pip`
- `build-essential git curl ca-certificates tmux sudo`
- `python3-dbus python3-gi` (system-pkg, for screenshot portal subprocess)
- All `requirements.txt` deps PLUS the transitive deps that need pinning: `anthropic openai google-genai google-generativeai apscheduler numpy scipy cryptography lxml pillow miniaudio webrtcvad-wheels pyserial mss reportlab python-docx beautifulsoup4 html2text duckduckgo_search psutil pytz tzlocal`
- `.env` with at minimum ONE provider key (OPENAI / ANTHROPIC / GOOGLE)
- systemd unit installed and started

**SHOULD_HAVE — degraded but functional without:**
- `tailscale` daemon (no public hostname, LAN-only)
- Chrome at `/opt/google/chrome/chrome` + `xdotool scrot openbox x11vnc` (Computer Use breaks, other providers work)
- Node binaries via nvm: `npm i -g @anthropic-ai/claude-code @google/gemini-cli @openai/codex` (CLI Agent breaks, core chat works)
- `mpg123 alsa-utils pulseaudio-utils` (audio playback degrades)

**FEATURE_OPTIONAL — only that feature breaks:**
- `docker.io docker-compose` + `Docker/docker-compose.phone.yml` up (FreeSwitch/Drachtio SIP only)
- Asterisk PBX system service + `Scripts/setup-cellular-polkit.sh` (cellular IVR only)
- `adb` (Android device pairing routes)
- `vncdotool` + `x11vnc` (remote-desktop CU targeting only)

**HARDWARE_OPTIONAL:**
- SIM7600G-H cellular modem (`pyserial`, `/dev/ttyUSB*`, `dialout` group)
- All UGV/robot stack — never on mini-PC, isolate to `docs/ugv-beast/`

**To build:**
1. Pin `requirements.txt` from `pip freeze` of the working venv (after one-time cleanup).
2. Maintain `docs/onboarding/system-packages.txt` with the apt list above, bucketed.
3. Onboarding step: detect what's already installed, only show user what they need to install. Show one-shot copy-paste commands.

---

### Pillar C — File Path Resolution

**Problem:** 194 hardcoded references to `/home/ai-black-box-fc/...` or `ai-black-box-fc-a620ai-wifi.tail401fb3.ts.net` in checked-in code. Any other machine = everything breaks.

**Worst offenders (need surgical replacement):**
- `Apps/system-monitor/blackbox-monitor.service` — User=, WorkingDirectory=, ExecStart= all hardcoded
- `.mcp.json:6,10` — full path to `MCP/blackbox_mcp_server.py` and `BLACKBOX_ROOT`
- `Claude.md:11,24,124,385` — Tailscale FQDN baked into examples
- `AUDIT_REPORT.md` — entire doc assumes specific hostname + `/home/ai-black-box-fc/`
- `Apps/{echoes-of-titan,dragon-production,...}/index.html` — media URLs use full Tailscale URL (these break for any user on any other machine; should be relative `/ui/uploads/...` or origin-aware)
- `Apps/*/server.py` — likely have similar hardcoded paths

**To build:**
1. Add `BLACKBOX_ROOT` env var to canonical `.env` (defaults to `os.path.dirname(os.path.dirname(__file__))` if unset).
2. Add `Orchestrator/utils/paths.py` — central `resolve(...)` helper. All file-path construction uses it.
3. Sweep all 194 hits. Categorize: (a) replace with relative path, (b) replace with `BLACKBOX_ROOT/<rest>`, (c) replace with `request.base_url` for app URLs.
4. systemd unit: rewrite via template at install time, substituting `${USER}` and `${BLACKBOX_ROOT}` at install-script execution.
5. `Apps/*/index.html` media URLs: serve via `<base href="${origin}">` injected at render time, or rewrite to relative paths at app registration.

**FOLLOWUP NEEDED:** Audit the 194 hits to bucket them. ~30 minutes of grep + categorization, deferred until planning starts.

---

### Pillar D — Tailscale Install + Verification

**Today:**
- Code shells out `tailscale status --json` only at `device_registry/registry.py:168`. Without Tailscale binary: `RuntimeError`.
- `.env` `TWILIO_WEBHOOK_BASE_URL` hardcodes the current machine's `*.ts.net` FQDN.
- `Apps/dragon-production/index.html` etc. hardcode the same FQDN.

**Onboarding flow for Tailscale:**
1. **Detect:** is `tailscale` binary on PATH? (`which tailscale`)
2. **Install if missing:** `curl -fsSL https://tailscale.com/install.sh | sudo sh`
3. **Authenticate:** `sudo tailscale up` — open browser to authenticate with user's Tailscale account
4. **Verify:** `tailscale status --json` returns valid output; user's hostname is visible
5. **Capture hostname:** parse JSON, write `BLACKBOX_TAILNET_HOSTNAME=foo.tail-net.ts.net` to `.env`
6. **HTTPS cert:** optionally `sudo tailscale cert <hostname>` for cert provisioning
7. **Twilio webhook URL:** auto-update `TWILIO_WEBHOOK_BASE_URL` in `.env` if Twilio is being configured

**Skippable:** if user opts out, system runs LAN-only on `localhost:9091`. Phone pairing then requires being on the same LAN.

---

### Pillar E — QR Pairing & Mini-PC Auto-Launch

**Pairing today:**
- **Backend:** `Orchestrator/routes/tts_routes.py:308-321` (orphan placement) defines `pair_tokens` dict + `POST /pair/start` returning `{type, token, exp}`. Token TTL 300s. **No `/pair/claim` or `/pair/status` ever validates.**
- **Web Portal:** `Portal/index.html:759-771` (#pairModal) + `Portal/modules/ui-setup.js:788-820`. Renders QR via external `api.qrserver.com` (network-dependent — breaks if internet down).
- **Android:** `PairingActivity.kt` works well — ZXing scanner, persists origin to SharedPreferences, idempotent re-pair guard. `SettingsViewModel.fetchPairToken()` allows phone to mint its own QR for cross-device pairing.

**Auto-launch on mini-PC:**
- **Today:** No autostart .desktop file in repo. No kiosk wrapper. User opens browser manually.
- **Goal:** when mini-PC boots, BlackBox Portal automatically opens in a browser window (full-screen or app-mode), with the QR pairing code visible right on the home screen.

**To build:**
1. Move `/pair/start` to `Orchestrator/routes/pairing_routes.py`. Add `/pair/claim` to validate token consumption (one-time use).
2. Replace `api.qrserver.com` dependency with server-side or client-side QR generation. Server-side: use Python `qrcode` lib in `pairing_routes.py` returning PNG. Client-side: use a JS QR lib (qrcode.js).
3. Add `Portal/onboarding/welcome.html` as the post-onboarding home page — shows operator status, QR pairing always visible, "add another device" CTA.
4. Onboarding step writes `~/.config/autostart/blackbox-portal.desktop`:
   ```ini
   [Desktop Entry]
   Type=Application
   Name=AI BlackBox Portal
   Exec=chromium --app=http://localhost:9091/ui --start-fullscreen
   X-GNOME-Autostart-enabled=true
   ```
5. (Optional) Build a cleaner Chromium app-mode wrapper that hides browser chrome.

---

### Pillar F — Per-Provider Setup Guides (Gmail Pattern)

**The Gmail case** illustrates a broader pattern: some integrations need user-side configuration in a third-party console (Google Cloud, Twilio, OpenAI org, etc.) before BlackBox can use them. Onboarding must walk the user through that external setup.

**Gmail specifically:**
- Code is ready (`Orchestrator/gmail/service.py` works via env vars).
- User must: create Google Cloud project → enable Gmail API → create OAuth 2.0 client → download JSON OR copy client_id + client_secret → paste into wizard.
- Validation: trigger OAuth flow against Google. On success, `Manifest/gmail_tokens/{operator}.json` is written.

**Other integrations needing similar treatment:**
- **Twilio:** sign up → create project → buy phone number → copy Account SID + Auth Token + phone number → paste into wizard.
- **OpenAI/Anthropic/Google AI keys:** sign up → create API key → paste. Validate with cheap call.
- **Google Cloud service account** (for Cloud TTS / Vertex / Imagen / Veo / Lyria / embeddings): create service account → grant roles → download JSON → upload via wizard. Most complex — should be optional.
- **Tailscale:** sign up → join tailnet → run install + `tailscale up` (covered in Pillar D).

**Pattern:** each integration gets a wizard step with: explanation text, screenshots/links to the third-party console, paste-fields for the credentials, validation button, success/fail status, "skip for now" option.

---

## What Exists vs What to Build

| Area | What exists | What to build |
|---|---|---|
| **systemd unit** | `blackbox.service` (active, simple), `blackbox-enhanced.service` (template, never deployed) | Resurrect `blackbox-enhanced.service` (notify-type, watchdog, hardening). Make install script template-substitute paths. |
| **Install script** | `setup.sh` (stale, port mismatch), `install_enhanced_service.sh` (incomplete) | New `Scripts/onboarding/bootstrap.sh` — orchestrates apt install + venv + systemd. Idempotent. |
| **First-run detection** | None | `Orchestrator/onboarding/state.py` — checks `BLACKBOX_ROOT/.onboarding_complete` flag. Routes serve wizard if absent. |
| **Wizard UI** | None | `Portal/onboarding/` module — step-by-step Vue/vanilla JS, progress bar, resumable. |
| **Per-provider validators** | None | `Orchestrator/onboarding/validators.py` — one-token validation per provider. |
| **Pairing redemption** | `/pair/start` exists, no `/pair/claim` | Move to `pairing_routes.py`, add claim flow. |
| **QR rendering (web)** | External `api.qrserver.com` | Local generation (server-side `qrcode` lib OR client-side `qrcode.js`). |
| **Auto-launch on boot** | None | `~/.config/autostart/blackbox-portal.desktop` written by install script. |
| **Hotplug template** | `cellular/hotplug.py` | Generalize to `Orchestrator/hotplug/` package, add camera/IMU/microphone detectors. (Maybe out of scope for v1 onboarding.) |

---

## Reference: Files to Read First When Planning

When the next session starts planning, these are the entry points:

1. **`Orchestrator/config.py`** — current secrets central config, mostly correct
2. **`Orchestrator/app.py`** + **`Orchestrator/startup.py:227-260`** — FastAPI lifespan; where to add first-run check
3. **`Orchestrator/routes/tts_routes.py:308-321`** — current `/pair/start` (move to `pairing_routes.py`)
4. **`Portal/index.html:759-771`** + **`Portal/modules/ui-setup.js:788-820`** — current pairing UI to replace
5. **`AI_BlackBox_Portal_Android_MVP (2)/.../PairingActivity.kt`** + **`.../SettingsSheet.kt:704-758,877-934`** — Android side reference (well-built)
6. **`setup.sh`** + **`install_enhanced_service.sh`** + **`blackbox-enhanced.service`** — current install pieces to consolidate
7. **`requirements.txt`** — needs full pinning
8. **`Orchestrator/cellular/hotplug.py`** — peripheral hotplug pattern
9. **`docs/onboarding/discovery-notes.md`** — this document

---

## Architectural Decisions (LOCKED 2026-05-10)

After reviewing the discovery findings, Brandon locked in the following decisions. These define the project shape for the entire onboarding build.

| Decision | Value | Implication |
|---|---|---|
| **Target persona** | Eventual customers / strangers buying a BlackBox | Full polish — every error needs recovery path, no assumed Linux knowledge, hardware detection. Defines the long-term UX bar. |
| **Distribution model** | Hardware product first (pre-installed mini-PC); software-only distribution as v2 | v1: mini-PC ships with Ubuntu + BlackBox + Tauri setup app pre-installed. v2: separate "BlackBox Installer" Tauri app handles software-only path. Same setup-app code reused. |
| **UI architecture** | **Tauri shell wrapping Portal `/onboarding` routes** | UI lives in HTML/CSS/JS in the Portal codebase (developer-friendly iteration, reuses existing styling + state-management + api-client modules). Tauri Rust shell provides full-screen branded chrome, taskbar icon, no browser address bar. ~10MB binary. |
| **Validation strictness** | Each integration has "Skip for now" | Onboarding marks complete with whatever's configured. User can return later. Matches the SHOULD_HAVE / FEATURE_OPTIONAL bucket structure. |
| **Secrets centralization scope** | **Lite** — collapse the 9 Python stragglers re-reading `os.getenv` to import from `Orchestrator.config`; leave Asterisk/FreeSWITCH config files alone | The 4 service config files (`ari.conf`, `pjsip.conf`, `freeswitch.xml`, `drachtio.conf.xml`) keep their own sources of truth since they're consumed by external daemons, not Python. ~2-3 hr of work, preserves what works. |
| **API key model** | **Bring-your-own-keys (BYOK)** | Customer pastes their own OpenAI / Anthropic / Google / etc. keys into the wizard. Customer pays providers directly. Zero billing infrastructure in v1. Wizard's per-provider step structure preserves future option to add "use BlackBox-pooled" toggle without architectural change. |

**Realistic v1 timeline:** 6-8 weeks of focused multi-session work across six tracks (see below).

---

## Six Implementation Tracks (skeleton — full plan to follow)

| # | Track | Effort | Dependencies |
|---|---|---|---|
| **0** | **Foundation cleanup** — pin `requirements.txt`, lite secrets sweep (9 stragglers), `BLACKBOX_ROOT` + path-resolver utility, sweep 194 hardcoded paths, move `/pair/start` to `pairing_routes.py` + add `/pair/claim` validation | 1–2 weeks | none — must come first |
| **1** | **Onboarding backend** — new `Orchestrator/routes/onboarding_routes.py` (state, validators, save, complete, skip), per-provider validators (`validators.py`), `Orchestrator/onboarding/state.py` first-run detection | 1 week | needs Track 0 |
| **2** | **Onboarding Portal UI** — new `Portal/onboarding/` module, modernized step-by-step wizard (~7-10 steps), resumable, branded, customer-facing | 2–3 weeks | needs Track 1 |
| **3** | **Tauri shell app** — new `installer/` Cargo project, full-screen webview wrapper, .deb / AppImage build, autostart `.desktop` file, self-disable after onboarding completes | 1–2 weeks | needs Track 2 (or can mock against placeholder) |
| **4** | **Install scripts** — modern `Scripts/install.sh` template-substituting paths, systemd unit installer, idempotent re-run, factory image script | 1 week | parallelizable with Tracks 1+2 |
| **5** | **Customer-facing docs** — README.md, TROUBLESHOOTING.md, per-integration setup guides (Gmail screenshots, Twilio walkthrough, Tailscale auth, etc.) | 1 week | parallelizable, finalizes after Track 2 |
| **6** | **(v2) Software-only distribution** — second Tauri app that *installs the BlackBox stack* before launching the setup app. Reuses Track 3 entirely. | 2 weeks | deferred until v1 hardware path ships |

---

## Open Questions DEFERRED to plan-writing session (not blocking)

These don't change the architectural shape but need answers before the plan can be written:

1. **Platform support** — Ubuntu only for v1? Or Debian-likes (Pop!_OS, Mint)? macOS for dev?
2. **Idempotency** — if user already has partial setup on re-run, detect-and-skip or always-prompt?
3. **Migration vs fresh** — does v1 support migrating an existing BlackBox install forward (snapshots, config, paired devices)? Or strictly net-new?
4. **Single-operator vs multi-operator setup** — does onboarding walk through adding multiple operators, or just default `Brandon`?
5. **Hardening at install time** — should onboarding auto-rotate the weak default passwords (Drachtio "cymru", FreeSWITCH "ClueCon", TG200 "password")? Recommend yes.
6. **Tier-1 integrations vs nice-to-have for v1** — do we need ALL provider validators (OpenAI, Anthropic, Google, xAI, ElevenLabs, Twilio, Asterisk, Google Service Account, Tailscale, Gmail), or can we ship v1 with the top 3-4 most-used and add the rest in v1.1?
7. **Hardware spec for shipped mini-PC** — is the spec nailed down? Affects factory image build (Track 5).
8. **Tailscale customer flow** — each customer joins their own tailnet (own auth), or all customers join your tailnet (shared subdomain)?

---

## Cheat Sheet: Specific File:Line Citations

**Secret-bearing env vars in central config (`Orchestrator/config.py`):**
- `OPENAI_API_KEY` L322 · `ANTHROPIC_API_KEY` L323 · `GOOGLE_API_KEY` L324 · `XAI_API_KEY` L325 · `PERPLEXITY_API_KEY` L326
- `PBX_3CX_PASSWORD` L374 · `DRACHTIO_SECRET` L381 (default "cymru") · `FREESWITCH_ESL_PASSWORD` L386 (default "ClueCon")
- `TWILIO_ACCOUNT_SID` L412 · `TWILIO_AUTH_TOKEN` L413 · `TWILIO_PHONE_NUMBER` L414 · `TWILIO_WEBHOOK_BASE_URL` L418
- `ASTERISK_ARI_PASSWORD` L436
- `GOOGLE_OAUTH_CLIENT_ID` L448 · `GOOGLE_OAUTH_CLIENT_SECRET` L449 · `GOOGLE_APPLICATION_CREDENTIALS` L452

**Stragglers re-reading via `os.getenv` (refactor to `from Orchestrator.config import …`):**
- `OPENAI_API_KEY`: `tts_routes.py:119,210,324,356`, `admin_routes.py:320`, `asterisk/ivr_audio.py:74`
- `ANTHROPIC_API_KEY`: `admin_routes.py:321`
- `GOOGLE_API_KEY`: `backfill_embeddings.py:26`, `admin_routes.py:322`
- `GEMINI_API_KEY`: `gemini_agent_routes.py:160,705` (the only env-var that bypasses central config)
- `ASTERISK_ARI_PASSWORD`: `asterisk/config.py:19`
- `TG200_HTTP_PASSWORD`: `asterisk/config.py:51` (default "password")

**Service config files with embedded passwords:**
- `Orchestrator/asterisk/configs/ari.conf:13` (ARI HTTP password)
- `Orchestrator/asterisk/configs/pjsip.conf:38` (SIP endpoint pincode), `:96` (SIP endpoint password)
- `Docker/freeswitch/config/freeswitch.xml:35` (ESL password), `:75` (3CX gateway password)
- `Docker/drachtio/config/drachtio.conf.xml:12` (Drachtio admin secret)

**Pairing endpoints today:**
- `Orchestrator/routes/tts_routes.py:308-321` (POST /pair/start — orphan placement)
- `Orchestrator/routes/admin_routes.py:245-279` (/health includes pairing.default_origin)

---

*End of discovery notes. Next step: Brandon answers the Open Questions, then we write the implementation plan.*
