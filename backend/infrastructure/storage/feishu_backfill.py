"""Bounded Feishu repair queries and updates without replacing card identities."""
from __future__ import annotations

from typing import Any

from backend.domain.card_payload import compact_card_metadata
from backend.domain.feishu_content import compact_feishu_evidence, feishu_metadata, keeps_previous_body, suspect_feishu_body
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads
from backend.infrastructure.storage.raw_items import RawItemRepository


class FeishuBackfillRepository(InfoOSRepository):
    def candidates(self, *, limit: int, after_card_id: str = "", only_suspect: bool = True,
                   include_complete: bool = False) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        cursor = after_card_id
        with self.connect_existing() as conn:
            while len(selected) < limit:
                rows = conn.execute(
                    """select c.card_id, c.content, c.raw_payload as card_payload,
                              r.source_url, r.raw_payload as item_payload,
                              coalesce((select json_extract(b.raw_payload, '$.read_attempts')
                                from card_content_blocks b
                                where b.block_id = 'block_' || c.card_id || '_external_doc_feishu'), 0) as read_attempts
                       from cards c join raw_items r
                         on r.connector_id = c.raw_connector_id and r.raw_item_id = c.raw_item_id
                       where c.source_platform = 'zsxq' and c.card_id > ?
                       order by c.card_id limit ?""",
                    (cursor, min(500, max(1, limit))),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    cursor = str(row["card_id"])
                    raw = loads(row["item_payload"], {})
                    evidence = feishu_metadata(raw)
                    if not evidence:
                        continue
                    current = feishu_metadata(loads(row["card_payload"], {})) or evidence
                    # Only explicit new full-read evidence can prove a historical fetched row complete.
                    proven = (current.get("content_source_status") == "fetched"
                              and current.get("content_source_completeness") == "complete")
                    suspect = suspect_feishu_body(row["content"])
                    # include_complete re-reads rows an earlier reader version reported complete.
                    if proven and not include_complete:
                        continue
                    if not proven and only_suspect and not suspect and current.get("content_source_status") == "fetched":
                        continue
                    selected.append({"card_id": cursor,
                                     "url": str(current.get("content_source_url") or evidence.get("content_source_url") or ""),
                                     "current_length": len(row["content"]),
                                     "read_generation": int(row["read_attempts"])})
                    if len(selected) >= limit:
                        break
        return selected

    def publish_read(self, card_id: str, *, content: str, evidence: dict[str, Any]) -> None:
        """Preserve version/hash until CARD_FINALIZE, revision the raw input atomically."""
        evidence = compact_feishu_evidence(evidence)
        with self.connect() as conn:
            row = conn.execute(
                "select content, raw_payload, raw_connector_id, raw_item_id from cards where card_id = ?",
                (card_id,),
            ).fetchone()
            if row is None:
                return
            # Also protect against a concurrent longer update after browser I/O.
            if keeps_previous_body(len(content), len(row["content"]),
                                   complete=evidence.get("content_source_completeness") == "complete"):
                content = row["content"]
                evidence = {**evidence, "content_source_status": "partial",
                            "content_source_completeness": "partial",
                            "reason": "external_doc_shorter_result"}
            card_payload = loads(row["raw_payload"], {})
            card_payload["external_doc_read"] = compact_card_metadata(evidence)
            if "normalized_content" in card_payload:  # Keep historical payloads in sync.
                card_payload["normalized_content"] = content
            conn.execute("update cards set content = ?, raw_payload = ? where card_id = ?",
                         (content, dumps(card_payload), card_id))
            raw = conn.execute(
                "select raw_payload, run_id from raw_items where connector_id = ? and raw_item_id = ?",
                (row["raw_connector_id"], row["raw_item_id"]),
            ).fetchone()
            if raw:
                item = RawItem.model_validate(loads(raw["raw_payload"], {}))
                metadata = {**item.raw_metadata, **evidence, "external_doc_read": evidence}
                item = item.model_copy(update={"raw_text": content, "raw_metadata": metadata})
                RawItemRepository(self.base_dir).upsert_raw_items([item], run_id=raw["run_id"], conn=conn)
