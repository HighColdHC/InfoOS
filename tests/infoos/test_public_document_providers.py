from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.application.usecases.public_documents import PublicDocumentAcquirer
from backend.domain.public_documents import (
    PublicDocument,
    PublicDocumentOutcome,
    PublicDocumentRequest,
)
from backend.infrastructure.public_documents import http as http_provider
from backend.infrastructure.public_documents import wigolo as wigolo_provider
from backend.infrastructure.public_documents.http import HttpPublicDocumentProvider
from backend.infrastructure.public_documents.wigolo import WigoloPublicDocumentProvider


def _request(url: str = "https://example.test/article") -> PublicDocumentRequest:
    return PublicDocumentRequest(
        url=url,
        headers={"User-Agent": "InfoOS/test"},
        timeout_seconds=20,
        max_bytes=1024 * 1024,
    )


def _document(
    *,
    provider: str,
    content: str,
    links: tuple[str, ...] = (),
) -> PublicDocument:
    return PublicDocument(
        provider=provider,
        final_url="https://example.test/final",
        title="Example",
        content=content,
        readable_html="<p>Example</p>" if provider == "infoos_http" else None,
        links=links,
        images=(),
        videos=(),
        fetch_tier="http",
        cache_hit=False,
        status_code=200,
        elapsed_ms=10,
        response_bytes=100 if provider == "infoos_http" else None,
        content_hash=f"{provider}-hash",
    )


class _Provider:
    def __init__(self, document: PublicDocument):
        self.name = document.provider
        self.document = document
        self.requests: list[PublicDocumentRequest] = []

    def fetch(self, request: PublicDocumentRequest) -> PublicDocumentOutcome:
        self.requests.append(request)
        return PublicDocumentOutcome(document=self.document)


def test_http_provider_maps_bounded_response_to_neutral_document(monkeypatch):
    response = SimpleNamespace(
        final_url="https://example.test/final",
        response=SimpleNamespace(status_code=200),
    )
    monkeypatch.setattr(
        http_provider,
        "read_public_url",
        lambda *_args, **_kwargs: (
            (
                b"<html><head><title>Fetched title</title></head><body>"
                b"<article><p>Fetched body.</p><a href='/next'>Next</a>"
                b"<img src='/cover.jpg'></article></body></html>"
            ),
            response,
        ),
    )

    outcome = HttpPublicDocumentProvider().fetch(_request())

    assert outcome.failure is None
    assert outcome.document is not None
    assert outcome.document.provider == "infoos_http"
    assert outcome.document.final_url == "https://example.test/final"
    assert outcome.document.title == "Fetched title"
    assert "Fetched body." in outcome.document.content
    assert outcome.document.links == ("https://example.test/next",)
    assert outcome.document.images == ("https://example.test/cover.jpg",)


def test_shadow_result_only_changes_evidence_and_flags_large_content_gap():
    primary_document = _document(
        provider="infoos_http",
        content="p" * 20_000,
        links=tuple(f"https://example.test/{index}" for index in range(120)),
    )
    shadow_document = _document(
        provider="wigolo",
        content="s" * 2_000,
        links=tuple(f"https://example.test/{index}" for index in range(10)),
    )
    primary = _Provider(primary_document)
    shadow = _Provider(shadow_document)

    acquired = PublicDocumentAcquirer(primary=primary, shadow=shadow).acquire(_request())

    assert acquired is not None
    assert acquired.document is primary_document
    assert shadow.requests[0].url == primary_document.final_url
    assert acquired.provider_evidence["shadow"]["content_chars"] == 2_000
    assert acquired.provider_evidence["comparison"]["shadow_truncation_suspected"] is True


def test_comparison_flags_large_primary_extraction_gap_without_promoting_shadow():
    primary_document = _document(provider="infoos_http", content="shell")
    shadow_document = _document(provider="wigolo", content="article " * 1_000)
    primary = _Provider(primary_document)
    shadow = _Provider(shadow_document)

    acquired = PublicDocumentAcquirer(primary=primary, shadow=shadow).acquire(_request())

    assert acquired.document is primary_document
    assert (
        acquired.provider_evidence["comparison"][
            "primary_extraction_gap_suspected"
        ]
        is True
    )


def test_primary_failure_remains_typed_without_running_shadow():
    from backend.domain.public_documents import PublicDocumentFailure

    class FailedPrimary:
        name = "infoos_http"

        def fetch(self, _request):
            return PublicDocumentOutcome(
                failure=PublicDocumentFailure(
                    provider=self.name,
                    failure_class="timeout",
                    elapsed_ms=45_000,
                )
            )

    shadow = _Provider(_document(provider="wigolo", content="shadow"))

    acquired = PublicDocumentAcquirer(primary=FailedPrimary(), shadow=shadow).acquire(
        _request()
    )

    assert acquired.document is None
    assert acquired.provider_evidence == {
        "mode": "primary_failure",
        "primary": {
            "provider": "infoos_http",
            "failure_class": "timeout",
            "elapsed_ms": 45_000,
        },
    }
    assert shadow.requests == []


def test_wigolo_adapter_returns_typed_shadow_document_without_persisting_token(
    tmp_path: Path,
    monkeypatch,
):
    token_file = tmp_path / "wigolo.token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    monkeypatch.setattr(
        wigolo_provider,
        "validate_and_pin_public_url",
        lambda *_args, **_kwargs: "93.184.216.34",
    )
    provider = WigoloPublicDocumentProvider(
        base_url="http://wigolo:3333",
        token_file=token_file,
    )
    monkeypatch.setattr(
        provider,
        "_post_fetch",
        lambda **_kwargs: (
            200,
            {
                "url": "https://example.test/final",
                "title": "Wigolo title",
                "markdown": "# Wigolo content",
                "links": ["https://example.test/next"],
                "images": ["https://example.test/cover.jpg"],
                "cached": True,
                "fetch_method": "cache",
                "http_status": 200,
                "content_hash": "wigolo-hash",
            },
        ),
    )

    outcome = provider.fetch(_request())

    assert outcome.failure is None
    assert outcome.document is not None
    assert outcome.document.provider == "wigolo"
    assert outcome.document.cache_hit is True
    assert outcome.document.fetch_tier == "cache"
    assert "secret-token" not in repr(outcome)
