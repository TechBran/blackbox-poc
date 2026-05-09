package com.aiblackbox.portal

import com.aiblackbox.portal.data.model.Provenance
import com.aiblackbox.portal.data.model.UiMessage
import com.aiblackbox.portal.ui.chat.ChatViewModel
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test

class ProvenanceSerializationTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun `parses all four fields from backend`() {
        val payload = """{"recent":["A"],"keyword":["B"],"semantic":["C"],"checkpoint":["D"]}"""
        val p = json.decodeFromString(Provenance.serializer(), payload)
        assertEquals(listOf("A"), p.recent)
        assertEquals(listOf("B"), p.keyword)
        assertEquals(listOf("C"), p.semantic)
        assertEquals(listOf("D"), p.checkpoint)
    }

    @Test fun `UiMessage carries typed Provenance not raw string`() {
        val msg = UiMessage(
            role = "assistant",
            content = "hi",
            provenance = Provenance(recent = listOf("SNAP-X"))
        )
        assertNotNull(msg.provenance)
        assertEquals(1, msg.provenance!!.recent.size)
    }

    @Test fun `round-trips through JSON with all four fields preserved`() {
        val original = Provenance(
            recent = listOf("SNAP-R"),
            keyword = listOf("SNAP-K"),
            semantic = listOf("SNAP-S"),
            checkpoint = listOf("SNAP-C"),
        )
        val encoded = json.encodeToString(Provenance.serializer(), original)
        val decoded = json.decodeFromString(Provenance.serializer(), encoded)
        assertEquals(original, decoded)
    }

    @Test fun `decodes payload with missing fields using emptyList defaults`() {
        val partial = """{"recent":["A"]}"""
        val p = json.decodeFromString(Provenance.serializer(), partial)
        assertEquals(listOf("A"), p.recent)
        assertEquals(emptyList<String>(), p.keyword)
        assertEquals(emptyList<String>(), p.semantic)
        assertEquals(emptyList<String>(), p.checkpoint)
    }

    @Test fun `ChatViewModel parses provenance event into typed Provenance`() {
        val raw = """{"recent":["SNAP-1"],"keyword":[],"semantic":["SNAP-2"],"checkpoint":["SNAP-3"]}"""
        val parsed = ChatViewModel.parseProvenance(raw)
        assertNotNull(parsed)
        assertEquals(1, parsed!!.recent.size)
        assertEquals(1, parsed.semantic.size)
        assertEquals(1, parsed.checkpoint.size)
    }

    // Regression for the bug Brandon hit on first Android-rebuild test (2026-04-25):
    // backend emits provenance INSIDE the stream_start event's data payload
    // (chat_routes.py:5953/6035), not as a standalone "provenance" SSE event.
    // Android was matching on event.event=="provenance" which never fired.
    @Test fun `extractProvenanceFromStreamStart pulls embedded provenance from stream_start data`() {
        val streamStartData =
            """{"provider":"gemini","model":"gemini-3.1-pro","provenance":""" +
            """{"recent":["SNAP-R1","SNAP-R2"],"keyword":["SNAP-K1"],"semantic":["SNAP-S1"],"checkpoint":["SNAP-C1","SNAP-C2"]}}"""
        val parsed = ChatViewModel.extractProvenanceFromStreamStart(streamStartData)
        assertNotNull(parsed)
        assertEquals(2, parsed!!.recent.size)
        assertEquals(1, parsed.keyword.size)
        assertEquals(1, parsed.semantic.size)
        assertEquals(2, parsed.checkpoint.size)
    }

    @Test fun `extractProvenanceFromStreamStart returns null when stream_start has no provenance field`() {
        val noProv = """{"provider":"gemini","model":"gemini-3.1-pro"}"""
        assertNull(ChatViewModel.extractProvenanceFromStreamStart(noProv))
    }

    @Test fun `extractProvenanceFromStreamStart returns null on malformed input`() {
        assertNull(ChatViewModel.extractProvenanceFromStreamStart("not json at all"))
        assertNull(ChatViewModel.extractProvenanceFromStreamStart(""))
    }
}
