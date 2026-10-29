"""Login verification: per-platform probe that returns a clean status enum."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.setup import verify  # noqa: E402
from backend.application.setup.state import AccountStatus  # noqa: E402
from backend.infrastructure.browser import session_state  # noqa: E402
from backend.infrastructure.runtime.environment import chrome_profile_dir  # noqa: E402


@pytest.fixture(autouse=True)
def dedicated_browser_session(tmp_path, monkeypatch):
    session_path = tmp_path / "browser-session.json"
    monkeypatch.setattr(session_state, "JSON_PATH", session_path)
    session_state._cache_clear()
    session_state.write(
        session_state.BrowserSessionState(
            schema_version=1,
            profile_dir=str(chrome_profile_dir()),
            context_id="ctx-dedicated",
            alias="infoos-fetch",
            started_at="2026-06-21T00:00:00Z",
            last_health_at="2026-06-21T00:00:00Z",
            last_health_status="ready",
        )
    )
    yield
    session_state._cache_clear()


def _stub_subprocess(monkeypatch, *, returncode=0, stdout="", stderr=""):
    """Make ``_SubprocessResult.from_call`` return a canned response.

    The probe goes through ``_run_opencli_command`` → ``_SubprocessResult.from_call``
    → ``subprocess.run``. Patching ``from_call`` is the cleanest hook:
    it lets us return arbitrary output without spawning a process.
    """
    from backend.application.setup.verify import _SubprocessResult

    def fake_from_call(args, *, timeout):
        return _SubprocessResult(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(_SubprocessResult, "from_call", staticmethod(fake_from_call))


def test_verify_x_logged_in(monkeypatch):
    # `twitter profile` returns the logged-in user's identity envelope.
    _stub_subprocess(
        monkeypatch,
        returncode=0,
        stdout='{"ok":true,"screen_name":"me","id":"12345"}\n',
    )
    result = verify.probe_platform("x")
    assert result.status == AccountStatus.VERIFIED
    assert result.error is None


def test_probe_platform_blocks_without_dedicated_browser_session(monkeypatch):
    session_state.JSON_PATH.unlink()
    session_state._cache_clear()
    calls: list[list[str]] = []

    from backend.application.setup.verify import _SubprocessResult

    def fake_from_call(args, *, timeout):
        calls.append(list(args))
        return _SubprocessResult(returncode=0, stdout='{"screen_name":"daily"}\n')

    monkeypatch.setattr(_SubprocessResult, "from_call", staticmethod(fake_from_call))

    result = verify.probe_platform("x")

    assert result.status == AccountStatus.NEEDS_LOGIN
    assert "当前实例未找到有效的专用浏览器会话记录" in (result.error or "")
    assert "browser_connect" in (result.error or "")
    assert calls == []


def test_verify_x_logged_out_via_ct0(monkeypatch):
    _stub_subprocess(
        monkeypatch,
        returncode=77,
        stderr='{"error":{"code":"AUTH_REQUIRED","message":"no ct0 cookie"}}\n',
    )
    result = verify.probe_platform("x")
    assert result.status == AccountStatus.NEEDS_LOGIN


def test_verify_x_logged_out_via_browser_connect(monkeypatch):
    # OpenCLI 1.8.0 returns rc=0 with body ok=false BROWSER_CONNECT when
    # the Browser Bridge profile is not connected to a logged-in Chrome.
    _stub_subprocess(
        monkeypatch,
        returncode=0,
        stdout=(
            '{"ok":false,"error":{"code":"BROWSER_CONNECT",'
            '"message":"Browser profile \\"demo0001\\" is not connected"}}\n'
        ),
    )
    result = verify.probe_platform("x")
    assert result.status == AccountStatus.NEEDS_LOGIN
    assert "not connected" in result.error


def test_verify_x_failed_when_unexpected_error(monkeypatch):
    _stub_subprocess(monkeypatch, returncode=1, stderr="network error\n")
    result = verify.probe_platform("x")
    assert result.status == AccountStatus.FAILED
    assert "network error" in result.error


def test_verify_x_logged_in_yaml_fallback(monkeypatch):
    # OpenCLI 1.8.0 may emit a YAML-style identity envelope on success
    # rather than JSON. The probe must recognize it.
    _stub_subprocess(
        monkeypatch,
        returncode=0,
        stdout="screen_name: me\nid: '12345'\nname: Me\n",
    )
    result = verify.probe_platform("x")
    assert result.status == AccountStatus.VERIFIED
    assert result.error is None


def test_verify_x_logged_out_yaml_envelope(monkeypatch):
    # Real-world OpenCLI 1.8.0 output when the Browser Bridge profile
    # is not connected: rc=69, YAML envelope on stderr.
    _stub_subprocess(
        monkeypatch,
        returncode=69,
        stderr=(
            "ok: false\nerror:\n"
            '  code: BROWSER_CONNECT\n'
            '  message: Browser profile "demo0001" is not connected\n'
        ),
    )
    result = verify.probe_platform("x")
    assert result.status == AccountStatus.NEEDS_LOGIN
    assert "is not connected" in result.error


def test_verify_x_logged_in_list_envelope(monkeypatch):
    # OpenCLI 1.8.0 twitter profile returns a JSON *array* with one
    # identity object on success. The probe must recognize the array
    # form and pull the first element's screen_name/id.
    payload = (
        '[{"bio":"走走走","screen_name":"example_account","id":"12345",'
        '"followers":15,"following":99,"verified":false,"tweets":0}]\n'
    )
    _stub_subprocess(monkeypatch, returncode=0, stdout=payload)
    result = verify.probe_platform("x")
    assert result.status == AccountStatus.VERIFIED
    assert result.error is None


def test_verify_x_failed_when_rc0_no_identity(monkeypatch):
    # Defensive: rc=0 but body has no screen_name/id and ok is not false —
    # the probe cannot tell who we are.
    _stub_subprocess(monkeypatch, returncode=0, stdout='{"ok":true,"unexpected":"shape"}\n')
    result = verify.probe_platform("x")
    assert result.status == AccountStatus.FAILED
    assert "no user identity" in result.error


def test_verify_youtube_logged_in(monkeypatch):
    _stub_subprocess(monkeypatch, returncode=0, stdout='{"logged_in":true,"channel_id":"c1"}\n')
    result = verify.probe_platform("youtube")
    assert result.status == AccountStatus.VERIFIED


def test_verify_youtube_retries_stale_page_identity_once(monkeypatch):
    from backend.application.setup.verify import _SubprocessResult

    calls: list[list[str]] = []

    def fake_from_call(args, *, timeout):
        calls.append(list(args))
        if args[-3:] == ["profile", "use", "ctx-dedicated"]:
            return _SubprocessResult(returncode=0)
        actual_probe_calls = [call for call in calls if call[-3:] != ["profile", "use", "ctx-dedicated"]]
        if len(actual_probe_calls) == 1:
            return _SubprocessResult(
                returncode=1,
                stderr=(
                    "ok: false\nerror:\n"
                    "  code: UNKNOWN\n"
                    "  message: 'Page not found: abc — stale page identity'\n"
                ),
            )
        return _SubprocessResult(returncode=0, stdout='{"logged_in":true,"channel_id":"c1"}\n')

    monkeypatch.setattr(_SubprocessResult, "from_call", staticmethod(fake_from_call))

    result = verify.probe_platform("youtube")

    assert result.status == AccountStatus.VERIFIED
    assert len(calls) == 3
    assert calls[0][-3:] == ["profile", "use", "ctx-dedicated"]


def test_verify_youtube_needs_login_on_auth_required(monkeypatch):
    _stub_subprocess(monkeypatch, returncode=0, stdout='{"logged_in":false,"error":"auth_required"}')
    result = verify.probe_platform("youtube")
    assert result.status == AccountStatus.NEEDS_LOGIN


def test_verify_reddit_logged_in(monkeypatch):
    _stub_subprocess(monkeypatch, returncode=0, stdout='[{"field":"username","value":"me"}]\n')
    result = verify.probe_platform("reddit")
    assert result.status == AccountStatus.VERIFIED


def test_verify_reddit_needs_login(monkeypatch):
    _stub_subprocess(monkeypatch, returncode=1, stderr="not logged in to Reddit")
    result = verify.probe_platform("reddit")
    assert result.status == AccountStatus.NEEDS_LOGIN


@pytest.mark.parametrize("platform", ["x", "youtube", "reddit", "xiaohongshu", "douyin", "zsxq"])
def test_whoami_probe_rejects_non_identity_success_envelope(monkeypatch, platform):
    _stub_subprocess(monkeypatch, returncode=0, stdout='{"status":"ok","site":"public"}\n')
    result = verify.probe_platform(platform)
    assert result.status == AccountStatus.FAILED


@pytest.mark.parametrize(
    ("platform", "command"),
    [
        ("x", ["twitter", "whoami", "--format", "json"]),
        ("youtube", ["youtube", "whoami", "--format", "json"]),
    ],
)
def test_x_and_youtube_use_authenticated_whoami(monkeypatch, platform, command):
    from backend.application.setup.verify import _SubprocessResult

    calls: list[list[str]] = []

    def fake_from_call(args, *, timeout):
        calls.append(list(args))
        if args[-3:] == ["profile", "use", "ctx-dedicated"]:
            return _SubprocessResult(returncode=0)
        return _SubprocessResult(returncode=0, stdout='{"logged_in":true,"username":"me"}\n')

    monkeypatch.setattr(_SubprocessResult, "from_call", staticmethod(fake_from_call))
    assert verify.probe_platform(platform).status == AccountStatus.VERIFIED
    assert calls[-1] == command


def test_verify_zsxq_logged_in(monkeypatch):
    _stub_subprocess(monkeypatch, returncode=0, stdout='{"logged_in":true,"user":{"name":"ok"}}\n')
    result = verify.probe_platform("zsxq")
    assert result.status == AccountStatus.VERIFIED


def test_verify_zsxq_logged_in_list_envelope(monkeypatch):
    _stub_subprocess(
        monkeypatch,
        returncode=0,
        stdout='[{"status":"logged_in","logged_in":true,"site":"zsxq","user_id":"u1","name":"me"}]\n',
    )
    result = verify.probe_platform("zsxq")
    assert result.status == AccountStatus.VERIFIED
    assert result.error is None


def test_probe_uses_bound_context_id_before_whoami(monkeypatch):
    from backend.application.setup.verify import _SubprocessResult

    calls: list[list[str]] = []

    def fake_from_call(args, *, timeout):
        calls.append(list(args))
        if args[-3:] == ["profile", "use", "ctx-dedicated"]:
            return _SubprocessResult(returncode=0)
        return _SubprocessResult(
            returncode=0,
            stdout='[{"status":"logged_in","logged_in":true,"site":"zsxq","user_id":"u1"}]\n',
        )

    monkeypatch.setattr(_SubprocessResult, "from_call", staticmethod(fake_from_call))

    result = verify.probe_platform("zsxq")

    assert result.status == AccountStatus.VERIFIED
    assert calls[0][-3:] == ["profile", "use", "ctx-dedicated"]
    assert calls[1][:2] == ["--profile", "infoos-fetch"]
    assert calls[1][2:6] == ["zsxq", "whoami", "--format", "json"]
    assert calls[1][-4:] == ["--site-session", "ephemeral", "--keep-tab", "false"]


def test_verify_zsxq_needs_login(monkeypatch):
    _stub_subprocess(monkeypatch, returncode=77, stderr="auth_required: missing __user_token.v3")
    result = verify.probe_platform("zsxq")
    assert result.status == AccountStatus.NEEDS_LOGIN


def test_verify_zsxq_needs_login_when_whoami_reports_false(monkeypatch):
    _stub_subprocess(monkeypatch, returncode=0, stdout='{"logged_in":false,"site":"zsxq"}\n')
    result = verify.probe_platform("zsxq")
    assert result.status == AccountStatus.NEEDS_LOGIN


@pytest.mark.parametrize("platform", ["xiaohongshu", "douyin"])
def test_verify_chinese_social_logged_in(monkeypatch, platform):
    _stub_subprocess(monkeypatch, returncode=0, stdout=f'{{"logged_in":true,"site":"{platform}","username":"me"}}\n')
    result = verify.probe_platform(platform)
    assert result.status == AccountStatus.VERIFIED


@pytest.mark.parametrize("platform", ["xiaohongshu", "douyin"])
def test_verify_chinese_social_needs_login(monkeypatch, platform):
    _stub_subprocess(monkeypatch, returncode=77, stderr=f"auth_required: not logged in to {platform}")
    result = verify.probe_platform(platform)
    assert result.status == AccountStatus.NEEDS_LOGIN


@pytest.mark.parametrize("platform", ["xiaohongshu", "douyin"])
def test_verify_chinese_social_needs_login_when_whoami_reports_false(monkeypatch, platform):
    _stub_subprocess(monkeypatch, returncode=0, stdout=f'{{"logged_in":false,"site":"{platform}"}}\n')
    result = verify.probe_platform(platform)
    assert result.status == AccountStatus.NEEDS_LOGIN


def test_verify_unsupported_platform_is_skipped():
    assert verify.probe_platform("wechat").status == AccountStatus.SKIPPED


def test_probe_does_not_call_runtime_prepare(monkeypatch):
    """probe_platform must NOT call prepare_opencli_browser_session.

    The wizard and the runtime fetch path are different concerns. If
    the setup session is already bound, the probe runs directly against
    that binding instead of preparing a fetch-time browser session.
    """
    from backend.application.setup.verify import _SubprocessResult

    def fake_from_call(args, *, timeout):
        return _SubprocessResult(
            returncode=69,
            stdout="",
            stderr='ok: false\nerror:\n  code: BROWSER_CONNECT\n  message: not connected\n',
        )

    monkeypatch.setattr(_SubprocessResult, "from_call", staticmethod(fake_from_call))

    def forbidden(*args, **kwargs):  # pragma: no cover
        raise AssertionError("probe_platform must not call prepare_opencli_browser_session")

    monkeypatch.setattr(verify, "prepare_opencli_browser_session", forbidden, raising=False)

    result = verify.probe_platform("x")
    assert result.status == AccountStatus.NEEDS_LOGIN
    assert "not connected" in result.error


def test_login_probe_uses_short_timeout(monkeypatch):
    """Setup login checks should return quickly instead of feeling like a hung flow."""
    from backend.application.setup.verify import _SubprocessResult
    seen: dict[str, int] = {}

    def fake_from_call(args, *, timeout):
        seen["timeout"] = timeout
        return _SubprocessResult(returncode=0, stdout='{"screen_name":"fast"}\n')

    monkeypatch.setattr(_SubprocessResult, "from_call", staticmethod(fake_from_call))
    assert verify.probe_platform("x").status == AccountStatus.VERIFIED
    assert seen["timeout"] <= 30


def test_subprocess_timeout_bytes_are_decoded(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=25,
            output=b"ok: false\n",
            stderr=b"error:\n  code: AUTH_REQUIRED\n  message: no ct0 cookie\n",
        )

    monkeypatch.setattr("backend.infrastructure.opencli.commands.subprocess.run", fake_run)

    result = verify.probe_platform("x")

    assert result.status == AccountStatus.NEEDS_LOGIN
    assert "no ct0 cookie" in result.error
