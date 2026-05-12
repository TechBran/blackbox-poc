// Optional integrations step — fourth screen of the onboarding wizard.
// Gmail OAuth (active) + Twilio / ElevenLabs (v1.1 deferred placeholders).
// Save & continue is always enabled — every integration here is optional.

const GMAIL_PROVIDER = {
    id: "gmail",
    label: "Gmail",
    description: "Read your Gmail inbox so the AI can help triage emails, draft replies, and surface calendar invites.",
    consoleUrl: "https://console.cloud.google.com/apis/credentials",
    docsUrl: "https://developers.google.com/workspace/guides/create-credentials#oauth-client-id",
};

const PLACEHOLDER_INTEGRATIONS = [
    {
        id: "twilio",
        label: "Twilio",
        description: "Inbound + outbound phone calls and SMS via Twilio webhooks.",
        v1_1_note: "Available in v1.1. v1 uses your TG200 cellular modem for phone + SMS — no setup needed.",
    },
    {
        id: "elevenlabs",
        label: "ElevenLabs",
        description: "Premium voice synthesis for text-to-speech in agent voices.",
        v1_1_note: "Available in v1.1. v1 uses Google + OpenAI TTS for high-quality speech.",
    },
];

let busy = false;

export async function render(container, { next, back, skip }) {
    const state = {
        gmail: { client_id: "", client_secret: "", status: "idle", result: null },
    };

    container.innerHTML = `
        <section class="ob-step ob-optional">
            <aside class="ob-step-sigil" aria-hidden="true">
                <div class="ob-step-sigil-num"><em>04</em></div>
                <div class="ob-step-sigil-rule"></div>
                <div class="ob-step-sigil-label">EXTRAS</div>
            </aside>
            <div class="ob-step-body">
                <div class="ob-step-eyebrow">
                    <span class="ob-step-eyebrow-dot" aria-hidden="true"></span>
                    Optional integrations
                </div>
                <h1 class="ob-step-title">
                    Wire up <em>extras</em>.
                </h1>
                <p class="ob-step-lede">
                    Each of these is optional. Add the ones you want now, or
                    skip and configure later from the System Menu.
                </p>
                <div class="ob-providers" id="ob-integrations">
                    ${renderGmailCard(GMAIL_PROVIDER)}
                    ${PLACEHOLDER_INTEGRATIONS.map(renderPlaceholderCard).join("")}
                </div>
                <div class="ob-cta-row">
                    <button type="button" class="ob-cta" id="ob-extras-save">
                        Save &amp; continue <span class="ob-cta-arrow" aria-hidden="true">&rarr;</span>
                    </button>
                </div>
                <nav class="ob-step-nav" aria-label="Step navigation">
                    <button type="button" class="ob-back" id="ob-extras-back">
                        <span aria-hidden="true">&larr;</span> Back to API keys
                    </button>
                    <button type="button" class="ob-skip" id="ob-extras-skip">
                        Skip everything &mdash; configure later <span aria-hidden="true">&rarr;</span>
                    </button>
                </nav>
            </div>
        </section>
    `;

    wireGmailCard(container, state, GMAIL_PROVIDER);
    wireSave(container, state, next);
    document.getElementById("ob-extras-back").addEventListener("click", back);
    document.getElementById("ob-extras-skip").addEventListener("click", skip);
}

