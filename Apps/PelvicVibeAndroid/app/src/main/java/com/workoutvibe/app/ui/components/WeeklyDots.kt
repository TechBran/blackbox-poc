package com.workoutvibe.app.ui.components

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.workoutvibe.app.ui.theme.*
import java.time.DayOfWeek
import java.time.LocalDate

/**
 * Weekly progress dots shown at the bottom of the main screen
 */
@Composable
fun WeeklyDots(
    completedDays: List<Boolean>,
    modifier: Modifier = Modifier
) {
    val dayLabels = listOf("S", "M", "T", "W", "T", "F", "S")
    val today = LocalDate.now().dayOfWeek
    val todayIndex = if (today == DayOfWeek.SUNDAY) 0 else today.value

    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp),
        horizontalArrangement = Arrangement.SpaceEvenly
    ) {
        dayLabels.forEachIndexed { index, label ->
            val isCompleted = completedDays.getOrNull(index) == true
            val isToday = index == todayIndex

            DayDot(
                label = label,
                isCompleted = isCompleted,
                isToday = isToday
            )
        }
    }
}

@Composable
private fun DayDot(
    label: String,
    isCompleted: Boolean,
    isToday: Boolean
) {
    val backgroundColor by animateColorAsState(
        targetValue = when {
            isCompleted -> Primary
            else -> Color.Transparent
        },
        label = "dotBg"
    )

    val borderColor by animateColorAsState(
        targetValue = when {
            isToday -> Primary
            isCompleted -> Primary
            else -> SurfaceVariant
        },
        label = "dotBorder"
    )

    val textColor by animateColorAsState(
        targetValue = when {
            isCompleted -> OnPrimary
            isToday -> Primary
            else -> OnSurfaceVariant
        },
        label = "dotText"
    )

    Box(
        modifier = Modifier
            .size(36.dp)
            .clip(CircleShape)
            .background(backgroundColor)
            .border(
                width = if (isToday && !isCompleted) 2.dp else 1.dp,
                color = borderColor,
                shape = CircleShape
            ),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelMedium.copy(
                fontWeight = if (isToday) FontWeight.Bold else FontWeight.Medium,
                fontSize = 12.sp
            ),
            color = textColor
        )
    }
}
