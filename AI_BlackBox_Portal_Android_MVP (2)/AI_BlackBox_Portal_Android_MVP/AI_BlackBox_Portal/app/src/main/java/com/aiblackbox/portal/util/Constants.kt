package com.aiblackbox.portal.util

object Constants {
    const val PREFS_NAME = "bbx_prefs"
    const val KEY_ORIGIN = "origin"
    const val KEY_OPERATOR = "operator"
    const val KEY_USE_NATIVE = "use_native_ui"

    // DataStore keys
    const val DS_PROVIDER = "provider"
    const val DS_MODEL = "model"
    const val DS_STREAMING = "streaming_enabled"
    const val DS_CLAUDE_MODEL = "claude_model"
    const val DS_TTS_VOICE = "tts_voice"

    // API paths
    const val API_CHAT = "/chat"
    const val API_CHAT_STREAM = "/chat/stream"
    const val API_CHAT_SAVE = "/chat/save"
    const val API_UPLOAD = "/upload"
    const val API_HEALTH = "/health"
    const val API_MODELS = "/models"
    const val API_OPERATORS = "/operators"
    const val API_OPERATOR_PREFS = "/operator/preferences"
    const val API_TASKS_LIST = "/tasks/list"
    const val API_TASKS_STATUS = "/tasks/status"
    const val API_AGENT_SESSION = "/agent/session"
    const val API_AGENT_APPS = "/agent/apps"
    const val API_AGENT_COMMANDS = "/agent/commands"
    const val API_GENERATE_IMAGE = "/generate/image"
    const val API_GENERATE_VIDEO = "/generate/video"
    const val API_GENERATE_MUSIC = "/generate/lyria_music"
    const val API_GENERATE_GOOGLE_TTS = "/generate/google_ssml"
    const val API_GENERATE_GEMINI_TTS = "/generate/gemini_tts"
    const val API_TTS = "/tts"
    const val API_TTS_VOICES = "/tts/voices"
    const val API_STT = "/stt"
    const val API_TIMELINE = "/timeline"
    const val API_ASSERT = "/assert"
    const val API_MEDIA_LIST = "/api/media/list"
    const val API_DEVICES = "/devices/"
    const val API_CRON_JOBS = "/api/cron/jobs"
    const val API_ASTERISK = "/asterisk"
    const val API_INTERNET = "/internet"
    const val API_BROWSER_SCREENSHOT = "/browser/screenshot"
    const val API_FOSSIL_HYBRID = "/fossil/hybrid"

    // WebSocket paths
    const val WS_AGENT = "/ws/agent"
    const val WS_GEMINI_AGENT = "/ws/gemini-agent"

    // Default values
    const val DEFAULT_PROVIDER = "gemini"
    const val DEFAULT_OPERATOR = "Brandon"

    // Client identification header
    const val CLIENT_HEADER = "X-BlackBox-Client"
    const val CLIENT_ID = "native-android/1.0"

    /**
     * Model options per provider.
     * Each entry is provider-id -> list of (modelId, displayName).
     * Empty modelId ("") means "Auto - Latest" (server picks best).
     */
    val MODEL_CONFIG: Map<String, List<Pair<String, String>>> = mapOf(
        "gemini" to listOf(
            "" to "Auto - Latest",
            "gemini-3.1-pro-preview-customtools" to "Gemini 3.1 Pro Custom Tools",
            "gemini-3.1-pro-preview" to "Gemini 3.1 Pro",
            "gemini-2.5-pro" to "Gemini 2.5 Pro",
            "gemini-2.5-flash" to "Gemini 2.5 Flash",
            "gemini-2.5-flash-lite" to "Gemini 2.5 Flash-Lite"
        ),
        "openai" to listOf(
            "" to "Auto - Latest",
            "gpt-5.1" to "GPT-5.1",
            "gpt-5" to "GPT-5",
            "gpt-5-mini" to "GPT-5 Mini",
            "o3" to "o3 (Reasoning)",
            "o4-mini" to "o4-mini (Reasoning)",
            "gpt-4o" to "GPT-4o"
        ),
        "anthropic" to listOf(
            "" to "Auto - Latest",
            "claude-opus-4-7" to "Claude Opus 4.7 (1M ctx, adaptive thinking)",
            "claude-opus-4-6" to "Claude Opus 4.6",
            "claude-sonnet-4-6" to "Claude Sonnet 4.6",
            "claude-sonnet-4-5" to "Claude Sonnet 4.5",
            "claude-haiku-4-5" to "Claude Haiku 4.5",
            "claude-opus-4-1" to "Claude Opus 4.1",
            "claude-sonnet-4" to "Claude Sonnet 4"
        ),
        // T3 (2026-05-18): xai entries were MALFORMED — "grok-4", "grok-4.1-fast",
        // "grok-3-mini" don't exist in the xAI API and would silently fail at
        // chat-completion time. Refreshed to real API IDs matching current 2026
        // catalog. These are OFFLINE-BOOTSTRAP fallbacks only — ChatViewModel's
        // fetchLiveModels() hydrates the dropdown from /models/xai on init.
        "xai" to listOf(
            "" to "Auto - Latest",
            "grok-4.3" to "Grok 4.3",
            "grok-4.20-multi-agent-0309" to "Grok 4.20 Multi-Agent",
            "grok-3-mini-beta" to "Grok 3 Mini (legacy)"
        ),
        "agents" to listOf(
            "sonnet" to "Sonnet",
            "opus" to "Opus",
            "haiku" to "Haiku"
        ),
        "gemini-agents" to listOf(
            "gemini-3.1-pro-preview" to "Gemini 3.1 Pro"
        ),
        "realtime" to listOf(
            "" to "GPT Realtime (GA)",
            "gpt-4o-realtime-preview" to "GPT-4o Realtime Preview"
        ),
        "gemini-live" to listOf(
            "" to "Gemini Live"
        ),
        "grok-live" to listOf(
            "" to "Grok Live"
        ),
        "computer-use" to listOf(
            "claude-opus-4-7" to "Claude Opus 4.7 (Sovereign)",
            "claude-opus-4-6" to "Claude Opus 4.6",
            "claude-sonnet-4-6" to "Claude Sonnet 4.6",
            "claude-sonnet-4-5" to "Claude Sonnet 4.5",
            "gemini-2.5-computer-use-preview-10-2025" to "Gemini CU Preview"
        ),
        "robotics" to listOf(
            "gemini-robotics-er-1.5-preview" to "Gemini Robotics-ER 1.5"
        )
    )
}
