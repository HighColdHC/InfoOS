from __future__ import annotations

EUROPEAN_LANGUAGE_CODES = {
    "de",
    "es",
    "fr",
    "it",
    "nl",
    "pl",
    "pt",
    "ru",
    "sv",
    "uk",
}


def normalize_language(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("_", "-")
    if not normalized:
        return "unknown"
    if normalized.startswith("zh"):
        return "zh"
    if normalized in {"cn", "cmn", "mandarin"}:
        return "zh"
    if normalized in {"yue", "cantonese", "zh-yue", "zh-hk"}:
        return "yue"
    return normalized.split("-", 1)[0]
