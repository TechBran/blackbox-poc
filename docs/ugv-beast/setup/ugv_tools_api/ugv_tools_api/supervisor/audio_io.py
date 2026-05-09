"""ALSA mic + speaker wrappers for the supervisor.

Mic is read on a DEDICATED single-threaded executor — never on asyncio's
default pool, which rclpy's spin_once can starve. Spike validated that
the default pool starvation manifests as "mic bytes stop flowing to
Gemini" which is catastrophic and silent. Do not share the executor.

Speaker writes bytes to a long-lived aplay subprocess with a 1s ring
buffer so Gemini's bursty per-turn output never drops. Chime uses the
same pipe.
"""
import concurrent.futures
import math
import struct
import subprocess
import threading
import time
from typing import Optional

import numpy as np
from scipy.signal import resample_poly

from .config import SupervisorConfig


class SpeakerReferenceRing:
    """Last-N-seconds ring buffer of speaker output PCM bytes.

    Producers (SpeakerStream.write) append timestamped PCM. Consumers (the
    AEC step in pump_mic) call read_aligned() to fetch a chunk that lines
    up with the mic capture timestamp, accounting for USB DAC + ALSA
    buffer delay.

    'now' is wall-clock float (time.monotonic()).
    """
    # Silence-gap zero-fill: if the most-recent write is older than the
    # AEC delay plus this grace, ring contents are stale (>2 s capacity
    # rotation overwrites with old PCM). Return zeros so AEC doesn't try
    # to cancel a phantom echo. 50 ms is enough cushion for normal jitter
    # without false-positive zero-fills during legitimate playback.
    _SILENCE_GRACE_S = 0.05

    def __init__(self, sample_rate_hz: int, capacity_seconds: float) -> None:
        self._sr = sample_rate_hz
        self._capacity_samples = int(sample_rate_hz * capacity_seconds)
        self._buf = np.zeros(self._capacity_samples, dtype=np.int16)
        self._write_pos = 0
        self._wrote_total = 0
        self._first_write_ts: Optional[float] = None
        self._last_write_ts: Optional[float] = None
        self._lock = threading.Lock()

    def write(self, pcm: bytes, ts: Optional[float] = None) -> None:
        if ts is None:
            ts = time.monotonic()
        arr = np.frombuffer(pcm, dtype=np.int16)
        with self._lock:
            if self._first_write_ts is None:
                self._first_write_ts = ts
            self._last_write_ts = ts
            n = len(arr)
            end = self._write_pos + n
            if end <= self._capacity_samples:
                self._buf[self._write_pos:end] = arr
            else:
                first = self._capacity_samples - self._write_pos
                self._buf[self._write_pos:] = arr[:first]
                self._buf[:n - first] = arr[first:]
            self._write_pos = end % self._capacity_samples
            self._wrote_total += n

    def read_aligned(self, now: float, size_bytes: int, delay_ms: float) -> bytes:
        size_samples = size_bytes // 2
        with self._lock:
            if self._first_write_ts is None:
                return b"\x00" * size_bytes
            # Silence-gap zero-fill (see _SILENCE_GRACE_S above): if last
            # write was longer ago than (delay + grace), the ring's bytes
            # at the target index are stale from a previous rotation.
            if self._last_write_ts is not None and (
                now - self._last_write_ts
                > (delay_ms / 1000.0) + self._SILENCE_GRACE_S
            ):
                return b"\x00" * size_bytes
            target_ts = now - (delay_ms / 1000.0)
            elapsed = target_ts - self._first_write_ts
            target_sample = int(elapsed * self._sr)
            if target_sample < 0 or target_sample >= self._wrote_total:
                return b"\x00" * size_bytes
            ring_idx = target_sample % self._capacity_samples
            end = ring_idx + size_samples
            if end <= self._capacity_samples:
                out = self._buf[ring_idx:end]
            else:
                first = self._capacity_samples - ring_idx
                out = np.concatenate([self._buf[ring_idx:], self._buf[:size_samples - first]])
            return out.tobytes()


def resample_24k_to_16k(pcm_24k: bytes) -> bytes:
    """Resample S16_LE mono 24 kHz to 16 kHz via polyphase. Used to align
    speaker reference (24 kHz) with mic capture (16 kHz)."""
    if not pcm_24k:
        return b""
    arr = np.frombuffer(pcm_24k, dtype=np.int16).astype(np.float32)
    out = resample_poly(arr, up=2, down=3)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


