"""Shared public links for reader entry points."""
from typing import Any
from urllib.parse import quote

from backend.infrastructure.runtime.environment import public_base_url


def reader_link(path: str) -> dict[str, Any]:
    origin = public_base_url()
    return {"url": f"{origin}{path}" if origin else path, "relative_url": origin is None}


def card_link(card_id: str) -> dict[str, Any]:
    return reader_link(f"/r/card/{quote(card_id, safe='')}")


def opportunity_link(card_id: str) -> dict[str, Any]:
    return reader_link(f"/r/opportunities?card={quote(card_id, safe='')}")


def brief_link(brief_date: str) -> dict[str, Any]:
    return reader_link(f"/r/brief/{quote(brief_date, safe='')}")
