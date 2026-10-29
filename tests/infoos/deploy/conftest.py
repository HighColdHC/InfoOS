from pathlib import Path
import re
import shlex

import pytest


@pytest.fixture
def dockerfile_stages():
    """Read logical Dockerfile instructions, independent of whitespace/comments."""
    path = Path(__file__).resolve().parents[3] / "deploy/docker/Dockerfile"
    source = re.sub(r"\\\n\s*", " ", path.read_text())
    stages = {}
    for line in source.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        instruction, body = line.split(None, 1)
        instruction = instruction.upper()
        if instruction == "FROM":
            tokens = shlex.split(body)
            stage = tokens[-1] if tokens[-2].upper() == "AS" else tokens[0]
            stages[stage] = []
        elif stages:
            stages[stage].append((instruction, body))
    return stages
