"""Strip non-speakable tokens (stage directions, markdown, emoji) from model text."""
from __future__ import annotations

import re

SENTENCE_TERMINATORS: str = ".!?"

_STAGE_DIRECTION = re.compile(r"\*[^*\n]+\*")
_PAREN_ASIDE = re.compile(r"\([^)\n]{1,80}\)")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")
_CODE_FENCE = re.compile(r"```[^`]*```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADING = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")
_EMOJI = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F1E0-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)


def clean_for_speech(text: str) -> str:
    if not text:
        return ""
    s = _CODE_FENCE.sub(" ", text)
    s = _STAGE_DIRECTION.sub(" ", s)
    s = _PAREN_ASIDE.sub(" ", s)
    s = _BOLD.sub(r"\1", s)
    s = _ITALIC.sub(r"\1", s)
    s = _INLINE_CODE.sub(r"\1", s)
    s = _MARKDOWN_LINK.sub(r"\1", s)
    s = _HEADING.sub("", s)
    s = _BULLET.sub("", s)
    s = _EMOJI.sub("", s)
    s = _WHITESPACE.sub(" ", s).strip()
    return s


def split_into_sentences(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in SENTENCE_TERMINATORS:
            piece = "".join(buf).strip()
            if piece:
                out.append(piece)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


if __name__ == "__main__":
    sample = "*nods* Okay, driving forward. (pauses) Done."
    print(repr(clean_for_speech(sample)))
    print(split_into_sentences(clean_for_speech(sample)))
