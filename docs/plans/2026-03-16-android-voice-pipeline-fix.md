# Android Voice Agent Pipeline Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the Android MVP voice agent module — audio pipeline dies after 1-2 responses due to cascading failures across client and server.

**Architecture:** Three-layer fix: (1) Android VoiceClient/WebSocketClient get audio_commit, keepalive, reconnection, post-speech delay. (2) VoiceViewModel gets decoupled audio playback with pre-buffering and thread sync. (3) Server WebSocket handlers get per-message error isolation and receive timeouts.

**Tech Stack:** Kotlin (Android), Python (FastAPI/Starlette), OkHttp WebSocket, AudioRecord/AudioTrack, asyncio

---

## File Manifest

| File | Action | Purpose |
|------|--------|---------|
| `AI_BlackBox_Portal_Android_MVP (2)/.../data/voice/VoiceClient.kt` | Modify | audio_commit, post-speech delay, keepalive, pong tracking |
| `AI_BlackBox_Portal_Android_MVP (2)/.../data/api/WebSocketClient.kt` | Modify | Buffer policy fix, reconnection support |
| `AI_BlackBox_Portal_Android_MVP (2)/.../ui/voice/VoiceScreen.kt` | Modify | Decoupled audio queue, pre-buffering, AudioTrack sync, volatile flag |
| `Orchestrator/routes/realtime_routes.py` | Modify | Per-message error isolation, receive timeout |
| `Orchestrator/routes/gemini_live_routes.py` | Modify | Same pattern |
| `Orchestrator/routes/grok_live_routes.py` | Modify | Same pattern |

**Base path for Android files:** `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/AI_BlackBox_Portal_Android_MVP (2)/AI_BlackBox_Portal_Android_MVP/AI_BlackBox_Portal/app/src/main/java/com/aiblackbox/portal/`

**Base path for server files:** `/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/routes/`

---

## Task 1: VoiceClient — Add audio_commit, post-speech delay, keepalive

**Files:**
- Modify: `data/voice/VoiceClient.kt` (full rewrite — 223 lines → ~320 lines)

**Why this matters:** The #1 reason the connection appears dead is that `audio_commit` is never sent, so the server never triggers a response. The #2 reason is no keepalive, so Tailscale/DERP times out idle connections. The #3 reason is no post-speech delay, causing feedback loops.

### Step 1: Replace VoiceClient.kt

Replace the entire file with this implementation. Key changes marked with `// FIX:` comments:

