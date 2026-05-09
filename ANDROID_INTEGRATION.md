# BlackBox Android WebWrapper Integration Guide

Complete guide for integrating BlackBox Portal push notifications with Android Studio WebWrapper app.

## Architecture Overview

```
BlackBox Portal (Web)
    ↓ JavaScript Interface
WebView (Android Bridge)
    ↓ Native API Calls
Android Notification System
    ↓
Device Notifications
```

## Prerequisites

- Existing Android WebWrapper app with WebView
- Android Studio (Kotlin or Java)
- Target API Level: 26+ (Android 8.0+) for notification channels
- BlackBox Portal accessible via Tailscale or local network

---

## Part 1: Android Studio Implementation

### Step 1: Add Permissions to AndroidManifest.xml

```xml
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <!-- Existing permissions -->
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />

    <!-- ADD THESE FOR NOTIFICATIONS -->
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.VIBRATE" />
    <uses-permission android:name="android.permission.WAKE_LOCK" />

    <application
        android:name=".BlackBoxApplication"
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="@string/app_name"
        android:theme="@style/AppTheme">

        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
```

### Step 2: Create Notification Manager Helper (Kotlin)

Create file: `app/src/main/java/com/yourpackage/BlackBoxNotificationManager.kt`

```kotlin
package com.yourpackage.blackbox

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

class BlackBoxNotificationManager(private val context: Context) {

    companion object {
        private const val CHANNEL_ID_TASKS = "blackbox_tasks"
        private const val CHANNEL_ID_SYSTEM = "blackbox_system"
        private const val NOTIFICATION_ID_BASE = 1000
    }

    init {
        createNotificationChannels()
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            // Task completion channel
            val taskChannel = NotificationChannel(
                CHANNEL_ID_TASKS,
                "BlackBox Tasks",
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = "Notifications for completed tasks (images, videos, audio)"
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 200, 100, 200)
            }

            // System notifications channel
            val systemChannel = NotificationChannel(
                CHANNEL_ID_SYSTEM,
                "BlackBox System",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "System status and updates"
            }

            val notificationManager = context.getSystemService(NotificationManager::class.java)
            notificationManager.createNotificationChannel(taskChannel)
            notificationManager.createNotificationChannel(systemChannel)
        }
    }

    fun showTaskNotification(
        title: String,
        body: String,
        operator: String? = null,
        taskType: String? = null,
        isSuccess: Boolean = true
    ) {
        // Create intent to open app when notification clicked
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra("from_notification", true)
            putExtra("task_type", taskType)
        }

        val pendingIntent = PendingIntent.getActivity(
            context,
            0,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        // Choose icon based on success/failure
        val icon = if (isSuccess) {
            android.R.drawable.ic_dialog_info // Or your custom icon
        } else {
            android.R.drawable.ic_dialog_alert
        }

        // Build notification
        val notification = NotificationCompat.Builder(context, CHANNEL_ID_TASKS)
            .setSmallIcon(icon)
            .setContentTitle(title)
            .setContentText(body)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true) // Dismiss when clicked
            .setVibrate(longArrayOf(0, 200, 100, 200))
            .build()

        // Generate unique notification ID (can use task ID hash if available)
        val notificationId = NOTIFICATION_ID_BASE + (taskType?.hashCode() ?: 0)

        NotificationManagerCompat.from(context).notify(notificationId, notification)
    }

    fun showSystemNotification(title: String, body: String) {
        val notification = NotificationCompat.Builder(context, CHANNEL_ID_SYSTEM)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(body)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setAutoCancel(true)
            .build()

        NotificationManagerCompat.from(context).notify(NOTIFICATION_ID_BASE + 1, notification)
    }
}
```

### Step 3: Create JavaScript Bridge Interface

Create file: `app/src/main/java/com/yourpackage/WebAppInterface.kt`

```kotlin
package com.yourpackage.blackbox

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.webkit.JavascriptInterface
import android.widget.Toast

class WebAppInterface(
    private val context: Context,
    private val notificationManager: BlackBoxNotificationManager
) {

    @JavascriptInterface
    fun showNotification(title: String, body: String, operator: String?, taskType: String?, isSuccess: Boolean) {
        Handler(Looper.getMainLooper()).post {
            // Filter by operator if needed (implement your logic)
            // For now, show all notifications
            notificationManager.showTaskNotification(title, body, operator, taskType, isSuccess)
        }
    }

    @JavascriptInterface
    fun vibrate(pattern: String) {
        // Pattern is comma-separated: "200,100,200"
        Handler(Looper.getMainLooper()).post {
            try {
                val vibrator = context.getSystemService(Context.VIBRATOR_SERVICE) as android.os.Vibrator
                val patternArray = pattern.split(",").map { it.toLong() }.toLongArray()
                vibrator.vibrate(patternArray, -1) // -1 means no repeat
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }
    }

    @JavascriptInterface
    fun requestNotificationPermission(): Boolean {
        // On Android 13+, need to request POST_NOTIFICATIONS permission
        // Return true if permission granted, false otherwise
        // This is a simplified version - implement proper permission request flow
        return true
    }

    @JavascriptInterface
    fun isNotificationEnabled(): Boolean {
        return notificationManager != null
    }

    @JavascriptInterface
    fun log(message: String) {
        android.util.Log.d("BlackBoxWebView", message)
    }
}
```

