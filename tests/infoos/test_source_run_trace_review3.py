from __future__ import annotations

import base64
import json

from backend.application.tasks.planner import raw_item_target_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.infrastructure.storage.database import connect
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.repositories import CardContentBlockRepository
from backend.infrastructure.storage.source_run_trace_queries import GRAPH_NODE_LIMIT, SourceRunTraceQueries
from backend.read_models.source_run_trace import decode_item_cursor
from backend.read_models.source_run_trace_safety import opaque_identifier
from backend.read_models.source_run_trace_safety import sanitize_external_url
from tests.infoos.test_source_run_trace_api import NOW, _link, _seed_run, _ta, _trace_path


def test_trace_loads_only_current_item_page(tmp_path, monkeypatch):
    raw_keys = tuple(("connector_a", f"raw_{index:03}") for index in range(240))
    client = _seed_run(tmp_path, raw_keys=raw_keys)
    CardContentBlockRepository(tmp_path).upsert_block({
        "block_id": "non_page_marker", "card_id": "card_200", "position": 0,
        "layer": "original", "block_kind": "article", "status": "done", "body": "not loaded",
    })
    captured = {}
    original = SourceRunTraceQueries.load_trace_page

    def capture(self, head, *, root_ids, raw_keys):
        result = original(self, head, root_ids=root_ids, raw_keys=raw_keys)
        captured.update(root_ids=list(root_ids), raw_keys=list(raw_keys), result=result)
        return result

    monkeypatch.setattr(SourceRunTraceQueries, "load_trace_page", capture)
    payload = client.get(_trace_path(client), params={"item_limit": 7}).json()
    assert payload["item_page"]["item_total"] == 240
    assert len(payload["item_page"]["items"]) == 7
    assert captured["root_ids"] == [f"build_{index}" for index in range(7)]
    assert len(captured["raw_keys"]) == 7
    assert {row["card_id"] for row in captured["result"]["cards"]} == {f"card_{index}" for index in range(7)}
    assert len(captured["result"]["tasks"]) <= 7 * GRAPH_NODE_LIMIT
    assert all(row["card_id"] != "card_200" for row in captured["result"]["blocks"])
    assert "non_page_marker" not in json.dumps(payload)


