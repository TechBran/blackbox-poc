package com.aiblackbox.portal.data.repository

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import com.aiblackbox.portal.data.api.BlackBoxApi
import com.aiblackbox.portal.data.model.UploadResponse
import java.io.File
import java.io.FileOutputStream

class MediaRepository(private val api: BlackBoxApi) {

    /**
     * Upload a file from a content URI to the server.
     * Copies the URI content to a temp file, uploads via multipart,
     * and returns the server URL for the uploaded file.
     */
    suspend fun uploadFile(context: Context, uri: Uri): UploadResponse {
        // Copy URI content to a temp file
        val fileName = getFileName(context, uri) ?: "upload_${System.currentTimeMillis()}"
        val tempFile = File(context.cacheDir, fileName)

        context.contentResolver.openInputStream(uri)?.use { input ->
            FileOutputStream(tempFile).use { output ->
                input.copyTo(output)
            }
        } ?: throw IllegalStateException("Cannot read file: $uri")

        return try {
            val response = api.uploadFile("/upload", tempFile)
            api.json.decodeFromString(UploadResponse.serializer(), response)
        } finally {
            tempFile.delete()
        }
    }

    /**
     * Get the display file name from a content URI using the ContentResolver.
     */
    private fun getFileName(context: Context, uri: Uri): String? {
        var name: String? = null
        context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (cursor.moveToFirst() && nameIndex >= 0) {
                name = cursor.getString(nameIndex)
            }
        }
        return name
    }

    /**
     * List uploaded media files on server.
     */
    suspend fun listMedia(): String {
        return api.get("/api/media/list")
    }
}
