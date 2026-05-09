package com.aiblackbox.portal.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class Device(
    val id: String = "",
    val name: String = "",
    @SerialName("tailscale_ip") val tailscaleIp: String = "",
    @SerialName("device_type") val deviceType: String = "",
    val protocol: String = "",
    val owner: String = "",
    @SerialName("adb_port") val adbPort: Int = 0,
    @SerialName("vnc_port") val vncPort: Int = 0,
    val status: String = "unknown",
    val description: String? = null,
    val metadata: DeviceMetadata? = null
)

@Serializable
data class DeviceMetadata(
    val model: String? = null,
    @SerialName("android_version") val androidVersion: String? = null,
    @SerialName("screen_size") val screenSize: String? = null
)

@Serializable
data class DevicesResponse(val devices: List<Device> = emptyList())

@Serializable
data class SyncTailscaleResponse(
    val results: Map<String, String> = emptyMap(),
    @SerialName("total_devices") val totalDevices: Int = 0
)

@Serializable
data class AdbDevice(
    val serial: String = "",
    val state: String = ""
)

@Serializable
data class AdbDevicesResponse(val devices: List<AdbDevice> = emptyList())

@Serializable
data class HealthCheckResponse(val status: String = "unknown")
