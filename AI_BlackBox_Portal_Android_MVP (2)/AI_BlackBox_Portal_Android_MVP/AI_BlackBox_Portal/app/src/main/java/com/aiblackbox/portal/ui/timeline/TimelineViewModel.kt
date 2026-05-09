package com.aiblackbox.portal.ui.timeline

import android.app.Application
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.aiblackbox.portal.data.api.BlackBoxApi
import com.aiblackbox.portal.data.model.SearchResponse
import com.aiblackbox.portal.data.model.SnapshotResult
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

private const val TAG = "TimelineVM"
private const val POLL_INTERVAL_MS = 10_000L  // Refresh every 10 seconds

class TimelineViewModel(application: Application) : AndroidViewModel(application) {
    private var api: BlackBoxApi? = null
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }
    private var pollJob: Job? = null
    private var currentOperator = ""

    private val _results = MutableStateFlow<List<SnapshotResult>>(emptyList())
    val results: StateFlow<List<SnapshotResult>> = _results.asStateFlow()

    private val _isSearching = MutableStateFlow(false)
    val isSearching: StateFlow<Boolean> = _isSearching.asStateFlow()

    // Tracks whether user is actively searching (disables auto-poll to avoid overwriting results)
    private var isUserSearching = false

    private val _selectedSnapshot = MutableStateFlow<SnapshotResult?>(null)
    val selectedSnapshot: StateFlow<SnapshotResult?> = _selectedSnapshot.asStateFlow()

    // Full snapshot content (fetched on demand when user opens detail)
    private val _fullContent = MutableStateFlow<String?>(null)
    val fullContent: StateFlow<String?> = _fullContent.asStateFlow()

    private val _isLoadingContent = MutableStateFlow(false)
    val isLoadingContent: StateFlow<Boolean> = _isLoadingContent.asStateFlow()

    fun initialize(origin: String) {
        if (origin.isBlank() || api != null) return
        api = BlackBoxApi(origin)
    }

    fun search(query: String, operator: String? = null) {
        val api = api ?: return
        isUserSearching = query.isNotBlank()
        _isSearching.value = true
        viewModelScope.launch {
            try {
                val params = buildString {
                    append("?q=")
                    append(java.net.URLEncoder.encode(query, "UTF-8"))
                    append("&limit=10")
                    if (!operator.isNullOrBlank()) append("&operator=$operator")
                }
                val response = api.get("/fossil/hybrid$params")
                val parsed = json.decodeFromString(SearchResponse.serializer(), response)
                _results.value = parsed.results
            } catch (e: Exception) {
                Log.e(TAG, "search failed: ${e.message}")
                _results.value = emptyList()
            } finally {
                _isSearching.value = false
            }
        }
    }

    /** Load recent snapshots. Set silent=true for background refresh (no loading spinner). */
    /** Load recent snapshots chronologically (newest first) from dedicated endpoint. */
    fun loadRecent(operator: String, silent: Boolean = false) {
        val api = api ?: return
        currentOperator = operator
        if (!silent) _isSearching.value = true
        viewModelScope.launch {
            try {
                val params = buildString {
                    append("?limit=30")
                    if (operator.isNotBlank()) append("&operator=$operator")
                }
                val response = api.get("/api/snapshots/recent$params")
                val parsed = json.decodeFromString(SearchResponse.serializer(), response)
                _results.value = parsed.results
            } catch (e: Exception) {
                if (!silent) Log.e(TAG, "loadRecent failed: ${e.message}")
                if (!silent) _results.value = emptyList()
            } finally {
                if (!silent) _isSearching.value = false
            }
        }
    }

    /** Start auto-polling for live updates. Call when timeline screen becomes visible. */
    fun startPolling(operator: String) {
        currentOperator = operator
        isUserSearching = false
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            while (isActive) {
                delay(POLL_INTERVAL_MS)
                // Don't overwrite search results with poll results
                if (!isUserSearching) {
                    loadRecent(currentOperator, silent = true)
                }
            }
        }
    }

    /** Stop auto-polling. Call when timeline screen is no longer visible. */
    fun stopPolling() {
        pollJob?.cancel()
        pollJob = null
    }

    /** Clear search and resume live feed. */
    fun clearSearch(operator: String) {
        isUserSearching = false
        currentOperator = operator
        loadRecent(operator)
    }

    /**
     * Select a snapshot and fetch its full content from /fossil/snapshot/{id}
     */
    fun selectSnapshot(snapshot: SnapshotResult?) {
        _selectedSnapshot.value = snapshot
        _fullContent.value = null

        if (snapshot != null) {
            fetchFullContent(snapshot.snapId)
        }
    }

    /**
     * Navigate to a specific snapshot by ID (for clickable SNAP-XXXXX links)
     */
    fun navigateToSnapshot(snapId: String) {
        val api = api ?: return
        _isLoadingContent.value = true
        viewModelScope.launch {
            try {
                val response = api.get("/fossil/snapshot/$snapId")
                val obj = json.parseToJsonElement(response).jsonObject
                val content = obj["content"]?.jsonPrimitive?.content ?: ""
                val meta = obj["metadata"]?.jsonObject
                val operator = meta?.get("operator")?.jsonPrimitive?.content ?: ""
                val timestamp = meta?.get("timestamp")?.jsonPrimitive?.content ?: ""
                val type = meta?.get("type")?.jsonPrimitive?.content ?: ""

                val snap = SnapshotResult(
                    snapId = snapId,
                    operator = operator,
                    timestamp = timestamp,
                    snippet = content,
                    type = type
                )
                _selectedSnapshot.value = snap
                _fullContent.value = content
            } catch (e: Exception) {
                Log.e(TAG, "navigateToSnapshot failed for $snapId: ${e.message}")
            } finally {
                _isLoadingContent.value = false
            }
        }
    }

    private fun fetchFullContent(snapId: String) {
        val api = api ?: return
        _isLoadingContent.value = true
        viewModelScope.launch {
            try {
                val response = api.get("/fossil/snapshot/$snapId")
                val obj = json.parseToJsonElement(response).jsonObject
                _fullContent.value = obj["content"]?.jsonPrimitive?.content ?: ""
            } catch (e: Exception) {
                Log.e(TAG, "fetchFullContent failed for $snapId: ${e.message}")
                // Fallback to snippet
                _fullContent.value = _selectedSnapshot.value?.snippet
            } finally {
                _isLoadingContent.value = false
            }
        }
    }
}
