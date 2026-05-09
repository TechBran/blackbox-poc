package com.aiblackbox.portal.ui.insets

import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * The measured height of the floating operator pill bubble plus a small
 * comfort margin. Screens that render content near the top of the
 * picker should consume this value as a top offset so their content
 * sits cleanly below the pill rather than being obscured by it.
 *
 * Default is 96.dp — the legacy hardcoded number from before this
 * abstraction. Provided by `BlackBoxTopBar` via `CompositionLocalProvider`
 * once the pill has been measured. Screens that aren't descendants of
 * the TopBar fall back to the default.
 *
 * Usage:
 *   val pillHeight = LocalOperatorPillHeight.current
 *   Modifier.padding(top = pillHeight, ...)
 *
 * See docs/plans/2026-05-09-cli-agent-codex-and-followups.md Track B
 * for the rationale and migration plan.
 */
val LocalOperatorPillHeight = compositionLocalOf<Dp> { 96.dp }
