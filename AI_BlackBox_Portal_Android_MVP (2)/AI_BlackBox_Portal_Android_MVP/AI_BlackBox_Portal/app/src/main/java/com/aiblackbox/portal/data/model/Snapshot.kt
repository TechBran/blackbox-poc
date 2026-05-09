package com.aiblackbox.portal.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class SnapshotResult(
    @SerialName("snap_id") val snapId: String,
    val operator: String = "",
    val timestamp: String = "",
    val snippet: String = "",
    val similarity: Float = 0f,
    val type: String = ""
)

@Serializable
data class SearchResponse(
    val results: List<SnapshotResult> = emptyList(),
    val count: Int = 0,
    val query: String = "",
    val operator: String = ""
)
