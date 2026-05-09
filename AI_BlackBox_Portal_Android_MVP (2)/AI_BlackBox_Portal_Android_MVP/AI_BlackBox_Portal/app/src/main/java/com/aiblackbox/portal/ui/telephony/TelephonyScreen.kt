package com.aiblackbox.portal.ui.telephony

import android.app.Application
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.aiblackbox.portal.data.api.BlackBoxApi
import android.view.HapticFeedbackConstants
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import com.aiblackbox.portal.ui.components.GlassCard
import com.aiblackbox.portal.ui.theme.BbxAccent
import com.aiblackbox.portal.ui.theme.BbxDim
import com.aiblackbox.portal.ui.theme.BbxWhite
import com.aiblackbox.portal.ui.theme.GlassBorder
import com.aiblackbox.portal.ui.theme.Neutral100
import com.aiblackbox.portal.ui.theme.Neutral200
import com.aiblackbox.portal.ui.theme.Neutral500
import com.aiblackbox.portal.ui.theme.RadiusMd
import com.aiblackbox.portal.ui.theme.SolidGreen
import com.aiblackbox.portal.ui.theme.glassSurface
import androidx.compose.foundation.shape.RoundedCornerShape
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

data class Gateway(
    val id: String,
    val name: String,
    val ip: String,
    val status: String,         // derived: "registered", "reachable", "offline", "unknown"
    val sipRegistered: Boolean = false,
    val reachable: Boolean = false,
    val capacity: Int = 0,
    val activeCalls: Int = 0
)

class TelephonyViewModel(application: Application) : AndroidViewModel(application) {
    private var api: BlackBoxApi? = null
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    private val _gateways = MutableStateFlow<List<Gateway>>(emptyList())
    val gateways: StateFlow<List<Gateway>> = _gateways.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    fun initialize(origin: String) {
        if (origin.isBlank() || api != null) return
        api = BlackBoxApi(origin)
        loadGateways()
    }

    fun loadGateways() {
        val api = api ?: return
        _isLoading.value = true
        viewModelScope.launch {
            try {
                val response = api.get("/asterisk/gateways")
                val root = json.parseToJsonElement(response).jsonObject
                val arr = root["gateways"]?.jsonArray ?: return@launch
                _gateways.value = arr.mapNotNull { el ->
                    try {
                        val obj = el.jsonObject
                        val id = obj["id"]?.jsonPrimitive?.content ?: return@mapNotNull null
                        val name = obj["name"]?.jsonPrimitive?.content ?: id
                        val ip = obj["ip"]?.jsonPrimitive?.content
                            ?: obj["host"]?.jsonPrimitive?.content ?: ""

                        // status is a complex object: {reachable, sip_registered, capacity, active_calls, ...}
                        val statusObj = obj["status"]?.jsonObject
                        val reachable = statusObj?.get("reachable")?.jsonPrimitive?.content?.toBooleanStrictOrNull() ?: false
                        val sipRegistered = statusObj?.get("sip_registered")?.jsonPrimitive?.content?.toBooleanStrictOrNull() ?: false
                        val capacity = statusObj?.get("capacity")?.jsonPrimitive?.content?.toIntOrNull() ?: 0
                        val activeCalls = statusObj?.get("active_calls")?.jsonPrimitive?.content?.toIntOrNull() ?: 0

                        val displayStatus = when {
                            sipRegistered -> "registered"
                            reachable -> "reachable"
                            else -> "offline"
                        }

                        Gateway(id, name, ip, displayStatus, sipRegistered, reachable, capacity, activeCalls)
                    } catch (_: Exception) { null }
                }
            } catch (_: Exception) {
                _gateways.value = emptyList()
            } finally {
                _isLoading.value = false
            }
        }
    }

    fun discover() {
        val api = api ?: return
        viewModelScope.launch {
            try {
                api.post("/asterisk/gateways/discover", "{}")
                loadGateways()
            } catch (_: Exception) {
            }
        }
    }

