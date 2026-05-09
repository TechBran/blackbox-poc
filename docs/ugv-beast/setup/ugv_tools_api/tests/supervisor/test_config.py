import pytest

from ugv_tools_api.supervisor import config as cfg


def test_defaults(monkeypatch):
    for v in ("SUPERVISOR_MODEL", "SUPERVISOR_VOICE", "SUPERVISOR_LANG",
              "SUPERVISOR_MIC", "SUPERVISOR_SPK", "TOOLS_API_URL", "ER_URL",
              "SUPERVISOR_CAMERA_TOPIC", "SUPERVISOR_COSTMAP_TOPIC",
              "SUPERVISOR_HANDLE_STORE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    s = cfg.load()
    assert s.model == "gemini-2.5-flash-native-audio-latest"
    assert s.voice == "Orus"
    assert s.language_code == "en-US"
    # Path B (system-pulseaudio routing) — default is the ALSA `pulse` PCM
    # plugin which forwards to the host's system-mode pulseaudio. See
    # test_supervisor_mic_default_uses_pulse_after_pipewire_routing below
    # for the explicit guard against accidental reversion.
    assert s.mic_device == "pulse"
    assert s.spk_device == "pulse"
    assert s.tools_api_url == "http://localhost:8080"
    assert s.er_url == "http://localhost:8082"
    assert s.camera_topic == "/camera/image/compressed"
    assert s.costmap_topic == "/global_costmap/costmap"
    assert s.handle_store_path == "/var/lib/ugv_supervisor/session_handle.txt"
    assert s.aec_mode == "aec3"
    assert s.aec_delay_ms == 50.0
    assert s.budget_window_tokens == 32000
    assert s.budget_threshold == 0.8


def test_model_env_override_for_rollback(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("SUPERVISOR_MODEL", "gemini-3.1-flash-live-preview")
    s = cfg.load()
    assert s.model == "gemini-3.1-flash-live-preview"


def test_override_via_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("SUPERVISOR_VOICE", "Charon")
    monkeypatch.setenv("TOOLS_API_URL", "http://10.0.0.5:8080")
    s = cfg.load()
    assert s.voice == "Charon"
    assert s.tools_api_url == "http://10.0.0.5:8080"


def test_google_api_key_required(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        cfg.load()


def test_google_api_key_whitespace_rejected(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "   ")
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        cfg.load()


def test_load_includes_gemini_live_url(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    from ugv_tools_api.supervisor.config import load
    cfg = load()
    assert cfg.gemini_live_url.startswith("wss://generativelanguage.googleapis.com/")
    assert "BidiGenerateContent" in cfg.gemini_live_url


def test_load_includes_ws_keepalive_defaults(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    from ugv_tools_api.supervisor.config import load
    cfg = load()
    assert cfg.ws_ping_interval_s == 60.0
    assert cfg.ws_ping_timeout_s == 60.0


def test_load_respects_ws_env_overrides(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    monkeypatch.setenv("SUPERVISOR_WS_PING_INTERVAL_S", "30")
    monkeypatch.setenv("SUPERVISOR_WS_PING_TIMEOUT_S", "45")
    from ugv_tools_api.supervisor.config import load
    cfg = load()
    assert cfg.ws_ping_interval_s == 30.0
    assert cfg.ws_ping_timeout_s == 45.0


def test_watch_defaults_present():
    """T1 of the embodied-observer plan: watch mode is default-on at
    session creation, with FPS exposed as a module-level constant.
    These knobs let the operator tune ambient perception cost without
    touching code (env vars: WATCH_DEFAULT_ON, WATCH_FPS)."""
    from ugv_tools_api.supervisor import config as cfg_mod
    assert cfg_mod.WATCH_DEFAULT_ON is True
    assert abs(cfg_mod.WATCH_FPS - 0.33) < 1e-9


def test_watch_env_overrides(monkeypatch):
    """Operator can override defaults via env vars. Reload the module
    so the module-level constants pick up the new env values."""
    import importlib
    monkeypatch.setenv("WATCH_DEFAULT_ON", "false")
    monkeypatch.setenv("WATCH_FPS", "0.2")
    from ugv_tools_api.supervisor import config as cfg_mod
    importlib.reload(cfg_mod)
    try:
        assert cfg_mod.WATCH_DEFAULT_ON is False
        assert abs(cfg_mod.WATCH_FPS - 0.2) < 1e-9
    finally:
        # Restore module to default state for downstream tests.
        monkeypatch.delenv("WATCH_DEFAULT_ON", raising=False)
        monkeypatch.delenv("WATCH_FPS", raising=False)
        importlib.reload(cfg_mod)


def test_supervisor_mic_default_uses_pulse_after_pipewire_routing(monkeypatch):
    """After Path B (system-pulseaudio routing), the production default for
    SUPERVISOR_MIC and SUPERVISOR_SPK is `pulse` (the ALSA PCM plugin that
    routes through system pulseaudio), not a direct hw: device."""
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    monkeypatch.delenv("SUPERVISOR_MIC", raising=False)
    monkeypatch.delenv("SUPERVISOR_SPK", raising=False)
    from ugv_tools_api.supervisor.config import load
    s = load()
    assert s.mic_device == "pulse", f"mic default reverted: {s.mic_device!r}"
    assert s.spk_device == "pulse", f"spk default reverted: {s.spk_device!r}"
