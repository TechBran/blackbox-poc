package com.aiblackbox.portal.ui.cron

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.aiblackbox.portal.data.api.BlackBoxApi
import com.aiblackbox.portal.data.model.CronHistoryEntry
import com.aiblackbox.portal.data.model.CronHistoryResponse
import com.aiblackbox.portal.data.model.CronJob
import com.aiblackbox.portal.data.model.CronJobCreateRequest
import com.aiblackbox.portal.data.model.CronJobsResponse
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

class CronViewModel(application: Application) : AndroidViewModel(application) {
    private var api: BlackBoxApi? = null
    private val json = Json { ignoreUnknownKeys = true; isLenient = true; encodeDefaults = true }

    // -- Raw state --
    private val _allJobs = MutableStateFlow<List<CronJob>>(emptyList())
    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    // -- Search & filter --
    private val _searchQuery = MutableStateFlow("")
    val searchQuery: StateFlow<String> = _searchQuery.asStateFlow()

    private val _statusFilter = MutableStateFlow("all")
    val statusFilter: StateFlow<String> = _statusFilter.asStateFlow()

    // -- Derived filtered list --
    private val _filteredJobs = MutableStateFlow<List<CronJob>>(emptyList())
    val filteredJobs: StateFlow<List<CronJob>> = _filteredJobs.asStateFlow()

    // -- History --
    private val _historyEntries = MutableStateFlow<List<CronHistoryEntry>>(emptyList())
    val historyEntries: StateFlow<List<CronHistoryEntry>> = _historyEntries.asStateFlow()

    private val _historyLoading = MutableStateFlow(false)
    val historyLoading: StateFlow<Boolean> = _historyLoading.asStateFlow()

    // -- Edit/Create dialog state --
    private val _editingJob = MutableStateFlow<CronJob?>(null)
    val editingJob: StateFlow<CronJob?> = _editingJob.asStateFlow()

    private val _showEditDialog = MutableStateFlow(false)
    val showEditDialog: StateFlow<Boolean> = _showEditDialog.asStateFlow()

    private val _showHistoryDialog = MutableStateFlow(false)
    val showHistoryDialog: StateFlow<Boolean> = _showHistoryDialog.asStateFlow()

    private val _showDeleteConfirm = MutableStateFlow<String?>(null)
    val showDeleteConfirm: StateFlow<String?> = _showDeleteConfirm.asStateFlow()

    private val _isSaving = MutableStateFlow(false)
    val isSaving: StateFlow<Boolean> = _isSaving.asStateFlow()

    private val _actionMessage = MutableStateFlow<String?>(null)
    val actionMessage: StateFlow<String?> = _actionMessage.asStateFlow()

    // -- Polling --
    private var pollJob: Job? = null

    init {
        // Combine raw jobs + search + filter into filtered list
        viewModelScope.launch {
            combine(_allJobs, _searchQuery, _statusFilter) { jobs, query, filter ->
                var result = jobs
                if (filter != "all") {
                    result = result.filter { it.status == filter }
                }
                if (query.isNotBlank()) {
                    val q = query.lowercase()
                    result = result.filter {
                        it.name.lowercase().contains(q) ||
                                it.prompt.lowercase().contains(q)
                    }
                }
                result
            }.collect { _filteredJobs.value = it }
        }
    }

    fun initialize(origin: String) {
        if (origin.isBlank() || api != null) return
        api = BlackBoxApi(origin)
        loadJobs()
        startPolling()
    }

    // -------------------------------------------------------------------------
    // Search & Filter
    // -------------------------------------------------------------------------

    fun setSearchQuery(query: String) {
        _searchQuery.value = query
    }

    fun setStatusFilter(filter: String) {
        _statusFilter.value = filter
        loadJobs()
    }

    // -------------------------------------------------------------------------
    // CRUD
    // -------------------------------------------------------------------------

    fun loadJobs() {
        val api = api ?: return
        _isLoading.value = true
        viewModelScope.launch {
            try {
                val status = _statusFilter.value
                val path = if (status != "all") "/api/cron/jobs?status=$status" else "/api/cron/jobs"
                val response = api.get(path)
                val parsed = json.decodeFromString(CronJobsResponse.serializer(), response)
                _allJobs.value = parsed.jobs
                _error.value = null
            } catch (e: Exception) {
                _error.value = "Failed to load jobs: ${e.message}"
                _allJobs.value = emptyList()
            } finally {
                _isLoading.value = false
            }
        }
    }

    fun runJob(jobId: String) {
        val api = api ?: return
        viewModelScope.launch {
            try {
                _actionMessage.value = "Running job..."
                api.post("/api/cron/jobs/$jobId/run", "{}")
                _actionMessage.value = "Job executed"
                loadJobs()
            } catch (e: Exception) {
                _actionMessage.value = "Failed to run: ${e.message}"
            }
        }
    }

