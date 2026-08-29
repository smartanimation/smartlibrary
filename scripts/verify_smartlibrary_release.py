from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

try:
    from .smarttools_runtime import sha256
except ImportError:
    from smarttools_runtime import sha256


def verify(artifact: Path, checksum: Path) -> dict:
    expected = checksum.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256(artifact)
    if expected != actual:
        raise ValueError(f"Release SHA256 mismatch: expected {expected}, got {actual}")
    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
        required = {
            "smartlibrary/SmartLauncher.bat",
            "smartlibrary/VERSION",
            "smartlibrary/release-manifest.json",
            "smartprojects-template/studio.yml",
        }
        missing = required - names
        if missing:
            raise ValueError(f"Release archive is missing required files: {sorted(missing)}")
        forbidden = [
            name for name in names
            if "/.git/" in f"/{name}" or "/tests/" in f"/{name}"
            or "/.venv/" in f"/{name}" or "__pycache__" in name
        ]
        if forbidden:
            raise ValueError(f"Release archive contains development files: {forbidden[:10]}")
        manifest = json.loads(archive.read("smartlibrary/release-manifest.json"))
        if manifest.get("schema") != "smartpipeline.release.v1":
            raise ValueError("Release manifest schema is invalid.")
        return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a SmartPipeline application Release ZIP.")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(json.dumps(
        verify(args.artifact.resolve(), args.checksum.resolve()),
        ensure_ascii=False, indent=2,
    ))
