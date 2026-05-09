package com.workoutvibe.app.ui.components

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.workoutvibe.app.data.TimerPhase
import com.workoutvibe.app.ui.theme.*

/**
 * Circular timer ring with progress indicator
 */
@Composable
fun TimerRing(
    timeRemaining: Int,
    phaseDuration: Int,
    phase: TimerPhase,
    modifier: Modifier = Modifier,
    size: Dp = 280.dp,
    strokeWidth: Dp = 8.dp
) {
    val progress = if (phaseDuration > 0) {
        (phaseDuration - timeRemaining).toFloat() / phaseDuration
    } else 0f

    val animatedProgress by animateFloatAsState(
        targetValue = progress,
        animationSpec = tween(300, easing = LinearEasing),
        label = "progress"
    )

    val progressColor by animateColorAsState(
        targetValue = when (phase) {
            TimerPhase.WORK -> Work
            TimerPhase.REST -> Rest
            TimerPhase.COMPLETE -> Complete
            TimerPhase.COUNTDOWN -> Countdown
            TimerPhase.READY -> Ready
        },
        animationSpec = tween(300),
        label = "color"
    )

    val glowColor = when (phase) {
        TimerPhase.WORK -> WorkGlow
        TimerPhase.REST -> RestGlow
        TimerPhase.COMPLETE -> CompleteGlow
        else -> Color.Transparent
    }

    Box(
        modifier = modifier.size(size),
        contentAlignment = Alignment.Center
    ) {
        // Background track
        Canvas(modifier = Modifier.fillMaxSize()) {
            val strokePx = strokeWidth.toPx()
            val radius = (this.size.minDimension - strokePx) / 2

            // Track circle
            drawCircle(
                color = TimerTrack,
                radius = radius,
                style = Stroke(width = strokePx)
            )

            // Progress arc
            val sweepAngle = animatedProgress * 360f
            drawArc(
                color = progressColor,
                startAngle = -90f,
                sweepAngle = sweepAngle,
                useCenter = false,
                topLeft = Offset(strokePx / 2, strokePx / 2),
                size = Size(
                    this.size.width - strokePx,
                    this.size.height - strokePx
                ),
                style = Stroke(
                    width = strokePx,
                    cap = StrokeCap.Round
                )
            )
        }

        // Center content
        Column(
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                text = timeRemaining.toString(),
                style = MaterialTheme.typography.displayLarge.copy(
                    fontSize = 72.sp,
                    fontWeight = FontWeight.Bold
                ),
                color = OnBackground
            )
            Text(
                text = "sec",
                style = MaterialTheme.typography.bodyMedium,
                color = OnSurfaceVariant
            )
        }
    }
}

/**
 * Breathing ring that pulses during exercise with distinct phase animations
 */
@Composable
fun BreathingRing(
    isActive: Boolean,
    phase: TimerPhase,
    modifier: Modifier = Modifier,
    baseSize: Dp = 320.dp
) {
    val infiniteTransition = rememberInfiniteTransition(label = "breathing")

    // WORK: Rapid, energetic pulse to signal effort
    val workScale by infiniteTransition.animateFloat(
        initialValue = 1.0f,
        targetValue = 1.1f,
        animationSpec = infiniteRepeatable(
            animation = tween(800, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "workScale"
    )

    val workAlpha by infiniteTransition.animateFloat(
        initialValue = 0.6f,
        targetValue = 0.2f,
        animationSpec = infiniteRepeatable(
            animation = tween(800, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "workAlpha"
    )

    // REST: Slow, expansive wave to signal release
    val restScale by infiniteTransition.animateFloat(
        initialValue = 1.0f,
        targetValue = 1.25f,
        animationSpec = infiniteRepeatable(
            animation = tween(2500, easing = LinearOutSlowInEasing),
            repeatMode = RepeatMode.Restart
        ),
        label = "restScale"
    )

    val restAlpha by infiniteTransition.animateFloat(
        initialValue = 0.5f,
        targetValue = 0.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(2500, easing = LinearOutSlowInEasing),
            repeatMode = RepeatMode.Restart
        ),
        label = "restAlpha"
    )

    // READY: Gentle heartbeat
    val readyScale by infiniteTransition.animateFloat(
        initialValue = 1.0f,
        targetValue = 1.03f,
        animationSpec = infiniteRepeatable(
            animation = tween(2000, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "readyScale"
    )

    if (isActive && phase != TimerPhase.COMPLETE) {
        val (scale, alpha, color) = when (phase) {
            TimerPhase.WORK -> Triple(workScale, workAlpha, Work)
            TimerPhase.REST -> Triple(restScale, restAlpha, Rest)
            else -> Triple(readyScale, 0.2f, OnSurfaceVariant)
        }

        Canvas(
            modifier = modifier.size(baseSize * 1.3f)
        ) {
            val center = center
            val baseRadius = size.minDimension / 2.5f

            // Core Glow
            drawCircle(
                brush = androidx.compose.ui.graphics.Brush.radialGradient(
                    colors = listOf(color.copy(alpha = 0.3f), Color.Transparent),
                    center = center,
                    radius = baseRadius * 1.2f
                ),
                radius = baseRadius * 1.2f
            )

            // Animated Ripple Ring
            if (phase == TimerPhase.REST) {
                drawCircle(
                    color = color.copy(alpha = alpha),
                    radius = baseRadius * scale,
                    style = Stroke(width = 4.dp.toPx())
                )
                drawCircle(
                    color = color.copy(alpha = alpha * 0.5f),
                    radius = baseRadius * (scale * 0.9f),
                    style = Stroke(width = 2.dp.toPx())
                )
            } else if (phase == TimerPhase.WORK) {
                drawCircle(
                    color = color.copy(alpha = alpha),
                    radius = baseRadius * scale,
                    style = Stroke(width = 6.dp.toPx())
                )
            } else {
                drawCircle(
                    color = color.copy(alpha = alpha),
                    radius = baseRadius * scale,
                    style = Stroke(width = 2.dp.toPx())
                )
            }
        }
    }
}
