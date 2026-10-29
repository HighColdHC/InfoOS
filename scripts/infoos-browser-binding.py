#!/usr/bin/env python3
"""Verify that an OpenCLI context belongs to the dedicated InfoOS Chrome.

The launcher establishes the association by selecting the only context that
appears after the dedicated Chrome starts.  This helper keeps the parsing and
runtime verification contract testable and shared by the supervisor and the
reliability acceptance script.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


BINDING_PROOF = "new_context_after_dedicated_launch"
SUPERVISED_CONTAINER_BINDING_PROOF = "supervised_container_dedicated_profile"
BINDING_PROOFS = {BINDING_PROOF, SUPERVISED_CONTAINER_BINDING_PROOF}


def connected_contexts(output: str) -> tuple[str, ...]:
    return tuple(
        line.split(maxsplit=1)[0]
        for line in _connected_profile_lines(output)
    )


def _connected_profile_lines(output: str) -> tuple[str, ...]:
    lines: list[str] = []
    inside_connected_section: bool | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Connected Browser Bridge profiles"):
            inside_connected_section = True
            continue
        if line.startswith("Disconnected saved profiles"):
            inside_connected_section = False
            continue
        if inside_connected_section is False:
            continue
        normalized = line.lower()
        if (
            "connected" not in normalized
            or "not connected" in normalized
            or "disconnected" in normalized
            or line.startswith(("Connected ", "No "))
        ):
            continue
        if line not in lines:
            lines.append(line)
    return tuple(lines)


def select_new_context(
    baseline: tuple[str, ...],
    current: tuple[str, ...],
    *,
    allow_reconnected: bool = False,
) -> str:
    baseline_set = set(baseline)
    candidates = [context_id for context_id in current if context_id not in baseline_set]
    if len(candidates) == 1:
        return candidates[0]
    if (
        allow_reconnected
        and len(current) == 1
        and current[0] in baseline_set
    ):
        return current[0]
    raise ValueError(
        f"expected exactly one new OpenCLI context, found {len(candidates)}: {candidates}"
    )


def select_single_context(current: tuple[str, ...]) -> str:
    if len(current) != 1:
        raise ValueError(
            f"expected exactly one connected OpenCLI context, found {len(current)}: {list(current)}"
        )
    return current[0]


def binding_errors(
    payload: dict[str, Any],
    *,
    expected_profile_dir: str,
    expected_chrome_bin: str,
    chrome_command: str,
    profile_list_output: str,
    allow_headless: bool = False,
) -> list[str]:
    errors: list[str] = []
    context_id = str(payload.get("context_id") or "").strip()
    chrome_pid = payload.get("chrome_pid")
    if payload.get("schema_version") != 2:
        errors.append("browser-session schema_version must be 2")
    if payload.get("profile_dir") != expected_profile_dir:
        errors.append("browser-session profile_dir does not match dedicated profile")
    if payload.get("alias") != "infoos-fetch":
        errors.append("browser-session alias must be infoos-fetch")
    binding_proof = payload.get("binding_proof")
    if binding_proof not in BINDING_PROOFS:
        errors.append("browser-session lacks dedicated-launch binding proof")
    if not context_id:
        errors.append("browser-session context_id is missing")
    if not isinstance(chrome_pid, int) or chrome_pid < 1:
        errors.append("browser-session chrome_pid is invalid")
    expected_flag = f"--user-data-dir={expected_profile_dir}"
    if expected_flag not in chrome_command:
        errors.append("bound Chrome pid does not use the dedicated profile")
    if not _is_stable_chrome_main_process(chrome_command, expected_chrome_bin):
        errors.append("bound Chrome pid is not the stable Chrome main process")
    if "--headless" in chrome_command and not allow_headless:
        errors.append("bound Chrome is headless")
    matching_lines = [
        line for line in _connected_profile_lines(profile_list_output)
        if line.split(maxsplit=1)[0:1] == [context_id]
    ]
    if not any("infoos-fetch" in line for line in matching_lines):
        errors.append("bound OpenCLI context is not connected as infoos-fetch")
    if (
        binding_proof == SUPERVISED_CONTAINER_BINDING_PROOF
        and len(_connected_profile_lines(profile_list_output)) != 1
    ):
        errors.append("supervised-container binding requires exactly one connected context")
    return errors


def _is_stable_chrome_main_process(command: str, expected_chrome_bin: str) -> bool:
    """Accept only the configured stable launcher or its Linux Chrome ELF."""
    if not command or "/Helpers/" in command or " --type=" in command:
        return False
    configured = Path(expected_chrome_bin).expanduser()
    allowed = {configured}
    try:
        allowed.add(configured.resolve(strict=False))
    except OSError:
        pass
    if configured.name in {"google-chrome", "google-chrome-stable"}:
        allowed.add(configured.with_name("chrome"))
        try:
            allowed.add(configured.resolve(strict=False).with_name("chrome"))
        except OSError:
            pass
    return any(command == str(binary) or command.startswith(f"{binary} ") for binary in allowed)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _profile_list(opencli: str) -> str:
    result = _run([opencli, "profile", "list"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "opencli profile list failed")
    return result.stdout


def _parse_context_csv(value: str) -> tuple[str, ...]:
    return tuple(item for item in (part.strip() for part in value.split(",")) if item)


def _check(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(args.session_json.read_text(encoding="utf-8"))
        chrome_pid = payload.get("chrome_pid")
        if not isinstance(chrome_pid, int) or chrome_pid < 1:
            raise ValueError("browser-session chrome_pid is invalid")
        ps_result = _run(["/bin/ps", "-p", str(chrome_pid), "-o", "command="])
        chrome_command = ps_result.stdout.strip() if ps_result.returncode == 0 else ""
        profile_output = _profile_list(args.opencli)
        errors = binding_errors(
            payload,
            expected_profile_dir=str(args.profile_dir),
            expected_chrome_bin=str(args.chrome_bin),
            chrome_command=chrome_command,
            profile_list_output=profile_output,
            allow_headless=args.allow_headless,
        )
        if errors:
            raise ValueError("; ".join(errors))
        if args.activate:
            use_result = _run([args.opencli, "profile", "use", str(payload["context_id"])])
            if use_result.returncode != 0:
                raise RuntimeError(use_result.stderr.strip() or "opencli profile use failed")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"infoos-browser-binding: {exc}", file=sys.stderr)
        return 1
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    contexts_parser = subparsers.add_parser("contexts")
    contexts_parser.add_argument("--opencli", required=True)

    select_parser = subparsers.add_parser("select-new")
    select_parser.add_argument("--opencli", required=True)
    select_parser.add_argument("--baseline", default="")
    select_parser.add_argument("--allow-reconnected", action="store_true")

    single_parser = subparsers.add_parser("select-single")
    single_parser.add_argument("--opencli", required=True)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--opencli", required=True)
    check_parser.add_argument("--session-json", type=Path, required=True)
    check_parser.add_argument("--profile-dir", type=Path, required=True)
    check_parser.add_argument("--chrome-bin", type=Path, required=True)
    check_parser.add_argument("--activate", action="store_true")
    check_parser.add_argument("--allow-headless", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "contexts":
            print(",".join(connected_contexts(_profile_list(args.opencli))))
            return 0
        if args.command == "select-new":
            current = connected_contexts(_profile_list(args.opencli))
            print(select_new_context(
                _parse_context_csv(args.baseline),
                current,
                allow_reconnected=args.allow_reconnected,
            ))
            return 0
        if args.command == "select-single":
            print(select_single_context(connected_contexts(_profile_list(args.opencli))))
            return 0
        return _check(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"infoos-browser-binding: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
