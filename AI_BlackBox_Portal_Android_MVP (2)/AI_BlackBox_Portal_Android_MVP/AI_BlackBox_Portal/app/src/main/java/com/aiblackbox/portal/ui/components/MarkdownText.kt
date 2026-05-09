package com.aiblackbox.portal.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mikepenz.markdown.m3.Markdown
import com.mikepenz.markdown.m3.markdownColor
import com.mikepenz.markdown.m3.markdownTypography
import com.aiblackbox.portal.ui.theme.BbxBlack
import com.aiblackbox.portal.ui.theme.BbxDim
import com.aiblackbox.portal.ui.theme.BbxWhite
import com.aiblackbox.portal.ui.theme.HighlightHeader
import com.aiblackbox.portal.ui.theme.HighlightKeyword
import com.aiblackbox.portal.ui.theme.HighlightLink
import com.aiblackbox.portal.ui.theme.Neutral100
import com.aiblackbox.portal.ui.theme.Neutral300

// =============================================================================
// MarkdownText — splits content into code blocks + regular markdown.
// Code blocks are rendered manually (full height, no truncation).
// Regular markdown goes through the mikepenz library.
// =============================================================================

private sealed class ContentSegment {
    data class Markdown(val text: String) : ContentSegment()
    data class CodeBlock(val code: String, val language: String) : ContentSegment()
}

/** Split markdown into alternating segments of prose and fenced code blocks. */
private fun splitContent(content: String): List<ContentSegment> {
    val segments = mutableListOf<ContentSegment>()
    val lines = content.split('\n')
    val buffer = StringBuilder()
    var inCodeBlock = false
    var codeLang = ""
    val codeBuffer = StringBuilder()

    for (line in lines) {
        if (!inCodeBlock && line.trimStart().startsWith("```")) {
            // Flush prose buffer
            if (buffer.isNotBlank()) {
                segments.add(ContentSegment.Markdown(buffer.toString().trim()))
            }
            buffer.clear()
            inCodeBlock = true
            codeLang = line.trimStart().removePrefix("```").trim()
            codeBuffer.clear()
        } else if (inCodeBlock && line.trimStart().startsWith("```")) {
            // End code block
            segments.add(ContentSegment.CodeBlock(codeBuffer.toString().trimEnd(), codeLang))
            inCodeBlock = false
            codeLang = ""
        } else if (inCodeBlock) {
            if (codeBuffer.isNotEmpty()) codeBuffer.append('\n')
            codeBuffer.append(line)
        } else {
            if (buffer.isNotEmpty()) buffer.append('\n')
            buffer.append(line)
        }
    }

    // Flush remaining
    if (inCodeBlock && codeBuffer.isNotEmpty()) {
        // Unclosed code block — render as code anyway
        segments.add(ContentSegment.CodeBlock(codeBuffer.toString().trimEnd(), codeLang))
    }
    if (buffer.isNotBlank()) {
        segments.add(ContentSegment.Markdown(buffer.toString().trim()))
    }

    return segments
}

@Composable
fun MarkdownText(
    content: String,
    modifier: Modifier = Modifier
) {
    val colors = markdownColor(
        text = BbxWhite,
        codeText = HighlightKeyword,
        codeBackground = Neutral100,
        inlineCodeText = HighlightKeyword,
        inlineCodeBackground = BbxBlack,
        linkText = HighlightLink,
        dividerColor = Neutral300
    )

    val typography = markdownTypography(
        h1 = MaterialTheme.typography.headlineLarge.copy(
            color = HighlightHeader, fontWeight = FontWeight.Bold, lineHeight = 28.sp
        ),
        h2 = MaterialTheme.typography.headlineMedium.copy(
            color = HighlightHeader, fontWeight = FontWeight.Bold, lineHeight = 24.sp
        ),
        h3 = MaterialTheme.typography.titleLarge.copy(
            color = HighlightHeader, fontWeight = FontWeight.SemiBold, lineHeight = 22.sp
        ),
        h4 = MaterialTheme.typography.titleMedium.copy(
            color = HighlightHeader, fontWeight = FontWeight.SemiBold
        ),
        h5 = MaterialTheme.typography.bodyLarge.copy(
            color = HighlightHeader, fontWeight = FontWeight.SemiBold
        ),
        h6 = MaterialTheme.typography.bodyMedium.copy(
            color = HighlightHeader, fontWeight = FontWeight.Medium
        ),
        text = MaterialTheme.typography.bodyLarge.copy(
            color = BbxWhite, lineHeight = 24.sp
        ),
        paragraph = MaterialTheme.typography.bodyLarge.copy(
            color = BbxWhite, lineHeight = 24.sp
        ),
        code = MaterialTheme.typography.bodyMedium.copy(
            fontFamily = FontFamily.Monospace, color = HighlightKeyword,
            fontSize = 13.sp, lineHeight = 20.sp
        ),
        quote = MaterialTheme.typography.bodyMedium.copy(
            fontStyle = FontStyle.Italic, color = BbxDim, lineHeight = 22.sp
        ),
        ordered = MaterialTheme.typography.bodyLarge.copy(color = BbxWhite, lineHeight = 24.sp),
        bullet = MaterialTheme.typography.bodyLarge.copy(color = BbxWhite, lineHeight = 24.sp),
        list = MaterialTheme.typography.bodyLarge.copy(color = BbxWhite, lineHeight = 24.sp)
    )

    val segments = splitContent(content)

    Column(modifier = modifier) {
        segments.forEach { segment ->
            when (segment) {
                is ContentSegment.CodeBlock -> {
                    // Full-height code block with copy button
                    val clipboardManager = androidx.compose.ui.platform.LocalClipboardManager.current
                    var copied by remember { mutableStateOf(false) }
                    LaunchedEffect(copied) {
                        if (copied) { kotlinx.coroutines.delay(1500); copied = false }
                    }

                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(8.dp))
                            .background(Neutral100)
                    ) {
                        // Header: language label + copy button
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .background(Neutral300.copy(alpha = 0.3f))
                                .padding(horizontal = 12.dp, vertical = 6.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = segment.language.ifBlank { "code" },
                                fontSize = 11.sp,
                                color = BbxDim,
                                fontFamily = FontFamily.Monospace
                            )
                            Text(
                                text = if (copied) "\u2713 Copied" else "\u2398 Copy",
                                fontSize = 11.sp,
                                color = if (copied) com.aiblackbox.portal.ui.theme.SolidGreen else HighlightKeyword,
                                fontWeight = FontWeight.Medium,
                                modifier = Modifier
                                    .clip(RoundedCornerShape(4.dp))
                                    .clickable {
                                        clipboardManager.setText(androidx.compose.ui.text.AnnotatedString(segment.code))
                                        copied = true
                                    }
                                    .padding(horizontal = 8.dp, vertical = 2.dp)
                            )
                        }
                        // Code content — no horizontalScroll (breaks vertical measurement in LazyColumn)
                        // Long lines wrap naturally on mobile; user can copy full text via button
                        Text(
                            text = segment.code,
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(12.dp),
                            fontFamily = FontFamily.Monospace,
                            fontSize = 13.sp,
                            lineHeight = 20.sp,
                            color = HighlightKeyword,
                            softWrap = true
                        )
                    }
                }
                is ContentSegment.Markdown -> {
                    Markdown(
                        content = segment.text,
                        colors = colors,
                        typography = typography
                    )
                }
            }
        }
    }
}
