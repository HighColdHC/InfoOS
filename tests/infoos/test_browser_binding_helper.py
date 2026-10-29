from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "infoos-browser-binding.py"
SPEC = importlib.util.spec_from_file_location("infoos_browser_binding", HELPER)
assert SPEC is not None and SPEC.loader is not None
binding = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binding)


def test_select_new_context_requires_exactly_one_context_delta() -> None:
    before = binding.connected_contexts(
        "Connected Browser Bridge profiles\nold daily default — connected v1.0.22\n"
    )
    after = binding.connected_contexts(
        "Connected Browser Bridge profiles\n"
        "old daily default — connected v1.0.22\n"
        "dedicated pending — connected v1.0.22\n"
    )

    assert binding.select_new_context(before, after) == "dedicated"
    with pytest.raises(ValueError, match="exactly one"):
        binding.select_new_context(before, before)
    with pytest.raises(ValueError, match="exactly one"):
        binding.select_new_context((), ("first", "second"))


def test_select_new_context_accepts_one_reconnected_context_only_when_proven() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        binding.select_new_context(("stable-context",), ("stable-context",))

    assert binding.select_new_context(
        ("stable-context",),
        ("stable-context",),
        allow_reconnected=True,
    ) == "stable-context"
    with pytest.raises(ValueError, match="exactly one"):
        binding.select_new_context(
            ("daily", "stable-context"),
            ("daily", "stable-context"),
            allow_reconnected=True,
        )


def test_select_single_context_rejects_zero_or_multiple_contexts() -> None:
    assert binding.select_single_context(("ctx-container",)) == "ctx-container"
    with pytest.raises(ValueError, match="exactly one connected"):
        binding.select_single_context(())
    with pytest.raises(ValueError, match="exactly one connected"):
        binding.select_single_context(("ctx-container", "ctx-daily"))


def test_connected_contexts_ignores_disconnected_saved_profiles() -> None:
    output = """Connected Browser Bridge profiles

  928wj8bv — connected v1.0.22

Disconnected saved profiles:
  bucewjjc infoos-fetch — not connected
"""

    assert binding.connected_contexts(output) == ("928wj8bv",)


@pytest.mark.parametrize("status", ["not connected", "disconnected"])
def test_binding_contract_rejects_non_connected_status(status: str) -> None:
    payload = {
        "schema_version": 2,
        "profile_dir": "/tmp/infoos-test/chrome-profile",
        "context_id": "ctx-infoos",
        "alias": "infoos-fetch",
        "chrome_pid": 123,
        "binding_proof": binding.BINDING_PROOF,
    }

    errors = binding.binding_errors(
        payload,
        expected_profile_dir="/tmp/infoos-test/chrome-profile",
        expected_chrome_bin="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        chrome_command=(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            "--user-data-dir=/tmp/infoos-test/chrome-profile"
        ),
        profile_list_output=f"ctx-infoos infoos-fetch — {status} v1.0.22\n",
    )

    assert "bound OpenCLI context is not connected as infoos-fetch" in errors


def test_binding_contract_ties_live_context_to_visible_dedicated_chrome_pid() -> None:
    payload = {
        "schema_version": 2,
        "profile_dir": "/tmp/infoos-test/chrome-profile",
        "context_id": "ctx-infoos",
        "alias": "infoos-fetch",
        "chrome_pid": 123,
        "binding_proof": binding.BINDING_PROOF,
    }
    chrome_command = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
        "--user-data-dir=/tmp/infoos-test/chrome-profile"
    )
    profile_output = "ctx-infoos infoos-fetch default — connected v1.0.22\n"

    assert binding.binding_errors(
        payload,
        expected_profile_dir="/tmp/infoos-test/chrome-profile",
        expected_chrome_bin="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        chrome_command=chrome_command,
        profile_list_output=profile_output,
    ) == []


def test_supervised_container_binding_requires_one_exact_connected_context() -> None:
    payload = {
        "schema_version": 2,
        "profile_dir": "/var/lib/infoos/state/chrome-profile",
        "context_id": "ctx-container",
        "alias": "infoos-fetch",
        "chrome_pid": 123,
        "binding_proof": binding.SUPERVISED_CONTAINER_BINDING_PROOF,
    }
    kwargs = {
        "expected_profile_dir": "/var/lib/infoos/state/chrome-profile",
        "expected_chrome_bin": "/usr/bin/google-chrome-stable",
        "chrome_command": (
            "/usr/bin/chrome "
            "--user-data-dir=/var/lib/infoos/state/chrome-profile"
        ),
    }

    assert binding.binding_errors(
        payload,
        profile_list_output="ctx-container infoos-fetch — connected v1.0.22\n",
        **kwargs,
    ) == []
    errors = binding.binding_errors(
        payload,
        profile_list_output=(
            "ctx-container infoos-fetch — connected v1.0.22\n"
            "ctx-other daily — connected v1.0.22\n"
        ),
        **kwargs,
    )
    assert "supervised-container binding requires exactly one connected context" in errors


