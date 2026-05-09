package com.aiblackbox.portal.ui.components

import android.view.HapticFeedbackConstants
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aiblackbox.portal.ui.theme.RadiusMd
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin

// =============================================================================
// AudioPlayerBar — production-grade waveform audio player
//
// Inspired by WhisperFlow / OpenAI voice app aesthetic:
//   - Smooth mirrored waveform (top + bottom reflection)
//   - Red accent on black background
//   - Fluid reactive bars with spring animation
//   - Gradient fade on played vs unplayed regions
// =============================================================================

private const val BAR_COUNT = 64
private const val POLL_MS = 33L // ~30fps for smooth animation

// Red accent palette on pure black
private val WaveRed = Color(0xFFEF4444)
private val WaveRedDim = Color(0xFFDC2626)
private val WaveRedGlow = Color(0x40EF4444)
private val WaveUnplayed = Color(0xFF2A2A2A)
private val WaveBg = Color(0xFF000000)
private val TimeColor = Color(0xCCEF4444)

/**
 * Generate a smooth, organic waveform pattern.
 * Uses layered sine waves with golden-ratio harmonics for a natural audio feel.
 */
private fun generateWaveform(seed: String, count: Int): FloatArray {
    val h = seed.hashCode()
    val phi = 1.618033988f // golden ratio
    return FloatArray(count) { i ->
        val t = i.toFloat() / count
        // 5 layered harmonics for rich organic shape
        val w1 = sin((t * 2 * Math.PI * 3 + h * 0.013).toFloat()) * 0.28f
        val w2 = sin((t * 2 * Math.PI * 5 * phi + h * 0.007).toFloat()) * 0.22f
        val w3 = cos((t * 2 * Math.PI * 8 + h * 0.023).toFloat()) * 0.15f
        val w4 = sin((t * 2 * Math.PI * 13 * phi + h * 0.003).toFloat()) * 0.10f
        val w5 = cos((t * 2 * Math.PI * 21 + h * 0.017).toFloat()) * 0.07f
        // Envelope: fade edges for natural look
        val envelope = sin((t * Math.PI).toFloat()).coerceIn(0.3f, 1f)
        val raw = (0.5f + w1 + w2 + w3 + w4 + w5) * envelope
        raw.coerceIn(0.06f, 0.98f)
    }
}