```kotlin
package com.aiblackbox.portal.data.voice

import com.aiblackbox.portal.data.api.WebSocketClient
import com.aiblackbox.portal.data.api.WsMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.OkHttpClient
import java.util.UUID

enum class VoiceBackend(val id: String, val displayName: String, val wsPath: String, val statusPath: String) {
    GPT_REALTIME("realtime", "GPT Realtime", "/ws/realtime", "/realtime/status"),
    GEMINI_LIVE("gemini-live", "Gemini Live", "/ws/gemini-live", "/gemini-live/status"),
    GROK_LIVE("grok-live", "Grok Live", "/ws/grok-live", "/grok-live/status")
}

enum class VoiceState { DISCONNECTED, CONNECTING, CONNECTED, SPEAKING, LISTENING, ERROR }

sealed class VoiceEvent {
    data class Transcript(val text: String, val isFinal: Boolean = false, val role: String = "assistant") : VoiceEvent()
    data class StatusChange(val state: VoiceState) : VoiceEvent()
    data class Error(val message: String) : VoiceEvent()
    data object Connected : VoiceEvent()
    data object Disconnected : VoiceEvent()
}

class VoiceClient(private val client: OkHttpClient, private val baseWsUrl: String) {
    private val wsClient = WebSocketClient(client)
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    private val _state = MutableStateFlow(VoiceState.DISCONNECTED)
    val state: StateFlow<VoiceState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<VoiceEvent>(extraBufferCapacity = 64)
    val events: SharedFlow<VoiceEvent> = _events.asSharedFlow()

    private val _transcript = MutableStateFlow<List<TranscriptEntry>>(emptyList())
    val transcript: StateFlow<List<TranscriptEntry>> = _transcript.asStateFlow()

    // AI speaking state — used by mic to auto-mute during AI output
    private val _isAISpeaking = MutableStateFlow(false)
    val isAISpeaking: StateFlow<Boolean> = _isAISpeaking.asStateFlow()

    // FIX: Track when AI stopped speaking for post-speech delay
    @Volatile
    var aiStoppedSpeakingAt: Long = 0L
        private set

    // Audio output flow — base64-encoded PCM16 chunks from the server
    private val _audioOutput = MutableSharedFlow<String>(extraBufferCapacity = 512)
    val audioOutput: SharedFlow<String> = _audioOutput.asSharedFlow()

    // Accumulator for streaming AI transcript deltas
    private var _currentAiText = MutableStateFlow("")

    private var connectionJob: Job? = null
    private var keepaliveJob: Job? = null
    private var currentOperator = ""
    private var currentVoice = ""
    private var scope: CoroutineScope? = null

    // FIX: Pong tracking for connection health monitoring
    @Volatile
    private var lastPongTime: Long = 0L

    companion object {
        const val POST_SPEECH_DELAY_MS = 800L  // FIX: Match OverlayService/Portal
        const val KEEPALIVE_INTERVAL_MS = 15_000L  // FIX: Send ping every 15s
        const val PONG_TIMEOUT_MS = 30_000L  // FIX: Close if no pong in 30s
    }

    fun connect(backend: VoiceBackend, operator: String, voice: String, scope: CoroutineScope) {
        this.scope = scope
        currentOperator = operator
        currentVoice = voice
        _state.value = VoiceState.CONNECTING
        connectionJob?.cancel()
        keepaliveJob?.cancel()

        connectionJob = scope.launch {
            val sessionId = UUID.randomUUID().toString()
            val url = "$baseWsUrl${backend.wsPath}/$sessionId?operator=$operator&voice=$voice"
            android.util.Log.d("VoiceClient", "Connecting to: $url")
            wsClient.connect(url).collect { msg ->
                when (msg) {
                    is WsMessage.Connected -> {
                        _state.value = VoiceState.CONNECTED
                        _events.emit(VoiceEvent.Connected)
                        lastPongTime = System.currentTimeMillis()
                        // Send connect message matching OverlayService pattern
                        val connectMsg = buildJsonObject {
                            put("type", "connect")
                            put("operator", currentOperator)
                            put("voice", currentVoice)
                        }
                        wsClient.send(connectMsg.toString())
                        android.util.Log.d("VoiceClient", "Sent connect message: operator=$currentOperator, voice=$currentVoice")

                        // FIX: Start keepalive loop
                        startKeepalive()
                    }
                    is WsMessage.Text -> parseMessage(msg.text)
                    is WsMessage.Closing -> {
                        android.util.Log.w("VoiceClient", "Server closing: ${msg.code} ${msg.reason}")
                        _events.emit(VoiceEvent.Error("Session closed: ${msg.reason}"))
                        _state.value = VoiceState.ERROR
                    }
                    is WsMessage.Error -> {
                        _state.value = VoiceState.ERROR
                        _events.emit(VoiceEvent.Error(msg.error.message ?: "Connection error"))
                    }
                    is WsMessage.Disconnected -> {
                        _state.value = VoiceState.DISCONNECTED
                        _isAISpeaking.value = false
                        _currentAiText.value = ""
                        _events.emit(VoiceEvent.Disconnected)
                    }
                }
            }
        }
    }

    fun disconnect() {
        keepaliveJob?.cancel()
        keepaliveJob = null
        connectionJob?.cancel()
        wsClient.close()
        _state.value = VoiceState.DISCONNECTED
        _isAISpeaking.value = false
        _currentAiText.value = ""
    }

    /** Send a base64-encoded PCM16 audio chunk to the server (mic input). */
    fun sendAudioChunk(base64Audio: String) {
        val msg = buildJsonObject {
            put("type", "audio_input")
            put("data", base64Audio)
        }
        wsClient.send(msg.toString())
    }

    // FIX: Send audio_commit to signal end of user speech turn
    fun sendAudioCommit() {
        val msg = buildJsonObject { put("type", "audio_commit") }
        wsClient.send(msg.toString())
        android.util.Log.d("VoiceClient", "Sent audio_commit")
    }

    fun sendText(text: String) {
        val msg = buildJsonObject { put("type", "text"); put("text", text) }
        wsClient.send(msg.toString())
    }

    // FIX: Application-level keepalive matching Portal pattern
    private fun startKeepalive() {
        keepaliveJob?.cancel()
        keepaliveJob = scope?.launch {
            while (isActive) {
                delay(KEEPALIVE_INTERVAL_MS)
                if (_state.value == VoiceState.DISCONNECTED || _state.value == VoiceState.ERROR) break

                // Check for pong timeout
                val timeSincePong = System.currentTimeMillis() - lastPongTime
                if (timeSincePong > PONG_TIMEOUT_MS) {
                    android.util.Log.w("VoiceClient", "No pong in ${timeSincePong}ms — closing for reconnect")
                    _state.value = VoiceState.ERROR
                    _events.emit(VoiceEvent.Error("Connection timed out"))
                    wsClient.close()
                    break
                }

                // Send application-level ping
                val ping = buildJsonObject { put("type", "ping") }
                if (!wsClient.send(ping.toString())) {
                    android.util.Log.w("VoiceClient", "Ping send failed — connection dead")
                    _state.value = VoiceState.ERROR
                    _events.emit(VoiceEvent.Error("Connection lost"))
                    break
                }
            }
        }
    }

    private suspend fun parseMessage(raw: String) {
        try {
            val obj = json.parseToJsonElement(raw).jsonObject
            val type = obj["type"]?.jsonPrimitive?.content ?: return
            val data = obj["data"]?.jsonPrimitive?.content ?: ""

            when (type) {
                "connected", "setup_complete" -> {
                    _state.value = VoiceState.CONNECTED
                    android.util.Log.d("VoiceClient", "Server message: $type")
                }

                "audio_delta" -> {
                    // AI is producing audio — mark as speaking and emit chunk
                    if (!_isAISpeaking.value) {
                        _isAISpeaking.value = true
                        _state.value = VoiceState.SPEAKING
                    }
                    if (data.isNotEmpty()) {
                        _audioOutput.emit(data) // base64 PCM16 chunk
                    }
                }

                "transcript_delta" -> {
                    _currentAiText.value += data
                    val updatedList = _transcript.value.toMutableList()
                    if (updatedList.isNotEmpty() && updatedList.last().role == "assistant") {
                        updatedList[updatedList.lastIndex] =
                            updatedList.last().copy(text = _currentAiText.value)
                    } else {
                        updatedList.add(TranscriptEntry(role = "assistant", text = _currentAiText.value))
                    }
                    _transcript.value = updatedList
                }

                "user_transcript" -> {
                    if (data.isNotBlank()) {
                        _transcript.value = _transcript.value + TranscriptEntry(role = "user", text = data)
                    }
                }

                "response_complete" -> {
                    // FIX: Track when AI stopped for post-speech delay
                    _isAISpeaking.value = false
                    aiStoppedSpeakingAt = System.currentTimeMillis()
                    _currentAiText.value = ""
                    _state.value = VoiceState.CONNECTED
                    android.util.Log.d("VoiceClient", "Response complete")
                }

                "transcript", "response" -> {
                    val text = obj["text"]?.jsonPrimitive?.content ?: data
                    val role = obj["role"]?.jsonPrimitive?.content ?: "assistant"
                    val isFinal = obj["final"]?.jsonPrimitive?.content?.toBooleanStrictOrNull() ?: true
                    if (text.isNotBlank()) {
                        _transcript.value = _transcript.value + TranscriptEntry(role = role, text = text)
                        _events.emit(VoiceEvent.Transcript(text, isFinal, role))
                    }
                }

                "speaking" -> {
                    _isAISpeaking.value = true
                    _state.value = VoiceState.SPEAKING
                }

                "listening" -> {
                    // FIX: Track when AI stopped for post-speech delay
                    _isAISpeaking.value = false
                    aiStoppedSpeakingAt = System.currentTimeMillis()
                    _state.value = VoiceState.LISTENING
                }

                // FIX: Handle pong for keepalive health monitoring
                "pong" -> {
                    lastPongTime = System.currentTimeMillis()
                }

                "error" -> {
                    val msg = obj["message"]?.jsonPrimitive?.content ?: data
                    _events.emit(VoiceEvent.Error(msg))
                    _state.value = VoiceState.ERROR
                    android.util.Log.e("VoiceClient", "Server error: $msg")
                }
            }
        } catch (e: Exception) {
            android.util.Log.e("VoiceClient", "Parse error: ${e.message}")
        }
    }
}

data class TranscriptEntry(
    val role: String,
    val text: String,
    val timestamp: Long = System.currentTimeMillis()
)
```

