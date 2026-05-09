package com.aiblackbox.portal.ui.theme

import android.app.Activity
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

// =============================================================================
// Dark Color Scheme — maps Portal CSS tokens onto Material3 color roles
// =============================================================================
private val BlackBoxColorScheme = darkColorScheme(
    primary = BbxAccent,
    onPrimary = BbxWhite,
    primaryContainer = BbxRed,
    onPrimaryContainer = BbxWhite,

    background = BbxBlack,
    onBackground = BbxWhite,

    surface = BbxBlack,
    onSurface = BbxWhite,
    surfaceVariant = Neutral100,
    onSurfaceVariant = BbxDim,

    surfaceContainerLowest = BbxBlack,
    surfaceContainerLow = Neutral50,
    surfaceContainer = Neutral100,
    surfaceContainerHigh = Neutral150,
    surfaceContainerHighest = Neutral200,

    outline = Neutral300,
    outlineVariant = Neutral200,

    error = BbxAccent,
    onError = BbxWhite,
)

// =============================================================================
// Extended token instances (singleton — all values are immutable defaults)
// =============================================================================
private val ExtendedColors = BlackBoxExtendedColors()
private val ExtendedShapes = BlackBoxExtendedShapes()
private val ExtendedElevation = BlackBoxElevation()
private val ExtendedAnimation = BlackBoxAnimation()
private val ExtendedGlass = BlackBoxGlass()

// =============================================================================
// BlackBoxTheme — single composable entry point for the design system
//
// Provides both Material3 theming AND extended tokens via CompositionLocal.
// Access extended tokens with:
//   BlackBoxTheme.extendedColors.accent
//   BlackBoxTheme.elevation.md
//   BlackBoxTheme.animation.fast
//   BlackBoxTheme.glass.blurMd
//   BlackBoxTheme.extendedShapes.pill
// =============================================================================
@Composable
fun BlackBoxTheme(
    content: @Composable () -> Unit,
) {
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = BbxBlack.toArgb()
            window.navigationBarColor = BbxBlack.toArgb()
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = false
                isAppearanceLightNavigationBars = false
            }
        }
    }

    CompositionLocalProvider(
        LocalBlackBoxColors provides ExtendedColors,
        LocalBlackBoxShapes provides ExtendedShapes,
        LocalBlackBoxElevation provides ExtendedElevation,
        LocalBlackBoxAnimation provides ExtendedAnimation,
        LocalBlackBoxGlass provides ExtendedGlass,
    ) {
        MaterialTheme(
            colorScheme = BlackBoxColorScheme,
            typography = BlackBoxTypography,
            shapes = BlackBoxShapes,
            content = content,
        )
    }
}

// =============================================================================
// BlackBoxTheme accessor object — the idiomatic Compose way to read tokens
//
// Usage anywhere inside BlackBoxTheme { ... }:
//   val accent = BlackBoxTheme.extendedColors.accent
//   val pillShape = BlackBoxTheme.extendedShapes.pill
//   val mdShadow = BlackBoxTheme.elevation.md
//   val fastMs = BlackBoxTheme.animation.fast
//   val blurLg = BlackBoxTheme.glass.blurLg
// =============================================================================
object BlackBoxTheme {
    val extendedColors: BlackBoxExtendedColors
        @Composable @ReadOnlyComposable
        get() = LocalBlackBoxColors.current

    val extendedShapes: BlackBoxExtendedShapes
        @Composable @ReadOnlyComposable
        get() = LocalBlackBoxShapes.current

    val elevation: BlackBoxElevation
        @Composable @ReadOnlyComposable
        get() = LocalBlackBoxElevation.current

    val animation: BlackBoxAnimation
        @Composable @ReadOnlyComposable
        get() = LocalBlackBoxAnimation.current

    val glass: BlackBoxGlass
        @Composable @ReadOnlyComposable
        get() = LocalBlackBoxGlass.current
}
