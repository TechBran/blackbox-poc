package com.workoutvibe.app.data

/**
 * Workout configuration settings
 */
data class WorkoutSettings(
    val workoutType: WorkoutType = WorkoutType.CUSTOM,
    val workDuration: Int = 5,
    val restDuration: Int = 5,
    val reps: Int = 10,
    val sets: Int = 1,
    val voiceGender: VoiceGender = VoiceGender.FEMALE,
    val soundEnabled: Boolean = true,
    val hapticEnabled: Boolean = false,
    val dailyWorkoutGoal: Int = 3
) {
    companion object {
        const val MIN_DURATION = 1
        const val MAX_DURATION = 60
        const val MIN_REPS = 1
        const val MAX_REPS = 50
        const val MIN_SETS = 1
        const val MAX_SETS = 10
        const val MIN_DAILY_GOAL = 1
        const val MAX_DAILY_GOAL = 10
        const val DURATION_STEP = 5
    }
}

/**
 * Timer phase states - renamed from SQUEEZE/RELAX to WORK/REST
 */
enum class TimerPhase {
    READY,
    COUNTDOWN,
    WORK,
    REST,
    COMPLETE
}

/**
 * Current workout state
 */
data class WorkoutState(
    val isRunning: Boolean = false,
    val isPaused: Boolean = false,
    val phase: TimerPhase = TimerPhase.READY,
    val currentRep: Int = 0,
    val currentSet: Int = 1,
    val timeRemaining: Int = 5,
    val phaseDuration: Int = 5
)

/**
 * Workout history entry
 */
data class WorkoutEntry(
    val date: String,
    val workoutType: WorkoutType,
    val completedSets: Int,
    val totalReps: Int,
    val timestamp: Long = System.currentTimeMillis()
)

/**
 * Wellness tracker data (steps only)
 */
data class WellnessData(
    val steps: Int = 0,
    val lastSaveDate: String = ""
) {
    companion object {
        const val STEPS_GOAL = 10000
    }
}

/**
 * Weekly progress data
 */
data class WeeklyProgress(
    val completedDays: List<String> = emptyList(),
    val streak: Int = 0
)

/**
 * Workout history for calendar
 */
data class WorkoutHistory(
    val entries: List<WorkoutEntry> = emptyList()
) {
    fun getEntriesForDate(date: String): List<WorkoutEntry> {
        return entries.filter { it.date == date }
    }

    fun getWorkoutCountForDate(date: String): Int {
        return entries.count { it.date == date }
    }

    fun isGoalMetForDate(date: String, dailyGoal: Int): Boolean {
        return getWorkoutCountForDate(date) >= dailyGoal
    }
}
