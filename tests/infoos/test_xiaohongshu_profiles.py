"""Behavior of the bounded profile fallback and independently versioned vocabularies."""
import json

import pytest

from backend.domain.sources import monetization_prefilter as policy
from backend.domain.sources.versioning import source_fetch_input_version
from backend.domain.sources.xiaohongshu_prefilter import screen_record, run_evidence
from backend.infrastructure.connectors.xiaohongshu_profiles import enrich_profiles
from tests.infoos.test_xiaohongshu_prefilter import source, run_batch
from backend.application.tasks.handlers.acquisition import handle_card_build
from backend.application.tasks.repository import TaskRepository
from backend.infrastructure.storage.repositories import CardContentBlockRepository, RawItemRepository


def rules():
    return policy.xhs_rules({"monetization_prefilter": True})


def record(user="author0001", text="日常", comments=None):
    return screen_record({"id": user, "author_user_id": user, "content": text,
                          "_opencli_detail_record": [{"field": "content", "value": text}],
                          "comments_ok": True, "first_screen_comments": comments or []}, rules())


def profile(user="author0001", **changes):
    return {"user_id": user, "profile_checked": True, "profile_ok": True, "description": "日常",
            "pinned_ok": True, "pinned_titles": [], "group_entry": False, "shop_entry": False, **changes}


@pytest.mark.parametrize("text,hit", [("私下", False), ("私人", False), ("隐私", False),
                                     ("私信", True), ("私我", True), ("私聊", True),
                                     ("私戳", True), ("私V", True), ("私滴", True)])
def test_private_tokens_both_platforms(text, hit):
    for vocab, weak in ((policy.TOKENS, policy.WEAK_TOKENS), (policy.XHS_TOKENS, policy.XHS_WEAK_TOKENS)):
        assert "私" not in vocab["contact"] and "戳" in vocab["contact"]
        assert bool(policy.xhs_text_hits(text, "signature", {"tokens": vocab, "token_strengths": policy.token_strengths(vocab, weak)})) is hit


@pytest.mark.parametrize("changes,status,field", [
    ({"description": "私信"}, "matched", "author_signature"),
    ({"pinned_titles": ["课程介绍"]}, "matched", "author_pinned"),
    ({"group_entry": True}, "matched", "author_group_entry"),
    ({"shop_entry": True}, "missed", None),
    ({"description": "求教程多少钱蹲"}, "missed", None),
    ({"profile_ok": False}, "unknown", None),
    ({"pinned_ok": False}, "unknown", None),
])
def test_profile_signals(changes, status, field):
    result = enrich_profiles([record()], rules(), lambda _: [profile(**changes)])[0]
    assert result["prefilter"]["status"] == status
    if field:
        assert result["prefilter"]["hits"][0]["field"] == field
    if field == "author_group_entry":
        assert result["prefilter"]["hits"] == [{"field": field, "token": "visible", "strength": "strong"}]


def test_text_hits_skip_profile_author_dedup_limit_and_summary(monkeypatch):
    monkeypatch.setattr("backend.infrastructure.connectors.xiaohongshu_profiles.time.sleep", lambda _: None)
    calls = []
    def load(command):
        calls.append(command)
        return [profile(u, description="课程") for u in json.loads(command[-1])]
    rows = [record("texthit000", "课程"), record(), record()]
    rows += [record(f"author{i:04d}") for i in range(2, 23)]
    results = enrich_profiles(rows, rules(), load)
    targets = [user for call in calls for user in json.loads(call[-1])]
    assert len(calls) == 4 and len(targets) == len(set(targets)) == 20
    assert "texthit000" not in targets
    assert [r["prefilter"]["status"] for r in results[-2:]] == ["unknown", "unknown"]
    summary = run_evidence(results, rules())["prefilter_summary"]
    assert summary["profiles_checked"] == summary["profiles_ok"] == 20
    assert summary["profile_hit_items"] == 21  # Shared author still accounts for both items.
    assert summary["comments_checked_items"] == len(rows) - 1
    assert summary["profile_batches"] == 4
    assert summary["profile_batches_failed"] == 0 and summary["profile_batch_errors"] == {}
    assert results[0]["prefilter"]["profile_checked"] is False


def test_failed_batch_and_missing_author_are_unknown_without_retry():
    calls = []
    def fail(command):
        calls.append(command)
        raise RuntimeError("timeout")
    rows = enrich_profiles([record(), record(""), record("invalid/id")], rules(), fail)
    assert len(calls) == 1
    assert all(r["prefilter"]["status"] == "unknown" for r in rows)
    assert run_evidence(rows, rules())["prefilter_summary"]["profiles_checked"] == 0
    assert rows[0]["prefilter"]["profile_checked"] is None
    assert run_evidence(rows, rules())["prefilter_summary"]["profiles_attempted_unknown"] == 1


