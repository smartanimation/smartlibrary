from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig, load_config
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import ProjectPaths
from smartlib.core.versioning import format_version, next_version, parse_version
from smartlib.dcc.maya.set_dress import FORMAT, load_package


@dataclass(frozen=True)
class SetDressIdentity:
    episode: str
    sequence: str
    shot: str = ""


@dataclass(frozen=True)
class SetDressVersion:
    package: str
    version: str
    path: str
    updated: str = ""
    comment: str = ""
    latest: bool = False


class SetDressPublishService:
    """Project-path, validation and immutable publish operations for Set Dress."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        if project_config.project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.paths = ProjectPaths(
            project_config.project_root,
            templates=_project_templates(project_config),
            project_name=_project_name(project_config),
        )

    def identity_from_context(self, context: dict[str, str]) -> SetDressIdentity:
        episode = str(context.get("episode") or "").strip()
        sequence = str(context.get("sequence") or "").strip()
        shot = str(context.get("shot") or "").strip()
        scene = Path(str(context.get("scene") or ""))
        parts = list(scene.parts)
        lower = [part.lower() for part in parts]
        if "shots" in lower:
            index = lower.index("shots")
            if len(parts) > index + 3:
                episode = episode or parts[index + 1]
                sequence = sequence or parts[index + 2]
                shot = shot or parts[index + 3]
        if not episode or not sequence:
            raise ValueError("Could not resolve episode/sequence from the current shot scene.")
        return SetDressIdentity(episode, sequence, shot)

    def data_path(self, identity: SetDressIdentity, package: str, *, scope: str = "shot") -> Path:
        filename = f"{_token(package)}.setdress.json"
        if scope == "sequence":
            return self.paths.sequence_workspace_root(identity.episode, identity.sequence) / "data" / "setdress" / filename
        if not identity.shot:
            raise ValueError("Shot is required for a shot Set Dress package.")
        return self.paths.shot_root(identity.episode, identity.sequence, identity.shot) / "data" / "setdress" / filename

    def publish(
        self,
        source: str | Path,
        identity: SetDressIdentity,
        *,
        package: str,
        scope: str = "shot",
        comment: str = "",
    ) -> Path:
        source_path = Path(source)
        loaded = load_package(source_path)
        if not loaded.layers:
            raise ValueError("Set Dress package has no layers.")
        if not any(layer.changes for layer in loaded.layers):
            raise ValueError("Set Dress package has no captured changes.")
        clean_package = _token(package)
        if scope == "sequence":
            base = self.paths.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "setdress" / clean_package
        else:
            if not identity.shot:
                raise ValueError("Shot is required for a shot Set Dress publish.")
            base = self.paths.shot_root(identity.episode, identity.sequence, identity.shot) / "publish" / "setdress" / clean_package
        version = _next_version(base)
        version_dir = base / version
        version_dir.mkdir(parents=True, exist_ok=False)
        output = version_dir / f"{clean_package}.setdress.json"
        shutil.copy2(source_path, output)
        published_at = datetime.now().isoformat(timespec="seconds")
        write_json(version_dir / "publish.json", {
            "publish_type": "set_dress",
            "format": FORMAT,
            "scope": scope,
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "package": clean_package,
            "version": version,
            "files": {"set_dress": output.name},
            "source_data": _relative(source_path, self.paths.project_root),
            "source_workfile": loaded.context.get("scene", ""),
            "layer_count": len(loaded.layers),
            "change_count": sum(len(layer.changes) for layer in loaded.layers),
            "comment": comment,
            "published_at": published_at,
        })
        write_json(base / "latest.json", {"version": version, "path": f"{version}/{output.name}"})
        _update_versions(base / "versions.json", version)
        return output

    def list_versions(self, identity: SetDressIdentity, *, scope: str = "shot") -> list[SetDressVersion]:
        if scope == "sequence":
            root = self.paths.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "setdress"
        else:
            if not identity.shot:
                return []
            root = self.paths.shot_root(identity.episode, identity.sequence, identity.shot) / "publish" / "setdress"
        rows = []
        if not root.exists():
            return rows
        for base in root.iterdir():
            if not base.is_dir():
                continue
            latest = read_json(base / "latest.json", {}) or {}
            latest_version = str(latest.get("version") or "")
            for version_dir in base.glob("v*"):
                if not version_dir.is_dir() or not version_dir.name[1:].isdigit():
                    continue
                manifest = read_json(version_dir / "publish.json", {}) or {}
                filename = str((manifest.get("files") or {}).get("set_dress") or f"{base.name}.setdress.json")
                path = version_dir / filename
                if not path.exists():
                    continue
                rows.append(SetDressVersion(
                    package=base.name,
                    version=version_dir.name,
                    path=str(path),
                    updated=str(manifest.get("published_at") or ""),
                    comment=str(manifest.get("comment") or ""),
                    latest=version_dir.name == latest_version,
                ))
        return sorted(rows, key=lambda row: (row.package, parse_version(row.version)), reverse=True)


def _project_templates(config: ProjectConfig) -> dict[str, str]:
    merged = {}
    for filename in ("templates_base.yml", "templates_assets.yml", "templates_shots.yml"):
        data = config.load(filename) if hasattr(config, "load") else load_config(config.config_dir / filename)
        merged.update({str(key): str(value) for key, value in (data.get("templates") or {}).items()})
    return merged


def _project_name(config: ProjectConfig) -> str:
    return str((config.base.get("anchors") or {}).get("project_name") or config.config_dir.name)


def _next_version(base: Path) -> str:
    versions = [parse_version(path.name) for path in base.glob("v*") if path.is_dir()]
    return format_version(next_version([value for value in versions if value]))


def _update_versions(path: Path, current: str) -> None:
    rows = read_json(path, []) or []
    output = []
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["status"] = "latest" if str(item.get("version")) == current else "superseded"
        found = found or str(item.get("version")) == current
        output.append(item)
    if not found:
        output.append({"version": current, "status": "latest"})
    write_json(path, output)


def _token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip()).strip("._") or "main"


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
