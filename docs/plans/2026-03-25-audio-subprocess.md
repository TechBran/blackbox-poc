# AudioSocket Subprocess Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move AudioSocket I/O into a dedicated subprocess with a clean event loop, giving us the same perfect 20ms audio timing that the pre-generated TTS test achieved — eliminating the choppiness caused by the main BlackBox event loop's load.

**Architecture:** A lightweight Python subprocess owns the AudioSocket TCP server (port 9092) and handles ALL audio frame pacing. The main BlackBox process communicates bidirectional audio via a unix socket pair created at fork time. The subprocess has its own asyncio event loop with nothing else running — just AudioSocket reads/writes at precise 20ms intervals. IVR stays in the main process (ARI/Stasis before AudioSocket). The voice_bridge.py in the main process sends raw 24kHz AI audio to the subprocess (which resamples + paces), and receives 8kHz phone audio from the subprocess (which it upsamples + sends to AI).

**Tech Stack:** Python `multiprocessing` (fork-based), `socket.socketpair()` for IPC, asyncio (subprocess), scipy `resample_poly`, existing AudioSocket 3-byte protocol.

---

## Why This Will Work

The pre-generated TTS test proved AudioSocket can deliver **perfect audio** when:
1. The event loop has nothing else running (just sleep + write)
2. Resampling is done in the same execution context as sending
3. No GIL contention with other threads/coroutines

The subprocess replicates this exact environment permanently. The main BlackBox event loop (hundreds of routes, WS handlers, HTTP server) never touches AudioSocket frames.

## Architecture Diagram

```
                    MAIN PROCESS                              SUBPROCESS
              (busy event loop - FastAPI,                (clean event loop -
               WS, ARI, sessions, etc.)                  ONLY audio I/O)
    ┌──────────────────────────────────┐          ┌────────────────────────────┐
    │                                  │          │                            │
    │  Orchestrator (port 9091)        │          │  AudioSocket TCP (:9092)   │
    │  ├─ WS bridges (OpenAI/etc)     │  Unix    │  ├─ Accept from Asterisk   │
    │  ├─ ARI client (IVR, Stasis)    │  Socket  │  ├─ Read phone audio       │
    │  ├─ voice_bridge.py             │◄────────►│  ├─ Write AI audio @20ms   │
    │  │  ├─ Receives AI audio_delta  │  Pair    │  ├─ resample_poly 24k→8k   │
    │  │  ├─ Sends raw 24kHz to sub ──┼─────────►│  ├─ Pre-buffer 1000ms      │
    │  │  └─ Receives 8kHz from sub ◄─┼──────────┤  └─ Perfect frame pacing   │
    │  │     └─ Upsamples → sends     │          │                            │
    │  │        to AI WS              │          │  Nothing else running.      │
    │  ├─ IVR (runs in Stasis)        │          │  Clean asyncio loop.        │
    │  ├─ Sessions, snapshots, chat   │          │  Same as TTS test.          │
    │  └─ All other BlackBox features │          │                            │
    └──────────────────────────────────┘          └────────────────────────────┘
```

## IPC Protocol

Unix socket pair (SOCK_STREAM). Simple length-prefixed messages:

```
[msg_type: 1 byte] [payload_length: 4 bytes LE uint32] [payload: N bytes]

Message Types:
  0x01 = PHONE_AUDIO     subprocess → main: 8kHz PCM16 from phone
  0x02 = AI_AUDIO        main → subprocess: raw 24kHz PCM16 from AI
  0x03 = CHANNEL_OPEN    subprocess → main: new AudioSocket channel (payload = UUID string)
  0x04 = CHANNEL_CLOSE   subprocess → main: channel disconnected (payload = UUID string)
  0x05 = CLEAR_BUFFER    main → subprocess: barge-in, clear output buffer
  0x06 = SHUTDOWN        main → subprocess: clean shutdown
```

Payload for audio messages includes the channel UUID prefix (36 bytes) + audio data.
This supports multiple concurrent calls.

---

## Task 1: Create the Audio Subprocess

**Files:**
- Create: `Orchestrator/asterisk/audio_subprocess.py`

This is a **standalone script** that runs as a subprocess. It:
1. Receives one end of a unix socketpair from the parent (fd passed via command-line arg)
2. Starts AudioSocket TCP server on port 9092
3. Handles all AudioSocket connections (read phone audio, write AI audio)
4. Communicates with parent via the unix socket IPC protocol
5. Resamples 24kHz→8kHz in its own thread (same pattern as TTS test)
6. Sends 8kHz frames at perfect 20ms pace via asyncio.sleep

**Key classes:**

