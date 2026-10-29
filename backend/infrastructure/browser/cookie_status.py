from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CookieRule:
    host_suffixes: tuple[str, ...]
    names: tuple[str, ...]
    login_url: str
    authorization_url: str
    require_all_names: bool = False


COOKIE_RULES: dict[str, CookieRule] = {
    "google": CookieRule(
        host_suffixes=("google.com", "accounts.google.com"),
        names=("SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID"),
        login_url="https://accounts.google.com/",
        authorization_url="https://accounts.google.com/",
    ),
    "x": CookieRule(
        host_suffixes=("x.com", "twitter.com"),
        names=("auth_token", "ct0"),
        login_url="https://x.com/login",
        authorization_url="https://x.com/home",
        require_all_names=True,
    ),
    "youtube": CookieRule(
        host_suffixes=("youtube.com", "google.com", "accounts.google.com"),
        names=("LOGIN_INFO", "SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID"),
        login_url="https://www.youtube.com/",
        authorization_url="https://www.youtube.com/",
    ),
    "reddit": CookieRule(
        host_suffixes=("reddit.com",),
        names=("reddit_session", "token_v2"),
        login_url="https://www.reddit.com/login/",
        authorization_url="https://www.reddit.com/",
    ),
    "xiaohongshu": CookieRule(
        host_suffixes=("xiaohongshu.com",),
        names=("web_session", "id_token"),
        login_url="https://www.xiaohongshu.com/",
        authorization_url="https://www.xiaohongshu.com/",
    ),
    "douyin": CookieRule(
        host_suffixes=("douyin.com", "creator.douyin.com"),
        names=("sessionid", "sid_guard", "uid_tt", "passport_assist_user"),
        login_url="https://www.douyin.com/",
        authorization_url="https://www.douyin.com/",
    ),
    "zsxq": CookieRule(
        host_suffixes=("zsxq.com",),
        names=("zsxq_access_token",),
        login_url="https://wx.zsxq.com/",
        authorization_url="https://wx.zsxq.com/",
    ),
}


# Authorization-only browser surfaces are intentionally kept outside the
# configurable source registry.  They support a user-managed login session
# without declaring a new InfoOS collection platform.
AUTHORIZATION_ONLY_URLS: dict[str, str] = {}


def login_url_for_platform(platform: str) -> str | None:
    rule = COOKIE_RULES.get(platform)
    return rule.login_url if rule else AUTHORIZATION_ONLY_URLS.get(platform)


def authorization_url_for_platform(platform: str) -> str | None:
    rule = COOKIE_RULES.get(platform)
    return rule.authorization_url if rule else AUTHORIZATION_ONLY_URLS.get(platform)


def check_cookie_login(platform: str, profile_dir: str | Path | None) -> dict[str, Any]:
    rule = COOKIE_RULES.get(platform)
    if rule is None:
        return {
            "platform": platform,
            "checked": False,
            "status": "unsupported",
            "error": "暂不支持检查该平台的登录状态",
            "error_code": "unsupported_platform",
        }
    if profile_dir is None:
        return _not_configured(platform, rule)

    profile = Path(profile_dir).expanduser()
    cookie_dbs = _cookie_db_paths(profile)
    existing = [path for path in cookie_dbs if path.exists()]
    if not existing:
        return _logged_out(platform, rule, "专用浏览器中未找到登录状态数据库", "cookie_database_missing")

    for path in existing:
        try:
            if _db_has_login_cookie(path, rule):
                return {
                    "platform": platform,
                    "checked": True,
                    "status": "logged_in",
                    "error": None,
                    "login_url": rule.login_url,
                    "method": "cookie",
                }
        except sqlite3.Error:
            return {
                "platform": platform,
                "checked": True,
                "status": "failed",
                "error": "无法读取专用浏览器的登录状态",
                "error_code": "cookie_database_unreadable",
                "login_url": rule.login_url,
                "method": "cookie",
            }
    return _logged_out(platform, rule, "未在专用浏览器中找到该平台的登录凭据", "auth_required")


def _cookie_db_paths(profile_dir: Path) -> list[Path]:
    return [
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Default" / "Cookies",
    ]


def login_cookie_changed_since(platform: str, profile_dir: str | Path, since: datetime) -> bool:
    rule = COOKIE_RULES[platform]
    elapsed = since - datetime(1601, 1, 1, tzinfo=timezone.utc)
    chrome_since = (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds
    for path in _cookie_db_paths(Path(profile_dir).expanduser().resolve()):
        if not path.exists():
            continue
        conn = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True, timeout=0.5)
        try:
            placeholders = ','.join('?' for _ in rule.names)
            for suffix in rule.host_suffixes:
                found = conn.execute(
                    f'SELECT 1 FROM cookies WHERE name IN ({placeholders}) '
                    'AND (host_key = ? OR host_key = ? OR host_key LIKE ?) '
                    'AND (creation_utc > ? OR last_update_utc > ?) LIMIT 1',
                    (*rule.names, suffix, f'.{suffix}', f'%.{suffix}', chrome_since, chrome_since),
                ).fetchone()
                if found is not None:
                    return True
        except sqlite3.OperationalError:
            # Chrome briefly locks the database while flushing; the 5-second poll retries.
            return False
        finally:
            conn.close()
    return False


def _db_has_login_cookie(path: Path, rule: CookieRule) -> bool:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=0.5)
    try:
        for host_suffix in rule.host_suffixes:
            host_patterns = (host_suffix, f".{host_suffix}", f"%.{host_suffix}")
            placeholders = ",".join("?" for _ in rule.names)
            query = (
                f"SELECT DISTINCT name FROM cookies WHERE name IN ({placeholders}) "
                "AND (host_key = ? OR host_key = ? OR host_key LIKE ?)"
            )
            found = {
                str(row[0])
                for row in conn.execute(query, [*rule.names, *host_patterns]).fetchall()
            }
            if found and (not rule.require_all_names or found == set(rule.names)):
                return True
    finally:
        conn.close()
    return False


def _not_configured(platform: str, rule: CookieRule) -> dict[str, Any]:
    return {
        "platform": platform,
        "checked": False,
        "status": "not_configured",
        "error": "当前实例未找到有效的专用浏览器会话记录",
        "error_code": "browser_session_not_configured",
        "login_url": rule.login_url,
        "method": "cookie",
    }


def _logged_out(platform: str, rule: CookieRule, error: str, error_code: str) -> dict[str, Any]:
    return {
        "platform": platform,
        "checked": True,
        "status": "logged_out",
        "error": error,
        "error_code": error_code,
        "login_url": rule.login_url,
        "method": "cookie",
    }