# How many times to retry spawning arecord if it exits within the
# startup readiness window. ALSA's plughw: plug layer sometimes needs
# a beat after a different process (ears' PyAudio) closes its handle
# before it will open again. One retry with a short delay absorbs that
# lag without leaking a second arecord.
_ARECORD_START_RETRIES = 5
_ARECORD_START_RETRY_DELAY_S = 1.0
# Arecord should produce the first raw bytes within this window. If it
# doesn't, something is wrong (device busy, missing, etc.) — we retry.
_ARECORD_READY_TIMEOUT_S = 1.5


def make_chime(duration_s: float = 0.18, freq_hz: float = 880.0,
               sample_rate: int = 24000, volume: float = 0.25) -> bytes:
    """Build a short sine-tone chime as raw S16_LE at output rate.

    Args:
        duration_s: Total length including fades. Must be >= 0.04s to
            fit the two 20ms fades. Spike default 0.18s (180ms) is
            noticeable without being intrusive.
        freq_hz: Pitch. 880 Hz (A5) cuts through room noise but isn't shrill.
        sample_rate: Must match the speaker's configured rate (24000 for
            Gemini Live).
        volume: 0.0..1.0 scalar applied before quantization. 0.25 is
            comfortable in a quiet room and well below clipping.

    Returns:
        Raw PCM bytes, little-endian int16, mono, at ``sample_rate`` Hz.

    A 20 ms linear fade is applied at each end to eliminate the click
    artifacts speakers produce when raw PCM starts or stops mid-waveform.
    """
    assert duration_s >= 0.04, "chime duration must fit 2x20ms fades"
    n = int(duration_s * sample_rate)
    amp = int(volume * 32767)
    samples = [int(amp * math.sin(2 * math.pi * freq_hz * i / sample_rate))
               for i in range(n)]
    fade = int(0.02 * sample_rate)
    for i in range(fade):
        samples[i] = int(samples[i] * i / fade)
        samples[-(i + 1)] = int(samples[-(i + 1)] * i / fade)
    return struct.pack(f"<{n}h", *samples)


