from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.infrastructure.observability.logging import configure_logging
from backend.infrastructure.observability.log_records import read_log_records


def test_configure_logging_writes_file_under_base_dir(tmp_path: Path):
    log_path = configure_logging(tmp_path)
    logger = logging.getLogger("infoos.test")

    logger.info("hello from logging test")
    for handler in logging.getLogger("infoos").handlers:
        handler.flush()

    assert log_path == tmp_path / "data" / "logs" / "infoos.log"
    assert "hello from logging test" in log_path.read_text(encoding="utf-8")


def test_file_logging_is_json_line_redacted_and_console_remains_readable(tmp_path: Path, capsys):
    log_path = configure_logging(tmp_path)
    logger = logging.getLogger("infoos.fetch")

    logger.info(
        "fetched Authorization: Bearer super-secret-token-value from /Users/private/source",
        extra={
            "area": "fetch",
            "object_type": "source",
            "object_id": "source-1",
            "source_id": "source-1",
            "task_id": "task-1",
            "worker_id": "worker-1",
            "request_id": "req-1",
        },
    )
    for handler in logging.getLogger("infoos").handlers:
        handler.flush()

    payload = json.loads(log_path.read_bytes().splitlines()[-1])
    assert payload == {
        "timestamp": payload["timestamp"],
        "level": "INFO",
        "area": "fetch",
        "message": "fetched [REDACTED]",
        "object_type": "source",
        "object_id": "source-1",
        "request_id": "req-1",
        "source_id": "source-1",
        "task_id": "task-1",
        "worker_id": "worker-1",
    }
    assert payload["timestamp"].endswith("Z")
    console = capsys.readouterr().err
    assert "fetched" in console
    assert "super-secret-token-value" not in console
    assert not console.lstrip().startswith("{")


def test_log_record_reader_handles_json_legacy_bad_lines_and_five_rotations(tmp_path: Path):
    log_path = tmp_path / "infoos.log"
    log_path.write_text(
        '\n'.join([
            '{"timestamp":"2026-07-16T10:00:00Z","level":"ERROR","area":"media","message":"download failed","object_type":"asset","object_id":"asset-1","request_id":null,"source_id":"source-1","task_id":"task-1","worker_id":"worker-1"}',
            "bad legacy line Cookie: sid=secret-value",
        ]) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "infoos.log.1").write_text(
        "2026-07-16 09:00:00,000 WARNING infoos.browser browser reconnect\n",
        encoding="utf-8",
    )
    for index in range(2, 7):
        (tmp_path / f"infoos.log.{index}").write_text(
            f"2026-07-1{6-index} 08:00:00,000 INFO infoos.system rotation {index}\n",
            encoding="utf-8",
        )

    records = read_log_records(log_path, max_records=20, max_bytes=1_000_000)

    assert any(item["message"] == "download failed" and item["area"] == "media" for item in records)
    assert any(item["message"] == "browser reconnect" and item["area"] == "browser" for item in records)
    bad = next(item for item in records if item["message"].startswith("bad legacy line"))
    assert "secret-value" not in bad["message"]
    assert {"rotation 2", "rotation 3", "rotation 4", "rotation 5"}.issubset(
        {item["message"] for item in records}
    )
    assert "rotation 6" not in {item["message"] for item in records}
    assert records == sorted(records, key=lambda item: (item["timestamp"], item["log_id"]), reverse=True)
    assert all(len(item["log_id"]) == 24 for item in records)


def test_log_record_reader_stops_at_record_and_byte_bounds(tmp_path: Path):
    log_path = tmp_path / "infoos.log"
    log_path.write_bytes(
        b"".join(
            f"2026-07-16 10:{index:02d}:00,000 INFO infoos.system line-{index}\n".encode()
            for index in range(20)
        )
    )

    records = read_log_records(log_path, max_records=3, max_bytes=512)

    assert len(records) == 3
    assert [item["message"] for item in records] == ["line-19", "line-18", "line-17"]
