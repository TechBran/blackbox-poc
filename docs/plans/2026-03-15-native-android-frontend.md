# Native Android Frontend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a complete native Jetpack Compose frontend for the AI BlackBox Portal that replaces the WebView wrapper with a gorgeous, high-performance native experience while keeping the browser Portal unchanged and the backend API identical.

**Architecture:** Single-Activity Compose app with Navigation, MVVM + Repository pattern, OkHttp for all networking (REST, SSE streaming, WebSocket). PairingActivity detects and routes to either NativeMainActivity (Compose) or PortalActivity (legacy WebView). All state persisted via DataStore. Design system ported from CSS tokens to Compose theme.

**Tech Stack:** Kotlin, Jetpack Compose (BOM 2025.05.01), Material 3, OkHttp 5.3.2, Kotlin Coroutines + Flow, DataStore, Navigation Compose, Coil (image loading), kotlinx-serialization-json

---

## Project Scope

### What We're Building
- Full native Compose UI replicating all 30 Portal JS modules
- Same Orchestrator API (no backend changes)
- Platform detection (native app vs browser)
- All existing Android code preserved (WebView, Overlay, XR)

### What We're NOT Changing
- Browser Portal (Portal/*.html, Portal/modules/*, Portal/styles/*)
- Backend API (Orchestrator/*)
- Overlay system (OverlayService, OverlayBridge, XR)
- KeepAliveService, NotificationManager

### Module Mapping: Portal JS → Native Kotlin

| Portal JS Module | Native Kotlin Equivalent | Phase |
|---|---|---|
| core-utils.js | `util/Extensions.kt` | 1 |
| state-management.js | `data/store/BlackBoxStore.kt` | 1 |
| app-init.js | `NativeMainActivity.kt` + `NavGraph.kt` | 1 |
| ui-setup.js | Scaffold composables | 1 |
| chat-bubbles.js | `ui/chat/ChatBubble.kt` | 2 |
| chat-send.js | `data/repository/ChatRepository.kt` | 2 |
| markdown-renderer.js | `ui/components/MarkdownText.kt` | 2 |
| file-upload.js | `ui/chat/FileAttachment.kt` | 3 |
| help-hints.js | `ui/chat/HomeScreen.kt` | 2 |
| notifications.js | `BlackBoxNotificationManager.kt` (existing) | 1 |
| status-line.js | `ui/components/StatusLine.kt` | 4 |
| tts-stt.js | `ui/voice/TtsSttManager.kt` | 5 |
| agent-handler.js | `data/agent/ClaudeAgentClient.kt` | 4 |
| gemini-agent-handler.js | `data/agent/GeminiAgentClient.kt` | 4 |
| gpt-realtime.js | `data/voice/GptRealtimeClient.kt` | 5 |
| gemini-live.js | `data/voice/GeminiLiveClient.kt` | 5 |
| grok-live.js | `data/voice/GrokLiveClient.kt` | 5 |
| gemini-recorder.js | `data/voice/AudioRecorder.kt` | 5 |
| generation-modals.js | `ui/generation/*.kt` | 6 |
| task-manager.js | `data/task/TaskManager.kt` | 3 |
| media-manager.js | `ui/media/MediaBrowser.kt` | 6 |
| timeline-browser.js | `ui/timeline/TimelineScreen.kt` | 7 |
| device-manager.js | `ui/devices/DeviceManager.kt` | 7 |
| cron-manager.js | `ui/cron/CronManager.kt` | 7 |
| telephony-manager.js | `ui/telephony/TelephonyManager.kt` | 7 |
| cellular-manager.js | `ui/cellular/CellularManager.kt` | 7 |
| cu-drawer.js | `ui/computeruse/CuDrawer.kt` | 7 |
| cu-interact.js | `ui/computeruse/CuViewer.kt` | 7 |
| slash-commands.js | `ui/chat/SlashCommands.kt` | 4 |
| todo-tracker.js | `ui/components/TodoPanel.kt` | 4 |

---

## File Structure

All new code goes under the existing app source:
```
app/src/main/java/com/aiblackbox/portal/
├── PairingActivity.kt              (MODIFY — route to native or webview)
├── PortalActivity.kt               (KEEP — legacy webview fallback)
├── NativeMainActivity.kt           (NEW — single-activity Compose host)
├── BlackBoxNotificationManager.kt  (KEEP)
├── KeepAliveService.kt             (KEEP)
│
├── navigation/
│   └── NavGraph.kt                 (NEW — all screen routes)
│
├── data/
│   ├── api/
│   │   ├── BlackBoxApi.kt          (NEW — OkHttp REST client)
│   │   ├── SSEClient.kt            (NEW — Server-Sent Events parser)
│   │   └── WebSocketClient.kt      (NEW — generic WS wrapper)
│   ├── model/
│   │   ├── ChatMessage.kt          (NEW — message data classes)
│   │   ├── Operator.kt             (NEW — operator data)
│   │   ├── Task.kt                 (NEW — async task model)
│   │   ├── Provider.kt             (NEW — provider/model enums)
│   │   ├── Device.kt               (NEW — Tailscale device)
│   │   ├── Snapshot.kt             (NEW — timeline snapshot)
│   │   └── CronJob.kt              (NEW — cron job model)
│   ├── repository/
│   │   ├── ChatRepository.kt       (NEW — chat send/stream/save)
│   │   ├── OperatorRepository.kt   (NEW — operator CRUD + prefs)
│   │   ├── TaskRepository.kt       (NEW — task polling)
│   │   ├── MediaRepository.kt      (NEW — upload/list media)
│   │   └── DeviceRepository.kt     (NEW — device management)
│   ├── store/
│   │   └── BlackBoxStore.kt        (NEW — DataStore persistence)
│   ├── agent/
│   │   ├── ClaudeAgentClient.kt    (NEW — Claude Code WS)
│   │   └── GeminiAgentClient.kt    (NEW — Gemini CLI WS)
│   └── voice/
│       ├── GptRealtimeClient.kt    (NEW — GPT Realtime WS)
│       ├── GeminiLiveClient.kt     (NEW — Gemini Live WS)
│       ├── GrokLiveClient.kt       (NEW — Grok Live WS)
│       ├── AudioRecorder.kt        (NEW — mic recording)
│       └── TtsSttManager.kt        (NEW — TTS/STT coordination)
│
├── ui/
│   ├── theme/
│   │   ├── BlackBoxTheme.kt        (NEW — Compose theme)
│   │   ├── Color.kt                (NEW — ported from _variables.css)
│   │   ├── Type.kt                 (NEW — typography scale)
│   │   └── Shape.kt                (NEW — radius scale)
│   ├── components/
│   │   ├── TopBar.kt               (NEW — header bar)
│   │   ├── ChatBubble.kt           (NEW — message bubbles)
│   │   ├── Composer.kt             (NEW — message input)
│   │   ├── MarkdownText.kt         (NEW — markdown rendering)
│   │   ├── StatusLine.kt           (NEW — agent status)
│   │   ├── TodoPanel.kt            (NEW — todo tracking)
│   │   ├── GlassCard.kt            (NEW — glass morphism)
│   │   ├── ProviderBanner.kt       (NEW — provider banners)
│   │   └── LoadingIndicators.kt    (NEW — shimmer, pulse, etc.)
│   ├── chat/
│   │   ├── ChatScreen.kt           (NEW — main chat screen)
│   │   ├── ChatViewModel.kt        (NEW — chat state)
│   │   ├── HomeScreen.kt           (NEW — help hints)
│   │   ├── FileAttachment.kt       (NEW — file picker/preview)
│   │   └── SlashCommands.kt        (NEW — autocomplete dropdown)
│   ├── settings/
│   │   ├── SettingsSheet.kt        (NEW — bottom sheet settings)
│   │   └── SettingsViewModel.kt    (NEW)
│   ├── generation/
│   │   ├── ImageGenScreen.kt       (NEW)
│   │   ├── VideoGenScreen.kt       (NEW)
│   │   ├── MusicGenScreen.kt       (NEW)
│   │   ├── TtsScreen.kt            (NEW)
│   │   └── GenerationViewModel.kt  (NEW)
│   ├── media/
│   │   ├── MediaBrowser.kt         (NEW)
│   │   └── MediaViewModel.kt       (NEW)
│   ├── timeline/
│   │   ├── TimelineScreen.kt       (NEW)
│   │   └── TimelineViewModel.kt    (NEW)
│   ├── devices/
│   │   ├── DeviceManager.kt        (NEW)
│   │   └── DeviceViewModel.kt      (NEW)
│   ├── computeruse/
│   │   ├── CuDrawer.kt             (NEW)
│   │   ├── CuViewer.kt             (NEW)
│   │   └── CuViewModel.kt          (NEW)
│   ├── cron/
│   │   ├── CronManager.kt          (NEW)
│   │   └── CronViewModel.kt        (NEW)
│   ├── telephony/
│   │   ├── TelephonyManager.kt     (NEW)
│   │   └── TelephonyViewModel.kt   (NEW)
│   └── cellular/
│       ├── CellularManager.kt      (NEW)
│       └── CellularViewModel.kt    (NEW)
│
├── overlay/                         (KEEP — existing 5 files)
│   ├── OverlayService.kt
│   ├── OverlayBridge.kt
│   ├── XrOverlayActivity.kt
│   ├── XrBubbleContent.kt
│   └── XrExpandedPanel.kt
│
└── util/
    ├── Extensions.kt               (NEW — Kotlin utility extensions)
    └── Constants.kt                 (NEW — shared constants)
```

Total new files: ~55 Kotlin files
Existing files modified: 3 (PairingActivity, AndroidManifest, build.gradle)
Existing files preserved: 9 (all current .kt files)

---

## Phase 1: Foundation

**Goal:** Set up the native app shell — theme, navigation, API client, state persistence, platform routing. After this phase, the app launches into a native Compose shell with a top bar and empty chat screen.

### Task 1.1: Add Dependencies to build.gradle

**Files:**
- Modify: `app/build.gradle`

**Step 1: Add new dependencies**

Add these to the `dependencies` block in `app/build.gradle`:

```groovy
plugins {
    id 'com.android.application'
    id 'org.jetbrains.kotlin.android'
    id 'org.jetbrains.kotlin.plugin.compose'
    id 'org.jetbrains.kotlin.plugin.serialization'  // ADD
}

dependencies {
    // ... existing deps ...

    // Serialization
    implementation "org.jetbrains.kotlinx:kotlinx-serialization-json:1.8.1"

    // Navigation
    implementation "androidx.navigation:navigation-compose:2.9.0"

    // DataStore
    implementation "androidx.datastore:datastore-preferences:1.1.7"

    // Image loading
    implementation "io.coil-kt:coil-compose:2.7.0"

    // Lifecycle ViewModel Compose
    implementation "androidx.lifecycle:lifecycle-viewmodel-compose:2.9.0"
}
```

Also add to project-level `build.gradle` or `settings.gradle` plugins block:
```groovy
id 'org.jetbrains.kotlin.plugin.serialization' version '<kotlin-version>' apply false
```

**Step 2: Sync and verify build**

Run: `./gradlew assembleDebug` — should succeed with no errors.

**Step 3: Commit**
```
feat: add navigation, serialization, datastore, coil dependencies
```

---

### Task 1.2: Design System — Color, Typography, Shape

**Files:**
- Create: `ui/theme/Color.kt`
- Create: `ui/theme/Type.kt`
- Create: `ui/theme/Shape.kt`
- Create: `ui/theme/BlackBoxTheme.kt`

**Step 1: Create Color.kt**

Port the CSS design tokens from `_variables.css` to Compose Color values:

```kotlin
package com.aiblackbox.portal.ui.theme

import androidx.compose.ui.graphics.Color

// ── Brand Colors ──
val BbxBlack = Color(0xFF000000)
val BbxDark = Color(0xFF0E0E10)
val BbxSurface = Color(0xFF17181B)
val BbxRed = Color(0xFFE10600)
val BbxAccent = Color(0xFFFF4A4A)
val BbxWhite = Color(0xFFFFFFFF)
val BbxDim = Color(0xFFC9C9C9)

// ── Neutral Scale (maps to --neutral-XXX) ──
val Neutral0 = Color(0xFF000000)
val Neutral50 = Color(0xFF0A0A0A)
val Neutral100 = Color(0xFF141414)
val Neutral150 = Color(0xFF1A1A1A)
val Neutral200 = Color(0xFF222222)
val Neutral250 = Color(0xFF2C2C2C)
val Neutral300 = Color(0xFF333333)
val Neutral400 = Color(0xFF444444)
val Neutral500 = Color(0xFF555555)
val Neutral600 = Color(0xFF666666)
val Neutral700 = Color(0xFF888888)
val Neutral800 = Color(0xFFAAAAAA)
val Neutral850 = Color(0xFFBBBBBB)
val Neutral900 = Color(0xFFCCCCCC)
val Neutral950 = Color(0xFFDDDDDD)
val Neutral1000 = Color(0xFFE0E0E0)

// ── Semantic Colors ──
val BubbleAssistant = BbxBlack       // --bubble
val BubbleUser = Neutral250          // --bubble-user (#2C2C2C)
val TextPrimary = BbxWhite           // --text
val TextMuted = BbxDim               // --muted
val Border = Neutral300              // --border

// ── Syntax Highlighting ──
val HighlightSnapshot = Color(0xFFBB86FC)  // Purple
val HighlightCitation = Color(0xFF4DD0E1)  // Cyan
val HighlightHeader = Color(0xFFFFA726)    // Orange
val HighlightKeyword = Color(0xFF66BB6A)   // Green
val HighlightLink = Color(0xFF64B5F6)      // Light blue
val HighlightNumber = Color(0xFFFF4A4A)    // Red

// ── Glow Colors ──
val GlowAccent = Color(0x40FF4A4A)    // 25% opacity
val GlowBlue = Color(0x404A9EFF)
val GlowGreen = Color(0x4027D980)

// ── Glass ──
val GlassBg = Color(0xBF141414)        // rgba(20,20,20,0.75)
val GlassBgHover = Color(0xCC1E1E1E)   // rgba(30,30,30,0.8)
val GlassBorder = Color(0x14FFFFFF)     // rgba(255,255,255,0.08)
val GlassBorderHover = Color(0x24FFFFFF) // rgba(255,255,255,0.14)
```

**Step 2: Create Type.kt**

```kotlin
package com.aiblackbox.portal.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// System default sans-serif (matches Portal's system font stack)
val BlackBoxFontFamily = FontFamily.SansSerif

val BlackBoxTypography = Typography(
    displayLarge = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.ExtraBold,
        fontSize = 48.sp,     // --text-5xl
        lineHeight = 56.sp
    ),
    displayMedium = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.Bold,
        fontSize = 36.sp,     // --text-4xl
        lineHeight = 44.sp
    ),
    displaySmall = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.Bold,
        fontSize = 28.sp,     // --text-3xl
        lineHeight = 36.sp
    ),
    headlineLarge = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,     // --text-2xl
        lineHeight = 28.sp
    ),
    headlineMedium = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp,     // --text-xl
        lineHeight = 24.sp
    ),
    titleLarge = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp,     // --text-lg
        lineHeight = 24.sp
    ),
    titleMedium = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.Medium,
        fontSize = 15.sp,     // --text-base
        lineHeight = 22.sp
    ),
    bodyLarge = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,     // --text-base
        lineHeight = 22.sp,
        color = TextPrimary
    ),
    bodyMedium = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,     // --text-sm
        lineHeight = 18.sp,
        color = TextPrimary
    ),
    bodySmall = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 11.sp,     // --text-xs
        lineHeight = 16.sp,
        color = TextMuted
    ),
    labelLarge = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.Medium,
        fontSize = 13.sp,
        lineHeight = 18.sp
    ),
    labelMedium = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.Medium,
        fontSize = 11.sp,
        lineHeight = 16.sp
    ),
    labelSmall = TextStyle(
        fontFamily = BlackBoxFontFamily,
        fontWeight = FontWeight.Medium,
        fontSize = 10.sp,
        lineHeight = 14.sp
    )
)
```

**Step 3: Create Shape.kt**

```kotlin
package com.aiblackbox.portal.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

// ── Radius Scale (maps to --radius-*) ──
val RadiusXs = 4.dp    // --radius-xs
val RadiusSm = 6.dp    // --radius-sm
val RadiusMd = 8.dp    // --radius-md
val RadiusLg = 12.dp   // --radius-lg
val RadiusXl = 16.dp   // --radius-xl
val Radius2xl = 24.dp  // --radius-2xl

val BlackBoxShapes = Shapes(
    extraSmall = RoundedCornerShape(RadiusXs),
    small = RoundedCornerShape(RadiusSm),
    medium = RoundedCornerShape(RadiusMd),
    large = RoundedCornerShape(RadiusLg),
    extraLarge = RoundedCornerShape(RadiusXl)
)

// ── Asymmetric Bubble Shapes (preserve Portal's distinctive look) ──
val AssistantBubbleShape = RoundedCornerShape(
    topStart = 4.dp,      // sharp top-left
    topEnd = 18.dp,
    bottomEnd = 18.dp,
    bottomStart = 18.dp
)

val UserBubbleShape = RoundedCornerShape(
    topStart = 18.dp,
    topEnd = 4.dp,        // sharp top-right
    bottomEnd = 18.dp,
    bottomStart = 18.dp
)
```

**Step 4: Create BlackBoxTheme.kt**

```kotlin
package com.aiblackbox.portal.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

private val BlackBoxColorScheme = darkColorScheme(
    primary = BbxAccent,
    onPrimary = BbxWhite,
    primaryContainer = BbxRed,
    onPrimaryContainer = BbxWhite,
    secondary = Neutral500,
    onSecondary = BbxWhite,
    secondaryContainer = Neutral250,
    onSecondaryContainer = BbxWhite,
    tertiary = HighlightCitation,
    onTertiary = BbxBlack,
    background = BbxBlack,
    onBackground = BbxWhite,
    surface = BbxBlack,
    onSurface = BbxWhite,
    surfaceVariant = Neutral100,
    onSurfaceVariant = BbxDim,
    surfaceContainerLowest = BbxBlack,
    surfaceContainerLow = Neutral50,
    surfaceContainer = Neutral100,
    surfaceContainerHigh = Neutral150,
    surfaceContainerHighest = Neutral200,
    outline = Neutral300,
    outlineVariant = Neutral200,
    error = BbxAccent,
    onError = BbxWhite
)

@Composable
fun BlackBoxTheme(content: @Composable () -> Unit) {
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = BbxBlack.toArgb()
            window.navigationBarColor = BbxBlack.toArgb()
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = false
                isAppearanceLightNavigationBars = false
            }
        }
    }

    MaterialTheme(
        colorScheme = BlackBoxColorScheme,
        typography = BlackBoxTypography,
        shapes = BlackBoxShapes,
        content = content
    )
}
```

**Step 5: Commit**
```
feat: add BlackBox design system — colors, typography, shapes, theme
```

---

### Task 1.3: Constants and Utility Extensions

**Files:**
- Create: `util/Constants.kt`
- Create: `util/Extensions.kt`

**Step 1: Create Constants.kt**

```kotlin
package com.aiblackbox.portal.util

object Constants {
    const val PREFS_NAME = "bbx_prefs"
    const val KEY_ORIGIN = "origin"
    const val KEY_OPERATOR = "operator"
    const val KEY_USE_NATIVE = "use_native_ui"

    // DataStore keys
    const val DS_PROVIDER = "provider"
    const val DS_MODEL = "model"
    const val DS_STREAMING = "streaming_enabled"
    const val DS_CLAUDE_MODEL = "claude_model"
    const val DS_TTS_VOICE = "tts_voice"

    // API paths
    const val API_CHAT = "/chat"
    const val API_CHAT_STREAM = "/chat/stream"
    const val API_CHAT_SAVE = "/chat/save"
    const val API_UPLOAD = "/upload"
    const val API_HEALTH = "/health"
    const val API_MODELS = "/models"
    const val API_OPERATORS = "/operators"
    const val API_OPERATOR_PREFS = "/operator/preferences"
    const val API_TASKS_LIST = "/tasks/list"
    const val API_TASKS_STATUS = "/tasks/status"
    const val API_AGENT_SESSION = "/agent/session"
    const val API_AGENT_APPS = "/agent/apps"
    const val API_AGENT_COMMANDS = "/agent/commands"
    const val API_GENERATE_IMAGE = "/generate/image"
    const val API_GENERATE_VIDEO = "/generate/video"
    const val API_GENERATE_MUSIC = "/generate/lyria_music"
    const val API_GENERATE_GOOGLE_TTS = "/generate/google_ssml"
    const val API_GENERATE_GEMINI_TTS = "/generate/gemini_tts"
    const val API_TTS = "/tts"
    const val API_TTS_VOICES = "/tts/voices"
    const val API_STT = "/stt"
    const val API_TIMELINE = "/timeline"
    const val API_ASSERT = "/assert"
    const val API_MEDIA_LIST = "/api/media/list"
    const val API_DEVICES = "/devices/"
    const val API_CRON_JOBS = "/api/cron/jobs"
    const val API_ASTERISK = "/asterisk"
    const val API_INTERNET = "/internet"
    const val API_BROWSER_SCREENSHOT = "/browser/screenshot"
    const val API_FOSSIL_HYBRID = "/fossil/hybrid"

    // WebSocket paths
    const val WS_AGENT = "/ws/agent"
    const val WS_GEMINI_AGENT = "/ws/gemini-agent"

    // Default values
    const val DEFAULT_PROVIDER = "gemini"
    const val DEFAULT_OPERATOR = "Brandon"

    // Client identification header
    const val CLIENT_HEADER = "X-BlackBox-Client"
    const val CLIENT_ID = "native-android/1.0"
}
```

**Step 2: Create Extensions.kt**

```kotlin
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
```

**Step 3: Commit**
```
feat: add constants and utility extensions
```

---

### Task 1.4: DataStore — State Persistence

**Files:**
- Create: `data/store/BlackBoxStore.kt`

**Step 1: Create BlackBoxStore.kt**

```kotlin
package com.aiblackbox.portal.data.store

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.aiblackbox.portal.util.Constants
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "bbx_settings")

class BlackBoxStore(private val context: Context) {

    companion object {
        val KEY_OPERATOR = stringPreferencesKey(Constants.KEY_OPERATOR)
        val KEY_PROVIDER = stringPreferencesKey(Constants.DS_PROVIDER)
        val KEY_MODEL = stringPreferencesKey(Constants.DS_MODEL)
        val KEY_STREAMING = booleanPreferencesKey(Constants.DS_STREAMING)
        val KEY_CLAUDE_MODEL = stringPreferencesKey(Constants.DS_CLAUDE_MODEL)
        val KEY_TTS_VOICE = stringPreferencesKey(Constants.DS_TTS_VOICE)
        val KEY_ORIGIN = stringPreferencesKey(Constants.KEY_ORIGIN)
    }

    // ── Origin (server URL) ──
    val origin: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_ORIGIN] ?: ""
    }

    suspend fun setOrigin(value: String) {
        context.dataStore.edit { it[KEY_ORIGIN] = value }
    }

    // ── Operator ──
    val operator: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_OPERATOR] ?: Constants.DEFAULT_OPERATOR
    }

    suspend fun setOperator(value: String) {
        context.dataStore.edit { it[KEY_OPERATOR] = value }
    }

    // ── Provider ──
    val provider: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_PROVIDER] ?: Constants.DEFAULT_PROVIDER
    }

    suspend fun setProvider(value: String) {
        context.dataStore.edit { it[KEY_PROVIDER] = value }
    }

    // ── Model ──
    val model: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_MODEL] ?: ""
    }

    suspend fun setModel(value: String) {
        context.dataStore.edit { it[KEY_MODEL] = value }
    }

    // ── Streaming ──
    val streamingEnabled: Flow<Boolean> = context.dataStore.data.map { prefs ->
        prefs[KEY_STREAMING] ?: true
    }

    suspend fun setStreamingEnabled(value: Boolean) {
        context.dataStore.edit { it[KEY_STREAMING] = value }
    }

    // ── Claude Model ──
    val claudeModel: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_CLAUDE_MODEL] ?: "sonnet"
    }

    suspend fun setClaudeModel(value: String) {
        context.dataStore.edit { it[KEY_CLAUDE_MODEL] = value }
    }

    // ── TTS Voice ──
    val ttsVoice: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_TTS_VOICE] ?: "onyx"
    }

    suspend fun setTtsVoice(value: String) {
        context.dataStore.edit { it[KEY_TTS_VOICE] = value }
    }

    // ── Generic get/set for operator-scoped preferences ──
    suspend fun setString(key: String, value: String) {
        context.dataStore.edit { it[stringPreferencesKey(key)] = value }
    }

    fun getString(key: String, default: String = ""): Flow<String> =
        context.dataStore.data.map { prefs ->
            prefs[stringPreferencesKey(key)] ?: default
        }
}
```

**Step 2: Commit**
```
feat: add DataStore-based state persistence
```

---

### Task 1.5: API Client — OkHttp REST + SSE + WebSocket

**Files:**
- Create: `data/api/BlackBoxApi.kt`
- Create: `data/api/SSEClient.kt`
- Create: `data/api/WebSocketClient.kt`
- Create: `data/model/ChatMessage.kt`
- Create: `data/model/Provider.kt`

**Step 1: Create data models**

`data/model/ChatMessage.kt`:
```kotlin
package com.aiblackbox.portal.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable
data class ChatMessage(
    val role: String,               // "user", "assistant", "system"
    val content: JsonElement,       // String or Array of content parts
    val timestamp: Long = System.currentTimeMillis(),
    val model: String? = null,
    val provider: String? = null,
    val reasoning: String? = null
)

@Serializable
data class ChatRequest(
    val messages: List<ChatMessage>,
    val operator: String,
    val provider: String? = null,
    val model: String? = null
)

@Serializable
data class StreamRequest(
    val messages: List<ChatMessage>,
    val operator: String,
    val provider: String? = null,
    val model: String? = null,
    @SerialName("session_id") val sessionId: String? = null,
    @SerialName("device_id") val deviceId: String? = null
)

@Serializable
data class SaveRequest(
    val operator: String,
    @SerialName("user_message") val userMessage: String,
    @SerialName("assistant_response") val assistantResponse: String,
    val reasoning: String? = null,
    val model: String? = null,
    val tokens: TokenCount? = null,
    val provenance: Provenance? = null
)

@Serializable
data class TokenCount(
    val prompt: Int = 0,
    val completion: Int = 0
)

@Serializable
data class Provenance(
    val recent: List<String> = emptyList(),
    val keyword: List<String> = emptyList(),
    val checkpoint: List<String> = emptyList()
)

@Serializable
data class TaskResponse(
    @SerialName("task_id") val taskId: String,
    val status: String,
    val message: String? = null
)

@Serializable
data class TaskStatus(
    @SerialName("task_id") val taskId: String,
    @SerialName("task_type") val taskType: String? = null,
    val status: String,
    val operator: String? = null,
    val progress: Int = 0,
    @SerialName("result_data") val resultData: JsonElement? = null,
    @SerialName("result_url") val resultUrl: String? = null,
    val error: String? = null
)

@Serializable
data class HealthResponse(
    val status: String,
    val detail: String? = null,
    @SerialName("snapshot_count") val snapshotCount: Int = 0,
    @SerialName("uptime_seconds") val uptimeSeconds: Long = 0
)

@Serializable
data class UploadResponse(
    val url: String
)
```

`data/model/Provider.kt`:
```kotlin
package com.aiblackbox.portal.data.model

enum class ChatProvider(val id: String, val displayName: String) {
    GEMINI("gemini", "Gemini"),
    ANTHROPIC("anthropic", "Anthropic"),
    OPENAI("openai", "OpenAI"),
    XAI("xai", "xAI"),
    AGENTS("agents", "Claude Code"),
    GEMINI_AGENTS("gemini-agents", "Gemini CLI"),
    REALTIME("realtime", "GPT Realtime"),
    GEMINI_LIVE("gemini-live", "Gemini Live"),
    GROK_LIVE("grok-live", "Grok Live"),
    COMPUTER_USE("computer-use", "Computer Use");

    companion object {
        fun fromId(id: String): ChatProvider =
            entries.find { it.id == id } ?: GEMINI
    }

    val isAgent get() = this == AGENTS || this == GEMINI_AGENTS
    val isVoice get() = this == REALTIME || this == GEMINI_LIVE || this == GROK_LIVE
    val isStreaming get() = !isAgent && !isVoice
}
```

**Step 2: Create BlackBoxApi.kt**

```kotlin
package com.aiblackbox.portal.data.api

import com.aiblackbox.portal.util.Constants
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class BlackBoxApi(private val baseUrl: String) {

    val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        encodeDefaults = true
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    // Long-timeout client for SSE streaming
    val streamClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)  // No read timeout for SSE
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private val jsonMediaType = "application/json".toMediaType()

    private fun buildRequest(path: String): Request.Builder =
        Request.Builder()
            .url("$baseUrl$path")
            .header(Constants.CLIENT_HEADER, Constants.CLIENT_ID)

    // ── GET ──
    suspend fun get(path: String): String = withContext(Dispatchers.IO) {
        val request = buildRequest(path).get().build()
        client.newCall(request).await().body?.string() ?: ""
    }

    // ── POST JSON ──
    suspend fun post(path: String, body: String): String = withContext(Dispatchers.IO) {
        val request = buildRequest(path)
            .post(body.toRequestBody(jsonMediaType))
            .build()
        client.newCall(request).await().body?.string() ?: ""
    }

    // ── PUT JSON ──
    suspend fun put(path: String, body: String): String = withContext(Dispatchers.IO) {
        val request = buildRequest(path)
            .put(body.toRequestBody(jsonMediaType))
            .build()
        client.newCall(request).await().body?.string() ?: ""
    }

    // ── DELETE ──
    suspend fun delete(path: String): String = withContext(Dispatchers.IO) {
        val request = buildRequest(path).delete().build()
        client.newCall(request).await().body?.string() ?: ""
    }

    // ── Upload file (multipart) ──
    suspend fun uploadFile(path: String, file: File, fieldName: String = "file"): String =
        withContext(Dispatchers.IO) {
            val body = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart(
                    fieldName, file.name,
                    file.asRequestBody("application/octet-stream".toMediaType())
                )
                .build()
            val request = buildRequest(path)
                .post(body)
                .build()
            client.newCall(request).await().body?.string() ?: ""
        }

    // ── SSE streaming request (POST) ──
    fun streamPost(path: String, body: String): Call {
        val request = buildRequest(path)
            .post(body.toRequestBody(jsonMediaType))
            .header("Accept", "text/event-stream")
            .build()
        return streamClient.newCall(request)
    }

    // ── SSE streaming request (GET) ──
    fun streamGet(path: String, queryParams: Map<String, String> = emptyMap()): Call {
        val url = buildString {
            append("$baseUrl$path")
            if (queryParams.isNotEmpty()) {
                append("?")
                append(queryParams.entries.joinToString("&") { "${it.key}=${it.value}" })
            }
        }
        val request = Request.Builder()
            .url(url)
            .header(Constants.CLIENT_HEADER, Constants.CLIENT_ID)
            .header("Accept", "text/event-stream")
            .get()
            .build()
        return streamClient.newCall(request)
    }

    // ── Raw OkHttp client access (for WebSocket) ──
    fun getClient(): OkHttpClient = client
    fun getBaseUrl(): String = baseUrl

    private suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
        cont.invokeOnCancellation { cancel() }
        enqueue(object : Callback {
            override fun onResponse(call: Call, response: Response) {
                cont.resume(response)
            }
            override fun onFailure(call: Call, e: IOException) {
                cont.resumeWithException(e)
            }
        })
    }
}
```

**Step 3: Create SSEClient.kt**

```kotlin
package com.aiblackbox.portal.data.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Response
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader

data class SSEEvent(
    val event: String = "message",
    val data: String = "",
    val id: String? = null
)

class SSEClient(private val api: BlackBoxApi) {

    fun stream(path: String, body: String): Flow<SSEEvent> = callbackFlow {
        val call = api.streamPost(path, body)

        call.enqueue(object : Callback {
            override fun onResponse(call: Call, response: Response) {
                try {
                    val reader = BufferedReader(
                        InputStreamReader(response.body?.byteStream() ?: return)
                    )
                    var eventType = "message"
                    var data = StringBuilder()

                    reader.forEachLine { line ->
                        when {
                            line.startsWith("event:") -> {
                                eventType = line.removePrefix("event:").trim()
                            }
                            line.startsWith("data:") -> {
                                data.append(line.removePrefix("data:").trim())
                            }
                            line.isBlank() && data.isNotEmpty() -> {
                                trySend(SSEEvent(event = eventType, data = data.toString()))
                                eventType = "message"
                                data = StringBuilder()
                            }
                        }
                    }
                    // Flush remaining
                    if (data.isNotEmpty()) {
                        trySend(SSEEvent(event = eventType, data = data.toString()))
                    }
                    close()
                } catch (e: Exception) {
                    close(e)
                }
            }

            override fun onFailure(call: Call, e: IOException) {
                close(e)
            }
        })

        awaitClose { call.cancel() }
    }
}
```

**Step 4: Create WebSocketClient.kt**

```kotlin
package com.aiblackbox.portal.data.api

import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

sealed class WsMessage {
    data class Text(val text: String) : WsMessage()
    data class Error(val error: Throwable) : WsMessage()
    data object Connected : WsMessage()
    data object Disconnected : WsMessage()
}

class WebSocketClient(private val client: OkHttpClient) {

    private var webSocket: WebSocket? = null

    fun connect(url: String): Flow<WsMessage> = callbackFlow {
        val request = Request.Builder().url(url).build()

        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                trySend(WsMessage.Connected)
            }

            override fun onMessage(ws: WebSocket, text: String) {
                trySend(WsMessage.Text(text))
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                trySend(WsMessage.Error(t))
                close(t)
            }

            override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                trySend(WsMessage.Disconnected)
                close()
            }
        })

        awaitClose {
            webSocket?.close(1000, "Client closed")
            webSocket = null
        }
    }

    fun send(text: String): Boolean = webSocket?.send(text) ?: false

    fun close() {
        webSocket?.close(1000, "Client closed")
        webSocket = null
    }
}
```

**Step 5: Commit**
```
feat: add API client layer — REST, SSE streaming, WebSocket
```

---

### Task 1.6: Navigation Graph

**Files:**
- Create: `navigation/NavGraph.kt`

**Step 1: Create NavGraph.kt**

```kotlin
package com.aiblackbox.portal.navigation

import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable

object Routes {
    const val CHAT = "chat"
    const val SETTINGS = "settings"
    const val TIMELINE = "timeline"
    const val MEDIA = "media"
    const val DEVICES = "devices"
    const val CRON = "cron"
    const val TELEPHONY = "telephony"
    const val CELLULAR = "cellular"
    const val IMAGE_GEN = "image_gen"
    const val VIDEO_GEN = "video_gen"
    const val MUSIC_GEN = "music_gen"
    const val TTS = "tts"
    const val COMPUTER_USE = "computer_use"
}

@Composable
fun BlackBoxNavGraph(
    navController: NavHostController,
    origin: String,
    operator: String
) {
    NavHost(
        navController = navController,
        startDestination = Routes.CHAT
    ) {
        composable(Routes.CHAT) {
            // ChatScreen — Phase 2
            // Placeholder for now
            PlaceholderScreen("Chat")
        }
        composable(Routes.SETTINGS) {
            PlaceholderScreen("Settings")
        }
        composable(Routes.TIMELINE) {
            PlaceholderScreen("Timeline")
        }
        composable(Routes.MEDIA) {
            PlaceholderScreen("Media Browser")
        }
        composable(Routes.DEVICES) {
            PlaceholderScreen("Device Manager")
        }
        composable(Routes.CRON) {
            PlaceholderScreen("Cron Jobs")
        }
        composable(Routes.TELEPHONY) {
            PlaceholderScreen("Telephony")
        }
        composable(Routes.CELLULAR) {
            PlaceholderScreen("Cellular")
        }
        composable(Routes.IMAGE_GEN) {
            PlaceholderScreen("Image Generation")
        }
        composable(Routes.VIDEO_GEN) {
            PlaceholderScreen("Video Generation")
        }
        composable(Routes.MUSIC_GEN) {
            PlaceholderScreen("Music Generation")
        }
        composable(Routes.TTS) {
            PlaceholderScreen("Text-to-Speech")
        }
        composable(Routes.COMPUTER_USE) {
            PlaceholderScreen("Computer Use")
        }
    }
}

@Composable
private fun PlaceholderScreen(name: String) {
    androidx.compose.foundation.layout.Box(
        modifier = androidx.compose.ui.Modifier.fillMaxSize(),
        contentAlignment = androidx.compose.ui.Alignment.Center
    ) {
        androidx.compose.material3.Text(
            text = name,
            style = androidx.compose.material3.MaterialTheme.typography.headlineMedium,
            color = androidx.compose.material3.MaterialTheme.colorScheme.onBackground
        )
    }
}
```

**Step 2: Commit**
```
feat: add navigation graph with all route definitions
```

---

### Task 1.7: NativeMainActivity — Compose Shell

**Files:**
- Create: `NativeMainActivity.kt`

**Step 1: Create NativeMainActivity.kt**

```kotlin
package com.aiblackbox.portal

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.navigation.compose.rememberNavController
import com.aiblackbox.portal.data.api.BlackBoxApi
import com.aiblackbox.portal.data.store.BlackBoxStore
import com.aiblackbox.portal.navigation.BlackBoxNavGraph
import com.aiblackbox.portal.ui.theme.BlackBoxTheme
import com.aiblackbox.portal.ui.theme.BbxBlack
import com.aiblackbox.portal.util.Constants

class NativeMainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // Read origin from SharedPreferences (set during pairing)
        val prefs = getSharedPreferences(Constants.PREFS_NAME, MODE_PRIVATE)
        val origin = prefs.getString(Constants.KEY_ORIGIN, "") ?: ""

        setContent {
            BlackBoxTheme {
                val navController = rememberNavController()
                val store = remember { BlackBoxStore(applicationContext) }
                val operator by store.operator.collectAsState(initial = Constants.DEFAULT_OPERATOR)

                Scaffold(
                    containerColor = BbxBlack,
                    topBar = {
                        // TopBar — Phase 2
                    },
                    bottomBar = {
                        // Composer — Phase 2
                    }
                ) { padding ->
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(padding)
                            .background(BbxBlack)
                    ) {
                        BlackBoxNavGraph(
                            navController = navController,
                            origin = origin,
                            operator = operator
                        )
                    }
                }
            }
        }
    }
}
```

**Step 2: Commit**
```
feat: add NativeMainActivity with Compose shell and navigation
```

---

### Task 1.8: Platform Routing — PairingActivity Modification

**Files:**
- Modify: `PairingActivity.kt`
- Modify: `AndroidManifest.xml`

**Step 1: Modify PairingActivity.kt to route based on UI mode**

Add native UI preference check. If `use_native_ui` is true (default), route to `NativeMainActivity`. Otherwise, route to `PortalActivity` (legacy WebView).

In `PairingActivity.kt`, change the two `startActivity(Intent(this, PortalActivity::class.java))` calls:

```kotlin
// Replace the existing auto-launch in onCreate:
prefs.getString("origin", null)?.let {
    launchMainActivity()
    finish(); return
}

// Replace the launch in onActivityResult:
// startActivity(Intent(this, PortalActivity::class.java))
launchMainActivity()
finish(); return
```

Add this private method:

```kotlin
private fun launchMainActivity() {
    val useNative = prefs.getBoolean("use_native_ui", true)
    val target = if (useNative) NativeMainActivity::class.java else PortalActivity::class.java
    startActivity(Intent(this, target))
}
```

**Step 2: Register NativeMainActivity in AndroidManifest.xml**

Add after the PortalActivity declaration:

```xml
<activity
    android:name=".NativeMainActivity"
    android:exported="false"
    android:configChanges="orientation|screenSize|screenLayout|keyboardHidden"
    android:theme="@style/Theme.AIBlackBox.NoBar"/>
```

**Step 3: Build and verify**

The app should now launch into the native Compose shell showing "Chat" placeholder text on a pure black background.

**Step 4: Commit**
```
feat: add platform routing — PairingActivity routes to native or webview
```

---

### Task 1.9: Top Bar Component (Shell)

**Files:**
- Create: `ui/components/TopBar.kt`
- Create: `ui/components/GlassCard.kt`

**Step 1: Create GlassCard.kt** (reusable glass morphism component)

```kotlin
package com.aiblackbox.portal.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.unit.dp
import com.aiblackbox.portal.ui.theme.GlassBg
import com.aiblackbox.portal.ui.theme.GlassBorder
import com.aiblackbox.portal.ui.theme.BlackBoxShapes

@Composable
fun GlassCard(
    modifier: Modifier = Modifier,
    shape: Shape = BlackBoxShapes.medium,
    content: @Composable BoxScope.() -> Unit
) {
    Box(
        modifier = modifier
            .clip(shape)
            .background(GlassBg)
            .border(1.dp, GlassBorder, shape),
        content = content
    )
}
```

**Step 2: Create TopBar.kt**

```kotlin
package com.aiblackbox.portal.ui.components

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aiblackbox.portal.ui.theme.BbxAccent
import com.aiblackbox.portal.ui.theme.BbxBlack
import com.aiblackbox.portal.ui.theme.BbxDim
import com.aiblackbox.portal.ui.theme.BbxWhite
import com.aiblackbox.portal.ui.theme.GlowGreen
import com.aiblackbox.portal.ui.theme.Neutral200

@Composable
fun BlackBoxTopBar(
    operator: String,
    snapshotCount: Int = 0,
    isHealthy: Boolean = true,
    onMenuClick: () -> Unit = {},
    onTimelineClick: () -> Unit = {},
    onOperatorClick: () -> Unit = {}
) {
    val healthColor by animateColorAsState(
        targetValue = if (isHealthy) GlowGreen else BbxAccent,
        label = "health"
    )

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(BbxBlack)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        // Brand
        Text(
            text = "AI",
            style = MaterialTheme.typography.titleLarge.copy(
                fontWeight = FontWeight.ExtraBold,
                color = BbxAccent,
                fontSize = 20.sp
            )
        )
        Text(
            text = " BlackBox",
            style = MaterialTheme.typography.titleLarge.copy(
                fontWeight = FontWeight.Bold,
                color = BbxWhite,
                fontSize = 20.sp
            )
        )

        Spacer(Modifier.width(8.dp))

        // Health indicator
        Box(
            modifier = Modifier
                .size(8.dp)
                .clip(CircleShape)
                .background(healthColor)
        )

        Spacer(Modifier.weight(1f))

        // Snapshot count
        if (snapshotCount > 0) {
            Text(
                text = "$snapshotCount",
                style = MaterialTheme.typography.labelMedium,
                color = BbxDim,
                modifier = Modifier.padding(end = 12.dp)
            )
        }

        // Operator pill
        GlassCard(
            modifier = Modifier
                .clickable { onOperatorClick() }
                .height(32.dp),
            shape = CircleShape
        ) {
            Text(
                text = operator,
                style = MaterialTheme.typography.labelLarge,
                color = BbxWhite,
                modifier = Modifier
                    .align(Alignment.Center)
                    .padding(horizontal = 14.dp)
            )
        }

        Spacer(Modifier.width(8.dp))

        // Menu button
        IconButton(onClick = onMenuClick) {
            Box(
                modifier = Modifier
                    .size(24.dp)
                    .background(Neutral200, CircleShape)
            )
            // TODO: Replace with proper menu icon in Phase 2
        }
    }
}
```

**Step 3: Wire TopBar into NativeMainActivity**

Update the `topBar` in `NativeMainActivity.kt`:
```kotlin
topBar = {
    BlackBoxTopBar(
        operator = operator,
        onMenuClick = { /* TODO */ },
        onTimelineClick = { navController.navigate(Routes.TIMELINE) },
        onOperatorClick = { /* TODO: operator picker */ }
    )
}
```

**Step 4: Commit**
```
feat: add TopBar component with brand, health indicator, operator pill
```

---

## Phase 2: Core Chat

**Goal:** Fully functional chat screen — message bubbles, markdown rendering, message composer, SSE streaming, chat history persistence. After this phase, you can send messages and receive streamed AI responses.

### Task 2.1: Chat ViewModel

**Files:**
- Create: `ui/chat/ChatViewModel.kt`
- Create: `data/repository/ChatRepository.kt`

The ChatViewModel manages:
- Message list (StateFlow<List<UiMessage>>)
- Current input text
- Streaming state (idle, streaming, thinking)
- Provider/model selection
- History persistence (serialize to DataStore)

ChatRepository wraps BlackBoxApi for:
- `sendStream(messages, provider, model, operator)` → Flow<SSEEvent>
- `sendAsync(messages, provider, model, operator)` → TaskResponse
- `saveConversation(request)` → SaveResponse
- Message serialization/deserialization

UiMessage data class:
```kotlin
data class UiMessage(
    val id: String = UUID.randomUUID().toString(),
    val role: String,           // "user" | "assistant"
    val content: String,        // rendered text
    val reasoning: String? = null,
    val timestamp: Long = System.currentTimeMillis(),
    val isStreaming: Boolean = false,
    val model: String? = null,
    val provider: String? = null,
    val images: List<String> = emptyList(),    // attached image URLs
    val attachments: List<String> = emptyList() // other file URLs
)
```

Key ViewModel actions:
- `sendMessage(text, attachments)` — append user message, trigger streaming
- `onStreamEvent(event)` — process SSE events, update assistant bubble
- `clearHistory()` — wipe messages
- `loadHistory()` — restore from DataStore

---

### Task 2.2: Markdown Rendering

**Files:**
- Create: `ui/components/MarkdownText.kt`

Options:
1. **Compose Markdown library** (recommended): `com.mikepenz:multiplatform-markdown-renderer-m3:0.28.0`
2. **Custom basic renderer** using AnnotatedString for bold, italic, code, links, headers
3. **WebView fallback** for complex markdown (code blocks with syntax highlighting)

Recommended approach: Use `multiplatform-markdown-renderer` for rich markdown with code block syntax highlighting. Add dependency:
```groovy
implementation "com.mikepenz:multiplatform-markdown-renderer-m3:0.28.0"
implementation "com.mikepenz:multiplatform-markdown-renderer-code:0.28.0"
```

The composable wraps the library with BlackBox theme colors for code blocks, links, headers matching the Portal's syntax highlighting palette.

---

### Task 2.3: Chat Bubble Component

**Files:**
- Create: `ui/components/ChatBubble.kt`

Key features:
- Asymmetric rounded corners (UserBubbleShape / AssistantBubbleShape from Shape.kt)
- User bubble: Neutral250 background, right-aligned
- Assistant bubble: BbxBlack background (blends with bg), left-aligned
- Thinking/reasoning expandable section (collapsible with animation)
- Streaming indicator (pulsing cursor)
- Image attachments inline (Coil AsyncImage)
- Copy button, share button (long-press menu)
- Markdown rendered content
- Timestamp display

---

### Task 2.4: Composer Component

**Files:**
- Create: `ui/chat/Composer.kt`

Features:
- Multi-line text input with auto-expand (1-6 lines)
- Send button (BbxAccent, animated on valid input)
- Attach button (file picker, camera, gallery)
- Slash command detection (/) triggers autocomplete overlay
- Provider indicator showing current model
- Disabled state during streaming

---

### Task 2.5: Chat Screen Assembly

**Files:**
- Create: `ui/chat/ChatScreen.kt`
- Create: `ui/chat/HomeScreen.kt`

ChatScreen:
- LazyColumn for message list (reversed, newest at bottom)
- Auto-scroll to bottom on new message
- Pull-to-load-more history
- HomeScreen shown when history is empty (help hints, branding)
- Keyboard-aware layout (imePadding)

HomeScreen:
- BlackBox branding with animated logo
- Feature hint cards (glass morphism)
- Quick action buttons (new chat, browse timeline, etc.)

---

### Task 2.6: Wire Chat into Navigation

Replace the placeholder in NavGraph with ChatScreen. Wire ChatViewModel. Test end-to-end: type message → SSE stream → bubble renders with markdown.

---

## Phase 3: File Upload & Task Manager

**Goal:** File attachments in chat, background task polling for async operations.

### Task 3.1: File Attachment

- Native file picker via `ActivityResultContracts.GetContent` / `GetMultipleContents`
- Image preview thumbnails in composer
- Upload to `/upload` endpoint via multipart
- Include URLs in message content array

### Task 3.2: Task Manager

- Background polling service for `/tasks/list` and `/tasks/status/{id}`
- Notification on task completion
- Progress indicators in UI
- Support for image/video/music generation tasks

---

## Phase 4: Agent Integration

**Goal:** Claude Code and Gemini CLI agent sessions via WebSocket.

### Task 4.1: Claude Code Agent Client

- WebSocket connection to `/ws/agent/{sessionId}`
- Send prompt, receive streaming output
- Permission request/response flow
- Session persistence (resume existing session)
- Tool execution display (file edits, bash commands)

### Task 4.2: Gemini Agent Client

- WebSocket connection to `/ws/gemini-agent/{sessionId}`
- YOLO mode toggle
- Streaming output with tool tracking

### Task 4.3: Agent Banner UI

- Collapsible banner showing agent status
- Model selector (opus/sonnet/haiku for Claude, pro/flash for Gemini)
- New session / end session buttons
- Context usage meter
- Cost display

### Task 4.4: Status Line

- Agent metrics: context %, cost, model, duration
- Animated progress bar for context window usage

### Task 4.5: Slash Command Autocomplete

- Fetch `/agent/commands` on demand
- Dropdown overlay when user types `/`
- Filter as user continues typing
- Insert selected command into composer

### Task 4.6: Todo Panel

- Floating panel showing Claude Code TodoWrite tasks
- Checkboxes, progress tracking
- Dismiss/minimize

---

## Phase 5: Voice Agents

**Goal:** Real-time voice interaction with GPT Realtime, Gemini Live, Grok Live.

### Task 5.1: Audio Recorder

- 16kHz mono PCM recording via AudioRecord
- Waveform visualization (Canvas-based composable)
- Start/stop/pause controls
- Buffer management for streaming

### Task 5.2: GPT Realtime Client

- WebSocket to OpenAI Realtime API (via backend proxy)
- Bidirectional audio streaming
- Transcript display
- Voice selector

### Task 5.3: Gemini Live Client

- WebSocket to Gemini Live API (via backend proxy)
- Bidirectional audio
- Voice selection (Charon, Puck, Kore, Aoede, etc.)
- VAD parameters

### Task 5.4: Grok Live Client

- WebSocket to Grok Live API (via backend proxy)
- Audio I/O
- Transcript

### Task 5.5: Voice Banner UI

- Unified voice agent banner
- Waveform visualization
- Transcript overlay
- Mic toggle, mute, disconnect
- Voice picker dropdown

### Task 5.6: TTS/STT Integration

- OpenAI TTS playback
- Google Cloud TTS
- Gemini Pro TTS
- Speech-to-text recording + transcription
- Auto-TTS toggle for assistant responses

---

## Phase 6: Media & Generation

**Goal:** Image/video/music generation modals, media browser.

### Task 6.1: Image Generation Screen

- Prompt input
- Aspect ratio selector (visual grid)
- Resolution picker
- Number of images (1-4)
- Generate → poll task → display results
- Save/share generated images

### Task 6.2: Video Generation Screen

- Prompt input
- Duration picker (4/6/8s)
- Aspect ratio, resolution
- Image-to-video option (attach source image)
- Progress tracking (5-20 min)
- Video player for results

### Task 6.3: Music Generation Screen

- Prompt input with style suggestions
- Negative prompt
- Sample count
- Progress tracking
- Audio player for results

### Task 6.4: TTS Screen

- Text input (multi-line)
- Voice browser (search/filter 1000+ voices)
- Model selector (tts-1, tts-1-hd, Gemini Pro)
- Preview playback
- SSML editor (advanced)

### Task 6.5: Media Browser

- Grid/list view of uploaded media
- Filter by type (image, video, audio, document)
- Inline players (video, audio)
- Full-screen image viewer
- Delete, share, copy URL actions
- Pagination

---

## Phase 7: Advanced Modules

**Goal:** All remaining Portal features as native screens.

### Task 7.1: Timeline Browser

- Snapshot list with search/filter
- Semantic search via `/fossil/hybrid`
- Snapshot detail view (content, metadata, operator, timestamp)
- Operator filter
- Syntax highlighting in snapshot content

### Task 7.2: Device Manager

- Tailscale mesh device list
- Device health checks
- ADB connect/disconnect/pair
- Device screenshot viewer
- Smart pairing flow

### Task 7.3: Computer Use

- CU drawer (session management, device selector, E-stop)
- Live screenshot viewer (polling, full-screen overlay)
- Click/scroll/type/key action sending
- Prompt queue

### Task 7.4: Cron Manager

- Job list with status indicators
- CRUD operations (create/edit/delete)
- Cron expression builder
- Run now / pause / resume
- Job history viewer
- Delivery target configuration

### Task 7.5: Telephony Manager

- Gateway list (Asterisk/TG200)
- Discovery
- Gateway CRUD
- Connection testing
- Status display

### Task 7.6: Cellular Manager

- Modem status display
- Signal strength, band info
- Speed test trigger
- AT command console
- Reconnect actions
- Connection history

---

## Phase 8: Settings & Polish

**Goal:** Settings screen, provider/model management, operator management, visual polish.

### Task 8.1: Settings Bottom Sheet

- Provider selector with visual cards
- Model picker (per-provider)
- Streaming toggle
- Operator management (add, switch, delete)
- TTS voice preference
- UI mode toggle (Native ↔ WebView fallback)
- About / version info
- Overlay toggle (launch OverlayService)
- Health check / system metrics display

### Task 8.2: Provider Banner System

- Animated provider banners (like Portal)
- Different banner content per provider type
- Collapse/expand with smooth animation
- Provider-specific accent colors

### Task 8.3: Loading States & Animations

- Skeleton/shimmer loading for chat history
- Pulsing dot for streaming cursor
- Spring animations for bubble appearance
- Haptic feedback on send, receive, navigation
- Smooth scroll-to-bottom with FAB

### Task 8.4: Menu System

- Bottom sheet or navigation drawer
- Quick actions: Health, Mint, Assert, Manifest
- Generation shortcuts (Image, Video, Music, TTS)
- Module launchers (Timeline, Media, Devices, Cron, etc.)
- App registry browser

### Task 8.5: Error Handling & Offline State

- Connection error states with retry
- Offline mode banner
- Graceful degradation when API unreachable
- Auto-reconnect for WebSocket sessions

---

## Phase 9: Integration Testing & Feature Parity

**Goal:** Verify every Portal feature works identically in native. Full regression.

### Task 9.1: Feature Parity Checklist

Cross-reference every Portal JS module feature against native implementation:
- [ ] Chat send/receive (all providers)
- [ ] SSE streaming with thinking/reasoning
- [ ] Agent sessions (Claude Code + Gemini CLI)
- [ ] Voice agents (all 3)
- [ ] File upload and inline display
- [ ] Image/video/music generation
- [ ] TTS/STT
- [ ] Timeline search and display
- [ ] Device management
- [ ] Computer Use viewer
- [ ] Cron job management
- [ ] Telephony management
- [ ] Cellular management
- [ ] Operator switching
- [ ] Notification system
- [ ] Slash commands
- [ ] Settings persistence
- [ ] History persistence across app restarts
- [ ] Overlay service launch from native UI
- [ ] Edge-to-edge display
- [ ] Keyboard handling
- [ ] Deep link support (optional)

### Task 9.2: Performance Benchmarks

Compare native vs WebView:
- Time to first interaction
- Message render latency
- Scroll smoothness (120Hz)
- Memory usage
- Battery consumption

### Task 9.3: Snapshot & Documentation

- Create development snapshot documenting the implementation
- Update CLAUDE.md with native app architecture notes
- Update build/deployment instructions

---

## Dependency Graph

```
Phase 1 (Foundation)
  ↓
