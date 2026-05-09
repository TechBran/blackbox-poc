package com.workoutvibe.app.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.*
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.format.DateTimeFormatter

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "workout_vibe_prefs")

/**
 * DataStore-based preferences manager for persisting app state
 */
class AppPreferences(private val context: Context) {

    private object Keys {
        val WORKOUT_TYPE = stringPreferencesKey("workout_type")
        val WORK_DURATION = intPreferencesKey("work_duration")
        val REST_DURATION = intPreferencesKey("rest_duration")
        val REPS = intPreferencesKey("reps")
        val SETS = intPreferencesKey("sets")
        val VOICE_GENDER = stringPreferencesKey("voice_gender")
        val HAPTIC_ENABLED = booleanPreferencesKey("haptic_enabled")
        val SOUND_ENABLED = booleanPreferencesKey("sound_enabled")
        val DAILY_WORKOUT_GOAL = intPreferencesKey("daily_workout_goal")
        val WEEKLY_PROGRESS = stringPreferencesKey("weekly_progress")
        val STREAK = intPreferencesKey("streak")
        val STEP_COUNT = intPreferencesKey("step_count")
        val LAST_SAVE_DATE = stringPreferencesKey("last_save_date")
        val WORKOUT_HISTORY = stringPreferencesKey("workout_history")
    }

    // Settings flow
    val settingsFlow: Flow<WorkoutSettings> = context.dataStore.data.map { prefs ->
        WorkoutSettings(
            workoutType = WorkoutType.fromName(prefs[Keys.WORKOUT_TYPE] ?: WorkoutType.CUSTOM.name),
            workDuration = prefs[Keys.WORK_DURATION] ?: 5,
            restDuration = prefs[Keys.REST_DURATION] ?: 5,
            reps = prefs[Keys.REPS] ?: 10,
            sets = prefs[Keys.SETS] ?: 1,
            voiceGender = VoiceGender.fromName(prefs[Keys.VOICE_GENDER] ?: VoiceGender.FEMALE.name),
            hapticEnabled = prefs[Keys.HAPTIC_ENABLED] ?: false,
            soundEnabled = prefs[Keys.SOUND_ENABLED] ?: true,
            dailyWorkoutGoal = prefs[Keys.DAILY_WORKOUT_GOAL] ?: 3
        )
    }

    // Weekly progress flow
    val weeklyProgressFlow: Flow<WeeklyProgress> = context.dataStore.data.map { prefs ->
        val progressJson = prefs[Keys.WEEKLY_PROGRESS] ?: "[]"
        val days = try {
            val arr = JSONArray(progressJson)
            (0 until arr.length()).map { arr.getString(it) }
        } catch (e: Exception) {
            emptyList()
        }
        WeeklyProgress(
            completedDays = days,
            streak = prefs[Keys.STREAK] ?: 0
        )
    }

    // Wellness data flow (steps only)
    val wellnessFlow: Flow<WellnessData> = context.dataStore.data.map { prefs ->
        val today = LocalDate.now().format(DateTimeFormatter.ISO_LOCAL_DATE)
        val lastSave = prefs[Keys.LAST_SAVE_DATE] ?: ""

        // Reset if new day
        if (lastSave != today) {
            WellnessData(steps = 0, lastSaveDate = today)
        } else {
            WellnessData(
                steps = prefs[Keys.STEP_COUNT] ?: 0,
                lastSaveDate = lastSave
            )
        }
    }

    // Workout history flow
    val workoutHistoryFlow: Flow<WorkoutHistory> = context.dataStore.data.map { prefs ->
        val historyJson = prefs[Keys.WORKOUT_HISTORY] ?: "[]"
        val entries = try {
            val arr = JSONArray(historyJson)
            (0 until arr.length()).map { i ->
                val obj = arr.getJSONObject(i)
                WorkoutEntry(
                    date = obj.getString("date"),
                    workoutType = WorkoutType.fromName(obj.getString("workoutType")),
                    completedSets = obj.getInt("completedSets"),
                    totalReps = obj.getInt("totalReps"),
                    timestamp = obj.optLong("timestamp", System.currentTimeMillis())
                )
            }
        } catch (e: Exception) {
            emptyList()
        }
        WorkoutHistory(entries = entries)
    }

