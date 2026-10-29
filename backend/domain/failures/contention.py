"""Explicit resource-contention failures that never consume a task attempt."""
from __future__ import annotations


class ResourceContentionError(RuntimeError):
    """A named, recoverable resource was busy."""


class ResourceLeaseLost(ResourceContentionError):
    """A worker lost ownership of a durable runtime resource."""

    def __init__(self, resource_kind: str, resource_key: str):
        self.resource_kind = str(resource_kind)
        self.resource_key = str(resource_key)
        super().__init__(f"resource lease lost: {resource_kind}/{resource_key}")


class OpenCLISiteLeaseTimeout(ResourceContentionError, TimeoutError):
    """The normalized OpenCLI site lease was not available before its deadline."""

    def __init__(self, site: str):
        self.site = str(site).strip().lower()
        super().__init__(f"OpenCLI site lease unavailable: {self.site}")