```python
class AudioSubprocess:
    """Main entry point for the audio subprocess."""

    def __init__(self, ipc_fd: int):
        """
        Args:
            ipc_fd: File descriptor of the unix socket to parent process
        """
        self._ipc_fd = ipc_fd
        self._ipc_reader = None
        self._ipc_writer = None

        # AudioSocket connections: uuid → (reader, writer)
        self._connections = {}

        # Per-channel output: uuid → OutputChannel
        self._outputs = {}

    async def run(self):
        """Main async entry point."""
        # Wrap IPC fd as asyncio streams
        loop = asyncio.get_event_loop()
        self._ipc_reader, self._ipc_writer = await asyncio.open_connection(sock=socket.fromfd(self._ipc_fd, socket.AF_UNIX, socket.SOCK_STREAM))

        # Start AudioSocket TCP server
        server = await asyncio.start_server(self._handle_audiosocket, '127.0.0.1', 9092)

        # Start IPC listener (receives AI audio from main process)
        asyncio.create_task(self._ipc_listener())

        print("[AUDIO-SUB] Subprocess ready on :9092")
        await server.serve_forever()


class OutputChannel:
    """Per-channel output buffer + 20ms drain."""

    def __init__(self, writer, uuid, rate=8000):
        self._writer = writer  # AudioSocket StreamWriter
        self._uuid = uuid
        self._rate = rate
        self._frame_bytes = 320  # 20ms at 8kHz
        self._frames = collections.deque()
        self._pre_buffer_frames = 50  # 1000ms
        self._pre_buffer_done = False
        self._active = True
        self._drain_task = None

    def start(self):
        self._drain_task = asyncio.create_task(self._drain())

    def push_raw_24k(self, pcm16_24k: bytes):
        """Receive 24kHz audio, resample, chunk into frames."""
        # resample_poly in the event loop is fine here —
        # this event loop has NOTHING ELSE running
        samples = np.frombuffer(pcm16_24k, dtype=np.int16).astype(np.float64)
        resampled = resample_poly(samples, 1, 3)
        pcm_8k = np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()
        # Chunk into 320-byte frames
        offset = 0
        while offset + self._frame_bytes <= len(pcm_8k):
            self._frames.append(pcm_8k[offset:offset + self._frame_bytes])
            offset += self._frame_bytes

    async def _drain(self):
        """Send 1 frame every 20ms — clean event loop = perfect timing."""
        HEADER = bytes([0x10, 0x01, 0x40])  # AudioSocket: type=audio, length=320
        next_send = asyncio.get_event_loop().time()
        interval = 0.020

        while self._active:
            now = asyncio.get_event_loop().time()
            wait = next_send - now
            if wait > 0.001:
                await asyncio.sleep(wait)
            next_send += interval

            if not self._pre_buffer_done:
                if len(self._frames) < self._pre_buffer_frames:
                    continue
                self._pre_buffer_done = True

            if self._frames:
                frame = self._frames.popleft()
                self._writer.write(HEADER + frame)
                await self._writer.drain()
```

**Subprocess entry point** (bottom of file):
```python
if __name__ == "__main__":
    import sys
    ipc_fd = int(sys.argv[1])
    sub = AudioSubprocess(ipc_fd)
    asyncio.run(sub.run())
```

**Step 1:** Create the full `audio_subprocess.py` with AudioSubprocess and OutputChannel classes.

**Step 2:** Verify it can run standalone:
```bash
python3 Orchestrator/asterisk/audio_subprocess.py --test
```

---

## Task 2: Create IPC Client for Main Process

**Files:**
- Create: `Orchestrator/asterisk/audio_ipc.py`

This runs in the main BlackBox process. It:
1. Spawns the subprocess (passing one end of the socketpair)
2. Sends AI audio (24kHz) to the subprocess
3. Receives phone audio (8kHz) from the subprocess
4. Receives channel connect/disconnect notifications
5. Provides async API matching the old audio_bridge interface

