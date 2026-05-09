package com.workoutvibe.app.ui.components

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.workoutvibe.app.R
import com.workoutvibe.app.ui.theme.*

/**
 * Navigation destinations
 */
enum class NavDestination {
    TIMER,
    WORKOUT_TYPES,
    CALENDAR,
    STEP_TRACKER,
    PROGRESS
}

/**
 * Navigation drawer content
 */
@Composable
fun NavigationDrawerContent(
    currentDestination: NavDestination,
    onNavigate: (NavDestination) -> Unit,
    onClose: () -> Unit,
    modifier: Modifier = Modifier
) {
    ModalDrawerSheet(
        modifier = modifier.width(300.dp),
        drawerContainerColor = Surface
    ) {
        Column(
            modifier = Modifier.fillMaxHeight()
        ) {
            // Header
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(24.dp)
            ) {
                Text(
                    text = stringResource(R.string.app_name),
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.Bold,
                    color = Primary
                )
                Text(
                    text = stringResource(R.string.app_tagline),
                    style = MaterialTheme.typography.bodyMedium,
                    color = OnSurfaceVariant
                )
            }

            Divider(color = SurfaceVariant)

            Spacer(modifier = Modifier.height(8.dp))

            // Menu items
            NavigationDrawerItem(
                icon = Icons.Default.Timer,
                label = "Timer",
                selected = currentDestination == NavDestination.TIMER,
                onClick = {
                    onNavigate(NavDestination.TIMER)
                    onClose()
                }
            )

            NavigationDrawerItem(
                icon = Icons.Default.FitnessCenter,
                label = stringResource(R.string.menu_workout_types),
                selected = currentDestination == NavDestination.WORKOUT_TYPES,
                onClick = {
                    onNavigate(NavDestination.WORKOUT_TYPES)
                    onClose()
                }
            )

            NavigationDrawerItem(
                icon = Icons.Default.CalendarMonth,
                label = stringResource(R.string.menu_calendar),
                selected = currentDestination == NavDestination.CALENDAR,
                onClick = {
                    onNavigate(NavDestination.CALENDAR)
                    onClose()
                }
            )

            NavigationDrawerItem(
                icon = Icons.Default.DirectionsWalk,
                label = stringResource(R.string.menu_trackers),
                selected = currentDestination == NavDestination.STEP_TRACKER,
                onClick = {
                    onNavigate(NavDestination.STEP_TRACKER)
                    onClose()
                }
            )

            NavigationDrawerItem(
                icon = Icons.Default.CheckCircle,
                label = stringResource(R.string.menu_progress),
                selected = currentDestination == NavDestination.PROGRESS,
                onClick = {
                    onNavigate(NavDestination.PROGRESS)
                    onClose()
                }
            )

            Spacer(modifier = Modifier.weight(1f))

            // Footer
            Text(
                text = "v3.0",
                style = MaterialTheme.typography.labelSmall,
                color = OnSurfaceVariant,
                modifier = Modifier
                    .padding(24.dp)
                    .align(Alignment.CenterHorizontally)
            )
        }
    }
}

@Composable
private fun NavigationDrawerItem(
    icon: ImageVector,
    label: String,
    selected: Boolean,
    onClick: () -> Unit
) {
    val backgroundColor = if (selected) Primary.copy(alpha = 0.1f) else androidx.compose.ui.graphics.Color.Transparent
    val contentColor = if (selected) Primary else OnSurface

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 4.dp)
            .clip(RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
            .padding(12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = contentColor,
            modifier = Modifier.size(24.dp)
        )
        Text(
            text = label,
            style = MaterialTheme.typography.bodyLarge,
            color = contentColor
        )
    }
}
