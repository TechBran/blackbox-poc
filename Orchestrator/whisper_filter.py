#!/usr/bin/env python3
"""
whisper_filter.py - Whisper Hallucination Filter

Filters known Whisper hallucination phrases that occur when given near-silence.
Used by all real-time voice routes and the phone bridge.
"""

import re

# Known Whisper hallucination phrases when given near-silence
_WHISPER_HALLUCINATIONS = {
    "thank you", "thank you.", "thank you for watching",
    "thank you for watching.", "thanks for watching",
    "thanks for watching.", "thanks for listening",
    "subtitles by", "subtitles by the amara.org community",
    "subscribe", "subscribe.", "like and subscribe",
    "please subscribe", "you", "you.", "bye", "bye.",
    "the end", "the end.", "so", "so.",
    "uh", "um", "hmm", "hm",
}

# Regex for CJK-only hallucinations (Chinese/Japanese/Korean characters with no Latin)
_CJK_ONLY_RE = re.compile(r'^[\u2e80-\u9fff\uf900-\ufaff\ufe30-\ufe4f\U00020000-\U0002a6df\s\.\,\!\?]+$')


def is_whisper_hallucination(text: str) -> bool:
    """Check if a Whisper transcript is a known hallucination."""
    if not text:
        return True
    cleaned = text.strip().lower().rstrip('.!?,')
    # Too short to be real speech
    if len(cleaned) <= 2:
        return True
    # Known hallucination phrases
    if cleaned in _WHISPER_HALLUCINATIONS or text.strip().lower() in _WHISPER_HALLUCINATIONS:
        return True
    # CJK-only output (Whisper hallucinates Chinese when given silence)
    if _CJK_ONLY_RE.match(text.strip()):
        return True
    # Repeated single word/character (e.g. "you you you")
    words = cleaned.split()
    if len(words) >= 2 and len(set(words)) == 1:
        return True
    return False
