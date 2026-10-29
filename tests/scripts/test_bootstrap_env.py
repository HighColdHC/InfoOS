"""Exercise bootstrap installer selection without installing anything."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/bootstrap_env.sh'


@pytest.mark.parametrize('pip,uv,expected', [(True, True, 'pip'), (False, True, 'uv'), (False, False, 'error')])
def test_installer_selection(tmp_path, pip, uv, expected):
    root = tmp_path / 'project'
    (root / 'scripts').mkdir(parents=True)
    shutil.copy2(SCRIPT, root / 'scripts/bootstrap_env.sh')
    (root / '.venv/bin').mkdir(parents=True)
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    for command in ('dirname',):
        (fake_bin / command).symlink_to(shutil.which(command))
    log = tmp_path / 'calls'
    python = root / '.venv/bin/python'
    python.write_text('#!/bin/bash\nif [[ "$1" == "-" ]]; then exit 0; fi\n'
                      f'if [[ "$3" == "--version" ]]; then exit {0 if pip else 1}; fi\n'
                      'echo "pip $*" >> "$CALL_LOG"\n')
    python.chmod(0o755)
    if uv:
        executable = fake_bin / 'uv'
        executable.write_text('#!/bin/bash\necho "uv $*" >> "$CALL_LOG"\n')
        executable.chmod(0o755)
    result = subprocess.run(['/bin/bash', str(root / 'scripts/bootstrap_env.sh')],
                            env={**os.environ, 'PATH': str(fake_bin), 'CALL_LOG': str(log)},
                            text=True, capture_output=True)
    if expected == 'error':
        assert result.returncode != 0
        assert 'ensurepip' in result.stderr and 'uv' in result.stderr
        assert not log.exists()
    else:
        assert result.returncode == 0, result.stderr
        calls = log.read_text().splitlines()
        assert all(call.startswith(expected + ' ') for call in calls)
        assert f'-e {root}[dev,web]' in calls[-1]
        if expected == 'uv':
            assert f'--python {python}' in calls[-1]
