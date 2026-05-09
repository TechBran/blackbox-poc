package com.aiblackbox.portal.data.store

import android.content.Context
import android.util.Log
import com.aiblackbox.portal.data.model.UiMessage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Persistent chat history storage — matches Portal's localStorage pattern.
 *
 * Stores per-operator chat history as JSON files in app internal storage.
 * Fast file I/O, no database overhead, survives app kills.
 *
 * Portal equivalent: localStorage key "bb_history_{operator}"
 * Max items: 100 (matches Portal MAX_HISTORY_ITEMS)
 */
class ChatHistoryStore(private val context: Context) {

    companion object {
        private const val TAG = "ChatHistory"
        private const val DIR_NAME = "chat_history"
        const val MAX_HISTORY_ITEMS = 100
    }

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        encodeDefaults = true
    }

    private val historyDir: File
        get() = File(context.filesDir, DIR_NAME).also { it.mkdirs() }

    private fun fileForOperator(operator: String): File {
        val safe = operator.replace(Regex("[^a-zA-Z0-9_-]"), "_")
        return File(historyDir, "history_$safe.json")
    }

    /**
     * Load chat history for an operator. Returns empty list if none exists.
     */
    suspend fun load(operator: String): List<UiMessage> = withContext(Dispatchers.IO) {
        try {
            val file = fileForOperator(operator)
            if (!file.exists()) return@withContext emptyList()

            val text = file.readText()
            if (text.isBlank()) return@withContext emptyList()

            val messages = json.decodeFromString<List<UiMessage>>(text)
            Log.d(TAG, "Loaded ${messages.size} messages for $operator")

            // Filter out any streaming/thinking artifacts from a crash
            messages.filter { !it.isStreaming && !it.isThinking }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to load history for $operator: ${e.message}")
            Log.w(TAG, "Possible cause: provenance schema upgraded from String to Provenance — old persisted history is no longer parseable. Stale history will reset to empty.")
            emptyList()
        }
    }

    /**
     * Save chat history for an operator. Trims to MAX_HISTORY_ITEMS.
     */
    suspend fun save(operator: String, messages: List<UiMessage>) = withContext(Dispatchers.IO) {
        try {
            // Only persist completed messages (not streaming/thinking)
            val toSave = messages
                .filter { !it.isStreaming && !it.isThinking }
                .takeLast(MAX_HISTORY_ITEMS)

            val text = json.encodeToString(toSave)
            fileForOperator(operator).writeText(text)
            Log.d(TAG, "Saved ${toSave.size} messages for $operator")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to save history for $operator: ${e.message}")
        }
    }

    /**
     * Clear history for an operator.
     */
    suspend fun clear(operator: String) = withContext(Dispatchers.IO) {
        try {
            fileForOperator(operator).delete()
            Log.d(TAG, "Cleared history for $operator")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to clear history for $operator: ${e.message}")
        }
    }

    /**
     * Clear all history for all operators.
     */
    suspend fun clearAll() = withContext(Dispatchers.IO) {
        try {
            historyDir.listFiles()?.forEach { it.delete() }
            Log.d(TAG, "Cleared all history")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to clear all history: ${e.message}")
        }
    }
}
