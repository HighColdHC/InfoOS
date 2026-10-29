"""Synthetic Chrome metadata only; the fixture has no cookie value column."""
import sqlite3
from datetime import datetime, timezone

import pytest

from backend.infrastructure.browser.cookie_status import login_cookie_changed_since

SINCE = datetime(2026, 10, 22, tzinfo=timezone.utc)
CHROME_SINCE = int((SINCE - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds()) * 1_000_000


@pytest.mark.parametrize('host,name,creation,updated,expected', [
    ('.xiaohongshu.com', 'web_session', -100, -1, False),
    ('.xiaohongshu.com', 'id_token', -100, 0, False),
    ('.xiaohongshu.com', 'web_session', 1, -100, True),
    ('www.xiaohongshu.com', 'id_token', -100, 1, True),
    ('evilaixiaohongshu.com', 'web_session', 1, 1, False),
    ('.xiaohongshu.com', 'visitor', 1, 1, False),
    ('.douyin.com', 'sessionid', -100, 1, True),
    ('wx.zsxq.com', 'zsxq_access_token', 1, -100, True),
])
def test_changed_login_metadata(tmp_path, host, name, creation, updated, expected):
    path = tmp_path / 'Default' / 'Network' / 'Cookies'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute('create table cookies(host_key text,name text,creation_utc integer,last_update_utc integer)')
        conn.execute('insert into cookies values(?,?,?,?)', (host, name, CHROME_SINCE + creation, CHROME_SINCE + updated))
    platform = 'douyin' if 'douyin' in host else 'zsxq' if 'zsxq' in host else 'xiaohongshu'
    assert login_cookie_changed_since(platform, tmp_path, SINCE) is expected
    with sqlite3.connect(path) as conn:
        assert conn.execute('select creation_utc,last_update_utc from cookies').fetchone() == (
            CHROME_SINCE + creation, CHROME_SINCE + updated)
