from __future__ import annotations

import argparse
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from .smarttools_runtime import inspect_runtime, iter_runtime_files, load_definition, sha256
except ImportError:
    from smarttools_runtime import inspect_runtime, iter_runtime_files, load_definition, sha256


def build(source_root: Path, definition_path: Path, output_dir: Path) -> Path:
    definition = load_definition(definition_path)
    versions = inspect_runtime(source_root, definition)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / str(definition["artifact"])
    temporary = artifact.with_suffix(artifact.suffix + ".tmp")
    files = list(iter_runtime_files(source_root, list(definition.get("layout") or [])))
    manifest = {
        "schema": "smartpipeline.smarttools-runtime-artifact.v1",
        "version": str(definition["version"]),
        "platform": str(definition["platform"]),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "components": versions,
        "files": len(files),
        "uncompressed_bytes": sum(path.stat().st_size for path, _ in files),
    }
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for source, relative in files:
                archive.write(source, relative.as_posix())
            archive.writestr(
                "smarttools/runtime-manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
        temporary.replace(artifact)
    finally:
        if temporary.exists():
            temporary.unlink()
    digest = sha256(artifact)
    artifact.with_suffix(artifact.suffix + ".sha256").write_text(
        f"{digest}  {artifact.name}\n", encoding="utf-8"
    )
    return artifact


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build the versioned SmartTools runtime archive.")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--definition",
        type=Path,
        default=root / "config" / "distribution" / "smarttools-runtime.yml",
    )
    parser.add_argument("--output-dir", type=Path, default=root / "dist")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(build(args.source_root.resolve(), args.definition.resolve(), args.output_dir.resolve()))
