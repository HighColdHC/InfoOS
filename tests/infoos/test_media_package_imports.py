from __future__ import annotations

import importlib
import sys


def test_media_package_does_not_eagerly_import_heavy_submodules():
    for module_name in [
        "backend.application.enrichment.media",
        "backend.application.enrichment.media.video_transcription",
        "backend.application.enrichment.media.pipeline",
    ]:
        sys.modules.pop(module_name, None)

    media = importlib.import_module("backend.application.enrichment.media")

    assert "backend.application.enrichment.media.video_transcription" not in sys.modules
    assert callable(media.extract_audio)
    assert "backend.application.enrichment.media.video_transcription" in sys.modules
