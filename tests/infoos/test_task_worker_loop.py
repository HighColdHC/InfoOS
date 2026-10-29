import signal
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_task_worker_loop_drains_until_stopped(tmp_path):
    from backend.workers.task_worker import run_task_worker_loop

    calls = []

    processed = iter([2, 2, 0])

    def run_due_tasks(base_dir, *, limit, max_workers):
        calls.append((base_dir, limit, max_workers))
        return {"processed": next(processed)}

    def wait(stop_event, seconds):
        assert seconds == 1.0
        stop_event.set()
        return stop_event.is_set()

    assert run_task_worker_loop(
        base_dir=tmp_path,
        batch_limit=7,
        max_workers=3,
        run_due_tasks_fn=run_due_tasks,
        wait_fn=wait,
    ) == 4
    assert calls == [(tmp_path, 7, 3)] * 3


def test_task_worker_processes_newly_ready_children_without_scheduler_cycle(tmp_path):
    from backend.workers.task_worker import run_task_worker_loop

    processed = iter([1, 1, 0])
    calls = []

    def run_due_tasks(base_dir, *, limit, max_workers):
        calls.append((base_dir, limit, max_workers))
        return {"processed": next(processed)}

    def wait(stop_event, _seconds):
        stop_event.set()
        return True

    assert run_task_worker_loop(
        base_dir=tmp_path,
        batch_limit=4,
        run_due_tasks_fn=run_due_tasks,
        wait_fn=wait,
    ) == 2
    assert len(calls) == 3


def test_task_worker_uses_short_poll_after_processing_work(tmp_path):
    from backend.workers.task_worker import run_task_worker_loop

    processed = iter([1, 0])
    waits = []

    def run_due_tasks(base_dir, *, limit, max_workers):
        return {"processed": next(processed)}

    def wait(stop_event, seconds):
        waits.append(seconds)
        stop_event.set()
        return True

    run_task_worker_loop(
        base_dir=tmp_path,
        poll_seconds=0.25,
        run_due_tasks_fn=run_due_tasks,
        wait_fn=wait,
    )

    assert waits == [0.25]


def test_task_worker_uses_idle_poll_when_queue_empty(tmp_path):
    from backend.workers.task_worker import run_task_worker_loop

    waits = []

    def run_due_tasks(base_dir, *, limit, max_workers):
        return {"processed": 0}

    def wait(stop_event, seconds):
        waits.append(seconds)
        stop_event.set()
        return True

    run_task_worker_loop(
        base_dir=tmp_path,
        poll_seconds=0.75,
        run_due_tasks_fn=run_due_tasks,
        wait_fn=wait,
    )

    assert waits == [0.75]


def test_task_worker_loop_survives_one_cycle_exception(tmp_path):
    from backend.workers.task_worker import run_task_worker_loop

    attempts = 0

    def run_due_tasks(base_dir, *, limit, max_workers):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return {"processed": 4 if attempts == 2 else 0}

    def wait(stop_event, seconds):
        if attempts == 3:
            stop_event.set()
        return stop_event.is_set()

    assert run_task_worker_loop(
        base_dir=tmp_path,
        run_due_tasks_fn=run_due_tasks,
        wait_fn=wait,
    ) == 4
    assert attempts == 3


def test_task_worker_loop_stops_on_signal(tmp_path, monkeypatch):
    from backend.workers.task_worker import run_task_worker_loop

    handlers = {}

    def install_signal(signum, handler):
        handlers[signum] = handler
        return None

    monkeypatch.setattr("backend.workers.task_worker.signal.signal", install_signal)

    def run_due_tasks(base_dir, *, limit, max_workers):
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return {"processed": 1}

    assert run_task_worker_loop(base_dir=tmp_path, run_due_tasks_fn=run_due_tasks) == 1
    assert signal.SIGTERM in handlers
    assert signal.SIGINT in handlers


def test_task_worker_loop_uses_resolved_data_home(monkeypatch, tmp_path):
    from backend.workers.task_worker import run_task_worker_loop
    from backend.infrastructure.storage.paths import resolve_sqlite_path

    data_home = tmp_path / "data-home"
    monkeypatch.setenv("INFOOS_DATA_HOME", str(data_home))
    received = []

    def run_due_tasks(base_dir, *, limit, max_workers):
        received.append(resolve_sqlite_path(base_dir))
        return {"processed": 0}

    def wait(stop_event, seconds):
        stop_event.set()
        return True

    assert run_task_worker_loop(
        base_dir=".",
        run_due_tasks_fn=run_due_tasks,
        wait_fn=wait,
    ) == 0
    assert received == [data_home / "infoos.sqlite3"]


def test_task_worker_main_exits_successfully_after_a_stopped_loop(monkeypatch):
    from backend.workers import task_worker

    monkeypatch.setattr(task_worker, "run_task_worker_loop", lambda **_kwargs: 12)

    assert task_worker.main(["--base-dir", "."]) == 0
