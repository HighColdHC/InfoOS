#!/usr/bin/env python3
"""Export SQLite and allowlisted runtime facts through a private transport."""
from __future__ import annotations

from readme_runtime import REMOTE_FACTS

import argparse
import gzip
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

REMOTE = r'''
import gzip,json,pathlib,shutil,sqlite3,subprocess,sys,tempfile
container = CONTAINER
mounts = json.loads(subprocess.check_output(["docker", "inspect", container, "--format", "{{json .Mounts}}" ]))
source_dir = next(m["Source"] for m in mounts if m["Destination"] == "/var/lib/infoos/data")
with tempfile.TemporaryDirectory(prefix="infoos-shots-") as directory:
    path = pathlib.Path(directory) / "snapshot.sqlite3"
    source = sqlite3.connect((pathlib.Path(source_dir) / "infoos.sqlite3").as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(path)
    source.backup(target)
    assert target.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    target.close(); source.close()
    with path.open("rb") as stream, gzip.GzipFile(fileobj=sys.stdout.buffer, mode="wb", compresslevel=1) as output:
        shutil.copyfileobj(stream, output)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", type=Path, required=True)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix="infoos-shots-", dir="/tmp"))
    directory.chmod(0o700)
    try:
        command = "docker exec " + shlex.quote(args.container) + " /opt/infoos/venv/bin/python -c " + shlex.quote(REMOTE_FACTS)
        facts = subprocess.check_output(["bash", str(args.transport), "command", command])
        compressed = directory / "snapshot.sqlite3.gz"
        code = REMOTE.replace("CONTAINER", repr(args.container), 1)
        with compressed.open("wb") as stream:
            subprocess.run(["bash", str(args.transport), "command", "python3 -c " + shlex.quote(code)],
                           stdout=stream, check=True)
        with gzip.open(compressed, "rb") as source, (directory / "snapshot.sqlite3").open("wb") as target:
            shutil.copyfileobj(source, target)
        compressed.unlink()
        (directory / "snapshot.sqlite3").chmod(0o600)
        (directory / "runtime-facts.json").write_bytes(facts)
        (directory / "runtime-facts.json").chmod(0o600)
        print(directory / "snapshot.sqlite3")
    except BaseException:
        shutil.rmtree(directory)
        raise


if __name__ == "__main__":
    main()
