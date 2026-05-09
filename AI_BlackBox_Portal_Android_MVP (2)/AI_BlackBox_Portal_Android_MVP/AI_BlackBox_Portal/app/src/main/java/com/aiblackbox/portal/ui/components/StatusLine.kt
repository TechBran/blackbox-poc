package com.aiblackbox.portal.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import com.aiblackbox.portal.ui.theme.BbxAccent
import com.aiblackbox.portal.ui.theme.BbxDim
import com.aiblackbox.portal.ui.theme.Neutral300
import com.aiblackbox.portal.ui.theme.Neutral500

@Composable
fun StatusLine(
    contextPercent: Float = 0f,     // 0-100
    cost: String = "",              // e.g. "$0.12"
    model: String = "",
    duration: String = "",          // e.g. "2m 34s"
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(top = 8.dp)
    ) {
        // Context bar
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "Context",
                style = MaterialTheme.typography.labelSmall,
                color = Neutral500
            )
            Spacer(Modifier.width(8.dp))
            LinearProgressIndicator(
                progress = { contextPercent / 100f },
                modifier = Modifier
                    .weight(1f)
                    .height(4.dp)
                    .clip(RoundedCornerShape(2.dp)),
                color = if (contextPercent > 80) BbxAccent else BbxDim,
                trackColor = Neutral300
            )
            Spacer(Modifier.width(8.dp))
            Text(
                text = "${contextPercent.toInt()}%",
                style = MaterialTheme.typography.labelSmall,
                color = if (contextPercent > 80) BbxAccent else Neutral500
            )
        }

        // Metrics row
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            if (model.isNotBlank()) {
                MetricChip("Model", model)
            }
            if (cost.isNotBlank()) {
                MetricChip("Cost", cost)
            }
            if (duration.isNotBlank()) {
                MetricChip("Time", duration)
            }
        }
    }
}

@Composable
private fun MetricChip(label: String, value: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            text = "$label: ",
            style = MaterialTheme.typography.labelSmall,
            color = Neutral500
        )
        Text(
            text = value,
            style = MaterialTheme.typography.labelSmall,
            color = BbxDim
        )
    }
}
