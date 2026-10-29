"""Aliases for JSON documents decoded at system boundaries."""

from __future__ import annotations

from typing import Any

# Key access on a decoded JSON object stays a boundary check (``.get`` plus narrowing).
JsonObject = dict[str, Any]
