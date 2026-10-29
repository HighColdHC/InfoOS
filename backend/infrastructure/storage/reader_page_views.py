"""Reader document visits, without network identity or cookies."""
from backend.domain.timestamps import utc_now_seconds_z
from backend.infrastructure.storage.base import InfoOSRepository


class ReaderPageViewRepository(InfoOSRepository):
    def insert(self, page: str, target_id: str | None, ref: str | None, user_agent: str | None) -> None:
        with self.connect() as conn:
            conn.execute("""insert into reader_page_views (page, target_id, ref, user_agent, created_at)
                values (?, ?, ?, ?, ?)""", (page, target_id, ref, user_agent, utc_now_seconds_z()))
