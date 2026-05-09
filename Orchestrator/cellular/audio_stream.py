#!/usr/bin/env python3
"""
audio_stream.py - Serial Audio Bridge for SIM7600G-H

Bidirectional PCM16 audio between the SIM7600G-H modem and PhoneAIBridge.
Uses a DEDICATED thread (not executor) with interleaved read/write loop.

CONFIRMED WORKING configuration (standalone audio_test.py):
  - CPCMFRM=0 (8kHz, default after reboot) — 16kHz writes are BROKEN on V2.0.2
  - Read: 8kHz PCM16 mono (modem→host)
  - Write: 8kHz PCM16 mono (host→modem), ~14KB/s throughput
  - Single serial port, timeout=0, write_timeout=0 (non-blocking)
  - 100-byte chunks, interleaved reads and writes on SAME thread
  - ModemManager MUST be disabled
  NOTE: 16kHz writes drain at 40 B/s on ttyUSB4 — completely broken.
"""

import asyncio
import queue
import threading
import time
import traceback
from typing import Optional, Callable, Awaitable

import numpy as np
from scipy.signal import resample

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

from Orchestrator.phone.audio_converter import AudioConverter

# Audio rates — CPCMFRM default is 0 (8kHz) after modem reset.
# 16kHz writes are broken on SIM7600G-H V2.0.2 (USB buffer drains at 40 B/s).
# 8kHz writes work at ~14KB/s (confirmed by standalone audio_test.py).
# Read rate may be 8kHz or 16kHz depending on CPCMFRM — we handle both.
MODEM_READ_RATE = 8000
MODEM_WRITE_RATE = 8000
PCM_SAMPLE_WIDTH = 2
PCM_FRAME_MS = 20

