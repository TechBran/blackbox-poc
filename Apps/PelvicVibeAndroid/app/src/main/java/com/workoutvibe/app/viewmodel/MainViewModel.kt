package com.workoutvibe.app.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.workoutvibe.app.audio.HapticManager
import com.workoutvibe.app.audio.WorkoutAudioManager
import com.workoutvibe.app.data.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.temporal.TemporalAdjusters

/**
 * Main ViewModel managing all app state including:
 * - Workout timer logic
 * - Settings with workout types
 * - Progress tracking
 * - Workout history for calendar
 */
class MainViewModel(application: Application) : AndroidViewModel(application) {

    // Initialize managers
    private val preferences = AppPreferences(application)
    private val audioManager = WorkoutAudioManager(application)
    private val hapticManager = HapticManager(application)

    // Workout state
    private val _workoutState = MutableStateFlow(WorkoutState())
    val workoutState: StateFlow<WorkoutState> = _workoutState.asStateFlow()

    // Settings from DataStore
    val settings: StateFlow<WorkoutSettings> = preferences.settingsFlow
        .stateIn(viewModelScope, SharingStarted.Eagerly, WorkoutSettings())

    // Weekly progress from DataStore
    val weeklyProgress: StateFlow<WeeklyProgress> = preferences.weeklyProgressFlow
        .stateIn(viewModelScope, SharingStarted.Eagerly, WeeklyProgress())

    // Wellness data from DataStore
    val wellnessData: StateFlow<WellnessData> = preferences.wellnessFlow
        .stateIn(viewModelScope, SharingStarted.Eagerly, WellnessData())

    // Workout history from DataStore
    val workoutHistory: StateFlow<WorkoutHistory> = preferences.workoutHistoryFlow
        .stateIn(viewModelScope, SharingStarted.Eagerly, WorkoutHistory())

    // Toast visibility
    private val _showCompletionToast = MutableStateFlow(false)
    val showCompletionToast: StateFlow<Boolean> = _showCompletionToast.asStateFlow()

    // Timer
    private var timerJob: kotlinx.coroutines.Job? = null

    init {
        // Initialize workout state with settings
        viewModelScope.launch {
            settings.collect { s ->
                if (_workoutState.value.phase == TimerPhase.READY) {
                    _workoutState.update {
                        it.copy(
                            timeRemaining = s.workDuration,
                            phaseDuration = s.workDuration
                        )
                    }
                }
                // Update audio manager with current settings
                audioManager.setVoiceGender(s.voiceGender)
                audioManager.setWorkoutType(s.workoutType)
            }
        }
    }

    // =====================
    // Timer Controls
    // =====================

    fun toggleTimer() {
        if (_workoutState.value.isRunning) {
            pauseTimer()
        } else {
            startTimer()
        }
    }

    fun startTimer() {
        val state = _workoutState.value
        val currentSettings = settings.value

        when (state.phase) {
            TimerPhase.COMPLETE -> resetTimer()
            TimerPhase.READY -> {
                // Start with countdown
                _workoutState.update {
                    it.copy(
                        isRunning = true,
                        isPaused = false,
                        phase = TimerPhase.COUNTDOWN
                    )
                }

                if (currentSettings.soundEnabled) {
                    audioManager.playCountdown {
                        startWorkPhase()
                    }
                } else {
                    startWorkPhase()
                }
            }
            else -> {
                // Resume from pause
                _workoutState.update {
                    it.copy(isRunning = true, isPaused = false)
                }
                processTimerStep(_workoutState.value.timeRemaining)
            }
        }
    }

    private fun startWorkPhase() {
        val currentSettings = settings.value
        _workoutState.update {
            it.copy(
                phase = TimerPhase.WORK,
                timeRemaining = currentSettings.workDuration,
                phaseDuration = currentSettings.workDuration,
                currentRep = 1
            )
        }

        if (currentSettings.hapticEnabled) hapticManager.workStart()

        if (currentSettings.soundEnabled) {
            audioManager.playWorkCue {
                processTimerStep(currentSettings.workDuration)
            }
        } else {
            processTimerStep(currentSettings.workDuration)
        }
    }

    fun pauseTimer() {
        timerJob?.cancel()
        audioManager.stopCountdown()

        val state = _workoutState.value
        if (state.phase == TimerPhase.COUNTDOWN) {
            _workoutState.update {
                it.copy(
                    isRunning = false,
                    isPaused = false,
                    phase = TimerPhase.READY
                )
            }
        } else {
            _workoutState.update {
                it.copy(isRunning = false, isPaused = true)
            }
        }
    }

