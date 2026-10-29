import pytest

from backend.domain.failures.connector_diagnostics import classify_connector_diagnostic


def test_reddit_auth_required_diagnostic_mentions_reddit():
    diagnostic = classify_connector_diagnostic(
        [
            {
                "message": (
                    "OpenCLI command failed with exit code 77: ok: false\n"
                    "error:\n"
                    "  code: AUTH_REQUIRED\n"
                    "  message: Reddit /api/me.json returned HTTP 403\n"
                )
            }
        ],
        platform="reddit",
    )

    assert diagnostic is not None
    assert diagnostic["category"] == "auth_required"
    assert diagnostic["retryable"] is False
    assert "Reddit" in diagnostic["action"]


def test_reddit_html_json_parse_error_without_auth_proof_requires_inspection():
    diagnostic = classify_connector_diagnostic(
        [
            {
                "message": (
                    "OpenCLI command failed with exit code 1: ok: false\n"
                    "error:\n"
                    "  code: UNKNOWN\n"
                    "  message: 'SyntaxError: Unexpected token '<', \"<body clas\"... is not valid JSON'\n"
                )
            }
        ],
        platform="reddit",
    )

    assert diagnostic is not None
    assert diagnostic["category"] == "invalid_output"
    assert diagnostic["severity"] == "error"
    assert diagnostic["retryable"] is False


def test_douyin_result_card_timeout_points_to_browser_security_state():
    diagnostic = classify_connector_diagnostic(
        [
            {
                "message": (
                    "OpenCLI command failed with exit code 1: Douyin search did not render result cards within "
                    "the timeout. Open the same search in Chrome and verify login/security state before retrying."
                )
            }
        ],
        platform="douyin",
    )

    assert diagnostic is not None
    assert diagnostic["category"] == "browser_session"
    assert diagnostic["severity"] == "blocked"
    assert diagnostic["retryable"] is True
    assert "Douyin" in diagnostic["action"]


def test_opencli_profile_not_connected_is_browser_session_diagnostic():
    diagnostic = classify_connector_diagnostic(
        [
            {
                "message": 'Browser profile "928wj8bv" is not connected',
            }
        ],
        platform="scys",
    )

    assert diagnostic is not None
    assert diagnostic["category"] == "browser_session"
    assert diagnostic["retryable"] is True


@pytest.mark.parametrize(
    ("tail", "expected_category", "expected_stage"),
    [
        ("error code COMMAND_EXEC: spawn opencli ENOENT", "command_execution", "command_execution"),
        ("page.goto failed: net::ERR_TIMED_OUT at https://www.douyin.com", "page_load", "page_load"),
        ("HTTP 429 Too Many Requests: rate limit exceeded", "rate_limited", "platform_response"),
        ("captcha verification required before continuing", "challenge_required", "platform_verification"),
        ("Douyin API auth/permission error 403", "auth_required", "platform_authentication"),
    ],
)
def test_long_douyin_failure_preserves_tail_and_classifies_stage(
    tail: str,
    expected_category: str,
    expected_stage: str,
):
    diagnostic = classify_connector_diagnostic(
        [{"message": "OpenCLI command failed with exit code 1: " + ("x" * 1100) + "\n" + tail}],
        platform="douyin",
    )

    assert diagnostic is not None
    assert diagnostic["category"] == expected_category
    assert diagnostic["failure_stage"] == expected_stage
    assert diagnostic["summary_truncated"] is True
    assert tail in diagnostic["summary"]
