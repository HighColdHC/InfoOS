"""Knowledge job verdicts inspect only the final report and never send alerts."""
from pathlib import Path
import subprocess

import pytest

ALERT = Path(__file__).parents[2] / "deploy/agent/agent-run-alert.sh"


@pytest.mark.parametrize("report,code,state", [
    ("codex\n分诊 accepted: 10\n拆解 accepted: 4\nrejected: 0\n", 0, "ok"),
    ("codex\n本轮分诊与拆解均无剩余候选\n", 0, "ok"),
    ("codex\naccepted: 1\nrejected: 2\n", 0, "fail"),
    ("user\n本轮分诊与拆解均无剩余候选\ncodex\n没有完成报告\n", 0, "fail"),
    ("tool call error: failed\ncodex\naccepted: 1\n", 0, "fail"),
    ("codex\naccepted: 1\n", 124, "fail"),
])
def test_knowledge_verdict(tmp_path, report, code, state):
    log = tmp_path / "run.log"
    log.write_text(report)
    result = subprocess.run(["bash", str(ALERT), "--verdict-only", "knowledge", str(code), str(log)],
                            capture_output=True, text=True, check=True)
    assert result.stdout.startswith(state + "\t")
