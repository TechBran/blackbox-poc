package com.aiblackbox.portal.ui.theme

import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.unit.dp

// =============================================================================
// Radius Scale — ported from Portal CSS _variables.css
// =============================================================================
val RadiusXs   = 4.dp   // --radius-xs
val RadiusSm   = 6.dp   // --radius-sm
val RadiusMd   = 8.dp   // --radius-md
val RadiusLg   = 12.dp  // --radius-lg
val RadiusXl   = 16.dp  // --radius-xl
val Radius2xl  = 24.dp  // --radius-2xl
val RadiusFull = 9999.dp // --radius-full (pill / capsule)

// =============================================================================
// Material3 Shape Scheme
// =============================================================================
val BlackBoxShapes = Shapes(
    extraSmall = RoundedCornerShape(RadiusXs),
    small      = RoundedCornerShape(RadiusSm),
    medium     = RoundedCornerShape(RadiusMd),
    large      = RoundedCornerShape(RadiusLg),
    extraLarge = RoundedCornerShape(RadiusXl),
)

// =============================================================================
// Named Shapes — convenience for direct use across the app
// =============================================================================
val PillShape = RoundedCornerShape(RadiusFull)  // fully rounded ends (tags, chips, badges)

// Asymmetric Bubble Shapes — Portal's distinctive chat bubble look
// Assistant: sharp top-start corner, rounded everywhere else
// User:     sharp top-end corner, rounded everywhere else
val AssistantBubbleShape = RoundedCornerShape(
    topStart = 4.dp,
    topEnd = 18.dp,
    bottomEnd = 18.dp,
    bottomStart = 18.dp,
)

val UserBubbleShape = RoundedCornerShape(
    topStart = 18.dp,
    topEnd = 4.dp,
    bottomEnd = 18.dp,
    bottomStart = 18.dp,
)

// =============================================================================
// Extended Shape System — accessed via BlackBoxTheme.extendedShapes
// =============================================================================
@Immutable
data class BlackBoxExtendedShapes(
    val xs: Shape = RoundedCornerShape(RadiusXs),
    val sm: Shape = RoundedCornerShape(RadiusSm),
    val md: Shape = RoundedCornerShape(RadiusMd),
    val lg: Shape = RoundedCornerShape(RadiusLg),
    val xl: Shape = RoundedCornerShape(RadiusXl),
    val xxl: Shape = RoundedCornerShape(Radius2xl),
    val pill: Shape = PillShape,
    val circle: Shape = CircleShape,
    val bubbleAssistant: Shape = AssistantBubbleShape,
    val bubbleUser: Shape = UserBubbleShape,
)

val LocalBlackBoxShapes = staticCompositionLocalOf { BlackBoxExtendedShapes() }
