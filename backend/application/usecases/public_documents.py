"""Acquire one public document while keeping optional shadow output read-only."""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path

from backend.domain.public_documents import (
    PublicDocument,
    PublicDocumentFailure,
    PublicDocumentOutcome,
    PublicDocumentProvider,
    PublicDocumentRequest,
    compare_public_documents,
)
from backend.infrastructure.public_documents import (
    HttpPublicDocumentProvider,
    WigoloPublicDocumentProvider,
)


@dataclass(frozen=True)
class PublicDocumentAcquisition:
    document: PublicDocument | None
    provider_evidence: dict[str, object]


class PublicDocumentAcquirer:
    def __init__(
        self,
        *,
        primary: PublicDocumentProvider,
        shadow: PublicDocumentProvider | None = None,
    ):
        self._primary = primary
        self._shadow = shadow

    def acquire(self, request: PublicDocumentRequest) -> PublicDocumentAcquisition:
        primary = self._primary.fetch(request)
        if primary.document is None:
            return PublicDocumentAcquisition(
                document=None,
                provider_evidence={
                    "mode": "primary_failure",
                    "primary": primary.failure.evidence(),
                },
            )
        evidence: dict[str, object] = {
            "mode": "primary_only" if self._shadow is None else "shadow_compare",
            "primary": primary.document.completeness_evidence(),
        }
        if self._shadow is not None:
            shadow_request = replace(request, url=primary.document.final_url)
            shadow = self._shadow.fetch(shadow_request)
            evidence.update(_shadow_evidence(primary.document, shadow))
        return PublicDocumentAcquisition(
            document=primary.document,
            provider_evidence=evidence,
        )


def acquire_public_document_from_environment(
    request: PublicDocumentRequest,
) -> PublicDocumentAcquisition:
    return _build_acquirer().acquire(request)


def _build_acquirer() -> PublicDocumentAcquirer:
    shadow_name = os.environ.get("INFOOS_PUBLIC_DOCUMENT_SHADOW_PROVIDER", "").strip().lower()
    shadow: PublicDocumentProvider | None = None
    if shadow_name == "wigolo":
        base_url = os.environ.get("INFOOS_WIGOLO_SHADOW_BASE_URL", "").strip()
        token_file = os.environ.get("INFOOS_WIGOLO_SHADOW_TOKEN_FILE", "").strip()
        if base_url and token_file:
            try:
                shadow = WigoloPublicDocumentProvider(
                    base_url=base_url,
                    token_file=Path(token_file),
                    timeout_seconds=_shadow_timeout(),
                )
            except ValueError:
                shadow = _UnavailableShadowProvider("wigolo")
        else:
            shadow = _UnavailableShadowProvider("wigolo")
    elif shadow_name:
        shadow = _UnavailableShadowProvider(shadow_name)
    return PublicDocumentAcquirer(
        primary=HttpPublicDocumentProvider(),
        shadow=shadow,
    )


def _shadow_evidence(
    primary: PublicDocument,
    shadow: PublicDocumentOutcome,
) -> dict[str, object]:
    if shadow.document is not None:
        return {
            "shadow": shadow.document.completeness_evidence(),
            "comparison": compare_public_documents(
                primary,
                shadow.document,
            ),
        }
    return {
        "shadow": shadow.failure.evidence(),
        "comparison": {
            "primary_provider": primary.provider,
            "shadow_provider": shadow.failure.provider,
            "shadow_failed": True,
        },
    }


class _UnavailableShadowProvider:
    def __init__(self, name: str):
        self.name = name

    def fetch(self, _request: PublicDocumentRequest) -> PublicDocumentOutcome:
        return PublicDocumentOutcome(
            failure=PublicDocumentFailure(
                provider=self.name,
                failure_class="configuration_error",
                elapsed_ms=0,
            )
        )


def _shadow_timeout() -> float:
    value = os.environ.get("INFOOS_WIGOLO_SHADOW_TIMEOUT_SECONDS", "45").strip()
    try:
        return min(120.0, max(1.0, float(value)))
    except ValueError:
        return 45.0
