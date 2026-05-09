package com.aiblackbox.portal.overlay

import android.graphics.Color as AndroidColor
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.util.Log
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalContext

/**
 * Transparent floating Activity that hosts the XR overlay.
 *
 * In Android XR Home Space mode, each Activity window becomes a floating panel.
 * By using a transparent theme and dynamic sizing, this Activity appears as:
 *   - Collapsed: a small ~180dp holographic orb floating in passthrough
 *   - Expanded: a larger control panel with voice session controls
 *
 * Communication with OverlayService flows through [OverlayBridge]:
 *   - Service → Activity: StateFlow (UI updates)
 *   - Activity → Service: CommandListener (user actions)
 */
class XrOverlayActivity : ComponentActivity() {

    companion object {
        private const val TAG = "XrOverlayActivity"
        // Panel dimensions in dp
        private const val BUBBLE_SIZE_DP = 180  // slightly larger than 160dp orb for glow breathing room
        private const val PANEL_WIDTH_DP = 440
        private const val PANEL_HEIGHT_DP = 480
    }

    private var density: Float = 1f

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.d(TAG, "XrOverlayActivity.onCreate — taskId=$taskId, isFinishing=$isFinishing")

        density = resources.displayMetrics.density

        // Make window background fully transparent so the orb floats
        window.setBackgroundDrawable(ColorDrawable(AndroidColor.TRANSPARENT))
        window.addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
        )

        // Start with bubble-sized window
        resizeWindow(expanded = false)

        setContent {
            val state by OverlayBridge.state.collectAsState()

            // Log every recomposition with key state values
            SideEffect {
                Log.d(TAG, "Compose — expanded=${state.isExpanded}, connected=${state.isConnected}, status=${state.statusText}")
            }

            // Finish the activity when the service signals shutdown
            if (state.shouldFinish) {
                val activity = LocalContext.current as? ComponentActivity
                LaunchedEffect(Unit) {
                    Log.d(TAG, "shouldFinish — finishing XrOverlayActivity")
                    activity?.finish()
                }
                return@setContent
            }

            // Dynamically resize the window when expanded state changes
            val context = LocalContext.current
            LaunchedEffect(state.isExpanded) {
                val act = context as? XrOverlayActivity ?: return@LaunchedEffect
                act.resizeWindow(state.isExpanded)
                Log.d(TAG, "Window resized — expanded=${state.isExpanded}")
            }

            // Render directly — Activity window IS the floating panel in XR Home Space
            Box(contentAlignment = Alignment.Center) {
                if (!state.isExpanded) {
                    XrBubbleContent(state = state)
                } else {
                    XrExpandedPanel(state = state)
                }
            }
        }
    }

    /** Resize the Activity window to match bubble vs expanded panel. */
    private fun resizeWindow(expanded: Boolean) {
        if (expanded) {
            window.setLayout(
                (PANEL_WIDTH_DP * density).toInt(),
                (PANEL_HEIGHT_DP * density).toInt()
            )
        } else {
            val size = (BUBBLE_SIZE_DP * density).toInt()
            window.setLayout(size, size)
        }
    }

    override fun onResume() {
        super.onResume()
        Log.d(TAG, "XrOverlayActivity.onResume")
    }

    override fun onPause() {
        super.onPause()
        Log.d(TAG, "XrOverlayActivity.onPause")
    }

    override fun onDestroy() {
        super.onDestroy()
        Log.d(TAG, "XrOverlayActivity.onDestroy — isFinishing=$isFinishing")
    }
}
