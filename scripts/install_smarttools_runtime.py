from __future__ import annotations

import argparse
import shutil
import uuid
import zipfile
from pathlib import Path

try:
    from .smarttools_runtime import sha256, validate_zip_members
except ImportError:
    from smarttools_runtime import sha256, validate_zip_members


def install(
    artifact: Path, checksum: Path, pipeline_root: Path, replace: bool = False
) -> Path:
    expected = checksum.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256(artifact)
    if actual != expected:
        raise ValueError(
            f"Runtime archive SHA256 mismatch: expected {expected}, got {actual}"
        )
    pipeline_root.mkdir(parents=True, exist_ok=True)
    destination = pipeline_root / "smarttools"
    staging = pipeline_root / f".smarttools-install-{uuid.uuid4().hex}"
    try:
        with zipfile.ZipFile(artifact) as archive:
            validate_zip_members(archive)
            archive.extractall(staging)
        extracted = staging / "smarttools"
        if not (extracted / "python" / "python.exe").is_file():
            raise FileNotFoundError("Runtime archive does not contain smarttools/python/python.exe")
        if destination.exists():
            if not replace:
                raise FileExistsError(f"SmartTools already exists; use --replace: {destination}")
            backup = pipeline_root / "smarttools.previous"
            if backup.exists():
                shutil.rmtree(backup)
            destination.replace(backup)
        extracted.replace(destination)
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install a SmartTools runtime archive.")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--pipeline-root", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(
        install(
            args.artifact.resolve(),
            args.checksum.resolve(),
            args.pipeline_root.resolve(),
            args.replace,
        )
    )
