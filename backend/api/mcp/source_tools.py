"""Scope-checked source maintenance using the Web lifecycle and audit transactions."""
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import Context, FastMCP
from pydantic import ValidationError

from backend.api.mcp.offload import threaded
from backend.api.mcp.source_tool_payloads import create_payload, update_payload
from backend.application.usecases.source_activation import set_source_enabled as activate_source
from backend.application.usecases.source_change_actor import SourceChangeActor
from backend.application.usecases.source_configuration_lifecycle import (
    DuplicateSourceError, ImmutableSourceIdentityError, SourceNotFoundError,
    create_source_configuration, update_source_configuration,
)
from backend.domain.timestamps import utc_now_seconds_z
from backend.infrastructure.security.plugin_auth import INTELLIGENCE_READ, SOURCES_WRITE
from backend.infrastructure.storage.sources import SourceRepository
from backend.read_models.source_agent import read_source_capabilities, read_source_changes
from backend.read_models.source_management import read_source_page

_WRITE_RULE = "只在用户明确要求时调用；改动前向用户复述将要做的修改。reason 必填，1–500字符，写入用户原话或概括。调研和系统捕获来源不可修改；需要删除时只能停用。"


def _editable(base_dir: Path, source_id: str) -> dict[str, Any]:
    source = SourceRepository(base_dir).get_source(source_id)
    if source is None:
        raise SourceNotFoundError(source_id)
    if source["source_scope"] != "maintained" or source["raw_metadata"].get("system_managed"):
        raise SourceNotEditableError()
    return source


class SourceNotEditableError(ValueError):
    pass


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, SourceNotEditableError):
        return {"error": "source_not_editable", "message": "调研来源与系统捕获来源不可修改"}
    if isinstance(exc, SourceNotFoundError):
        return {"error": "source_not_found", "message": "来源不存在"}
    if isinstance(exc, DuplicateSourceError):
        return {"error": "duplicate_source", "message": "来源已存在", "existing_source_id": exc.existing_source_id}
    if isinstance(exc, ImmutableSourceIdentityError):
        return {"error": "immutable_source_identity", "message": "平台、来源类型与标识不可修改", "fields": list(exc.fields)}
    if isinstance(exc, ValidationError):
        return {"error": "invalid_source_configuration", "message": "来源参数校验失败",
                "details": exc.errors(include_url=False, include_context=False, include_input=False)}
    return {"error": "invalid_source_configuration", "message": "来源配置无效", "details": str(exc)}


def _pagination_error(limit: int, offset: int) -> dict[str, Any] | None:
    if not 1 <= limit <= 100 or offset < 0:
        return {"error": "invalid_pagination", "message": "limit 必须为1–100，offset 必须非负"}
    return None


def register_source_tools(server: FastMCP, base_dir: Path, require_scope: Callable) -> None:
    @server.tool(description="要求 intelligence:read。新增或编辑前先查此注册表能力：可配置平台、来源类型、标识提示与选项、默认频率和条数上限；不含调研专用或捕获专用平台。")
    @threaded
    def get_source_capabilities(ctx: Context) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        return read_source_capabilities()

    @server.tool(description="要求 intelligence:read。编辑前用此工具查 source_id 与当前配置；只列持续来源。platform/enabled/query 可选，query 匹配名称、标识或 ID；limit 1–100（默认50），offset≥0。返回最近运行状态、时间和下次运行时间。")
    @threaded
    def list_sources(ctx: Context, platform: str | None = None, enabled: bool | None = None,
                     query: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        error = _pagination_error(limit, offset)
        if error:
            return error
        return read_source_page(base_dir, platform=platform, enabled=enabled, query=query, limit=limit, offset=offset)

    @server.tool(description="要求 sources:write（仅交互式 agent）。先查 get_source_capabilities。platform/source_type/identifier 按能力表；name 可选；options 可含 cadence_hours、max_items_per_run、enabled、notes、raw_metadata（Web 可编辑的媒体、订阅、关键词或平台选项，不接受内部执行配置）。频率和条数限能力表范围；新建与 Web 同样校验并提交 durable queue，返回 task_id。" + _WRITE_RULE)
    @threaded
    def create_source(platform: str, source_type: str, identifier: str, reason: str, ctx: Context,
                      name: str | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
        auth = require_scope(ctx, SOURCES_WRITE)
        try:
            actor = SourceChangeActor("mcp", auth.token_id, reason)
            payload = create_payload(platform, source_type, identifier, name, options)
            return create_source_configuration(base_dir, payload, now=utc_now_seconds_z(), actor=actor)
        except (ValueError, SourceNotFoundError) as exc:
            return _error(exc)

    @server.tool(description="要求 sources:write（仅交互式 agent）。先 list_sources，再传 source_id 和 changes；允许 display_name、notes、cadence_hours、max_items_per_run、enabled、raw_metadata（Web 可编辑选项）；平台、来源类型、标识不可变。部分字段合并当前配置后走 Web 更新用例；频率与条数见 get_source_capabilities。" + _WRITE_RULE)
    @threaded
    def update_source(source_id: str, reason: str, changes: dict[str, Any], ctx: Context) -> dict[str, Any]:
        auth = require_scope(ctx, SOURCES_WRITE)
        try:
            actor = SourceChangeActor("mcp", auth.token_id, reason)
            payload = update_payload(_editable(base_dir, source_id), changes)
            return update_source_configuration(base_dir, source_id, payload, now=utc_now_seconds_z(), actor=actor)
        except (ValueError, SourceNotFoundError) as exc:
            return _error(exc)

    @server.tool(description="要求 sources:write（仅交互式 agent）。先 list_sources 获取 source_id；enabled=true 启用，false 停用，不删除数据，不主动提交采集任务，沿用现有调度规则。" + _WRITE_RULE)
    @threaded
    def set_source_enabled(source_id: str, enabled: bool, reason: str, ctx: Context) -> dict[str, Any]:
        auth = require_scope(ctx, SOURCES_WRITE)
        try:
            actor = SourceChangeActor("mcp", auth.token_id, reason)
            _editable(base_dir, source_id)
            return activate_source(base_dir, source_id, enabled, actor=actor)
        except (ValueError, SourceNotFoundError) as exc:
            return _error(exc)

    @server.tool(description="要求 intelligence:read。来源修改后可查审计：source_id 可选；limit 1–100（默认50），offset≥0；按时间倒序返回动作、Token ID、用户请求和前后配置版本。")
    @threaded
    def list_source_changes(ctx: Context, source_id: str | None = None,
                            limit: int = 50, offset: int = 0) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        error = _pagination_error(limit, offset)
        return error or read_source_changes(base_dir, source_id, limit=limit, offset=offset)
