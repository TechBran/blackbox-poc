"""Env-resolved configuration for the supervisor service.

Fails fast at import/load time if GOOGLE_API_KEY is missing so systemd
surfaces the error instead of silently failing on first tool call.
"""
import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Watch mode (ambient pantilt RGB push to Gemini Live).
# Default-on at session creation so Gemini Live is never blind outside
# missions. Operator can flip via env (WATCH_DEFAULT_ON=false) or at
# runtime via set_watch_mode(on=...). FPS is the steady-state cadence;
# 0.33 = one frame every ~3 s, well within Google's 1 FPS ceiling and
# cheap enough to leave on for the full session lifetime. set_watch_mode
# can re-tune it on the fly via the optional fps= param (clamped to
# [0.1, 1.0]). See docs/plans/2026-04-29-gemini-live-embodied-observer.md.
WATCH_DEFAULT_ON: bool = _env_bool("WATCH_DEFAULT_ON", True)
WATCH_FPS: float = float(os.environ.get("WATCH_FPS", "0.33"))


@dataclass(frozen=True)
class SupervisorConfig:
    google_api_key: str
    model: str
    voice: str
    language_code: str
    mic_device: str
    spk_device: str
    # TOOLS_API_URL and ER_URL point at sibling Jetson services
    # (ugv-tools-api.service on :8080 and ugv-er.service on :8082). They
    # are intentionally NOT prefixed with SUPERVISOR_ because those
    # endpoints are shared infrastructure, not supervisor-owned.
    tools_api_url: str
    er_url: str
    camera_topic: str
    costmap_topic: str
    handle_store_path: str
    # Gemini Live protocol: 16 kHz PCM input, 24 kHz PCM output, 20 ms frames.
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    chunk_ms: int = 20
    # Software gain applied to mic int16 samples before sending to Gemini.
    # Native-audio models skip Google's server-side AGC, so low-output
    # USB webcam mics produce a signal too quiet for their end-to-end
    # encoder. A multiplier of ~3.0 (+9.5 dB) lifts typical -30 dBFS
    # room speech into the -20 dBFS range the model was trained on.
    # 1.0 disables the boost (identity). Clipping at ±32767 protects
    # against int16 wrap-around on loud peaks.
    mic_gain: float = 3.0
    # AEC mode for the supervisor mic path. "aec3" routes mic frames
    # through Aec3Wrapper (WebRTC AEC3 / speexdsp depending on backend
    # availability) so the operator can barge-in mid-model-utterance.
    # "halfduplex" preserves the legacy silent_chunk gate as a safety
    # rollback if AEC misbehaves. The session.pump_mic falls back to
    # halfduplex automatically if Aec3Wrapper.__init__ or .process()
    # raises, so this only needs to be flipped manually for hard rollback.
    aec_mode: str = "aec3"
    # Reference-to-mic alignment delay in milliseconds. Accounts for
    # USB DAC + ALSA buffer + speaker → mic acoustic travel. 50 ms is a
    # conservative starting point that empirically aligns the ring
    # within the AEC's adaptive search window.
    aec_delay_ms: float = 50.0
    # Proactive session rotation: approximate context-window size in
    # tokens (window_tokens) and the fraction of that budget at which
    # the supervisor closes the current session so the inner reconnect
    # loop opens a fresh one (carrying conversation continuity via the
    # persisted session_resumption handle). See budget.py for the rate
    # constants used to estimate usage from audio seconds + JPEG frames.
    # Defaults are conservative — over-rotating is cheap, under-rotating
    # results in hard GoAway mid-sentence or context starvation.
    budget_window_tokens: int = 32000
    budget_threshold: float = 0.8
    # Raw-WebSocket transport for Gemini Live (replaces google.genai SDK).
    # URL matches the Orchestrator's bridge (gemini_live_routes.py line 351).
    gemini_live_url: str = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    )
    # WebSocket keepalive — empirically tuned for long-lived Live sessions
    # where Gemini's audio replies create natural >20s gaps in client-bound
    # traffic. The default 20s/20s settings drop sessions on every long
    # response. Carried forward from regression guard #12.
    ws_ping_interval_s: float = 60.0
    ws_ping_timeout_s: float = 60.0


def load() -> SupervisorConfig:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GOOGLE_API_KEY is required but not set. "
            "Check the systemd EnvironmentFile "
            "(typically /home/jetson/ugv_ws_waveshare/ugv_tools_api/deploy/supervisor.env)."
        )
    return SupervisorConfig(
        google_api_key=key,
        model=os.environ.get("SUPERVISOR_MODEL", "gemini-2.5-flash-native-audio-latest"),
        voice=os.environ.get("SUPERVISOR_VOICE", "Orus"),
        language_code=os.environ.get("SUPERVISOR_LANG", "en-US"),
        # Path B (system-pulseaudio routing): production default is the ALSA
        # `pulse` PCM plugin, which forwards to the host's system-mode
        # pulseaudio daemon (bind-mounted at /run/pulse/native). The legacy
        # `plughw:CARD=*,DEV=0` defaults grabbed the EMEET hardware directly,
        # which collided with system-pulse's exclusive ownership.
        mic_device=os.environ.get("SUPERVISOR_MIC", "pulse"),
        spk_device=os.environ.get("SUPERVISOR_SPK", "pulse"),
        tools_api_url=os.environ.get("TOOLS_API_URL", "http://localhost:8080"),
        er_url=os.environ.get("ER_URL", "http://localhost:8082"),
        camera_topic=os.environ.get("SUPERVISOR_CAMERA_TOPIC", "/camera/image/compressed"),
        costmap_topic=os.environ.get("SUPERVISOR_COSTMAP_TOPIC", "/global_costmap/costmap"),
        handle_store_path=os.environ.get("SUPERVISOR_HANDLE_STORE", "/var/lib/ugv_supervisor/session_handle.txt"),
        mic_gain=float(os.environ.get("SUPERVISOR_MIC_GAIN", "3.0")),
        aec_mode=os.environ.get("SUPERVISOR_AEC_MODE", "aec3"),
        aec_delay_ms=float(os.environ.get("SUPERVISOR_AEC_DELAY_MS", "50")),
        budget_window_tokens=int(os.environ.get("SUPERVISOR_BUDGET_WINDOW_TOKENS", "32000")),
        budget_threshold=float(os.environ.get("SUPERVISOR_BUDGET_THRESHOLD", "0.8")),
        ws_ping_interval_s=float(os.environ.get("SUPERVISOR_WS_PING_INTERVAL_S", "60")),
        ws_ping_timeout_s=float(os.environ.get("SUPERVISOR_WS_PING_TIMEOUT_S", "60")),
    )