def test_comments_threshold_excludes_author_and_skips_profiles():
    comments = [{"text": "求教程求分享", "is_author": False},
                {"text": "多少钱", "is_author": False}, {"text": "求教程", "is_author": True}]
    def no_profile(_):
        pytest.fail("Comment hit must not open a profile")
    result = enrich_profiles([record(comments=comments)], rules(), no_profile)[0]
    assert result["prefilter"]["demand_comment_count"] == 2
    assert result["prefilter"]["status"] == "matched"
    summary = run_evidence([result], rules())["prefilter_summary"]
    assert summary["comments_checked_items"] == summary["comments_ok_items"] == summary["comment_hit_items"] == 1
    assert result["prefilter"]["profile_checked"] is False
    assert summary["profile_batches"] == 0
    result = enrich_profiles([record(comments=comments[1:])], rules(), lambda _: [profile()])[0]
    assert result["prefilter"]["status"] == "missed"
    assert run_evidence([result], rules())["prefilter_misses"][0]["coverage"] == [
        "title", "content", "tags", "comments", "author_signature", "author_pinned", "author_group_entry"]
    row = record(); row["comments_ok"] = False
    result = enrich_profiles([screen_record(row, rules())], rules(), lambda _: [profile()])[0]
    assert result["prefilter"]["status"] == "unknown"


def test_rule_and_vocabulary_fingerprints_are_independent(monkeypatch):
    assert policy.RULES_VERSION == "prefilter_v5" and policy.XHS_RULES_VERSION == "xhs_prefilter_v4"
    xhs = source(raw_metadata={"monetization_prefilter": True})
    dy = {**xhs, "platform": "douyin"}
    x0, d0 = source_fetch_input_version(xhs), source_fetch_input_version(dy)
    with monkeypatch.context() as m:
        m.setattr(policy, "XHS_RULES_VERSION", "next_xhs")
        m.setattr(policy, "XHS_TOKENS", {**policy.XHS_TOKENS, "contact": ("changed",)})
        assert source_fetch_input_version(xhs) != x0
        assert source_fetch_input_version(dy) == d0
    with monkeypatch.context() as m:
        m.setattr(policy, "RULES_VERSION", "next_douyin")
        m.setattr(policy, "TOKENS", {**policy.TOKENS, "contact": ("changed",)})
        assert source_fetch_input_version(dy) != d0
        assert source_fetch_input_version(xhs) == x0


def test_profile_blocks_persist_description_pinned_and_summary(tmp_path, monkeypatch):
    _, connector, task, _, _ = run_batch(tmp_path, monkeypatch, ["课程"])
    load = connector._load_payload
    def changed(command=None):
        if command and command[1] == "screening-profiles":
            return [profile("author0000", description="个人介绍", pinned_titles=["课程说明"])]
        result = load(command)
        if command:
            result = [r if r["field"] != "content" else {"field": "content", "value": "日常"} for r in result]
        return result
    monkeypatch.setattr(connector, "_load_payload", changed)
    item = connector.fetch_new_items(None).items[0]
    RawItemRepository(tmp_path).upsert_raw_items([item])
    # Use the existing queued card build so this checks the real block writer.
    tasks = TaskRepository.for_base_dir(tmp_path)
    from backend.application.tasks.types import TaskType
    build = next(t for t in tasks.list_pending() if t.task_type == TaskType.CARD_BUILD)
    card = handle_card_build(build, tmp_path)["artifacts"]["card_id"]
    blocks = {b["block_kind"]: b for b in CardContentBlockRepository(tmp_path).list_for_card(card)}
    assert "个人介绍" in blocks["author_profile"]["body"] and "课程说明" in blocks["author_profile"]["body"]
    assert "author_pinned" in blocks["screening_evidence"]["body"]
    assert connector.prefilter_evidence["prefilter_summary"]["profile_hit_items"] == 1


@pytest.mark.parametrize("failure", ["timeout", "stale"])
def test_profile_process_never_retries_on_timeout_or_stale_page(monkeypatch, failure):
    import subprocess
    from backend.infrastructure.connectors.opencli import OpenCLIConnector
    calls = []
    def run(command, **options):
        calls.append((command, options))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"])
        return subprocess.CompletedProcess(command, 1, "", "stale page identity")
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", run)
    monkeypatch.setattr("backend.infrastructure.connectors.opencli.resolve_opencli_executable", lambda _: "opencli")
    connector = OpenCLIConnector("fixture", ["xiaohongshu", "feed"], source_platform="xiaohongshu",
                                 use_browser_window=False)
    rows = enrich_profiles([record()], rules(), connector._load_payload)
    assert len(calls) == 1 and calls[0][1]["timeout"] == 600
    assert rows[0]["prefilter"]["status"] == "unknown"
    evidence = rows[0]["prefilter"]
    assert evidence["profile_error"] == ("batch_timeout" if failure == "timeout" else "batch_failed")
    assert ("TimeoutExpired" if failure == "timeout" else "stale page identity") in evidence["profile_error_detail"]


def test_missing_batch_rows_preserve_unknown_attempt_count():
    rows = enrich_profiles([record()], rules(), lambda _: [])
    summary = run_evidence(rows, rules())["prefilter_summary"]
    assert summary["profiles_attempted_unknown"] == 1 and summary["unknown"] == 1
    assert summary["profile_batches_failed"] == 1
    assert summary["profile_batch_errors"] == {"batch_failed": 1}


