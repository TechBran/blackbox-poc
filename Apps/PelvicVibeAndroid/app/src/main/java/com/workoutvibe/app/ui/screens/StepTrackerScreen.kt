package com.workoutvibe.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.workoutvibe.app.R
import com.workoutvibe.app.data.WellnessData
import com.workoutvibe.app.ui.theme.*

/**
 * Step tracker screen
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StepTrackerScreen(
    wellnessData: WellnessData,
    onAddSteps: (Int) -> Unit,
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
                    text = stringResource(R.string.trackers_title),
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

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Spacer(modifier = Modifier.height(32.dp))

            // Steps Card
            StepTrackerCard(
                steps = wellnessData.steps,
                goal = WellnessData.STEPS_GOAL,
                onAddSteps = onAddSteps
            )
        }
    }
}

@Composable
private fun StepTrackerCard(
    steps: Int,
    goal: Int,
    onAddSteps: (Int) -> Unit
) {
    val progress = (steps.toFloat() / goal).coerceIn(0f, 1f)

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(24.dp),
        colors = CardDefaults.cardColors(containerColor = Surface)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Icon(
                imageVector = Icons.Default.DirectionsWalk,
                contentDescription = null,
                tint = Movement,
                modifier = Modifier.size(64.dp)
            )

            Spacer(modifier = Modifier.height(16.dp))

            Text(
                text = stringResource(R.string.tracker_movement),
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
                color = OnSurface
            )

            Spacer(modifier = Modifier.height(8.dp))

            Text(
                text = "$steps",
                style = MaterialTheme.typography.displayMedium,
                fontWeight = FontWeight.Bold,
                color = Movement
            )

            Text(
                text = "of ${goal.formatWithCommas()} steps",
                style = MaterialTheme.typography.bodyMedium,
                color = OnSurfaceVariant
            )

            Spacer(modifier = Modifier.height(16.dp))

            // Progress bar
            LinearProgressIndicator(
                progress = progress,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(8.dp)
                    .clip(RoundedCornerShape(4.dp)),
                color = Movement,
                trackColor = SurfaceVariant
            )

            Spacer(modifier = Modifier.height(24.dp))

            // Add buttons
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly
            ) {
                AddStepsButton(steps = 100, onAdd = onAddSteps)
                AddStepsButton(steps = 500, onAdd = onAddSteps)
                AddStepsButton(steps = 1000, onAdd = onAddSteps)
            }
        }
    }
}

@Composable
private fun AddStepsButton(
    steps: Int,
    onAdd: (Int) -> Unit
) {
    OutlinedButton(
        onClick = { onAdd(steps) },
        shape = RoundedCornerShape(12.dp),
        colors = ButtonDefaults.outlinedButtonColors(
            contentColor = Movement
        )
    ) {
        Text("+$steps")
    }
}

private fun Int.formatWithCommas(): String {
    return String.format("%,d", this)
}
