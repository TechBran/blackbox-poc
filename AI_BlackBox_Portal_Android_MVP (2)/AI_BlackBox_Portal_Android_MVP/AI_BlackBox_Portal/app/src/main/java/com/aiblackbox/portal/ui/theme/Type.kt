package com.aiblackbox.portal.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// =============================================================================
// Typography — aligned with Portal CSS _variables.css text scale
//
// CSS scale (rem → px):
//   --text-xs:  0.6875rem = 11px    --text-sm:   0.8125rem = 13px
//   --text-base: 0.9375rem = 15px   --text-lg:   1.125rem  = 18px
//   --text-xl:  1.375rem  = 22px    --text-2xl:  1.75rem   = 28px
//   --text-3xl: 2.25rem   = 36px    --text-4xl:  3rem      = 48px
//   --text-5xl: 4rem      = 64px
//
// Mobile adaptation: display sizes are scaled for phone screens.
// Body/label sizes match the Portal exactly (11, 13, 15sp).
//
// Font family: system sans-serif (matches Portal: ui-sans-serif, system-ui)
// Weight map:  Normal=400, Medium=500, SemiBold=600, Bold=700, ExtraBold=800
// =============================================================================

val BlackBoxTypography = Typography(

    // Display — scaled for mobile (Portal: 64/48/36px)
    displayLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.ExtraBold,
        fontSize = 48.sp,
        lineHeight = 56.sp,
    ),
    displayMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 36.sp,
        lineHeight = 44.sp,
    ),
    displaySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 28.sp,
        lineHeight = 36.sp,
    ),

    // Headline — maps to --text-xl (22px) and --text-lg (18px)
    headlineLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,
        lineHeight = 28.sp,
    ),
    headlineMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp,
        lineHeight = 24.sp,
    ),

    // Title — maps to --text-lg (18px) and --text-base (15px)
    titleLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp,
        lineHeight = 24.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 15.sp,
        lineHeight = 22.sp,
    ),

    // Body — maps to --text-base (15px), --text-sm (13px), --text-xs (11px)
    bodyLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 22.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,
        lineHeight = 18.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 11.sp,
        lineHeight = 16.sp,
    ),

    // Labels — utility text (buttons, badges, captions)
    labelLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 13.sp,
        lineHeight = 18.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 11.sp,
        lineHeight = 16.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 10.sp,
        lineHeight = 14.sp,
    ),
)

// =============================================================================
// Named Font Weights — for direct use matching Portal --font-* tokens
// =============================================================================
val FontWeightNormal    = FontWeight.Normal     // --font-normal: 400
val FontWeightMedium    = FontWeight.Medium      // --font-medium: 500
val FontWeightSemiBold  = FontWeight.SemiBold    // --font-semibold: 600
val FontWeightBold      = FontWeight.Bold        // --font-bold: 700
val FontWeightExtraBold = FontWeight.ExtraBold   // --font-extrabold: 800