    fun resetTimer() {
        timerJob?.cancel()
        audioManager.stopCountdown()

        val currentSettings = settings.value
        _workoutState.value = WorkoutState(
            timeRemaining = currentSettings.workDuration,
            phaseDuration = currentSettings.workDuration
        )
    }

    fun skipPhase() {
        if (!_workoutState.value.isRunning) return

        when (_workoutState.value.phase) {
            TimerPhase.WORK -> switchToRest()
            TimerPhase.REST -> completeRep()
            else -> {}
        }
    }

    /**
     * Core timer loop driven by audio completion or time delay.
     */
    private fun processTimerStep(currentTime: Int) {
        timerJob?.cancel()
        timerJob = viewModelScope.launch {
            _workoutState.update { it.copy(timeRemaining = currentTime) }

            if (currentTime <= 0) {
                onPhaseFinished()
                return@launch
            }

            val currentSettings = settings.value

            if (currentSettings.soundEnabled) {
                val isWork = _workoutState.value.phase == TimerPhase.WORK
                val midpoint = currentSettings.workDuration / 2
                val isLongWork = currentSettings.workDuration > 5

                if (isWork && isLongWork && currentTime == midpoint) {
                    audioManager.playNumber(currentTime) {
                        audioManager.playKeepGoing {
                            processTimerStep(currentTime - 1)
                        }
                    }
                } else if (currentTime <= 60) {
                    audioManager.playNumber(currentTime) {
                        processTimerStep(currentTime - 1)
                    }
                } else {
                    kotlinx.coroutines.delay(1000)
                    processTimerStep(currentTime - 1)
                }
            } else {
                kotlinx.coroutines.delay(1000)
                processTimerStep(currentTime - 1)
            }
        }
    }

    private fun onPhaseFinished() {
        when (_workoutState.value.phase) {
            TimerPhase.WORK -> switchToRest()
            TimerPhase.REST -> completeRep()
            else -> {}
        }
    }

    private fun switchToRest() {
        timerJob?.cancel()
        val currentSettings = settings.value

        _workoutState.update {
            it.copy(
                phase = TimerPhase.REST,
                timeRemaining = currentSettings.restDuration,
                phaseDuration = currentSettings.restDuration
            )
        }

        if (currentSettings.hapticEnabled) hapticManager.restStart()

        if (currentSettings.soundEnabled) {
            audioManager.playRestCue {
                processTimerStep(currentSettings.restDuration)
            }
        } else {
            viewModelScope.launch {
                kotlinx.coroutines.delay(500)
                processTimerStep(currentSettings.restDuration)
            }
        }
    }

    private fun completeRep() {
        timerJob?.cancel()

        val state = _workoutState.value
        val currentSettings = settings.value

        if (state.currentRep >= currentSettings.reps) {
            if (state.currentSet < currentSettings.sets) {
                if (currentSettings.soundEnabled) {
                    audioManager.playSetComplete(state.currentSet)
                }

                _workoutState.update {
                    it.copy(
                        currentSet = it.currentSet + 1,
                        currentRep = 1,
                        phase = TimerPhase.WORK,
                        timeRemaining = currentSettings.workDuration,
                        phaseDuration = currentSettings.workDuration
                    )
                }

                timerJob = viewModelScope.launch {
                    kotlinx.coroutines.delay(if (currentSettings.soundEnabled) 3500 else 1000)

                    if (currentSettings.hapticEnabled) hapticManager.workStart()

                    if (currentSettings.soundEnabled) {
                        audioManager.playWorkCue {
                            processTimerStep(currentSettings.workDuration)
                        }
                    } else {
                        processTimerStep(currentSettings.workDuration)
                    }
                }
            } else {
                completeWorkout()
            }
        } else {
            _workoutState.update {
                it.copy(
                    currentRep = it.currentRep + 1,
                    phase = TimerPhase.WORK,
                    timeRemaining = currentSettings.workDuration,
                    phaseDuration = currentSettings.workDuration
                )
            }

            if (currentSettings.hapticEnabled) hapticManager.workStart()

            if (currentSettings.soundEnabled) {
                audioManager.playWorkCue {
                    processTimerStep(currentSettings.workDuration)
                }
            } else {
                viewModelScope.launch {
                    kotlinx.coroutines.delay(500)
                    processTimerStep(currentSettings.workDuration)
                }
            }
        }
    }