### Step 2: Verify changes compile

The changes are:
1. Added `sendAudioCommit()` method
2. Added `aiStoppedSpeakingAt` volatile field + `POST_SPEECH_DELAY_MS` constant
3. Added `startKeepalive()` loop that sends `{type: "ping"}` every 15s and monitors pong timeout
4. Added `"pong"` handler in `parseMessage()`
5. Set `aiStoppedSpeakingAt` in `"response_complete"` and `"listening"` handlers
6. Increased `_audioOutput` buffer from 256 to 512

---

## Task 2: WebSocketClient — Fix buffer policy

**Files:**
- Modify: `data/api/WebSocketClient.kt` (lines 46-47)

**Why:** `DROP_OLDEST` silently discards audio chunks. `SUSPEND` applies backpressure instead, which is safer because it pauses the producer rather than losing data.

### Step 1: Change buffer overflow policy

Change line 47 from:
```kotlin
.buffer(capacity = 512, onBufferOverflow = BufferOverflow.DROP_OLDEST)
```
to:
```kotlin
.buffer(capacity = 1024, onBufferOverflow = BufferOverflow.SUSPEND)
```

Increase capacity to 1024 (at ~50 msgs/sec = ~20 seconds buffer) and change to SUSPEND. The decoupled audio queue in Task 3 prevents the collector from blocking, so SUSPEND won't cause upstream stalls anymore.

### Step 2: Add Disconnected emission on failure

In `onFailure()` (lines 70-74), add `WsMessage.Disconnected` before closing the channel:

```kotlin
override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
    android.util.Log.e("WebSocket", "onFailure: ${t.message}, code=${response?.code}")
    trySend(WsMessage.Error(t))
    trySend(WsMessage.Disconnected)  // FIX: Emit Disconnected so client cleans up state
    channel.close()
}
```

---

## Task 3: VoiceViewModel — Decoupled audio queue, pre-buffering, thread sync, post-speech delay

