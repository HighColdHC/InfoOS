from __future__ import annotations

from backend.infrastructure.browser.console_access import (
    authorize_grant,
    grant_remaining_seconds,
    issue_grant,
    reset_grants_for_tests,
)


def setup_function() -> None:
    reset_grants_for_tests()


def test_grant_requires_same_origin_and_refreshes_before_idle_expiry(monkeypatch):
    monkeypatch.setenv("INFOOS_BROWSER_CONSOLE_IDLE_SECONDS", "10")
    monkeypatch.setenv("INFOOS_BROWSER_CONSOLE_MAX_SECONDS", "30")
    token = issue_grant(now=100)

    assert authorize_grant(
        token,
        origin="https://infoos.example:8443",
        referer="",
        forwarded_host="infoos.example:8443",
        forwarded_proto="https",
        now=105,
    )
    assert authorize_grant(
        token,
        origin="",
        referer="https://infoos.example:8443/browser-console/",
        forwarded_host="infoos.example:8443",
        forwarded_proto="https",
        now=114,
    )


def test_grant_rejects_cross_origin_idle_and_absolute_expiry(monkeypatch):
    monkeypatch.setenv("INFOOS_BROWSER_CONSOLE_IDLE_SECONDS", "10")
    monkeypatch.setenv("INFOOS_BROWSER_CONSOLE_MAX_SECONDS", "30")

    cross_origin = issue_grant(now=100)
    assert not authorize_grant(
        cross_origin,
        origin="https://attacker.example",
        referer="",
        forwarded_host="infoos.example",
        forwarded_proto="https",
        now=101,
    )

    idle = issue_grant(now=200)
    assert not authorize_grant(
        idle,
        origin="https://infoos.example",
        referer="",
        forwarded_host="infoos.example",
        forwarded_proto="https",
        now=211,
    )

    absolute = issue_grant(now=300)
    assert authorize_grant(
        absolute,
        origin="https://infoos.example",
        referer="",
        forwarded_host="infoos.example",
        forwarded_proto="https",
        now=309,
    )
    assert not authorize_grant(
        absolute,
        origin="https://infoos.example",
        referer="",
        forwarded_host="infoos.example",
        forwarded_proto="https",
        now=331,
    )


def test_remaining_lifetime_observes_heartbeat_without_extending_absolute_deadline(monkeypatch):
    monkeypatch.setenv("INFOOS_BROWSER_CONSOLE_IDLE_SECONDS", "10")
    monkeypatch.setenv("INFOOS_BROWSER_CONSOLE_MAX_SECONDS", "30")
    token = issue_grant(now=100)

    assert grant_remaining_seconds(token, now=105) == 5
    assert authorize_grant(
        token,
        origin="https://infoos.example",
        referer="",
        forwarded_host="infoos.example",
        forwarded_proto="https",
        now=108,
    )
    assert grant_remaining_seconds(token, now=115) == 3
    assert grant_remaining_seconds(token, now=131) == 0
