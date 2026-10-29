from __future__ import annotations

from pathlib import Path

from backend.infrastructure.browser import chrome_runtime


def test_linux_candidates_put_explicit_override_first(monkeypatch, tmp_path: Path) -> None:
    configured = tmp_path / "google-chrome-stable"
    monkeypatch.setenv("INFOOS_CHROME_BIN", str(configured))

    assert chrome_runtime.chrome_binary_candidates(system="Linux")[0] == configured


def test_linux_wrapper_accepts_only_its_exact_runtime_binary(monkeypatch) -> None:
    monkeypatch.setattr(chrome_runtime.platform, "system", lambda: "Linux")
    configured = Path("/opt/google/chrome/google-chrome")

    assert chrome_runtime.chrome_main_process_matches(
        "/opt/google/chrome/chrome --user-data-dir=/var/lib/infoos/state/chrome-profile",
        configured,
    )
    assert not chrome_runtime.chrome_main_process_matches(
        "/usr/bin/chromium --user-data-dir=/var/lib/infoos/state/chrome-profile",
        configured,
    )
    assert not chrome_runtime.chrome_main_process_matches(
        "/bin/bash -lc /opt/google/chrome/chrome --user-data-dir=/var/lib/infoos/state/chrome-profile",
        configured,
    )
    assert not chrome_runtime.chrome_main_process_matches(
        "/opt/google/chrome/chrome --type=utility "
        "--user-data-dir=/var/lib/infoos/state/chrome-profile",
        configured,
    )


def test_pid_namespace_match_rejects_container_process_from_host(monkeypatch) -> None:
    monkeypatch.setattr(chrome_runtime.platform, "system", lambda: "Linux")

    def fake_namespace_identity(path: str):
        namespaces = {
            "/proc/self/ns/pid": (1, 100),
            "/proc/123/ns/pid": (1, 100),
            "/proc/456/ns/pid": (1, 200),
        }
        return namespaces[path]

    monkeypatch.setattr(
        chrome_runtime,
        "_namespace_identity",
        fake_namespace_identity,
        raising=False,
    )

    assert chrome_runtime.pid_namespace_matches_current(123) is True
    assert chrome_runtime.pid_namespace_matches_current(456) is False
