"""Local-only digest recheck/backfill contracts through durable acquisition."""
from datetime import datetime, timezone

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.application.workflows.acquisition import fetch_source
from backend.application.workflows.fetch_support import build_connector
from backend.domain.sources.configuration import database_source_to_connector_config, validate_source_payload
from backend.domain.sources.versioning import source_fetch_input_version
from backend.domain.sources.zsxq_recheck import digest_recheck_command, digest_recheck_options, digest_windows
from backend.infrastructure.connectors.opencli import OpenCLICommandError
from backend.infrastructure.connectors.zsxq_topics import ZsxqTopicsConnector
from backend.infrastructure.storage.repositories import RawItemRepository, CardRepository, SourceRepository, SourceCursorRepository
from tests.infoos.test_zsxq_digest import _raw, _build, _evaluate


def _source(base, metadata=None):
    source = validate_source_payload({"source_id": "all", "connector_id": "all", "platform": "zsxq",
        "source_type": "topics", "identifier": "all", "display_name": "全部帖", "cadence_hours": 6,
        "max_items_per_run": 20, "connector_status": "supported", "raw_metadata": {"max_items_per_run": 20, **(metadata or {})}})
    source["connector_status"] = "supported"
    SourceRepository(base).upsert_source(source)
    return SourceRepository(base).get_source("all")


def _connector(base, source, monkeypatch, main, digests, details=None, error=None):
    connector = build_connector(base, database_source_to_connector_config(source))
    calls = []
    def load(command=None):
        calls.append(command)
        if command is None:
            return main
        if command[:2] == ["zsxq", "search"]:
            if error:
                raise error
            return digests
        return (details or {}).get(command[2], [])
    monkeypatch.setattr(connector, "_load_payload", load)
    monkeypatch.setattr(connector, "fetch_new_items", connector._fetch_new_items_in_lease)
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)
    return connector, calls


def _fetch(base, source, *, window=None):
    return fetch_source(base, source_id="all", task=Task(task_id="fetch-test", task_type=TaskType.FETCH,
        target_kind="source", target_id="all", input_version=source_fetch_input_version(source),
        artifacts_summary={"digest_backfill_window": window} if window else {}))


@pytest.mark.parametrize("metadata,days,limit", [({}, 14, 60), ({"digest_recheck_days": 0}, 0, 60),
    ({"digest_recheck_days": 3, "digest_recheck_limit": 9}, 3, 9)])
def test_command_defaults_disabled_custom_and_shanghai_date(tmp_path, metadata, days, limit):
    source = _source(tmp_path, metadata)
    config = database_source_to_connector_config(source)
    connector = build_connector(tmp_path, config)
    assert isinstance(connector, ZsxqTopicsConnector)
    command = digest_recheck_command("group", config["digest_recheck"],
        now=datetime(2026, 10, 12, 16, 30, tzinfo=timezone.utc))
    if days == 0:
        assert command is None
    else:
        assert command == ["zsxq", "search", "*", "--group_id", "group", "--limit", str(limit),
            "--sort", "newest", "--content-type", "essence", "--start-date",
            "2026-09-30" if days == 14 else "2026-10-11", "--end-date", "2026-10-13", "--hydrate", "false"]


@pytest.mark.parametrize("metadata", [{"digest_recheck_days": -1}, {"digest_recheck_limit": 0},
    {"digest_recheck_days": True}, {"digest_recheck_limit": 1.5}])
def test_invalid_options(metadata):
    with pytest.raises(ValueError):
        digest_recheck_options(metadata)


def test_hash_defaults_unchanged_and_nondefaults_versioned(tmp_path):
    source = _source(tmp_path)
    baseline = source_fetch_input_version(source)
    assert baseline == "sha256:bc8c9754cbd95f8b0e6ee868aadfd8600a6172a9f713f7a953f501e8b2aad712"
    assert source_fetch_input_version({**source, "raw_metadata": {**source["raw_metadata"],
        "digest_recheck_days": 14, "digest_recheck_limit": 60}}) == baseline
    for metadata in ({"digest_recheck_days": 0}, {"digest_recheck_days": 7}, {"digest_recheck_limit": 100}):
        assert source_fetch_input_version({**source, "raw_metadata": {**source["raw_metadata"], **metadata}}) != baseline


