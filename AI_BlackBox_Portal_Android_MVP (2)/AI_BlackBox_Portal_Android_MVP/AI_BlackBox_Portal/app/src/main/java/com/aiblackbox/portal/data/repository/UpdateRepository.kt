package com.aiblackbox.portal.data.repository

import android.util.Log
import com.aiblackbox.portal.data.api.BlackBoxApi
import com.aiblackbox.portal.data.api.SSEClient
import com.aiblackbox.portal.data.api.SSEEvent
import com.aiblackbox.portal.data.model.UpdateRollbackResponse
import com.aiblackbox.portal.data.model.UpdateStartRequest
import com.aiblackbox.portal.data.model.UpdateStartResponse
import com.aiblackbox.portal.data.model.UpdateStatus
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.Json

/**
 * Repository for the update pipeline backend (Orchestrator/routes/update_routes.py).
 *
 * - getStatus() / preflight() return the same shape (UpdateStatus). preflight()
 *   forces a fresh git fetch and busts the backend's 60s cache.
 * - start() POSTs /update/start; client must then open streamLog() with the
 *   returned task_id.
 * - streamLog() returns a Flow<SSEEvent> from /update/log/stream — each event
 *   carries a JSON payload to be parsed by the caller (phase / log / heartbeat /
 *   complete events have different shapes).
 * - rollback() reverts to the last pre-update-<ts> tag.
 * - healthOk() probes /health (used by the restart-detection poll loop).
 */
class UpdateRepository(private val api: BlackBoxApi) {

    private val sseClient = SSEClient(api)
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    suspend fun getStatus(): UpdateStatus = decode(api.get("/update/status"))

    suspend fun preflight(): UpdateStatus = decode(api.post("/update/preflight", "{}"))

    suspend fun start(confirmSha: String? = null): UpdateStartResponse {
        val body = json.encodeToString(
            UpdateStartRequest.serializer(),
            UpdateStartRequest(confirmSha = confirmSha)
        )
        return json.decodeFromString(UpdateStartResponse.serializer(), api.post("/update/start", body))
    }

    suspend fun rollback(): UpdateRollbackResponse =
        json.decodeFromString(UpdateRollbackResponse.serializer(), api.post("/update/rollback", "{}"))

    fun streamLog(taskId: String): Flow<SSEEvent> =
        sseClient.streamGet("/update/log/stream", mapOf("task_id" to taskId))

    /** Plain GET on /health — caller treats any IOException or non-200 as "not ready yet." */
    suspend fun healthOk(): Boolean {
        return try {
            api.get("/health").isNotBlank()
        } catch (e: Exception) {
            Log.d(TAG, "health probe failed (expected during restart): ${e.message}")
            false
        }
    }

    private fun decode(jsonText: String): UpdateStatus =
        json.decodeFromString(UpdateStatus.serializer(), jsonText)

    companion object { private const val TAG = "UpdateRepository" }
}
