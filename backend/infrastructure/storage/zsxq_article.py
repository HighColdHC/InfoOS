"""Article evidence and body publication without changing card identity."""
from __future__ import annotations

from typing import Any

from backend.domain.card_payload import compact_card_metadata
from backend.domain.feishu_content import combine_post_document, keeps_previous_body
from backend.domain.models.raw_item import RawItem
from backend.domain.zsxq_article import article_evidence, article_gap, article_url
from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads
from backend.infrastructure.storage.raw_items import RawItemRepository


class ZsxqArticleRepository(InfoOSRepository):
    def candidates(self, *, limit: int, after_card_id: str = "") -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        cursor = after_card_id
        with self.connect_existing() as conn:
            while len(selected) < limit:
                rows = conn.execute(
                    """select c.card_id, c.raw_payload, r.raw_payload as item_payload
                       from cards c left join raw_items r
                         on r.connector_id = c.raw_connector_id and r.raw_item_id = c.raw_item_id
                       where c.source_platform = 'zsxq' and c.card_id > ?
                       order by c.card_id limit 200""", (cursor,),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    cursor = row["card_id"]
                    payload = {**loads(row["raw_payload"], {}), "raw_metadata": loads(row["item_payload"], {})}
                    card = {"source_platform": "zsxq", "raw_payload": payload}
                    if not article_gap(card):
                        continue
                    selected.append({"card_id": cursor, "url": article_url(card),
                                     "read_generation": article_evidence(card).get("read_attempts", 0)})
                    if len(selected) == limit:
                        break
        return selected

    def publish_read(self, card_id: str, *, evidence: dict[str, Any], post: str = "",
                     links: list[str], images: list[str]) -> None:
        """Atomically publish the article plus any already retained Feishu body."""
        with self.connect() as conn:
            row = conn.execute(
                "select content, raw_payload, raw_connector_id, raw_item_id from cards where card_id = ?",
                (card_id,),
            ).fetchone()
            if row is None:
                return
            content = row["content"]
            if post:
                document = conn.execute(
                    "select body, raw_payload from card_content_blocks where block_id = ?",
                    (f"block_{card_id}_external_doc_feishu",),
                ).fetchone()
                document_payload = loads(document["raw_payload"], {}) if document else {}
                document_body = document["body"] if document and (
                    document_payload.get("fetch_state") == "fetched" or document_payload.get("retained_body")) else ""
                candidate = combine_post_document(post, document_body)
                if keeps_previous_body(len(candidate), len(content), complete=evidence["status"] == "fetched"):
                    evidence = {**evidence, "status": "partial", "reason": "shorter_result"}
                else:
                    content = candidate
            payload = loads(row["raw_payload"], {})
            payload.update(compact_card_metadata({"zsxq_article_read": evidence}))
            payload["article_url"] = evidence["article_url"]
            if "normalized_content" in payload:
                payload["normalized_content"] = content
            conn.execute("update cards set content = ?, raw_payload = ? where card_id = ?",
                         (content, dumps(payload), card_id))
            raw = conn.execute(
                "select raw_payload, run_id from raw_items where connector_id = ? and raw_item_id = ?",
                (row["raw_connector_id"], row["raw_item_id"]),
            ).fetchone()
            if raw:
                item = RawItem.model_validate(loads(raw["raw_payload"], {}))
                metadata = {**item.raw_metadata, "zsxq_article_read": evidence,
                            "article_url": evidence["article_url"]}
                for key, values in (("links", links), ("images", images)):
                    previous = metadata.get(key, [])
                    previous = previous if isinstance(previous, list) else []
                    merged = list(previous)
                    for value in values:
                        if value not in merged:
                            merged.append(value)
                    metadata[key] = merged
                item = item.model_copy(update={"raw_text": content, "raw_metadata": metadata})
                RawItemRepository(self.base_dir).upsert_raw_items([item], run_id=raw["run_id"], conn=conn)
