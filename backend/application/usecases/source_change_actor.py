"""User-request attribution for source lifecycle operations."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SourceChangeActor:
    actor_kind: str = "web"
    token_id: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.actor_kind not in {"web", "mcp"}:
            raise ValueError("无效的来源变更主体")
        if self.actor_kind == "mcp":
            if not self.token_id or not isinstance(self.reason, str) or not self.reason.strip() or not 1 <= len(self.reason) <= 500:
                raise ValueError("reason 必须为1–500字符的用户原话或概括，且必须提供 Token 身份")

    def as_dict(self) -> dict[str, Any]:
        return {"actor_kind": self.actor_kind, "token_id": self.token_id, "reason": self.reason}