```python
class AudioIPCClient:
    """Main-process side of the audio subprocess IPC."""

    def __init__(self):
        self._process = None
        self._ipc_writer = None
        self._ipc_reader = None
        self._running = False

        # Callbacks (same interface as old audio_bridge)
        self._audio_handlers = {}   # channel_uuid → async callback
        self._connect_handlers = {}
        self._hangup_handlers = {}
        self._connect_events = {}   # For wait_for_channel()

    async def start(self):
        """Spawn subprocess, establish IPC."""
        parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)

        # Spawn subprocess
        self._process = await asyncio.create_subprocess_exec(
            sys.executable,
            'Orchestrator/asterisk/audio_subprocess.py',
            str(child_sock.fileno()),
            pass_fds=(child_sock.fileno(),),
        )
        child_sock.close()  # Parent doesn't need this end

        # Wrap our end as asyncio streams
        loop = asyncio.get_event_loop()
        self._ipc_reader, self._ipc_writer = await asyncio.open_connection(
            sock=parent_sock
        )
        self._running = True
        asyncio.create_task(self._listener())

    async def send_ai_audio(self, channel_uuid: str, pcm16_24k: bytes):
        """Send raw 24kHz AI audio to subprocess for resampling + pacing."""
        # [0x02] [len:4 LE] [uuid:36] [audio data]
        payload = channel_uuid.encode('ascii') + pcm16_24k
        header = bytes([0x02]) + len(payload).to_bytes(4, 'little')
        self._ipc_writer.write(header + payload)
        await self._ipc_writer.drain()

    async def clear_buffer(self, channel_uuid: str):
        """Barge-in: tell subprocess to clear output buffer."""
        payload = channel_uuid.encode('ascii')
        header = bytes([0x05]) + len(payload).to_bytes(4, 'little')
        self._ipc_writer.write(header + payload)
        await self._ipc_writer.drain()

    def expect_channel(self, channel_uuid: str) -> asyncio.Event:
        """Register expectation for an AudioSocket channel."""
        evt = asyncio.Event()
        self._connect_events[channel_uuid] = evt
        return evt

    async def wait_for_channel(self, channel_uuid: str, timeout: float = 10.0) -> bool:
        """Wait for AudioSocket connection notification from subprocess."""
        evt = self._connect_events.get(channel_uuid)
        if not evt:
            evt = self.expect_channel(channel_uuid)
        try:
            await asyncio.wait_for(evt.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # Same register/unregister interface as audio_bridge
    def register_channel(self, uuid, on_audio=None, on_hangup=None):
        if on_audio: self._audio_handlers[uuid] = on_audio
        if on_hangup: self._hangup_handlers[uuid] = on_hangup

    def unregister_channel(self, uuid):
        self._audio_handlers.pop(uuid, None)
        self._hangup_handlers.pop(uuid, None)

    async def _listener(self):
        """Receive messages from subprocess."""
        while self._running:
            # Read header: [type:1][length:4]
            header = await self._ipc_reader.readexactly(5)
            msg_type = header[0]
            length = int.from_bytes(header[1:5], 'little')
            payload = await self._ipc_reader.readexactly(length) if length > 0 else b""

            if msg_type == 0x01:  # PHONE_AUDIO
                uuid = payload[:36].decode('ascii')
                audio = payload[36:]
                handler = self._audio_handlers.get(uuid)
                if handler:
                    asyncio.create_task(handler(uuid, audio))

            elif msg_type == 0x03:  # CHANNEL_OPEN
                uuid = payload.decode('ascii')
                evt = self._connect_events.pop(uuid, None)
                if evt:
                    evt.set()

            elif msg_type == 0x04:  # CHANNEL_CLOSE
                uuid = payload.decode('ascii')
                handler = self._hangup_handlers.get(uuid)
                if handler:
                    asyncio.create_task(handler(uuid))
                self.unregister_channel(uuid)
```

**Step 1:** Create `audio_ipc.py` with the full AudioIPCClient class.

---

## Task 3: Update voice_bridge.py — Use IPC Instead of Direct AudioSocket

**Files:**
- Modify: `Orchestrator/asterisk/voice_bridge.py`

Replace the `AudioOutputPipeline` and direct AudioSocket interaction with IPC calls. The voice_bridge now:
- Sends raw 24kHz AI audio via `ipc_client.send_ai_audio()` (subprocess handles resampling + pacing)
- Receives 8kHz phone audio via IPC callback (upsamples + sends to AI WS)
- Barge-in via `ipc_client.clear_buffer()`

**Key changes:**
1. Remove `AudioOutputPipeline` class entirely (subprocess handles this now)
2. Remove `StreamingDownsampler` (subprocess handles resampling)
3. Keep `StreamingUpsampler` (phone→AI upsampling stays in main process, lightweight)
4. Constructor takes `ipc_client` instead of `audio_bridge`
5. `push_raw()` calls become `ipc_client.send_ai_audio()`
6. Channel registration uses `ipc_client.register_channel()`

```python
class AsteriskVoiceBridge:
    def __init__(self, ipc_client, channel_uuid, backend, operator, voice, asterisk_rate):
        self._ipc = ipc_client  # AudioIPCClient (replaces audio_bridge)
        # ... rest stays similar

    async def start(self):
        # Connect to WS streaming endpoint (same as before)
        # Register IPC handlers for phone audio
        self._ipc.register_channel(
            self._channel_uuid,
            on_audio=self._on_phone_audio,
            on_hangup=self._on_phone_hangup,
        )
        # Start WS listener
        # Play connection tone via IPC (send pre-generated 8kHz chime)
```

