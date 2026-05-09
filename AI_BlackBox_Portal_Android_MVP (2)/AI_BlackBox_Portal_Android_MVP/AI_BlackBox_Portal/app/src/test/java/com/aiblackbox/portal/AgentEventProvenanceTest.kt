package com.aiblackbox.portal

import com.aiblackbox.portal.data.agent.AgentEvent
import com.aiblackbox.portal.data.model.Provenance
import org.junit.Assert.*
import org.junit.Test

/**
 * Plan Task 10 — guards the agent WS event surface that AgentChatScreen
 * pattern-matches against. ProvenanceUpdate must carry a typed Provenance
 * (not a raw string) so the screen's handleEvent branch can store it on
 * UiMessage.provenance directly without re-parsing.
 */
class AgentEventProvenanceTest {

    @Test fun `AgentEvent ProvenanceUpdate carries a typed Provenance`() {
        val prov = Provenance(
            recent = listOf("SNAP-AR"),
            keyword = listOf("SNAP-AK"),
            semantic = listOf("SNAP-AS"),
            checkpoint = listOf("SNAP-AC"),
        )
        val event: AgentEvent = AgentEvent.ProvenanceUpdate(prov)
        assertTrue(event is AgentEvent.ProvenanceUpdate)
        val payload = (event as AgentEvent.ProvenanceUpdate).provenance
        assertEquals(prov, payload)
        assertEquals(4, payload.totalCount())
    }

    @Test fun `AgentEvent ProvenanceUpdate equality works for state diffing`() {
        val a = AgentEvent.ProvenanceUpdate(Provenance(recent = listOf("X")))
        val b = AgentEvent.ProvenanceUpdate(Provenance(recent = listOf("X")))
        val c = AgentEvent.ProvenanceUpdate(Provenance(recent = listOf("Y")))
        assertEquals(a, b)
        assertNotEquals(a, c)
    }
}
