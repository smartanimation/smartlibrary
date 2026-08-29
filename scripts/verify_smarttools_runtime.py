from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

try:
    from .smarttools_runtime import inspect_runtime, load_definition, sha256, validate_zip_members
except ImportError:
    from smarttools_runtime import inspect_runtime, load_definition, sha256, validate_zip_members


def verify_root(root: Path, definition_path: Path) -> dict:
    definition = load_definition(definition_path)
    versions = inspect_runtime(root, definition)
    return {"version": definition["version"], "components": versions}


def verify_archive(artifact: Path, checksum: Path | None = None) -> dict:
    if checksum:
        expected = checksum.read_text(encoding="utf-8").split()[0].lower()
        actual = sha256(artifact)
        if actual != expected:
            raise ValueError(f"Runtime archive SHA256 mismatch: expected {expected}, got {actual}")
    with zipfile.ZipFile(artifact) as archive:
        validate_zip_members(archive)
        manifest = json.loads(archive.read("smarttools/runtime-manifest.json"))
        if manifest.get("schema") != "smartpipeline.smarttools-runtime-artifact.v1":
            raise ValueError("Runtime artifact manifest schema is invalid.")
        return manifest


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Verify an extracted SmartTools root or archive.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--root", type=Path)
    group.add_argument("--artifact", type=Path)
    parser.add_argument("--checksum", type=Path)
    parser.add_argument(
        "--definition",
        type=Path,
        default=root / "config" / "distribution" / "smarttools-runtime.yml",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = (
        verify_root(args.root.resolve(), args.definition.resolve())
        if args.root
        else verify_archive(args.artifact.resolve(), args.checksum.resolve() if args.checksum else None)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
