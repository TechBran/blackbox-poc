package com.aiblackbox.portal.data.voice

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import android.util.Log
import com.aiblackbox.portal.data.api.BlackBoxApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File

class AudioRecorderManager(private val context: Context) {
    private var mediaRecorder: MediaRecorder? = null
    private var audioFile: File? = null
    private var isRecording = false

    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    fun startRecording(): Boolean {
        try {
            val cacheDir = context.externalCacheDir ?: context.cacheDir
            audioFile = File(cacheDir, "recording_${System.currentTimeMillis()}.m4a")

            mediaRecorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                MediaRecorder(context)
            } else {
                @Suppress("DEPRECATION")
                MediaRecorder()
            }.apply {
                setAudioSource(MediaRecorder.AudioSource.MIC)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setAudioEncodingBitRate(128000)
                setAudioSamplingRate(44100)
                setOutputFile(audioFile!!.absolutePath)
                prepare()
                start()
            }

            isRecording = true
            Log.d("AudioRecorder", "Recording started: ${audioFile?.absolutePath}")
            return true
        } catch (e: Exception) {
            Log.e("AudioRecorder", "Failed to start recording: ${e.message}", e)
            cleanup()
            return false
        }
    }

    fun stopRecording(): File? {
        if (!isRecording) return null
        try {
            mediaRecorder?.stop()
            mediaRecorder?.release()
            mediaRecorder = null
            isRecording = false
            Log.d("AudioRecorder", "Recording stopped: ${audioFile?.absolutePath}")
            return audioFile
        } catch (e: Exception) {
            Log.e("AudioRecorder", "Failed to stop recording: ${e.message}", e)
            cleanup()
            return null
        }
    }

    /**
     * Upload recorded audio to BlackBox /stt endpoint (Whisper).
     * Returns transcribed text.
     */
    suspend fun transcribe(api: BlackBoxApi, audioFile: File): String = withContext(Dispatchers.IO) {
        try {
            val requestBody = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart(
                    "file",
                    audioFile.name,
                    audioFile.asRequestBody("audio/m4a".toMediaType())
                )
                .build()

            val request = okhttp3.Request.Builder()
                .url("${api.getBaseUrl()}/stt")
                .post(requestBody)
                .build()

            val response = api.getClient().newCall(request).execute()
            val body = response.body?.string() ?: return@withContext ""

            val obj = json.parseToJsonElement(body).jsonObject
            val text = obj["text"]?.jsonPrimitive?.content?.trim() ?: ""
            Log.d("AudioRecorder", "Transcription: $text")
            text
        } catch (e: Exception) {
            Log.e("AudioRecorder", "Transcription failed: ${e.message}", e)
            ""
        } finally {
            audioFile.delete()
        }
    }

    fun isCurrentlyRecording(): Boolean = isRecording

    /** Returns max amplitude since last call (0-32767). Safe to call at ~30-60fps. */
    fun getMaxAmplitude(): Int {
        if (!isRecording) return 0
        return try { mediaRecorder?.maxAmplitude ?: 0 } catch (_: Exception) { 0 }
    }

    private fun cleanup() {
        try {
            mediaRecorder?.release()
        } catch (_: Exception) {}
        mediaRecorder = null
        isRecording = false
        audioFile?.delete()
    }
}
