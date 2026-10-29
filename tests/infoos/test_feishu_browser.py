from __future__ import annotations

import base64
from contextlib import nullcontext
import json

from backend.infrastructure.external_docs import feishu_browser
from backend.infrastructure.opencli.commands import OpenCLIResult


def _unlocked(*_args, **_kwargs):
    return nullcontext()


def test_feishu_browser_fetches_bounded_visible_body_and_media(monkeypatch):
    commands: list[list[str]] = []
    timeouts: list[int] = []

    def fake_run(args, *, timeout, executable="opencli"):
        commands.append(args)
        timeouts.append(timeout)
        return OpenCLIResult(
            stdout=json.dumps(
                [
                    {
                        "url": "https://docs.feishu.cn/docx/ABC",
                        "body": ("完整可见正文包含登录操作说明。" * 20),
                        "login_required": False,
                        "images": [
                            "https://cdn.example.com/image",
                            "http://127.0.0.1/private.jpg",
                        ],
                        "videos": ["https://cdn.example.com/video"],
                        "media_blobs": [
                            {
                                "url": "https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/all/image-token/",
                                "mime_type": "image/png",
                                "base64": base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode(),
                            },
                            {
                                "url": "https://evil.example/private.png",
                                "mime_type": "image/png",
                                "base64": base64.b64encode(b"\x89PNG\r\n\x1a\nevil").decode(),
                            },
                        ],
                    },
                ]
            )
        )

    monkeypatch.setattr(feishu_browser, "run_opencli", fake_run)
    monkeypatch.setattr(feishu_browser, "opencli_tab_lease", _unlocked)
    monkeypatch.setattr(
        feishu_browser,
        "current_opencli_context_id",
        lambda: "bound-context",
        raising=False,
    )

    result = feishu_browser.fetch_feishu_document("https://docs.feishu.cn/docx/ABC")

    assert result.status == "fetched"
    assert result.body.startswith("完整可见正文")
    assert result.images == ["https://cdn.example.com/image"]
    assert result.videos == ["https://cdn.example.com/video"]
    assert len(result.media_blobs) == 1
    assert result.media_blobs[0].content == b"\x89PNG\r\n\x1a\nimage"
    assert result.diagnostics["authenticated_media_count"] == 1
    assert commands == [[
        "--profile",
        "bound-context",
        "feishu",
        "document",
        "https://docs.feishu.cn/docx/ABC",
        "--budget-seconds",
        "170",
        "--timeout",
        "180",
        "--format",
        "json",
        "--window",
        "background",
        "--site-session",
        "ephemeral",
        "--keep-tab",
        "false",
    ]]
    assert timeouts == [230]


def test_feishu_browser_rejects_untrusted_or_credentialed_urls_without_opening_browser(monkeypatch):
    monkeypatch.setattr(
        feishu_browser,
        "run_opencli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    for url in (
        "http://docs.feishu.cn/docx/ABC",
        "https://docs.feishu.cn.evil.example/docx/ABC",
        "https://user:pass@docs.feishu.cn/docx/ABC",
        "https://127.0.0.1/docx/ABC",
    ):
        result = feishu_browser.fetch_feishu_document(url)
        assert result.status == "blocked"
        assert result.reason == "external_doc_url_not_allowed"


def test_feishu_browser_marks_login_and_cross_domain_redirect_as_blocked(monkeypatch):
    payloads = [
        {
            "url": "https://passport.feishu.cn/login",
            "body": "请扫码登录" * 20,
            "login_required": True,
        },
        {
            "url": "https://evil.example/redirect",
            "body": "unexpected" * 20,
            "login_required": False,
        },
    ]

    for payload, expected_reason in zip(
        payloads,
        ("external_doc_login_required", "external_doc_redirect_not_allowed"),
        strict=True,
    ):
        def fake_run(_args, *, timeout, executable="opencli"):
            return OpenCLIResult(stdout=json.dumps([payload]))

        monkeypatch.setattr(feishu_browser, "run_opencli", fake_run)
        monkeypatch.setattr(feishu_browser, "opencli_tab_lease", _unlocked)

        result = feishu_browser.fetch_feishu_document("https://docs.feishu.cn/docx/ABC")

        assert result.status == "blocked"
        assert result.reason == expected_reason


def test_feishu_browser_diagnostics_never_copy_command_output(monkeypatch):
    monkeypatch.setattr(feishu_browser, "opencli_tab_lease", _unlocked)
    monkeypatch.setattr(
        feishu_browser,
        "run_opencli",
        lambda *_args, **_kwargs: OpenCLIResult(
            returncode=1,
            stdout="cookie=secret",
            stderr="X-TOKEN=secret",
        ),
    )

    result = feishu_browser.fetch_feishu_document("https://docs.feishu.cn/docx/ABC")

    assert result.status == "pending"
    assert result.diagnostics == {"returncode": 1}
    assert "secret" not in repr(result)


def test_feishu_browser_propagates_completeness_and_small_readable_body(monkeypatch):
    monkeypatch.setattr(feishu_browser, "opencli_tab_lease", _unlocked)
    for complete in (True, False):
        payload = {"url": "https://docs.feishu.cn/docx/ABC", "body": "短文正文",
                   "complete": complete, "completeness": "complete" if complete else "partial",
                   "reached_end": complete, "blocks": 4}
        monkeypatch.setattr(feishu_browser, "run_opencli",
                            lambda *_args, **_kwargs: OpenCLIResult(stdout=json.dumps([payload])))
        result = feishu_browser.fetch_feishu_document(payload["url"])
        assert result.status == "fetched"
        assert result.complete is complete and result.blocks == 4
        assert result.reached_end is complete
        assert result.reason == (None if complete else "external_doc_partial")
