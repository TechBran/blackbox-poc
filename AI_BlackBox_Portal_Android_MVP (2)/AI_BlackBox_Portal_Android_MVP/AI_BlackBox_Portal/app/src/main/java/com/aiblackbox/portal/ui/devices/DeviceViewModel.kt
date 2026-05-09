package com.aiblackbox.portal.ui.devices

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.aiblackbox.portal.data.api.BlackBoxApi
import com.aiblackbox.portal.data.model.AdbDevice
import com.aiblackbox.portal.data.model.AdbDevicesResponse
import com.aiblackbox.portal.data.model.Device
import com.aiblackbox.portal.data.model.DevicesResponse
import com.aiblackbox.portal.data.model.HealthCheckResponse
import com.aiblackbox.portal.data.model.SyncTailscaleResponse
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json

class DeviceViewModel(application: Application) : AndroidViewModel(application) {
    private var api: BlackBoxApi? = null
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    // -- Raw state --
    private val _allDevices = MutableStateFlow<List<Device>>(emptyList())
    private val _adbConnected = MutableStateFlow<List<AdbDevice>>(emptyList())

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    // -- Search & filter --
    private val _searchQuery = MutableStateFlow("")
    val searchQuery: StateFlow<String> = _searchQuery.asStateFlow()

    private val _typeFilter = MutableStateFlow("all")
    val typeFilter: StateFlow<String> = _typeFilter.asStateFlow()

    // -- Derived filtered list --
    private val _filteredDevices = MutableStateFlow<List<Device>>(emptyList())
    val filteredDevices: StateFlow<List<Device>> = _filteredDevices.asStateFlow()

    // -- ADB connected serials for fast lookup --
    private val _connectedSerials = MutableStateFlow<Set<String>>(emptySet())
    val connectedSerials: StateFlow<Set<String>> = _connectedSerials.asStateFlow()

    // -- Sync state --
    private val _isSyncing = MutableStateFlow(false)
    val isSyncing: StateFlow<Boolean> = _isSyncing.asStateFlow()

    // -- Action feedback --
    private val _actionMessage = MutableStateFlow<String?>(null)
    val actionMessage: StateFlow<String?> = _actionMessage.asStateFlow()

    // -- Delete confirmation --
    private val _showDeleteConfirm = MutableStateFlow<String?>(null)
    val showDeleteConfirm: StateFlow<String?> = _showDeleteConfirm.asStateFlow()

    // -- Polling --
    private var pollJob: Job? = null

    init {
        // Combine devices + search + type filter into filtered list
        viewModelScope.launch {
            combine(_allDevices, _searchQuery, _typeFilter) { devices, query, typeF ->
                var result = devices
                if (typeF != "all") {
                    result = result.filter { it.deviceType == typeF }
                }
                if (query.isNotBlank()) {
                    val q = query.lowercase()
                    result = result.filter {
                        it.name.lowercase().contains(q) ||
                                it.id.lowercase().contains(q) ||
                                (it.description ?: "").lowercase().contains(q) ||
                                it.tailscaleIp.contains(q)
                    }
                }
                result
            }.collect { _filteredDevices.value = it }
        }

        // Track connected ADB serials
        viewModelScope.launch {
            _adbConnected.collect { adbDevs ->
                _connectedSerials.value = adbDevs
                    .filter { it.state == "device" }
                    .map { it.serial }
                    .toSet()
            }
        }
    }

    fun initialize(origin: String) {
        if (origin.isBlank() || api != null) return
        api = BlackBoxApi(origin)
        loadDevices()
        startPolling()
    }

    // -------------------------------------------------------------------------
    // Search & Filter
    // -------------------------------------------------------------------------

    fun setSearchQuery(query: String) {
        _searchQuery.value = query
    }

    fun setTypeFilter(filter: String) {
        _typeFilter.value = filter
    }

    // -------------------------------------------------------------------------
    // Load
    // -------------------------------------------------------------------------

