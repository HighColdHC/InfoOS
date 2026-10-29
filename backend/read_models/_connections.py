"""Connection lifetime for synchronous, database-backed read-model entry points."""
from __future__ import annotations

from functools import wraps
from pathlib import Path
from typing import Callable, Concatenate, ParamSpec, TypeVar

from backend.infrastructure.storage.database import default_database_path, read_connection_scope

_P = ParamSpec("_P")
_R = TypeVar("_R")


def shared_read_connection(
    read: Callable[Concatenate[Path | str, _P], _R],
) -> Callable[Concatenate[Path | str, _P], _R]:
    """Set and reset the context in the same thread as the entire projection."""
    @wraps(read)
    def wrapped(base_dir: Path | str, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with read_connection_scope(default_database_path(base_dir)):
            return read(base_dir, *args, **kwargs)

    return wrapped
