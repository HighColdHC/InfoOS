"""Public-document provider adapters."""

from .http import HttpPublicDocumentProvider
from .wigolo import WigoloPublicDocumentProvider

__all__ = ["HttpPublicDocumentProvider", "WigoloPublicDocumentProvider"]
