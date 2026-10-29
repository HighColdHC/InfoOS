"""Single response-safety boundary for Source Run free text and URLs."""
from __future__ import annotations

import re
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)[\"']?\b(token|cookie|authorization|password|secret|api[_-]?key|signature|sig|x-amz-signature)\b[\"']?\s*[:=]\s*(?:[\"'][^\"']*[\"']|[^\s,;}]+)"
)
_AUTH_VALUE = re.compile(r"(?i)\b(Basic|Bearer)\s+[A-Za-z0-9._~+/=-]+")
_COOKIE_HEADER = re.compile(r"(?i)\bCookie\s*:\s*[^\r\n]+")
_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:)?/(?:[^\s/]+/)+[^\s]+")
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\s]+\\)+[^\\\s]+")
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password|cookie|authorization|signature|sig|x-amz-signature|credential)"
)
_YOUTUBE_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{6,32}")
_YOUTUBE_WATCH_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_REDDIT_SUBREDDIT = re.compile(r"r/([A-Za-z0-9_]{2,21})", re.IGNORECASE)


def redact_text(value: object, *, limit: int) -> str:
    text = str(value or "")
    text = _AUTH_VALUE.sub("[REDACTED]", text)
    text = _COOKIE_HEADER.sub("Cookie: [REDACTED]", text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _WINDOWS_PATH.sub("[PATH]", text)
    text = _LOCAL_PATH.sub("[PATH]", text).replace("..", "[PATH]")
    return text if len(text) <= limit else text[:limit] + "…"


def safe_label(value: object, *, limit: int = 120) -> str:
    text = redact_text(value, limit=limit).strip()
    if not text or any(part in text for part in ("/", "\\", "..")):
        return "unavailable"
    return text


def safe_identifier(value: object, *, limit: int = 240) -> str:
    raw = str(value or "")
    if not raw or len(raw) > limit or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        return "unavailable"
    lowered = raw.lower()
    if (
        lowered.startswith(("http://", "https://"))
        or "?" in raw or "#" in raw or "@" in raw
        or _LOCAL_PATH.search(raw) or _WINDOWS_PATH.search(raw)
        or _SECRET_ASSIGNMENT.search(raw)
        or "/" in raw or "\\" in raw
    ):
        return "unavailable"
    return redact_text(raw, limit=limit)


def opaque_identifier(kind: str, value: object) -> str:
    raw = str(value or "")
    if not raw:
        return "unavailable"
    digest = sha256(f"infoos-source-run:{kind}\0{raw}".encode("utf-8")).hexdigest()[:24]
    return f"{kind}_{digest}"


def task_identifier_alias(value: object) -> str:
    return opaque_identifier("task", value)


def safe_or_opaque_identifier(kind: str, value: object) -> str:
    safe = safe_identifier(value)
    return opaque_identifier(kind, value) if safe == "unavailable" and value not in (None, "") else safe


def source_identifier_fields(
    value: object, *, platform: object = None, source_type: object = None
) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "unavailable", "unavailable"
    if raw.lower().startswith(("http://", "https://")):
        safe_url = sanitize_external_url(raw)
        return (safe_url, safe_url) if safe_url else ("unavailable", "unavailable")
    if str(platform or "").lower() == "reddit" and str(source_type or "").lower() == "subreddit":
        match = _REDDIT_SUBREDDIT.fullmatch(raw)
        if match:
            return f"r/{match.group(1).lower()}", "unavailable"
    if (
        any(ord(char) < 32 or ord(char) == 127 for char in raw)
        or _LOCAL_PATH.search(raw) or _WINDOWS_PATH.search(raw)
        or _SECRET_ASSIGNMENT.search(raw) or ".." in raw or "/" in raw or "\\" in raw
    ):
        return "unavailable", "unavailable"
    return redact_text(raw, limit=240), "unavailable"


def sanitize_external_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    path = parsed.path
    query = _allowed_identity_query(host, path, parsed.query)
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def _allowed_identity_query(host: str, path: str, raw_query: str) -> str:
    if host not in _YOUTUBE_WATCH_HOSTS or path != "/watch" or not raw_query:
        return ""
    try:
        pairs = parse_qsl(raw_query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return ""
    video_ids = [value for key, value in pairs if key == "v"]
    if len(video_ids) != 1 or _YOUTUBE_VIDEO_ID.fullmatch(video_ids[0]) is None:
        return ""
    return urlencode({"v": video_ids[0]})


def sanitize_value(value: object, *, text_limit: int = 4000, list_limit: int = 40) -> object:
    if isinstance(value, dict):
        result = {}
        for key, child in list(value.items())[:40]:
            safe_key = redact_text(key, limit=100)
            result[safe_key] = "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else sanitize_value(
                child, text_limit=text_limit, list_limit=list_limit
            )
        return result
    if isinstance(value, list):
        return [sanitize_value(child, text_limit=text_limit, list_limit=list_limit) for child in value[:list_limit]]
    if isinstance(value, str):
        if value.strip().lower().startswith(("http://", "https://")):
            return sanitize_external_url(value) or "unavailable"
        return redact_text(value, limit=text_limit)
    return value
