from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any


IGNORED_NAMES = {".git", "__pycache__", ".pytest_cache"}
IGNORED_SUFFIXES = (".pyc", ".pyo")


def load_definition(path: Path) -> dict[str, Any]:
    import yaml

    with path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if data.get("schema") != "smartpipeline.smarttools-runtime.v1":
        raise ValueError(f"Unsupported smarttools runtime schema: {data.get('schema')!r}")
    return data


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_runtime_files(source_root: Path, layout: list[str]):
    for component in layout:
        root = source_root / component
        if not root.is_dir():
            raise FileNotFoundError(f"Required smarttools component was not found: {root}")
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source_root)
            if any(part in IGNORED_NAMES for part in relative.parts):
                continue
            if path.name.endswith(IGNORED_SUFFIXES):
                continue
            yield path, Path("smarttools") / relative


def inspect_runtime(runtime_root: Path, definition: dict[str, Any]) -> dict[str, Any]:
    python = runtime_root / "python" / "python.exe"
    if not python.is_file():
        raise FileNotFoundError(f"Runtime Python was not found: {python}")
    expected = dict(definition.get("components") or {})
    distributions = [name for name in expected if name != "python"]
    imports = list(definition.get("required_imports") or [])
    script = (
        "import importlib, importlib.metadata as m, json, platform;"
        f"imports={imports!r};"
        "[importlib.import_module(name) for name in imports];"
        f"names={distributions!r};"
        "print(json.dumps({'python':platform.python_version(),"
        "'components':{name:m.version(name) for name in names}}))"
    )
    result = subprocess.run(
        [str(python), "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    actual = json.loads(result.stdout)
    versions = {"python": actual["python"], **actual["components"]}
    mismatches = {
        name: {"expected": str(version), "actual": str(versions.get(name) or "")}
        for name, version in expected.items()
        if str(versions.get(name) or "") != str(version)
    }
    if mismatches:
        raise ValueError(f"Runtime component versions do not match definition: {mismatches}")
    return versions


def validate_zip_members(archive: zipfile.ZipFile) -> None:
    for member in archive.infolist():
        path = Path(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe runtime archive member: {member.filename}")
        if not path.parts or path.parts[0] != "smarttools":
            raise ValueError(f"Runtime archive member is outside smarttools/: {member.filename}")