def test_graph_depth_limit_returns_honest_gap(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    parent = "build_0"
    for index in range(30):
        task_id = f"deep_{index}"
        repo.enqueue(Task(task_id, TaskType.CONTEXT_PACK, "card", "card_0",
                          depends_on=(DependencySpec(parent, TaskStatus.SUCCESS),),
                          artifacts_summary={"chain_id": "chain_1"}))
        _link(tmp_path, parent, task_id)
        parent = task_id
    payload = client.get(_trace_path(client)).json()
    assert "pipeline graph truncated at configured safety limit" in payload["gaps"]
    assert len(payload["item_page"]["items"][0]["stages"]) <= GRAPH_NODE_LIMIT


def test_card_products_reject_wrong_targets_invalid_summary_and_detached_decide(tmp_path):
    client = _seed_run(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    tasks = [
        Task("summary_wrong", TaskType.SUMMARY, "card", "other_card", TaskStatus.SUCCESS,
             (DependencySpec("build_0", TaskStatus.SUCCESS),), artifacts_summary={"chain_id": "chain_1"},
             input_version="summary-wrong", result_version="summary-wrong", started_at=NOW, finished_at=NOW),
        Task("summary_invalid", TaskType.SUMMARY, "card", "card_0", TaskStatus.SUCCESS,
             (DependencySpec("build_0", TaskStatus.SUCCESS),), artifacts_summary={"chain_id": "chain_1"},
             input_version="summary-invalid", result_version="summary-invalid", started_at=NOW, finished_at=NOW),
        Task("decide_detached", TaskType.DECIDE, "card", "card_0", TaskStatus.SUCCESS,
             (DependencySpec("build_0", TaskStatus.SUCCESS),),
             artifacts_summary={"chain_id": "chain_1", "decision_id": "decision_detached"},
             input_version="decide-v", result_version="decide-v", started_at=NOW, finished_at=NOW),
        Task("output_detached", TaskType.OUTPUT_GENERATE, "card", "card_0", TaskStatus.SUCCESS,
             (DependencySpec("build_0", TaskStatus.SUCCESS),),
             artifacts_summary={"chain_id": "chain_1", "decision_id": "decision_detached", "artifact_id": "output_detached"},
             input_version="output-v", result_version="output-v", started_at=NOW, finished_at=NOW),
        Task("output_wrong_target", TaskType.OUTPUT_GENERATE, "card", "other_card", TaskStatus.SUCCESS,
             (DependencySpec("decide_detached", TaskStatus.SUCCESS),),
             artifacts_summary={"chain_id": "chain_1", "decision_id": "decision_detached", "artifact_id": "output_wrong"},
             input_version="output-wrong", result_version="output-wrong", started_at=NOW, finished_at=NOW),
    ]
    for task in tasks:
        repo.enqueue(task)
    _link(tmp_path, "build_0", "summary_wrong", "summary_invalid", "decide_detached", "output_detached")
    _link(tmp_path, "decide_detached", "output_wrong_target")
    valid_body = {"signal": "s", "why_it_matters": "w", "next_action": "a",
                  "evidence": [{"card_id": "card_0", "quote": "q"}]}
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        for version, main in (("summary-wrong", "valid"), ("summary-invalid", "")):
            conn.execute("""insert into card_ai_insight_versions
                           (card_id,input_version,main_point,topic_assignment,scores,score_reasons,why_it_matters,
                            is_time_sensitive,evidence_quotes,status,model,prompt_version,generated_at)
                           values (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("other_card" if version == "summary-wrong" else "card_0", version, main, None,
                          "{}", "{}", "why", 0, "[]", "succeeded", "m", "p", NOW))
        conn.execute("""insert into decision_records
                       (decision_id,target_kind,target_id,route,reason,evidence_refs,status,created_at,updated_at)
                       values (?,?,?,?,?,?,?,?,?)""",
                     ("decision_detached", "card", "card_0", "review", "r", "[]", "open", NOW, NOW))
        for artifact_id in ("output_detached", "output_wrong"):
            conn.execute("""insert into output_artifacts
                           (artifact_id,artifact_type,source_decision_id,title,body,status,created_at,updated_at)
                           values (?,?,?,?,?,?,?,?)""",
                         (artifact_id, "decision_brief", "decision_detached", "t",
                          json.dumps(valid_body), "draft", NOW, NOW))
    stages = client.get(_trace_path(client)).json()["item_page"]["items"][0]["stages"]
    by_id = {stage["task_id"]: stage for stage in stages}
    for task_id in ("summary_wrong", "summary_invalid", "output_detached", "output_wrong_target"):
        assert _ta(task_id) not in by_id


def test_shared_human_block_semantics_exclude_llm_context_and_include_ready_derived(tmp_path):
    client = _seed_run(tmp_path)
    repo = CardContentBlockRepository(tmp_path)
    repo.upsert_block({"block_id": "legacy_llm", "card_id": "card_0", "position": 1,
                       "layer": "context", "block_kind": "llm_context", "status": "done", "body": "machine"})
    repo.upsert_block({"block_id": "ready_transcript", "card_id": "card_0", "position": 2,
                       "layer": "derived", "block_kind": "video_transcript", "status": "done", "body": "transcript"})
    repo.upsert_block({"block_id": "pending_description", "card_id": "card_0", "position": 3,
                       "layer": "derived", "block_kind": "image_description", "status": "pending", "body": "pending"})
    blocks = client.get(_trace_path(client)).json()["item_page"]["items"][0]["final_card"]["blocks"]
    ids = [block["block_id"] for block in blocks]
    assert "legacy_llm" not in ids
    assert "pending_description" not in ids
    assert "ready_transcript" in ids


def test_response_boundary_redacts_structured_secrets_paths_and_technical_ids(tmp_path):
    client = _seed_run(tmp_path)
    db = resolve_sqlite_path(tmp_path)
    connector = "https://user@example.test?token=placeholder"
    raw_id = "C:\\Users\\name\\private.json"
    root_id = "https://tasks.test/root?api_key=placeholder"
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(root_id, TaskType.CARD_BUILD, "raw_item", raw_item_target_id(connector, raw_id),
                      TaskStatus.SUCCESS, (DependencySpec("fetch_1", TaskStatus.SUCCESS),),
                      error_reason='failed {"token":"placeholder"} at C:\\Users\\name\\private.json',
                      artifacts_summary={"chain_id": "chain_1", "card_id": "card_0", "created_or_updated": "created"},
                      result_version="v1", started_at=NOW, finished_at=NOW))
    score = Task("score_safe", TaskType.SCORE, "card", "card_0", TaskStatus.SUCCESS,
                 (DependencySpec(root_id, TaskStatus.SUCCESS),),
                 artifacts_summary={"chain_id": "chain_1", "score_id": "score_safe"},
                 input_version="score-v", result_version="score-v", started_at=NOW, finished_at=NOW)
    repo.enqueue(score)
    _link(tmp_path, root_id, "score_safe")
    block_repo = CardContentBlockRepository(tmp_path)
    block_repo.upsert_block({"block_id": "https://blocks.test/id?secret=placeholder", "card_id": "card_0",
                             "position": 1, "layer": "original", "block_kind": "article", "status": "done",
                             "body": '{"api_key":"placeholder"} C:\\Users\\name\\private.json'})
    with connect(db) as conn:
        checkpoint = json.loads(conn.execute("select payload from acquisition_checkpoints where task_id='fetch_1'").fetchone()[0])
        checkpoint["raw_item_keys"] = [{"connector_id": connector, "raw_item_id": raw_id}]
        checkpoint["raw_item_ids"] = []
        checkpoint["card_build_task_ids"] = [root_id]
        conn.execute("update acquisition_checkpoints set payload=? where task_id='fetch_1'", (json.dumps(checkpoint),))
        conn.execute("update cards set title=?, content=? where card_id='card_0'",
                     ('{"password":"placeholder"}', "C:\\Users\\name\\private.json"))
        conn.execute("""insert into value_scores (score_id,target_kind,target_id,scores,score_reasons,generated_at)
                       values (?,?,?,?,?,?)""",
                     ("score_safe", "card", "card_0", json.dumps({"api_key": "placeholder", "value": 1}), "{}", NOW))
    payload = client.get(_trace_path(client)).json()
    item = payload["item_page"]["items"][0]
    assert item["connector_id"] == opaque_identifier("connector", connector)
    assert item["raw_item_id"] == opaque_identifier("raw", raw_id)
    assert item["stages"][0]["task_id"] == _ta(root_id)
    assert item["final_card"]["blocks"][0]["block_id"] == opaque_identifier(
        "block", "https://blocks.test/id?secret=placeholder"
    )
    encoded = json.dumps(payload)
    assert "placeholder" not in encoded
    assert "C:\\\\Users" not in encoded
    assert all(stage["stage"] != "score" for stage in item["stages"])


def test_opaque_aliases_and_position_cursor_never_expose_business_keys(tmp_path):
    raw_keys = (
        ("https://user@example.test?token=a", "C:\\private\\one.json"),
        ("connector?sig=b", "/Users/name/two.json"),
        ("https://signed.test/item?X-Amz-Signature=c", "raw?token=c"),
        ("connector/path", "https://raw.test/id?api_key=d"),
        ("connector@host", "D:\\private\\five.txt"),
        ("https://six.test/path#fragment", "/var/private/six"),
    )
    client = _seed_run(tmp_path, raw_keys=raw_keys)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("child_alias", TaskType.CONTENT_ENRICH, "card", "card_0",
                      depends_on=(DependencySpec("build_0", TaskStatus.SUCCESS),),
                      artifacts_summary={"chain_id": "chain_1"}))
    _link(tmp_path, "build_0", "child_alias")

    pages = []
    cursor = None
    decoded_cursors = []
    while True:
        params = {"item_limit": 2, **({"cursor": cursor} if cursor else {})}
        response = client.get(_trace_path(client), params=params)
        assert response.status_code == 200
        page = response.json()
        pages.append(page)
        cursor = page["item_page"]["next_cursor"]
        if cursor is None:
            break
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        assert set(decoded) == {"index", "v"}
        decoded_cursors.append(decode_item_cursor(cursor))
    assert decoded_cursors == [2, 4]
    aliases = [(item["connector_id"], item["raw_item_id"])
               for page in pages for item in page["item_page"]["items"]]
    assert len(aliases) == len(set(aliases)) == 6
    stages_with_child = next(
        item["stages"] for page in pages for item in page["item_page"]["items"]
        if any(stage["stage"] == "content_enrich" for stage in item["stages"])
    )
    root = next(stage for stage in stages_with_child if stage["stage"] == "card_build")
    child = next(stage for stage in stages_with_child if stage["stage"] == "content_enrich")
    assert root["downstream_task_ids"] == [child["task_id"]]
    assert child["dependencies"][0]["task_id"] == root["task_id"]

    returned_strings = []
    def collect(value):
        if isinstance(value, dict):
            for child_value in value.values():
                collect(child_value)
        elif isinstance(value, list):
            for child_value in value:
                collect(child_value)
        elif isinstance(value, str):
            returned_strings.append(value)
    collect(pages)
    encoded = "\n".join(returned_strings)
    for connector, raw_id in raw_keys:
        assert connector not in encoded
        assert raw_id not in encoded
        assert raw_item_target_id(connector, raw_id) not in encoded
    assert "raw_item:v1:" not in encoded

    def token(payload, *, canonical=True):
        text = json.dumps(payload, sort_keys=True, separators=(",", ":")) if canonical else json.dumps(payload)
        return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
    for invalid in (
        token({"index": -1, "v": 1}),
        token({"index": 2, "v": 2}),
        token({"index": 2, "v": 1}, canonical=False),
        token({"index": 999, "v": 1}),
    ):
        assert client.get(_trace_path(client), params={"cursor": invalid}).status_code == 400


def test_fetch_alias_resolver_rejects_ambiguous_match(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
    import backend.read_models.source_run_aliases as aliases

    _seed_run(tmp_path)
    queries = SourceRunTraceQueries(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    alias = "task_aaaaaaaaaaaaaaaaaaaaaaaa"
    hashed = []

    def task_alias(task_id):
        hashed.append(task_id)
        return _ta(task_id) if task_id == "fetch_unique" else alias

    monkeypatch.setattr(aliases, "task_identifier_alias", task_alias)
    assert aliases.resolve_source_run_alias(queries, alias) == "fetch_1"
    assert aliases.resolve_source_run_alias(SourceRunTraceQueries(tmp_path), alias) == "fetch_1"
    assert hashed == ["fetch_1"]
    repo.enqueue(Task("fetch_b", TaskType.FETCH, "source", "source_1"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: aliases.resolve_source_run_alias(queries, alias), range(8))) == [None] * 8
    assert hashed == ["fetch_1", "fetch_b"]
    # A miss refreshes once and can find a task inserted after cache construction.
    assert aliases.resolve_source_run_alias(queries, _ta("fetch_unique")) is None
    repo.enqueue(Task("fetch_unique", TaskType.FETCH, "source", "source_1"))
    assert aliases.resolve_source_run_alias(queries, _ta("fetch_unique")) == "fetch_unique"
    assert aliases.resolve_source_run_alias(queries, alias) is None
    assert hashed == ["fetch_1", "fetch_b", "fetch_unique"]
    other = tmp_path / "other"
    initialize_runtime_storage(other)
    _seed_run(other)
    assert aliases.resolve_source_run_alias(SourceRunTraceQueries(other), alias) == "fetch_1"
    # Building an index with an existing collision has the same ambiguous result.
    initialize_runtime_storage(tmp_path / "cold")
    _seed_run(tmp_path / "cold")
    TaskRepository.for_base_dir(tmp_path / "cold").enqueue(Task("fetch_b", TaskType.FETCH, "source", "source_1"))
    assert aliases.resolve_source_run_alias(SourceRunTraceQueries(tmp_path / "cold"), alias) is None


def test_url_sanitizer_preserves_only_strict_youtube_watch_identity():
    assert sanitize_external_url(
        "https://www.youtube.com/watch?v=demoYt00002&token=placeholder&X-Amz-Signature=placeholder#fragment"
    ) == "https://www.youtube.com/watch?v=demoYt00002"
    assert sanitize_external_url("https://youtube.com/watch?v=abc_12") == "https://youtube.com/watch?v=abc_12"
    assert sanitize_external_url("https://m.youtube.com/watch?v=bad.value") == "https://m.youtube.com/watch"
    assert sanitize_external_url("https://youtube.com/watch?v=one123&v=two456") == "https://youtube.com/watch"
    assert sanitize_external_url("https://example.test/watch?v=demoYt00002") == "https://example.test/watch"
    assert sanitize_external_url("https://youtu.be/demoYt00002?token=placeholder") == "https://youtu.be/demoYt00002"
    assert sanitize_external_url("https://user:placeholder@youtube.com/watch?v=demoYt00002") is None


def test_youtube_current_projection_keeps_video_identity(tmp_path):
    client = _seed_run(tmp_path)
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("update raw_items set source_url=? where connector_id='connector_a' and raw_item_id='raw_1'",
                     ("https://www.youtube.com/watch?v=demoYt00002&token=placeholder#fragment",))
    item = client.get(_trace_path(client)).json()["item_page"]["items"][0]
    assert item["current_projection_url"] == "https://www.youtube.com/watch?v=demoYt00002"
