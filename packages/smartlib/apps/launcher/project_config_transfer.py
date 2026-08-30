from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


MANIFEST_NAME = "smartproject-manifest.json"
SCHEMA = "smartpipeline.project-config.v1"
_EXCLUDED_NAMES = {".git", ".cache", "__pycache__", "secrets.yml", "credentials.json"}
_EXCLUDED_SUFFIXES = {".key", ".pem", ".pyc"}


def _safe_project_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError(f"Invalid project config name: {value!r}")
    return name


def _is_exportable(relative_path: Path) -> bool:
    return not (
        any(part.lower() in _EXCLUDED_NAMES for part in relative_path.parts)
        or relative_path.suffix.lower() in _EXCLUDED_SUFFIXES
    )


def export_project_config(source_dir, archive_path, project_name=None) -> Path:
    source = Path(source_dir).resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Project config directory was not found: {source}")
    name = _safe_project_name(project_name or source.name)
    archive = Path(archive_path).resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    files = []
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if (
                path.resolve() == archive
                or not path.is_file()
                or path.is_symlink()
                or not _is_exportable(relative)
            ):
                continue
            member = PurePosixPath("smartprojects", "config", name, *relative.parts)
            bundle.write(path, member.as_posix())
            files.append(relative.as_posix())
        manifest = {"schema": SCHEMA, "project": name, "files": files}
        bundle.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    if not files:
        archive.unlink(missing_ok=True)
        raise ValueError("Project config does not contain any exportable files.")
    return archive


def inspect_project_config_archive(archive_path) -> dict:
    archive = Path(archive_path)
    with zipfile.ZipFile(archive) as bundle:
        try:
            manifest = json.loads(bundle.read(MANIFEST_NAME).decode("utf-8"))
        except KeyError as exc:
            raise ValueError("This is not a SmartPipeline project config archive.") from exc
        if manifest.get("schema") != SCHEMA:
            raise ValueError(f"Unsupported project config schema: {manifest.get('schema')!r}")
        project = _safe_project_name(manifest.get("project"))
        prefix = PurePosixPath("smartprojects", "config", project)
        members = []
        for info in bundle.infolist():
            if info.is_dir() or info.filename == MANIFEST_NAME:
                continue
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts or member.parts[:3] != prefix.parts:
                raise ValueError(f"Unsafe project config archive member: {info.filename}")
            relative = PurePosixPath(*member.parts[3:])
            if not relative.parts:
                raise ValueError(f"Invalid project config archive member: {info.filename}")
            members.append((info, relative))
        if not members:
            raise ValueError("Project config archive does not contain any config files.")
        return {"project": project, "members": members}


def import_project_config(archive_path, projects_root, *, replace=False) -> Path:
    projects = Path(projects_root)
    inspected = inspect_project_config_archive(archive_path)
    target = projects / inspected["project"]
    if target.exists() and not replace:
        raise FileExistsError(f"Project config already exists: {target}")
    projects.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="smartproject-import-", dir=projects) as temp_name:
        staged = Path(temp_name) / inspected["project"]
        with zipfile.ZipFile(archive_path) as bundle:
            for info, relative in inspected["members"]:
                destination = staged.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        backup = Path(temp_name) / f"{inspected['project']}.previous"
        if target.exists():
            target.replace(backup)
        try:
            staged.replace(target)
        except Exception:
            if backup.exists() and not target.exists():
                backup.replace(target)
            raise
    return target
