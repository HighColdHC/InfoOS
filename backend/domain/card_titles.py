"""Pure title rules for information cards and body-derived display titles."""

from __future__ import annotations

import re
from typing import Any


_LINK = re.compile(r"https?://[^\s<>。！？]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>。！？]*)?", re.I)
_ABBREVIATION = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|Inc|Ltd)\.|\b(?:[a-z]\.){2,}", re.I
)
_GREETING = re.compile(r"^(?:hi|hello|hey|dear(?:\s+all)?|你好|大家好)[,，.!！。:：\s]*$", re.I)
_LEADING_GREETING = re.compile(r"^(?:hi|hello|hey|你好|大家好)[,，]\s+", re.I)


def information_card_title(
    source_platform: Any,
    source_title: Any,
    normalized_content: Any,
    *,
    max_length: int = 80,
) -> str:
    """Preserve explicit source titles; derive a title for untitled X posts."""
    title = _collapse_whitespace(source_title)
    if title or str(source_platform or "").strip().lower() != "x":
        return title
    return body_title(normalized_content, max_length=max_length)


def body_title(value: Any, *, max_length: int = 80) -> str:
    """Select the first useful sentence/line, falling back to the body prefix."""
    text = str(value or "").strip()
    for line in text.splitlines():
        line = _LEADING_GREETING.sub("", line.strip())
        start = 0
        protected = _protected_positions(line)
        for index, char in enumerate(line):
            if char in ".!?。！？" and index not in protected:
                end = index + 1
                while end < len(line) and line[end] in '\"\'”’」』）)':
                    end += 1
                candidate = _collapse_whitespace(line[start:end])
                if _useful(candidate):
                    return _truncate(candidate, max_length)
                start = end
        candidate = _collapse_whitespace(line[start:])
        if _useful(candidate):
            return _truncate(candidate, max_length)
    return _truncate(_collapse_whitespace(text), max_length)


def broken_body_title(title: Any, content: Any) -> bool:
    """Identify a body-prefix title ending inside a token or lacking substance.

    Used for X display correction and the bounded repair, preserving non-prefix source titles.
    """
    title, content = _collapse_whitespace(title), _collapse_whitespace(content)
    if not title or not content.startswith(title):
        return False
    return (
        not _useful(title)
        or (title.endswith(".") and len(title) - 1 in _protected_positions(content))
    )


def title_needs_body(source_platform: str | None) -> bool:
    """Only X body-prefix titles can need the body to repair the display title."""
    return str(source_platform or "").strip().lower() == "x"


def display_title(source_platform: str | None, stored_title: str, content: str | None, *, max_length: int = 80) -> str:
    """Repair broken X body titles for display, preserving the stored input."""
    if title_needs_body(source_platform) and broken_body_title(stored_title, content):
        return body_title(content, max_length=max_length)
    return stored_title


def _protected_positions(text: str) -> set[int]:
    positions: set[int] = set()
    for pattern in (_LINK, _ABBREVIATION):
        for match in pattern.finditer(text):
            # Trailing URL sentence punctuation remains a sentence boundary.
            end = match.end()
            if pattern is _LINK:
                end = match.start() + len(match.group().rstrip(".!?,;:）)\"'"))
            positions.update(range(match.start(), end))
    for match in re.finditer(r"(?<=\d)\.(?=\d)", text):
        positions.add(match.start())
    return positions


def _useful(text: str) -> bool:
    meaningful = _LINK.sub("", text)
    return not _GREETING.fullmatch(text) and sum(char.isalnum() for char in meaningful) >= 6


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:max(0, limit)]
    prefix = text[:limit - 1]
    # Avoid slicing an English word; Chinese characters can be cut individually.
    if text[limit - 1].isascii() and text[limit - 1].isalnum() and prefix[-1].isascii() and prefix[-1].isalnum():
        boundary = prefix.rfind(" ")
        if boundary > 0:
            prefix = prefix[:boundary]
    return prefix.rstrip() + "…"


def _collapse_whitespace(value: Any) -> str:
    return " ".join(str(value or "").split())
