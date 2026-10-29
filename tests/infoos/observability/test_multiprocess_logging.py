from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
from pathlib import Path

from backend.infrastructure.observability.logging import (
    MultiProcessRotatingFileHandler,
    StructuredJsonFormatter,
    configure_logging,
)


def _logging_worker(
    log_path: str,
    state_home: str,
    data_home: str,
    llm_config: str,
    barrier,  # type: ignore[no-untyped-def]
    worker_index: int,
    line_count: int,
) -> None:
    os.environ["INFOOS_STATE_HOME"] = state_home
    os.environ["INFOOS_DATA_HOME"] = data_home
    os.environ["INFOOS_LLM_CONFIG"] = llm_config

    logger = logging.getLogger(f"infoos.multiprocess-test.{worker_index}")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)

    handler = MultiProcessRotatingFileHandler(
        log_path,
        maxBytes=12_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(StructuredJsonFormatter())
    logger.addHandler(handler)

    barrier.wait(timeout=10)
    for line_index in range(line_count):
        logger.info(
            "worker=%s line=%s payload=%s",
            worker_index,
            line_index,
            "x" * 80,
            extra={"worker_id": f"worker-{worker_index}"},
        )
    handler.close()


def test_configure_logging_is_idempotent_for_same_process_path_and_level(tmp_path: Path):
    log_path = tmp_path / "infoos.log"

    assert configure_logging(log_path=log_path, level=logging.INFO) == log_path
    logger = logging.getLogger("infoos")
    original_handlers = tuple(logger.handlers)

    assert configure_logging(log_path=log_path, level=logging.INFO) == log_path

    assert tuple(logger.handlers) == original_handlers


def test_rotating_json_log_is_safe_across_spawned_processes(tmp_path: Path):
    ctx = mp.get_context("spawn")
    process_count = 3
    lines_per_process = 60
    barrier = ctx.Barrier(process_count)
    log_path = tmp_path / "logs" / "infoos.log"
    log_path.parent.mkdir(parents=True)
    state_home = tmp_path / "state"
    data_home = state_home / "data"
    llm_config = state_home / "config" / "llm.json"
    processes = [
        ctx.Process(
            target=_logging_worker,
            args=(
                str(log_path),
                str(state_home),
                str(data_home),
                str(llm_config),
                barrier,
                worker_index,
                lines_per_process,
            ),
        )
        for worker_index in range(process_count)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)

    assert all(process.exitcode == 0 for process in processes)
    rotated_paths = sorted(
        path for path in log_path.parent.glob("infoos.log*") if path.name != "infoos.log.lock"
    )
    assert log_path.with_name("infoos.log.1") in rotated_paths
    assert all(path.stat().st_size < 12_000 for path in rotated_paths)

    payloads = []
    for path in rotated_paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            payloads.append(json.loads(line))

    assert len(payloads) == process_count * lines_per_process
    assert all(payload["message"].startswith("worker=") for payload in payloads)