def test_binding_contract_rejects_proofs_outside_allowlist() -> None:
    assert binding.BINDING_PROOFS == {
        "new_context_after_dedicated_launch",
        "supervised_container_dedicated_profile",
    }


def test_binding_contract_accepts_headless_only_when_explicitly_allowed() -> None:
    payload = {
        "schema_version": 2,
        "profile_dir": "/tmp/infoos-test/chrome-profile",
        "context_id": "ctx-infoos",
        "alias": "infoos-fetch",
        "chrome_pid": 123,
        "binding_proof": binding.BINDING_PROOF,
    }
    errors = binding.binding_errors(
        payload,
        expected_profile_dir="/tmp/infoos-test/chrome-profile",
        expected_chrome_bin="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        chrome_command=(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            "--user-data-dir=/tmp/infoos-test/chrome-profile --headless=new"
        ),
        profile_list_output="ctx-infoos infoos-fetch — connected v1.0.22\n",
        allow_headless=True,
    )

    assert errors == []


@pytest.mark.parametrize(
    ("payload_patch", "command_suffix", "profile_output", "expected"),
    [
        ({"binding_proof": ""}, "", "ctx-infoos infoos-fetch — connected", "binding proof"),
        ({}, " --headless=new", "ctx-infoos infoos-fetch — connected", "headless"),
        ({}, "", "other daily — connected", "not connected"),
    ],
)
def test_binding_contract_rejects_unproven_headless_or_wrong_context(
    payload_patch: dict[str, object],
    command_suffix: str,
    profile_output: str,
    expected: str,
) -> None:
    payload = {
        "schema_version": 2,
        "profile_dir": "/tmp/infoos-test/chrome-profile",
        "context_id": "ctx-infoos",
        "alias": "infoos-fetch",
        "chrome_pid": 123,
        "binding_proof": binding.BINDING_PROOF,
        **payload_patch,
    }
    command = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
        "--user-data-dir=/tmp/infoos-test/chrome-profile" + command_suffix
    )

    errors = binding.binding_errors(
        payload,
        expected_profile_dir="/tmp/infoos-test/chrome-profile",
        expected_chrome_bin="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        chrome_command=command,
        profile_list_output=profile_output,
    )

    assert any(expected in error for error in errors)


def test_binding_contract_accepts_linux_stable_chrome_main_process() -> None:
    payload = {
        "schema_version": 2,
        "profile_dir": "/var/lib/infoos/state/chrome-profile",
        "context_id": "ctx-linux",
        "alias": "infoos-fetch",
        "chrome_pid": 123,
        "binding_proof": binding.BINDING_PROOF,
    }

    assert binding.binding_errors(
        payload,
        expected_profile_dir="/var/lib/infoos/state/chrome-profile",
        expected_chrome_bin="/opt/google/chrome/google-chrome",
        chrome_command=(
            "/opt/google/chrome/chrome "
            "--user-data-dir=/var/lib/infoos/state/chrome-profile"
        ),
        profile_list_output="ctx-linux infoos-fetch — connected v1.0.22\n",
    ) == []


@pytest.mark.parametrize("process_type", ["renderer", "gpu-process", "utility"])
def test_binding_contract_rejects_linux_chrome_child_process(process_type: str) -> None:
    payload = {
        "schema_version": 2,
        "profile_dir": "/var/lib/infoos/state/chrome-profile",
        "context_id": "ctx-linux",
        "alias": "infoos-fetch",
        "chrome_pid": 123,
        "binding_proof": binding.SUPERVISED_CONTAINER_BINDING_PROOF,
    }

    errors = binding.binding_errors(
        payload,
        expected_profile_dir="/var/lib/infoos/state/chrome-profile",
        expected_chrome_bin="/opt/google/chrome/google-chrome",
        chrome_command=(
            "/opt/google/chrome/chrome "
            "--user-data-dir=/var/lib/infoos/state/chrome-profile "
            f"--type={process_type}"
        ),
        profile_list_output="ctx-linux infoos-fetch — connected v1.0.22\n",
    )

    assert "bound Chrome pid is not the stable Chrome main process" in errors