    // Save workout settings
    suspend fun saveSettings(settings: WorkoutSettings) {
        context.dataStore.edit { prefs ->
            prefs[Keys.WORKOUT_TYPE] = settings.workoutType.name
            prefs[Keys.WORK_DURATION] = settings.workDuration
            prefs[Keys.REST_DURATION] = settings.restDuration
            prefs[Keys.REPS] = settings.reps
            prefs[Keys.SETS] = settings.sets
            prefs[Keys.VOICE_GENDER] = settings.voiceGender.name
            prefs[Keys.HAPTIC_ENABLED] = settings.hapticEnabled
            prefs[Keys.SOUND_ENABLED] = settings.soundEnabled
            prefs[Keys.DAILY_WORKOUT_GOAL] = settings.dailyWorkoutGoal
        }
    }

    // Save weekly progress
    suspend fun saveWeeklyProgress(progress: WeeklyProgress) {
        context.dataStore.edit { prefs ->
            val arr = JSONArray()
            progress.completedDays.forEach { arr.put(it) }
            prefs[Keys.WEEKLY_PROGRESS] = arr.toString()
            prefs[Keys.STREAK] = progress.streak
        }
    }

    // Mark day as complete
    suspend fun markDayComplete() {
        context.dataStore.edit { prefs ->
            val today = LocalDate.now().format(DateTimeFormatter.ISO_LOCAL_DATE)
            val weekStart = getWeekStart()

            // Get current progress
            val progressJson = prefs[Keys.WEEKLY_PROGRESS] ?: "[]"
            val days = try {
                val arr = JSONArray(progressJson)
                (0 until arr.length()).map { arr.getString(it) }.toMutableList()
            } catch (e: Exception) {
                mutableListOf()
            }

            // Reset if new week
            if (days.isEmpty() || days.firstOrNull()?.let { it < weekStart } == true) {
                days.clear()
            }

            // Add today if not already completed
            if (!days.contains(today)) {
                days.add(today)
                prefs[Keys.STREAK] = (prefs[Keys.STREAK] ?: 0) + 1
            }

            val arr = JSONArray()
            days.forEach { arr.put(it) }
            prefs[Keys.WEEKLY_PROGRESS] = arr.toString()
        }
    }

    // Save wellness data
    suspend fun saveWellnessData(data: WellnessData) {
        context.dataStore.edit { prefs ->
            val today = LocalDate.now().format(DateTimeFormatter.ISO_LOCAL_DATE)
            prefs[Keys.STEP_COUNT] = data.steps
            prefs[Keys.LAST_SAVE_DATE] = today
        }
    }

    // Add steps
    suspend fun addSteps(steps: Int) {
        context.dataStore.edit { prefs ->
            val today = LocalDate.now().format(DateTimeFormatter.ISO_LOCAL_DATE)
            val lastSave = prefs[Keys.LAST_SAVE_DATE] ?: ""

            // Reset if new day
            val currentSteps = if (lastSave != today) 0 else (prefs[Keys.STEP_COUNT] ?: 0)

            prefs[Keys.STEP_COUNT] = currentSteps + steps
            prefs[Keys.LAST_SAVE_DATE] = today
        }
    }

    // Add workout entry to history
    suspend fun addWorkoutEntry(entry: WorkoutEntry) {
        context.dataStore.edit { prefs ->
            val historyJson = prefs[Keys.WORKOUT_HISTORY] ?: "[]"
            val arr = try {
                JSONArray(historyJson)
            } catch (e: Exception) {
                JSONArray()
            }

            val newEntry = JSONObject().apply {
                put("date", entry.date)
                put("workoutType", entry.workoutType.name)
                put("completedSets", entry.completedSets)
                put("totalReps", entry.totalReps)
                put("timestamp", entry.timestamp)
            }
            arr.put(newEntry)

            // Keep only last 90 days of history
            val cutoffDate = LocalDate.now().minusDays(90).format(DateTimeFormatter.ISO_LOCAL_DATE)
            val filteredArr = JSONArray()
            for (i in 0 until arr.length()) {
                val obj = arr.getJSONObject(i)
                if (obj.getString("date") >= cutoffDate) {
                    filteredArr.put(obj)
                }
            }

            prefs[Keys.WORKOUT_HISTORY] = filteredArr.toString()
        }
    }

    private fun getWeekStart(): String {
        val today = LocalDate.now()
        val dayOfWeek = today.dayOfWeek.value % 7 // Sunday = 0
        val weekStart = today.minusDays(dayOfWeek.toLong())
        return weekStart.format(DateTimeFormatter.ISO_LOCAL_DATE)
    }
}