**Files:**
- Modify: `ui/voice/VoiceScreen.kt` (VoiceViewModel class, lines 91-377)

**Why:** This is the biggest fix. The current code has AudioTrack.write() blocking inside the SharedFlow collector, which backs up the entire WebSocket pipeline. We need to decouple receipt from playback using a ConcurrentLinkedQueue, add pre-buffering (accumulate 12KB before first write), add AudioTrack synchronization, add post-speech delay to mic, and make `isRecordingAudio` volatile.

### Step 1: Replace VoiceViewModel class

Replace lines 91-377 with this implementation:

```kotlin
class VoiceViewModel(application: Application) : AndroidViewModel(application) {
    private val store = BlackBoxStore(application)
    private var voiceClient: VoiceClient? = null

    private val _backend = MutableStateFlow(VoiceBackend.GEMINI_LIVE)
    val backend: StateFlow<VoiceBackend> = _backend.asStateFlow()

    private val _voice = MutableStateFlow("Charon")
    val voice: StateFlow<String> = _voice.asStateFlow()

    private val _voiceState = MutableStateFlow(VoiceState.DISCONNECTED)
    val voiceState: StateFlow<VoiceState> = _voiceState.asStateFlow()

    private val _transcript = MutableStateFlow<List<TranscriptEntry>>(emptyList())
    val transcript: StateFlow<List<TranscriptEntry>> = _transcript.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    // Mic state
    private val _isMicActive = MutableStateFlow(false)
    val isMicActive: StateFlow<Boolean> = _isMicActive.asStateFlow()

    private var currentOperator = "Brandon"

    // Audio I/O
    private var audioRecord: AudioRecord? = null
    private var audioTrack: AudioTrack? = null
    private val audioTrackLock = Object()  // FIX: Synchronization lock matching OverlayService
    @Volatile private var isRecordingAudio = false  // FIX: Volatile for cross-thread visibility

    // FIX: Decoupled audio playback queue (matching OverlayService LinkedBlockingQueue pattern)
    private val audioPlaybackQueue = java.util.concurrent.ConcurrentLinkedQueue<ByteArray>()
    private var audioPlaybackJob: Job? = null

    // FIX: Pre-buffering state
    private var preBufferAccumulated = 0
    private var preBufferReady = false

    companion object {
        const val PRE_BUFFER_THRESHOLD_BYTES = 12_000  // ~250ms at 24kHz mono PCM16
    }

    init {
        viewModelScope.launch { store.operator.collect { currentOperator = it } }
    }

    fun initialize(origin: String) {
        if (origin.isBlank() || voiceClient != null) return
        try {
            val wsUrl = origin.replace("https://", "wss://").replace("http://", "ws://")
            voiceClient = VoiceClient(BlackBoxApi(origin).getClient(), wsUrl)

            // Collect state changes
            viewModelScope.launch {
                voiceClient?.state?.collect { state ->
                    _voiceState.value = state
                    // Auto-init audio when connected (matches OverlayService onOpen)
                    if (state == VoiceState.CONNECTED && audioTrack == null) {
                        try {
                            initAudioPlayback()
                            // FIX: Shorter delay, mic starts after audio pipeline ready
                            delay(200)
                            startMic()
                        } catch (e: Exception) {
                            android.util.Log.e("VoiceVM", "Audio init: ${e.message}", e)
                            _error.value = "Audio init failed: ${e.message}"
                        }
                    }
                    // Clean up audio on disconnect
                    if (state == VoiceState.DISCONNECTED || state == VoiceState.ERROR) {
                        stopMic()
                        stopAudioPlayback()
                    }
                }
            }
            // Collect transcripts
            viewModelScope.launch {
                voiceClient?.transcript?.collect { _transcript.value = it }
            }
            // Collect error events
            viewModelScope.launch {
                voiceClient?.events?.collect { event ->
                    if (event is VoiceEvent.Error) {
                        _error.value = event.message
                    }
                }
            }
            android.util.Log.d("VoiceVM", "Initialized: wsUrl=$wsUrl")
        } catch (e: Exception) {
            android.util.Log.e("VoiceVM", "Initialize failed: ${e.message}", e)
            _error.value = "Init failed: ${e.message}"
        }
    }

    fun setBackend(backend: VoiceBackend) { _backend.value = backend }
    fun setVoice(voice: String) { _voice.value = voice }

    fun connect() {
        _error.value = null
        // Clean up any previous session
        stopMic()
        stopAudioPlayback()
        _transcript.value = emptyList()

        voiceClient?.connect(_backend.value, currentOperator, _voice.value, viewModelScope)
    }

    fun disconnect() {
        stopMic()
        stopAudioPlayback()
        try {
            voiceClient?.disconnect()
        } catch (e: Exception) {
            android.util.Log.e("VoiceVM", "Disconnect: ${e.message}")
        }
        _voiceState.value = VoiceState.DISCONNECTED
    }

    fun toggleMic() {
        if (isRecordingAudio) stopMic() else startMic()
    }

    // -------------------------------------------------------------------------
    // Mic input — AudioRecord -> base64 PCM16 -> WebSocket
    // -------------------------------------------------------------------------
    fun startMic() {
        val app = getApplication<Application>()
        if (ContextCompat.checkSelfPermission(app, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            _error.value = "Microphone permission required"
            return
        }

        val sampleRate = when (_backend.value) {
            VoiceBackend.GPT_REALTIME -> 24000
            else -> 16000
        }

        val bufferSize = AudioRecord.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        ) * 2

        try {
            val record = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize
            )

            // FIX: Check STATE_INITIALIZED before starting (matching OverlayService)
            if (record.state != AudioRecord.STATE_INITIALIZED) {
                android.util.Log.e("VoiceVM", "AudioRecord failed to initialize")
                record.release()
                _error.value = "Microphone initialization failed"
                return
            }

            audioRecord = record
            record.startRecording()
            isRecordingAudio = true
            _isMicActive.value = true
            android.util.Log.d("VoiceVM", "Mic started: ${sampleRate}Hz, buffer=$bufferSize")

            viewModelScope.launch(Dispatchers.IO) {
                // FIX: Track if we were sending audio for audio_commit
                var wasSendingAudio = false
                try {
                    val buffer = ShortArray(bufferSize / 2)
                    while (isRecordingAudio) {
                        val readCount = audioRecord?.read(buffer, 0, buffer.size) ?: 0
                        if (readCount > 0) {
                            // FIX: Auto-mute while AI is speaking WITH post-speech delay
                            val client = voiceClient
                            if (client != null) {
                                val isSpeaking = client.isAISpeaking.value
                                val timeSinceStop = System.currentTimeMillis() - client.aiStoppedSpeakingAt
                                val inPostSpeechDelay = !isSpeaking && timeSinceStop < VoiceClient.POST_SPEECH_DELAY_MS

                                if (isSpeaking || inPostSpeechDelay) {
                                    // FIX: If we were sending audio, send commit before muting
                                    if (wasSendingAudio) {
                                        client.sendAudioCommit()
                                        wasSendingAudio = false
                                    }
                                    continue
                                }
                            }

                            // Convert shorts to little-endian bytes
                            val bytes = ByteArray(readCount * 2)
                            for (i in 0 until readCount) {
                                bytes[i * 2] = (buffer[i].toInt() and 0xFF).toByte()
                                bytes[i * 2 + 1] = (buffer[i].toInt() shr 8 and 0xFF).toByte()
                            }
                            val base64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
                            try {
                                voiceClient?.sendAudioChunk(base64)
                                wasSendingAudio = true
                            } catch (e: Exception) {
                                android.util.Log.e("VoiceVM", "Send audio chunk failed: ${e.message}")
                            }
                        }
                    }
                    // FIX: Send audio_commit when mic stops (matching OverlayService/Portal)
                    if (wasSendingAudio) {
                        voiceClient?.sendAudioCommit()
                    }
                } catch (e: Exception) {
                    android.util.Log.e("VoiceVM", "Mic loop error: ${e.message}", e)
                    isRecordingAudio = false
                    _isMicActive.value = false
                }
            }
        } catch (e: Exception) {
            android.util.Log.e("VoiceVM", "Mic start failed: ${e.message}", e)
            _error.value = "Mic start failed: ${e.message}"
        }
    }

    fun stopMic() {
        val wasRecording = isRecordingAudio
        isRecordingAudio = false
        _isMicActive.value = false
        try {
            audioRecord?.stop()
            audioRecord?.release()
        } catch (_: Exception) {}
        audioRecord = null
        // FIX: Send audio_commit on explicit stop if we were recording
        if (wasRecording) {
            voiceClient?.sendAudioCommit()
        }
        android.util.Log.d("VoiceVM", "Mic stopped")
    }

    // -------------------------------------------------------------------------
    // Audio playback — decoupled queue matching OverlayService pattern
    // -------------------------------------------------------------------------
    private fun initAudioPlayback() {
        val client = voiceClient ?: return

        // Release existing AudioTrack if any
        synchronized(audioTrackLock) {
            try {
                audioTrack?.stop()
                audioTrack?.release()
            } catch (_: Exception) {}
            audioTrack = null
        }

        // Reset pre-buffer state
        audioPlaybackQueue.clear()
        preBufferAccumulated = 0
        preBufferReady = false

        // All backends output 24kHz PCM16 mono (matches OverlayService)
        val outputSampleRate = 24000
        val channelConfig = AudioFormat.CHANNEL_OUT_MONO
        val audioFormat = AudioFormat.ENCODING_PCM_16BIT

        val minBufferSize = AudioTrack.getMinBufferSize(outputSampleRate, channelConfig, audioFormat)

        if (minBufferSize == AudioTrack.ERROR || minBufferSize == AudioTrack.ERROR_BAD_VALUE) {
            android.util.Log.e("VoiceVM", "Invalid AudioTrack buffer size: $minBufferSize")
            _error.value = "Audio output not available"
            return
        }

        val bufferSize = maxOf(minBufferSize * 4, 16384)

        try {
            val track = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setSampleRate(outputSampleRate)
                        .setChannelMask(channelConfig)
                        .setEncoding(audioFormat)
                        .build()
                )
                .setBufferSizeInBytes(bufferSize)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()

            if (track.state != AudioTrack.STATE_INITIALIZED) {
                android.util.Log.e("VoiceVM", "AudioTrack failed to initialize")
                track.release()
                _error.value = "Audio output failed to initialize"
                return
            }

            synchronized(audioTrackLock) {
                audioTrack = track
                track.play()
            }
            android.util.Log.d("VoiceVM", "AudioTrack initialized: ${outputSampleRate}Hz, buffer=$bufferSize")

            // FIX: Decoupled audio collector — receives chunks into queue (never blocks)
            viewModelScope.launch(Dispatchers.IO) {
                client.audioOutput.collect { base64Chunk ->
                    try {
                        val pcmBytes = Base64.decode(base64Chunk, Base64.NO_WRAP)
                        audioPlaybackQueue.offer(pcmBytes)  // Non-blocking enqueue

                        // FIX: Pre-buffering — accumulate before starting playback
                        if (!preBufferReady) {
                            preBufferAccumulated += pcmBytes.size
                            if (preBufferAccumulated >= PRE_BUFFER_THRESHOLD_BYTES) {
                                preBufferReady = true
                                startPlaybackDrain()
                            }
                        }
                    } catch (e: Exception) {
                        android.util.Log.e("VoiceVM", "Audio decode error: ${e.message}")
                    }
                }
            }
        } catch (e: Exception) {
            android.util.Log.e("VoiceVM", "AudioTrack creation failed: ${e.message}", e)
            _error.value = "Audio output error: ${e.message}"
        }
    }

    // FIX: Dedicated playback drain loop — writes to AudioTrack from queue
    private fun startPlaybackDrain() {
        audioPlaybackJob?.cancel()
        audioPlaybackJob = viewModelScope.launch(Dispatchers.IO) {
            android.util.Log.d("VoiceVM", "Playback drain started (pre-buffer: ${preBufferAccumulated}B)")
            try {
                while (isActive) {
                    val chunk = audioPlaybackQueue.poll()
                    if (chunk != null) {
                        synchronized(audioTrackLock) {
                            val track = audioTrack ?: return@synchronized
                            if (track.state == AudioTrack.STATE_INITIALIZED) {
                                track.write(chunk, 0, chunk.size)  // Blocking write OK here — dedicated thread
                            }
                        }
                    } else {
                        // Queue empty — if AI stopped speaking, we're done with this response
                        if (voiceClient?.isAISpeaking?.value != true) {
                            // Wait a bit for stragglers, then idle
                            delay(50)
                            if (audioPlaybackQueue.isEmpty()) {
                                delay(100)
                                // Keep polling in case new response starts
                            }
                        } else {
                            delay(5) // AI still speaking, poll quickly
                        }
                    }
                }
            } catch (e: Exception) {
                if (e !is kotlinx.coroutines.CancellationException) {
                    android.util.Log.e("VoiceVM", "Playback drain error: ${e.message}", e)
                }
            }
        }
    }

    private fun stopAudioPlayback() {
        audioPlaybackJob?.cancel()
        audioPlaybackJob = null
        audioPlaybackQueue.clear()
        preBufferAccumulated = 0
        preBufferReady = false
        synchronized(audioTrackLock) {
            try {
                audioTrack?.stop()
                audioTrack?.release()
            } catch (_: Exception) {}
            audioTrack = null
        }
        android.util.Log.d("VoiceVM", "AudioTrack stopped")
    }

    override fun onCleared() {
        super.onCleared()
        stopMic()
        stopAudioPlayback()
        try { voiceClient?.disconnect() } catch (_: Exception) {}
    }
}
```

