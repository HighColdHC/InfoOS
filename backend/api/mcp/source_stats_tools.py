"""Source health evidence tools; never mutate source configuration."""
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import Context, FastMCP

from backend.api.mcp.offload import threaded
from backend.infrastructure.security.plugin_auth import INTELLIGENCE_READ
from backend.read_models.source_stats import get_source_stats as read_source_stats


def register_source_stats_tools(server: FastMCP, base_dir: Path, require_scope: Callable) -> None:
    @server.tool(description="读取来源体检证据，要求 intelligence:read。按首次进入时间 [since, until) 统计 active 卡片及每卡最新评估；另按 FETCH 完成时间统计 fetch_run_count、partial_run_count、partial_run_rate（部分/全部轮数，无轮次为 null）与最近部分轮次 latest_stop_reason，不据此改 verdict。since/until 为带时区 ISO 8601，默认30天前/现在，min_evaluated 为1–1000（默认10）。low_yield 只是向用户提议的依据，降频或停用来源必须先问用户；InfoOS 不会自动修改来源配置。")
    @threaded
    def get_source_stats(ctx: Context, since: str | None = None, until: str | None = None,
                         min_evaluated: int = 10) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        return read_source_stats(base_dir, since, until, min_evaluated)
