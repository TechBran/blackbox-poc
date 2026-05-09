package com.workoutvibe.app.audio

import com.workoutvibe.app.data.VoiceGender
import com.workoutvibe.app.data.WorkoutType

/**
 * Audio file registry for TTS cues.
 * Maps workout types and voice genders to audio resource names.
 */
object TTSIndex {

    /**
     * Get the resource name for a number countdown (1-60)
     */
    fun getNumberResource(number: Int, voice: VoiceGender): String {
        if (number < 1 || number > 60) return ""
        return "${voice.prefix}_num_$number"
    }

    /**
     * Get the work cue resource for a workout type
     */
    fun getWorkCueResource(workoutType: WorkoutType, voice: VoiceGender): String {
        val cueName = when (workoutType) {
            WorkoutType.PUSHUPS -> "pushup"
            WorkoutType.SITUPS -> "crunch"
            WorkoutType.SQUATS -> "squat"
            WorkoutType.PLANKS -> "hold"
            WorkoutType.BURPEES -> "burpee"
            WorkoutType.RUNNING_INTERVALS -> "run"
            WorkoutType.SPRINT_INTERVALS -> "sprint"
            WorkoutType.CUSTOM -> "go"
        }
        return "${voice.prefix}_$cueName"
    }

    /**
     * Get the rest cue resource for a workout type
     */
    fun getRestCueResource(workoutType: WorkoutType, voice: VoiceGender): String {
        val cueName = when (workoutType) {
            WorkoutType.RUNNING_INTERVALS -> "walk"
            WorkoutType.SPRINT_INTERVALS -> "recover"
            else -> "rest"
        }
        return "${voice.prefix}_$cueName"
    }

    /**
     * Get the countdown resource
     */
    fun getCountdownResource(voice: VoiceGender): String {
        return "${voice.prefix}_countdown"
    }

    /**
     * Get the workout complete resource
     */
    fun getWorkoutCompleteResource(voice: VoiceGender): String {
        return "${voice.prefix}_workout_complete"
    }

    /**
     * Get the keep going resource
     */
    fun getKeepGoingResource(voice: VoiceGender): String {
        return "${voice.prefix}_keep_going"
    }

    /**
     * Get the halfway encouragement resource
     */
    fun getHalfwayResource(voice: VoiceGender): String {
        return "${voice.prefix}_halfway"
    }

    /**
     * Get the set complete resource (1-10)
     */
    fun getSetCompleteResource(setNumber: Int, voice: VoiceGender): String {
        if (setNumber < 1 || setNumber > 10) return ""
        return "${voice.prefix}_set_complete_$setNumber"
    }

    /**
     * Fallback mapping for legacy audio files (current female voice without prefix)
     * Used when the prefixed file doesn't exist
     */
    object LegacyMapping {
        private val legacyNames = mapOf(
            "f_countdown" to "countdown",
            "f_go" to "squeeze",      // Legacy uses "squeeze" for work cue
            "f_rest" to "relax",      // Legacy uses "relax" for rest cue
            "f_keep_going" to "keep_holding",
            "f_workout_complete" to "workout_complete"
        )

        fun getLegacyName(prefixedName: String): String? {
            // Check direct mapping
            legacyNames[prefixedName]?.let { return it }

            // Check for number files (f_num_X -> num_X)
            if (prefixedName.startsWith("f_num_")) {
                return prefixedName.removePrefix("f_")
            }

            // Check for set complete (f_set_complete_X -> setX_complete)
            if (prefixedName.startsWith("f_set_complete_")) {
                val num = prefixedName.removePrefix("f_set_complete_")
                return "set${num}_complete"
            }

            return null
        }
    }
}