### Step 2: Add missing import

At the top of VoiceScreen.kt, add the import for `ConcurrentLinkedQueue` — it's in `java.util.concurrent` which doesn't need an explicit import in Kotlin, but verify the `kotlinx.coroutines.CancellationException` reference compiles. The `kotlinx.coroutines.isActive` import is already available via the existing `kotlinx.coroutines.launch` import chain, but add it explicitly if needed:

```kotlin
import kotlinx.coroutines.isActive
```

---

## Task 4: Server — Per-message error isolation for realtime_routes.py

**Files:**
- Modify: `Orchestrator/routes/realtime_routes.py` (lines 1251-1313)

**Why:** A single malformed message (bad JSON, binary frame) kills the entire session. We need per-message try/catch inside the while loop, plus a receive timeout.

### Step 1: Replace the receive loop

Replace lines 1251-1313 with:

```python
    try:
        while True:
            # FIX: Receive with timeout — detect suspended/dead Android clients
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=120.0  # 2 minutes — generous for mobile
                )
            except asyncio.TimeoutError:
                print(f"[REALTIME] Client idle timeout (120s): {session_id}")
                await _safe_ws_send(websocket, {"type": "error", "data": "Idle timeout"})
                break

            # FIX: Per-message error isolation — bad JSON doesn't kill session
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as e:
                print(f"[REALTIME] Bad message from client: {e}")
                continue  # Skip bad message, keep session alive

            msg_type = data.get("type", "")

            if msg_type == "connect":
                # Initial connection - establish OpenAI WebSocket
                operator = data.get("operator", "")
                voice = data.get("voice", "ash")  # Default to ash if not specified
                session.operator = operator

                await _safe_ws_send(websocket, {
                    "type": "status",
                    "data": "Connecting to OpenAI..."
                })

                # Connect to OpenAI
                if await connect_to_openai(session):
                    # Configure session with tools, context, and voice
                    await configure_openai_session(session, operator, voice)
                    print(f"[REALTIME] Voice selected: {voice}")

                    # Start OpenAI listener task and keepalive
                    openai_task = asyncio.create_task(openai_listener(session))
                    keepalive_task = asyncio.create_task(openai_keepalive_loop(session))

                    await _safe_ws_send(websocket, {
                        "type": "connected",
                        "data": {
                            "session_id": session_id,
                            "operator": operator,
                            "model": OPENAI_REALTIME_MODEL
                        }
                    })
                else:
                    await _safe_ws_send(websocket, {
                        "type": "error",
                        "data": "Failed to connect to OpenAI Realtime API"
                    })

            elif msg_type == "disconnect":
                # Graceful disconnect
                session.intentional_disconnect = True
                break

            elif msg_type == "ping":
                # Keep-alive
                await _safe_ws_send(websocket, {"type": "pong"})

            else:
                # FIX: Isolate handler errors — don't kill session on one bad forward
                try:
                    await handle_portal_message(session, data)
                except Exception as handler_err:
                    print(f"[REALTIME] Handler error (non-fatal): {handler_err}")

    except WebSocketDisconnect:
        print(f"[REALTIME] Portal WebSocket disconnected: {session_id}")
        session.portal_ws = None

    except Exception as e:
        print(f"[REALTIME] WebSocket error: {e}")
        await _safe_ws_send(websocket, {
            "type": "error",
            "data": str(e)
        })
```

