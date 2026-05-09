package com.aiblackbox.portal

import com.aiblackbox.portal.ui.chat.ChatViewModel
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.*
import org.junit.Test

/**
 * Plan Task 10 — Verify the WS dispatcher pattern that voice and agent
 * transports use to surface provenance events.
 *
 * Both ClaudeAgentClient.parseMessage and VoiceClient.parseMessage receive
 * the on-wire shape:
 *
 *   {"type":"provenance","data":{"recent":[...],"keyword":[...],"semantic":[...],"checkpoint":[...]}}
 *
 * They re-stringify the inner "data" JsonObject and route it through
 * ChatViewModel.parseProvenance (the same parser SSE uses, no duplication).
 *
 * These tests cover the routing logic independent of the WebSocket transport
 * machinery so they run as fast unit tests.
 */
class WebSocketProvenanceTest {

    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    /** Mirrors the exact extraction code in both WS dispatchers. */
    private fun extractProvenanceFromWsFrame(rawFrame: String): String? {
        val obj = json.parseToJsonElement(rawFrame).jsonObject
        if (obj["type"]?.toString()?.trim('"') != "provenance") return null
        // Both production dispatchers wrap the .jsonObject access in try/catch
        // because "data" can be a string primitive on other event types and
        // the cast throws IllegalArgumentException.
        return try { obj["data"]?.jsonObject?.toString() } catch (_: Exception) { null }
    }

    @Test fun `ws frame with type provenance and nested data object routes through parseProvenance`() {
        val frame = """
            {"type":"provenance","data":{"recent":["SNAP-A"],"keyword":["SNAP-B"],"semantic":["SNAP-C"],"checkpoint":["SNAP-D"]}}
        """.trimIndent()

        val raw = extractProvenanceFromWsFrame(frame)
        assertNotNull("extractor must recover the inner JSON object", raw)

        val parsed = ChatViewModel.parseProvenance(raw!!)
        assertNotNull(parsed)
        assertEquals(listOf("SNAP-A"), parsed!!.recent)
        assertEquals(listOf("SNAP-B"), parsed.keyword)
        assertEquals(listOf("SNAP-C"), parsed.semantic)
        assertEquals(listOf("SNAP-D"), parsed.checkpoint)
        assertEquals(4, parsed.totalCount())
        assertFalse(parsed.isEmpty())
    }

    @Test fun `ws frame with empty provenance still parses to non-null Provenance`() {
        val frame = """{"type":"provenance","data":{"recent":[],"keyword":[],"semantic":[],"checkpoint":[]}}"""
        val raw = extractProvenanceFromWsFrame(frame)
        val parsed = ChatViewModel.parseProvenance(raw!!)
        assertNotNull(parsed)
        assertTrue(parsed!!.isEmpty())
        assertEquals(0, parsed.totalCount())
    }

    @Test fun `ws frame with partial provenance fills missing fields with empty lists`() {
        val frame = """{"type":"provenance","data":{"recent":["X","Y"]}}"""
        val raw = extractProvenanceFromWsFrame(frame)
        val parsed = ChatViewModel.parseProvenance(raw!!)
        assertNotNull(parsed)
        assertEquals(listOf("X", "Y"), parsed!!.recent)
        assertEquals(emptyList<String>(), parsed.keyword)
        assertEquals(emptyList<String>(), parsed.semantic)
        assertEquals(emptyList<String>(), parsed.checkpoint)
    }

    @Test fun `non-provenance ws frames are ignored by the extractor`() {
        val frame = """{"type":"audio_delta","data":"Zm9v"}"""
        assertNull(extractProvenanceFromWsFrame(frame))
    }

    @Test fun `malformed inner data does not crash parseProvenance`() {
        // "data" is a string, not an object — extractor returns null
        val frame = """{"type":"provenance","data":"not-an-object"}"""
        val raw = extractProvenanceFromWsFrame(frame)
        assertNull("extractor must reject non-object data", raw)
    }

    @Test fun `parseProvenance is shared between SSE and WS paths (no duplicate parser)`() {
        // SSE delivers the inner object as the SSE data string
        val sseInner = """{"recent":["SSE-1"],"keyword":[],"semantic":["SSE-2"],"checkpoint":[]}"""
        // WS delivers the same shape wrapped in a frame; extractor recovers the inner string
        val wsFrame = """{"type":"provenance","data":$sseInner}"""

        val fromSse = ChatViewModel.parseProvenance(sseInner)
        val fromWs = ChatViewModel.parseProvenance(extractProvenanceFromWsFrame(wsFrame)!!)

        assertEquals(fromSse, fromWs)
    }
}
