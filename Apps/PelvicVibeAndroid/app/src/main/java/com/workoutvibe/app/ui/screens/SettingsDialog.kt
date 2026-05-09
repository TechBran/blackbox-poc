package com.workoutvibe.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.workoutvibe.app.R
import com.workoutvibe.app.data.VoiceGender
import com.workoutvibe.app.data.WorkoutSettings
import com.workoutvibe.app.data.WorkoutType
import com.workoutvibe.app.ui.theme.*

/**
 * Settings dialog for workout configuration
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsDialog(
    settings: WorkoutSettings,
    onDismiss: () -> Unit,
    onUpdateWorkoutType: (WorkoutType) -> Unit,
    onUpdateWork: (Int) -> Unit,
    onUpdateRest: (Int) -> Unit,
    onUpdateReps: (Int) -> Unit,
    onUpdateSets: (Int) -> Unit,
    onToggleHaptic: () -> Unit,
    onToggleSound: () -> Unit,
    onUpdateVoice: (VoiceGender) -> Unit,
    onUpdateDailyGoal: (Int) -> Unit
) {
    var showWorkoutTypeMenu by remember { mutableStateOf(false) }
    var showVoiceMenu by remember { mutableStateOf(false) }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(
            dismissOnBackPress = true,
            dismissOnClickOutside = true
        )
    ) {
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(max = 600.dp),
            shape = RoundedCornerShape(24.dp),
            color = Surface,
            tonalElevation = 8.dp
        ) {
            Column(
                modifier = Modifier
                    .padding(24.dp)
                    .verticalScroll(rememberScrollState())
            ) {
                // Header
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = stringResource(R.string.settings_title),
                        style = MaterialTheme.typography.headlineSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = OnBackground
                    )
                    IconButton(onClick = onDismiss) {
                        Icon(
                            imageVector = Icons.Default.Close,
                            contentDescription = "Close",
                            tint = OnSurfaceVariant
                        )
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))

                // Workout Type Dropdown
                ExposedDropdownMenuBox(
                    expanded = showWorkoutTypeMenu,
                    onExpandedChange = { showWorkoutTypeMenu = it }
                ) {
                    OutlinedTextField(
                        value = settings.workoutType.displayName,
                        onValueChange = {},
                        readOnly = true,
                        label = { Text(stringResource(R.string.setting_workout_type)) },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = showWorkoutTypeMenu) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .menuAnchor(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = Primary,
                            unfocusedBorderColor = SurfaceVariant,
                            focusedTextColor = OnSurface,
                            unfocusedTextColor = OnSurface
                        )
                    )
                    ExposedDropdownMenu(
                        expanded = showWorkoutTypeMenu,
                        onDismissRequest = { showWorkoutTypeMenu = false }
                    ) {
                        WorkoutType.entries.forEach { type ->
                            DropdownMenuItem(
                                text = { Text(type.displayName) },
                                onClick = {
                                    onUpdateWorkoutType(type)
                                    showWorkoutTypeMenu = false
                                }
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))

                // Work Duration
                SettingRow(
                    label = stringResource(R.string.setting_work_duration),
                    value = "${settings.workDuration}s",
                    onDecrease = { onUpdateWork(-WorkoutSettings.DURATION_STEP) },
                    onIncrease = { onUpdateWork(WorkoutSettings.DURATION_STEP) }
                )

                Spacer(modifier = Modifier.height(12.dp))

                // Rest Duration
                SettingRow(
                    label = stringResource(R.string.setting_rest_duration),
                    value = "${settings.restDuration}s",
                    onDecrease = { onUpdateRest(-WorkoutSettings.DURATION_STEP) },
                    onIncrease = { onUpdateRest(WorkoutSettings.DURATION_STEP) }
                )

                Spacer(modifier = Modifier.height(12.dp))

                // Reps
                SettingRow(
                    label = stringResource(R.string.setting_reps),
                    value = settings.reps.toString(),
                    onDecrease = { onUpdateReps(-1) },
                    onIncrease = { onUpdateReps(1) }
                )

                Spacer(modifier = Modifier.height(12.dp))

                // Sets
                SettingRow(
                    label = stringResource(R.string.setting_sets),
                    value = settings.sets.toString(),
                    onDecrease = { onUpdateSets(-1) },
                    onIncrease = { onUpdateSets(1) }
                )

                Spacer(modifier = Modifier.height(12.dp))

                // Daily Goal
                SettingRow(
                    label = stringResource(R.string.setting_daily_goal),
                    value = settings.dailyWorkoutGoal.toString(),
                    onDecrease = { onUpdateDailyGoal(-1) },
                    onIncrease = { onUpdateDailyGoal(1) }
                )

                Spacer(modifier = Modifier.height(16.dp))

                // Voice Dropdown
                ExposedDropdownMenuBox(
                    expanded = showVoiceMenu,
                    onExpandedChange = { showVoiceMenu = it }
                ) {
                    OutlinedTextField(
                        value = settings.voiceGender.displayName,
                        onValueChange = {},
                        readOnly = true,
                        label = { Text(stringResource(R.string.setting_voice)) },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = showVoiceMenu) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .menuAnchor(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = Primary,
                            unfocusedBorderColor = SurfaceVariant,
                            focusedTextColor = OnSurface,
                            unfocusedTextColor = OnSurface
                        )
                    )
                    ExposedDropdownMenu(
                        expanded = showVoiceMenu,
                        onDismissRequest = { showVoiceMenu = false }
                    ) {
                        VoiceGender.entries.forEach { voice ->
                            DropdownMenuItem(
                                text = { Text(voice.displayName) },
                                onClick = {
                                    onUpdateVoice(voice)
                                    showVoiceMenu = false
                                }
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))
                Divider(color = SurfaceVariant)
                Spacer(modifier = Modifier.height(16.dp))

                // Haptic Toggle
                ToggleRow(
                    label = stringResource(R.string.setting_haptic),
                    isEnabled = settings.hapticEnabled,
                    onToggle = onToggleHaptic
                )

                Spacer(modifier = Modifier.height(12.dp))

                // Sound Toggle
                ToggleRow(
                    label = stringResource(R.string.setting_sound),
                    isEnabled = settings.soundEnabled,
                    onToggle = onToggleSound
                )
            }
        }
    }
}

@Composable
private fun SettingRow(
    label: String,
    value: String,
    onDecrease: () -> Unit,
    onIncrease: () -> Unit
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodyLarge,
            color = OnSurface
        )

        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            IconButton(
                onClick = onDecrease,
                modifier = Modifier
                    .size(36.dp)
                    .clip(CircleShape)
                    .background(SurfaceVariant)
            ) {
                Icon(
                    imageVector = Icons.Default.Remove,
                    contentDescription = "Decrease",
                    tint = OnSurface,
                    modifier = Modifier.size(18.dp)
                )
            }

            Text(
                text = value,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                color = Primary,
                modifier = Modifier.widthIn(min = 40.dp)
            )

            IconButton(
                onClick = onIncrease,
                modifier = Modifier
                    .size(36.dp)
                    .clip(CircleShape)
                    .background(SurfaceVariant)
            ) {
                Icon(
                    imageVector = Icons.Default.Add,
                    contentDescription = "Increase",
                    tint = OnSurface,
                    modifier = Modifier.size(18.dp)
                )
            }
        }
    }
}

@Composable
private fun ToggleRow(
    label: String,
    isEnabled: Boolean,
    onToggle: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onToggle() },
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodyLarge,
            color = OnSurface
        )

        Switch(
            checked = isEnabled,
            onCheckedChange = { onToggle() },
            colors = SwitchDefaults.colors(
                checkedThumbColor = Primary,
                checkedTrackColor = Primary.copy(alpha = 0.5f),
                uncheckedThumbColor = OnSurfaceVariant,
                uncheckedTrackColor = SurfaceVariant
            )
        )
    }
}
