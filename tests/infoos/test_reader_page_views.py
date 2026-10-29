"""Only real GET reader document openings produce usage rows."""
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.infrastructure.storage.reader_page_views import ReaderPageViewRepository


def test_reader_get_records_visit_head_does_not(tmp_path):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>Reader</html>")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/r/card/x?from=" + "f" * 40, headers={"user-agent": "u" * 170}).status_code == 200
        assert client.head("/r/card/x?from=feishu").status_code == 200
    with ReaderPageViewRepository(tmp_path).connect_existing() as conn:
        rows = conn.execute("select page, target_id, ref, user_agent, created_at from reader_page_views").fetchall()
    assert len(rows) == 1
    assert dict(rows[0]) == {"page": "card", "target_id": "x", "ref": "f" * 32,
                             "user_agent": "u" * 160, "created_at": rows[0]["created_at"]}
    assert rows[0]["created_at"].endswith("Z")
