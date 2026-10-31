"""Check that every vendored binary/frontend file matches the committed manifest."""

import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "bin"
manifest = json.loads((root / "SHA256SUMS.json").read_text(encoding="utf-8"))
for name, expected in manifest.items():
    path = root / name
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise SystemExit(f"Bundled file checksum mismatch: {name}")
for name in ["windows-amd64/artifex.exe", "linux-amd64/artifex", "darwin-arm64/artifex", "frontend/index.html"]:
    if name not in manifest:
        raise SystemExit(f"Required bundled file is absent: {name}")
print(f"Verified {len(manifest)} bundled files")
