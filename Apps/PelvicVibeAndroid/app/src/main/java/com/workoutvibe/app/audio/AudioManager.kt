package com.workoutvibe.app.audio

import android.content.Context
import android.media.MediaPlayer
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.workoutvibe.app.data.VoiceGender
import com.workoutvibe.app.data.WorkoutType
import java.util.concurrent.ConcurrentLinkedQueue

/**
 * Manages all audio playback for the app with:
 * - Single audio stream (no overlapping)
 * - Priority queue system (announcements interrupt numbers)
 * - Volume levels (numbers quieter, announcements louder)
 * - Voice gender selection
 * - Workout-specific cues
 */
class WorkoutAudioManager(private val context: Context) {

    private var currentPlayer: MediaPlayer? = null
    private val handler = Handler(Looper.getMainLooper())

    // Audio queue for sequential playback
    private val audioQueue = ConcurrentLinkedQueue<AudioItem>()
    private var isPlaying = false

    // Track what's currently playing to allow interruption
    private var currentPriority = Priority.LOW

    // Current configuration
    private var voiceGender: VoiceGender = VoiceGender.FEMALE
    private var workoutType: WorkoutType = WorkoutType.CUSTOM

    // Volume levels
    object Volume {
        const val NUMBER = 0.5f      // Countdown numbers - quieter
        const val CUE = 0.85f        // Work/Rest cues
        const val ANNOUNCEMENT = 1.0f // Set complete, workout complete, keep going
    }

    // Priority levels for audio
    enum class Priority {
        LOW,      // Countdown numbers
        MEDIUM,   // Work/Rest cues
        HIGH      // Announcements (set complete, keep going, workout complete)
    }

    data class AudioItem(
        val resourceName: String,
        val volume: Float,
        val priority: Priority,
        val delayAfter: Long = 0,  // Pause after this audio completes
        val onComplete: (() -> Unit)? = null
    )

    /**
     * Set the voice gender for audio playback
     */
    fun setVoiceGender(gender: VoiceGender) {
        voiceGender = gender
    }

    /**
     * Set the workout type for appropriate cues
     */
    fun setWorkoutType(type: WorkoutType) {
        workoutType = type
    }

    /**
     * Play the initial countdown (3-2-1-Go!)
     */
    fun playCountdown(onComplete: () -> Unit) {
        stopAll()

        val resourceName = TTSIndex.getCountdownResource(voiceGender)
        val resId = getResourceIdWithFallback(resourceName)

        if (resId != 0) {
            val manager = this
            currentPlayer = MediaPlayer.create(context, resId).apply {
                setVolume(Volume.ANNOUNCEMENT, Volume.ANNOUNCEMENT)
                setOnCompletionListener {
                    release()
                    manager.currentPlayer = null
                    manager.isPlaying = false
                    manager.currentPriority = Priority.LOW
                    handler.postDelayed({ onComplete() }, 1500)
                }
                start()
            }
            currentPriority = Priority.HIGH
            isPlaying = true
        } else {
            handler.postDelayed({ onComplete() }, 2000)
        }
    }

    /**
     * Play the work phase cue (e.g., "Push Up!", "Squat!", "Go!")
     */
    fun playWorkCue(onComplete: () -> Unit = {}) {
        val resourceName = TTSIndex.getWorkCueResource(workoutType, voiceGender)
        queueAudio(AudioItem(
            resourceName = resourceName,
            volume = Volume.CUE,
            priority = Priority.MEDIUM,
            delayAfter = 600,
            onComplete = onComplete
        ))
    }

    /**
     * Play the rest phase cue (e.g., "Rest", "Walk", "Recover")
     */
    fun playRestCue(onComplete: () -> Unit = {}) {
        val resourceName = TTSIndex.getRestCueResource(workoutType, voiceGender)
        queueAudio(AudioItem(
            resourceName = resourceName,
            volume = Volume.CUE,
            priority = Priority.MEDIUM,
            delayAfter = 600,
            onComplete = onComplete
        ))
    }

    /**
     * Queues a countdown number (1-60) for playback.
     */
    fun playNumber(number: Int, onComplete: () -> Unit = {}) {
        if (number < 1 || number > 60) {
            onComplete()
            return
        }

        val resourceName = TTSIndex.getNumberResource(number, voiceGender)
        queueAudio(AudioItem(
            resourceName = resourceName,
            volume = Volume.NUMBER,
            priority = Priority.LOW,
            delayAfter = 1000,
            onComplete = onComplete
        ))
    }

    /**
     * Play the keep going encouragement - HIGH priority
     */
    fun playKeepGoing(onComplete: () -> Unit = {}) {
        val resourceName = TTSIndex.getKeepGoingResource(voiceGender)
        queueAudio(AudioItem(
            resourceName = resourceName,
            volume = Volume.ANNOUNCEMENT,
            priority = Priority.HIGH,
            delayAfter = 1000,
            onComplete = onComplete
        ))
    }

