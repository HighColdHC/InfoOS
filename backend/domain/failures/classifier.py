"""Failure classifier: 把底层异常归类到 13 种 FetchFailureReason。

职责：
  - 不重试，不通知，不写日志——只分类
  - 调用方（fetch 编排层）拿到 reason 后去查 RetryPolicy / Notification
  - 无法分类时抛 ClassificationError，调用方应当转成 failed + 写日志
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from sqlite3 import IntegrityError, OperationalError

from backend.domain.failures.taxonomy import FetchFailureReason


@dataclass(frozen=True)
class FetchContext:
    """fetch 失败时的上下文信息，用于分类。"""

    platform: str  # x | youtube | rss
    source_type: str
    http_status: int | None = None
    exit_code: int | None = None
    stderr: str = ""


class ClassificationError(Exception):
    """无法分类的失败。调用方应当转成 failed + 写日志。"""


# 模式匹配表（按优先级排序：先匹配更具体的）
_PATTERNS: list[tuple[FetchFailureReason, re.Pattern[str]]] = [
    (FetchFailureReason.PLATFORM_NOT_LOGGED_IN, re.compile(
        r"login[_\s]required|not[_\s]logged[_\s]in|\b(?:HTTP(?:\s*Error)?|status(?:[\s_]code)?)[\s:=]*401\b|auth[_\s]failed",
        re.IGNORECASE,
    )),
    (FetchFailureReason.RATE_LIMITED, re.compile(
        r"rate[_\s]limit|too[_\s]many[_\s]requests|\b429\b",
        re.IGNORECASE,
    )),
    (FetchFailureReason.NETWORK_TIMEOUT, re.compile(
        r"timed?\s*out|connection[_\s](reset|aborted|refused)",
        re.IGNORECASE,
    )),
    (FetchFailureReason.PARSE_FAILED, re.compile(
        r"selector.*not[_\s]found|selector[_\s](missing)|parse[_\s]error|invalid[_\s]html",
        re.IGNORECASE,
    )),
    (FetchFailureReason.OPENCLI_DAEMON_DOWN, re.compile(
        r"daemon[_\s](not[_\s]running|down|stopped)|cannot[_\s]connect[_\s]to[_\s]daemon",
        re.IGNORECASE,
    )),
    (FetchFailureReason.CHROME_NOT_CONFIGURED, re.compile(
        r"browser-session\.json|chrome[_\s]not[_\s]configured|no[_\s]browser[_\s]profile",
        re.IGNORECASE,
    )),
    (FetchFailureReason.MEDIA_DOWNLOAD_FAILED, re.compile(
        r"media[_\s]download[_\s]failed|\b404\b.*media|asset[_\s]download",
        re.IGNORECASE,
    )),
]


def classify_failure(exc: Exception, ctx: FetchContext) -> FetchFailureReason:
    """把异常 + 上下文归类到 FetchFailureReason。

    优先用 http_status 判定（最确定），其次用 stderr/exc 文本匹配。
    """
    # 1. SQLite 错误单独判定（最确定）
    if isinstance(exc, IntegrityError):
        return FetchFailureReason.DB_WRITE_FAILED
    if isinstance(exc, OperationalError) and "disk" in str(exc).lower():
        return FetchFailureReason.DB_WRITE_FAILED

    # 2. http_status 判定
    if ctx.http_status == 401:
        return FetchFailureReason.PLATFORM_NOT_LOGGED_IN
    if ctx.http_status == 429:
        return FetchFailureReason.RATE_LIMITED
    if ctx.http_status is not None and 500 <= ctx.http_status < 600:
        # 5xx 暂时归类为网络超时（fetch 阶段的 5xx 通常是上游问题）
        return FetchFailureReason.NETWORK_TIMEOUT
    if ctx.http_status == 404:
        # 404 可能是媒体下载失败或页面不存在；归类到 MEDIA_DOWNLOAD_FAILED
        # （更精确的判定需要看 url 是否指向 media 域）
        return FetchFailureReason.MEDIA_DOWNLOAD_FAILED

    # 3. exit_code 判定
    if ctx.exit_code == 1 and "daemon" in ctx.stderr.lower():
        return FetchFailureReason.OPENCLI_DAEMON_DOWN

    # 4. 文本模式匹配
    text = f"{type(exc).__name__}: {exc} {ctx.stderr}"
    for reason, pattern in _PATTERNS:
        if pattern.search(text):
            return reason

    # 5. 兜底：无法分类
    raise ClassificationError(
        f"cannot classify failure: exc={exc!r} ctx={ctx}"
    )
