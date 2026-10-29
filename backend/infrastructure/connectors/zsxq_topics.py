"""ZSXQ topics plus bounded, metadata-only digest observations in one lease."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.domain.failures.connector_diagnostics import classify_connector_diagnostic
from backend.domain.sources.zsxq_recheck import digest_list_command, digest_recheck_command, digest_recheck_options
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.connectors.opencli import OpenCLIConnector
from backend.infrastructure.security.redaction import summarize_subprocess_output
from backend.infrastructure.storage.raw_items import RawItemRepository


@dataclass
class ZsxqTopicsConnector(OpenCLIConnector):
    base_dir: Path = Path(".")
    digest_recheck: dict[str, int] = field(default_factory=lambda: digest_recheck_options({}))
    digest_group_id: str = "1824528822"
    digest_backfill_window: dict[str, Any] | None = None
    digest_recheck_error: Exception | None = field(init=False, default=None)

    def _fetch_new_items_in_lease(self, since_cursor: str | None) -> FetchResult:
        self.digest_recheck_error = None
        window = self.digest_backfill_window
        if window:
            result = FetchResult(items=[], next_cursor=since_cursor)
            command = digest_list_command(self.digest_group_id, window["start_date"],
                                          window["end_date"], window["limit"])
            self.discovery_evidence = {"availability": "available", "discovered_link_count": 0}
        else:
            result = super()._fetch_new_items_in_lease(since_cursor)
            command = digest_recheck_command(self.digest_group_id, self.digest_recheck)
        if command is None:
            self.discovery_evidence["digest_recheck"] = {"status": "disabled"}
            return result
        evidence: dict[str, Any] = {"status": "success", "arguments": command,
                                    "listed_count": 0, "existing_count": 0, "new_count": 0}
        self.discovery_evidence["digest_recheck"] = evidence
        try:
            records = self._extract_records(self._load_payload(command))
        except Exception as exc:  # noqa: BLE001 - OpenCLI digest subprocess failures must preserve the primary fetch result
            if window:
                raise  # Backfill has no independent primary result to preserve.
            self._record_failure(evidence, exc)
            return result
        raw_repo = RawItemRepository(self.base_dir)
        selected_ids = {item.raw_item_id for item in result.items}
        for record in records[:int(command[command.index("--limit") + 1])]:
            item = self._to_raw_item({**record, "is_digest": True})
            evidence["listed_count"] += 1
            if item.raw_item_id in selected_ids:
                result.items = [old.model_copy(update={"raw_metadata": {**old.raw_metadata, "is_digest": True}})
                                if old.raw_item_id == item.raw_item_id else old for old in result.items]
                continue
            existing = raw_repo.zsxq_topic_exists(item)
            if existing:
                evidence["existing_count"] += 1
            elif window:
                evidence["missing_count"] = evidence.get("missing_count", 0) + 1
                continue
            else:
                try:
                    detail = self._extract_records(self._load_payload(
                        ["zsxq", "topic", item.raw_item_id, "--group_id", self.digest_group_id]))
                    if not detail:
                        raise RuntimeError("zsxq topic returned no detail")
                    item = self._to_raw_item({**record, **detail[0], "topic_id": item.raw_item_id, "is_digest": True})
                    evidence["new_count"] += 1
                except Exception as exc:  # noqa: BLE001 - One OpenCLI topic subprocess failure must not discard other digest observations
                    self._record_failure(evidence, exc)
                    continue
            item = item.model_copy(update={"raw_metadata": {**item.raw_metadata,
                "_zsxq_digest_observation": True, "_zsxq_digest_only_existing": existing or bool(window)}})
            result.items.append(item)
            selected_ids.add(item.raw_item_id)
        evidence["limit_reached"] = len(records) >= int(command[command.index("--limit") + 1])
        if window:
            result.visible_count = len(records)
        result.new_count = len(result.items)
        return result

    def _record_failure(self, evidence: dict[str, Any], exc: Exception) -> None:
        old = self.digest_recheck_error
        old_diagnostic = classify_connector_diagnostic(
            [{"message": getattr(old, "stderr_summary", None) or str(old)}], platform="zsxq") if old else None
        if not old_diagnostic or old_diagnostic.get("category") not in {"auth_required", "challenge_required", "chrome_not_configured"}:
            self.digest_recheck_error = exc
        evidence["status"] = "partial_failure"
        evidence.setdefault("errors", []).append(summarize_subprocess_output(
            None, getattr(exc, "stderr_summary", None) or str(exc)))
