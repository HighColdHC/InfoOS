"""Bounded queries and mutations for the X media cleanup command."""

from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from typing import Any, Iterator

from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.database import connect_writable


_MEDIA_TEXT_KINDS = {"translation", "summary"}
_MEDIA_DERIVED_LAYERS = {"derived", "intelligence"}
_AD_KEYWORDS = ("starlink", "spacex")


class XMediaCleanupStore(InfoOSRepository):
    """Resolve and atomically remove one exact bad X media hash."""

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = connect_writable(self.db_path)
        try:
            conn.execute("begin immediate")
            yield conn
            conn.execute("commit")
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    def plan(
        self,
        content_hash: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        if connection is not None:
            return self._plan(connection, content_hash)
        with self.connect_existing() as conn:
            return self._plan(conn, content_hash)

    @staticmethod
    def _plan(conn: sqlite3.Connection, content_hash: str) -> dict[str, Any]:
        owners = conn.execute(
            """
            select a.asset_id, a.card_id, c.source_platform
            from card_assets a
            join cards c on c.card_id = a.card_id
            where a.content_hash = ?
            order by a.card_id, a.asset_id
            """,
            (content_hash,),
        ).fetchall()
        non_x = [dict(row) for row in owners if row["source_platform"] != "x"]
        if non_x:
            identities = ", ".join(
                f"{row['card_id']}/{row['asset_id']} ({row['source_platform']})"
                for row in non_x
            )
            raise ValueError(
                "x_media_cleanup refuses a hash also owned by non-X cards: " + identities
            )
        asset_rows = conn.execute(
            """
            select a.*
            from card_assets a
            join cards c on c.card_id = a.card_id
            where a.content_hash = ?
            order by a.card_id, a.asset_id
            """,
            (content_hash,),
        ).fetchall()
        assets = [dict(row) for row in asset_rows]
        card_ids = sorted({str(row["card_id"]) for row in assets})
        cards: list[dict[str, Any]] = []
        for card_id in card_ids:
            card = dict(conn.execute("select * from cards where card_id = ?", (card_id,)).fetchone())
            affected_assets = [row for row in assets if row["card_id"] == card_id]
            affected_ids = {str(row["asset_id"]) for row in affected_assets}
            all_assets = [
                dict(row)
                for row in conn.execute(
                    "select * from card_assets where card_id = ? order by created_at, asset_id",
                    (card_id,),
                ).fetchall()
            ]
            all_blocks = [
                dict(row)
                for row in conn.execute(
                    "select * from card_content_blocks where card_id = ? order by position, created_at, block_id",
                    (card_id,),
                ).fetchall()
            ]
            image_blocks: list[dict[str, Any]] = []
            linked_blocks: list[dict[str, Any]] = []
            for block in all_blocks:
                raw_payload = _json_object(block["raw_payload"])
                for asset in affected_assets:
                    asset_id = str(asset["asset_id"])
                    if (
                        block["block_kind"] == "image"
                        and (
                            raw_payload.get("asset_id") == asset_id
                            or (
                                asset["local_path"]
                                and block["body"] == asset["local_path"]
                            )
                        )
                    ):
                        image_blocks.append(block)
                        break
                    if (
                        block["source_url"] == f"asset:{asset_id}"
                        and block["block_kind"] in {"image_ocr", "image_description"}
                    ):
                        linked_blocks.append(block)
                        break
            image_blocks = _unique_rows(image_blocks, "block_id")
            linked_blocks = _unique_rows(linked_blocks, "block_id")
            keyword_matches = []
            for block in all_blocks:
                if not (
                    block["layer"] in _MEDIA_DERIVED_LAYERS
                    or block["block_kind"] in _MEDIA_TEXT_KINDS
                ):
                    continue
                body = block["body"]
                matches = [keyword for keyword in _AD_KEYWORDS if keyword in body.casefold()]
                if matches:
                    keyword_matches.append(
                        {
                            "block_id": block["block_id"],
                            "block_kind": block["block_kind"],
                            "keywords": matches,
                        }
                    )
            cards.append(
                {
                    "card_id": card_id,
                    "card": card,
                    "assets": affected_assets,
                    "image_blocks": image_blocks,
                    "linked_image_blocks": linked_blocks,
                    "other_assets": [row for row in all_assets if row["asset_id"] not in affected_ids],
                    "derived_text_keyword_scan": {
                        "has_ad_keywords": bool(keyword_matches),
                        "matches": keyword_matches,
                    },
                }
            )
        return {
            "content_hash": content_hash,
            "affected_card_count": len(cards),
            "affected_asset_count": len(assets),
            "affected_cards": cards,
        }

    @staticmethod
    def delete_and_increment(
        conn: sqlite3.Connection,
        plan: dict[str, Any],
        *,
        updated_at: str,
    ) -> dict[str, int]:
        asset_ids = sorted(
            str(asset["asset_id"])
            for card in plan["affected_cards"]
            for asset in card["assets"]
        )
        block_ids = sorted(
            {
                str(block["block_id"])
                for card in plan["affected_cards"]
                for block in [*card["image_blocks"], *card["linked_image_blocks"]]
            }
        )
        deleted_blocks = _delete_ids(conn, "card_content_blocks", "block_id", block_ids)
        deleted_assets = _delete_ids(conn, "card_assets", "asset_id", asset_ids)
        updated_cards = 0
        for card in plan["affected_cards"]:
            cursor = conn.execute(
                "update cards set version = version + 1, updated_at = ? "
                "where card_id = ? and source_platform = 'x'",
                (updated_at, card["card_id"]),
            )
            updated_cards += int(cursor.rowcount)
        return {
            "deleted_assets": deleted_assets,
            "deleted_blocks": deleted_blocks,
            "updated_cards": updated_cards,
        }

    @staticmethod
    def finalize_state(
        conn: sqlite3.Connection,
        card_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        card_row = conn.execute("select * from cards where card_id = ?", (card_id,)).fetchone()
        if card_row is None:
            raise KeyError(card_id)
        card = _decode_row(dict(card_row))
        blocks = [
            _decode_row(dict(row))
            for row in conn.execute(
                "select * from card_content_blocks where card_id = ? order by position, created_at, block_id",
                (card_id,),
            ).fetchall()
        ]
        assets = [
            _decode_row(dict(row))
            for row in conn.execute(
                "select * from card_assets where card_id = ? order by created_at, asset_id",
                (card_id,),
            ).fetchall()
        ]
        return card, blocks, assets


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    json_fields = (
        ("raw_payload", {}),
        ("segments", []),
        ("missing_reasons", []),
        ("secondary_domain_ids", []),
    )
    for key, default in json_fields:
        if key in row and isinstance(row[key], str):
            try:
                row[key] = json.loads(row[key])
            except ValueError:
                row[key] = default
    return row


def _unique_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return list({str(row[key]): row for row in rows}.values())


def _delete_ids(conn: sqlite3.Connection, table: str, column: str, values: list[str]) -> int:
    if not values:
        return 0
    placeholders = ",".join("?" for _ in values)
    cursor = conn.execute(f"delete from {table} where {column} in ({placeholders})", values)
    return int(cursor.rowcount)
