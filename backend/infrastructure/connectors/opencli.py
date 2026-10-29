from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from backend.infrastructure.connectors.base import FetchResult
from backend.domain.sources.acquisition import normalize_acquisition
from backend.domain.sources.monetization_prefilter import run_evidence
from backend.domain.models.raw_item import RawAuthor, RawItem, RawTimestamps
from backend.infrastructure.opencli.commands import pin_adapter_lifecycle
from backend.infrastructure.opencli.runtime import resolve_opencli_executable
from backend.infrastructure.opencli.tab_lease import normalize_opencli_site, opencli_tab_lease
from backend.infrastructure.browser.runtime_policy import browser_mutation_block_reason
from backend.infrastructure.security.redaction import redact_secrets, summarize_subprocess_output
from backend.domain.sources.registry import opencli_tab_policy_platforms
from backend.infrastructure.storage.raw_media import RawMediaStore


_YOUTUBE_WATCH_URL_RE = re.compile(r"https://(?:www\.)?youtube\.com/watch\?v=[^\s|]+")

_DEFAULT_ITEMS_KEYS = ("items", "results", "data", "tweets", "posts")
_STALE_PAGE_IDENTITY_MARKERS = (
    "stale page identity",
    "page not found:",
    "no tab with id:",
    "detached while handling command",
)
_TAB_POLICY_PLATFORMS = opencli_tab_policy_platforms()
_DEFAULT_FIELD_ALIASES = {
    "id": ("id", "raw_item_id", "guid", "url", "link", "href", "permalink"),
    "title": ("title", "name", "headline", "text", "content"),
    "url": ("url", "link", "href", "source_url", "permalink"),
    "text": ("text", "content", "body", "description", "summary", "title"),
    "author_name": ("author.name", "authorName", "name", "author", "user.name", "creator.name"),
    "author_handle": ("author.username", "author.handle", "username", "author", "user.username", "user.handle"),
    "author_url": ("author.url", "user.url"),
    "published_at": ("published_at", "created_at", "posted_at", "date", "time", "timestamp"),
}


class OpenCLICommandError(RuntimeError):
    """OpenCLI process failure with a safe diagnostic summary."""

    def __init__(self, returncode: int, *, stderr_summary: str):
        super().__init__(f"OpenCLI command failed with exit code {returncode}")
        self.stderr_summary = stderr_summary


