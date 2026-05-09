#!/usr/bin/env python3
"""
audio_ipc.py - Main-process side of the audio subprocess IPC.

Spawns the audio subprocess and communicates via a Unix socketpair
using a binary framing protocol. Provides the same interface as
AsteriskAudioBridge so voice_bridge.py and asterisk_routes.py can
use it with minimal changes.

IPC Protocol (matches audio_subprocess.py):
  [type: 1 byte] [length: 4 bytes LE uint32] [payload]

  0x01 = PHONE_AUDIO   subprocess→main   uuid(36B ASCII) + 8kHz PCM16
  0x02 = AI_AUDIO       main→subprocess   uuid(36B ASCII) + 24kHz PCM16
  0x03 = CHANNEL_OPEN   subprocess→main   uuid string (36B)
  0x04 = CHANNEL_CLOSE  subprocess→main   uuid string (36B)
  0x05 = CLEAR_BUFFER   main→subprocess   uuid string (36B)
  0x06 = SHUTDOWN        main→subprocess   empty payload
"""

import asyncio
import os
import socket
import struct
import sys
from typing import Optional, Callable, Awaitable, Dict

# IPC message types
MSG_PHONE_AUDIO = 0x01
MSG_AI_AUDIO = 0x02
MSG_CHANNEL_OPEN = 0x03
MSG_CHANNEL_CLOSE = 0x04
MSG_CLEAR_BUFFER = 0x05
MSG_SHUTDOWN = 0x06

# IPC header: 1 byte type + 4 bytes LE uint32 length
IPC_HEADER_SIZE = 5
IPC_HEADER_FMT = "<BI"

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_ipc_client: Optional["AudioIPCClient"] = None


def get_ipc_client() -> Optional["AudioIPCClient"]:
    """Get the singleton IPC client."""
    return _ipc_client


async def init_ipc_client() -> Optional["AudioIPCClient"]:
    """Initialize and start the singleton IPC client."""
    global _ipc_client
    if _ipc_client and _ipc_client.is_running:
        return _ipc_client
    client = AudioIPCClient()
    started = await client.start()
    if started:
        _ipc_client = client
        return client
    return None


