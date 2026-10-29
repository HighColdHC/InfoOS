"""Small channel contract; provider details never cross this boundary."""
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class ChannelResult:
    status: Literal["sent", "retryable", "failed"]
    error_code: str | None = None
    provider_message_id: str | None = None


class NotificationChannel(Protocol):
    def send(self, *, title: str, body: str, image_path: str | None, message_id: str) -> ChannelResult: ...