    fun loadDevices() {
        val api = api ?: return
        _isLoading.value = true
        viewModelScope.launch {
            try {
                val devResponse = api.get("/devices/")
                val parsed = json.decodeFromString(DevicesResponse.serializer(), devResponse)
                _allDevices.value = parsed.devices

                // Also load ADB connected list
                try {
                    val adbResponse = api.get("/adb/devices")
                    val adbParsed = json.decodeFromString(AdbDevicesResponse.serializer(), adbResponse)
                    _adbConnected.value = adbParsed.devices
                } catch (_: Exception) {
                    _adbConnected.value = emptyList()
                }

                _error.value = null
            } catch (e: Exception) {
                _error.value = "Failed to load devices: ${e.message}"
                _allDevices.value = emptyList()
            } finally {
                _isLoading.value = false
            }
        }
    }

    // -------------------------------------------------------------------------
    // ADB Connect / Disconnect
    // -------------------------------------------------------------------------

    fun isAdbConnected(device: Device): Boolean {
        val connStr = "${device.tailscaleIp}:${if (device.adbPort > 0) device.adbPort else 5555}"
        return connStr in _connectedSerials.value
    }

    fun adbConnect(deviceId: String) {
        val api = api ?: return
        viewModelScope.launch {
            try {
                _actionMessage.value = "Connecting..."
                api.post("/adb/connect/$deviceId", "{}")
                _actionMessage.value = "Connected"
                loadDevices()
            } catch (e: Exception) {
                _actionMessage.value = "Connection failed: ${e.message}"
            }
        }
    }

    fun adbDisconnect(deviceId: String) {
        val api = api ?: return
        viewModelScope.launch {
            try {
                api.post("/adb/disconnect/$deviceId", "{}")
                _actionMessage.value = "Disconnected"
                loadDevices()
            } catch (e: Exception) {
                _actionMessage.value = "Disconnect failed: ${e.message}"
            }
        }
    }

    // -------------------------------------------------------------------------
    // Tailscale Sync
    // -------------------------------------------------------------------------

    fun syncTailscale() {
        val api = api ?: return
        if (_isSyncing.value) return
        _isSyncing.value = true
        viewModelScope.launch {
            try {
                val response = api.post("/devices/sync-tailscale", "{}")
                val parsed = json.decodeFromString(SyncTailscaleResponse.serializer(), response)
                val added = parsed.results.values.count { it == "added" }
                val updated = parsed.results.values.count { it == "updated" }
                _actionMessage.value = "Synced: $added added, $updated updated (${parsed.totalDevices} total)"
                loadDevices()
            } catch (e: Exception) {
                _actionMessage.value = "Sync failed: ${e.message}"
            } finally {
                _isSyncing.value = false
            }
        }
    }

    // -------------------------------------------------------------------------
    // Health Check
    // -------------------------------------------------------------------------

    fun checkHealth(deviceId: String) {
        val api = api ?: return
        viewModelScope.launch {
            try {
                _actionMessage.value = "Checking health..."
                val response = api.get("/devices/$deviceId/health")
                val parsed = json.decodeFromString(HealthCheckResponse.serializer(), response)
                _actionMessage.value = "Status: ${parsed.status}"
                loadDevices()
            } catch (e: Exception) {
                _actionMessage.value = "Health check failed: ${e.message}"
            }
        }
    }

    // -------------------------------------------------------------------------
    // Remove Device
    // -------------------------------------------------------------------------

    fun requestRemove(deviceId: String) {
        _showDeleteConfirm.value = deviceId
    }

    fun cancelRemove() {
        _showDeleteConfirm.value = null
    }

    fun confirmRemove() {
        val deviceId = _showDeleteConfirm.value ?: return
        _showDeleteConfirm.value = null
        removeDevice(deviceId)
    }

    private fun removeDevice(deviceId: String) {
        val api = api ?: return
        viewModelScope.launch {
            try {
                api.delete("/devices/$deviceId")
                _actionMessage.value = "Device removed"
                loadDevices()
            } catch (e: Exception) {
                _actionMessage.value = "Remove failed: ${e.message}"
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
                delay(10000)
                try {
                    val devResponse = api?.get("/devices/") ?: continue
                    val parsed = json.decodeFromString(DevicesResponse.serializer(), devResponse)
                    _allDevices.value = parsed.devices

                    try {
                        val adbResponse = api?.get("/adb/devices") ?: continue
                        val adbParsed = json.decodeFromString(AdbDevicesResponse.serializer(), adbResponse)
                        _adbConnected.value = adbParsed.devices
                    } catch (_: Exception) {}
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