    fun pauseJob(jobId: String) {
        val api = api ?: return
        viewModelScope.launch {
            try {
                api.post("/api/cron/jobs/$jobId/pause", "{}")
                _actionMessage.value = "Job paused"
                loadJobs()
            } catch (e: Exception) {
                _actionMessage.value = "Failed to pause: ${e.message}"
            }
        }
    }

    fun resumeJob(jobId: String) {
        val api = api ?: return
        viewModelScope.launch {
            try {
                api.post("/api/cron/jobs/$jobId/resume", "{}")
                _actionMessage.value = "Job resumed"
                loadJobs()
            } catch (e: Exception) {
                _actionMessage.value = "Failed to resume: ${e.message}"
            }
        }
    }

    fun requestDelete(jobId: String) {
        _showDeleteConfirm.value = jobId
    }

    fun cancelDelete() {
        _showDeleteConfirm.value = null
    }

    fun confirmDelete() {
        val jobId = _showDeleteConfirm.value ?: return
        _showDeleteConfirm.value = null
        deleteJob(jobId)
    }

    private fun deleteJob(jobId: String) {
        val api = api ?: return
        viewModelScope.launch {
            try {
                api.delete("/api/cron/jobs/$jobId")
                _actionMessage.value = "Job deleted"
                loadJobs()
            } catch (e: Exception) {
                _actionMessage.value = "Failed to delete: ${e.message}"
            }
        }
    }

    // -------------------------------------------------------------------------
    // Create / Edit Dialog
    // -------------------------------------------------------------------------

    fun openCreateDialog() {
        _editingJob.value = null
        _showEditDialog.value = true
    }

    fun openEditDialog(jobId: String) {
        _editingJob.value = _allJobs.value.find { it.id == jobId }
        _showEditDialog.value = true
    }

    fun dismissEditDialog() {
        _showEditDialog.value = false
        _editingJob.value = null
    }

    fun saveJob(
        name: String,
        prompt: String,
        schedule: String,
        frequencyHint: String,
        model: String,
        delivery: String,
        deliveryTarget: String,
        operator: String,
        oneShot: Boolean
    ) {
        val api = api ?: return
        if (_isSaving.value) return
        _isSaving.value = true

        viewModelScope.launch {
            try {
                val existingId = _editingJob.value?.id
                val requestBody = json.encodeToString(
                    CronJobCreateRequest(
                        name = name,
                        prompt = prompt,
                        schedule = schedule,
                        frequencyHint = frequencyHint.ifBlank { null },
                        model = model,
                        delivery = delivery,
                        deliveryTarget = deliveryTarget.ifBlank { null },
                        operator = operator,
                        oneShot = oneShot
                    )
                )

                if (existingId != null) {
                    api.put("/api/cron/jobs/$existingId", requestBody)
                    _actionMessage.value = "Job updated"
                } else {
                    api.post("/api/cron/jobs", requestBody)
                    _actionMessage.value = "Job created"
                }

                _showEditDialog.value = false
                _editingJob.value = null
                loadJobs()
            } catch (e: Exception) {
                _actionMessage.value = "Failed to save: ${e.message}"
            } finally {
                _isSaving.value = false
            }
        }
    }

    // -------------------------------------------------------------------------
    // History
    // -------------------------------------------------------------------------

    fun openHistory(jobId: String) {
        _showHistoryDialog.value = true
        loadHistory(jobId)
    }

    fun dismissHistory() {
        _showHistoryDialog.value = false
        _historyEntries.value = emptyList()
    }

    private fun loadHistory(jobId: String) {
        val api = api ?: return
        _historyLoading.value = true
        viewModelScope.launch {
            try {
                val response = api.get("/api/cron/jobs/$jobId/history")
                val parsed = json.decodeFromString(CronHistoryResponse.serializer(), response)
                _historyEntries.value = parsed.history
            } catch (_: Exception) {
                _historyEntries.value = emptyList()
            } finally {
                _historyLoading.value = false
            }
        }
    }

    // -------------------------------------------------------------------------
    // Polling
    // -------------------------------------------------------------------------

    private fun startPolling() {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            while (isActive) {
                delay(5000)
                try {
                    val status = _statusFilter.value
                    val path = if (status != "all") "/api/cron/jobs?status=$status" else "/api/cron/jobs"
                    val response = api?.get(path) ?: continue
                    val parsed = json.decodeFromString(CronJobsResponse.serializer(), response)
                    _allJobs.value = parsed.jobs
                } catch (_: Exception) {
                    // Silent poll failure
                }
            }
        }
    }

    fun clearActionMessage() {
        _actionMessage.value = null
    }

    override fun onCleared() {
        super.onCleared()
        pollJob?.cancel()
    }
}
