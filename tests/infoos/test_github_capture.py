"""GitHub capture from fixed public API/HTML samples through durable card build."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.application.tasks.executor import run_tasks_by_ids
from backend.application.tasks.lanes import ExecutionLane, execution_lane_and_site_for
from backend.application.tasks.repository import TaskRepository
from backend.application.usecases.manual_capture import submit_manual_capture
from backend.application.usecases.api_tokens import create_token
from backend.application.workflows import capture_fetch
from backend.infrastructure.connectors import github
from backend.infrastructure.storage.capture_receipts import CaptureReceiptRepository
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.repositories import CardRepository


_FIXTURES = Path(__file__).parent / "fixtures" / "github_capture"
_API = "https://api.github.com/repos/example/project"
_URL = "https://github.com/example/project"


@pytest.fixture
def capture_token_id(tmp_path):
    return create_token(tmp_path, name="Fixture", scopes=["captures:write"])["token"]["token_id"]


def _responses(kind):
    if kind == "repository":
        return {_API: "repository.json", f"{_API}/readme": "readme.html"}
    if kind == "issue":
        return {f"{_API}/issues/1": "issue.json"}
    if kind == "pull_request":
        return {f"{_API}/issues/2": "pull_request.json"}
    if kind == "release":
        return {f"{_API}/releases/tags/v1.0.0": "release.json"}
    return {f"{_URL}/discussions/1": "discussion.html"}


@pytest.mark.parametrize("kind,path,title,body", [
    ("repository", "", "example/project", "Model Context Protocol servers"),
    ("issue", "/issues/1", "Example issue", "I have documented all environment variables"),
    ("pull_request", "/pull/2", "Example pull_request", "npm install @modelcontextprotocol/servers"),
    ("release", "/releases/tag/v1.0.0", "Example release", "Security fixes"),
    ("discussion", "/discussions/1", "Example discussion", "This is the place for you to make feature suggestions"),
])
def test_github_capture_builds_title_and_main_body(tmp_path, monkeypatch, capture_token_id, kind, path, title, body):
    responses = _responses(kind)
    calls = []

    def read(url, **kwargs):
        calls.append(url)
        assert kwargs["max_bytes"] == 5 * 1024 * 1024
        assert kwargs["allowed_hosts"] == (frozenset({"github.com", "www.github.com"}) if kind == "discussion" else frozenset({"api.github.com"}))
        if url.endswith("/readme"):
            assert kwargs["headers"]["Accept"] == "application/vnd.github.html+json"
        elif kind != "discussion":
            assert kwargs["headers"]["Accept"] == "application/vnd.github.full+json"
        return (_FIXTURES / responses[url]).read_bytes(), SimpleNamespace(final_url=url)

    monkeypatch.setattr(github, "read_public_url", read)
    receipt = submit_manual_capture(tmp_path, token_id=capture_token_id, payload={
        "url": _URL + path, "client_capture_id": kind, "capture_mode": "url_only",
        "captured_at": "2026-10-27T07:20:17Z",
    }).receipt
    task = TaskRepository.for_base_dir(tmp_path).get(receipt["task_id"])
    assert task.artifacts_summary["capture_adapter"] == "github:item"
    assert task.queue_name == "public_fetch"
    assert execution_lane_and_site_for(task, tmp_path) == (ExecutionLane.PUBLIC_ACQUISITION, None)

    result = run_tasks_by_ids(tmp_path, [receipt["task_id"]], max_workers=1)
    assert result["succeeded"] == 1, result
    raw = RawItemRepository(tmp_path).get(receipt["raw_item_id"])
    assert raw["title"] == title
    assert body in raw["raw_text"]
    assert raw["captured_at"] == "2026-10-27T07:20:17Z"
    assert raw["source_type"] == "capture"
    assert raw["raw_payload"]["raw_metadata"]["github_item_type"] == kind

    built = run_tasks_by_ids(tmp_path, result["results"][0]["downstream_task_ids"], max_workers=1)
    assert built["succeeded"] == 1, built
    stored = CaptureReceiptRepository(tmp_path).get(receipt["capture_id"])
    card = CardRepository(tmp_path).get_card(stored["card_id"])
    assert card["title"] == title
    assert body in card["content"]
    assert card["source_url"] == _URL + path
    for noise in ("Navigation Menu", "Sign in", "You must be logged in", "Another person's reply", "Terms Privacy"):
        assert noise not in card["content"]
        assert noise not in raw["raw_payload"]["raw_metadata"]["sanitized_readable_html"]
    assert calls == list(responses)


def test_github_readable_capture_replaces_page_frame_and_media(tmp_path, monkeypatch, capture_token_id):
    responses = _responses("repository")
    monkeypatch.setattr(github, "read_public_url", lambda url, **kwargs: (
        (_FIXTURES / responses[url]).read_bytes(), SimpleNamespace(final_url=url),
    ))
    receipt = submit_manual_capture(tmp_path, token_id=capture_token_id, payload={
        "url": _URL, "client_capture_id": "readable", "capture_mode": "readable_content",
        "title": "GitHub", "captured_at": "2026-10-27T07:20:17Z",
        "rendered_content": {"html": '<nav>Navigation Menu<img src="https://example.com/avatar.png"></nav>', "text": "Navigation Menu"},
    }).receipt
    result = run_tasks_by_ids(tmp_path, [receipt["task_id"]], max_workers=1)
    assert result["succeeded"] == 1, result
    raw = RawItemRepository(tmp_path).get(receipt["raw_item_id"])
    metadata = raw["raw_payload"]["raw_metadata"]
    assert raw["title"] == "example/project"
    assert "Model Context Protocol servers" in raw["raw_text"]
    assert "Navigation Menu" not in raw["raw_text"]
    assert "https://example.com/avatar.png" not in metadata.get("images", [])


@pytest.mark.parametrize("capture_mode", ["url_only", "readable_content"])
def test_github_blob_capture_uses_web_fallback(tmp_path, monkeypatch, capture_token_id, capture_mode):
    url = _URL + "/blob/main/README.md"
    body = "File contents from the public web" if capture_mode == "url_only" else "File contents from the extension"
    public_calls = []

    def public_document(request):
        public_calls.append(request.url)
        return SimpleNamespace(
            document=SimpleNamespace(
                final_url=url, content=body, readable_html=f"<p>{body}</p>", images=(), videos=(),
            ),
            provider_evidence={},
        )

    def github_read(*args, **kwargs):
        pytest.fail("Unsupported GitHub URLs must not call the GitHub adapter")

    monkeypatch.setattr(capture_fetch, "acquire_public_document_from_environment", public_document)
    monkeypatch.setattr(github, "read_public_url", github_read)
    receipt = submit_manual_capture(tmp_path, token_id=capture_token_id, payload={
        "url": url, "client_capture_id": "blob", "capture_mode": capture_mode,
        "title": "README.md", "captured_at": "2026-10-27T07:20:17Z",
        "rendered_content": {"html": f"<p>{body}</p>", "text": body} if capture_mode == "readable_content" else None,
    }).receipt
    task = TaskRepository.for_base_dir(tmp_path).get(receipt["task_id"])
    assert task.artifacts_summary["capture_adapter"] == "web"
    assert task.artifacts_summary["queue_platform"] == "web"
    assert task.queue_name == "public_fetch"
    assert execution_lane_and_site_for(task, tmp_path) == (ExecutionLane.PUBLIC_ACQUISITION, None)

    result = run_tasks_by_ids(tmp_path, [receipt["task_id"]], max_workers=1)
    assert result["succeeded"] == 1, result
    raw = RawItemRepository(tmp_path).get(receipt["raw_item_id"])
    assert raw["source_platform"] == "github"
    assert raw["raw_payload"]["raw_metadata"]["capture_adapter"] == "web"
    assert raw["raw_text"] == body
    assert public_calls == ([url] if capture_mode == "url_only" else [])

    built = run_tasks_by_ids(tmp_path, result["results"][0]["downstream_task_ids"], max_workers=1)
    assert built["succeeded"] == 1, built
    stored = CaptureReceiptRepository(tmp_path).get(receipt["capture_id"])
    card = CardRepository(tmp_path).get_card(stored["card_id"])
    assert card["source_url"] == url
    assert body in card["content"]


def test_discussion_without_main_post_fails_instead_of_using_navigation(tmp_path, monkeypatch, capture_token_id):
    monkeypatch.setattr(github, "read_public_url", lambda url, **kwargs: (
        b'<nav>Navigation Menu Sign in</nav><span class="js-issue-title">Example</span>',
        SimpleNamespace(final_url=url),
    ))
    receipt = submit_manual_capture(tmp_path, token_id=capture_token_id, payload={
        "url": _URL + "/discussions/1", "client_capture_id": "missing-body",
        "capture_mode": "url_only", "captured_at": "2026-10-27T07:20:17Z",
    }).receipt
    result = run_tasks_by_ids(tmp_path, [receipt["task_id"]], max_workers=1)
    assert result["succeeded"] == 0
    assert result["requeued"] == 1
    task = TaskRepository.for_base_dir(tmp_path).get(receipt["task_id"])
    assert "github_discussion_body_or_title_missing" in task.error_reason
    stored = CaptureReceiptRepository(tmp_path).get(receipt["capture_id"])
    assert stored["card_id"] is None


def _run_capture_to_terminal(tmp_path, capture_token_id, *, url, client_id):
    receipt = submit_manual_capture(tmp_path, token_id=capture_token_id, payload={
        "url": url, "client_capture_id": client_id, "capture_mode": "url_only",
        "captured_at": "2026-10-27T07:20:17Z",
    }).receipt
    pending = [receipt["task_id"]]
    tasks = []
    for _ in range(6):
        if not pending:
            break
        result = run_tasks_by_ids(tmp_path, pending, max_workers=1)
        assert result["requeued"] == 0, result
        tasks.extend(TaskRepository.for_base_dir(tmp_path).get(task_id) for task_id in pending)
        pending = [child for item in result["results"] for child in item["downstream_task_ids"]]
    assert not pending
    stored = CaptureReceiptRepository(tmp_path).get(receipt["capture_id"])
    assert stored["processing_status"] == "succeeded"
    card = CardRepository(tmp_path).get_card(stored["card_id"])
    assert card["status"] == "active"
    return receipt, card, tasks


@pytest.mark.parametrize("media_outcome", ["badges_only", "downloaded", "unavailable"])
def test_github_readme_optional_media_reaches_finalize(tmp_path, monkeypatch, capture_token_id, media_outcome):
    from backend.application.enrichment import web_content
    from backend.application.enrichment.media import artifacts
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    html = (_FIXTURES / "media.html").read_text()
    if media_outcome == "badges_only":
        html = html[:html.index('<img src="https://github.com/example/project/assets/')]

    monkeypatch.setattr(github, "read_public_url", lambda url, **kwargs: (
        html.encode() if url.endswith("/readme") else (_FIXTURES / "repository.json").read_bytes(),
        SimpleNamespace(final_url=url),
    ))
    monkeypatch.setattr(web_content, "read_public_url", lambda *args, **kwargs: pytest.fail("Do not refetch GitHub pages"))
    calls = []

    class ImageResponse:
        headers = {"content-type": "image/png"}

        def iter_content(self, **kwargs):
            yield b"\x89PNG\r\n\x1a\nfixed-example-image"

        def close(self):
            pass

    def open_media(url, **kwargs):
        calls.append(url)
        # Model a TLS failure for the benchmark; a camo raster can still succeed.
        if media_outcome == "unavailable" or "/assets/" in url:
            raise OSError("fixture TLS connection failed")
        return ImageResponse(), url

    monkeypatch.setattr(artifacts, "_open_public_url", open_media)
    receipt, card, tasks = _run_capture_to_terminal(tmp_path, capture_token_id, url=_URL, client_id=media_outcome)
    raw = RawItemRepository(tmp_path).get(receipt["raw_item_id"])
    images = raw["raw_payload"]["raw_metadata"]["images"]
    expected = [] if media_outcome == "badges_only" else [
        "https://github.com/example/project/assets/1/benchmark",
        "https://camo.githubusercontent.com/example/68747470733a2f2f6578616d706c652e636f6d2f73637265656e73686f742e706e67",
    ]
    assert images == expected
    assert calls == expected
    assets = CardAssetRepository(tmp_path).list_for_card(card["card_id"])
    assert len(assets) == (1 if media_outcome == "downloaded" else 0)
    if assets:
        assert assets[0]["remote_url"] == expected[1]
        assert (tmp_path / "data/assets" / assets[0]["local_path"]).read_bytes().startswith(b"\x89PNG")
    blocks = CardContentBlockRepository(tmp_path).list_for_card(card["card_id"])
    gaps = [block for block in blocks if block["block_kind"] == "media_download_gap"]
    assert bool(gaps) == (media_outcome == "unavailable")
    assert card["completeness_status"] == ("partial" if gaps else "complete")
    media_task = next(task for task in tasks if task.task_type.value == "media_download")
    assert media_task.status.value == ("success" if assets else "skipped")
    if gaps:
        assert gaps[0]["raw_payload"]["reason"] == "direct_media_download_failed"
        assert media_task.artifacts_summary["download_status"] == "skipped_optional_media_unavailable"


def test_github_short_issue_does_not_refetch_own_page(tmp_path, monkeypatch, capture_token_id):
    from backend.application.enrichment import web_content
    from backend.application.enrichment.media import artifacts
    from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository

    monkeypatch.setattr(github, "read_public_url", lambda url, **kwargs: (
        (_FIXTURES / "short_issue.json").read_bytes(), SimpleNamespace(final_url=url),
    ))
    monkeypatch.setattr(web_content, "read_public_url", lambda *args, **kwargs: pytest.fail("Do not fetch the Issue page frame"))
    monkeypatch.setattr(artifacts, "_open_public_url", lambda *args, **kwargs: pytest.fail("No page-frame images"))
    receipt, card, _ = _run_capture_to_terminal(tmp_path, capture_token_id, url=_URL + "/issues/1", client_id="short-issue")
    raw = RawItemRepository(tmp_path).get(receipt["raw_item_id"])
    assert len(raw["raw_text"]) < 80
    assert "Run tests, formatting, linting." in card["content"]
    assert 'href="https://example.com/guide"' in raw["raw_payload"]["raw_metadata"]["sanitized_readable_html"]
    assert not CardAssetRepository(tmp_path).list_for_card(card["card_id"])
    assert not any(block["block_kind"] == "linked_article" for block in CardContentBlockRepository(tmp_path).list_for_card(card["card_id"]))
    assert card["completeness_status"] == "complete"
