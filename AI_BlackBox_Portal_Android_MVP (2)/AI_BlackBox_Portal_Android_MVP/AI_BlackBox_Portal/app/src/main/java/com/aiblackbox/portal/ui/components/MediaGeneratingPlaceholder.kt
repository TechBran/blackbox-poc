package com.aiblackbox.portal.ui.components

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aiblackbox.portal.ui.theme.BbxAccent
import com.aiblackbox.portal.ui.theme.BbxWhite
import com.aiblackbox.portal.ui.theme.GlassBorder
import com.aiblackbox.portal.ui.theme.Neutral100
import com.aiblackbox.portal.ui.theme.Neutral200
import com.aiblackbox.portal.ui.theme.Neutral500
import com.aiblackbox.portal.ui.theme.RadiusLg
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

// =============================================================================
// MediaGeneratingPlaceholder — animated placeholders for in-progress generation
//
// Three distinct animations:
//   Image: orbiting particles around a frame icon
//   Video: film reel with spinning frames
//   Music: pulsing audio waveform bars
// =============================================================================

private val ImageAccent = Color(0xFF7C4DFF)   // Purple for image
private val VideoAccent = Color(0xFFFF6D00)    // Orange for video
private val MusicAccent = Color(0xFF00E5FF)    // Cyan for music

@Composable
fun MediaGeneratingPlaceholder(
    taskType: String,
    modifier: Modifier = Modifier
) {
    val (accent, icon, label) = when {
        taskType.contains("image", true) -> Triple(ImageAccent, "\uD83C\uDFA8", "Generating image")
        taskType.contains("video", true) -> Triple(VideoAccent, "\uD83C\uDFA5", "Generating video")
        taskType.contains("music", true) || taskType.contains("audio", true) ->
            Triple(MusicAccent, "\uD83C\uDFB5", "Generating music")
        else -> Triple(BbxAccent, "\u2699\uFE0F", "Processing")
    }

    // Clean minimal look — just the animation on the chat bubble background, no box or border
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        // Animated visual
        when {
            taskType.contains("image", true) -> ImageGeneratingAnimation(accent)
            taskType.contains("video", true) -> VideoGeneratingAnimation(accent)
            taskType.contains("music", true) || taskType.contains("audio", true) ->
                MusicGeneratingAnimation(accent)
            else -> SpinnerAnimation(accent)
        }

        Spacer(Modifier.height(12.dp))

        // Label with pulsing dots
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center
        ) {
            Text(icon, fontSize = 16.sp)
            Spacer(Modifier.width(8.dp))
            Text(
                label,
                style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Medium),
                color = BbxWhite
            )
            PulsingDots(accent)
        }
    }
}

// ── Image: Orbiting particles around a spinning ring ──
@Composable
private fun ImageGeneratingAnimation(accent: Color) {
    val transition = rememberInfiniteTransition(label = "imgAnim")
    val rotation by transition.animateFloat(
        initialValue = 0f, targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(3000, easing = LinearEasing)),
        label = "rot"
    )
    val pulse by transition.animateFloat(
        initialValue = 0.5f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(1200), RepeatMode.Reverse),
        label = "pulse"
    )

    Canvas(modifier = Modifier.size(80.dp)) {
        val center = Offset(size.width / 2, size.height / 2)
        val radius = size.minDimension / 2.5f

        // Outer ring
        drawCircle(
            color = accent.copy(alpha = 0.15f),
            radius = radius + 8f,
            center = center,
            style = Stroke(width = 2f)
        )

        // Spinning arc
        drawArc(
            color = accent.copy(alpha = pulse),
            startAngle = rotation,
            sweepAngle = 120f,
            useCenter = false,
            style = Stroke(width = 3f, cap = StrokeCap.Round),
            topLeft = Offset(center.x - radius, center.y - radius),
            size = androidx.compose.ui.geometry.Size(radius * 2, radius * 2)
        )

        // Orbiting particles (3 particles at 120 degree offsets)
        for (i in 0..2) {
            val angle = Math.toRadians((rotation + i * 120.0)).toFloat()
            val px = center.x + radius * cos(angle)
            val py = center.y + radius * sin(angle)
            drawCircle(
                color = accent.copy(alpha = 0.6f + pulse * 0.4f),
                radius = 4f + pulse * 2f,
                center = Offset(px, py)
            )
        }

        // Center icon area
        drawCircle(
            color = accent.copy(alpha = 0.1f),
            radius = radius * 0.5f,
            center = center
        )
    }
}

