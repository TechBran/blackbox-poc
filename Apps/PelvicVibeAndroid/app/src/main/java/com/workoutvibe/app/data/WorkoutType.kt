package com.workoutvibe.app.data

/**
 * Workout types with their default configurations
 */
enum class WorkoutType(
    val displayName: String,
    val workCue: String,
    val restCue: String,
    val defaultWork: Int,
    val defaultRest: Int,
    val defaultReps: Int,
    val defaultSets: Int
) {
    PUSHUPS("Push-ups", "Push Up!", "Rest", 2, 3, 10, 3),
    SITUPS("Sit-ups", "Crunch!", "Rest", 2, 3, 15, 3),
    SQUATS("Squats", "Squat!", "Rest", 3, 3, 12, 3),
    PLANKS("Planks", "Hold!", "Rest", 30, 15, 1, 3),
    BURPEES("Burpees", "Burpee!", "Rest", 3, 5, 8, 3),
    RUNNING_INTERVALS("Running", "Run!", "Walk", 60, 30, 1, 8),
    SPRINT_INTERVALS("Sprints", "Sprint!", "Recover", 20, 40, 1, 10),
    CUSTOM("Custom", "Go!", "Rest", 5, 5, 10, 1);

    companion object {
        fun fromName(name: String): WorkoutType {
            return entries.find { it.name == name } ?: CUSTOM
        }
    }
}