    private fun completeWorkout() {
        val currentSettings = settings.value
        val state = _workoutState.value

        _workoutState.update {
            it.copy(
                isRunning = false,
                isPaused = false,
                phase = TimerPhase.COMPLETE
            )
        }

        // Mark day complete and add workout entry
        viewModelScope.launch {
            preferences.markDayComplete()

            // Add to workout history
            val entry = WorkoutEntry(
                date = LocalDate.now().format(DateTimeFormatter.ISO_LOCAL_DATE),
                workoutType = currentSettings.workoutType,
                completedSets = state.currentSet,
                totalReps = currentSettings.reps * state.currentSet
            )
            preferences.addWorkoutEntry(entry)
        }

        if (currentSettings.soundEnabled) audioManager.playWorkoutComplete()
        if (currentSettings.hapticEnabled) hapticManager.successPattern()

        _showCompletionToast.value = true
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            _showCompletionToast.value = false
        }, 3000)
    }

    // =====================
    // Settings
    // =====================

    fun setWorkoutType(type: WorkoutType) {
        viewModelScope.launch {
            val current = settings.value
            val newSettings = current.copy(
                workoutType = type,
                workDuration = type.defaultWork,
                restDuration = type.defaultRest,
                reps = type.defaultReps,
                sets = type.defaultSets
            )
            preferences.saveSettings(newSettings)

            // Update timer display if not running
            if (_workoutState.value.phase == TimerPhase.READY) {
                _workoutState.update {
                    it.copy(timeRemaining = type.defaultWork, phaseDuration = type.defaultWork)
                }
            }
        }
    }

    fun updateWorkDuration(delta: Int) {
        viewModelScope.launch {
            val current = settings.value
            val newValue = (current.workDuration + delta)
                .coerceIn(WorkoutSettings.MIN_DURATION, WorkoutSettings.MAX_DURATION)
            preferences.saveSettings(current.copy(workDuration = newValue))

            if (_workoutState.value.phase == TimerPhase.READY) {
                _workoutState.update {
                    it.copy(timeRemaining = newValue, phaseDuration = newValue)
                }
            }
        }
    }

    fun updateRestDuration(delta: Int) {
        viewModelScope.launch {
            val current = settings.value
            val newValue = (current.restDuration + delta)
                .coerceIn(WorkoutSettings.MIN_DURATION, WorkoutSettings.MAX_DURATION)
            preferences.saveSettings(current.copy(restDuration = newValue))
        }
    }

    fun updateReps(delta: Int) {
        viewModelScope.launch {
            val current = settings.value
            val newValue = (current.reps + delta)
                .coerceIn(WorkoutSettings.MIN_REPS, WorkoutSettings.MAX_REPS)
            preferences.saveSettings(current.copy(reps = newValue))
        }
    }

    fun updateSets(delta: Int) {
        viewModelScope.launch {
            val current = settings.value
            val newValue = (current.sets + delta)
                .coerceIn(WorkoutSettings.MIN_SETS, WorkoutSettings.MAX_SETS)
            preferences.saveSettings(current.copy(sets = newValue))
        }
    }

    fun toggleHaptic() {
        viewModelScope.launch {
            val current = settings.value
            preferences.saveSettings(current.copy(hapticEnabled = !current.hapticEnabled))
        }
    }

    fun toggleSound() {
        viewModelScope.launch {
            val current = settings.value
            preferences.saveSettings(current.copy(soundEnabled = !current.soundEnabled))
        }
    }

    fun setVoiceGender(gender: VoiceGender) {
        viewModelScope.launch {
            val current = settings.value
            preferences.saveSettings(current.copy(voiceGender = gender))
            audioManager.setVoiceGender(gender)
        }
    }

    fun updateDailyGoal(delta: Int) {
        viewModelScope.launch {
            val current = settings.value
            val newValue = (current.dailyWorkoutGoal + delta)
                .coerceIn(WorkoutSettings.MIN_DAILY_GOAL, WorkoutSettings.MAX_DAILY_GOAL)
            preferences.saveSettings(current.copy(dailyWorkoutGoal = newValue))
        }
    }

    // =====================
    // Step Tracker
    // =====================

    fun addSteps(steps: Int) {
        viewModelScope.launch {
            preferences.addSteps(steps)
        }
    }

    // =====================
    // Progress Helpers
    // =====================

    fun getWeekDayCompletion(): List<Boolean> {
        val progress = weeklyProgress.value
        val today = LocalDate.now()
        val weekStart = today.with(TemporalAdjusters.previousOrSame(DayOfWeek.SUNDAY))

        return (0..6).map { dayOffset ->
            val date = weekStart.plusDays(dayOffset.toLong())
            val dateStr = date.format(DateTimeFormatter.ISO_LOCAL_DATE)
            progress.completedDays.contains(dateStr)
        }
    }

    override fun onCleared() {
        super.onCleared()
        timerJob?.cancel()
        audioManager.release()
    }
}
