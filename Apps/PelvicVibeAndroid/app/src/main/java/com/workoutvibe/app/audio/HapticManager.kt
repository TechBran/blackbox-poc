package com.workoutvibe.app.audio

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/**
 * Manages haptic feedback for the app
 */
class HapticManager(context: Context) {

    private val vibrator: Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val vibratorManager = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
        vibratorManager.defaultVibrator
    } else {
        @Suppress("DEPRECATION")
        context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
    }

    /**
     * Light haptic feedback for UI interactions
     */
    fun lightTap() {
        vibrate(50)
    }

    /**
     * Medium haptic feedback for phase changes
     */
    fun mediumTap() {
        vibrate(100)
    }

    /**
     * Success pattern for workout completion
     */
    fun successPattern() {
        vibratePattern(longArrayOf(0, 50, 50, 100))
    }

    /**
     * Work phase start haptic
     */
    fun workStart() {
        vibrate(100)
    }

    /**
     * Rest phase start haptic
     */
    fun restStart() {
        vibrate(100)
    }

    private fun vibrate(duration: Long) {
        vibrator?.let {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                it.vibrate(VibrationEffect.createOneShot(duration, VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                it.vibrate(duration)
            }
        }
    }

    private fun vibratePattern(pattern: LongArray) {
        vibrator?.let {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                it.vibrate(VibrationEffect.createWaveform(pattern, -1))
            } else {
                @Suppress("DEPRECATION")
                it.vibrate(pattern, -1)
            }
        }
    }

    /**
     * Check if haptic feedback is available
     */
    fun hasVibrator(): Boolean = vibrator?.hasVibrator() == true
}
