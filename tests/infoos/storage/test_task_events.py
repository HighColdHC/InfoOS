from __future__ import annotations

from backend.infrastructure.storage.task_events import TaskEventRepository


def test_task_event_round_trip(tmp_path):
    events = TaskEventRepository(tmp_path)

    event_id = events.append(
        event_type="claimed",
        task_id="task_1",
        chain_id="chain_1",
        status="running",
        payload={"attempt": 1},
        occurred_at="2026-07-10T08:00:00Z",
    )

    assert event_id.startswith("evt_")
    assert events.list_events(task_id="task_1") == [
        {
            "event_id": event_id,
            "task_id": "task_1",
            "chain_id": "chain_1",
            "event_type": "claimed",
            "status": "running",
            "failure_class": None,
            "payload": {"attempt": 1},
            "occurred_at": "2026-07-10T08:00:00Z",
        }
    ]


def test_task_events_survive_repository_recreation(tmp_path):
    TaskEventRepository(tmp_path).append(
        event_type="completed",
        task_id="task_1",
        chain_id="chain_1",
        status="success",
    )

    assert len(TaskEventRepository(tmp_path).list_events(chain_id="chain_1")) == 1