Key changes:
1. `receive_json()` → `receive_text()` + `json.loads()` with per-message error isolation
2. `asyncio.wait_for()` with 120s timeout to detect dead/suspended clients
3. `handle_portal_message()` wrapped in try/except so handler bugs don't kill the session

---

## Task 5: Server — Per-message error isolation for gemini_live_routes.py

**Files:**
- Modify: `Orchestrator/routes/gemini_live_routes.py` (lines 1454-1516)

### Step 1: Replace the receive loop

Replace lines 1454-1516 with the same pattern as Task 4 but with Gemini-specific content:

```python
    try:
        while True:
            # FIX: Receive with timeout — detect suspended/dead Android clients
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=120.0
                )
            except asyncio.TimeoutError:
                print(f"[GEMINI-LIVE] Client idle timeout (120s): {session_id}")
                await _safe_ws_send(websocket, {"type": "error", "data": "Idle timeout"})
                break

            # FIX: Per-message error isolation
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as e:
                print(f"[GEMINI-LIVE] Bad message from client: {e}")
                continue

            msg_type = data.get("type", "")

            if msg_type == "connect":
                # Initial connection - establish Gemini WebSocket
                operator = data.get("operator", "")
                voice = data.get("voice", GEMINI_LIVE_DEFAULT_VOICE)
                session.operator = operator

                await _safe_ws_send(websocket, {
                    "type": "status",
                    "data": "Connecting to Gemini Live..."
                })

                # Connect to Gemini
                if await connect_to_gemini(session):
                    # Configure session with tools and context
                    await configure_gemini_session(session, operator, voice)

                    # Start Gemini listener task and keepalive
                    gemini_task = asyncio.create_task(gemini_listener(session))
                    keepalive_task = asyncio.create_task(gemini_keepalive_loop(session))

                    await _safe_ws_send(websocket, {
                        "type": "connected",
                        "data": {
                            "session_id": session_id,
                            "operator": operator,
                            "model": GEMINI_LIVE_MODEL,
                            "voice": voice
                        }
                    })
                else:
                    await _safe_ws_send(websocket, {
                        "type": "error",
                        "data": "Failed to connect to Gemini Live API"
                    })

            elif msg_type == "disconnect":
                # Graceful disconnect
                session.intentional_disconnect = True
                break

            elif msg_type == "ping":
                # Keep-alive
                await _safe_ws_send(websocket, {"type": "pong"})

            else:
                # FIX: Isolate handler errors
                try:
                    await handle_portal_message(session, data)
                except Exception as handler_err:
                    print(f"[GEMINI-LIVE] Handler error (non-fatal): {handler_err}")

    except WebSocketDisconnect:
        print(f"[GEMINI-LIVE] Portal WebSocket disconnected: {session_id}")
        session.portal_ws = None  # Clear portal ws but keep model connection for grace period

    except Exception as e:
        print(f"[GEMINI-LIVE] WebSocket error: {e}")
        await _safe_ws_send(websocket, {
            "type": "error",
            "data": str(e)
        })
```

