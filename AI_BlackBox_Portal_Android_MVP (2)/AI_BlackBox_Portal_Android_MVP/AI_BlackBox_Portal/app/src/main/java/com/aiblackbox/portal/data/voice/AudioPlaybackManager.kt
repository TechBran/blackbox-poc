package com.aiblackbox.portal.data.voice

import android.media.MediaPlayer
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Singleton audio playback manager — survives composable disposal (scrolling).
 * Only stops when explicitly paused/stopped or when release() is called (app close).
 */
object AudioPlaybackManager {
    private const val TAG = "AudioPlayback"

    private var mediaPlayer: MediaPlayer? = null
    private var currentUrl: String? = null
    private var autoPlayOnPrepare = false

    private val _isPlaying = MutableStateFlow(false)
    val isPlaying: StateFlow<Boolean> = _isPlaying.asStateFlow()

    private val _isPrepared = MutableStateFlow(false)
    val isPrepared: StateFlow<Boolean> = _isPrepared.asStateFlow()

    private val _duration = MutableStateFlow(0L)
    val duration: StateFlow<Long> = _duration.asStateFlow()

    private val _position = MutableStateFlow(0f)
    val position: StateFlow<Float> = _position.asStateFlow()

    private val _activeUrl = MutableStateFlow<String?>(null)
    val activeUrl: StateFlow<String?> = _activeUrl.asStateFlow()

    private val _hasError = MutableStateFlow(false)
    val hasError: StateFlow<Boolean> = _hasError.asStateFlow()

    /** Load and prepare audio from URL. If already loaded, does nothing. */
    fun load(url: String) {
        if (url == currentUrl && mediaPlayer != null) return
        stop()
        currentUrl = url
        _activeUrl.value = url
        _hasError.value = false
        _isPrepared.value = false
        _position.value = 0f
        autoPlayOnPrepare = false

        try {
            val player = MediaPlayer()
            player.setDataSource(url)
            player.setOnPreparedListener { mp ->
                _duration.value = mp.duration.toLong()
                _isPrepared.value = true
                // Auto-play if play() was called before prepare finished
                if (autoPlayOnPrepare) {
                    autoPlayOnPrepare = false
                    mp.start()
                    _isPlaying.value = true
                }
            }
            player.setOnCompletionListener {
                _isPlaying.value = false
                _position.value = 0f
                try { it.seekTo(0) } catch (_: Exception) {}
            }
            player.setOnErrorListener { _, what, extra ->
                Log.e(TAG, "MediaPlayer error: what=$what extra=$extra url=$url")
                _hasError.value = true
                _isPlaying.value = false
                autoPlayOnPrepare = false
                true
            }
            player.prepareAsync()
            mediaPlayer = player
        } catch (e: Exception) {
            Log.e(TAG, "Failed to load: ${e.message}", e)
            _hasError.value = true
        }
    }

    /** Load and immediately play (queues auto-play if still preparing) */
    fun loadAndPlay(url: String) {
        if (url == currentUrl && mediaPlayer != null && _isPrepared.value) {
            // Already loaded and ready — just play
            play()
            return
        }
        load(url)
        autoPlayOnPrepare = true
    }

    fun play() {
        val mp = mediaPlayer ?: return
        if (!_isPrepared.value) {
            // Not ready yet — queue it
            autoPlayOnPrepare = true
            return
        }
        try {
            mp.start()
            _isPlaying.value = true
        } catch (e: Exception) {
            Log.e(TAG, "Play failed: ${e.message}")
            _hasError.value = true
        }
    }

    fun pause() {
        autoPlayOnPrepare = false
        try { mediaPlayer?.pause() } catch (_: Exception) {}
        _isPlaying.value = false
    }

    fun togglePlayPause() {
        if (_isPlaying.value) pause() else play()
    }

    fun seekTo(fraction: Float) {
        val mp = mediaPlayer ?: return
        if (!_isPrepared.value) return
        val seekMs = (fraction * mp.duration).toInt()
        mp.seekTo(seekMs)
        _position.value = fraction
    }

    /** Update position — call from a polling coroutine */
    fun updatePosition() {
        try {
            val mp = mediaPlayer
            if (mp != null && _isPrepared.value && _isPlaying.value) {
                val dur = mp.duration.toLong()
                if (dur > 0) {
                    _position.value = mp.currentPosition.toFloat() / dur.toFloat()
                }
            }
        } catch (_: Exception) {}
    }

    fun stop() {
        try {
            mediaPlayer?.let { mp ->
                if (mp.isPlaying) mp.stop()
                mp.release()
            }
        } catch (_: Exception) {}
        mediaPlayer = null
        currentUrl = null
        _activeUrl.value = null
        _isPlaying.value = false
        _isPrepared.value = false
        _duration.value = 0L
        _position.value = 0f
        _hasError.value = false
    }

    /** Call from Activity onDestroy */
    fun release() {
        stop()
    }
}