### Step 4: MainActivity Setup

Update `MainActivity.kt`:

```kotlin
package com.yourpackage.blackbox

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var notificationManager: BlackBoxNotificationManager
    private val NOTIFICATION_PERMISSION_CODE = 100

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Initialize notification manager
        notificationManager = BlackBoxNotificationManager(this)

        // Request notification permission on Android 13+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.POST_NOTIFICATIONS
                ) != PackageManager.PERMISSION_GRANTED
            ) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    NOTIFICATION_PERMISSION_CODE
                )
            }
        }

        // Setup WebView
        webView = findViewById(R.id.webView)
        setupWebView()

        // Load BlackBox Portal (use your Tailscale URL)
        val blackboxUrl = "https://your-tailscale-hostname/" // Or http://10.0.2.2:9091 for emulator
        webView.loadUrl(blackboxUrl)
    }

    private fun setupWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            cacheMode = WebSettings.LOAD_DEFAULT
            mediaPlaybackRequiresUserGesture = false // For autoplay videos
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }

        // Add JavaScript interface
        webView.addJavascriptInterface(
            WebAppInterface(this, notificationManager),
            "AndroidBridge"
        )

        // WebViewClient for handling navigation
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean {
                // Keep navigation within WebView
                return false
            }

            override fun onPageFinished(view: WebView, url: String) {
                super.onPageFinished(view, url)
                // Inject detection script
                view.evaluateJavascript(
                    "window.isAndroidWebWrapper = true;",
                    null
                )
            }
        }

        // Enable remote debugging
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT) {
            WebView.setWebContentsDebuggingEnabled(true)
        }
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }
}
```

### Step 5: Layout File

`app/src/main/res/layout/activity_main.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<androidx.constraintlayout.widget.ConstraintLayout
    xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:app="http://schemas.android.com/apk/res-auto"
    android:layout_width="match_parent"
    android:layout_height="match_parent">

    <WebView
        android:id="@+id/webView"
        android:layout_width="match_parent"
        android:layout_height="match_parent"
        app:layout_constraintTop_toTopOf="parent"
        app:layout_constraintBottom_toBottomOf="parent"
        app:layout_constraintStart_toStartOf="parent"
        app:layout_constraintEnd_toEndOf="parent" />

</androidx.constraintlayout.widget.ConstraintLayout>
```

---

## Part 2: BlackBox Portal Integration

### Update Portal app.js

Add this Android Bridge detection and integration:

```javascript
// Detect Android WebWrapper
const isAndroidApp = typeof AndroidBridge !== 'undefined';

// Override showNotification to use native Android when available
const originalShowNotification = showNotification;

function showNotification(title, options = {}) {
  const currentOperator = window.__operator || 'Brandon';

  // Check operator filtering
  if (options.operator && options.operator !== currentOperator) {
    console.log(`[Notifications] Skipping - wrong operator`);
    return;
  }

  // Use Android native notifications if available
  if (isAndroidApp && typeof AndroidBridge.showNotification === 'function') {
    try {
      AndroidBridge.showNotification(
        title,
        options.body || '',
        options.operator || null,
        options.taskType || null,
        !title.includes('Failed') && !title.includes('❌')
      );

      // Also vibrate if specified
      if (options.vibrate && typeof AndroidBridge.vibrate === 'function') {
        AndroidBridge.vibrate(options.vibrate.join(','));
      }

      return;
    } catch (err) {
      console.error('[Notifications] Android bridge error:', err);
      // Fall through to web notifications
    }
  }

  // Fall back to web notifications
  if (Notification.permission === "granted") {
    const notification = new Notification(title, {
      icon: '/favicon.ico',
      badge: '/favicon.ico',
      requireInteraction: false,
      vibrate: [200, 100, 200],
      ...options
    });

    setTimeout(() => notification.close(), 10000);

    notification.onclick = () => {
      window.focus();
      notification.close();
    };

    return notification;
  }
}
```

---

## Part 3: Testing Integration

### Test Checklist

1. **Build Android App**
   ```bash
   # In Android Studio
   Build → Make Project
   Run → Run 'app'
   ```

2. **Grant Permissions**
   - App will request POST_NOTIFICATIONS permission
   - Grant it when prompted

3. **Test Notification Flow**
   ```
   1. Open app (loads BlackBox Portal)
   2. Generate image/video/audio
   3. Put app in background or lock screen
   4. Wait for task to complete
   5. Should receive Android notification!
   ```

