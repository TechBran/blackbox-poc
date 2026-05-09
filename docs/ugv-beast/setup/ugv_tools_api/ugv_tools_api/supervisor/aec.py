"""WebRTC AEC3 wrapper for the supervisor's mic path.

Backend chosen at module import time:
  Path A (preferred): webrtc-audio-processing (Chrome's AEC3)
  Path A.alt: speexdsp echo canceller (fallback)

Both expose process(mic_pcm, ref_pcm) -> echo_cancelled_pcm. Frame size
must match what the backend expects (10 ms at 16 kHz = 160 samples = 320 bytes).

Backend selection at deployment time (Jetson Orin aarch64):
  - webrtc-audio-processing FAILS to compile on aarch64 because its
    fir_filter_sse.cc unconditionally includes <xmmintrin.h> (Intel SSE
    intrinsics, x86-only). Reported upstream; no clean workaround on ARM.
  - speexdsp builds and runs cleanly on aarch64 once libspeexdsp-dev +
    swig are present in the container. This is what production uses.
  - On x86 dev hosts, webrtc-audio-processing builds and gives Chrome's
    AEC3 (the original target). The wrapper transparently picks whichever
    backend imports first, so dev/prod parity is preserved at the API
    level even though the underlying algorithm differs.

Failure modes:
  - Library not installed: `_make_aec` raises; caller catches and falls back to halfduplex
  - process() raises: caller catches and falls back to halfduplex for that session
"""
from __future__ import annotations
from typing import Protocol


class _AECBackend(Protocol):
    def process(self, mic: bytes, ref: bytes) -> bytes: ...


def _make_aec(sample_rate_hz: int, frame_ms: int) -> _AECBackend:
    """Try webrtc-audio-processing first; fall back to speexdsp.

    Raises RuntimeError if neither backend is available — caller is
    expected to catch and fall back to half-duplex for the session.
    """
    try:
        from webrtc_audio_processing import AudioProcessingModule
        ap = AudioProcessingModule(aec_type=2, enable_ns=False, agc_type=0)
        ap.set_stream_format(sample_rate_hz, 1)
        ap.set_reverse_stream_format(sample_rate_hz, 1)

        class _WebRtcAec:
            def process(self, mic: bytes, ref: bytes) -> bytes:
                ap.process_reverse_stream(ref)
                return ap.process_stream(mic)
        return _WebRtcAec()
    except Exception as e:
        print(f"[aec] webrtc-audio-processing unavailable ({type(e).__name__}: {e}); trying speexdsp")

    try:
        from speexdsp import EchoCanceller
        frame_size = int(sample_rate_hz * frame_ms / 1000)
        filter_length = int(sample_rate_hz * 0.2)  # 200 ms tail
        ec = EchoCanceller.create(frame_size, filter_length, sample_rate_hz)

        class _SpeexAec:
            def process(self, mic: bytes, ref: bytes) -> bytes:
                return ec.process(mic, ref)
        return _SpeexAec()
    except Exception as e:
        raise RuntimeError(
            f"No AEC backend available (webrtc-audio-processing + speexdsp both failed): {e}"
        ) from e


class Aec3Wrapper:
    def __init__(self, sample_rate_hz: int = 16000, frame_ms: int = 10) -> None:
        self._sr = sample_rate_hz
        self._frame_ms = frame_ms
        self._backend = _make_aec(sample_rate_hz=sample_rate_hz, frame_ms=frame_ms)

    def process(self, mic_pcm: bytes, ref_pcm: bytes) -> bytes:
        """Echo-cancel one frame. mic and ref are S16_LE mono at sample_rate_hz.
        Returns the cancelled mic frame."""
        return self._backend.process(mic_pcm, ref_pcm)
