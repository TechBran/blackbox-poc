package com.aiblackbox.portal.ui.cli_agent

// WhisperMicButton — tap-to-record state machine that injects Whisper
// transcripts as bracketed paste into the active terminal session.
//
// State machine: idle 🎤 → recording 🔴 → transcribing ⏳ → idle 🎤
// Long-press during recording cancels (discards audio).
//
// Recording: AudioRecorderManager (AAC/M4A) — reused from chat composer.
// 60s hard cap with 50s warning toast. Posts blob to BlackBox /stt
// endpoint (Whisper) via AudioRecorderManager.transcribe().
// Surfaces transcript via onTranscript: (String) -> Unit callback;
// caller wraps in {"type":"paste","text":transcript} text frame.

import android.Manifest
import android.content.pm.PackageManager
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.aiblackbox.portal.data.api.BlackBoxApi
import com.aiblackbox.portal.data.voice.AudioRecorderManager
import com.aiblackbox.portal.ui.components.MicIcon
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Mic-button state for the WhisperMicButton state machine.
 *
 *   Idle         — tap to begin recording.
 *   Recording    — red icon w/ pulse; tap to stop, long-press to cancel.
 *   Transcribing — spinner while audio uploads to /stt.
 */
private enum class MicState { Idle, Recording, Transcribing }

private const val MAX_RECORDING_MS = 60_000L
private const val WARNING_AT_MS = 50_000L

