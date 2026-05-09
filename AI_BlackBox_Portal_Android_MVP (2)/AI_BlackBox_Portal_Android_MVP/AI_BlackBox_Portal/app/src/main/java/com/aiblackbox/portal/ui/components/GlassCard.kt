package com.aiblackbox.portal.ui.components

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.aiblackbox.portal.ui.theme.BlackBoxShapes
import com.aiblackbox.portal.ui.theme.glassSurface

@Composable
fun GlassCard(
    modifier: Modifier = Modifier,
    shape: Shape = BlackBoxShapes.medium,
    bg: Color = Color.Unspecified,
    hovered: Boolean = false,
    elevation: Dp = 0.dp,
    content: @Composable BoxScope.() -> Unit
) {
    Box(
        modifier = modifier.glassSurface(
            shape = shape,
            bg = bg,
            hovered = hovered,
            elevation = elevation,
        ),
        content = content
    )
}
