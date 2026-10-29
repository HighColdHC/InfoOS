"""A reserved maintenance consumer keeps checking while delivery is blocked."""
from functools import partial
import threading

from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.repository import TaskRepository
from backend.domain.tasks import Task, TaskType
from backend.workers.task_worker import run_task_worker_loop


def test_qr_check_worker_progresses_during_slow_delivery(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task('slow-delivery', TaskType.NOTIFICATION_DELIVER, 'notification', 'fixture'))
    delivering, release_delivery, checked = threading.Event(), threading.Event(), threading.Event()
    stop_delivery, stop_check = threading.Event(), threading.Event()
    failures = []

    def send(task, base):
        delivering.set()
        if not release_delivery.wait(10):
            raise TimeoutError('test did not release delivery')
        stop_delivery.set()
        return {'status': 'success'}

    def check(task, base):
        checked.set()
        stop_check.set()
        return {'status': 'success'}

    run = partial(run_due_tasks, handlers={TaskType.NOTIFICATION_DELIVER: send, TaskType.AUTH_QR_CHECK: check})

    def worker(stop, types):
        try:
            run_task_worker_loop(base_dir=tmp_path, queue_name='maintenance_io', max_workers=1,
                task_types=types, stop_event=stop, poll_seconds=0.01, run_due_tasks_fn=run)
        except (RuntimeError, OSError, TimeoutError) as exc:
            failures.append(exc)

    delivery_thread = threading.Thread(target=worker, args=(stop_delivery, [TaskType.NOTIFICATION_DELIVER]))
    check_thread = threading.Thread(target=worker, args=(stop_check, [TaskType.AUTH_QR_CHECK]))
    delivery_thread.start()
    try:
        assert delivering.wait(5)
        repo.enqueue(Task('passive-check', TaskType.AUTH_QR_CHECK, 'auth_incident', 'fixture'))
        check_thread.start()
        assert checked.wait(5)
        assert not release_delivery.is_set()
    finally:
        release_delivery.set()
        stop_delivery.set()
        stop_check.set()
        delivery_thread.join(10)
        if check_thread.ident is not None:
            check_thread.join(10)
    assert not failures
    assert not delivery_thread.is_alive() and not check_thread.is_alive()
    assert repo.get('passive-check').status.value == 'success'