# ---------------------------------------------------------------------------
# AudioIPCClient
# ---------------------------------------------------------------------------
class AudioIPCClient:
    """
    Spawns the audio subprocess and provides the same channel interface
    as AsteriskAudioBridge:
      - expect_channel / wait_for_channel
      - register_channel / unregister_channel
      - send_ai_audio (24kHz PCM16 → subprocess handles resampling + pacing)
      - clear_buffer (barge-in)
      - get_channel_rate (always 8000 — subprocess handles conversion)
    """

    def __init__(self):
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._running = False

        self._listener_task: Optional[asyncio.Task] = None
        self._log_task: Optional[asyncio.Task] = None

        # Channel UUID → asyncio.Event (fires when subprocess reports CHANNEL_OPEN)
        self._connect_events: Dict[str, asyncio.Event] = {}
        # Active channel tracking
        self._active_channels: set = set()

        # Per-channel callbacks
        self._audio_handlers: Dict[str, Callable[[str, bytes], Awaitable[None]]] = {}
        self._hangup_handlers: Dict[str, Callable[[str], Awaitable[None]]] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_channels(self) -> set:
        return self._active_channels.copy()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Spawn the audio subprocess and wire up IPC."""
        try:
            parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)

            # Path to subprocess script
            sub_script = os.path.join(os.path.dirname(__file__), "audio_subprocess.py")

            # Find the venv python
            venv_python = os.path.join(
                os.path.dirname(__file__), "..", "venv", "bin", "python3"
            )
            if not os.path.exists(venv_python):
                venv_python = sys.executable

            # Spawn subprocess, passing child socket fd
            self._process = await asyncio.create_subprocess_exec(
                venv_python,
                sub_script,
                str(child_sock.fileno()),
                pass_fds=(child_sock.fileno(),),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            child_sock.close()  # Parent doesn't need the child end

            # Wrap our socket end as asyncio streams
            reader, writer = await asyncio.open_connection(sock=parent_sock)
            self._reader = reader
            self._writer = writer
            self._running = True

            # Start listener for messages from subprocess
            self._listener_task = asyncio.create_task(self._ipc_listener())
            # Forward subprocess stdout to our logging
            self._log_task = asyncio.create_task(self._log_forwarder())

            print("[IPC] Audio subprocess started, PID:", self._process.pid)
            return True

        except Exception as e:
            print(f"[IPC] Failed to start audio subprocess: {e}")
            return False

    async def shutdown(self):
        """Gracefully stop subprocess."""
        self._running = False

        # Send SHUTDOWN message
        if self._writer:
            try:
                header = struct.pack(IPC_HEADER_FMT, MSG_SHUTDOWN, 0)
                self._writer.write(header)
                await self._writer.drain()
            except Exception:
                pass

        # Terminate subprocess
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass

        # Cancel background tasks
        for task in [self._listener_task, self._log_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # Close writer
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

        # Cleanup state
        self._active_channels.clear()
        self._connect_events.clear()
        self._audio_handlers.clear()
        self._hangup_handlers.clear()
        self._reader = None
        self._writer = None
        self._process = None
        self._listener_task = None
        self._log_task = None

        print("[IPC] Audio subprocess stopped")

    # ------------------------------------------------------------------
    # Per-channel registration (same interface as AsteriskAudioBridge)
    # ------------------------------------------------------------------

    def register_channel(
        self,
        channel_id: str,
        on_audio: Callable[[str, bytes], Awaitable[None]] = None,
        on_hangup: Callable[[str], Awaitable[None]] = None,
    ):
        """Register per-channel audio/hangup handlers."""
        if on_audio:
            self._audio_handlers[channel_id] = on_audio
        if on_hangup:
            self._hangup_handlers[channel_id] = on_hangup

    def unregister_channel(self, channel_id: str):
        """Remove all handlers for a channel."""
        self._audio_handlers.pop(channel_id, None)
        self._hangup_handlers.pop(channel_id, None)
        self._active_channels.discard(channel_id)
        self._connect_events.pop(channel_id, None)

    def get_channel_rate(self, channel_id: str) -> int:
        """Get the sample rate for a channel. Always 8000 — subprocess handles conversion."""
        return 8000

    # ------------------------------------------------------------------
    # Connection waiting (same interface as AsteriskAudioBridge)
    # ------------------------------------------------------------------

    def expect_channel(self, channel_id: str) -> asyncio.Event:
        """
        Register expectation for a channel connection.

        Returns an Event that fires when the subprocess reports CHANNEL_OPEN.
        Used by outbound call flow to wait for Asterisk to connect back.
        """
        event = asyncio.Event()
        self._connect_events[channel_id] = event
        return event

    async def wait_for_channel(self, channel_id: str, timeout: float = 15.0) -> bool:
        """Wait for a specific channel to open in the subprocess."""
        event = self._connect_events.get(channel_id)
        if not event:
            event = self.expect_channel(channel_id)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            print(f"[IPC] Timeout waiting for channel: {channel_id}")
            self._connect_events.pop(channel_id, None)
            return False

    # ------------------------------------------------------------------
    # Sending to subprocess
    # ------------------------------------------------------------------

    async def send_ai_audio(self, channel_uuid: str, pcm16_24k: bytes):
        """Send raw 24kHz AI audio to subprocess for resampling + pacing."""
        if not self._running or not self._writer:
            return
        payload = channel_uuid.encode("ascii") + pcm16_24k
        header = struct.pack(IPC_HEADER_FMT, MSG_AI_AUDIO, len(payload))
        self._writer.write(header + payload)
        # Periodic drain to prevent OS buffer overflow without blocking every frame
        if not hasattr(self, '_ai_audio_frame_count'):
            self._ai_audio_frame_count = 0
        self._ai_audio_frame_count += 1
        if self._ai_audio_frame_count % 5 == 0:  # Drain every 5 frames (~100ms)
            await self._writer.drain()

    async def clear_buffer(self, channel_uuid: str):
        """Send clear-buffer command to subprocess (barge-in)."""
        if not self._running or not self._writer:
            return
        payload = channel_uuid.encode("ascii")
        header = struct.pack(IPC_HEADER_FMT, MSG_CLEAR_BUFFER, len(payload))
        self._writer.write(header + payload)
        await self._writer.drain()  # Barge-in is high priority — drain immediately

    # ------------------------------------------------------------------
    # IPC listener (receives messages from subprocess)
    # ------------------------------------------------------------------

    async def _ipc_listener(self):
        """Receive messages from subprocess."""
        try:
            while self._running:
                # Read 5-byte header
                header = await self._reader.readexactly(IPC_HEADER_SIZE)
                msg_type = header[0]
                length = int.from_bytes(header[1:5], "little")
                payload = await self._reader.readexactly(length) if length > 0 else b""

                if msg_type == MSG_PHONE_AUDIO:
                    # Phone audio from subprocess: uuid(36) + 8kHz PCM16
                    uuid_str = payload[:36].decode("ascii")
                    audio = payload[36:]
                    handler = self._audio_handlers.get(uuid_str)
                    if handler:
                        asyncio.create_task(handler(uuid_str, audio))

                elif msg_type == MSG_CHANNEL_OPEN:
                    uuid_str = (
                        payload[:36].decode("ascii")
                        if len(payload) >= 36
                        else payload.decode("ascii")
                    )
                    print(f"[IPC] Channel opened: {uuid_str}")
                    self._active_channels.add(uuid_str)
                    evt = self._connect_events.pop(uuid_str, None)
                    if evt:
                        evt.set()

                elif msg_type == MSG_CHANNEL_CLOSE:
                    uuid_str = (
                        payload[:36].decode("ascii")
                        if len(payload) >= 36
                        else payload.decode("ascii")
                    )
                    print(f"[IPC] Channel closed: {uuid_str}")
                    handler = self._hangup_handlers.get(uuid_str)
                    if handler:
                        asyncio.create_task(handler(uuid_str))
                    self.unregister_channel(uuid_str)

        except asyncio.IncompleteReadError:
            print("[IPC] Subprocess disconnected")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[IPC] Listener error: {e}")
        finally:
            self._running = False

    # ------------------------------------------------------------------
    # Log forwarder (subprocess stdout → our log)
    # ------------------------------------------------------------------

    async def _log_forwarder(self):
        """Forward subprocess stdout to our log."""
        try:
            while self._running and self._process and self._process.stdout:
                line = await self._process.stdout.readline()
                if not line:
                    break
                print(f"[AUDIO-SUB] {line.decode(errors='replace').rstrip()}")
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
