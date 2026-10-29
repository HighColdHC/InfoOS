from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def _shell_function(script: str, name: str, next_name: str) -> str:
    start = script.index(f"{name}() {{")
    end = script.index(f"\n\n{next_name}() {{", start)
    return script[start:end]


def test_headless_supervisor_tolerates_expected_browser_lifecycle_transitions() -> None:
    launcher = (ROOT / "scripts" / "run-infoos-test-env.sh").read_text(encoding="utf-8")
    runtime_health = _shell_function(
        launcher,
        "browser_runtime_healthy",
        "browser_binding_ready",
    )
    probe = subprocess.run(
        [
            "bash",
            "-c",
            f"""
headless_infoos_chrome_running() {{ [[ "$PROCESS_RUNNING" == "1" ]]; }}
browser_mode_running() {{ headless_infoos_chrome_running; }}
browser_binding_ready() {{ [[ "$BINDING_READY" == "1" ]]; }}
{runtime_health}
for state in running_and_bound terminating_unbound fully_stopped starting_unbound; do
  case "$state" in
    running_and_bound) PROCESS_RUNNING=1; BINDING_READY=1 ;;
    terminating_unbound) PROCESS_RUNNING=1; BINDING_READY=0 ;;
    fully_stopped) PROCESS_RUNNING=0; BINDING_READY=0 ;;
    starting_unbound) PROCESS_RUNNING=1; BINDING_READY=0 ;;
  esac
  browser_runtime_healthy true || {{ echo "$state=FATAL"; exit 1; }}
  echo "$state=HEALTHY"
done
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "FATAL" not in probe.stdout
    assert probe.stdout.splitlines() == [
        "running_and_bound=HEALTHY",
        "terminating_unbound=HEALTHY",
        "fully_stopped=HEALTHY",
        "starting_unbound=HEALTHY",
    ]


def test_visible_supervisor_still_fails_closed_on_process_or_binding_loss() -> None:
    launcher = (ROOT / "scripts" / "run-infoos-test-env.sh").read_text(encoding="utf-8")
    runtime_health = _shell_function(
        launcher,
        "browser_runtime_healthy",
        "browser_binding_ready",
    )
    probe = subprocess.run(
        [
            "bash",
            "-c",
            f"""
headless_infoos_chrome_running() {{ return 1; }}
browser_mode_running() {{ [[ "$PROCESS_RUNNING" == "1" ]]; }}
browser_binding_ready() {{ [[ "$BINDING_READY" == "1" ]]; }}
{runtime_health}
PROCESS_RUNNING=1; BINDING_READY=1
browser_runtime_healthy false || exit 1
PROCESS_RUNNING=0; BINDING_READY=1
browser_runtime_healthy false && exit 2
PROCESS_RUNNING=1; BINDING_READY=0
browser_runtime_healthy false && exit 3
exit 0
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert probe.returncode == 0, probe.stdout + probe.stderr
