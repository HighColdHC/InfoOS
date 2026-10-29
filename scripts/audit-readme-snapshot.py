#!/usr/bin/env python3
"""Compare final OCR/source text to a disposable remote read-only backup."""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from readme_redaction import policy
from readme_screenshot_audit import SWIFT_OCR

ROOT = Path(__file__).resolve().parents[1]
REMOTE_AUDIT = r'''
import json,pathlib,re,sqlite3,subprocess,sys,tempfile
payload = json.load(sys.stdin)
mounts = json.loads(subprocess.check_output(["docker","inspect",payload["container"],"--format","{{json .Mounts}}"] ))
source_dir = next(m["Source"] for m in mounts if m["Destination"] == "/var/lib/infoos/data")
namespace = {"__file__":"/tmp/readme_redaction.py"}
exec(payload["module"],namespace)
namespace["policy"] = lambda: payload["rules"]
namespace["interface_literals"] = lambda: payload["rules"]["interface_literals"]
with tempfile.TemporaryDirectory(prefix="infoos-shots-audit-") as directory:
    path = pathlib.Path(directory)/"snapshot.sqlite3"
    source = sqlite3.connect((pathlib.Path(source_dir)/"infoos.sqlite3").as_uri()+"?mode=ro",uri=True)
    target = sqlite3.connect(path)
    source.backup(target)
    assert target.execute("pragma quick_check").fetchone()[0] == "ok"
    target.close();source.close()
    # Only corpus's credential-excluding SELECTs run here; no demo server opens
    # this backup. Credential tables, columns and nested keys are never queried.
    values = namespace["corpus"](path)
    identifiers = values["identity"] | {v for v in values["body"] if re.match(r"https?://|@|[\w.+-]+@",v)}
    audit = namespace["IdentifierAudit"](identifiers)
    print(json.dumps({"ocr_hits":{k:audit.inspect(v) for k,v in payload["ocr"].items()},
                      "text_hits":{k:audit.inspect([v]) for k,v in payload["texts"].items()},
                      "identifier_count":len(identifiers)}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", type=Path, required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    images = sorted((ROOT / "docs/images/features").glob("*.webp"))
    with tempfile.TemporaryDirectory(prefix="infoos-shots-audit-", dir="/tmp") as directory:
        work = Path(directory)
        swift = work / "ocr.swift"
        swift.write_text(SWIFT_OCR)
        pngs = []
        for path in images:
            png = work / (path.stem + ".png")
            with Image.open(path) as image:
                image.save(png)
            pngs.append(png)
        recognized = subprocess.check_output(["swift", str(swift), *map(str, pngs)], text=True)
        ocr = {p.stem:json.loads(line) for p,line in zip(images,recognized.splitlines(),strict=True)}
        files = [ROOT / "README.md", ROOT / "docs/images/features/README.md",
                 *ROOT.glob("scripts/*readme*.py"), ROOT / "scripts/readme-redaction.json"]
        payload = {"container":args.container,"module":(ROOT / "scripts/readme_redaction.py").read_text(),
                   "rules":policy(),"ocr":ocr,"texts":{str(p.relative_to(ROOT)):p.read_text() for p in files}}
        result = subprocess.run(["bash", str(args.transport), "command", "python3 -c " + shlex.quote(REMOTE_AUDIT)],
                                input=json.dumps(payload),capture_output=True,text=True,check=True)
        report = json.loads(result.stdout)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
        assert not any(report["ocr_hits"].values()) and not any(report["text_hits"].values()), "Private identifier audit failed"


if __name__ == "__main__":
    main()