READ_FRAME_BYTES = (MODEM_READ_RATE * PCM_FRAME_MS // 1000) * PCM_SAMPLE_WIDTH   # 640
WRITE_FRAME_BYTES = (MODEM_WRITE_RATE * PCM_FRAME_MS // 1000) * PCM_SAMPLE_WIDTH  # 640
WRITE_BYTES_PER_SEC = MODEM_WRITE_RATE * PCM_SAMPLE_WIDTH  # 32000

# Legacy compat
PCM_FRAME_BYTES = READ_FRAME_BYTES
BYTES_PER_SEC = WRITE_BYTES_PER_SEC
MODEM_SAMPLE_RATE = MODEM_READ_RATE
PCM_SAMPLE_RATE = MODEM_READ_RATE

IO_CHUNK = 100  # Elementzon chunk size

_active_stream: Optional["CellularAudioStream"] = None


def get_active_stream() -> Optional["CellularAudioStream"]:
    return _active_stream


class CellularAudioStream:
    """
    Single-threaded bidirectional audio on ttyUSB4.

    Uses a DEDICATED thread (not asyncio executor) for the I/O loop.
    Write data is passed via thread-safe queue.Queue.
    Read frames are dispatched to async callbacks via the event loop.
    """

    def __init__(self, audio_port: str = "/dev/ttyUSB4"):
        self.audio_port = audio_port
        self._serial: Optional[serial.Serial] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Thread-safe write queue
        self._write_queue: queue.Queue = queue.Queue()

        # Write buffer (instance-level so it can be flushed from outside the I/O thread)
        self._write_buffer = bytearray()

        # Flush signal for barge-in
        self._flush_event = threading.Event()

        # Stats
        self._frames_read = 0
        self._frames_written = 0
        self._bytes_read = 0
        self._bytes_written = 0
        self._start_time = 0.0
        self._write_errors = 0

        # Callbacks
        self.on_audio: Optional[Callable[[bytes], Awaitable[None]]] = None
        self.on_pcm16: Optional[Callable[[bytes], Awaitable[None]]] = None

    async def start(self) -> bool:
        """Open serial port and start dedicated I/O thread."""
        global _active_stream

        if not SERIAL_AVAILABLE:
            print("[CELLULAR-AUDIO] pyserial not available")
            return False

        if _active_stream and _active_stream is not self and _active_stream._running:
            print("[CELLULAR-AUDIO] Stopping ghost audio stream")
            await _active_stream.stop()

        try:
            # Match standalone audio_test.py pattern exactly:
            # Properties set individually, then open()
            self._serial = serial.Serial()
            self._serial.port = self.audio_port
            self._serial.baudrate = 115200
            self._serial.timeout = 0        # Non-blocking reads
            self._serial.write_timeout = 0  # Non-blocking writes (avoid deadlock)
            self._serial.bytesize = serial.EIGHTBITS
            self._serial.parity = serial.PARITY_NONE
            self._serial.stopbits = serial.STOPBITS_ONE
            self._serial.xonxoff = False
            self._serial.rtscts = False
            self._serial.open()
            self._serial.flushInput()
            self._serial.flushOutput()

            self._loop = asyncio.get_running_loop()
            self._running = True
            self._start_time = time.monotonic()

            # Dedicated thread — NOT an executor thread
            self._thread = threading.Thread(
                target=self._io_loop,
                name="cellular-audio-io",
                daemon=True,
            )
            self._thread.start()

            _active_stream = self
            print(f"[CELLULAR-AUDIO] Stream started on {self.audio_port} "
                  f"(dedicated thread, read={MODEM_READ_RATE//1000}kHz, "
                  f"write={MODEM_WRITE_RATE//1000}kHz)")
            return True

        except Exception as e:
            print(f"[CELLULAR-AUDIO] Start error: {e}")
            traceback.print_exc()
            return False

    async def stop(self):
        """Stop I/O thread and close serial port."""
        global _active_stream

        self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

        if self._serial and self._serial.is_open:
            self._serial.close()

        if _active_stream is self:
            _active_stream = None

        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        print(
            f"[CELLULAR-AUDIO] Stream stopped. "
            f"Read: {self._frames_read} frames ({self._bytes_read} bytes), "
            f"Written: {self._bytes_written} bytes, "
            f"Errors: {self._write_errors}, Duration: {elapsed:.1f}s"
        )

    def flush_audio(self):
        """Clear all pending write audio (for barge-in). Thread-safe."""
        self._flush_event.set()
        # Also drain the queue immediately from the caller's side
        while not self._write_queue.empty():
            try:
                self._write_queue.get_nowait()
            except queue.Empty:
                break

    def _io_loop(self):
        """Dedicated thread: interleaved read/write on single serial port.
        Matches the standalone audio_test.py pattern exactly."""
        print("[CELLULAR-AUDIO] I/O thread started")
        s = self._serial
        read_buffer = bytearray()
        write_buffer = self._write_buffer  # Instance-level — clearable via flush_audio()
        last_stats_time = time.monotonic()
        write_started = False

        while self._running and s and s.is_open:
            try:
                # === FLUSH CHECK (barge-in) ===
                if self._flush_event.is_set():
                    flushed = len(write_buffer)
                    write_buffer.clear()
                    # Drain anything left in the queue
                    while not self._write_queue.empty():
                        try:
                            self._write_queue.get_nowait()
                        except queue.Empty:
                            break
                    self._flush_event.clear()
                    if flushed > 0:
                        print(f"[CELLULAR-AUDIO] Flushed {flushed} bytes (barge-in)")

                # === READ ===
                try:
                    data = s.read(IO_CHUNK)
                except (serial.SerialException, OSError) as e:
                    if self._running:
                        print(f"[CELLULAR-AUDIO] Serial read error (USB disconnected?): {e}")
                    self._running = False
                    break
                if data:
                    read_buffer.extend(data)
                    self._bytes_read += len(data)

                # === WRITE ===
                # Drain queue into write buffer
                while not self._write_queue.empty():
                    try:
                        new_data = self._write_queue.get_nowait()
                        write_buffer.extend(new_data)
                        if not write_started:
                            write_started = True
                            print(f"[CELLULAR-AUDIO] First write queued: "
                                  f"{len(write_buffer)} bytes")
                    except queue.Empty:
                        break

                if write_buffer:
                    chunk = bytes(write_buffer[:IO_CHUNK])
                    try:
                        s.write(chunk)
                        self._bytes_written += len(chunk)
                        del write_buffer[:len(chunk)]
                        self._frames_written += 1
                    except serial.SerialTimeoutException:
                        time.sleep(0.001)
                    except (serial.SerialException, OSError) as e:
                        self._write_errors += 1
                        if self._write_errors <= 5:
                            print(f"[CELLULAR-AUDIO] Write error: {e}")

                # === DISPATCH READ FRAMES ===
                while len(read_buffer) >= READ_FRAME_BYTES:
                    frame = bytes(read_buffer[:READ_FRAME_BYTES])
                    del read_buffer[:READ_FRAME_BYTES]
                    self._frames_read += 1

                    if self.on_pcm16 and self._loop:
                        asyncio.run_coroutine_threadsafe(
                            self._safe_callback(self.on_pcm16, frame), self._loop
                        )
                    elif self.on_audio and self._loop:
                        ulaw = AudioConverter.pcm16_to_ulaw_bytes(frame)
                        asyncio.run_coroutine_threadsafe(
                            self._safe_callback(self.on_audio, ulaw), self._loop
                        )

                # Periodic stats (every 2 seconds)
                now = time.monotonic()
                if now - last_stats_time >= 2.0:
                    elapsed = now - self._start_time
                    qsize = self._write_queue.qsize()
                    wbuf = len(write_buffer)
                    print(f"[CELLULAR-AUDIO] STATS {elapsed:.0f}s: "
                          f"read={self._bytes_read}B "
                          f"written={self._bytes_written}B "
                          f"wbuf={wbuf}B queue={qsize} "
                          f"errors={self._write_errors}")
                    last_stats_time = now

            except Exception as e:
                if self._running:
                    self._write_errors += 1
                    if self._write_errors <= 5:
                        print(f"[CELLULAR-AUDIO] I/O error: {e}")
                        traceback.print_exc()
                    time.sleep(0.01)

        print(f"[CELLULAR-AUDIO] I/O thread stopped "
              f"(read={self._bytes_read}B, written={self._bytes_written}B, "
              f"wbuf_remaining={len(write_buffer)}B)")

    async def _safe_callback(self, cb, data):
        try:
            await cb(data)
        except Exception as e:
            print(f"[CELLULAR-AUDIO] Callback error: {e}")

    async def write_pcm16_direct(self, pcm16_data: bytes,
                                stop_event: Optional[asyncio.Event] = None):
        """Queue 8kHz PCM16 audio for playback and wait for it to drain.

        Args:
            pcm16_data: Raw 8kHz PCM16 mono audio bytes
            stop_event: If set, flush audio and return early (barge-in support)
        """
        if not self._running:
            print("[CELLULAR-AUDIO] write_pcm16_direct REJECTED: not running")
            return

        data_len = len(pcm16_data)
        duration_s = data_len / WRITE_BYTES_PER_SEC
        max_wait = duration_s + 10.0
        print(f"[CELLULAR-AUDIO] write_pcm16_direct: {data_len} bytes "
              f"({duration_s:.1f}s, timeout={max_wait:.0f}s)")

        # Capture BEFORE queueing — I/O thread could start writing immediately
        written_before = self._bytes_written
        self._write_queue.put(pcm16_data)
        start = asyncio.get_event_loop().time()
        while self._running:
            # Check for barge-in interruption
            if stop_event and stop_event.is_set():
                self.flush_audio()
                print(f"[CELLULAR-AUDIO] write_pcm16_direct interrupted (barge-in)")
                return

            written_now = self._bytes_written
            if written_now >= written_before + data_len:
                break
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > max_wait:
                written_so_far = self._bytes_written - written_before
                print(f"[CELLULAR-AUDIO] write_pcm16_direct TIMEOUT after {elapsed:.1f}s "
                      f"({written_so_far}/{data_len} bytes written)")
                break
            await asyncio.sleep(0.05)

        print(f"[CELLULAR-AUDIO] write_pcm16_direct done: {self._bytes_written} total")

    async def write_audio(self, ulaw_data: bytes):
        """Write ULAW audio (legacy path). Decodes to 8kHz PCM16."""
        if not self._running:
            return
        try:
            pcm_8k = AudioConverter.ulaw_bytes_to_pcm16(ulaw_data)
            self._write_queue.put(pcm_8k)
        except Exception as e:
            print(f"[CELLULAR-AUDIO] Write error: {e}")

    async def write_ai_audio(self, pcm16_data: bytes, source_rate: int = 24000):
        """Queue AI audio. Resamples to 8kHz."""
        if not self._running:
            return
        try:
            if source_rate == MODEM_WRITE_RATE:
                pcm_modem = pcm16_data
            else:
                samples = np.frombuffer(pcm16_data, dtype=np.int16)
                num_out = int(len(samples) * MODEM_WRITE_RATE / source_rate)
                resampled = resample(samples.astype(np.float64), num_out)
                pcm_modem = np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()

            self._write_queue.put(pcm_modem)

            if self._bytes_written == 0 and len(pcm_modem) > 0:
                print(f"[CELLULAR-AUDIO] First AI audio: {len(pcm_modem)} bytes "
                      f"(from {source_rate}Hz)")
        except Exception as e:
            print(f"[CELLULAR-AUDIO] AI audio error: {e}")

    @property
    def stats(self) -> dict:
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        return {
            "running": self._running,
            "frames_read": self._frames_read,
            "bytes_read": self._bytes_read,
            "bytes_written": self._bytes_written,
            "write_queue_size": self._write_queue.qsize(),
            "write_errors": self._write_errors,
            "elapsed_seconds": round(elapsed, 1),
            "port": self.audio_port,
        }