// ── Video: Spinning film reel segments ──
@Composable
private fun VideoGeneratingAnimation(accent: Color) {
    val transition = rememberInfiniteTransition(label = "vidAnim")
    val rotation by transition.animateFloat(
        initialValue = 0f, targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(2000, easing = LinearEasing)),
        label = "rot"
    )
    val frameAlpha by transition.animateFloat(
        initialValue = 0.3f, targetValue = 0.9f,
        animationSpec = infiniteRepeatable(tween(500), RepeatMode.Reverse),
        label = "frame"
    )

    Canvas(modifier = Modifier.size(80.dp)) {
        val center = Offset(size.width / 2, size.height / 2)
        val outerR = size.minDimension / 2.5f
        val innerR = outerR * 0.55f

        // Outer ring
        drawCircle(
            color = accent.copy(alpha = 0.2f),
            radius = outerR,
            center = center,
            style = Stroke(width = 6f)
        )

        // Film frames (8 segments)
        for (i in 0..7) {
            val angle = Math.toRadians((rotation + i * 45.0)).toFloat()
            val segAlpha = if (i % 2 == 0) frameAlpha else 1f - frameAlpha
            val px = center.x + (innerR + outerR) / 2 * cos(angle)
            val py = center.y + (innerR + outerR) / 2 * sin(angle)
            drawCircle(
                color = accent.copy(alpha = segAlpha * 0.6f),
                radius = 5f,
                center = Offset(px, py)
            )
        }

        // Center hub
        drawCircle(color = accent.copy(alpha = 0.15f), radius = innerR, center = center)
        drawCircle(color = accent.copy(alpha = 0.3f), radius = 6f, center = center)
    }
}

// ── Music: Pulsing equalizer bars ──
@Composable
private fun MusicGeneratingAnimation(accent: Color) {
    val transition = rememberInfiniteTransition(label = "musAnim")
    val bars = (0..6).map { i ->
        transition.animateFloat(
            initialValue = 0.2f, targetValue = 1f,
            animationSpec = infiniteRepeatable(
                tween(400 + i * 80, easing = LinearEasing),
                RepeatMode.Reverse
            ),
            label = "bar$i"
        )
    }

    Canvas(modifier = Modifier.size(80.dp, 60.dp)) {
        val barWidth = size.width / 14f
        val gap = barWidth * 0.8f
        val totalWidth = bars.size * barWidth + (bars.size - 1) * gap
        val startX = (size.width - totalWidth) / 2

        bars.forEachIndexed { i, anim ->
            val barHeight = size.height * 0.8f * anim.value
            val x = startX + i * (barWidth + gap)
            val y = (size.height - barHeight) / 2

            drawRoundRect(
                color = accent.copy(alpha = 0.4f + anim.value * 0.5f),
                topLeft = Offset(x, y),
                size = androidx.compose.ui.geometry.Size(barWidth, barHeight),
                cornerRadius = androidx.compose.ui.geometry.CornerRadius(barWidth / 2)
            )
        }
    }
}

// ── Generic spinner ──
@Composable
private fun SpinnerAnimation(accent: Color) {
    val transition = rememberInfiniteTransition(label = "spin")
    val rotation by transition.animateFloat(
        initialValue = 0f, targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(1500, easing = LinearEasing)),
        label = "rot"
    )

    Canvas(modifier = Modifier.size(60.dp)) {
        val center = Offset(size.width / 2, size.height / 2)
        val radius = size.minDimension / 3f
        drawArc(
            color = accent,
            startAngle = rotation,
            sweepAngle = 90f,
            useCenter = false,
            style = Stroke(width = 3f, cap = StrokeCap.Round),
            topLeft = Offset(center.x - radius, center.y - radius),
            size = androidx.compose.ui.geometry.Size(radius * 2, radius * 2)
        )
    }
}

// ── Animated trailing dots ──
@Composable
private fun PulsingDots(accent: Color) {
    val transition = rememberInfiniteTransition(label = "dots")
    (0..2).forEach { i ->
        val alpha by transition.animateFloat(
            initialValue = 0.2f, targetValue = 1f,
            animationSpec = infiniteRepeatable(
                tween(600, delayMillis = i * 200),
                RepeatMode.Reverse
            ),
            label = "dot$i"
        )
        Text(
            ".",
            color = accent.copy(alpha = alpha),
            fontWeight = FontWeight.Bold,
            fontSize = 18.sp,
            modifier = Modifier.padding(start = if (i == 0) 4.dp else 0.dp)
        )
    }
}
