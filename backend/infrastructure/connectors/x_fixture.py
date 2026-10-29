from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from backend.infrastructure.connectors.base import FetchResult
from backend.domain.models.raw_item import RawAuthor, RawItem, RawTimestamps
from backend.domain.timestamps import utc_now_micro_z


@dataclass
class XFixtureConnector:
    connector_id: str
    fixture_path: Path
    source_platform: str = "x"
    source_type: str = "social_post"

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        tweets = payload.get("tweets", [])

        raw_items: list[RawItem] = []
        seen_cursor = since_cursor is None

        for tweet in tweets:
            tweet_id = str(tweet.get("id") or "")
            if not tweet_id:
                continue

            if not seen_cursor:
                if tweet_id == since_cursor:
                    seen_cursor = True
                continue

            raw_items.append(self._to_raw_item(tweet))

        next_cursor = raw_items[-1].raw_item_id if raw_items else since_cursor
        visible_count = len([tweet for tweet in tweets if tweet.get("id")])
        new_count = len(raw_items)
        return FetchResult(
            items=raw_items,
            next_cursor=next_cursor,
            visible_count=visible_count,
            new_count=new_count,
            dedup_skipped_count=max(0, visible_count - new_count),
        )

    def _to_raw_item(self, tweet: dict) -> RawItem:
        author = tweet.get("author") or {}
        created_at = tweet.get("created_at")
        return RawItem(
            raw_item_id=str(tweet["id"]),
            connector_id=self.connector_id,
            source_platform=self.source_platform,
            source_type=self.source_type,
            source_url=tweet.get("url") or "",
            captured_at=utc_now_micro_z(),
            raw_text=tweet.get("text"),
            raw_author=RawAuthor(
                name=author.get("name"),
                handle=author.get("username"),
                url=author.get("url"),
            ),
            raw_timestamps=RawTimestamps(published_at=created_at),
            raw_metadata={
                "metrics": tweet.get("metrics", {}),
                "conversation_id": tweet.get("conversation_id"),
            },
        )
