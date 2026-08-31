from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from .build_vendor_distribution import (
        _ignore, _smarttools_dependency, copy_vendor_library,
        _write_default_studio, _write_launcher,
    )
    from .smarttools_runtime import sha256
except ImportError:
    from build_vendor_distribution import (
        _ignore, _smarttools_dependency, copy_vendor_library,
        _write_default_studio, _write_launcher,
    )
    from smarttools_runtime import sha256


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.name in _ignore(str(path.parent), [path.name]):
            continue
        yield path, relative


def build(source_root: Path, output_dir: Path, version: str) -> Path:
    import shutil

    version = str(version).strip()
    if not version:
        raise ValueError("Release version is required.")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / f"smartpipeline-{version}-windows-x64.zip"
    temporary = artifact.with_suffix(artifact.suffix + ".tmp")
    runtime = _smarttools_dependency(source_root)
    with tempfile.TemporaryDirectory(prefix="smartpipeline-release-") as temp_value:
        staging = Path(temp_value)
        library = staging / "smartlibrary"
        projects = staging / "smartprojects-template"
        copy_vendor_library(source_root, library)
        _write_launcher(library)
        (library / "VERSION").write_text(version + "\n", encoding="utf-8")
        _write_default_studio(projects / "studio.yml", "vendor_id", "Vendor Name", "vendor")
        release_manifest = {
            "schema": "smartpipeline.release.v1",
            "version": version,
            "platform": "windows-x64",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "smarttools_runtime": runtime,
        }
        (library / "release-manifest.json").write_text(
            json.dumps(release_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                for source, relative in _iter_files(staging):
                    archive.write(source, relative.as_posix())
            temporary.replace(artifact)
        finally:
            if temporary.exists():
                temporary.unlink()
    artifact.with_suffix(artifact.suffix + ".sha256").write_text(
        f"{sha256(artifact)}  {artifact.name}\n", encoding="utf-8"
    )
    return artifact


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the SmartPipeline application Release ZIP.")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "dist")
    parser.add_argument("--version", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(build(args.source_root.resolve(), args.output_dir.resolve(), args.version))
