package com.aiblackbox.portal.ui.theme

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.Easing
import androidx.compose.animation.core.SpringSpec
import androidx.compose.animation.core.TweenSpec
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf

// =============================================================================
// Animation Tokens — ported from Portal CSS _variables.css
//
// CSS transitions:
//   --transition-fast:   0.15s ease
//   --transition-base:   0.2s ease
//   --transition-slow:   0.3s ease
//   --transition-spring: 0.25s cubic-bezier(0.4, 0, 0.2, 1)
//
// Compose equivalents use tween() for CSS ease and spring() for physics.
// The CSS cubic-bezier(0.4, 0, 0.2, 1) is Material's standard easing.
// =============================================================================

// Easing curves
val EaseStandard = CubicBezierEasing(0.4f, 0.0f, 0.2f, 1.0f) // Material standard
val EaseIn       = CubicBezierEasing(0.4f, 0.0f, 1.0f, 1.0f)
val EaseOut      = CubicBezierEasing(0.0f, 0.0f, 0.2f, 1.0f)
val EaseInOut    = CubicBezierEasing(0.4f, 0.0f, 0.2f, 1.0f) // same as standard

// Duration constants (ms)
const val DurationFast   = 150  // --transition-fast: 0.15s
const val DurationBase   = 200  // --transition-base: 0.2s
const val DurationSlow   = 300  // --transition-slow: 0.3s
const val DurationSpring = 250  // --transition-spring: 0.25s

@Immutable
data class BlackBoxAnimation(
    // Tween specs matching CSS transitions
    val fast: Int = DurationFast,
    val base: Int = DurationBase,
    val slow: Int = DurationSlow,

    // Standard easing (CSS "ease" is close to Material standard)
    val easing: Easing = EaseStandard,

    // Spring physics — for interactive elements that benefit from bounce
    // dampingRatio: 0.7 gives a subtle bounce; stiffness matches ~250ms settle
    val springDampingRatio: Float = 0.7f,
    val springStiffness: Float = 400f,
) {
    // Pre-built animation specs for common use
    fun <T> tweenFast(): TweenSpec<T> = tween(durationMillis = fast, easing = easing)
    fun <T> tweenBase(): TweenSpec<T> = tween(durationMillis = base, easing = easing)
    fun <T> tweenSlow(): TweenSpec<T> = tween(durationMillis = slow, easing = easing)
    fun <T> springDefault(): SpringSpec<T> = spring(
        dampingRatio = springDampingRatio,
        stiffness = springStiffness,
    )
}

val LocalBlackBoxAnimation = staticCompositionLocalOf { BlackBoxAnimation() }
