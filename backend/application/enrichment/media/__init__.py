import importlib

__all__ = [
    "extract_audio",
    "transcribe_audio",
]

_SUBMODULES = {
    "candidates",
    "douyin",
    "download",
    "gallery",
    "tooling",
    "video_transcription",
    "x_failures",
    "xiaohongshu",
    "youtube",
}


def __getattr__(name: str):
    if name in _SUBMODULES:
        return importlib.import_module(f"{__name__}.{name}")
    if name in {"extract_audio", "transcribe_audio"}:
        from backend.application.enrichment.media.video_transcription import extract_audio, transcribe_audio

        return {"extract_audio": extract_audio, "transcribe_audio": transcribe_audio}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
