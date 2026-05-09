package com.aiblackbox.portal.ui.cli_agent

import com.aiblackbox.portal.data.api.BlackBoxApi
import com.aiblackbox.portal.data.model.CliAgentApp
import com.aiblackbox.portal.data.model.CliAgentAppsResponse
import com.aiblackbox.portal.data.model.CliAgentKillResponse
import com.aiblackbox.portal.data.model.CliAgentSessionEntry
import com.aiblackbox.portal.data.model.CliAgentSessionsResponse
import java.io.IOException
import java.net.URLEncoder

/**
 * Build the deterministic CLI Agent tmux session id.
 *
 * Field separator is "__" (double underscore) so hyphenated operators
 * like "Brandon-DEV" round-trip cleanly. Apps root pick uses slug "_root".
 *
 * MUST match Orchestrator/cli_agent/session_manager.py::session_name.
 */
fun cliAgentSessionId(operator: String, provider: String, appSlug: String): String {
    require("__" !in operator && "__" !in provider) {
        "Operator/provider names must not contain '__'"
    }
    val slug = if (appSlug.isEmpty()) "_root" else appSlug
    return "cli-agent-${operator}__${provider}__${slug}"
}

/**
 * Repository for CLI Agent picker data: apps + sessions list + kill.
 *
 * Apps are filtered to those under the BlackBox `Apps/` directory
 * (workspace allowlist per design Q3). Sessions are scoped to the
 * given operator. Kill is idempotent — returns false (with reason
 * "not-found") for a session that doesn't exist.
 */
class CliAgentSessionRepository(private val api: BlackBoxApi) {

    /** Apps available as CLI Agent workspaces (those under Apps/). */
    suspend fun listApps(): List<CliAgentApp> {
        val body = api.get("/agent/apps")
        val parsed = api.json.decodeFromString(CliAgentAppsResponse.serializer(), body)
        return parsed.apps.filter { "/Apps/" in it.directory }
    }

    /** Live CLI Agent sessions for [operator]. */
    suspend fun listSessions(operator: String): List<CliAgentSessionEntry> {
        // URL-encode the operator name defensively
        val encoded = URLEncoder.encode(operator, "UTF-8")
        val body = api.get("/cli-agent/sessions?op=$encoded")
        return api.json.decodeFromString(CliAgentSessionsResponse.serializer(), body).sessions
    }

    /** Kill a session by id. Idempotent — never throws on missing. */
    suspend fun killSession(sessionId: String): Boolean {
        return try {
            val body = api.delete("/cli-agent/sessions/$sessionId")
            api.json.decodeFromString(CliAgentKillResponse.serializer(), body).killed
        } catch (e: IOException) {
            // 404 path on backends that don't return idempotent 200
            false
        }
    }
}
