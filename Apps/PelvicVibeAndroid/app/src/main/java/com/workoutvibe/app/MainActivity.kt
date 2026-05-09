package com.workoutvibe.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.workoutvibe.app.ui.components.NavDestination
import com.workoutvibe.app.ui.components.NavigationDrawerContent
import com.workoutvibe.app.ui.screens.*
import com.workoutvibe.app.ui.theme.Background
import com.workoutvibe.app.ui.theme.OnBackground
import com.workoutvibe.app.ui.theme.WorkoutVibeTheme
import com.workoutvibe.app.viewmodel.MainViewModel
import kotlinx.coroutines.launch

/**
 * Main Activity for WorkoutVibe
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            WorkoutVibeTheme {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Background)
                        .systemBarsPadding(),
                    contentAlignment = Alignment.Center
                ) {
                    WorkoutVibeContent()
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WorkoutVibeContent(
    viewModel: MainViewModel = viewModel()
) {
    val workoutState by viewModel.workoutState.collectAsStateWithLifecycle()
    val settings by viewModel.settings.collectAsStateWithLifecycle()
    val weeklyProgress by viewModel.weeklyProgress.collectAsStateWithLifecycle()
    val wellnessData by viewModel.wellnessData.collectAsStateWithLifecycle()
    val showCompletionToast by viewModel.showCompletionToast.collectAsStateWithLifecycle()

    // UI state
    var currentDestination by remember { mutableStateOf(NavDestination.TIMER) }
    var showSettingsDialog by remember { mutableStateOf(false) }

    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val scope = rememberCoroutineScope()

    val weekDayCompletion = remember(weeklyProgress) {
        viewModel.getWeekDayCompletion()
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            NavigationDrawerContent(
                currentDestination = currentDestination,
                onNavigate = { destination ->
                    currentDestination = destination
                },
                onClose = {
                    scope.launch { drawerState.close() }
                }
            )
        },
        gesturesEnabled = currentDestination == NavDestination.TIMER
    ) {
        Box(modifier = Modifier.fillMaxSize()) {
            // Main content based on current destination
            when (currentDestination) {
                NavDestination.TIMER -> {
                    TimerScreen(
                        workoutState = workoutState,
                        settings = settings,
                        weekDayCompletion = weekDayCompletion,
                        onMenuClick = { scope.launch { drawerState.open() } },
                        onSettingsClick = { showSettingsDialog = true },
                        onStartPause = { viewModel.toggleTimer() },
                        onReset = { viewModel.resetTimer() },
                        onSkip = { viewModel.skipPhase() }
                    )

                    // Completion toast overlay
                    CompletionToast(
                        visible = showCompletionToast,
                        modifier = Modifier.align(Alignment.TopCenter)
                    )
                }

                NavDestination.WORKOUT_TYPES -> {
                    WorkoutSelectorScreen(
                        currentWorkoutType = settings.workoutType,
                        onWorkoutTypeSelected = { viewModel.setWorkoutType(it) },
                        onBack = { currentDestination = NavDestination.TIMER }
                    )
                }

                NavDestination.CALENDAR -> {
                    CalendarScreen(
                        workoutHistory = viewModel.workoutHistory.collectAsStateWithLifecycle().value,
                        dailyGoal = settings.dailyWorkoutGoal,
                        onBack = { currentDestination = NavDestination.TIMER }
                    )
                }

                NavDestination.STEP_TRACKER -> {
                    StepTrackerScreen(
                        wellnessData = wellnessData,
                        onAddSteps = { viewModel.addSteps(it) },
                        onBack = { currentDestination = NavDestination.TIMER }
                    )
                }

                NavDestination.PROGRESS -> {
                    ProgressScreen(
                        weeklyProgress = weeklyProgress,
                        weekDayCompletion = weekDayCompletion,
                        wellnessData = wellnessData,
                        onBack = { currentDestination = NavDestination.TIMER }
                    )
                }
            }
        }
    }

    // Settings dialog
    if (showSettingsDialog) {
        SettingsDialog(
            settings = settings,
            onDismiss = { showSettingsDialog = false },
            onUpdateWorkoutType = { viewModel.setWorkoutType(it) },
            onUpdateWork = { viewModel.updateWorkDuration(it) },
            onUpdateRest = { viewModel.updateRestDuration(it) },
            onUpdateReps = { viewModel.updateReps(it) },
            onUpdateSets = { viewModel.updateSets(it) },
            onToggleHaptic = { viewModel.toggleHaptic() },
            onToggleSound = { viewModel.toggleSound() },
            onUpdateVoice = { viewModel.setVoiceGender(it) },
            onUpdateDailyGoal = { viewModel.updateDailyGoal(it) }
        )
    }
}
