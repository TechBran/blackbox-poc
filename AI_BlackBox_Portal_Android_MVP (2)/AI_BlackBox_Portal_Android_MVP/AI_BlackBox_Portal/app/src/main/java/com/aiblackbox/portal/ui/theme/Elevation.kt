package com.aiblackbox.portal.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

// =============================================================================
// Elevation & Shadow System — ported from Portal CSS _variables.css
//
// CSS shadow scale:
//   --shadow-xs:  0 1px 2px   rgba(0,0,0,0.30)
//   --shadow-sm:  0 2px 6px   rgba(0,0,0,0.25)
//   --shadow-md:  0 4px 16px  rgba(0,0,0,0.35)
//   --shadow-lg:  0 8px 32px  rgba(0,0,0,0.45)
//   --shadow-xl:  0 12px 48px rgba(0,0,0,0.55)
//
// CSS glow scale:
//   --shadow-glow-accent: 0 0 20px rgba(255,74,74,0.25)
//   --shadow-glow-blue:   0 0 20px rgba(74,158,255,0.25)
//   --shadow-glow-green:  0 0 20px rgba(39,217,128,0.25)
//
// In Compose, elevation is expressed as Dp for Material3 components and
// as shadow() modifier params for custom drawing. This file provides both.
// =============================================================================

/**
 * Shadow definition matching a Portal CSS box-shadow value.
 * Use with Modifier.shadow(elevation, shape, ambientColor, spotColor)
 * or with Canvas drawShadow for precise control.
 */
@Immutable
data class ShadowSpec(
    val elevation: Dp,
    val alpha: Float,
)

/**
 * Glow definition for accent/colored soft shadows.
 * Applied via Modifier.shadow with the glow color as ambientColor.
 */
@Immutable
data class GlowSpec(
    val radius: Dp,
    val color: Color,
)

@Immutable
data class BlackBoxElevation(
    // Shadow scale — maps CSS blur radius to Compose elevation
    // Compose elevation approximates shadow spread, not a 1:1 blur match,
    // but these values produce visually equivalent depth cues on OLED screens.
    val xs: ShadowSpec = ShadowSpec(elevation = 1.dp, alpha = 0.30f),
    val sm: ShadowSpec = ShadowSpec(elevation = 3.dp, alpha = 0.25f),
    val md: ShadowSpec = ShadowSpec(elevation = 8.dp, alpha = 0.35f),
    val lg: ShadowSpec = ShadowSpec(elevation = 16.dp, alpha = 0.45f),
    val xl: ShadowSpec = ShadowSpec(elevation = 24.dp, alpha = 0.55f),

    // Glow presets — colored ambient shadows for interactive elements
    val glowAccent: GlowSpec = GlowSpec(radius = 20.dp, color = GlowAccent),
    val glowBlue: GlowSpec = GlowSpec(radius = 20.dp, color = GlowBlue),
    val glowGreen: GlowSpec = GlowSpec(radius = 20.dp, color = GlowGreen),

    // Material3 tonal elevation shortcuts (for surfaceColorAtElevation)
    val level0: Dp = 0.dp,
    val level1: Dp = 1.dp,
    val level2: Dp = 3.dp,
    val level3: Dp = 6.dp,
    val level4: Dp = 8.dp,
    val level5: Dp = 12.dp,
)

val LocalBlackBoxElevation = staticCompositionLocalOf { BlackBoxElevation() }