def _youtube_channel_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenCLI YouTube channel field/value rows to video records."""
    videos: list[dict[str, Any]] = []
    metadata: dict[str, str] = {}

    for record in records:
        field = str(record.get("field") or "").strip()
        value = str(record.get("value") or "").strip()
        if not field or not value or field == "---":
            continue

        match = _YOUTUBE_WATCH_URL_RE.search(value)
        if match:
            url = match.group(0)
            parts = [p.strip() for p in value.split("|")]
            # "duration | views | published | url" carries a date; the shorter
            # "duration | channel | url" row does not, so never take the URL.
            published = parts[2] if len(parts) > 3 else None
            videos.append({
                "id": url,
                "title": field,
                "url": url,
                "text": field,
                "channel": metadata.get("name") or metadata.get("handle") or "",
                "published_at": published,
                "_opencli_record": record,
            })
        else:
            metadata[field] = value

    return videos


@dataclass
class OpenCLIConnector:
    connector_id: str
    command: list[str]
    executable: str = "opencli"
    source_platform: str = "opencli"
    source_type: str = "external_record"
    items_path: str | None = None
    field_mapping: dict[str, str] = field(default_factory=dict)
    metadata_mapping: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 60
    result_order: str = "oldest_first"
    keep_alive: bool = True  # Deprecated config compatibility; explicit leases own tab lifetime.
    use_browser_window: bool = True
    detail_command_template: list[str] | None = None
    detail_max_items: int | None = None
    xhs_prefilter_rules: dict[str, Any] | None = None
    data_dir: Path | None = None
    discovery_evidence: dict[str, Any] = field(
        init=False,
        default_factory=lambda: {"availability": "unavailable"},
    )
    prefilter_evidence: dict[str, Any] = field(init=False, default_factory=dict)

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        if self.use_browser_window and (reason := browser_mutation_block_reason()):
            raise RuntimeError(reason)
        lease_site = self._lease_site()
        with opencli_tab_lease(
            lease_site,
            executable=self.executable,
            close_tabs=False,
        ) if lease_site else nullcontext():
            return self._fetch_new_items_in_lease(since_cursor)

    def _fetch_new_items_in_lease(self, since_cursor: str | None) -> FetchResult:
        payload = self._load_payload()
        extracted_records = self._extract_records(payload)
        if self.xhs_prefilter_rules is not None and self.source_platform == "xiaohongshu":
            from backend.domain.sources.xiaohongshu_prefilter import screen_record, run_evidence as xhs_evidence
            extracted_records = [screen_record(self._enrich_record_with_detail(record, index), self.xhs_prefilter_rules)
                                 for index, record in enumerate(extracted_records)]
            from backend.infrastructure.connectors.xiaohongshu_profiles import enrich_profiles
            extracted_records = enrich_profiles(extracted_records, self.xhs_prefilter_rules, self._load_payload)
            self.prefilter_evidence = xhs_evidence(extracted_records, self.xhs_prefilter_rules)
        else:
            self.prefilter_evidence = (run_evidence(extracted_records)
                                   if self._is_douyin_feed() and _has_flag(self.command, "--prefilter") else {})
        visible_count = len(extracted_records)
        if self._is_douyin_feed():
            extracted_records = [record for record in extracted_records
                                 if str(record.get("is_live")).lower() not in {"true", "1"}]
            # Missing URLs on ordinary videos still have a stable, canonical identity.
            extracted_records = [
                {**record, "url": record.get("url") or ("https://www.douyin.com/video/" + str(record["aweme_id"])
                                                      if record.get("aweme_id") else "")}
                for record in extracted_records
            ]
        discovery_items = [self._to_raw_item(record) for record in extracted_records]
        self.discovery_evidence = {
            "executor": "opencli",
            "session_alias": self._lease_site() or self.source_platform,
            "sanitized_arguments": _safe_command_arguments(self.command),
            "links": [item.source_url for item in discovery_items if item.source_url][:50],
            "discovered_link_count": len(discovery_items),
            "availability": "available",
        }

        if self.result_order == "snapshot":
            previous_ids = _snapshot_cursor_ids(since_cursor)
            selected = [
                (record, item)
                for record, item in zip(extracted_records, discovery_items, strict=True)
                if item.raw_item_id not in previous_ids
            ]
        elif since_cursor is None:
            selected = list(zip(extracted_records, discovery_items, strict=True))
        else:
            cursor_index = next(
                (idx for idx, item in enumerate(discovery_items) if item.raw_item_id == since_cursor),
                None,
            )
            if cursor_index is None:
                # Stale, corrupt, or manually reset cursor: recover by fetching visible batch
                selected = list(zip(extracted_records, discovery_items, strict=True))
            elif self.result_order == "newest_first":
                selected = list(
                    zip(
                        extracted_records[:cursor_index],
                        discovery_items[:cursor_index],
                        strict=True,
                    )
                )
            else:
                selected = list(
                    zip(
                        extracted_records[cursor_index + 1:],
                        discovery_items[cursor_index + 1:],
                        strict=True,
                    )
                )
        cursor_items = [item for _, item in selected]
        if self.source_platform == "zsxq":
            # A visible older post can be digested after the source cursor passed
            # it. Carry that observation through the durable metadata merge.
            selected_ids = {item.raw_item_id for _, item in selected}
            selected.extend((record, item) for record, item in zip(extracted_records, discovery_items, strict=True)
                            if item.raw_item_id not in selected_ids and item.raw_metadata.get("is_digest") is True)
        raw_items: list[RawItem] = []
        for index, (record, discovery_item) in enumerate(selected):
            enriched = record if self.xhs_prefilter_rules is not None else self._enrich_record_with_detail(record, index)
            item = self._to_raw_item(enriched)
            if item.raw_item_id != discovery_item.raw_item_id:
                item = item.model_copy(update={"raw_item_id": discovery_item.raw_item_id})
            raw_items.append(item)

        if self.result_order == "snapshot":
            next_cursor = _snapshot_cursor(discovery_items)
        elif not raw_items:
            next_cursor = since_cursor
        elif self.result_order == "newest_first":
            next_cursor = raw_items[0].raw_item_id
        else:
            next_cursor = raw_items[-1].raw_item_id

        if self.source_platform == "zsxq" and self.result_order != "snapshot":
            next_cursor = (cursor_items[0 if self.result_order == "newest_first" else -1].raw_item_id
                           if cursor_items else since_cursor)

        new_count = len(raw_items)
        return FetchResult(
            items=raw_items,
            next_cursor=next_cursor,
            visible_count=visible_count,
            new_count=new_count,
            dedup_skipped_count=max(0, visible_count - new_count),
            acquisition=normalize_acquisition(payload.get("acquisition")) if isinstance(payload, dict) else None,
        )

    def _is_douyin_feed(self) -> bool:
        return self.source_platform == "douyin" and self.command[:2] == ["douyin", "feed"]

    def _load_payload(self, command_template: list[str] | None = None) -> Any:
        command = [resolve_opencli_executable(self.executable), *self._command_with_json_format(command_template)]

        screening = command[1:3] in (["xiaohongshu", "screening-profiles"], ["xiaohongshu", "screening-note"])
        command_timeout = (max(self.timeout_seconds, 600)
                           if command[1:3] == ["xiaohongshu", "screening-profiles"] else self.timeout_seconds)
        if self.source_platform == "zsxq":
            from backend.infrastructure.connectors.zsxq_runtime import command_timeout as zsxq_timeout
            command_timeout = max(command_timeout, zsxq_timeout(command_template or self.command))

        def run_command() -> subprocess.CompletedProcess[str]:
            command_env = None
            x_search = self.source_platform == "x" and command[1:3] == ["twitter", "search"]
            if self.source_platform == "zsxq" or self._is_douyin_feed() or screening or x_search:
                command_env = os.environ.copy()
                command_env["OPENCLI_BROWSER_COMMAND_TIMEOUT"] = str(command_timeout)
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=command_timeout,
                env=command_env,
                check=False,
            )

        try:
            try:
                result = run_command()
            except subprocess.TimeoutExpired:
                if self.source_platform != "xiaohongshu" or screening:
                    raise
                # XHS navigation may hit the extension's 15-second page-load
                # ceiling even though the page finishes before the connector
                # deadline. Retry before the outer tab lease closes.
                result = run_command()
            if (result.returncode != 0 and not screening and not self._is_douyin_feed()
                    and (command_template or self.command)[:2] != ["zsxq", "probe"]
                    and _looks_like_stale_page_identity(result.stdout, result.stderr)):
                result = run_command()
        except FileNotFoundError as exc:
            raise RuntimeError(f"OpenCLI executable not found: {self.executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"OpenCLI command timed out after {command_timeout}s") from exc

        if result.returncode != 0:
            raise OpenCLICommandError(
                result.returncode,
                stderr_summary=summarize_subprocess_output(result.stdout, result.stderr),
            )

        stdout = result.stdout.strip()
        if not stdout:
            return []

        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenCLI command did not return valid JSON") from exc

    def _command_with_json_format(self, command_template: list[str] | None = None) -> list[str]:
        command = [str(part) for part in (command_template or self.command)]
        # If the user / site-session config already pinned a --window mode,
        # respect it. Otherwise use background: current OpenCLI accepts only
        # foreground/background, and older hidden-mode commands now fail.
        flags: list[str] = []
        if self.use_browser_window and not _has_flag(command, "--window"):
            flags.append("--window")
            flags.append("background")
        if any(part in {"--format", "-f", "--json"} or part.startswith("--format=") for part in command):
            formatted = [*command, *flags]
        else:
            formatted = [*command, "--format", "json", *flags]
        if self._lease_site():
            if self.source_platform == "xiaohongshu":
                return pin_adapter_lifecycle(
                    formatted,
                    site_session="persistent",
                    keep_tab=False,
                )
            return pin_adapter_lifecycle(formatted)
        return formatted

    def _lease_site(self) -> str | None:
        if not self.use_browser_window or self.source_platform not in _TAB_POLICY_PLATFORMS:
            return None
        return normalize_opencli_site(self.source_platform)

    def _enrich_record_with_detail(self, record: dict[str, Any], index: int) -> dict[str, Any]:
        if not self.detail_command_template:
            return record
        # The XHS prefilter enriches every record before selection; never open a note twice.
        if "_opencli_detail_record" in record or "_opencli_detail_error" in record:
            return record
        if self.detail_max_items is not None and index >= int(self.detail_max_items):
            return record
        command = self._render_detail_command(record)
        if command is None:
            return record
        try:
            detail_payload = self._load_payload(command)
        except Exception as exc:  # noqa: BLE001 - OpenCLI subprocess boundary; one detail failure must not fail the fetch
            return {
                **record,
                "_opencli_detail_error": {
                    "command": command,
                    "error": str(exc),
                },
            }
        detail_record = _detail_payload_to_record(detail_payload)
        if not detail_record:
            return record
        return {**record, **detail_record, "_opencli_detail_record": redact_secrets(detail_payload)}

    def _render_detail_command(self, record: dict[str, Any]) -> list[str] | None:
        rendered: list[str] = []
        for part in self.detail_command_template:
            value = str(part)
            for placeholder in re.findall(r"{([^{}]+)}", value):
                replacement = _get_path(record, placeholder)
                if replacement in (None, ""):
                    return None
                value = value.replace("{" + placeholder + "}", str(replacement))
            rendered.append(value)
        return rendered

    def _extract_records(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            records = [record for record in payload if isinstance(record, dict)]
            # YouTube channel output is field/value rows, not video objects
            if self.source_platform == "youtube":
                youtube_records = _youtube_channel_records(records)
                if youtube_records:
                    return youtube_records
            return records

        if not isinstance(payload, dict):
            return []

        if self.items_path:
            value = _get_path(payload, self.items_path)
            if isinstance(value, list):
                return [record for record in value if isinstance(record, dict)]
            if isinstance(value, dict):
                return [value]
            return []

        for key in _DEFAULT_ITEMS_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [record for record in value if isinstance(record, dict)]
            if isinstance(value, dict):
                nested = self._extract_records(value)
                if nested:
                    return nested

        return [payload]

    def _to_raw_item(self, record: dict[str, Any]) -> RawItem:
        raw_item_id = str(self._mapped_value(record, "id") or _stable_record_id(record))
        source_url = _string_or_none(self._mapped_value(record, "url")) or ""
        text = _string_or_none(self._mapped_value(record, "text")) or ""
        published_at = _string_or_none(self._mapped_value(record, "published_at"))
        captured_at = utc_now_micro_z()

        record = RawMediaStore(self.data_dir).externalize(record)

        raw_metadata = {
            "opencli_command": self.command,
            "opencli_record": redact_secrets(record),
        }
        for metadata_key, source_path in self.metadata_mapping.items():
            value = _get_path(record, source_path)
            if value not in (None, ""):
                raw_metadata[metadata_key] = value

        return RawItem(
            raw_item_id=raw_item_id,
            connector_id=self.connector_id,
            source_platform=self.source_platform,
            source_type=self.source_type,
            source_url=source_url,
            captured_at=captured_at,
            raw_text=text,
            raw_author=RawAuthor(
                name=_string_or_none(self._mapped_value(record, "author_name")),
                handle=_string_or_none(self._mapped_value(record, "author_handle")),
                url=_string_or_none(self._mapped_value(record, "author_url")),
            ),
            raw_timestamps=RawTimestamps(published_at=published_at),
            raw_metadata=raw_metadata,
        )

    def _mapped_value(self, record: dict[str, Any], field_name: str) -> Any:
        configured_path = self.field_mapping.get(field_name)
        failed_feishu_body = (field_name == "text" and record.get("content_source") == "feishu"
                              and record.get("content_source_status") in {"login_required", "empty"})
        if configured_path and not (failed_feishu_body and configured_path == "title"):
            value = _get_path(record, configured_path)
            if value not in (None, ""):
                return value

        for alias in _DEFAULT_FIELD_ALIASES[field_name]:
            if failed_feishu_body and alias == "title":
                continue
            value = _get_path(record, alias)
            if value not in (None, ""):
                return value
        return None


def _get_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _safe_command_arguments(command: list[str]) -> list[str]:
    secret_flags = {
        "--authorization", "--cookie", "--password", "--secret", "--token",
        "--api-key", "--api_key", "--signature", "--sig",
    }
    result: list[str] = []
    redact_next = False
    for raw in command[:30]:
        value = str(raw)
        if redact_next:
            result.append("[REDACTED]")
            redact_next = False
            continue
        flag = value.split("=", 1)[0].lower()
        if flag in secret_flags:
            if "=" in value:
                result.append(flag + "=[REDACTED]")
            else:
                result.append(value)
                redact_next = True
            continue
        result.append(str(redact_secrets(value))[:160])
    return result


def _detail_payload_to_record(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, list):
        return {}
    record: dict[str, Any] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        if not field or field == "---":
            continue
        record[field] = item.get("value")
    return record


def _stable_record_id(record: dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _snapshot_cursor(items: list[RawItem]) -> str:
    return "snapshot:" + json.dumps(
        [item.raw_item_id for item in items],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _snapshot_cursor_ids(value: str | None) -> set[str]:
    if not value or not value.startswith("snapshot:"):
        return set()
    try:
        loaded = json.loads(value.removeprefix("snapshot:"))
    except json.JSONDecodeError:
        return set()
    if not isinstance(loaded, list):
        return set()
    return {str(item) for item in loaded if isinstance(item, str)}


def _looks_like_stale_page_identity(stdout: str | bytes | None, stderr: str | bytes | None) -> bool:
    text = _combined_output(stdout, stderr).lower()
    return any(marker in text for marker in _STALE_PAGE_IDENTITY_MARKERS)


def _combined_output(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    parts = [_coerce_text(stdout).strip(), _coerce_text(stderr).strip()]
    return "\n".join(part for part in parts if part).strip()


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _has_flag(command: list[str], flag: str) -> bool:
    return any(part == flag or part.startswith(f"{flag}=") for part in command)


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