function renderGmailCard(p) {
    return `
        <div class="ob-provider-card ob-integration-card" data-provider="${p.id}">
            <div class="ob-provider-header">
                <div class="ob-provider-label">${escapeHtml(p.label)}</div>
                <a class="ob-provider-link" href="${escapeHtml(p.consoleUrl)}" target="_blank" rel="noopener">
                    Google Cloud Console <span aria-hidden="true">↗</span>
                </a>
            </div>
            <p class="ob-integration-desc">${escapeHtml(p.description)}</p>
            <details class="ob-disclosure ob-walkthrough">
                <summary class="ob-disclosure-summary">
                    <span class="ob-disclosure-q">
                        Walk me through <em>Google Cloud OAuth setup</em>
                    </span>
                    <span class="ob-disclosure-toggle" aria-hidden="true">Show / Hide</span>
                </summary>
                <div class="ob-walkthrough-body">
                    <ol class="ob-walkthrough-steps">
                        <li>In <a href="${escapeHtml(p.consoleUrl)}" target="_blank" rel="noopener">Google Cloud Console &rarr; Credentials</a>, click <strong>Create Credentials &rarr; OAuth client ID</strong>.</li>
                        <li>Pick <strong>Web application</strong> as the type. Name it something like "AI BlackBox".</li>
                        <li>Under <strong>Authorized redirect URIs</strong>, add <code>http://localhost:9091/auth/gmail/callback</code>.</li>
                        <li>Click <strong>Create</strong>. Google shows you a <em>Client ID</em> and <em>Client Secret</em> &mdash; paste both below.</li>
                        <li>Don't forget to enable the <strong>Gmail API</strong> in <a href="https://console.cloud.google.com/apis/library/gmail.googleapis.com" target="_blank" rel="noopener">API Library</a>.</li>
                    </ol>
                </div>
            </details>
            <div class="ob-gmail-fields">
                <label class="ob-field-label" for="ob-gmail-client-id">Client ID</label>
                <input
                    type="text"
                    class="ob-provider-input"
                    id="ob-gmail-client-id"
                    placeholder="123456789-abc...apps.googleusercontent.com"
                    autocomplete="off"
                    autocapitalize="off"
                    spellcheck="false"
                />
                <label class="ob-field-label" for="ob-gmail-client-secret">Client Secret</label>
                <div class="ob-provider-input-row">
                    <input
                        type="password"
                        class="ob-provider-input"
                        id="ob-gmail-client-secret"
                        placeholder="GOCSPX-..."
                        autocomplete="off"
                        autocapitalize="off"
                        spellcheck="false"
                    />
                    <button type="button" class="ob-reveal-btn" id="ob-gmail-reveal" aria-label="Show or hide Client Secret">&#128065;</button>
                    <button type="button" class="ob-validate-btn" id="ob-gmail-validate" disabled>Validate</button>
                </div>
            </div>
            <div class="ob-provider-status" id="ob-gmail-status" data-status="idle"></div>
        </div>
    `;
}

function renderPlaceholderCard(p) {
    return `
        <div class="ob-provider-card ob-integration-card ob-integration-deferred" data-provider="${p.id}" aria-disabled="true">
            <div class="ob-provider-header">
                <div class="ob-provider-label">${escapeHtml(p.label)}</div>
                <span class="ob-deferred-pill">Coming in v1.1</span>
            </div>
            <p class="ob-integration-desc">${escapeHtml(p.description)}</p>
            <p class="ob-integration-deferred-note">${escapeHtml(p.v1_1_note)}</p>
        </div>
    `;
}

function wireGmailCard(container, state, p) {
    const idInput = container.querySelector("#ob-gmail-client-id");
    const secretInput = container.querySelector("#ob-gmail-client-secret");
    const revealBtn = container.querySelector("#ob-gmail-reveal");
    const validateBtn = container.querySelector("#ob-gmail-validate");
    const statusEl = container.querySelector("#ob-gmail-status");

    function updateValidateButton() {
        validateBtn.disabled = !(state.gmail.client_id && state.gmail.client_secret);
    }

    function resetStatus() {
        if (state.gmail.status !== "idle") {
            state.gmail.status = "idle";
            state.gmail.result = null;
            statusEl.dataset.status = "idle";
            statusEl.innerHTML = "";
        }
    }

    idInput.addEventListener("input", () => {
        state.gmail.client_id = idInput.value.trim();
        resetStatus();
        updateValidateButton();
    });
    secretInput.addEventListener("input", () => {
        state.gmail.client_secret = secretInput.value.trim();
        resetStatus();
        updateValidateButton();
    });
    revealBtn.addEventListener("click", () => {
        const isPassword = secretInput.type === "password";
        secretInput.type = isPassword ? "text" : "password";
        revealBtn.innerHTML = isPassword ? "&#128584;" : "&#128065;";
    });
    validateBtn.addEventListener("click", () => validateGmail(container, state));
}

