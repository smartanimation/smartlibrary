from __future__ import annotations

import csv
import os
import re
from dataclasses import asdict, dataclass, field
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.credentials import credentials_path
from smartlib.core.metadata import read_json, sidecar_path, write_json
from smartlib.core.path_resolver import ProjectPaths
from smartlib.core.selection_context import read_selected_asset
from smartlib.core.validation import ValidationIssue
from smartlib.core.versioning import format_version, next_version, parse_version


DEFAULT_SHOT_DEPARTMENTS = ["layout", "anim", "fx", "lighting", "comp"]
DEFAULT_REVIEW_LAYERS = {
    "CHA": {"members": [], "order": 20},
    "CHB": {"members": [], "order": 10},
    "BGA": {"members": [], "order": 0},
    "FX": {"members": [], "order": 30},
    "ENV": {"members": [], "order": -10},
}
ROLE_ALIASES = {
    "BG": "BGA",
    "BACKGROUND": "BGA",
    "BACK": "BGA",
    "SET": "BGA",
    "ENVIRONMENT": "BGA",
}
VALID_ASSET_PUBLISH = {"approved", "latest"}
CAST_CSV_COLUMNS = [
    "episode",
    "sequence",
    "shot",
    "cast_key",
    "asset",
    "variant",
    "role",
    "namespace",
    "asset_publish",
    "required",
    "note",
]


@dataclass(frozen=True)
class ShotIdentity:
    episode: str
    sequence: str
    shot: str

    @property
    def code(self) -> str:
        return f"{self.episode}_{self.sequence}_{self.shot}"


@dataclass(frozen=True)
class SequenceIdentity:
    episode: str
    sequence: str

    @property
    def code(self) -> str:
        return f"{self.episode}_{self.sequence}"


@dataclass(frozen=True)
class CastEntry:
    asset: str
    variant: str = "default"
    role: str = "CHA"
    namespace: str = ""
    asset_publish: str = "approved"
    note: str = ""
    required: bool = True


@dataclass(frozen=True)
class ReviewLayer:
    members: list[str] = field(default_factory=list)
    order: int = 0
    camera: dict[str, Any] = field(default_factory=dict)
    resolution: dict[str, Any] = field(default_factory=dict)
    ae: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BuildPreviewItem:
    cast_key: str
    asset: str
    variant: str
    namespace: str
    role: str
    review_layer: str
    asset_publish: str
    required: bool
    status: str
    asset_root: str = ""
    variant_root: str = ""
    publish_path: str = ""
    message: str = ""


@dataclass(frozen=True)
class ShotWorkFile:
    department: str
    option: str
    file: str
    path: str
    updated: str
    version: int = 0
    take: int = 0
    comment: str = ""
    thumbnail: str = ""


@dataclass(frozen=True)
class ShotDataVersion:
    name: str
    version: str
    path: str
    updated: str = ""
    comment: str = ""
    latest: bool = False


@dataclass(frozen=True)
class LayoutPublishStatusItem:
    name: str
    state: str
    version: str = ""
    path: str = ""
    message: str = ""


@dataclass(frozen=True)
class AnimInputBuildResult:
    shot: str
    cast_publish: Path
    placements_publish: Path
    anim_input: Path


@dataclass(frozen=True)
class ShotCreateRequest:
    episode: str
    sequence: str
    shot: str
    fps: int = 24
    cut_in: int = 1001
    cut_out: int = 1001
    status: str = "wip"
    create_work_dirs: bool = True

    @property
    def identity(self) -> ShotIdentity:
        return ShotIdentity(self.episode, self.sequence, self.shot)


