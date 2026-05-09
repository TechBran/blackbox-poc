#!/usr/bin/env python3
"""
audio_bridge.py - AudioSocket TCP Server for Asterisk ↔ Orchestrator Audio

Implements the Asterisk AudioSocket protocol:
  - TCP server on port 9092 (configurable)
  - Asterisk connects per-call via AudioSocket() dialplan app
  - Bidirectional 8kHz PCM16 (slin) audio streaming

AudioSocket Frame Format (Asterisk 20 protocol):
  [type: 1 byte] [length: 2 bytes big-endian (uint16)] [payload: length bytes]

  Type 0x01 = UUID    — first frame, 16-byte binary UUID
  Type 0x10 = Audio   — raw PCM16 slin audio data
  Type 0x00 = Hangup  — connection closing
  Type 0xFF = Error   — error frame

Note: Header is 3 bytes total (1 type + 2 length), NOT 4.
UUID is 16-byte binary, NOT 36-char ASCII.
"""

import asyncio
import struct
import traceback
import uuid as uuid_mod
from typing import Optional, Callable, Awaitable, Dict

from Orchestrator.asterisk.config import (
    ASTERISK_AUDIOSOCKET_HOST,
    ASTERISK_AUDIOSOCKET_PORT,
    ASTERISK_SAMPLE_RATE,
)

# AudioSocket frame types
FRAME_TYPE_HANGUP = 0x00
FRAME_TYPE_UUID = 0x01
FRAME_TYPE_AUDIO = 0x10
FRAME_TYPE_AUDIO_SLIN16 = 0x12  # slin 16kHz (Asterisk 23+)
FRAME_TYPE_AUDIO_SLIN24 = 0x13  # slin 24kHz (Asterisk 23+)
FRAME_TYPE_ERROR = 0xFF

# Map frame type → sample rate
AUDIO_TYPE_RATES = {
    0x10: 8000,
    0x11: 12000,
    0x12: 16000,
    0x13: 24000,
}

# Frame header size: 1 byte type + 2 bytes length (uint16 big-endian)
HEADER_SIZE = 3

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_audio_bridge: Optional["AsteriskAudioBridge"] = None


def get_audio_bridge() -> Optional["AsteriskAudioBridge"]:
    """Get the singleton AudioSocket bridge."""
    return _audio_bridge


async def init_audio_bridge() -> Optional["AsteriskAudioBridge"]:
    """Initialize and start the singleton AudioSocket bridge."""
    global _audio_bridge
    if _audio_bridge and _audio_bridge.is_running:
        return _audio_bridge

    bridge = AsteriskAudioBridge(
        host=ASTERISK_AUDIOSOCKET_HOST,
        port=ASTERISK_AUDIOSOCKET_PORT,
    )
    started = await bridge.start()
    if started:
        _audio_bridge = bridge
        return bridge
    return None