---

## Task 6: Server — Per-message error isolation for grok_live_routes.py

**Files:**
- Modify: `Orchestrator/routes/grok_live_routes.py` (lines 1297-1360)

### Step 1: Replace the receive loop

Replace lines 1297-1360 with the same pattern:

```python
    try:
        while True:
            # FIX: Receive with timeout — detect suspended/dead Android clients
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=120.0
                )
            except asyncio.TimeoutError:
                print(f"[GROK-LIVE] Client idle timeout (120s): {session_id}")
                await _safe_ws_send(websocket, {"type": "error", "data": "Idle timeout"})
                break

            # FIX: Per-message error isolation
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as e:
                print(f"[GROK-LIVE] Bad message from client: {e}")
                continue

            msg_type = data.get("type", "")

            if msg_type == "connect":
                # Initial connection - establish Grok WebSocket
                operator = data.get("operator", "")
                voice = data.get("voice", GROK_LIVE_DEFAULT_VOICE)
                session.operator = operator

                await _safe_ws_send(websocket, {
                    "type": "status",
                    "data": "Connecting to Grok..."
                })

                # Connect to Grok
                if await connect_to_grok(session):
                    # Configure session with tools, context, and voice
                    await configure_grok_session(session, operator, voice)
                    print(f"[GROK-LIVE] Voice selected: {voice}")

                    # Start Grok listener task and keepalive
                    grok_task = asyncio.create_task(grok_listener(session))
                    keepalive_task = asyncio.create_task(grok_keepalive_loop(session))

                    await _safe_ws_send(websocket, {
                        "type": "connected",
                        "data": {
                            "session_id": session_id,
                            "operator": operator,
                            "model": "grok-voice-agent",
                            "voice": voice
                        }
                    })
                else:
                    await _safe_ws_send(websocket, {
                        "type": "error",
                        "data": "Failed to connect to Grok Voice Agent API"
                    })

            elif msg_type == "disconnect":
                # Graceful disconnect
                session.intentional_disconnect = True
                break

            elif msg_type == "ping":
                # Keep-alive
                await _safe_ws_send(websocket, {"type": "pong"})

            else:
                # FIX: Isolate handler errors
                try:
                    await handle_grok_portal_message(session, data)
                except Exception as handler_err:
                    print(f"[GROK-LIVE] Handler error (non-fatal): {handler_err}")

    except WebSocketDisconnect:
        print(f"[GROK-LIVE] Portal WebSocket disconnected: {session_id}")
        session.portal_ws = None

    except Exception as e:
        print(f"[GROK-LIVE] WebSocket error: {e}")
        await _safe_ws_send(websocket, {
            "type": "error",
            "data": str(e)
        })
```

