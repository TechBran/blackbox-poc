package com.aiblackbox.portal.data.model

import kotlinx.serialization.Serializable
import java.util.UUID

@Serializable
data class UiMessage(
    val id: String = UUID.randomUUID().toString(),
    val role: String,                    // "user" or "assistant"
    val content: String,                 // rendered text content
    val reasoning: String? = null,       // thinking/reasoning (expandable)
    val timestamp: Long = System.currentTimeMillis(),
    val isStreaming: Boolean = false,     // currently being streamed
    val isThinking: Boolean = false,     // currently in thinking phase
    val model: String? = null,
    val provider: String? = null,
    val images: List<String> = emptyList(),       // attached image URLs
    val attachments: List<String> = emptyList(),  // other file URLs
    val tokens: TokenCount? = null,
    val mediaTasks: List<String> = emptyList(),   // pending image/video/music task IDs
    val provenance: Provenance? = null,            // typed retrieval provenance (recent/keyword/semantic/checkpoint)
    val ttsAudioUrl: String? = null,              // URL of generated TTS audio for inline player
    val ttsGenerating: Boolean = false             // true while TTS is being generated
)
