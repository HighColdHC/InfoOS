"""Downloaded artifacts must pass their checksum before installation starts."""
from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize(("variable", "checksum_arg", "installer"), [
    ("mihomo_gz", "MIHOMO_SHA256", "gzip"),
    ("chrome_deb", "GOOGLE_CHROME_SHA256", "/opt/base/bin/add-pkg"),
    ("extension_crx", "OPENCLI_EXTENSION_SHA256", "printf"),
])
def test_artifact_checksum_gates_installation(
    dockerfile_stages, tmp_path, variable, checksum_arg, installer,
):
    runtime = dockerfile_stages["runtime"]
    commands = [body for instruction, body in runtime if instruction == "RUN"
                and shlex.split(body)[0].startswith(variable + "=")]
    assert len(commands) == 1
    steps = commands[0].split("&&")
    # Run the actual build steps up to the first consumer of the artifact.
    # Replace that consumer with a marker so no package or executable is installed.
    install_index = next(index for index, step in enumerate(steps)
                         if shlex.split(step)[0] == installer
                         and (installer != "printf" or shlex.split(step)[1].startswith("{")))
    artifact = tmp_path / "artifact"
    marker = tmp_path / "installed"
    steps[0] = f"{variable}={shlex.quote(str(artifact))}"
    script = " && ".join(steps[:install_index])
    script += f" && touch {shlex.quote(str(marker))}"

    # Keep network and filesystem setup hermetic; checksum verification is real.
    curl = tmp_path / "curl"
    curl.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""\
        import os
        from pathlib import Path
        import sys

        args = sys.argv[1:]
        Path(args[args.index("-o") + 1]).write_bytes(Path(os.environ["PAYLOAD_FILE"]).read_bytes())
        """))
    curl.chmod(0o755)
    mkdir = tmp_path / "mkdir"
    mkdir.write_text("#!/bin/sh\nexit 0\n")
    mkdir.chmod(0o755)
    payload_file = tmp_path / "payload"
    payload = b"verified artifact bytes\n"
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
           "PAYLOAD_FILE": str(payload_file)}
    for instruction, body in runtime:
        if instruction == "ARG":
            name, separator, value = body.partition("=")
            if separator:
                env[name] = value
    env[checksum_arg] = hashlib.sha256(payload).hexdigest()
    # Verify the optional artifact's checksum on the explicitly enabled path.
    env["INFOOS_WITH_MIHOMO"] = "1"
    for content, accepted in ((payload, True), (payload + b"corruption", False)):
        marker.unlink(missing_ok=True)
        payload_file.write_bytes(content)
        artifact.write_bytes(content)  # The extension is COPY'd rather than downloaded.
        result = subprocess.run(["/bin/sh", "-c", script], env=env, capture_output=True, text=True)
        assert (result.returncode == 0) == accepted, result.stdout + result.stderr
        assert marker.exists() == accepted
