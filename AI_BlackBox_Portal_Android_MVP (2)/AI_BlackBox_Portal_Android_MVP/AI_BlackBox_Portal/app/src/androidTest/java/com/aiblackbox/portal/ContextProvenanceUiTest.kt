package com.aiblackbox.portal

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import com.aiblackbox.portal.data.model.Provenance
import com.aiblackbox.portal.ui.components.ContextProvenance
import org.junit.Rule
import org.junit.Test

class ContextProvenanceUiTest {
    @get:Rule val composeTestRule = createComposeRule()

    @Test fun contextProvenance_empty_renders_nothing() {
        composeTestRule.setContent {
            ContextProvenance(provenance = Provenance(), expanded = false, onToggle = {})
        }
        composeTestRule.onNodeWithTag("context-provenance-root")
            .assertDoesNotExist()
    }

    @Test fun contextProvenance_populated_shows_count_label() {
        val p = Provenance(recent = listOf("SNAP-A"), semantic = listOf("SNAP-B"))
        composeTestRule.setContent {
            ContextProvenance(provenance = p, expanded = false, onToggle = {})
        }
        composeTestRule
            .onNodeWithContentDescription("Show context snapshots, 2 total", substring = true)
            .assertIsDisplayed()
    }

    @Test fun contextProvenance_expanded_shows_sections() {
        val p = Provenance(
            recent = listOf("SNAP-R"),
            semantic = listOf("SNAP-S"),
            checkpoint = listOf("SNAP-C"),
        )
        composeTestRule.setContent {
            ContextProvenance(provenance = p, expanded = true, onToggle = {})
        }
        composeTestRule.onNodeWithText("Recent").assertIsDisplayed()
        composeTestRule.onNodeWithText("Semantic").assertIsDisplayed()
        composeTestRule.onNodeWithText("Checkpoint").assertIsDisplayed()
        // Implementation strips the "SNAP-" prefix from chip text, so chip reads "R" for "SNAP-R".
        composeTestRule.onNodeWithText("R").assertIsDisplayed()
    }
}
