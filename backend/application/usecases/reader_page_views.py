"""Record a reader page opening at the HTTP input boundary."""
from pathlib import Path

from backend.infrastructure.storage.reader_page_views import ReaderPageViewRepository


def record_reader_page_view(base_dir: Path | str, page: str, target_id: str | None,
                            ref: str | None, user_agent: str | None) -> None:
    ReaderPageViewRepository(base_dir).insert(page, target_id, ref[:32] if ref is not None else None,
                                            user_agent[:160] if user_agent is not None else None)