@pytest.mark.parametrize("failure", [None, "exception", "risk", "navigation"])
def test_twelve_authors_batches_gap_isolation_and_risk_stop(monkeypatch, failure):
    events = []
    users = [f"author{i:04d}" for i in range(12)]
    monkeypatch.setattr("backend.infrastructure.connectors.xiaohongshu_profiles.time.sleep",
                        lambda seconds: events.append(("wait", seconds)))
    calls = []
    def load(command):
        batch = json.loads(command[-1])
        calls.append(batch)
        events.append(("load", len(batch)))
        if len(calls) == 2:
            if failure == "exception":
                raise RuntimeError(
                    f"failed {batch} https://example.com/user/profile/{users[5]}?xsec_token=url-secret&foo=query-secret "
                    "token=plain-secret access_token=access-secret --token cli-secret "
                    "Bearer bearer-secret " + "原因" * 400)
            if failure in {"risk", "navigation"}:
                return [{"user_id": u, "profile_checked": i == 0, "profile_ok": False,
                         "error": ("login_or_risk" if failure == "risk" else "profile_unavailable")
                         if i == 0 else "batch_stopped"} for i, u in enumerate(batch)]
        return [profile(user) for user in batch]
    rows = enrich_profiles([record(u) for u in users] + [record(users[5])], rules(), load)
    expected = [("load", 5), ("wait", 3), ("load", 5)]
    if failure != "risk":
        expected += [("wait", 3), ("load", 2)]
    assert events == expected
    assert [u for batch in calls for u in batch] == users[:10 if failure == "risk" else 12]
    assert all(r["prefilter"]["status"] == "missed" for r in rows[:5])
    assert all(r["prefilter"]["status"] == ("unknown" if failure else "missed") for r in rows[5:10])
    assert all(r["prefilter"]["status"] == ("unknown" if failure == "risk" else "missed") for r in rows[10:12])
    if failure == "exception":
        for row in rows[5:10] + rows[-1:]:
            evidence = row["prefilter"]
            assert evidence["profile_error"] == "batch_failed"
            detail = evidence["profile_error_detail"]
            assert detail.startswith("RuntimeError:") and len(detail) <= 300
            assert not any(u in detail for u in users)
            assert "secret" not in detail and "?" not in detail and "foo=" not in detail
    if failure == "risk":
        assert all(r["prefilter"]["profile_checked"] is False for r in rows[10:12])
        assert all(r["prefilter"]["profile_error"] == "login_or_risk" for r in rows[10:12])
    summary = run_evidence(rows, rules())["prefilter_summary"]
    assert summary["profile_batches"] == (2 if failure == "risk" else 3)
    assert summary["profile_batches_failed"] == bool(failure)
    assert summary["profile_batch_errors"] == (
        {"login_or_risk" if failure == "risk" else "batch_failed": 1} if failure else {})


def test_comments_unavailable_or_below_threshold_still_try_profile():
    for comments_ok, comments in [(False, []), (True, [{"text": "求教程", "is_author": False}])]:
        row = record(comments=comments)
        row = screen_record({**row, "comments_ok": comments_ok}, rules())
        calls = []
        def load(command):
            calls.append(command)
            return [profile(description="课程")]
        result = enrich_profiles([row], rules(), load)[0]["prefilter"]
        assert len(calls) == 1 and result["status"] == "matched"
        assert result["comments_checked"] is True


def test_exception_text_mentioning_risk_does_not_stop_later_batches(monkeypatch):
    monkeypatch.setattr("backend.infrastructure.connectors.xiaohongshu_profiles.time.sleep", lambda _: None)
    calls = []
    def load(command):
        calls.append(command)
        raise RuntimeError("login_or_risk")
    rows = enrich_profiles([record(f"author{i:04d}") for i in range(12)], rules(), load)
    assert len(calls) == 3
    summary = run_evidence(rows, rules())["prefilter_summary"]
    assert summary["profile_batches_failed"] == 3
    assert summary["profile_batch_errors"] == {"batch_failed": 3}


@pytest.mark.parametrize("message", [
    "author0001 https://example.com/user/profile/author0001?foo=secret-value&xsec_token=secret-value",
    'token=secret-value access_token: secret-value --xsec-token secret-value',
    '''{'xsec_token': 'secret-value', 'token': 'secret-value with spaces'}''',
    '''['--token', 'secret-value', '--access-token', 'secret-value']''',
    'Authorization: Bearer secret-value',
])
def test_opencli_stderr_error_detail_is_redacted(message):
    from backend.infrastructure.connectors.opencli import OpenCLICommandError
    def load(_):
        raise OpenCLICommandError(1, stderr_summary=message)
    evidence = enrich_profiles([record()], rules(), load)[0]["prefilter"]
    detail = evidence["profile_error_detail"]
    assert detail.startswith("OpenCLICommandError:")
    assert "[" in detail and len(detail) <= 300
    assert "secret-value" not in detail and "author0001" not in detail
    assert "?" not in detail and "foo=" not in detail
