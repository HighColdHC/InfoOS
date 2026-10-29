"""Scope-checked collection tools using the existing durable workflows."""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from mcp.server.fastmcp import Context, FastMCP
from pydantic import ValidationError

from backend.api.mcp.offload import threaded
from backend.api.routes.capture_schemas import CaptureRequestV1
from backend.api.schemas.research import ResearchRunBody
from backend.application.usecases.agent_capture import find_existing_capture, prepare_agent_capture
from backend.domain.capture_links import extract_capture_url
from backend.application.usecases.capture_policy import capture_limit_error, has_unsafe_capture_url
from backend.application.usecases.manual_capture import (
    CaptureDailyLimitExceeded, CaptureIdempotencyConflict, read_manual_capture, submit_manual_capture,
)
from backend.application.workflows.research import create_research_run
from backend.domain import agent_collect
from backend.domain.research import ResearchRequestError
from backend.infrastructure.security.capture_rate_limit import admit_capture_request
from backend.infrastructure.security.plugin_auth import COLLECT_WRITE, INTELLIGENCE_READ
from backend.read_models.collect_capabilities import read_collect_capabilities
from backend.read_models.research_runs import decode_research_cursor, read_research_run


def register_collect_tools(server: FastMCP, base_dir: Path, require_scope: Callable,
                           card_link: Callable) -> None:
    @server.tool(description="要求 intelligence:read。先查此能力表，再发起调研或链接捕获，随后轮询进度；仅列注册表声明的调研平台及参数、限制，不代表全网或全历史。带 collect:write 时另返回本 Token 滚动24小时剩余额度。")
    @threaded
    def get_collect_capabilities(ctx: Context) -> dict[str, Any]:
        auth = require_scope(ctx, INTELLIGENCE_READ)
        return read_collect_capabilities(base_dir, auth.token_id if COLLECT_WRITE in auth.scopes else None)

    @server.tool(description="要求 collect:write。先查 get_collect_capabilities。name 1–120字符；keywords 1–20个唯一词，每个最多100字符；platforms 1–10个不重复平台，每项含 platform、max_items（不超过能力上限）、可选 sort/options，总条数最多5000。date_range 可选，含 start_date/end_date（YYYY-MM-DD，含首尾）和 IANA timezone；RSS 必须提供 options.source_ids。idempotency_key 可选（1–200字符），重试复用同 key。提交只入 durable queue，不改常驻来源；发起后用 get_research_run 轮询。")
    @threaded
    def start_research(name: str, keywords: list[str], platforms: list[dict[str, Any]], ctx: Context,
                       date_range: dict[str, Any] | None = None,
                       idempotency_key: str | None = None) -> dict[str, Any]:
        auth = require_scope(ctx, COLLECT_WRITE)
        try:
            body = ResearchRunBody.model_validate({
                "name": name, "keywords": keywords, "platforms": platforms, "date_range": date_range,
            })
            run_id, replayed = create_research_run(
                base_dir, payload=body.model_dump(mode="json"),
                idempotency_key=f"mcp-{uuid4()}" if idempotency_key is None else idempotency_key,
                origin={"kind": "mcp", "token_id": auth.token_id},
                daily_limit=agent_collect.MCP_RESEARCH_DAILY_LIMIT,
            )
        except ValidationError as exc:
            return {"error": "invalid_request", "message": "调研参数校验失败",
                    "details": exc.errors(include_url=False, include_context=False, include_input=False)}
        except ResearchRequestError as exc:
            return {"error": exc.code, "message": exc.message, **exc.details}
        detail = read_research_run(base_dir, run_id)
        return {"research_run_id": run_id, "replayed": replayed,
                "research_run": detail["research_run"], "hint": "用 get_research_run 轮询"}

    @server.tool(description="要求 intelligence:read。发起调研后用 research_run_id 轮询平台运行状态、覆盖缺口和结果卡；item_limit 1–100，默认25，cursor 用上页 next_cursor。结果页不含正文，用 get_card / get_cards 读全文。")
    @threaded
    def get_research_run(research_run_id: str, ctx: Context, item_limit: int = 25,
                         cursor: str | None = None) -> dict[str, Any]:
        require_scope(ctx, INTELLIGENCE_READ)
        if not 1 <= item_limit <= 100:
            return {"error": "invalid_request", "message": "item_limit 必须为1–100"}
        try:
            decode_research_cursor(cursor)
            result = read_research_run(base_dir, research_run_id, item_limit=item_limit, cursor=cursor)
        except ValueError as exc:
            return {"error": "invalid_cursor", "message": str(exc)}
        if result is None:
            return {"error": "research_run_not_found", "message": "调研批次不存在"}
        # The read model projects only card references, never long bodies.
        result["item_page"]["items"] = [
            {**item, "deep_link": card_link(item["card_id"])} for item in result["item_page"]["items"]
        ]
        return {**result, "hint": "用 get_card / get_cards 读取结果卡全文"}

    @server.tool(description="要求 collect:write。先查 get_collect_capabilities；url 可直接粘贴分享文本，提取第一个公开 http(s) 链接（最多4096字节），只支持 url_only；提交前会查重；已采集直接返回卡片，需要重采时传 refresh=true（默认false）；处理中的同身份捕获不重复入队。title 可选，最多2048字节。client_capture_id 可选，1–128字符，仅字母数字及 ._:-，首字符为字母或数字；重试复用同 ID。返回回执，提交后用 get_capture 轮询，再用 get_card / get_cards 读卡。")
    @threaded
    def capture_url(url: str, ctx: Context, title: str = "",
                    client_capture_id: str | None = None, refresh: bool = False) -> dict[str, Any]:
        auth = require_scope(ctx, COLLECT_WRITE)
        url = extract_capture_url(url)
        if not url:
            return {"error": "invalid_capture_request", "message": "未找到 HTTP(S) 链接"}
        try:
            body = CaptureRequestV1.model_validate({
                "schema": "infoos.capture-request.v1",
                "client_capture_id": f"mcp-{uuid4()}" if client_capture_id is None else client_capture_id,
                "url": url, "title": title, "captured_at": datetime.now(timezone.utc),
                "capture_mode": "url_only", "client": {"name": "infoos-mcp", "version": "1"},
            })
        except ValidationError:
            return {"error": "invalid_capture_request", "message": "捕获参数不符合 infoos.capture-request.v1"}
        payload = body.model_dump(mode="json", by_alias=True)
        limit_error = capture_limit_error(payload)
        if limit_error:
            return {"error": limit_error, "message": "捕获字段超过服务器上限"}
        if has_unsafe_capture_url(payload):
            return {"error": "unsafe_capture_url", "message": "捕获 URL 必须是公开 HTTP(S) 链接"}
        resolution = prepare_agent_capture(payload["url"])
        payload["url"] = resolution["resolved_url"]
        limit_error = capture_limit_error(payload)
        if limit_error:
            return {"error": limit_error, "message": "解析后的捕获字段超过服务器上限", **resolution}
        if not refresh:
            existing = find_existing_capture(base_dir, payload["url"])
            if existing:
                return {**existing, **resolution,
                        **({"deep_link": card_link(existing["card_id"])} if existing.get("card_id") else {})}
        if not admit_capture_request(auth.token_id, "mcp"):
            return {"error": "capture_rate_limited", "message": "捕获请求过于频繁，请稍后重试", **resolution}
        try:
            submission = submit_manual_capture(
                base_dir, token_id=auth.token_id, payload=payload,
                daily_limit=agent_collect.MCP_CAPTURE_DAILY_LIMIT,
                idempotency_ignore_captured_at=True, deduplicate=not refresh,
            )
        except CaptureIdempotencyConflict as exc:
            return {"error": "idempotency_conflict", "message": str(exc), **resolution}
        except CaptureDailyLimitExceeded as exc:
            return {"error": "daily_limit_exceeded", "message": str(exc),
                    "limit": exc.limit, "window_hours": exc.window_hours, **resolution}
        receipt = submission.receipt
        return {**receipt, **resolution, **({"deep_link": card_link(receipt["card_id"])} if receipt.get("card_id") else {})}

    @server.tool(description="要求 collect:write。提交链接后以 capture_id 轮询回执与处理状态，只能读取本 Token 的捕获；有 card_id 时附阅读链接，用 get_card / get_cards 读全文（需要 intelligence:read）。")
    @threaded
    def get_capture(capture_id: str, ctx: Context) -> dict[str, Any]:
        auth = require_scope(ctx, COLLECT_WRITE)
        receipt = read_manual_capture(base_dir, capture_id=capture_id, token_id=auth.token_id)
        if receipt is None:
            return {"error": "capture_not_found", "message": "捕获回执不存在"}
        return {**receipt, **({"deep_link": card_link(receipt["card_id"])} if receipt.get("card_id") else {})}