---

## Task 7: Restart server and verify

### Step 1: Restart the BlackBox service

```bash
sudo systemctl restart blackbox.service
```

### Step 2: Verify service is running

```bash
sleep 5 && sudo systemctl status blackbox.service --no-pager -l
```

### Step 3: Verify WebSocket endpoints respond

```bash
curl -s http://localhost:9091/realtime/status | python3 -m json.tool
curl -s http://localhost:9091/gemini-live/status | python3 -m json.tool
curl -s http://localhost:9091/grok-live/status | python3 -m json.tool
```

---

## Summary of All Fixes

| # | Issue | Fix | File |
|---|-------|-----|------|
| 1 | No audio_commit sent | Added `sendAudioCommit()`, called on mic stop and AI-speaking mute | VoiceClient.kt |
| 2 | No post-speech delay | Added `aiStoppedSpeakingAt` + 800ms delay before resuming mic | VoiceClient.kt + VoiceScreen.kt |
| 3 | No keepalive pings | Added 15s ping loop with 30s pong timeout | VoiceClient.kt |
| 4 | DROP_OLDEST buffer | Changed to SUSPEND + increased capacity to 1024 | WebSocketClient.kt |
| 5 | onFailure missing Disconnected | Added Disconnected emission before channel close | WebSocketClient.kt |
| 6 | AudioTrack.write blocks collector | Decoupled with ConcurrentLinkedQueue + dedicated drain coroutine | VoiceScreen.kt |
| 7 | No pre-buffering | Accumulate 12KB before first AudioTrack.write | VoiceScreen.kt |
| 8 | No AudioTrack sync | Added `audioTrackLock` synchronized block | VoiceScreen.kt |
| 9 | isRecordingAudio not volatile | Added `@Volatile` annotation | VoiceScreen.kt |
| 10 | No AudioRecord STATE check | Added check before startRecording() | VoiceScreen.kt |
| 11 | receive_json kills on bad frame | Changed to receive_text + json.loads with per-message catch | All 3 server files |
| 12 | No receive timeout | Added asyncio.wait_for with 120s timeout | All 3 server files |
| 13 | Handler errors kill session | Wrapped handler calls in try/except | All 3 server files |