Phase 2 (Core Chat) ← must complete before anything else uses chat
  ↓
Phase 3 (Files & Tasks) ── Phase 4 (Agents) ── Phase 5 (Voice)
  ↓                           ↓                    ↓
Phase 6 (Media & Gen) ─── Phase 7 (Advanced Modules)
                              ↓
                        Phase 8 (Settings & Polish)
                              ↓
                        Phase 9 (Integration Testing)
```

Phases 3, 4, 5 can be executed in parallel after Phase 2.
Phases 6, 7 can be executed in parallel after Phase 3.

---

## Estimated Scope

| Phase | New Files | LOC Estimate |
|---|---|---|
| 1. Foundation | 12 | ~1,200 |
| 2. Core Chat | 8 | ~2,500 |
| 3. Files & Tasks | 4 | ~800 |
| 4. Agents | 8 | ~3,000 |
| 5. Voice | 8 | ~2,500 |
| 6. Media & Gen | 8 | ~2,000 |
| 7. Advanced | 14 | ~4,000 |
| 8. Settings & Polish | 6 | ~1,500 |
| 9. Testing | 2 | ~500 |
| **Total** | **~70** | **~18,000** |

---

## Critical Implementation Notes

1. **All API calls use the same `origin` from QR pairing** — no hardcoded URLs
2. **`X-BlackBox-Client: native-android/1.0` header** on every request for future backend detection
3. **Operator passed in every request body** — same as Portal JS
4. **SSE parsing must handle multi-line `data:` fields** — some responses span multiple data lines before blank line
5. **WebSocket reconnection with exponential backoff** — agent sessions must survive network blips
6. **SharedPreferences for `origin`/`operator` remain** (PairingActivity uses them) — DataStore for everything else
7. **`PortalActivity` preserved as fallback** — settings toggle between native and WebView
8. **Overlay system unchanged** — OverlayService/Bridge/XR work independently of UI mode
9. **Audio recording reuses OverlayService patterns** — 16kHz mono PCM, same format
10. **Coil for all image loading** — handles caching, transformations, error states
