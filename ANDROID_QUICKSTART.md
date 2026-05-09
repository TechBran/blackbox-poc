# BlackBox Android Notifications - 5 Minute Integration

Quick-start guide to enable native Android notifications in your existing WebWrapper app.

## Step 1: Add Permissions (AndroidManifest.xml)

Add these inside `<manifest>` tag:

```xml
<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
<uses-permission android:name="android.permission.VIBRATE" />
```

## Step 2: Create Simple JavaScript Bridge

Add this class to your Android project:

**File:** `WebAppInterface.java` (or .kt for Kotlin)

### Java Version:
```java
package com.yourpackage;

import android.content.Context;
import android.webkit.JavascriptInterface;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.os.Build;
import androidx.core.app.NotificationCompat;

public class WebAppInterface {
    Context mContext;

    WebAppInterface(Context c) {
        mContext = c;
        createNotificationChannel();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                "blackbox_tasks",
                "BlackBox Tasks",
                NotificationManager.IMPORTANCE_DEFAULT
            );
            channel.setDescription("Task completion notifications");
            channel.enableVibration(true);
            channel.setVibrationPattern(new long[]{0, 200, 100, 200});

            NotificationManager manager = mContext.getSystemService(NotificationManager.class);
            manager.createNotificationChannel(channel);
        }
    }

    @JavascriptInterface
    public void showNotification(String title, String body) {
        NotificationCompat.Builder builder = new NotificationCompat.Builder(mContext, "blackbox_tasks")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(body)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setVibrate(new long[]{0, 200, 100, 200})
            .setAutoCancel(true);

        NotificationManager manager = (NotificationManager) mContext.getSystemService(Context.NOTIFICATION_SERVICE);
        manager.notify((int) System.currentTimeMillis(), builder.build());
    }

    @JavascriptInterface
    public boolean isAndroidApp() {
        return true;
    }
}
```

### Kotlin Version:
```kotlin
package com.yourpackage

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.webkit.JavascriptInterface
import androidx.core.app.NotificationCompat

class WebAppInterface(private val context: Context) {

    init {
        createNotificationChannel()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                "blackbox_tasks",
                "BlackBox Tasks",
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = "Task completion notifications"
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 200, 100, 200)
            }

            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    @JavascriptInterface
    fun showNotification(title: String, body: String) {
        val builder = NotificationCompat.Builder(context, "blackbox_tasks")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(body)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setVibrate(longArrayOf(0, 200, 100, 200))
            .setAutoCancel(true)

        val manager = context.getSystemService(NotificationManager::class.java)
        manager.notify(System.currentTimeMillis().toInt(), builder.build())
    }

    @JavascriptInterface
    fun isAndroidApp(): Boolean = true
}
```

## Step 3: Add Bridge to WebView (MainActivity)

In your MainActivity, **BEFORE** `webView.loadUrl()`:

### Java:
```java
webView.addJavascriptInterface(new WebAppInterface(this), "AndroidBridge");
```

### Kotlin:
```kotlin
webView.addJavascriptInterface(WebAppInterface(this), "AndroidBridge")
```

## Step 4: Request Permission (Android 13+)

Add to `onCreate()` in MainActivity:

### Java:
```java
if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
    if (checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
        requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, 100);
    }
}
```

### Kotlin:
```kotlin
if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
    if (checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
        requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 100)
    }
}
```

## Step 5: Test!

1. **Build and run** the Android app
2. **Grant notification permission** when prompted
3. **Generate an image** in the Portal
4. **Lock screen or switch apps**
5. **When task completes:** Should get Android notification!

---

## That's It!

Just 4 steps:
1. Add permissions to manifest
2. Create WebAppInterface class
3. Add bridge to WebView
4. Request permission

**Your phone will now receive native Android notifications when tasks complete!** 📱🔔

---

## Debugging

**Test if bridge is connected:**

Open Portal in Android app, press F12 in Chrome DevTools (chrome://inspect), run:
```javascript
console.log('AndroidBridge exists:', typeof AndroidBridge !== 'undefined');
if (typeof AndroidBridge !== 'undefined') {
    AndroidBridge.showNotification('Test', 'Bridge is working!');
}
```

**Should see:**
- Console: "AndroidBridge exists: true"
- Phone: Notification pops up!

If not working:
- Check `addJavascriptInterface()` is BEFORE `loadUrl()`
- Check JavaScript is enabled in WebView settings
- Check notification permission granted in Android settings
