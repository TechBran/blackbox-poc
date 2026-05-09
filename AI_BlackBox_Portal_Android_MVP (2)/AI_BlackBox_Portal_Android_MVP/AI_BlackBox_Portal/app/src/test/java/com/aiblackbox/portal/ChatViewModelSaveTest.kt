package com.aiblackbox.portal

import com.aiblackbox.portal.data.model.Provenance
import com.aiblackbox.portal.data.model.SaveRequest
import com.aiblackbox.portal.data.model.TokenCount
import com.aiblackbox.portal.ui.chat.ChatViewModel
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Test

/**
 * Plan Task 9 — Verifies provenance threads through the save path.
 *
 * Strategy: rather than instantiating AndroidViewModel (which requires
 * Robolectric / instrumentation and a kotlinx-coroutines-test dep we don't
 * have), we test ChatViewModel.buildSaveRequest — a @VisibleForTesting pure
 * builder that the production saveConversation() path delegates to. Proving
 * this builder forwards the typed Provenance into SaveRequest is enough,
 * because the call site (ChatViewModel.kt:593) is a literal forward.
 */
class ChatViewModelSaveTest {

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    @Test fun `buildSaveRequest forwards provenance when present`() {
        val req = ChatViewModel.buildSaveRequest(
            operator = "Brandon",
            userMessage = "hi",
            assistantResponse = "hello",
            reasoning = "",
            model = "gemini",
            tokens = null,
            provenance = Provenance(recent = listOf("SNAP-Z"))
        )
        assertNotNull(req.provenance)
        assertEquals(listOf("SNAP-Z"), req.provenance!!.recent)
    }

    @Test fun `buildSaveRequest sets provenance to null when absent`() {
        val req = ChatViewModel.buildSaveRequest(
            operator = "Brandon",
            userMessage = "hi",
            assistantResponse = "hello",
            reasoning = "",
            model = "gemini",
            tokens = null,
            provenance = null
        )
        assertNull(req.provenance)
    }

    @Test fun `buildSaveRequest preserves all four provenance fields`() {
        val prov = Provenance(
            recent = listOf("SNAP-R"),
            keyword = listOf("SNAP-K"),
            semantic = listOf("SNAP-S"),
            checkpoint = listOf("SNAP-C"),
        )
        val req = ChatViewModel.buildSaveRequest(
            operator = "Brandon",
            userMessage = "u",
            assistantResponse = "a",
            reasoning = "",
            model = null,
            tokens = TokenCount(prompt = 10, completion = 20),
            provenance = prov
        )
        assertEquals(prov, req.provenance)
    }

    @Test fun `SaveRequest with provenance serializes the lineage to JSON for chat save`() {
        val req = ChatViewModel.buildSaveRequest(
            operator = "Brandon",
            userMessage = "hi",
            assistantResponse = "hello",
            reasoning = "",
            model = "gemini",
            tokens = null,
            provenance = Provenance(
                recent = listOf("SNAP-A"),
                semantic = listOf("SNAP-B"),
            )
        )
        val encoded = json.encodeToString(SaveRequest.serializer(), req)
        val obj = json.parseToJsonElement(encoded).jsonObject
        val prov = obj["provenance"]?.jsonObject
        assertNotNull("provenance must be in serialized payload", prov)
        assertEquals(
            "SNAP-A",
            prov!!["recent"]!!.jsonArray[0].jsonPrimitive.content
        )
        assertEquals(
            "SNAP-B",
            prov["semantic"]!!.jsonArray[0].jsonPrimitive.content
        )
    }

    @Test fun `reasoning is null when blank in built SaveRequest`() {
        val req = ChatViewModel.buildSaveRequest(
            operator = "Brandon",
            userMessage = "u",
            assistantResponse = "a",
            reasoning = "   ",
            model = null,
            tokens = null,
            provenance = null
        )
        assertNull(req.reasoning)
    }

    @Test fun `reasoning is preserved when non-blank`() {
        val req = ChatViewModel.buildSaveRequest(
            operator = "Brandon",
            userMessage = "u",
            assistantResponse = "a",
            reasoning = "thought process",
            model = null,
            tokens = null,
            provenance = null
        )
        assertEquals("thought process", req.reasoning)
    }
}
