"""AEC3 wrapper synthetic test."""
import numpy as np
import pytest

from ugv_tools_api.supervisor.aec import Aec3Wrapper


def _sine(freq_hz: float, dur_s: float, sr: int = 16000) -> np.ndarray:
    t = np.arange(int(dur_s * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * freq_hz * t) * 32767).astype(np.int16)


def test_residual_at_least_12db_below_input():
    """AEC should suppress echo by >= 12 dB on a synthetic delayed echo."""
    speaker = _sine(440.0, 1.0)
    delay_samples = int(0.05 * 16000)
    mic = np.concatenate([np.zeros(delay_samples, dtype=np.int16), speaker[:-delay_samples]])

    aec = Aec3Wrapper(sample_rate_hz=16000, frame_ms=10)
    out = []
    frame = int(0.01 * 16000)
    for i in range(0, len(mic) - frame, frame):
        ref_chunk = speaker[i:i+frame].tobytes()
        mic_chunk = mic[i:i+frame].tobytes()
        out.append(aec.process(mic_chunk, ref_chunk))
    out_arr = np.frombuffer(b"".join(out), dtype=np.int16).astype(np.float32)
    mic_arr = mic.astype(np.float32)[:len(out_arr)]
    skip = int(0.2 * 16000)  # AEC convergence time
    in_rms = np.sqrt(np.mean(mic_arr[skip:]**2))
    out_rms = np.sqrt(np.mean(out_arr[skip:]**2))
    db = 20.0 * np.log10(max(out_rms, 1e-6) / max(in_rms, 1e-6))
    assert db <= -12.0, f"residual only {db:.1f} dB below input (need <= -12 dB)"


def test_passes_through_when_no_reference():
    aec = Aec3Wrapper(sample_rate_hz=16000, frame_ms=10)
    speech = (np.random.randn(160) * 5000).astype(np.int16).tobytes()
    silence = b"\x00" * 320
    out = aec.process(speech, silence)
    arr = np.frombuffer(out, dtype=np.int16)
    assert np.std(arr) > 100


def test_fallback_on_init_failure(monkeypatch):
    """If both AEC backends fail to load, Aec3Wrapper.__init__ must
    propagate a RuntimeError so session.py can catch it once and fall
    back to half-duplex. Locks the contract that pump_mic depends on."""
    monkeypatch.setattr(
        "ugv_tools_api.supervisor.aec._make_aec",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("synthetic backend failure")),
    )
    with pytest.raises(RuntimeError):
        Aec3Wrapper(sample_rate_hz=16000, frame_ms=10)