@Composable
fun WhisperMicButton(
    onTranscript: (String) -> Unit,
    api: BlackBoxApi,
    operator: String,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    // Recorder is per-button; survives recompositions.
    val recorder = remember { AudioRecorderManager(context) }

    var state by remember { mutableStateOf(MicState.Idle) }
    // Tracks the auto-stop / warning coroutine so cancel can interrupt it.
    var timerJob by remember { mutableStateOf<Job?>(null) }
    // Set true by long-press: stopRecording() runs but we discard the file.
    var cancelRequested by remember { mutableStateOf(false) }

    // Permission launcher. On grant → start recording. On deny → toast + idle.
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            startRecording(
                recorder = recorder,
                onStarted = {
                    state = MicState.Recording
                    cancelRequested = false
                    timerJob = scope.launch {
                        delay(WARNING_AT_MS)
                        if (state == MicState.Recording) {
                            Toast.makeText(
                                context,
                                "Recording limit approaching",
                                Toast.LENGTH_SHORT
                            ).show()
                        }
                        delay(MAX_RECORDING_MS - WARNING_AT_MS)
                        if (state == MicState.Recording) {
                            // Hard cap: stop & transcribe.
                            finishAndTranscribe(
                                recorder = recorder,
                                api = api,
                                operator = operator,
                                onTranscript = onTranscript,
                                onStateChange = { state = it },
                                onError = { msg ->
                                    Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
                                },
                                scope = scope,
                            )
                        }
                    }
                },
                onError = { msg ->
                    Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
                    state = MicState.Idle
                },
            )
        } else {
            Toast.makeText(
                context,
                "Microphone permission required",
                Toast.LENGTH_SHORT
            ).show()
            state = MicState.Idle
        }
    }

    // Visual: pulsing alpha for the recording state.
    val infiniteTransition = rememberInfiniteTransition(label = "micPulse")
    val pulseAlpha by infiniteTransition.animateFloat(
        initialValue = 1f,
        targetValue = 0.5f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "micPulseAlpha",
    )

    val iconScale by animateFloatAsState(
        targetValue = if (state == MicState.Recording) 1.1f else 1f,
        label = "micIconScale",
    )

    // Free recorder if the Composable leaves composition mid-recording.
    DisposableEffect(recorder) {
        onDispose {
            timerJob?.cancel()
            // If we're still holding the recorder, drop it cleanly.
            if (recorder.isCurrentlyRecording()) {
                runCatching { recorder.stopRecording()?.delete() }
            }
        }
    }

    val shape = RoundedCornerShape(6.dp)
    val isRecording = state == MicState.Recording
    val isTranscribing = state == MicState.Transcribing

    Box(
        modifier = modifier
            .defaultMinSize(minWidth = 44.dp, minHeight = 36.dp)
            .height(36.dp)
            .clip(shape)
            .background(
                if (isRecording) MaterialTheme.colorScheme.errorContainer
                else MaterialTheme.colorScheme.surface
            )
            .border(
                width = 1.dp,
                color = if (isRecording) MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.outlineVariant,
                shape = shape,
            )
            .pointerInput(state) {
                detectTapGestures(
                    onTap = {
                        when (state) {
                            MicState.Idle -> {
                                // Permission gate.
                                val granted = ContextCompat.checkSelfPermission(
                                    context,
                                    Manifest.permission.RECORD_AUDIO,
                                ) == PackageManager.PERMISSION_GRANTED
                                if (granted) {
                                    startRecording(
                                        recorder = recorder,
                                        onStarted = {
                                            state = MicState.Recording
                                            cancelRequested = false
                                            timerJob = scope.launch {
                                                delay(WARNING_AT_MS)
                                                if (state == MicState.Recording) {
                                                    Toast.makeText(
                                                        context,
                                                        "Recording limit approaching",
                                                        Toast.LENGTH_SHORT
                                                    ).show()
                                                }
                                                delay(MAX_RECORDING_MS - WARNING_AT_MS)
                                                if (state == MicState.Recording) {
                                                    finishAndTranscribe(
                                                        recorder = recorder,
                                                        api = api,
                                                        operator = operator,
                                                        onTranscript = onTranscript,
                                                        onStateChange = { state = it },
                                                        onError = { msg ->
                                                            Toast.makeText(
                                                                context,
                                                                msg,
                                                                Toast.LENGTH_SHORT
                                                            ).show()
                                                        },
                                                        scope = scope,
                                                    )
                                                }
                                            }
                                        },
                                        onError = { msg ->
                                            Toast.makeText(
                                                context,
                                                msg,
                                                Toast.LENGTH_SHORT
                                            ).show()
                                            state = MicState.Idle
                                        },
                                    )
                                } else {
                                    permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                                }
                            }
                            MicState.Recording -> {
                                timerJob?.cancel()
                                timerJob = null
                                if (cancelRequested) {
                                    // Long-press already requested cancel; honor it.
                                    val f = recorder.stopRecording()
                                    f?.delete()
                                    cancelRequested = false
                                    state = MicState.Idle
                                } else {
                                    finishAndTranscribe(
                                        recorder = recorder,
                                        api = api,
                                        operator = operator,
                                        onTranscript = onTranscript,
                                        onStateChange = { state = it },
                                        onError = { msg ->
                                            Toast.makeText(
                                                context,
                                                msg,
                                                Toast.LENGTH_SHORT
                                            ).show()
                                        },
                                        scope = scope,
                                    )
                                }
                            }
                            MicState.Transcribing -> {
                                // Ignore taps while uploading.
                            }
                        }
                    },
                    onLongPress = {
                        if (state == MicState.Recording) {
                            // Discard recording, return to idle.
                            timerJob?.cancel()
                            timerJob = null
                            cancelRequested = true
                            val f = recorder.stopRecording()
                            f?.delete()
                            cancelRequested = false
                            state = MicState.Idle
                            Toast.makeText(
                                context,
                                "Recording cancelled",
                                Toast.LENGTH_SHORT
                            ).show()
                        }
                    },
                )
            },
        contentAlignment = Alignment.Center,
    ) {
        when {
            isTranscribing -> {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            isRecording -> {
                MicIcon(
                    modifier = Modifier
                        .size(20.dp)
                        .scale(iconScale)
                        .alpha(pulseAlpha),
                    color = MaterialTheme.colorScheme.error,
                )
            }
            else -> {
                MicIcon(
                    modifier = Modifier.size(20.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/**
 * Start recording via AudioRecorderManager. On success, invokes [onStarted].
 * On failure (MediaRecorder threw), invokes [onError] and the caller should
 * return to Idle.
 */
private fun startRecording(
    recorder: AudioRecorderManager,
    onStarted: () -> Unit,
    onError: (String) -> Unit,
) {
    val ok = runCatching { recorder.startRecording() }.getOrDefault(false)
    if (ok) onStarted() else onError("Failed to start recording")
}

/**
 * Stop recording and POST the resulting M4A to /stt for Whisper transcription.
 * Drives the state machine: Recording → Transcribing → Idle. Empty / failed
 * transcriptions surface a toast via [onError] but still return to Idle.
 */
private fun finishAndTranscribe(
    recorder: AudioRecorderManager,
    api: BlackBoxApi,
    operator: String,
    onTranscript: (String) -> Unit,
    onStateChange: (MicState) -> Unit,
    onError: (String) -> Unit,
    scope: kotlinx.coroutines.CoroutineScope,
) {
    onStateChange(MicState.Transcribing)
    scope.launch {
        try {
            val file = recorder.stopRecording()
            if (file == null) {
                onError("Recording failed")
                onStateChange(MicState.Idle)
                return@launch
            }
            // operator is currently unused by AudioRecorderManager.transcribe()
            // but is retained in the public API for future per-operator routing
            // (e.g. snapshot attribution on the orchestrator side).
            @Suppress("UNUSED_VARIABLE")
            val op = operator
            val text = recorder.transcribe(api, file)
            if (text.isBlank()) {
                onError("No transcription returned")
            } else {
                onTranscript(text)
            }
        } catch (e: Exception) {
            onError("Transcription failed: ${e.message ?: "unknown"}")
        } finally {
            onStateChange(MicState.Idle)
        }
    }
}
