from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit, urlunsplit

_X_STATUS_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
X_MEDIA_HOSTS = frozenset({"pbs.twimg.com", "video.twimg.com"})
_STATUS_PATH = re.compile(r"^/([A-Za-z0-9_]+)/status/([0-9]+)$")
_VIDEO_PATH_PREFIXES = ("/amplify_video/", "/ext_tw_video/", "/tweet_video/")
_AMPLIFY_POSTER_PATH = re.compile(r"^/amplify_video_thumb/([0-9]+)/")


@dataclass(frozen=True)
class CanonicalXStatus:
    url: str
    handle: str
    tweet_id: str


def parse_canonical_x_status_url(value: str) -> CanonicalXStatus | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or hostname not in _X_STATUS_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    match = _STATUS_PATH.fullmatch(parsed.path)
    if match is None:
        return None
    handle, tweet_id = match.groups()
    if not handle or handle in {".", ".."}:
        return None
    canonical = urlunsplit(("https", "x.com", f"/{handle}/status/{tweet_id}", "", ""))
    return CanonicalXStatus(canonical, handle, tweet_id)


def is_safe_x_media_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower() in X_MEDIA_HOSTS
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def is_x_tweet_media_url(value: str) -> bool:
    """A CDN URL shaped like tweet media; ownership is checked separately."""
    if not is_safe_x_media_url(value):
        return False
    parsed = urlsplit(value)
    if parsed.hostname == "pbs.twimg.com":
        return parsed.path.startswith("/media/") and len(parsed.path) > len("/media/")
    return parsed.path.startswith(_VIDEO_PATH_PREFIXES)


def x_video_id(value: str) -> str | None:
    if not is_x_tweet_media_url(value) or urlsplit(value).hostname != "video.twimg.com":
        return None
    match = re.match(r"^/(?:amplify_video|ext_tw_video)/([0-9]+)/", urlsplit(value).path)
    return match.group(1) if match else None


def is_x_tweet_poster_url(value: str, video_ids: set[str]) -> bool:
    if not is_safe_x_media_url(value) or urlsplit(value).hostname != "pbs.twimg.com":
        return False
    path = urlsplit(value).path
    if path.startswith("/media/") and len(path) > len("/media/"):
        return True
    match = _AMPLIFY_POSTER_PATH.match(path)
    return bool(match and match.group(1) in video_ids)