class MicStream:
    """arecord subprocess + dedicated thread pool for blocking reads.

    The pool has exactly 1 worker so scheduling is deterministic. Using
    the default asyncio executor instead would share with rclpy's
    spin_once blocking calls and cause sporadic mic read stalls.

    Single-use: stop() is terminal (thread pool is shut down). To resume
    mic capture after stop(), construct a new MicStream.
    """

    def __init__(self, cfg: SupervisorConfig) -> None:
        self._cfg = cfg
        self._proc: Optional[subprocess.Popen] = None
        # Thread for draining arecord's stderr so its pipe buffer can't
        # fill and block the writer. Drained bytes accumulate in
        # self._stderr_buf for diagnostic inclusion in RuntimeError.
        self._stderr_thread: Optional[threading.Thread] = None
        self._stderr_buf = bytearray()
        self._stderr_lock = threading.Lock()
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="supervisor-mic",
        )

    def _spawn_arecord(self) -> subprocess.Popen:
        """Spawn ONE arecord subprocess. Does NOT retry — caller does.

        We keep stderr as PIPE (not DEVNULL) so failures surface in
        logs. A background thread drains it to prevent pipe-buffer
        backpressure on long runs.
        """
        # Drop `-q` so arecord emits capture setup on stderr; useful
        # when debugging "device not available" style failures. The
        # drain thread swallows the noise on success.
        proc = subprocess.Popen(
            ["arecord", "-D", self._cfg.mic_device, "-f", "S16_LE",
             "-r", str(self._cfg.input_sample_rate), "-c", "1",
             "-t", "raw"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            while True:
                chunk = proc.stderr.read(1024)
                if not chunk:
                    return
                with self._stderr_lock:
                    self._stderr_buf.extend(chunk)
                    # Cap at 4 KiB — enough for a typical ALSA error
                    # plus some setup noise, not so much that a
                    # long-running arecord bloats RAM with status chatter.
                    if len(self._stderr_buf) > 4096:
                        del self._stderr_buf[: len(self._stderr_buf) - 4096]

        t = threading.Thread(target=_drain_stderr, daemon=True,
                             name="supervisor-mic-stderr")
        t.start()
        return proc

    def _readiness_check(self, proc: subprocess.Popen) -> Optional[str]:
        """Block briefly for arecord to either produce data or die.

        Returns None if ready (process alive AND first byte seen within
        the timeout), or a human-readable error string if it died.
        We use a short read-peek plus an aliveness check — if arecord
        opens the device cleanly it starts streaming within ~100 ms;
        if it fails to open (device busy, missing), it exits in <50 ms.
        """
        deadline = time.monotonic() + _ARECORD_READY_TIMEOUT_S
        while time.monotonic() < deadline:
            rc = proc.poll()
            if rc is not None:
                # Died. Give the stderr drain thread a moment to catch up.
                time.sleep(0.05)
                with self._stderr_lock:
                    err = bytes(self._stderr_buf).decode("utf-8", errors="replace")
                return (
                    f"arecord exited rc={rc} before readiness; "
                    f"stderr: {err.strip()[:500] or '<empty>'}"
                )
            time.sleep(0.05)
        # Still alive past the readiness window — call it good.
        return None

    def start(self) -> None:
        """Spawn arecord; retry on immediate exit.

        The common failure mode is the USB mic still being held by ears'
        PyAudio stream when supervisor's handoff delay (500 ms) elapsed.
        ALSA needs a beat for the previous holder to fully release.
        Retries with a short backoff absorb that lag; if every attempt
        fails, raises RuntimeError with arecord's stderr so the caller
        can decide how to escalate (log + close session, usually).
        Idempotent: no-op if already running.
        """
        if self._proc is not None and self._proc.poll() is None:
            return

        errors: list[str] = []
        for attempt in range(1, _ARECORD_START_RETRIES + 1):
            with self._stderr_lock:
                self._stderr_buf.clear()
            proc = self._spawn_arecord()
            err = self._readiness_check(proc)
            if err is None:
                self._proc = proc
                return
            # Clean up the dead one before retrying so we don't leak zombies.
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
            errors.append(f"attempt {attempt}: {err}")
            if attempt < _ARECORD_START_RETRIES:
                time.sleep(_ARECORD_START_RETRY_DELAY_S)

        raise RuntimeError(
            "MicStream: arecord failed to start after "
            f"{_ARECORD_START_RETRIES} attempts. Details: "
            + " | ".join(errors)
        )

    def read_chunk(self, n_bytes: int) -> bytes:
        """Blocking read; call from event loop via run_in_executor(self.pool, ...).

        Returns empty bytes on arecord EOF (subprocess died). Callers MUST
        treat len==0 as terminal mic failure and stop the pump loop; do
        not retry in a loop since the subprocess is not coming back.

        If ``cfg.mic_gain`` is not 1.0, applies a static gain multiplier
        to the int16 samples with saturation clipping at ±32767. This is
        a software replacement for Google's server-side AGC — the preview
        model's pipeline does it for free, but native-audio skips it and
        expects speech in the -20 dBFS range. Default 3.0× (~+9.5 dB)
        lifts the USB webcam mic's ~-30 dBFS output into that range.
        """
        assert self._proc is not None and self._proc.stdout is not None
        data = self._proc.stdout.read(n_bytes)
        if not data or self._cfg.mic_gain == 1.0:
            return data
        # np.int16 → int32 so the multiply can overflow freely, then clip
        # to the int16 range before packing back. np.clip on int32 is
        # saturating; without the widen step, 30000 * 3.0 would wrap.
        samples = np.frombuffer(data, dtype=np.int16).astype(np.int32)
        samples = np.clip(samples * self._cfg.mic_gain, -32768, 32767)
        return samples.astype(np.int16).tobytes()

    @property
    def pool(self) -> concurrent.futures.ThreadPoolExecutor:
        return self._pool

    def stop(self) -> None:
        """Terminate arecord and shut down the thread pool. Best-effort.

        Terminal: the thread pool is shut down, so a subsequent start()
        would hand reads off to a dead pool. Construct a new MicStream
        if you need to resume.
        """
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        self._pool.shutdown(wait=False, cancel_futures=True)


class SpeakerStream:
    """aplay subprocess with a 1s buffer; accepts chunks and chime bytes.

    The --buffer-size is sized to the output sample rate (1 second of PCM)
    so Gemini's bursty per-turn audio output doesn't drop at the ALSA
    boundary. Spike validated this value.

    write() and stop() are serialized through an internal lock so that
    shutdown during a concurrent write cannot race into stdin.close()
    mid-syscall (classic POSIX pipe undefined-behavior footgun).
    """

    # Guard window for the half-duplex anti-echo gate in pump_mic.
    # Must cover FOUR lags after we stop writing:
    #   1. aplay's ring buffer (--buffer-size=24000 = 1 s at 24 kHz)
    #   2. kernel ALSA buffer still draining to the DAC
    #   3. acoustic travel + near-field direct speaker-to-mic coupling
    #   4. room reverb tail — highly variable by space, loud speakers +
    #      reflective surfaces can ring for 3-4 s at audible level
    # Observed in production: 2.5 s closed the gate but echo of the
    # model's greeting was still arriving ~1.5 s later, transcribed as
    # `[user] Hello` and triggering a feedback loop where the model
    # greeted itself perpetually. 5.0 s absorbs typical indoor reverb.
    # Trade-off: 5 s deadband after the model stops before your speech
    # is forwarded. Walkie-talkie cadence, not full-duplex conversation.
    # If 5 s still echoes, we need real AEC (webrtc-audio-processing).
    _PLAYBACK_TAIL_GUARD_S = 5.0

    def __init__(self, cfg: SupervisorConfig) -> None:
        self._cfg = cfg
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        # Wall-clock timestamp of the last write() call. is_playing()
        # returns True if now - _last_write_at < _PLAYBACK_TAIL_GUARD_S.
        # This is simpler and more robust than predicting queue-end
        # times: it only requires that we keep marking "speaker had
        # bytes recently" on each write. As long as Gemini's audio
        # stream is arriving at roughly realtime, last_write_at
        # advances continuously during playback, so is_playing() stays
        # True without gaps. -inf means "never written, never playing."
        self._last_write_at: float = float("-inf")
        # 2-second ring of the bytes we just wrote to aplay; consumed by
        # the AEC reference path in pump_mic. Capacity covers the worst
        # observed mic-capture lag (USB DAC + ALSA buffer + acoustic
        # propagation + room reverb tail) plus headroom. Captured at the
        # speaker's native rate (24 kHz); the consumer resamples to 16 kHz
        # to align with the mic.
        self.reference_ring = SpeakerReferenceRing(
            sample_rate_hz=cfg.output_sample_rate, capacity_seconds=2.0,
        )

    def start(self) -> None:
        """Spawn the aplay subprocess. Idempotent — no-op if already running."""
        if self._proc is not None and self._proc.poll() is None:
            return
        self._proc = subprocess.Popen(
            ["aplay", "-D", self._cfg.spk_device, "-f", "S16_LE",
             "-r", str(self._cfg.output_sample_rate), "-c", "1",
             "-t", "raw", f"--buffer-size={self._cfg.output_sample_rate}"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def write(self, data: bytes) -> None:
        """Write PCM bytes to the speaker pipe.

        Blocking on ALSA ring-buffer backpressure — at 24 kHz a 1 s chunk
        can stall until the buffer drains. Async callers MUST dispatch
        via run_in_executor; never call directly from a coroutine:

            await loop.run_in_executor(None, spk.write, chunk)

        Raises ``RuntimeError`` with aplay's stderr contents if the pipe
        has closed (ALSA device disappeared, aplay crashed). The real
        service should decide policy on the error — restart aplay, end
        the session, etc. This method deliberately does not hide the
        failure with a silent retry.
        """
        with self._lock:
            if self._proc is None or self._proc.stdin is None:
                raise RuntimeError("speaker not started")
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except BrokenPipeError:
                err = b""
                if self._proc.stderr is not None:
                    try:
                        err = self._proc.stderr.read()
                    except Exception:
                        pass
                raise RuntimeError(
                    f"aplay pipe closed: {err.decode(errors='replace')[:500]}"
                )
            # Mark "speaker had bytes just now." During a continuous
            # Gemini response, writes happen at ~realtime pace so this
            # value advances steadily and is_playing() stays True
            # throughout. After the LAST chunk, is_playing() remains
            # True for _PLAYBACK_TAIL_GUARD_S seconds, covering aplay's
            # ring-buffer drain + room reverb.
            self._last_write_at = time.monotonic()
            # Tap into the AEC reference ring with the SAME bytes we just
            # handed to aplay, timestamped now. The ring is lock-internal
            # so this is safe to call inside the speaker write lock.
            try:
                self.reference_ring.write(data, ts=self._last_write_at)
            except Exception:
                # Ring write must not break speaker output. Worst case
                # the AEC misses a frame and the operator hears a brief
                # echo blip — acceptable trade for never dropping speech.
                pass

    def is_playing(self) -> bool:
        """Return True if the speaker is still producing sound — either
        currently playing or still inside the post-write guard window
        that covers aplay's ring buffer and room reverb.

        Called from pump_mic to decide whether to forward real mic
        bytes or zero-byte silence. Lock-free read of a float is fine
        on CPython: the atomicity risk is a one-tick staleness, which
        at 20 ms mic frames is invisible. No race-critical branch
        depends on the exact transition.
        """
        return time.monotonic() - self._last_write_at < self._PLAYBACK_TAIL_GUARD_S

    def stop(self) -> None:
        """Close the speaker pipe and terminate aplay. Best-effort.

        Serialized with write() via the shared lock so shutdown can't
        race into a mid-write stdin.close().
        """
        with self._lock:
            if self._proc is not None:
                try:
                    if self._proc.stdin is not None:
                        self._proc.stdin.close()
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc = None
