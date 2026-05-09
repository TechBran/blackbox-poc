package com.workoutvibe.app.data

/**
 * Voice gender options for TTS
 */
enum class VoiceGender(val displayName: String, val prefix: String) {
    FEMALE("Female", "f"),
    MALE("Male", "m");

    companion object {
        fun fromName(name: String): VoiceGender {
            return entries.find { it.name == name } ?: FEMALE
        }
    }
}
