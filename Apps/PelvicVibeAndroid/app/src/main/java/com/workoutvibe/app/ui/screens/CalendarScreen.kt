package com.workoutvibe.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.workoutvibe.app.R
import com.workoutvibe.app.data.WorkoutHistory
import com.workoutvibe.app.ui.theme.*
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.*

/**
 * Calendar screen showing workout history
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CalendarScreen(
    workoutHistory: WorkoutHistory,
    dailyGoal: Int,
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    var currentMonth by remember { mutableStateOf(YearMonth.now()) }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(Background)
    ) {
        TopAppBar(
            title = {
                Text(
                    text = stringResource(R.string.calendar_title),
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

        // Month navigation
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            IconButton(onClick = { currentMonth = currentMonth.minusMonths(1) }) {
                Icon(
                    imageVector = Icons.Default.ChevronLeft,
                    contentDescription = "Previous month",
                    tint = OnSurface
                )
            }

            Text(
                text = "${currentMonth.month.getDisplayName(TextStyle.FULL, Locale.getDefault())} ${currentMonth.year}",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
                color = OnSurface
            )

            IconButton(onClick = { currentMonth = currentMonth.plusMonths(1) }) {
                Icon(
                    imageVector = Icons.Default.ChevronRight,
                    contentDescription = "Next month",
                    tint = OnSurface
                )
            }
        }

        // Day headers
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceEvenly
        ) {
            listOf("S", "M", "T", "W", "T", "F", "S").forEach { day ->
                Text(
                    text = day,
                    style = MaterialTheme.typography.labelMedium,
                    color = OnSurfaceVariant,
                    modifier = Modifier.weight(1f),
                    textAlign = TextAlign.Center
                )
            }
        }

        // Calendar grid
        CalendarGrid(
            yearMonth = currentMonth,
            workoutHistory = workoutHistory,
            dailyGoal = dailyGoal
        )

        // Legend
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .size(12.dp)
                    .clip(CircleShape)
                    .background(Primary)
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = stringResource(R.string.calendar_goal_met),
                style = MaterialTheme.typography.bodySmall,
                color = OnSurfaceVariant
            )
        }
    }
}

@Composable
private fun CalendarGrid(
    yearMonth: YearMonth,
    workoutHistory: WorkoutHistory,
    dailyGoal: Int
) {
    val firstDayOfMonth = yearMonth.atDay(1)
    val lastDayOfMonth = yearMonth.atEndOfMonth()
    val firstDayOfWeek = firstDayOfMonth.dayOfWeek.value % 7

    val days = mutableListOf<LocalDate?>()
    repeat(firstDayOfWeek) { days.add(null) }
    for (day in 1..lastDayOfMonth.dayOfMonth) {
        days.add(yearMonth.atDay(day))
    }

    val today = LocalDate.now()
    val formatter = DateTimeFormatter.ISO_LOCAL_DATE

    LazyVerticalGrid(
        columns = GridCells.Fixed(7),
        modifier = Modifier.padding(horizontal = 16.dp),
        contentPadding = PaddingValues(vertical = 8.dp)
    ) {
        items(days) { date ->
            if (date == null) {
                Box(modifier = Modifier.size(48.dp))
            } else {
                val dateStr = date.format(formatter)
                val workoutCount = workoutHistory.getWorkoutCountForDate(dateStr)
                val isGoalMet = workoutCount >= dailyGoal
                val isToday = date == today
                val isFuture = date.isAfter(today)

                CalendarDay(
                    day = date.dayOfMonth,
                    isToday = isToday,
                    isGoalMet = isGoalMet && !isFuture,
                    workoutCount = if (isFuture) 0 else workoutCount
                )
            }
        }
    }
}

@Composable
private fun CalendarDay(
    day: Int,
    isToday: Boolean,
    isGoalMet: Boolean,
    workoutCount: Int
) {
    val backgroundColor = when {
        isGoalMet -> Primary
        else -> Color.Transparent
    }

    val borderColor = when {
        isToday -> Primary
        else -> Color.Transparent
    }

    val textColor = when {
        isGoalMet -> OnPrimary
        isToday -> Primary
        else -> OnSurface
    }

    Box(
        modifier = Modifier
            .size(48.dp)
            .padding(4.dp)
            .clip(CircleShape)
            .background(backgroundColor)
            .then(
                if (isToday && !isGoalMet) {
                    Modifier.border(2.dp, borderColor, CircleShape)
                } else {
                    Modifier
                }
            ),
        contentAlignment = Alignment.Center
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                text = day.toString(),
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = if (isToday || isGoalMet) FontWeight.Bold else FontWeight.Normal,
                color = textColor
            )
            if (workoutCount > 0 && !isGoalMet) {
                Text(
                    text = "$workoutCount",
                    style = MaterialTheme.typography.labelSmall,
                    color = OnSurfaceVariant
                )
            }
        }
    }
}