**Step 1:** Rewrite voice_bridge.py to use AudioIPCClient.

---

## Task 4: Update asterisk_routes.py — Use IPC Client

**Files:**
- Modify: `Orchestrator/routes/asterisk_routes.py`

The inbound call handler currently does:
```python
audio_bridge = get_audio_bridge()
audio_bridge.expect_channel(call_uuid)
# ... later ...
voice_bridge = AsteriskVoiceBridge(audio_bridge=audio_bridge, ...)
```

Change to:
```python
from Orchestrator.asterisk.audio_ipc import get_ipc_client
ipc_client = get_ipc_client()
ipc_client.expect_channel(call_uuid)
# ... later ...
voice_bridge = AsteriskVoiceBridge(ipc_client=ipc_client, ...)
```

The interface is nearly identical — `expect_channel`, `wait_for_channel`, `register_channel` all exist on AudioIPCClient with the same signatures.

**Step 1:** Update `handle_inbound_call` to use `get_ipc_client()`.
**Step 2:** Update `_handle_outbound_call` similarly.

---

## Task 5: Update startup.py — Spawn Subprocess

**Files:**
- Modify: `Orchestrator/startup.py`

Replace `init_audio_bridge()` with subprocess spawning:

```python
# In startup_asterisk():
from Orchestrator.asterisk.audio_ipc import init_ipc_client
ipc_client = await init_ipc_client()
if ipc_client:
    logger.info("Audio subprocess started on port 9092")
else:
    logger.warning("Audio subprocess failed to start")
```

Add shutdown:
```python
# In shutdown_asterisk():
from Orchestrator.asterisk.audio_ipc import get_ipc_client
client = get_ipc_client()
if client:
    await client.shutdown()
```

**Step 1:** Update startup to use subprocess instead of in-process AudioSocket server.

---

## Task 6: Connection Tone via IPC

**Files:**
- Modify: `Orchestrator/asterisk/voice_bridge.py`

The connection tone (ascending chime) needs to be sent through the subprocess. Two options:
- A: Send pre-generated 8kHz chime as AI_AUDIO message (subprocess just passes it through)
- B: Add a PLAY_TONE IPC message type

Option A is simpler. Generate the chime in the main process, send as 8kHz PCM16 via IPC with a special "direct" flag or just send as AI_AUDIO with rate=8000 (subprocess detects no resampling needed).

**Step 1:** Generate chime bytes in voice_bridge, send via `ipc_client.send_ai_audio()` with a flag or pre-downsampled.

---

## Task 7: Deploy and End-to-End Test

**Steps:**

1. Restart BlackBox:
   ```bash
   sudo systemctl restart blackbox.service
   ```

2. Verify subprocess is running:
   ```bash
   ps aux | grep audio_subprocess
   ss -tlnp | grep 9092
   ```

3. Test IVR flow (should be unchanged)

4. Test AI voice quality — this is the big test:
   - Call in, go through IVR, select GPT
   - Listen to full AI response
   - Should sound as clean as the TTS test (no choppiness)
   - Try Gemini and Grok too

5. Check logs:
   ```bash
   sudo journalctl -u blackbox.service --no-pager -n 30 | grep -iE "AUDIO-SUB|IPC|VOICE-BRIDGE"
   ```

6. Test barge-in:
   - Interrupt AI mid-sentence
   - AI should stop and listen
   - New response should start cleanly

7. Test concurrent calls (if possible):
   - Two calls simultaneously
   - Each should have independent audio

---

## Dependency Graph

```
Task 1 (audio_subprocess.py) ────────┐
Task 2 (audio_ipc.py) ──────────────┤
Task 3 (voice_bridge.py update) ─────┤── Task 7 (Deploy + Test)
Task 4 (asterisk_routes.py update) ──┤
Task 5 (startup.py update) ──────────┤
Task 6 (connection tone) ────────────┘
```

Tasks 1 and 2 can be developed in parallel (subprocess and IPC client are independent until integration).

---

## Rollback Plan

If the subprocess approach has issues, the old audio_bridge.py + AudioOutputPipeline is still in the codebase. To rollback:
1. Revert startup.py to use `init_audio_bridge()` instead of `init_ipc_client()`
2. Revert asterisk_routes.py to use `get_audio_bridge()` instead of `get_ipc_client()`
3. Revert voice_bridge.py to use AudioOutputPipeline

The old classes are not deleted, just bypassed.
