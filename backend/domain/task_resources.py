"""Task resource requests and leases."""
from __future__ import annotations
from dataclasses import dataclass
from uuid import uuid4
from backend.domain.tasks import Task

@dataclass(frozen=True)
class ResourceRequest:
    resource_kind: str
    resource_key: str
    alternative_keys: tuple[str, ...] = ()

    @classmethod
    def pooled(cls, resource_kind: str, capacity: int) -> "ResourceRequest":
        if capacity <= 0:
            raise ValueError("resource pool capacity must be positive")
        keys = tuple(f"slot:{index}" for index in range(capacity))
        return cls(resource_kind, keys[0], keys[1:])

    @property
    def candidate_keys(self) -> tuple[str, ...]:
        return (self.resource_key, *self.alternative_keys)

    @property
    def contention_key(self) -> str:
        if not self.alternative_keys:
            return self.resource_key
        return f"pool:{len(self.candidate_keys)}"


@dataclass(frozen=True)
class ResourceLease:
    resource_kind: str
    resource_key: str
    owner_token: str


@dataclass(frozen=True)
class AdmittedTask:
    task: Task
    resource: ResourceLease


def new_resource_owner_token() -> str:
    return f"resource-{uuid4().hex}"