async function validateGmail(container, state) {
    const validateBtn = container.querySelector("#ob-gmail-validate");
    const statusEl = container.querySelector("#ob-gmail-status");
    const idInput = container.querySelector("#ob-gmail-client-id");
    const secretInput = container.querySelector("#ob-gmail-client-secret");

    if (state.gmail.status === "validating") return;
    state.gmail.status = "validating";
    statusEl.dataset.status = "validating";
    statusEl.innerHTML = `<span class="ob-status-pill ob-status-pill-validating">Validating OAuth flow&hellip;</span>`;
    validateBtn.disabled = true;
    idInput.disabled = true;
    secretInput.disabled = true;

    try {
        const r = await fetch("/onboarding/validate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                provider: "gmail",
                credentials: {
                    client_id: state.gmail.client_id,
                    client_secret: state.gmail.client_secret,
                },
            }),
        });
        const result = await r.json();
        state.gmail.result = result;
        if (result.ok) {
            state.gmail.status = "ok";
            statusEl.dataset.status = "ok";
            statusEl.innerHTML = `
                <span class="ob-status-pill ob-status-pill-ok">
                    <span class="ob-status-pill-glyph" aria-hidden="true">&check;</span>
                    OAuth flow constructed &middot; ${result.latency_ms}ms
                </span>
                <p class="ob-step-helper">
                    You'll complete the actual Gmail authorization once setup is done &mdash; we'll
                    send you to Google's consent screen from the System Menu.
                </p>
            `;
        } else {
            state.gmail.status = "error";
            statusEl.dataset.status = "error";
            const errMsg = (result.error || "validation failed").replace(/^\w+Error:\s*/, "");
            statusEl.innerHTML = `
                <span class="ob-status-pill ob-status-pill-error">
                    <span class="ob-status-pill-glyph" aria-hidden="true">!</span>
                    ${escapeHtml(errMsg.slice(0, 160))}
                </span>
            `;
        }
    } catch (e) {
        state.gmail.status = "error";
        statusEl.dataset.status = "error";
        statusEl.innerHTML = `
            <span class="ob-status-pill ob-status-pill-error">
                <span class="ob-status-pill-glyph" aria-hidden="true">!</span>
                Network error: ${escapeHtml(e.message.slice(0, 120))}
            </span>
        `;
    } finally {
        idInput.disabled = false;
        secretInput.disabled = false;
        validateBtn.disabled = !(state.gmail.client_id && state.gmail.client_secret);
    }
}

function wireSave(container, state, next) {
    const saveBtn = container.querySelector("#ob-extras-save");
    saveBtn.addEventListener("click", async () => {
        if (busy) return;
        busy = true;
        saveBtn.disabled = true;
        const orig = saveBtn.innerHTML;
        saveBtn.innerHTML = "Saving&hellip;";

        const secrets = {};
        if (state.gmail.status === "ok") {
            secrets.GOOGLE_OAUTH_CLIENT_ID = state.gmail.client_id;
            secrets.GOOGLE_OAUTH_CLIENT_SECRET = state.gmail.client_secret;
        }

        try {
            // Always POST /save (server handles empty secrets gracefully)
            const r = await fetch("/onboarding/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ secrets }),
            });
            if (!r.ok) {
                throw new Error(`Save failed: ${r.status}`);
            }
            await next();
        } catch (e) {
            saveBtn.innerHTML = orig;
            saveBtn.disabled = false;
            const integrations = container.querySelector("#ob-integrations");
            const toast = document.createElement("div");
            toast.className = "ob-step-error-inline";
            toast.textContent = `Couldn't save: ${e.message}. Try again.`;
            integrations.parentNode.insertBefore(toast, integrations.nextSibling);
            setTimeout(() => toast.remove(), 5000);
        } finally {
            busy = false;
        }
    });
}

function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}