@pytest.mark.parametrize("connector_id", ["all", "research"])
def test_observation_preserves_body_evaluation_and_cursor_without_processing(tmp_path, monkeypatch, connector_id):
    source = _source(tmp_path)
    raw = _raw(connector_id, None)
    card_id = _build(tmp_path, raw)["card_id"]
    _evaluate(tmp_path, card_id)
    before = CardRepository(tmp_path).get_card(card_id)
    original = RawItemRepository(tmp_path).get_for_connector(connector_id, raw.raw_item_id)
    SourceCursorRepository(tmp_path).upsert(source_id="all", connector_id="all", cursor_value="cursor-main", last_run_id="before")
    connector, calls = _connector(tmp_path, source, monkeypatch,
        [{"topic_id": "cursor-main", "full_text": "当前主游标", "url": "https://wx.zsxq.com/topic/cursor-main"}],
        [{**raw.raw_metadata["opencli_record"], "full_text": "被缩短且不同的摘要", "title": "不同标题"}])
    checkpoint = _fetch(tmp_path, source)
    after = CardRepository(tmp_path).get_card(card_id)
    assert after["raw_payload"]["raw_payload"]["is_digest"] is True
    assert {k: v for k, v in after.items() if k != "raw_payload"} == {k: v for k, v in before.items() if k != "raw_payload"}
    stored = RawItemRepository(tmp_path).get_for_connector(connector_id, raw.raw_item_id)
    assert stored["raw_text"] == original["raw_text"]
    assert stored["raw_payload"]["raw_metadata"]["title"] == original["raw_payload"]["raw_metadata"]["title"]
    assert stored["raw_payload"]["raw_metadata"]["is_digest"] is True
    assert SourceCursorRepository(tmp_path).get("all")["cursor_value"] == "cursor-main"
    assert checkpoint["card_build_task_ids"] == []
    assert TaskRepository.for_base_dir(tmp_path).list_tasks() == []
    assert len(calls) == 2 and calls[1][-2:] == ["--hydrate", "false"]
    assert connector.discovery_evidence["digest_recheck"]["existing_count"] == 1


def test_missing_digest_is_hydrated_ingested_and_buildable(tmp_path, monkeypatch):
    source = _source(tmp_path)
    old = _raw()
    listed = old.raw_metadata["opencli_record"]
    detail = {**listed, "full_text": "完整帖子与外部文档正文"}
    connector, calls = _connector(tmp_path, source, monkeypatch, [], [listed], {old.raw_item_id: [detail]})
    checkpoint = _fetch(tmp_path, source)
    assert checkpoint["cursor_after"] is None  # Old digests cannot become the latest-post cursor.
    assert len(checkpoint["card_build_task_ids"]) == 1
    repo = TaskRepository.for_base_dir(tmp_path)
    from backend.application.tasks.handlers.acquisition import handle_card_build
    result = handle_card_build(repo.get(checkpoint["card_build_task_ids"][0]), tmp_path)
    card = CardRepository(tmp_path).get_card(result["artifacts"]["card_id"])
    assert card["content"] == detail["full_text"]
    assert card["raw_payload"]["raw_payload"]["is_digest"] is True
    assert calls[2][:3] == ["zsxq", "topic", old.raw_item_id]
    assert connector.discovery_evidence["digest_recheck"]["new_count"] == 1
    assert "_zsxq_digest_observation" not in RawItemRepository(tmp_path).get(old.raw_item_id)["raw_payload"]["raw_metadata"]


@pytest.mark.parametrize("error", [RuntimeError("OpenCLI command timed out after 180s"),
    OpenCLICommandError(1, stderr_summary="AUTH_REQUIRED: login required")])
def test_recheck_failure_retains_main_and_durable_evidence(tmp_path, monkeypatch, error):
    source = _source(tmp_path)
    connector, _ = _connector(tmp_path, source, monkeypatch, [_raw().raw_metadata["opencli_record"]], [], error=error)
    checkpoint = _fetch(tmp_path, source)
    assert RawItemRepository(tmp_path).get(_raw().raw_item_id) is not None
    evidence = checkpoint["discovery_evidence"]["digest_recheck"]
    assert evidence["status"] == "partial_failure" and evidence["errors"]
    assert len(checkpoint["card_build_task_ids"]) == 1
    if isinstance(error, OpenCLICommandError):
        assert checkpoint["digest_recheck_failure"]["reason"] == "platform_not_logged_in"
    assert connector.digest_recheck_error is error


def test_disabled_recheck_does_not_make_second_call(tmp_path, monkeypatch):
    source = _source(tmp_path, {"digest_recheck_days": 0})
    _, calls = _connector(tmp_path, source, monkeypatch, [], [])
    checkpoint = _fetch(tmp_path, source)
    assert calls == [None]
    assert checkpoint["discovery_evidence"]["digest_recheck"]["status"] == "disabled"