class ShotManagerService:
    """Core/service layer for shot folders, shot.json, and cast.json."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.paths = ProjectPaths(project_root)

    @property
    def shot_departments(self) -> list[str]:
        departments = self.project_config.base.get("shot_depts") or []
        return list(departments) if departments else list(DEFAULT_SHOT_DEPARTMENTS)

    @property
    def project_fps(self) -> int:
        fps = (self.project_config.base.get("anchors") or {}).get("fps", 24)
        try:
            return int(fps)
        except (TypeError, ValueError):
            return 24

    def shot_root(self, identity: ShotIdentity) -> Path:
        return self.paths.shot_root(identity.episode, identity.sequence, identity.shot)

    def sequence_workspace_root(self, episode: str, sequence: str) -> Path:
        return self.paths.sequence_workspace_root(episode, sequence)

    def list_shots(self) -> list[ShotIdentity]:
        shots_root = self.paths.shots_root()
        if not shots_root.exists():
            return []
        shots: list[ShotIdentity] = []
        for shot_json in shots_root.glob("*/*/*/shot.json"):
            shot_root = shot_json.parent
            try:
                sequence_root = shot_root.parent
                episode_root = sequence_root.parent
                shots.append(
                    ShotIdentity(
                        episode=episode_root.name,
                        sequence=sequence_root.name,
                        shot=shot_root.name,
                    )
                )
            except Exception:
                continue
        return sorted(shots, key=lambda item: (item.episode.lower(), item.sequence.lower(), item.shot.lower()))

    def list_sequences(self) -> list[SequenceIdentity]:
        sequences: dict[tuple[str, str], SequenceIdentity] = {}
        sequences_root = self.paths.sequences_root()
        if sequences_root.exists():
            for sequence_json in sequences_root.glob("*/*/sequence.json"):
                sequence_root = sequence_json.parent
                episode_root = sequence_root.parent
                sequences[(episode_root.name, sequence_root.name)] = SequenceIdentity(episode_root.name, sequence_root.name)
        for shot in self.list_shots():
            sequences.setdefault((shot.episode, shot.sequence), SequenceIdentity(shot.episode, shot.sequence))
        return sorted(sequences.values(), key=lambda item: (item.episode.lower(), item.sequence.lower()))

    def load_sequence(self, identity: SequenceIdentity) -> dict[str, Any]:
        sequence_path = self.sequence_workspace_root(identity.episode, identity.sequence) / "sequence.json"
        data = read_json(sequence_path, None)
        if isinstance(data, dict):
            return data
        legacy_path = self.paths.sequence_root(identity.episode, identity.sequence) / "sequence.json"
        data = read_json(legacy_path, None)
        if isinstance(data, dict):
            return data
        shots = [shot for shot in self.list_shots() if shot.episode == identity.episode and shot.sequence == identity.sequence]
        return {"episode": identity.episode, "sequence": identity.sequence, "shots": [{"shot": shot.shot} for shot in shots]}

    def load_shot(self, identity: ShotIdentity) -> dict[str, Any]:
        return read_json(self.shot_root(identity) / "shot.json", {})

    def shot_work_dir(
        self,
        identity: ShotIdentity,
        department: str,
        option: str | None = None,
        tool_name: str = "maya",
    ) -> Path:
        work_dir = self.paths.shot_work_dir(
            identity.episode,
            identity.sequence,
            identity.shot,
            department,
            _normalize_tool_name(tool_name),
        )
        if option:
            return work_dir / _normalize_work_option(option)
        return work_dir

    def legacy_shot_work_dir(self, identity: ShotIdentity, department: str, option: str | None = None) -> Path:
        work_dir = self.paths.legacy_shot_work_dir(identity.episode, identity.sequence, identity.shot, department)
        if option:
            return work_dir / _normalize_work_option(option)
        return work_dir

    def list_shot_work_options(self, identity: ShotIdentity, department: str, tool_name: str = "maya") -> list[str]:
        work_dirs = [self.shot_work_dir(identity, department, tool_name=tool_name), self.legacy_shot_work_dir(identity, department)]
        options = {"main"}
        for work_dir in work_dirs:
            if not work_dir.exists():
                continue
            options.update(
                path.name
                for path in work_dir.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            )
        return sorted(options, key=lambda item: (item != "main", item.lower()))

    def create_shot_work_option(self, identity: ShotIdentity, option: str) -> list[Path]:
        option_name = _normalize_work_option(option)
        if option_name == "all":
            raise ValueError("'all' is reserved for the work option filter.")
        paths = []
        for department in self.shot_departments:
            path = self.shot_work_dir(identity, department, option_name, tool_name="maya")
            path.mkdir(parents=True, exist_ok=True)
            paths.append(path)
        return paths

    def shot_work_file_path(
        self,
        identity: ShotIdentity,
        department: str,
        version: int,
        take: int,
        option: str = "main",
        tool_name: str = "maya",
        ext: str = "ma",
    ) -> Path:
        version_label = f"v{version:03d}"
        take_label = f"{take:02d}"
        filename = f"{identity.shot}_{department}_{version_label}_{take_label}.{ext.lstrip('.')}"
        return self.shot_work_dir(identity, department, option, tool_name=tool_name) / filename

    def sequence_work_dir(self, identity: SequenceIdentity, department: str, tool_name: str = "maya") -> Path:
        return self.paths.sequence_work_dir(identity.episode, identity.sequence, department, _normalize_tool_name(tool_name))

    def sequence_work_file_path(
        self,
        identity: SequenceIdentity,
        department: str,
        version: int,
        take: int,
        tool_name: str = "maya",
        ext: str = "ma",
    ) -> Path:
        version_label = f"v{version:03d}"
        take_label = f"{take:02d}"
        filename = f"{identity.episode}_{identity.sequence}_{department}_{version_label}_{take_label}.{ext.lstrip('.')}"
        return self.sequence_work_dir(identity, department, tool_name) / filename

    def next_sequence_work_path(
        self,
        identity: SequenceIdentity,
        department: str,
        current_path: str | Path | None = None,
        tool_name: str = "maya",
        ext: str = "ma",
    ) -> Path:
        parsed = parse_shot_work_file(Path(current_path).name) if current_path else None
        if parsed and parsed.get("shot") != f"{identity.episode}_{identity.sequence}":
            parsed = None
        if parsed:
            department = parsed["department"]
            ext = parsed["ext"]
            version = parsed["version"]
        else:
            version = 1
        take = self.next_sequence_work_take(identity, department, version, tool_name, ext)
        return self.sequence_work_file_path(identity, department, version, take, tool_name, ext)

    def next_sequence_work_take(
        self,
        identity: SequenceIdentity,
        department: str,
        version: int,
        tool_name: str = "maya",
        ext: str = "ma",
    ) -> int:
        work_dir = self.sequence_work_dir(identity, department, tool_name)
        max_take = 0
        for path in work_dir.iterdir() if work_dir.exists() else []:
            parsed = parse_shot_work_file(path.name)
            if not parsed:
                continue
            if (
                parsed["shot"] == f"{identity.episode}_{identity.sequence}"
                and parsed["department"] == department
                and parsed["version"] == version
                and parsed["ext"] == ext
            ):
                max_take = max(max_take, parsed["take"])
        return max_take + 1

    def next_shot_work_path(
        self,
        identity: ShotIdentity,
        department: str,
        current_path: str | Path | None = None,
        next_version: bool = False,
        option: str = "main",
        tool_name: str = "maya",
        ext: str = "ma",
    ) -> Path:
        parsed = parse_shot_work_file(Path(current_path).name) if current_path else None
        if parsed and parsed.get("shot") != identity.shot:
            parsed = None
        if parsed:
            department = parsed["department"]
            ext = parsed["ext"]
            version = parsed["version"] + 1 if next_version else parsed["version"]
            if next_version:
                take = 1
            else:
                take = self.next_shot_work_take(identity, department, version, ext, option=option, tool_name=tool_name)
        else:
            version = 1
            take = self.next_shot_work_take(identity, department, version, ext, option=option, tool_name=tool_name)
        return self.shot_work_file_path(identity, department, version, take, option, tool_name, ext)

    def next_shot_work_take(
        self,
        identity: ShotIdentity,
        department: str,
        version: int,
        ext: str = "ma",
        option: str = "main",
        tool_name: str = "maya",
    ) -> int:
        max_take = 0
        for work_dir in self._shot_work_option_dirs(identity, department, option, tool_name=tool_name):
            for path in work_dir.iterdir() if work_dir.exists() else []:
                parsed = parse_shot_work_file(path.name)
                if not parsed:
                    continue
                if parsed["shot"] == identity.shot and parsed["department"] == department and parsed["version"] == version and parsed["ext"] == ext:
                    max_take = max(max_take, parsed["take"])
        return max_take + 1

    def list_shot_work_files(
        self,
        identity: ShotIdentity,
        department: str | None = None,
        option: str | None = None,
        tool_name: str = "maya",
    ) -> list[ShotWorkFile]:
        departments = [department] if department else self.shot_departments
        files: list[ShotWorkFile] = []
        for dept in departments:
            options = self.list_shot_work_options(identity, dept, tool_name=tool_name) if not option or option == "all" else [_normalize_work_option(option)]
            for option_name in options:
                for work_dir in self._shot_work_option_dirs(identity, dept, option_name, tool_name=tool_name):
                    if not work_dir.exists():
                        continue
                    for path in work_dir.iterdir():
                        if not path.is_file() or path.suffix.lower() not in {".ma", ".mb"}:
                            continue
                        parsed = parse_shot_work_file(path.name) or {}
                        metadata = read_json(sidecar_path(path), {}) or {}
                        comment = str(metadata.get("comment") or "")
                        thumbnail = str(metadata.get("thumbnail") or "")
                        updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                        files.append(
                            ShotWorkFile(
                                department=dept,
                                option=str(metadata.get("option") or option_name),
                                file=path.name,
                                path=str(path),
                                updated=updated,
                                version=int(parsed.get("version") or 0),
                                take=int(parsed.get("take") or 0),
                                comment=comment,
                                thumbnail=thumbnail,
                            )
                        )
        return sorted(files, key=lambda item: (item.department, item.option, item.version, item.take, item.file.lower()), reverse=True)

    def list_sequence_work_files(
        self,
        identity: SequenceIdentity,
        department: str | None = None,
        tool_name: str | None = None,
    ) -> list[ShotWorkFile]:
        departments = [department] if department else ["layout"]
        tools = [_normalize_tool_name(tool_name)] if tool_name else ["maya", "houdini"]
        files: list[ShotWorkFile] = []
        for dept in departments:
            for tool in tools:
                work_dir = self.sequence_work_dir(identity, dept, tool)
                if not work_dir.exists():
                    continue
                for path in work_dir.iterdir():
                    if not path.is_file() or path.suffix.lower() not in {".ma", ".mb", ".hip", ".hiplc", ".hipnc"}:
                        continue
                    parsed = parse_shot_work_file(path.name) or {}
                    metadata = read_json(sidecar_path(path), {}) or {}
                    updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    files.append(
                        ShotWorkFile(
                            department=dept,
                            option=str(metadata.get("tool") or tool),
                            file=path.name,
                            path=str(path),
                            updated=updated,
                            version=int(parsed.get("version") or 0),
                            take=int(parsed.get("take") or 0),
                            comment=str(metadata.get("comment") or ""),
                            thumbnail=str(metadata.get("thumbnail") or ""),
                        )
                    )
        return sorted(files, key=lambda item: (item.department, item.option, item.version, item.take, item.file.lower()), reverse=True)

    def _shot_work_option_dirs(
        self,
        identity: ShotIdentity,
        department: str,
        option: str,
        tool_name: str = "maya",
    ) -> list[Path]:
        option_name = _normalize_work_option(option)
        directories = [self.shot_work_dir(identity, department, option_name, tool_name=tool_name)]
        if option_name == "main":
            directories.append(self.shot_work_dir(identity, department, tool_name=tool_name))
            directories.append(self.legacy_shot_work_dir(identity, department))
        directories.append(self.legacy_shot_work_dir(identity, department, option_name))
        seen = set()
        unique = []
        for directory in directories:
            key = directory.as_posix().lower()
            if key not in seen:
                seen.add(key)
                unique.append(directory)
        return unique

    def write_shot_work_metadata(
        self,
        path: str | Path,
        identity: ShotIdentity,
        department: str,
        option: str = "main",
        scene_info: dict[str, Any] | None = None,
        comment: str = "",
        thumbnail: str = "",
    ) -> Path:
        work_path = Path(path)
        parsed = parse_shot_work_file(work_path.name) or {}
        data = {
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "department": department,
            "tool": "maya",
            "option": _normalize_work_option(option),
            "version": parsed.get("version"),
            "take": parsed.get("take"),
            "comment": comment,
            "thumbnail": thumbnail,
            "source": work_path.name,
            "scene_info": scene_info or {},
        }
        return write_json(sidecar_path(work_path), data)

    def write_sequence_work_metadata(
        self,
        path: str | Path,
        identity: SequenceIdentity,
        department: str,
        tool_name: str = "maya",
        scene_info: dict[str, Any] | None = None,
        comment: str = "",
        thumbnail: str = "",
    ) -> Path:
        work_path = Path(path)
        parsed = parse_shot_work_file(work_path.name) or {}
        data = {
            "episode": identity.episode,
            "sequence": identity.sequence,
            "department": department,
            "tool": _normalize_tool_name(tool_name),
            "version": parsed.get("version"),
            "take": parsed.get("take"),
            "comment": comment,
            "thumbnail": thumbnail,
            "source": work_path.name,
            "scene_info": scene_info or {},
        }
        return write_json(sidecar_path(work_path), data)

    @staticmethod
    def thumbnail_path_for_workfile(path: str | Path) -> Path:
        work_path = Path(path)
        return work_path.parent / ".thumbnails" / f"{work_path.stem}.jpg"

    def validate_cast(self, identity: ShotIdentity) -> list[ValidationIssue]:
        return validate_cast_data(self.load_cast(identity))

    def build_preview(self, identity: ShotIdentity) -> list[BuildPreviewItem]:
        return self._build_preview_from_cast(self.load_cast(identity))

    def build_sequence_preview(self, identity: SequenceIdentity) -> list[BuildPreviewItem]:
        return self._build_preview_from_cast(self.load_sequence_cast(identity.episode, identity.sequence))

    def latest_anim_input(self, identity: ShotIdentity) -> Path | None:
        base_dir = self.shot_root(identity) / "publish" / "anim_input" / "main"
        latest = read_json(base_dir / "latest.json", {}) or {}
        path = base_dir / str(latest.get("path") or "")
        return path if path.exists() else None

    def build_preview_from_anim_input(self, identity: ShotIdentity) -> list[BuildPreviewItem]:
        anim_input = self.latest_anim_input(identity)
        if not anim_input:
            raise RuntimeError(f"Anim input package was not found for {identity.code}.")
        data = read_json(anim_input, {}) or {}
        cast_path = self.paths.project_root / str(data.get("cast") or "")
        cast_data = read_json(cast_path, {}) if cast_path.exists() else {}
        if not (cast_data.get("cast") or {}):
            cast_data = self.load_cast(identity)
        if not (cast_data.get("cast") or {}):
            cast_data = self.load_sequence_cast(identity.episode, identity.sequence)
        return self._build_preview_from_cast(cast_data)

    def publish_animation_curves(
        self,
        identity: ShotIdentity,
        curve_data: dict[str, Any],
        *,
        target: str = "main",
        subset: str = "curves",
        source_workfile: str | Path = "",
        comment: str = "",
    ) -> Path:
        return self.export_animation_curves_data(
            identity,
            curve_data,
            target=target,
            subset=subset,
            source_workfile=source_workfile,
            comment=comment,
        )

    def export_animation_curves_data(
        self,
        identity: ShotIdentity,
        curve_data: dict[str, Any],
        *,
        target: str = "main",
        subset: str = "curves",
        source_workfile: str | Path = "",
        comment: str = "",
    ) -> Path:
        clean_target = _clean_publish_token(target or "main")
        clean_subset = _clean_publish_token(subset or "curves")
        base_dir = self.shot_root(identity) / "data" / "animation" / clean_target / clean_subset
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)
        data = dict(curve_data)
        data.update(
            {
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "data_type": "animation",
                "target": clean_target,
                "subset": clean_subset,
                "version": version_label,
                "comment": comment,
            }
        )
        if source_workfile:
            data["source_workfile"] = self._relative_to_project(Path(source_workfile))
        animation_path = write_json(version_dir / "animation_curve.json", data)
        write_json(
            version_dir / "data.json",
            {
                "data_type": "animation",
                "target": clean_target,
                "subset": clean_subset,
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "version": version_label,
                "files": {"animation_curve": "animation_curve.json"},
                "source_workfile": data.get("source_workfile", ""),
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/animation_curve.json"})
        self._update_versions(base_dir / "versions.json", version_label)
        return animation_path

    def latest_animation_curve_path(
        self,
        identity: ShotIdentity,
        *,
        target: str = "main",
        subset: str = "curves",
    ) -> Path | None:
        clean_target = _clean_publish_token(target or "main")
        clean_subset = _clean_publish_token(subset or "curves")
        base_dir = self.shot_root(identity) / "data" / "animation" / clean_target / clean_subset
        latest = read_json(base_dir / "latest.json", {}) or {}
        path = base_dir / str(latest.get("path") or "")
        if path.exists():
            return path
        legacy_base_dir = self.shot_root(identity) / "publish" / "animation" / clean_target / clean_subset
        latest = read_json(legacy_base_dir / "latest.json", {}) or {}
        path = legacy_base_dir / str(latest.get("path") or "")
        return path if path.exists() else None

    def list_animation_curve_versions(
        self,
        identity: ShotIdentity,
        *,
        target: str = "main",
        subset: str = "curves",
    ) -> list[ShotDataVersion]:
        clean_target = _clean_publish_token(target or "main")
        clean_subset = _clean_publish_token(subset or "curves")
        base_dir = self.shot_root(identity) / "data" / "animation" / clean_target / clean_subset
        rows = self._list_animation_curve_versions_from_dir(base_dir, clean_target, clean_subset, legacy=False)
        legacy_base_dir = self.shot_root(identity) / "publish" / "animation" / clean_target / clean_subset
        rows.extend(self._list_animation_curve_versions_from_dir(legacy_base_dir, clean_target, clean_subset, legacy=True))
        return sorted(rows, key=lambda row: (parse_version(row.version), row.name), reverse=True)

    def _list_animation_curve_versions_from_dir(
        self,
        base_dir: Path,
        target: str,
        subset: str,
        *,
        legacy: bool = False,
    ) -> list[ShotDataVersion]:
        if not base_dir.exists():
            return []
        latest = read_json(base_dir / "latest.json", {}) or {}
        latest_version = str(latest.get("version") or "")
        rows: list[ShotDataVersion] = []
        for version_dir in base_dir.glob("v*"):
            if not version_dir.is_dir() or not version_dir.name[1:].isdigit():
                continue
            animation_path = version_dir / "animation_curve.json"
            if not animation_path.exists():
                continue
            metadata = read_json(version_dir / "data.json", {}) or read_json(version_dir / "publish.json", {}) or {}
            updated = ""
            try:
                updated = datetime.fromtimestamp(animation_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            prefix = "legacy publish" if legacy else "data"
            rows.append(
                ShotDataVersion(
                    name=f"{prefix}/animation/{target}/{subset}",
                    version=version_dir.name,
                    path=str(animation_path),
                    updated=updated,
                    comment=str(metadata.get("comment") or ""),
                    latest=version_dir.name == latest_version,
                )
            )
        return rows

    def publish_animation_from_data(
        self,
        identity: ShotIdentity,
        animation_curve_path: str | Path,
        *,
        target: str = "main",
        subset: str = "curves",
        comment: str = "",
    ) -> Path:
        source_path = Path(animation_curve_path)
        if not source_path.exists():
            raise FileNotFoundError(f"Animation curve data was not found: {source_path}")
        curve_data = read_json(source_path, {}) or {}
        if not curve_data.get("curves"):
            raise RuntimeError(f"Animation curve data has no curves: {source_path}")

        clean_target = _clean_publish_token(target or curve_data.get("target") or "main")
        clean_subset = _clean_publish_token(subset or curve_data.get("subset") or "curves")
        base_dir = self.shot_root(identity) / "publish" / "animation" / clean_target / clean_subset
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)

        published_curve = dict(curve_data)
        published_curve.update(
            {
                "publish_type": "animation",
                "data_source": self._relative_to_project(source_path),
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "target": clean_target,
                "subset": clean_subset,
                "version": version_label,
                "comment": comment,
            }
        )
        animation_curve_file = write_json(version_dir / "animation_curve.json", published_curve)
        usd_path = version_dir / "animation.usd"
        usd_path.write_text(
            "\n".join(
                [
                    "#usda 1.0",
                    "(",
                    "    customLayerData = {",
                    '        string smartpipeline_publish_type = "animation"',
                    f'        string episode = "{identity.episode}"',
                    f'        string sequence = "{identity.sequence}"',
                    f'        string shot = "{identity.shot}"',
                    f'        string target = "{clean_target}"',
                    f'        string subset = "{clean_subset}"',
                    f'        string version = "{version_label}"',
                    "    }",
                    ")",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": "animation",
                "target": clean_target,
                "subset": clean_subset,
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "version": version_label,
                "files": {
                    "animation_curve": "animation_curve.json",
                    "usd": "animation.usd",
                },
                "source_data": self._relative_to_project(source_path),
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/animation_curve.json"})
        self._update_versions(base_dir / "versions.json", version_label)
        return animation_curve_file

    def published_animation_source_paths(self, identity: ShotIdentity) -> set[str]:
        root = self.shot_root(identity) / "publish" / "animation"
        sources: set[str] = set()
        if not root.exists():
            return sources
        for publish_json in root.glob("**/publish.json"):
            data = read_json(publish_json, {}) or {}
            source_data = str(data.get("source_data") or "")
            if not source_data:
                continue
            source_path = self.paths.project_root / source_data
            sources.add(source_path.resolve().as_posix().lower())
        return sources

    def list_shot_data_versions(self, identity: ShotIdentity) -> list[ShotDataVersion]:
        return self._list_data_versions(self.shot_root(identity) / "data")

    def list_sequence_data_versions(self, identity: SequenceIdentity, department: str = "layout") -> list[ShotDataVersion]:
        return self._list_data_versions(self.sequence_workspace_root(identity.episode, identity.sequence) / department / "data")

    def _list_data_versions(self, data_root: Path) -> list[ShotDataVersion]:
        rows: list[ShotDataVersion] = []
        if not data_root.exists():
            return rows
        latest_by_base: dict[Path, str] = {}
        for latest_json in data_root.glob("**/latest.json"):
            latest = read_json(latest_json, {}) or {}
            latest_by_base[latest_json.parent] = str(latest.get("version") or "")
        for version_dir in data_root.glob("**/v*"):
            if not version_dir.is_dir() or not version_dir.name[1:].isdigit():
                continue
            base_dir = version_dir.parent
            files = sorted(
                path.name
                for path in version_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".json", ".yml", ".yaml"}
            )
            try:
                rel_name = base_dir.relative_to(data_root).as_posix()
            except ValueError:
                rel_name = base_dir.name
            updated = ""
            try:
                updated = datetime.fromtimestamp(version_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            rows.append(
                ShotDataVersion(
                    name=rel_name,
                    version=version_dir.name,
                    path=str(version_dir),
                    updated=updated,
                    comment=", ".join(files),
                    latest=latest_by_base.get(base_dir) == version_dir.name,
                )
            )
        return sorted(rows, key=lambda row: (row.name, row.version), reverse=True)

    def layout_publish_status(self, identity: SequenceIdentity) -> list[LayoutPublishStatusItem]:
        root = self.sequence_workspace_root(identity.episode, identity.sequence)
        cast_path = root / "cast.json"
        cast_data = read_json(cast_path, {}) if cast_path.exists() else {}
        statuses = [
            self._status_from_file(
                "cast",
                cast_path,
                exists=bool((cast_data or {}).get("cast")),
                message="cast.json has no cast entries.",
            ),
            self._status_from_latest("placements", root / "publish" / "layout" / "placements"),
            self._status_from_camera_publishes(root / "publish" / "camera"),
            self._status_from_latest(
                "editorial",
                self.paths.project_root / "editorial" / "publish" / identity.episode / identity.sequence,
            ),
            self._status_from_sequence_timing(identity),
        ]
        return statuses

    def shot_anim_input_status(self, identity: ShotIdentity) -> list[LayoutPublishStatusItem]:
        shot_data = self.load_shot(identity)
        sequence_data = self.load_sequence(SequenceIdentity(identity.episode, identity.sequence))
        sequence_root = self.sequence_workspace_root(identity.episode, identity.sequence)
        shot_cast = self.load_cast(identity)
        sequence_cast = self.load_sequence_cast(identity.episode, identity.sequence)
        cast_ready = bool((shot_cast.get("cast") or {}) or (sequence_cast.get("cast") or {}))
        camera_publish = self._latest_shot_camera_publish(identity)
        layout_overlay = self._latest_layout_overlay_usd(identity)
        statuses = [
            self._status_from_file(
                "cast",
                self.shot_root(identity) / "cast.json",
                exists=cast_ready,
                message="No shot or sequence cast entries were found.",
            ),
            self._status_from_latest("placements", sequence_root / "publish" / "layout" / "placements"),
            LayoutPublishStatusItem(
                name="camera",
                state="READY" if camera_publish else "MISSING",
                version=self._latest_publish_version_label(sequence_root / "publish" / "camera" / identity.shot / "main"),
                path=str(camera_publish or sequence_root / "publish" / "camera" / identity.shot / "main"),
                message="" if camera_publish else "Camera publish was not found for this shot.",
            ),
            self._status_from_shot_timing(identity, shot_data, sequence_data),
            LayoutPublishStatusItem(
                name="layout_overlay",
                state="READY" if layout_overlay else "OPTIONAL",
                path=str(layout_overlay or ""),
                message="" if layout_overlay else "Optional layout USD overlay was not found.",
            ),
        ]
        return statuses

    def build_anim_input_package(self, identity: SequenceIdentity, comment: str = "") -> list[AnimInputBuildResult]:
        statuses = self.layout_publish_status(identity)
        blocking = [item for item in statuses if item.state != "READY"]
        if blocking:
            names = ", ".join(item.name for item in blocking)
            raise RuntimeError(f"Anim input package is blocked by missing layout publish data: {names}")
        root = self.sequence_workspace_root(identity.episode, identity.sequence)
        sequence_data = self.load_sequence(identity)
        shots = self._sequence_shot_identities(identity, sequence_data)
        if not shots:
            raise RuntimeError("No production shots were found for this sequence.")
        results = []
        for shot_identity in shots:
            results.append(self.build_anim_input_package_for_shot(shot_identity, comment=comment, sequence_data=sequence_data))
        return results

    def build_anim_input_package_for_shot(
        self,
        identity: ShotIdentity,
        comment: str = "",
        sequence_data: dict[str, Any] | None = None,
    ) -> AnimInputBuildResult:
        statuses = self.shot_anim_input_status(identity)
        blocking = [item for item in statuses if item.state == "MISSING" and item.name != "layout_overlay"]
        if blocking:
            names = ", ".join(item.name for item in blocking)
            raise RuntimeError(f"Anim input package is blocked by missing shot publish data: {names}")
        shot_data = self.load_shot(identity)
        sequence_data = sequence_data or self.load_sequence(SequenceIdentity(identity.episode, identity.sequence))
        cast_publish = self.publish_shot_cast_from_sequence(identity, comment=comment)
        placements_publish = self.publish_shot_placements_from_sequence(identity, comment=comment)
        anim_input = self.publish_shot_anim_input(
            identity,
            cast_publish=cast_publish,
            placements_publish=placements_publish,
            shot_data=shot_data,
            sequence_data=sequence_data,
            comment=comment,
        )
        return AnimInputBuildResult(
            shot=identity.shot,
            cast_publish=cast_publish,
            placements_publish=placements_publish,
            anim_input=anim_input,
        )

    def publish_shot_cast_from_sequence(self, identity: ShotIdentity, comment: str = "") -> Path:
        shot_cast = self.load_cast(identity)
        source = shot_cast if (shot_cast.get("cast") or {}) else self.load_sequence_cast(identity.episode, identity.sequence)
        cast_data = deepcopy(source)
        cast_data["episode"] = identity.episode
        cast_data["sequence"] = identity.sequence
        cast_data["shot"] = identity.shot
        if not (shot_cast.get("cast") or {}) and (cast_data.get("cast") or {}):
            self.write_cast(identity, cast_data)
        base_dir = self.shot_root(identity) / "publish" / "cast" / "main"
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)
        cast_path = write_json(version_dir / "cast.json", cast_data)
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": "cast",
                "subset": "main",
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "version": version_label,
                "files": {"cast": "cast.json"},
                "source": "shot_cast" if (shot_cast.get("cast") or {}) else "sequence_cast",
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/cast.json"})
        self._update_versions(base_dir / "versions.json", version_label)
        return cast_path

    def publish_sequence_cast(self, episode: str, sequence: str, comment: str = "") -> Path:
        cast_data = deepcopy(self.load_sequence_cast(episode, sequence))
        cast_data["episode"] = episode
        cast_data["sequence"] = sequence
        issues = validate_cast_data(cast_data)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            messages = ", ".join(issue.message for issue in errors)
            raise ValueError(f"Invalid sequence cast data: {messages}")
        base_dir = self.sequence_workspace_root(episode, sequence) / "publish" / "cast" / "main"
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)
        cast_path = write_json(version_dir / "cast.json", cast_data)
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": "cast",
                "subset": "main",
                "episode": episode,
                "sequence": sequence,
                "version": version_label,
                "files": {"cast": "cast.json"},
                "source": "sequence_cast",
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/cast.json"})
        self._update_versions(base_dir / "versions.json", version_label)
        return cast_path

    def publish_shot_placements_from_sequence(self, identity: ShotIdentity, comment: str = "") -> Path:
        sequence_root = self.sequence_workspace_root(identity.episode, identity.sequence)
        source_base = sequence_root / "publish" / "layout" / "placements"
        latest = read_json(source_base / "latest.json", {}) or {}
        source_dir = source_base / str(latest.get("version") or "")
        if not source_dir.exists():
            raise RuntimeError(f"Sequence placement publish was not found: {source_base}")
        base_dir = self.shot_root(identity) / "publish" / "layout" / "placements"
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)
        placements = read_json(source_dir / "placements.json", {}) or {}
        members = read_json(source_dir / "placement_members.json", {}) or {}
        placements_path = write_json(version_dir / "placements.json", placements)
        write_json(version_dir / "placement_members.json", members)
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": "layout",
                "subset": "placements",
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "version": version_label,
                "files": {"placements": "placements.json", "placement_members": "placement_members.json"},
                "source_sequence_publish": self._relative_to_project(source_dir),
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/placements.json"})
        self._update_versions(base_dir / "versions.json", version_label)
        return placements_path

    def publish_shot_anim_input(
        self,
        identity: ShotIdentity,
        *,
        cast_publish: Path,
        placements_publish: Path,
        shot_data: dict[str, Any],
        sequence_data: dict[str, Any],
        comment: str = "",
    ) -> Path:
        base_dir = self.shot_root(identity) / "publish" / "anim_input" / "main"
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)
        editorial = (shot_data.get("editorial") or {}) if shot_data else {}
        sequence_editorial = sequence_data.get("editorial") or {}
        camera_publish = self._latest_shot_camera_publish(identity)
        layout_overlay = self._latest_layout_overlay_usd(identity)
        cut_in = editorial.get("cut_in")
        cut_out = editorial.get("cut_out")
        handles = self._editorial_handles(editorial)
        work_range = self._anim_work_range(cut_in, cut_out, handles)
        anim_input = {
            "package_type": "anim_input",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "version": version_label,
            "fps": editorial.get("fps") or sequence_editorial.get("fps") or self.project_fps,
            "source_cut_range": [cut_in, cut_out],
            "work_range": work_range,
            "cut_range": self._anim_cut_range_in_work(work_range, cut_in, cut_out, handles),
            "handles": handles,
            "cast": self._relative_to_project(cast_publish),
            "placements": self._relative_to_project(placements_publish),
            "camera": self._relative_to_project(camera_publish) if camera_publish else "",
            "layout_overlay": self._relative_to_project(layout_overlay) if layout_overlay else "",
            "layout_overlay_usage": "reference_only",
            "editorial": self._relative_to_project(self.paths.project_root / "editorial" / "publish" / identity.episode / identity.sequence / "latest.json"),
            "comment": comment,
        }
        anim_input_path = write_json(version_dir / "anim_input.json", anim_input)
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": "anim_input",
                "subset": "main",
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "version": version_label,
                "files": {"anim_input": "anim_input.json"},
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/anim_input.json"})
        self._update_versions(base_dir / "versions.json", version_label)
        return anim_input_path

    def _status_from_file(self, name: str, path: Path, *, exists: bool | None = None, message: str = "") -> LayoutPublishStatusItem:
        ready = path.exists() if exists is None else exists and path.exists()
        return LayoutPublishStatusItem(
            name=name,
            state="READY" if ready else "MISSING",
            path=str(path) if path.exists() else "",
            message="" if ready else message or f"{name} was not found.",
        )

    def _status_from_latest(self, name: str, base_dir: Path) -> LayoutPublishStatusItem:
        latest_path = base_dir / "latest.json"
        latest = read_json(latest_path, {}) if latest_path.exists() else {}
        version = str((latest or {}).get("version") or "")
        target = base_dir / str((latest or {}).get("path") or "")
        ready = bool(version) and target.exists()
        return LayoutPublishStatusItem(
            name=name,
            state="READY" if ready else "MISSING",
            version=version,
            path=str(target if target.exists() else latest_path),
            message="" if ready else f"{name} latest publish was not found.",
        )

    def _status_from_camera_publishes(self, camera_root: Path) -> LayoutPublishStatusItem:
        latest_paths = list(camera_root.glob("*/*/latest.json")) if camera_root.exists() else []
        versions = []
        ready_count = 0
        for latest_path in latest_paths:
            latest = read_json(latest_path, {}) or {}
            version = str(latest.get("version") or "")
            target = latest_path.parent / str(latest.get("path") or "")
            if version and not target.exists():
                for fallback_name in ("camera.json", "publish.json", "camera.ma"):
                    fallback = latest_path.parent / version / fallback_name
                    if fallback.exists():
                        target = fallback
                        break
            if version:
                versions.append(f"{latest_path.parent.parent.name}/{latest_path.parent.name}:{version}")
            if target.exists():
                ready_count += 1
        ready = bool(latest_paths) and ready_count == len(latest_paths)
        return LayoutPublishStatusItem(
            name="camera",
            state="READY" if ready else "MISSING",
            version=", ".join(versions[:3]) + (" ..." if len(versions) > 3 else ""),
            path=str(camera_root),
            message="" if ready else "No camera publish latest.json was found.",
        )

    def _status_from_sequence_timing(self, identity: SequenceIdentity) -> LayoutPublishStatusItem:
        sequence_data = self.load_sequence(identity)
        editorial = sequence_data.get("editorial") or {}
        required = ["fps", "cut_in", "cut_out"]
        missing = [key for key in required if editorial.get(key) is None]
        return LayoutPublishStatusItem(
            name="timing",
            state="READY" if not missing else "MISSING",
            version=str(editorial.get("fps") or ""),
            path=str(self.sequence_workspace_root(identity.episode, identity.sequence) / "sequence.json"),
            message="" if not missing else "Missing editorial timing: " + ", ".join(missing),
        )

    def _status_from_shot_timing(
        self,
        identity: ShotIdentity,
        shot_data: dict[str, Any],
        sequence_data: dict[str, Any],
    ) -> LayoutPublishStatusItem:
        editorial = shot_data.get("editorial") or {}
        sequence_editorial = sequence_data.get("editorial") or {}
        fps = editorial.get("fps") or sequence_editorial.get("fps") or self.project_fps
        required = ["cut_in", "cut_out"]
        missing = [key for key in required if editorial.get(key) is None]
        return LayoutPublishStatusItem(
            name="timing",
            state="READY" if not missing else "MISSING",
            version=str(fps or ""),
            path=str(self.shot_root(identity) / "shot.json"),
            message="" if not missing else "Missing shot timing: " + ", ".join(missing),
        )

    def _sequence_shot_identities(self, identity: SequenceIdentity, sequence_data: dict[str, Any] | None = None) -> list[ShotIdentity]:
        sequence_data = sequence_data or self.load_sequence(identity)
        rows = []
        for row in sequence_data.get("shots") or []:
            shot_name = str(row.get("shot") or "").strip() if isinstance(row, dict) else ""
            if shot_name:
                rows.append(ShotIdentity(identity.episode, identity.sequence, shot_name))
        if not rows:
            rows = [
                shot for shot in self.list_shots()
                if shot.episode == identity.episode and shot.sequence == identity.sequence
            ]
        return sorted(rows, key=lambda shot: shot.shot)

    def _latest_shot_camera_publish(self, identity: ShotIdentity, camera_option: str = "main") -> Path | None:
        base_dir = self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "camera" / identity.shot / camera_option
        latest = read_json(base_dir / "latest.json", {}) or {}
        version = str(latest.get("version") or "")
        path = base_dir / str(latest.get("path") or "")
        if path.is_file():
            return path
        if path.is_dir():
            for fallback_name in ("camera.json", "publish.json", "camera.ma"):
                fallback = path / fallback_name
                if fallback.exists():
                    return fallback
        for fallback_name in ("camera.json", "publish.json", "camera.ma"):
            fallback = base_dir / version / fallback_name
            if fallback.exists():
                return fallback
        return None

    @staticmethod
    def _latest_publish_version_label(base_dir: Path) -> str:
        latest = read_json(base_dir / "latest.json", {}) or {}
        return str(latest.get("version") or "")

    def _latest_layout_overlay_usd(self, identity: ShotIdentity) -> Path | None:
        direct_candidates = [
            self.shot_root(identity) / "publish" / "usd" / "layout.usda",
            self.shot_root(identity) / "publish" / "usd" / "layout.usd",
            self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "usd" / identity.shot / "layout.usda",
            self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "usd" / identity.shot / "layout.usd",
        ]
        for candidate in direct_candidates:
            if candidate.exists():
                return candidate

        publish_dirs = [
            self.shot_root(identity) / "publish" / "layout" / "usd",
            self.shot_root(identity) / "publish" / "layout" / "proxy",
            self.shot_root(identity) / "publish" / "layout" / "main",
            self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "layout" / identity.shot / "usd",
            self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "layout" / identity.shot / "main",
            self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "layout" / identity.shot / "proxy",
        ]
        for publish_dir in publish_dirs:
            path = self._latest_publish_file(publish_dir, ("layout.usda", "layout.usd", "shot.usda", "shot.usd"))
            if path:
                return path
        return None

    @staticmethod
    def _latest_publish_file(base_dir: Path, filenames: tuple[str, ...]) -> Path | None:
        latest = read_json(base_dir / "latest.json", {}) or {}
        latest_path = base_dir / str(latest.get("path") or "")
        candidates = []
        if latest_path.name:
            candidates.append(latest_path)
            if latest_path.is_dir():
                candidates.extend(latest_path / name for name in filenames)
        version = str(latest.get("version") or "")
        if version:
            version_dir = base_dir / version
            candidates.append(version_dir)
            candidates.extend(version_dir / name for name in filenames)
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() in {".usd", ".usda"}:
                return candidate
        return None

    @staticmethod
    def _editorial_handles(editorial: dict[str, Any]) -> list[int]:
        handles = editorial.get("handles") if isinstance(editorial, dict) else None
        if isinstance(handles, dict):
            return [int(handles.get("head") or 0), int(handles.get("tail") or 0)]
        if isinstance(handles, list) and len(handles) >= 2:
            return [int(handles[0] or 0), int(handles[1] or 0)]
        return [0, 0]

    @staticmethod
    def _anim_work_range(cut_in: Any, cut_out: Any, handles: list[int]) -> list[int | None]:
        try:
            duration = int(cut_out) - int(cut_in) + 1
        except (TypeError, ValueError):
            return [None, None]
        total = max(1, duration + int(handles[0]) + int(handles[1]))
        return [1001, 1001 + total - 1]

    @staticmethod
    def _anim_cut_range_in_work(
        work_range: list[int | None],
        cut_in: Any,
        cut_out: Any,
        handles: list[int],
    ) -> list[int | None]:
        if work_range[0] is None:
            return [cut_in, cut_out]
        try:
            duration = int(cut_out) - int(cut_in) + 1
        except (TypeError, ValueError):
            return [None, None]
        cut_start = int(work_range[0]) + int(handles[0])
        return [cut_start, cut_start + duration - 1]

    def _next_publish_version(self, base_dir: Path) -> str:
        versions = [
            parse_version(path.name)
            for path in base_dir.glob("v*")
            if path.is_dir() and parse_version(path.name) is not None
        ]
        return format_version(next_version([version for version in versions if version]))

    def _update_versions(self, path: Path, version_label: str) -> None:
        versions = read_json(path, []) if path.exists() else []
        if not isinstance(versions, list):
            versions = []
        next_versions = []
        seen = False
        for item in versions:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            if item.get("version") == version_label:
                item["status"] = "latest"
                seen = True
            elif item.get("status") == "latest":
                item["status"] = "available"
            next_versions.append(item)
        if not seen:
            next_versions.append({"version": version_label, "status": "latest"})
        write_json(path, next_versions)

    def _relative_to_project(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.paths.project_root.resolve()).as_posix()
        except Exception:
            return path.as_posix()

    def _build_preview_from_cast(self, cast_data: dict[str, Any]) -> list[BuildPreviewItem]:
        cast = cast_data.get("cast") or {}
        review_layers = cast_data.get("review_layers") or {}
        member_to_layer = {}
        for layer_name, layer in review_layers.items():
            for member in layer.get("members", []):
                member_to_layer[member] = layer_name

        items: list[BuildPreviewItem] = []
        for cast_key, entry in sorted(cast.items()):
            asset_name = str(entry.get("asset") or "")
            variant = str(entry.get("variant") or "default")
            asset_publish = str(entry.get("asset_publish") or "approved")
            required = bool(entry.get("required", True))
            asset_root = self.find_asset_root(asset_name)
            if not asset_root:
                items.append(
                    BuildPreviewItem(
                        cast_key=cast_key,
                        asset=asset_name,
                        variant=variant,
                        namespace=str(entry.get("namespace") or cast_key),
                        role=str(entry.get("role") or ""),
                        review_layer=member_to_layer.get(cast_key, ""),
                        asset_publish=asset_publish,
                        required=required,
                        status="missing" if required else "optional missing",
                        message="Asset folder was not found.",
                    )
                )
                continue

            variant_root = asset_root / variant
            if not variant_root.exists():
                items.append(
                    BuildPreviewItem(
                        cast_key=cast_key,
                        asset=asset_name,
                        variant=variant,
                        namespace=str(entry.get("namespace") or cast_key),
                        role=str(entry.get("role") or ""),
                        review_layer=member_to_layer.get(cast_key, ""),
                        asset_publish=asset_publish,
                        required=required,
                        status="missing" if required else "optional missing",
                        asset_root=str(asset_root),
                        message="Asset variant folder was not found.",
                    )
                )
                continue

            publish_path = self.resolve_asset_context_work_publish(variant_root, asset_publish)
            if not publish_path:
                publish_path = self.resolve_asset_publish(variant_root, asset_publish)
            status = "resolved" if publish_path else ("missing" if required else "optional missing")
            items.append(
                BuildPreviewItem(
                    cast_key=cast_key,
                    asset=asset_name,
                    variant=variant,
                    namespace=str(entry.get("namespace") or cast_key),
                    role=str(entry.get("role") or ""),
                    review_layer=member_to_layer.get(cast_key, ""),
                    asset_publish=asset_publish,
                    required=required,
                    status=status,
                    asset_root=str(asset_root),
                    variant_root=str(variant_root),
                    publish_path=str(publish_path) if publish_path else "",
                    message="" if publish_path else "Publish was not found.",
                )
            )
        return items

    def find_asset_root(self, asset_name: str) -> Path | None:
        assets_root = self.paths.assets_root()
        if not asset_name or not assets_root.exists():
            return None
        matches = sorted(assets_root.glob(f"*/*/{asset_name}"))
        return matches[0] if matches else None

    def resolve_asset_publish(self, variant_root: Path, asset_publish: str) -> Path | None:
        publish_root = variant_root / "publish"
        if not publish_root.exists():
            return None
        if _is_version_label(asset_publish):
            candidates = sorted(publish_root.glob(f"*/*/{asset_publish}/*"))
            files = _maya_scene_publish_paths(candidates)
            return _preferred_publish(files)
        if asset_publish == "approved":
            approved = self._approved_publish_paths(publish_root)
            if approved:
                return _preferred_publish(approved)
        latest = self._latest_publish_paths(publish_root)
        return _preferred_publish(latest)

    def resolve_asset_context_work_publish(self, variant_root: Path, asset_publish: str) -> Path | None:
        context_root = variant_root / "publish" / "asset" / "asset_work"
        if not context_root.exists():
            return None
        if _is_version_label(asset_publish):
            return _preferred_context_scene(context_root / asset_publish)
        if asset_publish == "approved":
            versions = read_json(context_root / "versions.json", [])
            candidates = [
                _preferred_context_scene(context_root / str(row.get("version") or ""))
                for row in versions if isinstance(row, dict)
                and row.get("status") in {"approved", "latest"}
                and row.get("version")
            ]
            candidates = [path for path in candidates if path]
            if candidates:
                return sorted(candidates, key=lambda path: path.as_posix().lower())[-1]
        latest = read_json(context_root / "latest.json", {})
        latest_path = context_root / str((latest or {}).get("path") or "")
        if _is_maya_scene_publish(latest_path):
            return latest_path
        if latest.get("version"):
            return _preferred_context_scene(context_root / str(latest["version"]))
        return None

    def _latest_publish_paths(self, publish_root: Path) -> list[Path]:
        paths = []
        for latest_json in publish_root.glob("*/*/latest.json"):
            latest = read_json(latest_json, {})
            if latest.get("path"):
                path = latest_json.parent / latest["path"]
                if path.exists() and _is_maya_scene_publish(path):
                    paths.append(path)
        return paths

    def _approved_publish_paths(self, publish_root: Path) -> list[Path]:
        paths = []
        for versions_json in publish_root.glob("*/*/versions.json"):
            versions = read_json(versions_json, [])
            approved_versions = [
                item.get("version")
                for item in versions
                if item.get("status") in {"approved", "latest"} and item.get("version")
            ]
            for version in approved_versions:
                version_dir = versions_json.parent / version
                if version_dir.exists():
                    paths.extend(_maya_scene_publish_paths(version_dir.iterdir()))
        return paths

    def planned_shot_paths(self, request: ShotCreateRequest) -> list[Path]:
        identity = request.identity
        shot_root = self.shot_root(identity)
        paths = [
            self.paths.sequence_root(identity.episode, identity.sequence),
            shot_root,
            shot_root / "data",
            shot_root / "publish",
        ]
        if request.create_work_dirs:
            paths.extend(self.shot_work_dir(identity, department, "main", tool_name="maya") for department in self.shot_departments)
        return paths

    def create_shot(self, request: ShotCreateRequest) -> Path:
        request = ShotCreateRequest(
            episode=request.episode,
            sequence=request.sequence,
            shot=request.shot,
            fps=self.project_fps,
            cut_in=request.cut_in,
            cut_out=request.cut_out,
            status=request.status,
            create_work_dirs=request.create_work_dirs,
        )
        for path in self.planned_shot_paths(request):
            path.mkdir(parents=True, exist_ok=True)
        sequence_path = self.paths.sequence_root(request.episode, request.sequence) / "sequence.json"
        if not sequence_path.exists():
            write_json(sequence_path, {"episode": request.episode, "sequence": request.sequence})
        shot_root = self.shot_root(request.identity)
        self.write_shot_json(request)
        self.ensure_cast_json(request.identity)
        return shot_root

    def ensure_sequence_all_shot(self, episode: str, sequence: str) -> Path:
        identity = ShotIdentity(episode, sequence, "all")
        shot_root = self.shot_root(identity)
        if (shot_root / "shot.json").exists():
            return shot_root
        request = ShotCreateRequest(
            episode=episode,
            sequence=sequence,
            shot="all",
            fps=self.project_fps,
            cut_in=0,
            cut_out=0,
            status="sequence",
        )
        for path in self.planned_shot_paths(request):
            path.mkdir(parents=True, exist_ok=True)
        self.write_shot_json(request)
        self.ensure_cast_json(identity)
        return shot_root

    def update_sequence_all_shot(self, episode: str, sequence: str) -> Path:
        identity = ShotIdentity(episode, sequence, "all")
        shot_root = self.ensure_sequence_all_shot(episode, sequence)
        ranges = []
        sequence_root = self.paths.sequence_root(episode, sequence)
        for shot_json in sequence_root.glob("*/shot.json"):
            shot_name = shot_json.parent.name
            if shot_name == "all":
                continue
            data = read_json(shot_json, {}) or {}
            editorial = data.get("editorial") or {}
            try:
                cut_in = int(editorial.get("cut_in"))
                cut_out = int(editorial.get("cut_out"))
            except (TypeError, ValueError):
                continue
            if cut_out >= cut_in:
                ranges.append((cut_in, cut_out))
        cut_in = min((item[0] for item in ranges), default=0)
        cut_out = max((item[1] for item in ranges), default=0)
        status = (read_json(shot_root / "shot.json", {}) or {}).get("status", "sequence")
        request = ShotCreateRequest(
            episode=episode,
            sequence=sequence,
            shot="all",
            fps=self.project_fps,
            cut_in=cut_in,
            cut_out=cut_out,
            status=str(status or "sequence"),
        )
        return self.write_shot_json(request)

    def write_shot_json(self, request: ShotCreateRequest) -> Path:
        duration = max(0, request.cut_out - request.cut_in + 1)
        data = {
            "episode": request.episode,
            "sequence": request.sequence,
            "shot": request.shot,
            "status": request.status,
            "editorial": {
                "fps": request.fps,
                "cut_in": request.cut_in,
                "cut_out": request.cut_out,
                "duration": duration,
                "handles": {"head": 0, "tail": 0},
            },
        }
        return write_json(self.shot_root(request.identity) / "shot.json", data)

    def ensure_cast_json(self, identity: ShotIdentity) -> Path:
        path = self.shot_root(identity) / "cast.json"
        if path.exists():
            return path
        return write_json(path, {"cast": {}, "review_layers": DEFAULT_REVIEW_LAYERS})

    def load_cast(self, identity: ShotIdentity) -> dict[str, Any]:
        return read_json(self.shot_root(identity) / "cast.json", {"cast": {}, "review_layers": {}})

    def write_cast(self, identity: ShotIdentity, cast_data: dict[str, Any]) -> Path:
        issues = validate_cast_data(cast_data)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            messages = ", ".join(issue.message for issue in errors)
            raise ValueError(f"Invalid cast data: {messages}")
        return write_json(self.shot_root(identity) / "cast.json", cast_data)

    def sequence_cast_path(self, episode: str, sequence: str) -> Path:
        return self.sequence_workspace_root(episode, sequence) / "cast.json"

    def load_sequence_cast(self, episode: str, sequence: str) -> dict[str, Any]:
        return read_json(self.sequence_cast_path(episode, sequence), {"cast": {}, "review_layers": DEFAULT_REVIEW_LAYERS})

    def write_sequence_cast(self, episode: str, sequence: str, cast_data: dict[str, Any]) -> Path:
        root = self.sequence_workspace_root(episode, sequence)
        root.mkdir(parents=True, exist_ok=True)
        return write_json(
            root / "cast.json",
            {
                "cast": cast_data.get("cast") or {},
                "review_layers": _defaulted_review_layers(cast_data.get("review_layers")),
            },
        )

    def review_layers(self, identity: ShotIdentity) -> dict[str, dict[str, Any]]:
        return _defaulted_review_layers(self.load_cast(identity).get("review_layers"))

    def write_review_layers(self, identity: ShotIdentity, review_layers: dict[str, Any]) -> Path:
        cast_data = self.load_cast(identity)
        cast_data["review_layers"] = _defaulted_review_layers(review_layers)
        return self.write_cast(identity, cast_data)

    def review_layer_rows(self, identity: ShotIdentity) -> list[dict[str, Any]]:
        rows = []
        for layer_name, layer in self.review_layers(identity).items():
            camera = layer.get("camera") or {}
            resolution = layer.get("resolution") or {}
            ae = layer.get("ae") or {}
            rows.append(
                {
                    "layer": layer_name,
                    "members": ", ".join(layer.get("members") or []),
                    "camera": camera.get("name", ""),
                    "camera_publish": camera.get("version", ""),
                    "publish_type": camera.get("publish_type", "camera"),
                    "width": resolution.get("width", ""),
                    "height": resolution.get("height", ""),
                    "scale": resolution.get("scale", ""),
                    "ae_slot": ae.get("template_slot", ""),
                    "comp_name": ae.get("comp_name", layer_name),
                    "order": layer.get("order", 0),
                    "three_d_layer": bool(layer.get("three_d_layer", False)),
                    "frame_range": layer.get("frame_range", "Animation"),
                    "take": layer.get("take", 1),
                    "outputs": ", ".join(layer.get("outputs") or []),
                }
            )
        return sorted(rows, key=lambda item: (int(item.get("order") or 0), str(item.get("layer"))))

    def plan_review_publish(
        self,
        identity: ShotIdentity,
        department: str,
        *,
        version: int | None = None,
        take: int | None = None,
        source_workfile: str = "",
        comment: str = "",
        write: bool = True,
    ):
        from smartlib.review.package import build_review_package_plan, write_review_package_plan

        plan = build_review_package_plan(
            self.shot_root(identity),
            self.load_shot(identity),
            self.load_cast(identity),
            department,
            version=version,
            take=take,
            source_workfile=source_workfile,
            comment=comment,
            project_root=self.paths.project_root,
            pipeline_root=_pipeline_root(),
        )
        return write_review_package_plan(plan) if write else plan

    def plan_review_playblast_take(
        self,
        identity: ShotIdentity,
        department: str,
        *,
        source_workfile: str = "",
        comment: str = "",
        write: bool = True,
    ):
        from smartlib.review.package import (
            build_review_package_plan,
            latest_review_version,
            next_review_take,
            write_review_package_plan,
        )

        shot_root = self.shot_root(identity)
        version = latest_review_version(shot_root, department) or 1
        version_label = f"v{version:03d}"
        take = next_review_take(shot_root / "publish" / "review" / department / version_label)
        plan = build_review_package_plan(
            shot_root,
            self.load_shot(identity),
            self.load_cast(identity),
            department,
            version=version,
            take=take,
            source_workfile=source_workfile,
            comment=comment,
            project_root=self.paths.project_root,
            pipeline_root=_pipeline_root(),
        )
        return write_review_package_plan(plan) if write else plan

    def cast_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return self.build_cast_data(rows)

    def import_cast_csv(self, identity: ShotIdentity, path: str | Path) -> Path:
        rows = self.read_cast_csv(path, identity=identity)
        cast_data = self.build_cast_data(rows, existing=self.load_cast(identity))
        return self.write_cast(identity, cast_data)

    def import_cast_spreadsheet(self, identity: ShotIdentity, sheet_id: str | None = None) -> Path:
        self.sync_cast_spreadsheet_cache(sheet_id=sheet_id)
        return self.import_cast_cache(identity)

    def import_cast_cache(self, identity: ShotIdentity) -> Path:
        rows = read_json(self.cast_cache_path, [])
        if not isinstance(rows, list):
            raise RuntimeError(f"Cast cache is not a list: {self.cast_cache_path}")
        rows = [row for row in rows if isinstance(row, dict) and _row_matches_identity(row, identity)]
        cast_data = self.build_cast_data(rows, existing=self.load_cast(identity))
        return self.write_cast(identity, cast_data)

    def sync_cast_spreadsheet_cache(self, sheet_id: str | None = None) -> Path:
        rows = self.read_cast_spreadsheet(sheet_id=sheet_id)
        return write_json(self.cast_cache_path, rows)

    def export_cast_csv(self, identity: ShotIdentity, path: str | Path) -> Path:
        cast_data = self.load_cast(identity)
        rows = self.cast_data_to_rows(identity, cast_data)
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CAST_CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return output

    def read_cast_csv(self, path: str | Path, identity: ShotIdentity | None = None) -> list[dict[str, Any]]:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if identity is None:
            return rows
        return [
            row for row in rows
            if _row_matches_identity(row, identity)
        ]

    def read_cast_spreadsheet(self, sheet_id: str | None = None) -> list[dict[str, Any]]:
        sheet_id = sheet_id or self.cast_sheet_id
        if not sheet_id:
            raise RuntimeError("google_sheets.cast_list_id is not set in templates_base.yml")
        path = self.credentials_path()
        if not path or not path.exists():
            raise RuntimeError("Credentials file was not found. Set CREDENTIALS_PATH, CREDENTIALS_DIR, or %APPDATA%/credentials.json.")
        try:
            import gspread
        except ImportError as exc:
            raise RuntimeError(f"gspread is not installed for this Python: {exc}") from exc
        gc = gspread.service_account(filename=str(path))
        return gc.open_by_key(sheet_id).sheet1.get_all_records()

    @property
    def cast_cache_path(self) -> Path:
        return self.project_config.config_dir / ".cache" / "cast_list.json"

    @property
    def cast_sheet_id(self) -> str:
        google_sheets = self.project_config.base.get("google_sheets") or {}
        if isinstance(google_sheets, dict):
            return str(google_sheets.get("cast_list_id", "")).strip()
        return ""

    @staticmethod
    def credentials_path() -> Path | None:
        return credentials_path()

    def cast_data_to_rows(self, identity: ShotIdentity, cast_data: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        cast = cast_data.get("cast") or {}
        for cast_key, entry in sorted(cast.items()):
            rows.append(
                {
                    "episode": identity.episode,
                    "sequence": identity.sequence,
                    "shot": identity.shot,
                    "cast_key": cast_key,
                    "asset": entry.get("asset", ""),
                    "variant": entry.get("variant", "default"),
                    "role": entry.get("role", ""),
                    "namespace": entry.get("namespace", ""),
                    "asset_publish": entry.get("asset_publish", "approved"),
                    "required": "TRUE" if entry.get("required", True) else "FALSE",
                    "note": entry.get("note", ""),
                }
            )
        return rows

    def build_cast_data(
        self,
        rows: list[dict[str, Any]],
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cast: dict[str, Any] = {}
        review_layers: dict[str, dict[str, Any]] = _defaulted_review_layers((existing or {}).get("review_layers"))
        for layer in review_layers.values():
            layer["members"] = []
        for row in rows:
            cast_key = str(row.get("cast_key") or row.get("Cast Key") or "").strip()
            if not cast_key:
                continue
            role = _normalize_role(row.get("role") or row.get("Role") or "CHA")
            entry = CastEntry(
                asset=str(row.get("asset") or row.get("Asset") or "").strip(),
                variant=str(row.get("variant") or row.get("Variant") or "default").strip() or "default",
                role=role,
                namespace=str(row.get("namespace") or row.get("Namespace") or cast_key).strip(),
                asset_publish=str(row.get("asset_publish") or row.get("Asset Publish") or "approved").strip(),
                note=str(row.get("note") or row.get("Note") or "").strip(),
                required=_parse_bool(row.get("required", row.get("Required", True))),
            )
            cast[cast_key] = asdict(entry)
            layer = review_layers.setdefault(role, {"members": [], "order": len(review_layers) * 10})
            layer["members"].append(cast_key)
        return {"cast": cast, "review_layers": review_layers or deepcopy(DEFAULT_REVIEW_LAYERS)}

    def selected_asset_for_cast(self, existing_cast: dict[str, Any] | None = None) -> dict[str, Any]:
        selected = read_selected_asset(self.project_config)
        if not selected.get("asset"):
            return {}
        return self.asset_selection_cast_row(selected, existing_cast=existing_cast)

    def asset_selection_cast_row(
        self,
        selected: dict[str, Any],
        existing_cast: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not selected.get("asset"):
            return {}
        role = _role_from_asset_selection(selected)
        cast_key = _unique_cast_key(existing_cast or {}, selected.get("asset"))
        return {
            "cast_key": cast_key,
            "asset": selected.get("asset", ""),
            "variant": selected.get("variant", "default") or "default",
            "role": role,
            "namespace": cast_key,
            "asset_publish": "approved",
            "required": True,
            "note": f"from Asset Manager: {selected.get('category', '')}/{selected.get('group', '')}",
        }

    def add_asset_selections_to_cast(
        self,
        identity: ShotIdentity,
        selections: list[dict[str, Any]],
    ) -> tuple[Path, list[dict[str, Any]]]:
        cast_data = self.load_cast(identity)
        cast_rows = self.cast_data_to_rows(identity, cast_data)
        reserved_cast = dict(cast_data.get("cast") or {})
        added_rows: list[dict[str, Any]] = []
        for selected in selections:
            row = self.asset_selection_cast_row(selected, existing_cast=reserved_cast)
            if not row:
                continue
            cast_rows.append(
                {
                    "episode": identity.episode,
                    "sequence": identity.sequence,
                    "shot": identity.shot,
                    **row,
                }
            )
            reserved_cast[row["cast_key"]] = {}
            added_rows.append(row)
        if not added_rows:
            raise ValueError("No valid asset selections were provided.")
        updated = self.build_cast_data(cast_rows, existing=cast_data)
        return self.write_cast(identity, updated), added_rows

    def add_asset_selections_to_sequence_cast(
        self,
        episode: str,
        sequence: str,
        selections: list[dict[str, Any]],
    ) -> tuple[Path, list[dict[str, Any]]]:
        cast_data = self.load_sequence_cast(episode, sequence)
        cast = dict(cast_data.get("cast") or {})
        review_layers = _defaulted_review_layers(cast_data.get("review_layers"))
        added_rows: list[dict[str, Any]] = []
        for selected in selections:
            row = self.asset_selection_cast_row(selected, existing_cast=cast)
            if not row:
                continue
            cast[row["cast_key"]] = {
                "asset": row["asset"],
                "variant": row["variant"],
                "role": row["role"],
                "namespace": row["namespace"],
                "asset_publish": row["asset_publish"],
                "required": row["required"],
                "note": row["note"],
            }
            layer = review_layers.setdefault(row["role"], {"members": [], "order": len(review_layers) * 10})
            members = list(layer.get("members") or [])
            if row["cast_key"] not in members:
                members.append(row["cast_key"])
            layer["members"] = members
            added_rows.append(row)
        if not added_rows:
            raise ValueError("No valid asset selections were provided.")
        return self.write_sequence_cast(episode, sequence, {"cast": cast, "review_layers": review_layers}), added_rows


def validate_cast_data(cast_data: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    cast = cast_data.get("cast") or {}
    review_layers = _defaulted_review_layers(cast_data.get("review_layers"))
    namespaces: dict[str, str] = {}

    for cast_key, entry in cast.items():
        namespace = str(entry.get("namespace") or "")
        role = _normalize_role(entry.get("role") or "")
        asset_publish = str(entry.get("asset_publish") or "")
        if namespace in namespaces:
            issues.append(ValidationIssue("namespace_duplicate", f"namespace is duplicated: {namespace}", "error"))
        namespaces[namespace] = cast_key
        if role and role not in review_layers:
            issues.append(ValidationIssue("missing_review_layer", f"role has no review layer: {role}", "error"))
        if asset_publish not in VALID_ASSET_PUBLISH and not _is_version_label(asset_publish):
            issues.append(ValidationIssue("invalid_asset_publish", f"asset_publish is invalid: {asset_publish}", "error"))

    for layer_name, layer in review_layers.items():
        for member in layer.get("members", []):
            if member not in cast:
                issues.append(ValidationIssue("missing_cast_member", f"{layer_name} member is missing from cast: {member}", "error"))
        resolution = layer.get("resolution") or {}
        for key in ("width", "height"):
            if key in resolution and int(resolution.get(key) or 0) <= 0:
                issues.append(ValidationIssue("invalid_resolution", f"{layer_name}.{key} must be positive", "error"))

    return issues


SHOT_WORK_RE = re.compile(
    r"^(?P<shot>.+?)_(?P<department>[^_]+)_v(?P<version>\d+)_(?P<take>\d+)\.(?P<ext>[^.]+)$"
)

WORK_OPTION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]*$")


def _normalize_work_option(option: Any) -> str:
    value = str(option or "main").strip()
    if not value:
        value = "main"
    if not WORK_OPTION_RE.match(value):
        raise ValueError("Shot work option may contain letters, numbers, underscores, and hyphens.")
    return value


def _normalize_tool_name(tool_name: Any) -> str:
    value = str(tool_name or "maya").strip().lower()
    aliases = {"houdini": "houdini", "hython": "houdini", "maya": "maya", "mayapy": "maya"}
    value = aliases.get(value, value)
    if not WORK_OPTION_RE.match(value):
        raise ValueError("Tool name may contain letters, numbers, underscores, and hyphens.")
    return value


def _clean_publish_token(value: Any) -> str:
    text = str(value or "main").strip()
    clean = re.sub(r"[^0-9A-Za-z_-]+", "_", text).strip("_")
    return clean or "main"


def parse_shot_work_file(filename: str) -> dict[str, Any] | None:
    match = SHOT_WORK_RE.match(filename)
    if not match:
        return None
    data = match.groupdict()
    data["version"] = int(data["version"])
    data["take"] = int(data["take"])
    return data


def _defaulted_review_layers(review_layers: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    merged = deepcopy(DEFAULT_REVIEW_LAYERS)
    for name, layer in (review_layers or {}).items():
        normalized_name = _normalize_role(name)
        incoming = dict(layer or {})
        if normalized_name in merged:
            existing_members = list(merged[normalized_name].get("members") or [])
            incoming_members = list(incoming.get("members") or [])
            incoming["members"] = existing_members + [member for member in incoming_members if member not in existing_members]
        merged[normalized_name] = incoming
        merged[normalized_name].setdefault("members", [])
        merged[normalized_name].setdefault("order", DEFAULT_REVIEW_LAYERS.get(normalized_name, {}).get("order", len(merged) * 10))
    return merged


def _normalize_role(value: Any) -> str:
    role = str(value or "").strip().upper() or "CHA"
    return ROLE_ALIASES.get(role, role)


def _role_from_asset_selection(selected: dict[str, Any]) -> str:
    text = " ".join(
        str(selected.get(key, ""))
        for key in ("category", "group", "asset_type", "type")
    ).lower()
    if any(token in text for token in ("env", "environment", "bg", "background", "set")):
        return "BGA"
    if "fx" in text:
        return "FX"
    return "CHA"


def _unique_cast_key(existing_cast: dict[str, Any], asset_name: Any) -> str:
    base = re.sub(r"[^0-9A-Za-z_]+", "_", str(asset_name or "asset")).strip("_") or "asset"
    candidate = f"{base}_main"
    if candidate not in existing_cast:
        return candidate
    index = 2
    while f"{base}_{index:02d}" in existing_cast:
        index += 1
    return f"{base}_{index:02d}"


def _is_version_label(value: str) -> bool:
    return len(value) >= 4 and value.lower().startswith("v") and value[1:].isdigit()


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _row_matches_identity(row: dict[str, Any], identity: ShotIdentity) -> bool:
    episode = str(row.get("episode") or row.get("Episode") or "").strip()
    sequence = str(row.get("sequence") or row.get("Sequence") or "").strip()
    shot = str(row.get("shot") or row.get("Shot") or "").strip()
    if not episode and not sequence and not shot:
        return True
    return episode == identity.episode and sequence == identity.sequence and shot == identity.shot


def _preferred_publish(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    priority = [
        "/rig/anim/",
        "/rig/layout/",
        "/asset/",
        "/model/render/",
        "/model/hires/",
        "/model/proxy/",
        "/model/guide/",
    ]
    normalized = [(path.as_posix().lower(), path) for path in paths]
    for marker in priority:
        matches = [path for text, path in normalized if marker in text]
        if matches:
            return sorted(matches, key=lambda path: path.as_posix().lower())[-1]
    return sorted(paths, key=lambda path: path.as_posix().lower())[-1]


def _maya_scene_publish_paths(paths) -> list[Path]:
    return [path for path in paths if _is_maya_scene_publish(path)]


def _preferred_context_scene(version_dir: Path) -> Path | None:
    for filename in ("asset.ma", "asset.mb"):
        path = version_dir / filename
        if _is_maya_scene_publish(path):
            return path
    record = read_json(version_dir / "publish.json", {})
    for key in ("ma", "mb"):
        filename = str((record.get("files") or {}).get(key) or "")
        path = version_dir / Path(filename).name
        if _is_maya_scene_publish(path):
            return path
    return None


def _is_maya_scene_publish(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in {".ma", ".mb"}


def _pipeline_root() -> Path:
    return Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or Path(__file__).resolve().parents[4]
    )
