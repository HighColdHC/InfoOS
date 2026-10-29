from __future__ import annotations

import pytest

from backend.infrastructure.storage.repositories import CardRepository


def _card(**changes):
    payload = {
        "card_id": "card_search",
        "source_platform": "rss",
        "source_type": "article",
        "source_url": "https://example.com/search",
        "source_author": "authorcreate",
        "title": "titlecreate",
        "content": "contentcreate",
        "summary": "summarycreate",
        "captured_at": "2026-07-01T00:01:00Z",
        "processed_at": "2026-07-01T00:02:00Z",
    }
    payload.update(changes)
    return payload


def test_card_search_rows_are_immediately_fresh(tmp_path):
    cards = CardRepository(tmp_path)
    cards.upsert_card(_card())
    for term in ("titlecreate", "contentcreate", "authorcreate"):
        assert _search(tmp_path, term)[0]["card_id"] == "card_search"
    assert _search(tmp_path, "summarycreate") == []

    cards.upsert_card(
        _card(title="titleupdated", content="contentupdated", summary="summaryupdated", source_author="authorupdated")
    )
    for term in ("titleupdated", "contentupdated", "authorupdated"):
        assert _search(tmp_path, term)[0]["card_id"] == "card_search"
    assert _search(tmp_path, "titlecreate") == []


def test_source_write_rolls_back_when_index_write_fails(tmp_path):
    import sqlite3
    cards = CardRepository(tmp_path)
    with cards.connect() as conn:
        conn.execute('drop table card_text_fts')
    with pytest.raises(sqlite3.OperationalError, match='card_text_fts'):
        cards.upsert_card(_card())
    assert cards.get_card('card_search') is None


def test_card_search_triggers_do_not_commit(tmp_path):
    cards = CardRepository(tmp_path)
    cards.upsert_card(_card())
    with cards.connect() as conn:
        conn.execute("begin")
        conn.execute("update cards set title = 'transactionterm' where card_id = 'card_search'")
        conn.rollback()
    assert _search(tmp_path, "transactionterm") == []
    assert _search(tmp_path, "titlecreate")


def _search(base_dir, term):
    with CardRepository(base_dir).connect_existing() as conn:
        return conn.execute("select card_id from cards where rowid in "
                            "(select rowid from card_text_fts where card_text_fts match ?)",
                            ('"' + term.replace('"', '""') + '"',)).fetchall()
