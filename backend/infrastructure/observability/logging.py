from __future__ import annotations

import copy
import fcntl
import json
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator

from backend.infrastructure.storage.paths import data_dir
from backend.infrastructure.observability.log_records import LOG_AREAS, infer_area
from backend.infrastructure.security.redaction import redact_secrets, redact_text

LOGGER_NAME = "infoos"
DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
STRUCTURED_FIELDS = (
    "object_type", "object_id", "request_id", "source_id", "task_id", "worker_id",
)


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        raw_area = str(getattr(record, "area", "") or "").strip().lower()
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.upper(),
            "area": raw_area if raw_area in LOG_AREAS else infer_area(record.name, message),
            "message": message,
            **{field: getattr(record, field, None) for field in STRUCTURED_FIELDS},
        }
        return json.dumps(redact_secrets(payload), ensure_ascii=False, separators=(",", ":"))


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        safe_record = copy.copy(record)
        safe_record.msg = redact_text(record.getMessage())
        safe_record.args = ()
        return super().format(safe_record)

    def formatException(self, exc_info) -> str:
        return redact_text(super().formatException(exc_info))


class MultiProcessRotatingFileHandler(RotatingFileHandler):
    """Rotate a shared log without racing other InfoOS processes."""

    def __init__(self, filename: str | Path, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(filename, *args, **kwargs)
        log_path = Path(self.baseFilename)
        self._lock_path = log_path.with_name(f"{log_path.name}.lock")

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        lock_fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _reopen_if_replaced(self) -> None:
        if self.stream is None:
            self.stream = self._open()
            return

        try:
            path_stat = os.stat(self.baseFilename)
        except FileNotFoundError:
            path_stat = None
        stream_stat = os.fstat(self.stream.fileno())
        if path_stat is not None and (path_stat.st_dev, path_stat.st_ino) == (
            stream_stat.st_dev,
            stream_stat.st_ino,
        ):
            return

        self.stream.flush()
        self.stream.close()
        self.stream = self._open()

    def _should_rollover_locked(self, record: logging.LogRecord) -> bool:
        if self.maxBytes <= 0:
            return False
        try:
            current_size = os.stat(self.baseFilename).st_size
        except FileNotFoundError:
            current_size = 0
        if current_size == 0:
            return False
        if not os.path.isfile(self.baseFilename):
            return False
        message = f"{self.format(record)}{self.terminator}"
        message_size = len(
            message.encode(self.encoding or "utf-8", errors=self.errors or "strict")
        )
        return current_size + message_size >= self.maxBytes

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with self._process_lock():
                self._reopen_if_replaced()
                if self._should_rollover_locked(record):
                    self.doRollover()
                logging.FileHandler.emit(self, record)
        except Exception:  # noqa: BLE001 - Logging sink and formatter failures must follow handleError without replacing the business result
            self.handleError(record)


def resolve_log_path(base_dir: str | Path | None = None) -> Path:
    if base_dir is not None and Path(base_dir) != Path("."):
        return Path(base_dir) / "data" / "logs" / "infoos.log"
    return data_dir() / "logs" / "infoos.log"


def configure_logging(
    base_dir: str | Path | None = None,
    *,
    level: int | str = logging.INFO,
    log_path: str | Path | None = None,
) -> Path:
    path = Path(log_path) if log_path is not None else resolve_log_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized_level = logging._checkLevel(level)
    configuration = (os.getpid(), str(path.resolve()), normalized_level)

    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_infoos_configuration", None) == configuration:
        return path

    logger.setLevel(normalized_level)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, "_infoos_managed", False):
            logger.removeHandler(handler)
            handler.close()

    file_handler = MultiProcessRotatingFileHandler(
        path,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(StructuredJsonFormatter())
    file_handler.setLevel(normalized_level)
    file_handler._infoos_managed = True  # type: ignore[attr-defined]

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(RedactingTextFormatter(DEFAULT_LOG_FORMAT))
    console_handler.setLevel(normalized_level)
    console_handler._infoos_managed = True  # type: ignore[attr-defined]

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger._infoos_configuration = configuration  # type: ignore[attr-defined]
    return path


def get_logger(name: str | None = None) -> logging.Logger:
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