@Composable
fun AudioPlayerBar(
    audioUrl: String,
    modifier: Modifier = Modifier
) {
    val view = LocalView.current
    val mgr = com.aiblackbox.portal.data.voice.AudioPlaybackManager

    val activeUrl by mgr.activeUrl.collectAsState()
    val isThisActive = activeUrl == audioUrl
    val isPlaying by mgr.isPlaying.collectAsState()
    val isPrepared by mgr.isPrepared.collectAsState()
    val duration by mgr.duration.collectAsState()
    val position by mgr.position.collectAsState()
    val hasError by mgr.hasError.collectAsState()
    var isSeeking by remember { mutableStateOf(false) }
    var seekPosition by remember { mutableFloatStateOf(0f) }

    val thisPlaying = isThisActive && isPlaying
    val thisPrepared = isThisActive && isPrepared
    val displayPosition = if (isSeeking) seekPosition else if (isThisActive) position else 0f
    val displayDuration = if (isThisActive) duration else 0L

    // Smooth animated position for fluid playhead movement
    val animatedPosition by animateFloatAsState(
        targetValue = displayPosition,
        animationSpec = spring(dampingRatio = 0.8f, stiffness = 300f),
        label = "wavePos"
    )

    // Subtle breathing animation when playing
    val infiniteTransition = rememberInfiniteTransition(label = "wavePulse")
    val breathe by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(2000, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "breathe"
    )

    // Position polling
    LaunchedEffect(isThisActive, isPlaying) {
        while (isThisActive && isPlaying && isActive) {
            if (!isSeeking) mgr.updatePosition()
            delay(POLL_MS)
        }
    }

    val waveform = remember(audioUrl) { generateWaveform(audioUrl, BAR_COUNT) }

    val playBtnBg by animateColorAsState(
        targetValue = if (thisPlaying) WaveRed else WaveRed.copy(alpha = 0.2f),
        animationSpec = tween(200), label = "btnBg"
    )
    val playIconColor by animateColorAsState(
        targetValue = if (thisPlaying) Color.Black else WaveRed,
        animationSpec = tween(200), label = "btnIcon"
    )

    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(RadiusMd))
            .background(WaveBg)
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        // ── Play/Pause ──
        Box(
            modifier = Modifier
                .size(34.dp)
                .clip(CircleShape)
                .background(playBtnBg)
                .clickable {
                    view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                    if (!isThisActive) mgr.loadAndPlay(audioUrl)
                    else mgr.togglePlayPause()
                },
            contentAlignment = Alignment.Center
        ) {
            if (thisPlaying) {
                PauseIcon(Modifier.size(14.dp), playIconColor)
            } else {
                PlayIcon(Modifier.size(14.dp), playIconColor)
            }
        }

        // ── Waveform ──
        var canvasWidth by remember { mutableFloatStateOf(1f) }

        Box(
            modifier = Modifier
                .weight(1f)
                .height(40.dp)
                .pointerInput(isThisActive) {
                    detectTapGestures { offset ->
                        if (canvasWidth > 0) {
                            val frac = (offset.x / canvasWidth).coerceIn(0f, 1f)
                            view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                            if (!isThisActive) mgr.loadAndPlay(audioUrl)
                            seekPosition = frac
                            mgr.seekTo(frac)
                        }
                    }
                }
                .pointerInput(isThisActive) {
                    detectHorizontalDragGestures(
                        onDragStart = { isSeeking = true },
                        onDragEnd = {
                            if (isThisActive) mgr.seekTo(seekPosition)
                            isSeeking = false
                        },
                        onDragCancel = { isSeeking = false },
                        onHorizontalDrag = { change, _ ->
                            if (canvasWidth > 0) {
                                seekPosition = (change.position.x / canvasWidth).coerceIn(0f, 1f)
                            }
                        }
                    )
                }
        ) {
            Canvas(modifier = Modifier.fillMaxSize()) {
                canvasWidth = size.width
                val cW = size.width
                val cH = size.height
                val centerY = cH / 2f
                val gap = 1.8f
                val barW = (cW - gap * (BAR_COUNT - 1)) / BAR_COUNT
                val playedIdx = (animatedPosition * BAR_COUNT).toInt()

                waveform.forEachIndexed { i, amp ->
                    // Breathing modulation when playing
                    val liveAmp = if (thisPlaying) {
                        val proximity = 1f - abs(i.toFloat() / BAR_COUNT - animatedPosition).coerceIn(0f, 0.3f) / 0.3f
                        amp * (1f + proximity * breathe * 0.15f)
                    } else amp

                    val barH = liveAmp.coerceIn(0.06f, 0.98f) * cH * 0.85f
                    val halfH = barH / 2f
                    val x = i * (barW + gap)

                    val isPlayed = i <= playedIdx
                    val barColor = if (isPlayed) WaveRed else WaveUnplayed

                    // Top half (mirrored waveform)
                    drawRoundRect(
                        color = barColor,
                        topLeft = Offset(x, centerY - halfH),
                        size = Size(barW, halfH),
                        cornerRadius = CornerRadius(barW / 2f)
                    )
                    // Bottom half (reflection, slightly dimmer)
                    drawRoundRect(
                        color = barColor.copy(alpha = if (isPlayed) 0.5f else 0.3f),
                        topLeft = Offset(x, centerY + 1f),
                        size = Size(barW, halfH * 0.7f),
                        cornerRadius = CornerRadius(barW / 2f)
                    )
                }

                // Glow line at center
                drawLine(
                    color = WaveRed.copy(alpha = if (thisPlaying) 0.15f else 0.05f),
                    start = Offset(0f, centerY),
                    end = Offset(cW, centerY),
                    strokeWidth = 0.5f
                )

                // Playhead — thin bright line
                if (animatedPosition > 0.001f) {
                    val px = animatedPosition * cW
                    drawLine(
                        color = WaveRed.copy(alpha = 0.9f),
                        start = Offset(px, cH * 0.05f),
                        end = Offset(px, cH * 0.95f),
                        strokeWidth = 1.5f
                    )
                    // Glow dot at playhead
                    drawCircle(
                        color = WaveRed,
                        radius = 3f,
                        center = Offset(px, centerY)
                    )
                }
            }
        }

        // ── Time ──
        val currentMs = if (thisPrepared && displayDuration > 0) (displayPosition * displayDuration).toLong() else 0L
        Text(
            text = formatMs(currentMs),
            fontSize = 11.sp,
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Medium,
            color = TimeColor,
            maxLines = 1
        )
    }
}

// =============================================================================
// Icons
// =============================================================================

@Composable
private fun PlayIcon(modifier: Modifier = Modifier.size(14.dp), color: Color = WaveRed) {
    Canvas(modifier = modifier) {
        val path = Path().apply {
            moveTo(size.width * 0.25f, size.height * 0.12f)
            lineTo(size.width * 0.85f, size.height * 0.5f)
            lineTo(size.width * 0.25f, size.height * 0.88f)
            close()
        }
        drawPath(path, color)
    }
}

@Composable
private fun PauseIcon(modifier: Modifier = Modifier.size(14.dp), color: Color = WaveRed) {
    Canvas(modifier = modifier) {
        val barW = size.width * 0.22f
        val gap = size.width * 0.12f
        val startX = (size.width - barW * 2 - gap) / 2
        drawRoundRect(color, Offset(startX, size.height * 0.15f), Size(barW, size.height * 0.7f), CornerRadius(barW * 0.3f))
        drawRoundRect(color, Offset(startX + barW + gap, size.height * 0.15f), Size(barW, size.height * 0.7f), CornerRadius(barW * 0.3f))
    }
}

private fun formatMs(ms: Long): String {
    val totalSec = ms / 1000
    return "%d:%02d".format(totalSec / 60, totalSec % 60)
}