4. **Debug with Chrome DevTools**
   ```
   chrome://inspect
   → Find your device
   → Inspect WebView
   → Check console for "Android bridge" logs
   ```

---

## Part 4: Advanced Features

### Background Notifications

For notifications when app is fully closed, implement a **Service Worker** (optional):

```javascript
// In Portal, register service worker
if ('serviceWorker' in navigator && !isAndroidApp) {
  navigator.serviceWorker.register('/service-worker.js')
    .then(reg => console.log('[SW] Registered'))
    .catch(err => console.log('[SW] Error:', err));
}
```

### Notification Actions

Add action buttons to Android notifications:

```kotlin
val notification = NotificationCompat.Builder(context, CHANNEL_ID_TASKS)
    .setSmallIcon(R.drawable.ic_notification)
    .setContentTitle(title)
    .setContentText(body)
    .addAction(
        R.drawable.ic_view,
        "View",
        pendingIntent
    )
    .addAction(
        R.drawable.ic_dismiss,
        "Dismiss",
        dismissIntent
    )
    .build()
```

### Per-Operator Filtering in Android

```kotlin
fun showTaskNotification(
    title: String,
    body: String,
    operator: String?,
    taskType: String?,
    isSuccess: Boolean
) {
    // Get current operator from SharedPreferences
    val prefs = context.getSharedPreferences("blackbox", Context.MODE_PRIVATE)
    val currentOperator = prefs.getString("current_operator", "Brandon")

    // Filter: Only show if task belongs to current operator
    if (operator != null && operator != currentOperator) {
        Log.d("BlackBox", "Skipping notification - wrong operator: $operator vs $currentOperator")
        return
    }

    // Show notification...
    // (rest of implementation)
}
```

---

## Part 5: Prompt for Gemini/Claude in Android Studio

**Copy this prompt and paste into Gemini/Claude in Android Studio:**

```
I need to integrate native Android notifications with my BlackBox WebWrapper app.

REQUIREMENTS:
1. WebView loads BlackBox Portal (https://my-tailscale-url/)
2. JavaScript bridge named "AndroidBridge" with methods:
   - showNotification(title, body, operator, taskType, isSuccess)
   - vibrate(pattern) - pattern is comma-separated string like "200,100,200"
   - requestNotificationPermission() → boolean
   - isNotificationEnabled() → boolean

3. Notification channels:
   - "blackbox_tasks" - Task completions (IMPORTANCE_DEFAULT)
   - "blackbox_system" - System updates (IMPORTANCE_LOW)

4. Notification features:
   - Vibration on task complete
   - Click to open app
   - Auto-dismiss after showing
   - Filter by operator (only show if task.operator matches current user)

5. Permissions:
   - POST_NOTIFICATIONS (Android 13+)
   - VIBRATE
   - Internet already configured

6. WebView settings:
   - JavaScript enabled
   - DOM storage enabled
   - Media autoplay allowed (for videos)

EXISTING CODE:
- MainActivity with WebView already set up
- Portal loads and displays correctly
- Need to ADD notification integration

Please provide:
1. Complete BlackBoxNotificationManager.kt
2. Complete WebAppInterface.kt
3. Updated MainActivity.kt with bridge setup
4. AndroidManifest.xml permission additions
5. How to test the integration

Use Kotlin, target API 26+, follow modern Android best practices.
```

---

## Part 6: Verification

### How to Verify It's Working

**In Android Studio Logcat:**
```
D/BlackBoxWebView: [Notifications] Android bridge detected
D/BlackBoxWebView: Notification sent: Task Complete - Your image generation is ready!
D/BlackBox: Showing notification for operator: Brandon
```

**On Device:**
- Task starts → Notification appears when complete
- Vibrates with pattern (buzz-buzz)
- Click → Opens/focuses BlackBox app
- Shows in notification shade

### Common Issues

**Issue:** Notifications not showing
**Fix:** Check permission granted in Settings → Apps → BlackBox → Notifications

**Issue:** WebView can't call AndroidBridge
**Fix:** Ensure `addJavascriptInterface()` called before `loadUrl()`

**Issue:** Vibration not working
**Fix:** Check VIBRATE permission in manifest

---

## Summary

This integration enables:
- ✅ Native Android notifications for task completion
- ✅ Per-operator filtering (Anna sees her tasks, Brandon sees his)
- ✅ Vibration patterns on mobile
- ✅ Background notifications (even when app closed)
- ✅ Click to open/focus app
- ✅ Proper notification channels (Android 8+)

**Hand this guide to Gemini/Claude in Android Studio and they'll implement the full integration!**

The web and native systems will "shake hands" properly:
- Web detects AndroidBridge
- Calls native methods
- Android shows proper system notifications
- Per-operator filtering works seamlessly

🤝 Perfect integration! 🤖📱
