"""Pure Douyin item identity rules."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


DOUYIN_CANONICAL_HOST = "www.douyin.com"
DOUYIN_INPUT_HOSTS = frozenset(
    {
        "douyin.com",
        DOUYIN_CANONICAL_HOST,
        "v.douyin.com",
        "iesdouyin.com",
        "www.iesdouyin.com",
        "v.iesdouyin.com",
    }
)

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_BARE_DOUYIN_URL_RE = re.compile(
    r"(?<![/:])\b(?:v\.douyin\.com|v\.iesdouyin\.com)/[^\s<>\"']+",
    re.IGNORECASE,
)
_VIDEO_PATH_RE = re.compile(r"^/(?:video|share/video)/(\d+)(?:/|$)")


@dataclass(frozen=True)
class DouyinItemIdentity:
    aweme_id: str

    @property
    def canonical_url(self) -> str:
        return f"https://{DOUYIN_CANONICAL_HOST}/video/{self.aweme_id}"


def extract_single_douyin_url(value: str) -> str:
    """Extract exactly one accepted Douyin HTTP(S) URL from URL/share text."""
    text = str(value or "")
    matches = [*_URL_RE.findall(text), *_BARE_DOUYIN_URL_RE.findall(text)]
    if len(matches) != 1:
        raise ValueError("douyin item identifier must contain exactly one URL")
    candidate = matches[0].rstrip(".,，。!！?？;；:：)]}】》")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlsplit(candidate)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 80 if parsed.scheme.lower() == "http" else 443}
        or hostname not in DOUYIN_INPUT_HOSTS
    ):
        raise ValueError("unsupported douyin item URL")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, parsed.fragment))


def parse_douyin_item_url(value: str) -> DouyinItemIdentity | None:
    """Parse a canonical or Douyin share-video URL without doing I/O."""
    try:
        candidate = extract_single_douyin_url(value)
    except ValueError:
        return None
    parsed = urlsplit(candidate)
    match = _VIDEO_PATH_RE.match(parsed.path)
    if match is None:
        return None
    return DouyinItemIdentity(match.group(1))


def sanitized_douyin_url_evidence(value: str) -> str:
    """Keep only scheme, accepted host and path for diagnostic evidence."""
    candidate = extract_single_douyin_url(value)
    parsed = urlsplit(candidate)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", ""))
