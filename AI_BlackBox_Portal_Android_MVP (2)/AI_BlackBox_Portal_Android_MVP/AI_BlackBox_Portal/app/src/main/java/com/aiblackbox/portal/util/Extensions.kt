package com.aiblackbox.portal.util

import android.content.Context
import android.widget.Toast
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

fun Context.toast(message: String, long: Boolean = false) {
    Toast.makeText(this, message, if (long) Toast.LENGTH_LONG else Toast.LENGTH_SHORT).show()
}

fun String.truncate(max: Int = 100): String =
    if (length <= max) this else take(max - 3) + "..."

fun Long.toRelativeTime(): String {
    val now = System.currentTimeMillis()
    val diff = now - this
    return when {
        diff < 60_000 -> "just now"
        diff < 3_600_000 -> "${diff / 60_000}m ago"
        diff < 86_400_000 -> "${diff / 3_600_000}h ago"
        diff < 604_800_000 -> "${diff / 86_400_000}d ago"
        else -> SimpleDateFormat("MMM d", Locale.US).format(Date(this))
    }
}

fun String.toHttpUrl(origin: String): String =
    if (startsWith("http")) this else "$origin$this"

fun String.toWsUrl(origin: String): String {
    val base = origin.replace("https://", "wss://").replace("http://", "ws://")
    return "$base$this"
}

fun <T> Flow<T>.catchAndLog(tag: String): Flow<T> =
    catch { e -> android.util.Log.e(tag, "Flow error", e) }

/**
 * Normalize a server origin URL for API calls.
 * Matches PortalActivity.normalizeOrigin() logic:
 * - For .ts.net domains: force HTTPS, strip port (Tailscale serves on 443)
 * - Remove trailing /ui/ suffix (that's for WebView, not API)
 * - Ensure no trailing slash
 */
fun normalizeApiOrigin(url: String): String {
    var u = url.trim()
    val tsNet = Regex("https?://[^/]*\\.ts\\.net", RegexOption.IGNORE_CASE)
    val isTsNet = tsNet.containsMatchIn(u)

    if (isTsNet) {
        // Force HTTPS for Tailscale .ts.net domains
        u = u.replaceFirst(Regex("^http://", RegexOption.IGNORE_CASE), "https://")
        // Remove port number (Tailscale HTTPS serves on standard 443)
        u = u.replace(Regex("^(https://[^/]+):\\d+(?=/|$)", RegexOption.IGNORE_CASE), "$1")
    }

    // Strip /ui/ suffix and trailing slash — we want the bare API base
    u = u.removeSuffix("/ui/").removeSuffix("/ui").removeSuffix("/")
    return u
}
