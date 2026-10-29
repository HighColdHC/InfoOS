"""OCR and pixel-container audit; only counts leave the disposable workspace."""
from __future__ import annotations

import json
import re
import struct
import subprocess
from pathlib import Path

from PIL import Image
from readme_redaction import IdentifierAudit, corpus

SWIFT_OCR = r'''
import Foundation
import Vision
import ImageIO
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = false
for path in CommandLine.arguments.dropFirst() {
    let source = CGImageSourceCreateWithURL(URL(fileURLWithPath:path) as CFURL, nil)!
    let image = CGImageSourceCreateImageAtIndex(source, 0, nil)!
    try VNImageRequestHandler(cgImage:image).perform([request])
    let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    let data = try JSONEncoder().encode(lines)
    print(String(data:data,encoding:.utf8)!)
}
'''


def audit_images(database: Path, directory: Path, output: Path) -> dict:
    values = corpus(database)
    # OCR targets original private identities and addresses. Full prose is
    # compared against the DB in the DOM audit before every screenshot.
    identifiers = values["identity"] | {v for v in values["body"] if re.match(r"https?://|@|[\w.+-]+@", v)}
    audit = IdentifierAudit(identifiers)
    swift = directory / "readme-ocr.swift"
    swift.write_text(SWIFT_OCR)
    paths = sorted(output.glob("*.webp"))
    pngs = []
    for path in paths:
        raw = path.read_bytes()
        assert raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
        chunks, offset = [], 12
        while offset < len(raw):
            size = struct.unpack_from("<I", raw, offset + 4)[0]
            chunks.append(raw[offset:offset + 4].decode("ascii"))
            offset += 8 + size + size % 2
        assert chunks == ["VP8L"], (path.name, chunks)
        png = directory / (path.stem + ".png")
        with Image.open(path) as image:
            image.save(png)
        pngs.append(png)
    result = subprocess.run(["swift", str(swift), *map(str, pngs)], capture_output=True, text=True, check=True)
    rows = []
    for path, line in zip(paths, result.stdout.splitlines(), strict=True):
        hits = audit.inspect(json.loads(line))
        rows.append({"name":path.stem,"ocr_hits":hits,"bytes":path.stat().st_size})
    root = output.parents[2]
    text_files = [root / "README.md", output / "README.md", *root.glob("scripts/*readme*.py"),
                  root / "scripts/readme_redaction.py", root / "scripts/readme-redaction.json"]
    # demo-admin is generated locally, not a production identifier.
    text_audit = IdentifierAudit(identifiers)
    text_hits = {str(p.relative_to(root)):text_audit.inspect([p.read_text()])
                 for p in text_files}
    return {"images":rows,"total_bytes":sum(row["bytes"] for row in rows),
            "ocr_hits":sum(row["ocr_hits"] for row in rows),"metadata_chunks":0,"text_hits":text_hits}
