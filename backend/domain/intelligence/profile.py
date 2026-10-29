"""Pure intelligence-layer vocabulary and profile parsing."""
from __future__ import annotations

import re
from hashlib import sha256


PROFILE_SECTIONS = ("主线与KR", "进行中项目", "正在追问的问题", "暂放方向", "长期领域")
ATTRIBUTION_KINDS = frozenset({"project", "kr", "question", "domain", "exploration", "paused"})
CONTENT_TYPES = frozenset({"event", "method", "case", "opinion", "tool", "data"})
SUGGESTED_ACTIONS = frozenset({"aware", "read", "distill_candidate", "action_candidate"})
MAX_PROFILE_CHARACTERS = 20_000
_HEADING = re.compile(r"^##[ \t]+(.+?)[ \t]*$")
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(.+)$")


class ProfileValidationError(ValueError):
    """Invalid profile Markdown supplied by an agent."""


def parse_profile(markdown: str) -> dict[str, list[str]]:
    if len(markdown) > MAX_PROFILE_CHARACTERS:
        raise ProfileValidationError("markdown exceeds 20,000 characters")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        heading = _HEADING.match(line)
        if heading:
            title = heading.group(1).strip()
            current = title if title in PROFILE_SECTIONS else None
            if current is not None:
                if current in sections:
                    raise ProfileValidationError(f"duplicate section: {current}")
                sections[current] = []
            continue
        if re.match(r"^#[ \t]", line):
            current = None
            continue
        item = _LIST_ITEM.match(line)
        if current is not None and item:
            sections[current].append(item.group(1))
    missing = [name for name in PROFILE_SECTIONS if name not in sections]
    if missing:
        raise ProfileValidationError("missing sections: " + ", ".join(missing))
    return {name: sections[name] for name in PROFILE_SECTIONS}


def profile_hash(markdown: str) -> str:
    return "sha256:" + sha256(markdown.encode("utf-8")).hexdigest()