# ---------------------------------------------------------------------------
# AudioSocket Bridge
# ---------------------------------------------------------------------------
class AsteriskAudioBridge:
    """
    Bidirectional audio bridge between Asterisk AudioSocket and the Orchestrator.

    Asterisk sends 8kHz PCM16 (slin) frames via AudioSocket.
    We receive them and fire on_audio(channel_id, pcm16_8k).
    To send audio back, call send_audio(channel_id, pcm16_8k).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9092):
        self.host = host
        self.port = port
        self._server: Optional[asyncio.Server] = None
        self._running = False

        # Active connections: channel_uuid → (reader, writer)
        self._connections: Dict[str, asyncio.StreamWriter] = {}
        # Channel UUID → asyncio.Event (signals when AudioSocket connects)
        self._connect_events: Dict[str, asyncio.Event] = {}
        # Track active channel UUIDs for status reporting
        self._active_channels: set = set()

        # Per-channel callback dispatch (replaces singleton callbacks)
        self._audio_handlers: Dict[str, Callable[[str, bytes], Awaitable[None]]] = {}
        self._hangup_handlers: Dict[str, Callable[[str], Awaitable[None]]] = {}
        self._connect_handlers: Dict[str, Callable[[str], Awaitable[None]]] = {}
        self.on_connect: Optional[Callable[[str], Awaitable[None]]] = None  # Global fallback

        # Dynamic sample rate tracking per channel
        self._channel_rates: Dict[str, int] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_channels(self) -> set:
        return self._active_channels.copy()

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Start the AudioSocket TCP server."""
        try:
            self._server = await asyncio.start_server(
                self._handle_connection,
                self.host,
                self.port,
            )
            self._running = True
            addr = self._server.sockets[0].getsockname()
            print(f"[AudioSocket] Server listening on {addr[0]}:{addr[1]}")
            return True
        except Exception as e:
            print(f"[AudioSocket] Failed to start server: {e}")
            return False

    async def stop(self):
        """Stop the server and close all connections."""
        self._running = False
        # Close all active connections
        for channel_id, writer in list(self._connections.items()):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._connections.clear()
        self._active_channels.clear()
        self._connect_events.clear()
        self._channel_rates.clear()
        self._audio_handlers.clear()
        self._hangup_handlers.clear()
        self._connect_handlers.clear()

        if self._server:
            self._server.close()
            await self._server.wait_closed()
        print("[AudioSocket] Server stopped")

    # ------------------------------------------------------------------
    # Per-channel registration
    # ------------------------------------------------------------------

    def register_channel(self, channel_id: str,
                         on_audio: Callable[[str, bytes], Awaitable[None]] = None,
                         on_hangup: Callable[[str], Awaitable[None]] = None,
                         on_connect: Callable[[str], Awaitable[None]] = None):
        """Register per-channel audio/hangup/connect handlers."""
        if on_audio:
            self._audio_handlers[channel_id] = on_audio
        if on_hangup:
            self._hangup_handlers[channel_id] = on_hangup
        if on_connect:
            self._connect_handlers[channel_id] = on_connect

    def unregister_channel(self, channel_id: str):
        """Remove all handlers for a channel."""
        self._audio_handlers.pop(channel_id, None)
        self._hangup_handlers.pop(channel_id, None)
        self._connect_handlers.pop(channel_id, None)

    def get_channel_rate(self, channel_id: str) -> int:
        """Get the detected sample rate for a channel (default 8000)."""
        return self._channel_rates.get(channel_id, 8000)

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a single AudioSocket connection from Asterisk."""
        peer = writer.get_extra_info("peername")
        channel_id = None

        try:
            print(f"[AudioSocket] New connection from {peer}")

            # First frame must be UUID (type 0x01)
            frame_type, payload = await self._read_frame(reader)
            if frame_type != FRAME_TYPE_UUID:
                print(f"[AudioSocket] Expected UUID frame, got type={frame_type:#x}")
                writer.close()
                return

            # Asterisk sends UUID as 16-byte binary, not 36-char ASCII
            if len(payload) == 16:
                channel_id = str(uuid_mod.UUID(bytes=payload))
            else:
                channel_id = payload.decode("ascii", errors="replace").strip()
            print(f"[AudioSocket] Channel UUID: {channel_id}")

            # Register connection
            self._connections[channel_id] = writer
            self._active_channels.add(channel_id)
            self._channel_rates[channel_id] = 8000  # Default, updated on first audio frame

            # Signal anyone waiting for this channel to connect
            if channel_id in self._connect_events:
                self._connect_events[channel_id].set()

            # Fire per-channel connect handler, or global fallback
            connect_handler = self._connect_handlers.get(channel_id)
            if connect_handler:
                asyncio.create_task(connect_handler(channel_id))
            elif self.on_connect:
                asyncio.create_task(self.on_connect(channel_id))

            # Audio streaming loop
            while self._running:
                try:
                    frame_type, payload = await self._read_frame(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    print(f"[AudioSocket] Connection lost: {channel_id}")
                    break

                if frame_type in AUDIO_TYPE_RATES:
                    # Dynamic rate detection from frame type byte
                    detected_rate = AUDIO_TYPE_RATES[frame_type]
                    if detected_rate != self._channel_rates.get(channel_id, 8000):
                        self._channel_rates[channel_id] = detected_rate
                        print(f"[AudioSocket] Channel {channel_id} rate: {detected_rate}Hz")

                    # Forward PCM16 audio to per-channel handler
                    handler = self._audio_handlers.get(channel_id)
                    if handler and payload:
                        try:
                            await handler(channel_id, payload)
                        except Exception as e:
                            print(f"[AudioSocket] on_audio error: {e}")

                elif frame_type == FRAME_TYPE_HANGUP:
                    print(f"[AudioSocket] Hangup frame received: {channel_id}")
                    break

                elif frame_type == FRAME_TYPE_ERROR:
                    print(f"[AudioSocket] Error frame received: {channel_id}")
                    break

                elif frame_type is None:
                    # EOF
                    break

        except Exception as e:
            print(f"[AudioSocket] Connection error: {e}")
            traceback.print_exc()
        finally:
            # Cleanup
            if channel_id:
                # Fire per-channel hangup handler
                hangup_handler = self._hangup_handlers.get(channel_id)
                if hangup_handler:
                    asyncio.create_task(hangup_handler(channel_id))
                self.unregister_channel(channel_id)
                self._connections.pop(channel_id, None)
                self._active_channels.discard(channel_id)
                self._channel_rates.pop(channel_id, None)
                self._connect_events.pop(channel_id, None)
                print(f"[AudioSocket] Connection closed: {channel_id}")

            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_frame(self, reader: asyncio.StreamReader):
        """
        Read a single AudioSocket frame.

        Returns:
            (frame_type, payload) or (None, None) on EOF.
        """
        # Read 3-byte header: [type:1][length:2 big-endian]
        header = await reader.readexactly(HEADER_SIZE)
        frame_type = header[0]
        length = (header[1] << 8) | header[2]

        # Read payload
        if length > 0:
            payload = await reader.readexactly(length)
        else:
            payload = b""

        return frame_type, payload

    # ------------------------------------------------------------------
    # Send audio to Asterisk
    # ------------------------------------------------------------------

    async def send_audio(self, channel_id: str, pcm16_data: bytes):
        """
        Send PCM16 audio to Asterisk for a specific channel.
        Data must be 8kHz PCM16 (slin) — matching Asterisk AudioSocket's output format.
        """
        writer = self._connections.get(channel_id)
        if not writer or writer.is_closing():
            return

        try:
            # Select frame type based on detected channel rate
            rate = self._channel_rates.get(channel_id, 8000)
            if rate == 16000:
                ft = FRAME_TYPE_AUDIO_SLIN16
            elif rate == 24000:
                ft = FRAME_TYPE_AUDIO_SLIN24
            else:
                ft = FRAME_TYPE_AUDIO

            # Build AudioSocket frame: [type][length:2 bytes big-endian][payload]
            length = len(pcm16_data)
            header = bytes([
                ft,
                (length >> 8) & 0xFF,
                length & 0xFF,
            ])
            writer.write(header + pcm16_data)
            await writer.drain()
        except (ConnectionError, BrokenPipeError):
            print(f"[AudioSocket] Write failed (connection lost): {channel_id}")
            self._connections.pop(channel_id, None)
            self._active_channels.discard(channel_id)
        except Exception as e:
            print(f"[AudioSocket] Write error: {e}")

    def flush_audio(self, channel_id: str):
        """Flush pending audio for barge-in support (no-op for TCP, but kept for interface compat)."""
        # TCP doesn't buffer like serial, but if we add a write queue later, flush it here
        pass

    # ------------------------------------------------------------------
    # Connection waiting (for outbound call coordination)
    # ------------------------------------------------------------------

    def expect_channel(self, channel_id: str) -> asyncio.Event:
        """
        Register expectation for a channel connection.

        Returns an Event that fires when the channel's AudioSocket connects.
        Used by outbound call flow to wait for Asterisk to connect back.
        """
        event = asyncio.Event()
        self._connect_events[channel_id] = event
        return event

    async def wait_for_channel(self, channel_id: str, timeout: float = 15.0) -> bool:
        """Wait for a specific channel's AudioSocket to connect."""
        event = self._connect_events.get(channel_id)
        if not event:
            event = self.expect_channel(channel_id)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            print(f"[AudioSocket] Timeout waiting for channel: {channel_id}")
            self._connect_events.pop(channel_id, None)
            return False

    def is_channel_connected(self, channel_id: str) -> bool:
        """Check if a specific channel has an active AudioSocket connection."""
        writer = self._connections.get(channel_id)
        return writer is not None and not writer.is_closing()