@pytest.mark.parametrize("start,end,days", [("2026-10-02", "2026-10-01", 7), ("bad", "2026-10-01", 7),
    ("2026-10-01", "2026-10-02", 0)])
def test_invalid_windows(start, end, days):
    with pytest.raises(ValueError):
        digest_windows(start, end, days)


def test_backfill_only_marks_existing_without_cursor_or_cadence_updates(tmp_path, monkeypatch):
    source = _source(tmp_path)
    raw = _raw()
    _build(tmp_path, raw)
    original = RawItemRepository(tmp_path).get(raw.raw_item_id)
    SourceCursorRepository(tmp_path).upsert(source_id="all", connector_id="all", cursor_value="keep", last_run_id="before")
    window = {"start_date": "2026-08-26", "end_date": "2026-09-01", "limit": 60}
    connector, calls = _connector(tmp_path, source, monkeypatch, [], [raw.raw_metadata["opencli_record"],
        {"topic_id": "missing", "full_text": "列表摘要"}])
    # The normal workflow injects this window before connector construction.
    connector.digest_backfill_window = window
    checkpoint = _fetch(tmp_path, source, window=window)
    assert len(calls) == 1 and calls[0][-2:] == ["--hydrate", "false"]
    assert RawItemRepository(tmp_path).get(raw.raw_item_id)["raw_text"] == original["raw_text"]
    assert RawItemRepository(tmp_path).get(raw.raw_item_id)["raw_payload"]["raw_metadata"]["is_digest"] is True
    assert RawItemRepository(tmp_path).get("missing") is None
    assert checkpoint["card_build_task_ids"] == []
    assert SourceCursorRepository(tmp_path).get("all")["cursor_value"] == "keep"
    assert SourceRepository(tmp_path).get_source("all")["last_run_at"] == source["last_run_at"]
    assert connector.discovery_evidence["digest_recheck"]["missing_count"] == 1


@pytest.mark.parametrize("verified", [False, True])
def test_supplemental_login_error_preserves_success_and_uses_existing_auth_recovery(tmp_path, monkeypatch, verified):
    from backend.application.tasks.executor import run_tasks_by_ids
    from backend.application.tasks.types import TaskStatus
    from backend.infrastructure.runtime.setup_state import AccountState, AccountStatus
    from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
    source = _source(tmp_path, {"fetch_rate_policy": {"enabled": False}})
    _connector(tmp_path, source, monkeypatch, [_raw().raw_metadata["opencli_record"]], [],
        error=OpenCLICommandError(1, stderr_summary="AUTH_REQUIRED: login required"))
    probes = []
    monkeypatch.setattr("backend.application.tasks.auth_recheck.probe_platform", lambda platform:
        probes.append(platform) or AccountState(status=AccountStatus.VERIFIED if verified else AccountStatus.NEEDS_LOGIN))
    monkeypatch.setattr("backend.application.workflows.auth_recovery.enqueue_notification", lambda *args, **kwargs: [])
    # Suppress only runtime side effects, keeping the incident and circuit transaction real.
    monkeypatch.setattr("backend.application.workflows.auth_recovery.mark_account_needs_login", lambda **_: None)
    version = source_fetch_input_version(source)
    task = Task(task_id="supplemental", task_type=TaskType.FETCH, target_kind="source", target_id="all",
        input_version=version, artifacts_summary={"source_input_version": version, "plan_followup_fetch": False})
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(task)
    run_tasks_by_ids(tmp_path, [task.task_id])
    saved = repo.get(task.task_id)
    assert saved.status is TaskStatus.SUCCESS
    assert RawItemRepository(tmp_path).get(_raw().raw_item_id)["raw_text"] == _raw().raw_text
    assert probes == ["zsxq"]
    evidence = saved.artifacts_summary["digest_recheck_failure"]["diagnostic"]["auth_recheck"]
    assert evidence["outcome"] == ("verified" if verified else "not_logged_in")
    circuit = RuntimeCircuitRepository(tmp_path).get("platform", "zsxq")
    if not verified:
        assert circuit["state"] == "open" and circuit["recovery_mode"] == "manual"
        with CardRepository(tmp_path).connect_existing() as conn:
            assert conn.execute("select count(*) from auth_recovery_incidents").fetchone()[0] == 1
    else:
        assert circuit["reason_code"] == "opencli_command_failed"
        assert circuit["recovery_mode"] == "automatic"