    /**
     * Play the halfway reminder - HIGH priority
     */
    fun playHalfway(onComplete: () -> Unit = {}) {
        val resourceName = TTSIndex.getHalfwayResource(voiceGender)
        queueAudio(AudioItem(
            resourceName = resourceName,
            volume = Volume.ANNOUNCEMENT,
            priority = Priority.HIGH,
            delayAfter = 1000,
            onComplete = onComplete
        ))
    }

    /**
     * Play set completion announcement - HIGH priority
     */
    fun playSetComplete(setNumber: Int) {
        if (setNumber < 1 || setNumber > 10) return

        stopAll()

        val resourceName = TTSIndex.getSetCompleteResource(setNumber, voiceGender)
        queueAudio(AudioItem(
            resourceName = resourceName,
            volume = Volume.ANNOUNCEMENT,
            priority = Priority.HIGH,
            delayAfter = 800
        ))
    }

    /**
     * Play workout complete announcement - HIGH priority
     */
    fun playWorkoutComplete() {
        stopAll()

        val resourceName = TTSIndex.getWorkoutCompleteResource(voiceGender)
        queueAudio(AudioItem(
            resourceName = resourceName,
            volume = Volume.ANNOUNCEMENT,
            priority = Priority.HIGH,
            delayAfter = 500
        ))
    }

    /**
     * Add an audio item to the queue and handle interruptions.
     */
    private fun queueAudio(item: AudioItem) {
        // If a high-priority item comes in, it must interrupt any low-priority sound
        if (item.priority > Priority.LOW && isPlaying && currentPriority == Priority.LOW) {
            Log.d("AudioManager", "High priority item interrupting a number.")
            audioQueue.removeIf { it.priority == Priority.LOW }
            stopCurrentAudio()
        }

        audioQueue.add(item)

        if (!isPlaying) {
            processQueue()
        }
    }

    /**
     * Process the next item in the queue
     */
    private fun processQueue() {
        val item = audioQueue.poll() ?: run {
            isPlaying = false
            currentPriority = Priority.LOW
            return
        }

        val resId = getResourceIdWithFallback(item.resourceName)
        if (resId == 0) {
            Log.w("AudioManager", "Resource not found: ${item.resourceName}")
            handler.post { processQueue() }
            return
        }

        isPlaying = true
        currentPriority = item.priority

        try {
            val manager = this
            currentPlayer = MediaPlayer.create(context, resId).apply {
                setVolume(item.volume, item.volume)
                setOnCompletionListener { mp ->
                    mp.release()
                    manager.currentPlayer = null

                    if (item.delayAfter > 0) {
                        handler.postDelayed({
                            item.onComplete?.invoke()
                            manager.processQueue()
                        }, item.delayAfter)
                    } else {
                        handler.post {
                            item.onComplete?.invoke()
                            manager.processQueue()
                        }
                    }
                }
                setOnErrorListener { mp, what, extra ->
                    Log.e("AudioManager", "MediaPlayer error: $what, $extra")
                    mp.release()
                    manager.currentPlayer = null
                    handler.post { manager.processQueue() }
                    true
                }
                start()
            }
        } catch (e: Exception) {
            Log.e("AudioManager", "Failed to play ${item.resourceName}: ${e.message}")
            currentPlayer = null
            handler.post { processQueue() }
        }
    }

    /**
     * Get resource ID with fallback to legacy naming
     */
    private fun getResourceIdWithFallback(name: String): Int {
        // Try the new prefixed name first
        var resId = context.resources.getIdentifier(name, "raw", context.packageName)

        // If not found, try legacy naming
        if (resId == 0) {
            val legacyName = TTSIndex.LegacyMapping.getLegacyName(name)
            if (legacyName != null) {
                resId = context.resources.getIdentifier(legacyName, "raw", context.packageName)
            }
        }

        return resId
    }

    /**
     * Stop current audio without clearing queue
     */
    private fun stopCurrentAudio() {
        currentPlayer?.let {
            try {
                if (it.isPlaying) {
                    it.stop()
                }
                it.release()
            } catch (e: Exception) {
                Log.w("AudioManager", "Error stopping player: ${e.message}")
            }
        }
        currentPlayer = null
        isPlaying = false
    }

    /**
     * Stop all audio and clear queue
     */
    fun stopAll() {
        audioQueue.clear()
        stopCurrentAudio()
        handler.removeCallbacksAndMessages(null)
        isPlaying = false
        currentPriority = Priority.LOW
    }

    /**
     * Stop the countdown (alias for stopAll for compatibility)
     */
    fun stopCountdown() {
        stopAll()
    }

    /**
     * Release all audio resources
     */
    fun release() {
        stopAll()
    }
}
