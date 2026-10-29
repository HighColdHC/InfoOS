"""Registry-backed agent capabilities and persistent rolling budget usage."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NotRequired, TypedDict

from backend.domain.capture_links import SHORT_LINK_HOSTS
from backend.domain import agent_collect
from backend.domain.sources.registry import capability_catalog
from backend.domain.sources.registry_types import ResearchCapabilityItem
from backend.infrastructure.storage.capture_receipts import CaptureReceiptRepository
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.research_runs import count_token_research_runs


class ResearchCapability(ResearchCapabilityItem):
    platform: str
    label: str


class CaptureCapability(TypedDict):
    inputs: list[str]
    capture_mode: str
    short_link_hosts: list[str]
    description: str


class Budget(TypedDict):
    limit: int
    remaining: int


class Quota(TypedDict):
    window_hours: int
    research: Budget
    capture: Budget


class CollectCapabilities(TypedDict):
    research: list[ResearchCapability]
    capture: CaptureCapability
    workflow: str
    quota: NotRequired[Quota]


def read_collect_capabilities(base_dir: Path, token_id: str | None = None) -> CollectCapabilities:
    result: CollectCapabilities = {
        "research": [
            {"platform": item["platform"], "label": item["label"], **item["research"]}
            for item in capability_catalog() if item["research"] is not None
        ],
        "capture": {"inputs": ["public_http_url", "public_https_url", "share_text"], "capture_mode": "url_only",
                    "short_link_hosts": sorted(host for hosts in SHORT_LINK_HOSTS.values() for host in hosts),
                    "description": "可直接粘贴分享文本，提取第一个链接；提交前会查重，已采集返回卡片，处理中不重复入队且均不占额度。需要重采时传 refresh=true。短链解析失败仍用原链接提交并标记原因。"},
        "workflow": "先查能力，再发起调研或捕获；用 get_research_run / get_capture 轮询，用 get_card / get_cards 读卡。仅覆盖注册表声明的能力，不代表全网或全历史。",
    }
    if token_id is not None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        since = now - timedelta(hours=agent_collect.MCP_COLLECT_WINDOW_HOURS)
        bounds = {"since": since.isoformat().replace("+00:00", "Z"),
                  "until": now.isoformat().replace("+00:00", "Z")}
        research_used = capture_used = 0
        db_path = resolve_sqlite_path(base_dir)
        if db_path.is_file():
            with connect_existing(db_path) as conn:
                research_used = count_token_research_runs(conn, token_id, **bounds)
                capture_used = CaptureReceiptRepository(base_dir).count_for_token(
                    token_id, connection=conn, **bounds,
                )
        result["quota"] = {
            "window_hours": agent_collect.MCP_COLLECT_WINDOW_HOURS,
            "research": {"limit": agent_collect.MCP_RESEARCH_DAILY_LIMIT,
                         "remaining": max(0, agent_collect.MCP_RESEARCH_DAILY_LIMIT - research_used)},
            "capture": {"limit": agent_collect.MCP_CAPTURE_DAILY_LIMIT,
                        "remaining": max(0, agent_collect.MCP_CAPTURE_DAILY_LIMIT - capture_used)},
        }
    return result
