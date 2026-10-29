"""Isolated application, real Bearer transport, and no external platform I/O."""
import socket

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.usecases.api_tokens import create_token, revoke_token

SCHEDULED_SCOPES = ["intelligence:read", "evaluation:write", "profile:write"]
INTERACTIVE_SCOPES = SCHEDULED_SCOPES + ["collect:write", "sources:write", "verification:write"]


@pytest.fixture
def journey(tmp_path, monkeypatch):
    monkeypatch.delenv("INFOOS_PUBLIC_BASE_URL", raising=False)
    # A missed platform double fails without ever contacting DNS or the network.
    def offline(*args, **kwargs):
        raise AssertionError("端到端场景不得访问外部网络")
    monkeypatch.setattr(socket, "getaddrinfo", offline)
    monkeypatch.setattr(socket, "create_connection", offline)
    monkeypatch.setattr("backend.infrastructure.public_http.open_pinned_url", offline)
    # Generic article enrichment has no remote article beyond the seeded body.
    monkeypatch.setattr("backend.application.enrichment.web_content._fetch_enriched_content_and_media",
                        lambda raw: (None, {}))
    # Fake Douyin media availability: these journeys supply text only, no remote
    # video/audio. The real media handler records the gap and still finalizes.
    monkeypatch.setattr("backend.application.enrichment.media.download.download_douyin_card_media",
                        lambda card, base: [])
    # Reader HTML is built by the separate frontend build; exercise its real SPA routes.
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text('<html><div id="root">InfoOS reader</div></html>')
    app = create_app(tmp_path)
    scheduled = create_token(tmp_path, name="定时 agent", scopes=SCHEDULED_SCOPES)
    interactive = create_token(tmp_path, name="交互 agent", scopes=INTERACTIVE_SCOPES)
    with TestClient(app) as client:
        yield client, scheduled, interactive
    for token in (scheduled, interactive):
        revoke_token(tmp_path, token["token"]["token_id"])
