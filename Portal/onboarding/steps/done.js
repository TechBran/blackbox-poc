// Done step — final screen of the onboarding wizard.
// Renders a summary of what was configured/skipped, then a single CTA
// that POSTs /onboarding/complete (writes the sentinel that lets
// FirstRunMiddleware stop redirecting /ui) and navigates to Portal.
//
// Visual: editorial gravitas. Sigil "07" + "DONE" label. Big Fraunces
// title "All set." with italic-red on a key word. Summary list with
// status pips. Big "Open Portal →" CTA. The customer has crossed a
// threshold; the system is now theirs.

let busy = false;

export async function render(container, { next, back, skip }) {
    container.innerHTML = `
        <section class="ob-step ob-done">
            <aside class="ob-step-sigil" aria-hidden="true">
                <div class="ob-step-sigil-num"><em>07</em></div>
                <div class="ob-step-sigil-rule"></div>
                <div class="ob-step-sigil-label">DONE</div>
            </aside>
            <div class="ob-step-body">
                <div class="ob-step-eyebrow">
                    <span class="ob-step-eyebrow-dot" aria-hidden="true"></span>
                    Setup complete
                </div>
                <h1 class="ob-step-title">
                    Your <em>BlackBox</em> is ready.
                </h1>
                <p class="ob-step-lede">
                    Here's what you configured. You can change any of this from
                    the System Menu later.
                </p>
                <div id="ob-done-summary" class="ob-done-summary">
                    <div class="ob-loading">Building summary&hellip;</div>
                </div>
                <div class="ob-cta-row">
                    <button type="button" class="ob-cta ob-cta-large" id="ob-done-open" disabled>
                        Open Portal <span class="ob-cta-arrow" aria-hidden="true">&rarr;</span>
                    </button>
                </div>
                <nav class="ob-step-nav" aria-label="Step navigation">
                    <button type="button" class="ob-back" id="ob-done-back">
                        <span aria-hidden="true">&larr;</span> Back to operator setup
                    </button>
                </nav>
            </div>
        </section>
    `;

    document.getElementById("ob-done-back").addEventListener("click", back);

    // Fetch summary data + render
    const summary = await loadSummary();
    renderSummary(container, summary);

    // Wire the Open Portal CTA
    const openBtn = document.getElementById("ob-done-open");
    openBtn.disabled = false;  // enable once summary loaded
    openBtn.addEventListener("click", () => completeAndOpen(openBtn));
}

async function loadSummary() {
    const data = { config: null, state: null, error: null };
    try {
        const [configR, stateR] = await Promise.all([
            fetch("/onboarding/current-config"),
            fetch("/onboarding/state"),
        ]);
        if (configR.ok) data.config = await configR.json();
        if (stateR.ok) data.state = await stateR.json();
    } catch (e) {
        data.error = e.message;
    }
    return data;
}

function renderSummary(container, { config, state, error }) {
    const summaryEl = container.querySelector("#ob-done-summary");
    if (error || !config || !state) {
        summaryEl.innerHTML = `
            <p class="ob-step-helper">
                Couldn't load the summary (${escapeHtml(error || "unknown error")}).
                Setup is still complete &mdash; clicking Open Portal works.
            </p>
        `;
        return;
    }

    const skipped = new Set(state.skipped_steps || []);
    const rows = [];

    // Tailscale row
    if (skipped.has("tailscale")) {
        rows.push(summaryRow("Tailscale", "skip", "LAN-only mode"));
    } else if (config.tailscale && config.tailscale.configured) {
        const host = (config.tailscale.detail && config.tailscale.detail.hostname) || "configured";
        rows.push(summaryRow("Tailscale", "ok", host));
    } else {
        rows.push(summaryRow("Tailscale", "warn", "not configured"));
    }

    // API keys row — count present LLM providers
    const llmKeys = ["openai", "anthropic", "google"];
    const presentLLM = llmKeys.filter(k => config.providers?.[k]?.present);
    if (skipped.has("api_keys") && presentLLM.length === 0) {
        rows.push(summaryRow("AI providers", "skip", "no keys yet"));
    } else if (presentLLM.length > 0) {
        const labels = presentLLM.map(k => k.charAt(0).toUpperCase() + k.slice(1)).join(", ");
        rows.push(summaryRow("AI providers", "ok", `${presentLLM.length} configured · ${labels}`));
    } else {
        rows.push(summaryRow("AI providers", "warn", "no keys configured"));
    }

    // Optional integrations (Gmail) row
    if (skipped.has("optional_integrations")) {
        rows.push(summaryRow("Optional integrations", "skip", "configure later"));
    } else if (config.providers?.gmail?.present) {
        rows.push(summaryRow("Gmail", "ok", "OAuth client configured"));
    } else {
        rows.push(summaryRow("Optional integrations", "skip", "none configured"));
    }

    // Phone pairing row
    const pairedCount = (config.paired_devices || []).length;
    if (skipped.has("pair_phone")) {
        rows.push(summaryRow("Phone pairing", "skip", "pair later from System Menu"));
    } else if (pairedCount > 0) {
        const names = config.paired_devices.map(d => d.hostname || d.device_kind || "device").join(", ");
        rows.push(summaryRow("Phone pairing", "ok", `${pairedCount} paired · ${names}`));
    } else {
        rows.push(summaryRow("Phone pairing", "skip", "no devices paired"));
    }

    // Operators row
    const operators = config.operators || [];
    if (operators.length > 0) {
        rows.push(summaryRow("Operators", "ok", `${operators.length} · ${operators.join(", ")}`));
    } else {
        rows.push(summaryRow("Operators", "warn", "none registered"));
    }

    summaryEl.innerHTML = `
        <ul class="ob-summary-list">
            ${rows.join("")}
        </ul>
    `;
}

function summaryRow(label, status, detail) {
    const glyph = status === "ok" ? "&check;" : (status === "skip" ? "&#8856;" : "!");
    return `
        <li class="ob-summary-row ob-summary-${status}">
            <span class="ob-summary-glyph" aria-hidden="true">${glyph}</span>
            <span class="ob-summary-label">${escapeHtml(label)}</span>
            <span class="ob-summary-detail">${escapeHtml(detail)}</span>
        </li>
    `;
}

async function completeAndOpen(btn) {
    if (busy) return;
    busy = true;
    btn.disabled = true;
    const orig = btn.innerHTML;
    btn.innerHTML = "Finalizing&hellip;";

    try {
        // POST /complete writes the sentinel — FirstRunMiddleware respects it
        const r = await fetch("/onboarding/complete", { method: "POST" });
        if (!r.ok) {
            throw new Error(`/onboarding/complete returned ${r.status}`);
        }
        // Brief moment for the sentinel write to settle
        await new Promise(resolve => setTimeout(resolve, 250));
        // Navigate to Portal — FirstRunMiddleware now sees the sentinel and lets us through
        location.href = "/ui";
    } catch (e) {
        btn.innerHTML = orig;
        btn.disabled = false;
        const summaryEl = document.getElementById("ob-done-summary");
        if (summaryEl) {
            const err = document.createElement("p");
            err.className = "ob-step-helper ob-summary-error";
            err.textContent = `Couldn't finalize setup: ${e.message}. Try again.`;
            summaryEl.appendChild(err);
        }
    } finally {
        busy = false;
    }
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
