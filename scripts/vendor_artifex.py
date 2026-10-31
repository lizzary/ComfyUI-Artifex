"""Vendor the pinned official Artifex release; no model download or source changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "bin" / "upstream.json"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack(archive: Path, destination: Path) -> None:
    """Extract only regular release files, resolving library symlinks as copies."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir():
                    continue
                target = safe_target(destination, member.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                if not (member.isfile() or member.issym() or member.islnk()):
                    continue
                target = safe_target(destination, member.name)
                # extractfile resolves links inside the archive, never on the host.
                source = bundle.extractfile(member)
                if source is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("wb") as dst:
                    shutil.copyfileobj(source, dst)
                target.chmod(member.mode | 0o600)


def safe_target(destination: Path, name: str) -> Path:
    target = (destination / name).resolve()
    if not target.is_relative_to(destination.resolve()):
        raise ValueError(f"Unsafe archive path: {name}")
    return target


def reset_bundle_directory(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != (ROOT / "bin").resolve():
        raise ValueError(f"Unexpected bundle directory: {path}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", type=Path, default=ROOT / ".cache" / "artifex-release")
    parser.add_argument("--download", action="store_true", help="Download missing official archives")
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    args.archives.mkdir(parents=True, exist_ok=True)
    for asset in lock["assets"]:
        archive = args.archives / asset["name"]
        if not archive.exists() and args.download:
            print(f"Downloading {asset['name']}")
            urllib.request.urlretrieve(asset["url"], archive)
        if not archive.exists():
            raise SystemExit(f"Missing {archive}; supply --download or download the pinned assets first")
        if sha256(archive) != asset["sha256"]:
            raise SystemExit(f"Checksum mismatch: {archive}")
        with tempfile.TemporaryDirectory(prefix="unpack-", dir=args.archives) as temporary:
            stage = Path(temporary)
            unpack(archive, stage)
            release = stage / f"artifex-{asset['platform']}"
            destination = ROOT / "bin" / asset["platform"]
            reset_bundle_directory(destination)
            for source in release.iterdir():
                # Keep the canonical shared library. Versioned names in the
                # upstream archives are equivalent aliases, unnecessary here.
                if source.is_file() and source.name in {
                    "artifex", "artifex.exe", "onnxruntime.dll",
                    "libonnxruntime.so", "libonnxruntime.dylib",
                    "libonnxruntime_providers_shared.so",
                    "libonnxruntime_providers_shared.dylib",
                }:
                    shutil.copy2(source, destination / source.name)
            # The frontend is identical in the three official archives. Copy once.
            if asset["platform"] == "windows-amd64":
                reset_bundle_directory(ROOT / "bin" / "frontend")
                shutil.copytree(release / "frontend", ROOT / "bin" / "frontend", dirs_exist_ok=True)
                shutil.copy2(release / "LICENSE", ROOT / "bin" / "ARTIFEX-LICENSE")
        print(f"Verified and installed {asset['platform']}")
    write_manifest()


def write_manifest() -> None:
    files = {}
    for path in sorted((ROOT / "bin").rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.json":
            files[path.relative_to(ROOT / "bin").as_posix()] = sha256(path)
    (ROOT / "bin" / "SHA256SUMS.json").write_text(
        json.dumps(files, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
