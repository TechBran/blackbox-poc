package com.aiblackbox.portal.data.model

enum class ChatProvider(val id: String, val displayName: String) {
    GEMINI("gemini", "Gemini"),
    ANTHROPIC("anthropic", "Anthropic"),
    OPENAI("openai", "OpenAI"),
    XAI("xai", "xAI"),
    AGENTS("agents", "Claude Code"),
    GEMINI_AGENTS("gemini-agents", "Gemini CLI"),
    REALTIME("realtime", "GPT Realtime"),
    GEMINI_LIVE("gemini-live", "Gemini Live"),
    GROK_LIVE("grok-live", "Grok Live"),
    ROBOTICS("robotics", "Robotics (ER)"),
    COMPUTER_USE("computer-use", "Computer Use");

    companion object {
        fun fromId(id: String): ChatProvider = entries.find { it.id == id } ?: GEMINI
    }

    val isAgent get() = this == AGENTS || this == GEMINI_AGENTS
    val isVoice get() = this == REALTIME || this == GEMINI_LIVE || this == GROK_LIVE
    val isRobotics get() = this == ROBOTICS
    val isStreaming get() = !isAgent && !isVoice
}