    fun testGateway(id: String) {
        val api = api ?: return
        viewModelScope.launch {
            try {
                api.post("/asterisk/gateways/$id/test", "{}")
            } catch (_: Exception) {
            }
        }
    }
}

@Composable
fun TelephonyScreen(
    origin: String,
    modifier: Modifier = Modifier,
    viewModel: TelephonyViewModel = viewModel()
) {
    val gateways by viewModel.gateways.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()

    LaunchedEffect(origin) { viewModel.initialize(origin) }

    val view = LocalView.current

    Column(modifier = modifier.fillMaxSize().padding(start = 16.dp, end = 16.dp, bottom = 16.dp, top = 100.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                "\uD83D\uDCDE Telephony",
                style = MaterialTheme.typography.headlineMedium.copy(fontWeight = FontWeight.Bold),
                color = BbxWhite
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = {
                    view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
                    viewModel.discover()
                }) { Text("Discover", color = BbxAccent) }
                TextButton(onClick = {
                    view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                    viewModel.loadGateways()
                }) { Text("Refresh", color = BbxDim) }
            }
        }
        Spacer(Modifier.height(12.dp))

        if (isLoading) {
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = BbxAccent, modifier = Modifier.size(24.dp))
            }
        }

        if (gateways.isEmpty() && !isLoading) {
            Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
                Text("No gateways found. Tap Discover to scan.", color = Neutral500, style = MaterialTheme.typography.bodyMedium)
            }
        }

        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(gateways, key = { it.id }) { gw ->
                val isRegistered = gw.status == "registered"
                val isReachable = gw.status == "reachable"
                val accentColor = when {
                    isRegistered -> SolidGreen
                    isReachable -> Color(0xFFFFA726) // amber
                    else -> BbxAccent // red
                }
                val borderColor = accentColor.copy(alpha = 0.4f)

                GlassCard(modifier = Modifier.fillMaxWidth().border(1.dp, borderColor, RoundedCornerShape(RadiusMd))) {
                    Row(modifier = Modifier.padding(14.dp)) {
                        // Left accent bar
                        Box(
                            Modifier.width(3.dp).height(70.dp)
                                .background(accentColor, RoundedCornerShape(2.dp))
                        )
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text("\uD83D\uDCDE", style = MaterialTheme.typography.titleMedium)
                                Spacer(Modifier.width(8.dp))
                                Text(gw.name, style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold), color = BbxWhite)
                                Spacer(Modifier.weight(1f))
                                // Status badge
                                Box(
                                    Modifier.clip(RoundedCornerShape(8.dp))
                                        .background(accentColor.copy(alpha = 0.15f))
                                        .padding(horizontal = 8.dp, vertical = 2.dp)
                                ) {
                                    Text(
                                        gw.status.uppercase(),
                                        style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Medium),
                                        color = accentColor
                                    )
                                }
                            }
                            Spacer(Modifier.height(4.dp))
                            // IP + capacity info
                            Text(gw.ip, style = MaterialTheme.typography.bodySmall, color = Neutral500)
                            if (gw.capacity > 0) {
                                Spacer(Modifier.height(2.dp))
                                Text(
                                    "Capacity: ${gw.activeCalls}/${gw.capacity} calls",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = Neutral500
                                )
                            }
                            Spacer(Modifier.height(8.dp))
                            // Action buttons
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Box(
                                    Modifier.clip(RoundedCornerShape(RadiusMd))
                                        .background(BbxAccent.copy(alpha = 0.1f))
                                        .border(1.dp, BbxAccent.copy(alpha = 0.3f), RoundedCornerShape(RadiusMd))
                                        .clickable {
                                            view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
                                            viewModel.testGateway(gw.id)
                                        }
                                        .padding(horizontal = 12.dp, vertical = 6.dp)
                                ) {
                                    Text("Test Call", style = MaterialTheme.typography.labelSmall, color = BbxAccent)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
