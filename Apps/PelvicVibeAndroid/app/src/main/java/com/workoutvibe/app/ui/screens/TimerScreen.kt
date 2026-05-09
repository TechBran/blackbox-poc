package com.workoutvibe.app.ui.screens

import androidx.compose.animation.*
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.workoutvibe.app.R
import com.workoutvibe.app.data.TimerPhase
import com.workoutvibe.app.data.WorkoutSettings
import com.workoutvibe.app.data.WorkoutState
import com.workoutvibe.app.ui.components.BreathingRing
import com.workoutvibe.app.ui.components.TimerRing
import com.workoutvibe.app.ui.components.WeeklyDots
import com.workoutvibe.app.ui.theme.*

/**
 * Main timer screen with workout controls
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TimerScreen(
    workoutState: WorkoutState,
    settings: WorkoutSettings,
    weekDayCompletion: List<Boolean>,
    onMenuClick: () -> Unit,
    onSettingsClick: () -> Unit,
    onStartPause: () -> Unit,
    onReset: () -> Unit,
    onSkip: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(Background)
    ) {
        Column(
            modifier = Modifier.fillMaxSize()
        ) {
            // Top Bar
            TopAppBar(
                title = {
                    Column {
                        Text(
                            text = stringResource(R.string.app_name),
                            style = MaterialTheme.typography.headlineMedium,
                            fontWeight = FontWeight.SemiBold
                        )
                        Text(
                            text = settings.workoutType.displayName,
                            style = MaterialTheme.typography.bodySmall,
                            color = Primary
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onMenuClick) {
                        Icon(
                            imageVector = Icons.Default.Menu,
                            contentDescription = "Menu"
                        )
                    }
                },
                actions = {
                    IconButton(onClick = onSettingsClick) {
                        Icon(
                            imageVector = Icons.Default.Settings,
                            contentDescription = "Settings"
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Background,
                    titleContentColor = OnBackground,
                    navigationIconContentColor = OnBackground,
                    actionIconContentColor = OnBackground
                )
            )

            // Main Content
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.SpaceEvenly
            ) {
                // Phase Label
                PhaseLabel(phase = workoutState.phase)

                // Timer Ring with Breathing Animation
                Box(
                    contentAlignment = Alignment.Center
                ) {
                    BreathingRing(
                        isActive = workoutState.isRunning,
                        phase = workoutState.phase
                    )

                    TimerRing(
                        timeRemaining = workoutState.timeRemaining,
                        phaseDuration = workoutState.phaseDuration,
                        phase = workoutState.phase
                    )
                }

                // Workout Stats
                WorkoutStats(
                    currentSet = workoutState.currentSet,
                    totalSets = settings.sets,
                    currentRep = workoutState.currentRep,
                    totalReps = settings.reps
                )

                // Control Buttons
                ControlButtons(
                    isRunning = workoutState.isRunning,
                    phase = workoutState.phase,
                    onStartPause = onStartPause,
                    onReset = onReset,
                    onSkip = onSkip
                )

                // Quick Info
                QuickInfo(
                    workDuration = settings.workDuration,
                    restDuration = settings.restDuration
                )
            }

            // Weekly Progress Bar
            WeeklyDots(
                completedDays = weekDayCompletion,
                modifier = Modifier.padding(vertical = 16.dp)
            )
        }
    }
}

@Composable
private fun PhaseLabel(phase: TimerPhase) {
    val phaseText = when (phase) {
        TimerPhase.READY -> stringResource(R.string.phase_ready)
        TimerPhase.COUNTDOWN -> stringResource(R.string.phase_countdown)
        TimerPhase.WORK -> stringResource(R.string.phase_work)
        TimerPhase.REST -> stringResource(R.string.phase_rest)
        TimerPhase.COMPLETE -> stringResource(R.string.phase_complete)
    }

    val phaseColor = when (phase) {
        TimerPhase.WORK -> Work
        TimerPhase.REST -> Rest
        TimerPhase.COMPLETE -> Complete
        TimerPhase.COUNTDOWN -> Countdown
        TimerPhase.READY -> OnSurfaceVariant
    }

    Text(
        text = phaseText,
        style = MaterialTheme.typography.headlineSmall,
        fontWeight = FontWeight.SemiBold,
        color = phaseColor
    )
}

@Composable
private fun WorkoutStats(
    currentSet: Int,
    totalSets: Int,
    currentRep: Int,
    totalReps: Int
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(24.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = "Set ",
                style = MaterialTheme.typography.bodyMedium,
                color = OnSurfaceVariant
            )
            Text(
                text = "$currentSet/$totalSets",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                color = OnSurface
            )
        }

        Box(
            modifier = Modifier
                .width(1.dp)
                .height(20.dp)
                .background(SurfaceVariant)
        )

        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = "Rep ",
                style = MaterialTheme.typography.bodyMedium,
                color = OnSurfaceVariant
            )
            Text(
                text = "$currentRep/$totalReps",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                color = OnSurface
            )
        }
    }
}

@Composable
private fun ControlButtons(
    isRunning: Boolean,
    phase: TimerPhase,
    onStartPause: () -> Unit,
    onReset: () -> Unit,
    onSkip: () -> Unit
) {
    Row(
        horizontalArrangement = Arrangement.spacedBy(24.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        IconButton(
            onClick = onReset,
            modifier = Modifier
                .size(56.dp)
                .clip(CircleShape)
                .background(SurfaceVariant)
        ) {
            Icon(
                imageVector = Icons.Default.Refresh,
                contentDescription = "Reset",
                tint = OnSurface
            )
        }

        IconButton(
            onClick = onStartPause,
            modifier = Modifier
                .size(80.dp)
                .clip(CircleShape)
                .background(Primary)
        ) {
            Icon(
                imageVector = if (isRunning) Icons.Default.Pause else Icons.Default.PlayArrow,
                contentDescription = if (isRunning) "Pause" else "Play",
                tint = OnPrimary,
                modifier = Modifier.size(40.dp)
            )
        }

        IconButton(
            onClick = onSkip,
            enabled = isRunning && (phase == TimerPhase.WORK || phase == TimerPhase.REST),
            modifier = Modifier
                .size(56.dp)
                .clip(CircleShape)
                .background(SurfaceVariant)
        ) {
            Icon(
                imageVector = Icons.Default.SkipNext,
                contentDescription = "Skip",
                tint = if (isRunning && (phase == TimerPhase.WORK || phase == TimerPhase.REST))
                    OnSurface else OnSurfaceVariant
            )
        }
    }
}

@Composable
private fun QuickInfo(
    workDuration: Int,
    restDuration: Int
) {
    Text(
        text = "${workDuration}s work / ${restDuration}s rest",
        style = MaterialTheme.typography.bodySmall,
        color = OnSurfaceVariant
    )
}

/**
 * Completion toast overlay
 */
@Composable
fun CompletionToast(
    visible: Boolean,
    modifier: Modifier = Modifier
) {
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn() + slideInVertically { -it },
        exit = fadeOut() + slideOutVertically { -it },
        modifier = modifier
    ) {
        Surface(
            modifier = Modifier.padding(16.dp),
            shape = RoundedCornerShape(24.dp),
            color = Surface,
            tonalElevation = 8.dp
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 24.dp, vertical = 16.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Icon(
                    imageVector = Icons.Default.CheckCircle,
                    contentDescription = null,
                    tint = Complete
                )
                Text(
                    text = stringResource(R.string.workout_complete),
                    style = MaterialTheme.typography.titleMedium,
                    color = OnSurface
                )
            }
        }
    }
}
