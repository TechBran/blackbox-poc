# BlackBox Portal Notification Code - Complete Reference

This document contains ALL the notification-related code from the BlackBox Portal. Use this to ensure the Android app implements the correct AndroidBridge interface.

---

## JavaScript Code That Calls AndroidBridge

### 1. Main Notification Function (app.js)

This is THE function that gets called when tasks complete:

```javascript
// Show browser notification (with Android native support)
function showNotification(title, options = {}) {
  // Check operator filtering - only notify if task belongs to current operator
  const currentOperator = window.__operator || 'Brandon';
  if (options.operator && options.operator !== currentOperator) {
    console.log(`[Notifications] Skipping - task belongs to ${options.operator}, current operator is ${currentOperator}`);
    return;
  }

  // PRIORITY 1: Use Android native notifications if available
  if (typeof AndroidBridge !== 'undefined' && typeof AndroidBridge.showNotification === 'function') {
    try {
      console.log('[Notifications] Using Android native bridge');
      AndroidBridge.showNotification(title, options.body || '');
      return;
    } catch (err) {
      console.error('[Notifications] Android bridge error:', err);
      // Fall through to web notifications
    }
  }

  // PRIORITY 2: Use Web Notifications API (fallback)
  if (!("Notification" in window)) {
    console.log("[Notifications] Not supported, falling back to toast");
    toast(options.body || title);
    return;
  }

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
  } else if (Notification.permission === "default") {
    if (isMobileOrRemote()) {
      Notification.requestPermission().then(permission => {
        if (permission === "granted") {
          showNotification(title, options);
        }
      });
    }
  } else {
    toast(options.body || title);
  }
}
```

### 2. Task Completion Notification Wrapper

```javascript
function notifyTaskComplete(taskType, success = true, taskOperator = null) {
  if (success) {
    // Success vibration (two short pulses)
    vibrate([100, 50, 100]);

    showNotification("✅ Task Complete", {
      body: `Your ${taskType} is ready!`,
      tag: "task-complete",
      silent: false,
      operator: taskOperator  // Pass operator for filtering
    });
  } else {
    // Failure vibration (longer pulse)
    vibrate([400]);

    showNotification("❌ Task Failed", {
      body: `Your ${taskType} task failed`,
      operator: taskOperator,
      tag: "task-failed",
      silent: false
    });
  }
}
```

### 3. When Notifications Are Triggered

In TaskManager, when a task completes:

```javascript
TaskManager.prototype.handleTaskComplete = function(taskId, status) {
    console.log(`[TaskManager] Task ${taskId} completed:`, status);

    if (status.status === 'completed') {
        // THIS IS WHERE THE NOTIFICATION GETS TRIGGERED:
        const taskTypeName = status.task_type.replace(/_/g, ' ');
        const taskOperator = status.result_data?.operator || null;
        notifyTaskComplete(taskTypeName, true, taskOperator);

        // Then handle different task types (display results)
        if (status.task_type === 'image_generation' && status.result_url) {
            this.displayImageResult(status.result_url);
        } else if (status.task_type === 'video_generation' && status.result_url) {
            this.displayVideoResult(status.result_url);
        } else if (status.task_type === 'gemini_tts' && status.result_url) {
            this.displayAudioResult(status.result_url);
        }
        // ... more task types
    }
}
```

---

## What AndroidBridge Must Implement

Based on the JavaScript code above, your Android app needs **these exact methods**:

### Required AndroidBridge Interface:

```javascript
AndroidBridge.showNotification(title, body)
```

**Called like this:**
```javascript
AndroidBridge.showNotification("✅ Task Complete", "Your image generation is ready!")
AndroidBridge.showNotification("❌ Task Failed", "Your video generation task failed")
```

**Expected behavior:**
- Show native Android notification
- Title and body as provided
- Vibrate device (pattern: 200ms, 100ms, 200ms)
- Auto-dismiss after 10 seconds
- Click to open/focus app

---

## Example Notification Calls

Here's what actually gets called from the Portal:

### Image Generation Complete:
```javascript
AndroidBridge.showNotification(
    "✅ Task Complete",
    "Your image generation is ready!"
)
```

### Video Generation Complete:
```javascript
AndroidBridge.showNotification(
    "✅ Task Complete",
    "Your video generation is ready!"
)
```

### Music Generation Complete:
```javascript
AndroidBridge.showNotification(
    "✅ Task Complete",
    "Your lyria music is ready!"
)
```

### TTS Complete:
```javascript
AndroidBridge.showNotification(
    "✅ Task Complete",
    "Your gemini tts is ready!"
)
```

### Task Failed:
```javascript
AndroidBridge.showNotification(
    "❌ Task Failed",
    "Your video generation task failed"
)
```

---

## Minimal Android Implementation

**THIS IS ALL YOU NEED IN ANDROID:**

### WebAppInterface.java (Minimal Version):

