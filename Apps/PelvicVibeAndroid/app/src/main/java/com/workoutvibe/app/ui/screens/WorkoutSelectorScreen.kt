package com.workoutvibe.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.workoutvibe.app.R
import com.workoutvibe.app.data.WorkoutType
import com.workoutvibe.app.ui.theme.*

/**
 * Screen for selecting workout type
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WorkoutSelectorScreen(
    currentWorkoutType: WorkoutType,
    onWorkoutTypeSelected: (WorkoutType) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .background(Background)
    ) {
        TopAppBar(
            title = {
                Text(
                    text = stringResource(R.string.menu_workout_types),
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.SemiBold
                )
            },
            navigationIcon = {
                IconButton(onClick = onBack) {
                    Icon(
                        imageVector = Icons.Default.ArrowBack,
                        contentDescription = "Back"
                    )
                }
            },
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = Background,
                titleContentColor = OnBackground,
                navigationIconContentColor = OnBackground
            )
        )

        LazyVerticalGrid(
            columns = GridCells.Fixed(2),
            contentPadding = PaddingValues(16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.fillMaxSize()
        ) {
            items(WorkoutType.entries.toList()) { type ->
                WorkoutTypeCard(
                    workoutType = type,
                    isSelected = type == currentWorkoutType,
                    onClick = {
                        onWorkoutTypeSelected(type)
                        onBack()
                    }
                )
            }
        }
    }
}

@Composable
private fun WorkoutTypeCard(
    workoutType: WorkoutType,
    isSelected: Boolean,
    onClick: () -> Unit
) {
    val icon = when (workoutType) {
        WorkoutType.PUSHUPS -> Icons.Default.FitnessCenter
        WorkoutType.SITUPS -> Icons.Default.SportsGymnastics
        WorkoutType.SQUATS -> Icons.Default.AccessibilityNew
        WorkoutType.PLANKS -> Icons.Default.HorizontalRule
        WorkoutType.BURPEES -> Icons.Default.DirectionsRun
        WorkoutType.RUNNING_INTERVALS -> Icons.Default.DirectionsRun
        WorkoutType.SPRINT_INTERVALS -> Icons.Default.Speed
        WorkoutType.CUSTOM -> Icons.Default.Tune
    }

    val borderColor = if (isSelected) Primary else SurfaceVariant
    val backgroundColor = if (isSelected) Primary.copy(alpha = 0.1f) else Surface

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(1f)
            .clip(RoundedCornerShape(16.dp))
            .border(
                width = if (isSelected) 2.dp else 1.dp,
                color = borderColor,
                shape = RoundedCornerShape(16.dp)
            )
            .clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = backgroundColor)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = if (isSelected) Primary else OnSurface,
                modifier = Modifier.size(48.dp)
            )

            Spacer(modifier = Modifier.height(12.dp))

            Text(
                text = workoutType.displayName,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Medium,
                color = if (isSelected) Primary else OnSurface,
                textAlign = TextAlign.Center
            )

            Spacer(modifier = Modifier.height(4.dp))

            Text(
                text = "${workoutType.defaultWork}s / ${workoutType.defaultRest}s",
                style = MaterialTheme.typography.bodySmall,
                color = OnSurfaceVariant,
                textAlign = TextAlign.Center
            )
        }
    }
}
