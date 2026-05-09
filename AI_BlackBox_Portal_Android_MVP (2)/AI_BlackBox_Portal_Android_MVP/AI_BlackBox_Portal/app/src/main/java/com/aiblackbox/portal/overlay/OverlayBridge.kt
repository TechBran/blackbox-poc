package com.aiblackbox.portal.overlay

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Singleton bridge between OverlayService (state producer) and XrOverlayActivity (state consumer).
 * On phone devices this is never used. On XR devices, the Service writes state here and the
 * Compose-based Activity observes it via StateFlow.
 */
object OverlayBridge {

    // ---- State ----

    data class TranscriptEntry(
        val speaker: String,
        val text: String,
        val timestamp: Long = System.currentTimeMillis()
    )

    data class OverlayState(
        // Connection
        val isConnected: Boolean = false,
        val isRecording: Boolean = false,
        val isAISpeaking: Boolean = false,
        val isScreenSharing: Boolean = false,
        val isExpanded: Boolean = false,
        val isUserMuted: Boolean = false,

        // Status
        val statusText: String = "Disconnected",

        // Selectors
        val currentModel: String = "Gemini Live",
        val currentVoice: String = "Charon",
        val currentOperator: String = "Brandon",

        // Lists for dropdowns
        val models: List<String> = listOf("Gemini Live", "GPT Realtime", "Grok Live"),
        val voices: List<String> = listOf("Charon", "Puck", "Kore", "Aoede", "Fenrir", "Orus"),
        val operators: List<String> = listOf("Brandon", "default"),

        // Transcript
        val transcriptEntries: List<TranscriptEntry> = emptyList(),
        val liveResponseText: String = "",

        // Progress
        val isConnecting: Boolean = false,

        // Lifecycle — set true when service is stopping so XR Activity can finish()
        val shouldFinish: Boolean = false
    )

    private val _state = MutableStateFlow(OverlayState())
    val state: StateFlow<OverlayState> = _state.asStateFlow()

    /** Atomically update state. Called from OverlayService on the main thread. */
    fun updateState(transform: (OverlayState) -> OverlayState) {
        _state.value = transform(_state.value)
    }

    /** Reset to defaults when service is destroyed. */
    fun reset() {
        _state.value = OverlayState()
        commandListener = null
    }

    // ---- Commands (XR Activity → Service) ----

    interface CommandListener {
        fun toggleConnect()
        fun toggleMic()
        fun toggleScreenShare()
        fun toggleExpanded()
        fun selectModel(model: String)
        fun selectVoice(voice: String)
        fun selectOperator(operator: String)
        fun openCamera()
        fun openPortal()
        fun minimize()
    }

    private var commandListener: CommandListener? = null

    fun registerCommandListener(listener: CommandListener) {
        commandListener = listener
    }

    fun unregisterCommandListener() {
        commandListener = null
    }

    // Command dispatchers — called from Compose UI
    fun toggleConnect() = commandListener?.toggleConnect()
    fun toggleMic() = commandListener?.toggleMic()
    fun toggleScreenShare() = commandListener?.toggleScreenShare()
    fun toggleExpanded() = commandListener?.toggleExpanded()
    fun selectModel(model: String) = commandListener?.selectModel(model)
    fun selectVoice(voice: String) = commandListener?.selectVoice(voice)
    fun selectOperator(operator: String) = commandListener?.selectOperator(operator)
    fun openCamera() = commandListener?.openCamera()
    fun openPortal() = commandListener?.openPortal()
    fun minimize() = commandListener?.minimize()
}
