"""Notification 文案：全部稳定失败分类 → 用户提示（spec §10.2）。

文案修改前必须确认：spec §10.2 是单一真相源。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.domain.failures.taxonomy import FetchFailureReason


class NotificationLevel(str, Enum):
    SILENT = "silent"  # 无通知，写日志
    DESKTOP = "desktop"  # macOS 通知
    DESKTOP_AND_WEB = "desktop_and_web"  # macOS + Web UI


@dataclass(frozen=True)
class Notification:
    level: NotificationLevel
    copy: str  # 用户看到的话术


NOTIFICATION_COPY: dict[FetchFailureReason, Notification] = {
    FetchFailureReason.CHROME_NOT_CONFIGURED: Notification(
        NotificationLevel.DESKTOP_AND_WEB,
        "InfoOS 需要专用 Chrome 才能采集。请在菜单栏 InfoOS.app → 'Set up browser'。",
    ),
    FetchFailureReason.OPENCLI_DAEMON_DOWN: Notification(
        NotificationLevel.SILENT,
        "OpenCLI daemon 未运行，已尝试自动重启。",
    ),
    FetchFailureReason.BROWSER_PAGE_LOAD_FAILED: Notification(
        NotificationLevel.SILENT,
        "平台页面加载失败，已自动重试。",
    ),
    FetchFailureReason.BROWSER_SESSION_RENDER_BLOCKED: Notification(
        NotificationLevel.DESKTOP_AND_WEB,
        "请检查平台登录状态和安全验证，完成后重试。",
    ),
    FetchFailureReason.PLATFORM_CHALLENGE_REQUIRED: Notification(
        NotificationLevel.DESKTOP_AND_WEB,
        "平台要求安全验证。请在 InfoOS 专用 Chrome 中完成验证码或安全验证。",
    ),
    FetchFailureReason.PLATFORM_NOT_LOGGED_IN: Notification(
        NotificationLevel.DESKTOP_AND_WEB,
        "平台登录态已过期。请在 InfoOS 专用 Chrome 中重新登录。",
    ),
    FetchFailureReason.SOURCE_UNAVAILABLE: Notification(
        NotificationLevel.DESKTOP_AND_WEB,
        "来源账号或订阅地址不存在或不可用，请核对并修改或停用该来源。",
    ),
    FetchFailureReason.OPENCLI_COMMAND_FAILED: Notification(
        NotificationLevel.SILENT,
        "OpenCLI 命令失败，已按指数退避自动重试。",
    ),
    FetchFailureReason.PARSE_FAILED: Notification(
        NotificationLevel.DESKTOP,
        "采集成功但解析异常。可能是 X / YouTube 页面结构变了。",
    ),
    FetchFailureReason.RATE_LIMITED: Notification(
        NotificationLevel.SILENT,
        "平台返回 429，已自动退避。",
    ),
    FetchFailureReason.NETWORK_TIMEOUT: Notification(
        NotificationLevel.SILENT,
        "网络超时，已自动重试。",
    ),
    FetchFailureReason.LLM_API_FAILED: Notification(
        NotificationLevel.SILENT,
        "LLM API 失败，已自动重试。",
    ),
    FetchFailureReason.LLM_CONTENT_REJECTED: Notification(
        NotificationLevel.DESKTOP,
        "AI 拒绝处理此内容（可能涉及敏感话题）。",
    ),
    FetchFailureReason.DB_WRITE_FAILED: Notification(
        NotificationLevel.SILENT,
        "数据库写入失败，已自动重试。",
    ),
    FetchFailureReason.MEDIA_DOWNLOAD_FAILED: Notification(
        NotificationLevel.SILENT,
        "媒体文件下载失败，已自动重试。",
    ),
    FetchFailureReason.VIDEO_TRANSCRIBE_FAILED: Notification(
        NotificationLevel.DESKTOP,
        "视频转录失败，可能是模型加载问题。",
    ),
    FetchFailureReason.OCR_FAILED: Notification(
        NotificationLevel.SILENT,
        "OCR/图片描述失败，已自动重试。",
    ),
    FetchFailureReason.DATA_EXPORT_FAILED: Notification(
        NotificationLevel.DESKTOP_AND_WEB,
        "数据导出失败。请检查本机存储空间后重试，并可在日志中查看详情。",
    ),
}


def get_notification(reason: FetchFailureReason) -> Notification:
    return NOTIFICATION_COPY[reason]