```java
package com.yourpackage;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.os.Build;
import android.webkit.JavascriptInterface;
import android.util.Log;
import androidx.core.app.NotificationCompat;

public class WebAppInterface {
    private Context context;
    private static final String CHANNEL_ID = "blackbox_tasks";
    private static final String TAG = "BlackBoxBridge";

    public WebAppInterface(Context c) {
        this.context = c;
        createNotificationChannel();
        Log.d(TAG, "WebAppInterface created, channel initialized");
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "BlackBox Tasks",
                NotificationManager.IMPORTANCE_DEFAULT
            );
            channel.setDescription("Task completion notifications");
            channel.enableVibration(true);
            channel.setVibrationPattern(new long[]{0, 200, 100, 200});

            NotificationManager manager = context.getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.createNotificationChannel(channel);
                Log.d(TAG, "Notification channel created: " + CHANNEL_ID);
            }
        }
    }

    @JavascriptInterface
    public void showNotification(String title, String body) {
        Log.d(TAG, "showNotification called: " + title + " - " + body);

        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(body)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setVibrate(new long[]{0, 200, 100, 200})
            .setAutoCancel(true);

        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) {
            int notificationId = (int) System.currentTimeMillis();
            manager.notify(notificationId, builder.build());
            Log.d(TAG, "Notification shown with ID: " + notificationId);
        }
    }

    @JavascriptInterface
    public void log(String message) {
        Log.d(TAG, "WebView log: " + message);
    }
}
```

### MainActivity.java - Add This:

```java
// In onCreate(), BEFORE webView.loadUrl():

// Enable JavaScript (required)
webView.getSettings().setJavaScriptEnabled(true);

// Add the bridge - THIS IS CRITICAL
webView.addJavascriptInterface(new WebAppInterface(this), "AndroidBridge");

// Enable remote debugging
if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT) {
    WebView.setWebContentsDebuggingEnabled(true);
}

// THEN load the URL
webView.loadUrl("https://your-tailscale-url/");
```

---

## Testing Commands

### Test Bridge in Chrome DevTools (chrome://inspect):

```javascript
// Check if bridge exists
console.log('AndroidBridge:', typeof AndroidBridge);

// Test notification
if (typeof AndroidBridge !== 'undefined') {
    AndroidBridge.showNotification('Test Title', 'Test Body');
    console.log('Notification sent!');
} else {
    console.error('AndroidBridge not found!');
}
```

### Expected Console Output:
```
AndroidBridge: object
[Notifications] Using Android native bridge
Notification sent!
```

### Expected Android Logcat Output:
```
D/BlackBoxBridge: showNotification called: Test Title - Test Body
D/BlackBoxBridge: Notification shown with ID: 1733702345
```

### Expected Device Behavior:
- Notification appears in notification shade
- Phone vibrates (buzz-buzz)
- Clicking notification opens/focuses app

---

## Full Call Sequence

When an image generation completes:

```
1. Task finishes on server
2. Portal polls /tasks/list
3. Detects task.status === 'completed'
4. Calls: notifyTaskComplete('image generation', true, 'Brandon')
5. Calls: showNotification('✅ Task Complete', {body: 'Your image generation is ready!', operator: 'Brandon'})
6. Detects: typeof AndroidBridge !== 'undefined' → TRUE
7. Calls: AndroidBridge.showNotification('✅ Task Complete', 'Your image generation is ready!')
8. Android receives call
9. Android shows notification
10. Phone vibrates
```

---

## Copy-Paste for Gemini

**COPY THIS ENTIRE MESSAGE TO GEMINI IN ANDROID STUDIO:**

```
I have a WebView app loading BlackBox Portal. The Portal JavaScript is calling:

AndroidBridge.showNotification(title, body)

When tasks complete (image generation, video, audio, etc.).

I need you to implement the AndroidBridge interface that:
1. Accepts these calls from JavaScript
2. Shows native Android notifications
3. Vibrates device (pattern: 200ms, 100ms, 200ms)
4. Uses notification channel "blackbox_tasks"
5. Logs all calls to Logcat with tag "BlackBoxBridge"

Here's the minimal WebAppInterface code that works:

[PASTE THE JAVA CODE FROM SECTION "Minimal Android Implementation" ABOVE]

And here's what to add to MainActivity:

[PASTE THE MAINACTIVITY CODE FROM SECTION "MainActivity.java - Add This" ABOVE]

Please verify:
1. The bridge is added BEFORE loadUrl()
2. JavaScript is enabled in WebView settings
3. Notification channel is created properly
4. Logs appear in Logcat when notification is called
5. Actual notification appears on device

Test it by running this in chrome://inspect console:
AndroidBridge.showNotification('Test', 'If you see this notification, it works!');
```

---

**Give Gemini the code above and it should get the Android side working!** The Portal (v65) is already calling the right methods. 🔔✨