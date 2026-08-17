from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig, load_config
from smartlib.core.folder_structure import copy_entity_folder_structure, folder_structure_source
from smartlib.core.output_resolver import OutputPathResolver
from smartlib.core.asset_publish_resolver import AssetPublishResolver
from smartlib.core.asset_load_policy import resolve_asset_load_policy
from smartlib.core.credentials import credentials_path
from smartlib.core.metadata import read_json, sidecar_path, write_json
from smartlib.core.path_resolver import ProjectPaths
from smartlib.core.selection_context import read_selected_asset
from smartlib.core.validation import ValidationIssue
from smartlib.core.versioning import format_version, next_version, parse_version


DEFAULT_SHOT_DEPARTMENTS = ["layout", "anim", "fx", "lighting", "comp"]
LEGACY_DEFAULT_REVIEW_LAYERS = {
    "CHA": {"members": [], "order": 20},
    "CHB": {"members": [], "order": 10},
    "BGA": {"members": [], "order": 0},
    "FX": {"members": [], "order": 30},
    "ENV": {"members": [], "order": -10},
}
DEFAULT_REVIEW_LAYERS: dict[str, dict[str, Any]] = {}
ROLE_ALIASES = {
    "BG": "BGA",
    "BACKGROUND": "BGA",
    "BACK": "BGA",
    "SET": "BGA",
    "ENVIRONMENT": "BGA",
}
VALID_ASSET_PUBLISH = {"approved", "latest"}
DEPENDENCY_TYPES = {"mocap", "virtual_camera", "audio", "reference"}
DEPENDENCY_STATUSES = {"selected", "alternate"}
CONSTRUCT_TYPES = {
    "rig", "camera", "animation", "animation_curve", "fx", "light", "audio",
    "cast", "placement", "layout_overlay", "set_dress", "preview_render", "playblast_settings", "usd",
}
CONSTRUCT_MODES = {"reference", "import", "apply", "reference_cache", "file", "payload"}
FX_CACHE_EXTENSIONS = {".abc", ".usd", ".usda", ".usdc"}
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


def _project_templates(project_config: ProjectConfig) -> dict[str, str]:
    templates = getattr(project_config, "templates", None)
    if isinstance(templates, dict):
        return {str(key): str(value) for key, value in templates.items()}

    merged: dict[str, str] = {}
    config_dir = Path(getattr(project_config, "config_dir", ""))
    for filename in ("templates_base.yml", "templates_assets.yml", "templates_shots.yml"):
        if hasattr(project_config, "load"):
            data = project_config.load(filename)
        else:
            data = load_config(config_dir / filename)
        values = data.get("templates") or {}
        if isinstance(values, dict):
            merged.update({str(key): str(value) for key, value in values.items()})
    return merged


def _project_name(project_config: ProjectConfig) -> str:
    name = getattr(project_config, "project_name", "")
    if name:
        return str(name)
    base = getattr(project_config, "base", {}) or {}
    config_dir = Path(getattr(project_config, "config_dir", ""))
    return str((base.get("anchors") or {}).get("project_name") or config_dir.name)


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
class ShotDependency:
    id: str
    type: str
    role: str
    source: str
    representation: str = ""
    status: str = "alternate"
    asset: str = ""
    target: str = "Shot"


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
    task: str = "main"


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
    placements_publish: Path | None
    anim_input: Path


@dataclass(frozen=True)
class ConstructComponent:
    component_type: str
    name: str
    version: str = "latest"
    mode: str = "reference"
    namespace: str = ""
    path: str = ""
    required: bool = True
    enabled: bool = True
    note: str = ""
    source: dict[str, Any] = field(default_factory=dict)


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
        self.paths = ProjectPaths(
            project_root,
            templates=_project_templates(project_config),
            project_name=_project_name(project_config),
        )
        self.asset_publish_resolver = AssetPublishResolver(project_config)
        self.output_resolver = OutputPathResolver(project_config)

    @property
    def shot_departments(self) -> list[str]:
        departments = self.project_config.base.get("shot_depts") or []
        return list(departments) if departments else list(DEFAULT_SHOT_DEPARTMENTS)

    def shot_tasks(self, department: str) -> list[str]:
        configured = self.project_config.base.get("shot_tasks") or {}
        tasks = configured.get(str(department)) if isinstance(configured, dict) else None
        if not isinstance(tasks, list):
            return ["main"]
        normalized = ["main"]
        for task in tasks:
            value = _normalize_work_option(task)
            if value not in normalized:
                normalized.append(value)
        return normalized

    @property
    def project_fps(self) -> int:
        fps = (self.project_config.base.get("anchors") or {}).get("fps", 24)
        try:
            return int(fps)
        except (TypeError, ValueError):
            return 24

    def shot_root(self, identity: ShotIdentity) -> Path:
        return self.paths.shot_root(identity.episode, identity.sequence, identity.shot)

    def shot_data_root(self, identity: ShotIdentity) -> Path:
        resolver = getattr(self.paths, "shot_data_root", None)
        return resolver(identity.episode, identity.sequence, identity.shot) if resolver else self.shot_root(identity) / "data"

    def shot_publish_root(self, identity: ShotIdentity) -> Path:
        resolver = getattr(self.paths, "shot_publish_root", None)
        return resolver(identity.episode, identity.sequence, identity.shot) if resolver else self.shot_root(identity) / "publish"

    def shot_output_root(self, identity: ShotIdentity) -> Path:
        resolver = getattr(self.paths, "shot_output_root", None)
        return resolver(identity.episode, identity.sequence, identity.shot) if resolver else self.shot_root(identity) / "output"

    def shot_render_root(self, identity: ShotIdentity) -> Path:
        resolver = getattr(self.paths, "shot_render_root", None)
        return resolver(identity.episode, identity.sequence, identity.shot) if resolver else self.shot_root(identity) / "render"

    def sequence_workspace_root(self, episode: str, sequence: str) -> Path:
        return self.paths.sequence_workspace_root(episode, sequence)

    def sequence_build_root(self, identity: SequenceIdentity) -> Path:
        return self.paths.sequence_build_root(identity.episode, identity.sequence)

    def sequence_build_dir(
        self, identity: SequenceIdentity, department: str, dcc: str, task: str, version: str
    ) -> Path:
        return self.paths.sequence_build_dir(
            identity.episode, identity.sequence, department, dcc, task, version
        )

    def legacy_sequence_build_root(self, identity: SequenceIdentity) -> Path:
        return self.sequence_workspace_root(identity.episode, identity.sequence) / "output" / "scene_build"

    def shot_build_root(self, identity: ShotIdentity) -> Path:
        return self.paths.shot_build_root(identity.episode, identity.sequence, identity.shot)

    def shot_build_dir(
        self, identity: ShotIdentity, department: str, dcc: str, task: str, version: str
    ) -> Path:
        return self.paths.shot_build_dir(
            identity.episode, identity.sequence, identity.shot,
            department, dcc, task, version,
        )

    def legacy_shot_build_root(self, identity: ShotIdentity) -> Path:
        return self.shot_output_root(identity) / "scene_build"

    def list_shots(self) -> list[ShotIdentity]:
        shots_root = self.paths.shots_root()
        if not shots_root.exists():
            return []
        shots: dict[tuple[str, str, str], ShotIdentity] = {}
        for shot_json in shots_root.glob("**/shot.json"):
            shot_root = shot_json.parent
            data = read_json(shot_json, {}) or {}
            episode = str(data.get("episode") or "").strip()
            sequence = str(data.get("sequence") or data.get("seq") or "").strip()
            shot = str(data.get("shot") or "").strip()
            if episode and sequence and shot:
                identity = ShotIdentity(episode=episode, sequence=sequence, shot=shot)
                shots[(episode, sequence, shot)] = identity
                continue
            try:
                sequence_root = shot_root.parent
                episode_root = sequence_root.parent
                identity = ShotIdentity(
                    episode=episode_root.name,
                    sequence=sequence_root.name,
                    shot=shot_root.name,
                )
                shots[(identity.episode, identity.sequence, identity.shot)] = identity
            except Exception:
                continue
        return sorted(
            shots.values(),
            key=lambda item: (item.episode.lower(), item.sequence.lower(), item.shot.lower()),
        )

    def shot_identity_from_path(
        self,
        path: str | Path,
    ) -> ShotIdentity | None:
        """Resolve the owning shot from a scene/work path."""
        if not path:
            return None
        candidate = os.path.normcase(os.path.abspath(str(path)))
        matches: list[tuple[int, ShotIdentity]] = []
        for identity in self.list_shots():
            root = os.path.normcase(os.path.abspath(str(self.shot_root(identity))))
            try:
                common = os.path.commonpath([candidate, root])
            except ValueError:
                continue
            if common == root:
                matches.append((len(root), identity))
        return max(matches, default=(0, None), key=lambda item: item[0])[1]

    def list_sequences(self) -> list[SequenceIdentity]:
        sequences: dict[tuple[str, str], SequenceIdentity] = {}
        sequences_root = self.paths.sequences_root()
        if sequences_root.exists():
            for sequence_json in sequences_root.glob("**/sequence.json"):
                sequence_root = sequence_json.parent
                episode_root = sequence_root.parent
                data = read_json(sequence_json, {}) or {}
                episode = str(data.get("episode") or episode_root.name).strip()
                sequence = str(data.get("sequence") or data.get("seq") or sequence_root.name).strip()
                if episode and sequence:
                    sequences[(episode, sequence)] = SequenceIdentity(episode, sequence)
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

    def dependencies_path(self, identity: ShotIdentity | SequenceIdentity) -> Path:
        if isinstance(identity, SequenceIdentity):
            return self.sequence_workspace_root(identity.episode, identity.sequence) / "dependencies.json"
        return self.shot_root(identity) / "dependencies.json"

    def load_dependencies(self, identity: ShotIdentity | SequenceIdentity) -> dict[str, Any]:
        target = identity.shot if isinstance(identity, ShotIdentity) else identity.sequence
        data = read_json(self.dependencies_path(identity), None)
        if data is None:
            return {"schema_version": 1, "shot": target, "dependencies": []}
        if not isinstance(data, dict):
            raise ValueError("dependencies.json must contain a JSON object.")
        data.setdefault("schema_version", 1)
        data.setdefault("shot", target)
        data.setdefault("dependencies", [])
        return data

    def write_dependencies(
        self,
        identity: ShotIdentity | SequenceIdentity,
        dependencies_data: dict[str, Any],
    ) -> Path:
        target = identity.shot if isinstance(identity, ShotIdentity) else identity.sequence
        clean_data = {
            "schema_version": 1,
            "shot": target,
            "dependencies": [dict(item) for item in dependencies_data.get("dependencies", [])],
        }
        issues = validate_dependencies_data(clean_data)
        if issues:
            raise ValueError("Invalid dependencies data: " + ", ".join(issues))
        return write_json(self.dependencies_path(identity), clean_data)

    def set_selected_dependency(
        self,
        identity: ShotIdentity | SequenceIdentity,
        dependency_id: str,
    ) -> Path:
        data = self.load_dependencies(identity)
        entries = data.get("dependencies") or []
        selected = next((item for item in entries if item.get("id") == dependency_id), None)
        if selected is None:
            raise ValueError(f"Dependency was not found: {dependency_id}")
        selected_target = str(selected.get("target") or selected.get("asset") or "Shot")
        for item in entries:
            item_target = str(item.get("target") or item.get("asset") or "Shot")
            if (
                item.get("type") == selected.get("type")
                and item.get("role") == selected.get("role")
                and item_target == selected_target
            ):
                item["status"] = "selected" if item is selected else "alternate"
        return self.write_dependencies(identity, data)

    def sequence_shot_identities(self, identity: SequenceIdentity) -> list[ShotIdentity]:
        sequence_data = self.load_sequence(identity)
        rows = []
        for item in sequence_data.get("shots") or []:
            shot = str(item.get("shot") if isinstance(item, dict) else item or "").strip()
            if shot:
                rows.append(ShotIdentity(identity.episode, identity.sequence, shot))
        if rows:
            return rows
        return [shot for shot in self.list_shots() if shot.episode == identity.episode and shot.sequence == identity.sequence]

    def sequence_dependency_assignments(self, identity: SequenceIdentity) -> dict[str, dict[str, Any]]:
        return {shot.shot: self.load_dependencies(shot) for shot in self.sequence_shot_identities(identity)}

    def sequence_input_candidates(self, identity: SequenceIdentity) -> list[dict[str, Any]]:
        data_root = self.sequence_workspace_root(identity.episode, identity.sequence) / "data"
        supported = {".fbx", ".abc", ".usd", ".usda", ".usdc", ".wav", ".aif", ".aiff", ".mp3", ".mov", ".mp4"}
        candidates = []
        if not data_root.is_dir():
            return candidates
        for path in sorted(data_root.rglob("*"), key=lambda value: str(value).lower()):
            if not path.is_file() or path.suffix.lower() not in supported:
                continue
            relative = path.relative_to(data_root)
            parts = relative.parts
            dependency_type = _dependency_type_from_path(parts, path.suffix)
            representation = path.suffix.lower().lstrip(".")
            target = "Shot"
            name = path.stem
            if dependency_type == "mocap":
                # Expected layout: mocap/<representation>/<cast>/<version>/<file>
                if len(parts) >= 3:
                    target = parts[2]
            elif dependency_type == "virtual_camera" and len(parts) >= 2:
                target = "Camera"
                name = parts[1]
            role = {
                "mocap": "body_motion",
                "virtual_camera": "camera_reference",
                "audio": "editorial_mix",
                "reference": "reference",
            }[dependency_type]
            candidates.append(
                {
                    "id": _dependency_id(dependency_type, target, name, representation),
                    "name": name,
                    "type": dependency_type,
                    "target": target,
                    "role": role,
                    "source": str(path),
                    "representation": representation,
                    "updated": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="minutes"),
                }
            )
        return candidates

    def shot_frame_range(self, identity: ShotIdentity) -> tuple[int, int]:
        shot_data = self.load_shot(identity)
        editorial = shot_data.get("editorial") or {}
        frame_range = editorial.get("frame_range") or shot_data.get("frame_range")
        if isinstance(frame_range, (list, tuple)) and len(frame_range) >= 2:
            start, end = frame_range[0], frame_range[1]
        else:
            start = editorial.get("cut_in", shot_data.get("cut_in"))
            end = editorial.get("cut_out", shot_data.get("cut_out"))
        try:
            start, end = int(start), int(end)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"shot.json does not define a valid editorial frame range: "
                f"{self.shot_root(identity) / 'shot.json'}"
            ) from exc
        if end < start:
            raise RuntimeError(f"Invalid shot.json frame range: {start}-{end}")
        return start, end

    def shot_work_dir(
        self,
        identity: ShotIdentity,
        department: str,
        option: str | None = None,
        tool_name: str = "maya",
        task: str = "main",
    ) -> Path:
        work_dir = self.paths.shot_work_dir(
            identity.episode,
            identity.sequence,
            identity.shot,
            department,
            _normalize_tool_name(tool_name),
        ) / _normalize_work_option(task)
        if option:
            return work_dir / _normalize_work_option(option)
        return work_dir

    def pre_task_shot_work_dir(
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
        return work_dir / _normalize_work_option(option) if option else work_dir

    def legacy_shot_work_dir(self, identity: ShotIdentity, department: str, option: str | None = None) -> Path:
        work_dir = self.paths.legacy_shot_work_dir(identity.episode, identity.sequence, identity.shot, department)
        if option:
            return work_dir / _normalize_work_option(option)
        return work_dir

    def legacy_shot_tool_work_dir(
        self,
        identity: ShotIdentity,
        department: str,
        option: str | None = None,
        tool_name: str = "maya",
    ) -> Path:
        work_dir = self.paths.legacy_shot_tool_work_dir(
            identity.episode,
            identity.sequence,
            identity.shot,
            department,
            _normalize_tool_name(tool_name),
        )
        if option:
            return work_dir / _normalize_work_option(option)
        return work_dir

    def list_shot_work_options(
        self,
        identity: ShotIdentity,
        department: str,
        tool_name: str = "maya",
        task: str = "main",
    ) -> list[str]:
        work_dirs = [
            self.shot_work_dir(identity, department, tool_name=tool_name, task=task),
        ]
        if task == self.shot_tasks(department)[0]:
            work_dirs.extend(
                [
                    self.pre_task_shot_work_dir(identity, department, tool_name=tool_name),
                    self.legacy_shot_tool_work_dir(identity, department, tool_name=tool_name),
                    self.legacy_shot_work_dir(identity, department),
                ]
            )
        options = {"main"}
        task_names = set(self.shot_tasks(department))
        for work_dir in work_dirs:
            if not work_dir.exists():
                continue
            options.update(
                path.name
                for path in work_dir.iterdir()
                if path.is_dir()
                and not path.name.startswith(".")
                and (
                    work_dir != self.pre_task_shot_work_dir(
                        identity,
                        department,
                        tool_name=tool_name,
                    )
                    or path.name not in task_names
                )
            )
        return sorted(options, key=lambda item: (item != "main", item.lower()))

    def create_shot_work_option(
        self,
        identity: ShotIdentity,
        option: str,
        department: str | None = None,
        task: str = "main",
    ) -> list[Path]:
        option_name = _normalize_work_option(option)
        if option_name == "all":
            raise ValueError("'all' is reserved for the work option filter.")
        paths = []
        departments = [department] if department else self.shot_departments
        for department_name in departments:
            path = self.shot_work_dir(
                identity,
                department_name,
                option_name,
                tool_name="maya",
                task=task,
            )
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
        task: str = "main",
    ) -> Path:
        work_root = self.paths.shot_work_dir(
            identity.episode,
            identity.sequence,
            identity.shot,
            department,
            _normalize_tool_name(tool_name),
        )
        output = self.output_resolver.resolve(
            "shot_work_scene",
            {
                "shot_root": self.shot_root(identity).as_posix(),
                "shot_work": work_root.as_posix(),
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "department": department,
                "dcc": _normalize_tool_name(tool_name),
                "task": _normalize_work_option(task),
                "option": _normalize_work_option(option),
                "version": f"{version:03d}",
                "take": f"{take:02d}",
                "ext": ext.lstrip("."),
            },
            default_directory="{shot_work}/{task}/{option}",
            default_filename="{shot}_{department}_v{version}_{take}.{ext}",
        )
        return output.path

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
        task: str = "main",
    ) -> Path:
        parsed = parse_shot_work_file(Path(current_path).name) if current_path else None
        if parsed and not parsed.get("generic") and parsed.get("shot") != identity.shot:
            parsed = None
        if parsed:
            department = parsed.get("department") or department
            ext = parsed["ext"]
            version = parsed["version"] + 1 if next_version else parsed["version"]
            if next_version:
                take = 1
            else:
                take = self.next_shot_work_take(identity, department, version, ext, option=option, tool_name=tool_name, task=task)
        else:
            version = 1
            take = self.next_shot_work_take(identity, department, version, ext, option=option, tool_name=tool_name, task=task)
        return self.shot_work_file_path(identity, department, version, take, option, tool_name, ext, task)

    def next_shot_work_take(
        self,
        identity: ShotIdentity,
        department: str,
        version: int,
        ext: str = "ma",
        option: str = "main",
        tool_name: str = "maya",
        task: str = "main",
    ) -> int:
        max_take = 0
        for work_dir in self._shot_work_option_dirs(identity, department, option, tool_name=tool_name, task=task):
            for path in work_dir.iterdir() if work_dir.exists() else []:
                parsed = parse_shot_work_file(path.name)
                if not parsed:
                    continue
                if (
                    (parsed.get("generic") or parsed["shot"] == identity.shot)
                    and (parsed.get("generic") or parsed["department"] == department)
                    and parsed["version"] == version
                    and parsed["ext"] == ext
                ):
                    max_take = max(max_take, parsed["take"])
        return max_take + 1

    def list_shot_work_files(
        self,
        identity: ShotIdentity,
        department: str | None = None,
        option: str | None = None,
        tool_name: str = "maya",
        task: str = "main",
    ) -> list[ShotWorkFile]:
        departments = [department] if department else self.shot_departments
        files: list[ShotWorkFile] = []
        for dept in departments:
            options = self.list_shot_work_options(identity, dept, tool_name=tool_name, task=task) if not option or option == "all" else [_normalize_work_option(option)]
            for option_name in options:
                for work_dir in self._shot_work_option_dirs(identity, dept, option_name, tool_name=tool_name, task=task):
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
                                task=str(metadata.get("task") or task),
                            )
                        )
        return sorted(files, key=lambda item: (item.department, item.task, item.option, item.version, item.take, item.file.lower()), reverse=True)

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
                work_dirs = [work_dir]
                work_dirs.extend(path for path in work_dir.iterdir() if path.is_dir())
                for path in (item for directory in work_dirs for item in directory.iterdir()):
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

    def list_sequence_construct_build_scenes(
        self,
        identity: SequenceIdentity,
        *,
        department: str = "",
        task: str = "",
    ) -> list[dict[str, Any]]:
        """List generated sequence scenes that passed through scene-build validation."""

        records = []
        department_filter = _normalize_work_option(department) if department else ""
        task_filter = _normalize_work_option(task) if task else ""
        roots = [
            (self.sequence_build_root(identity), "*/*/*/v*/build_manifest.json"),
            (self.sequence_build_root(identity), "*/*/v*/build_manifest.json"),
            (self.legacy_sequence_build_root(identity), "*/*/v*/build_manifest.json"),
        ]
        for manifest_path in (
            manifest_path
            for root, pattern in roots
            for manifest_path in (root.glob(pattern) if root.is_dir() else [])
        ):
            manifest = read_json(manifest_path, {}) or {}
            relative_parts = manifest_path.relative_to(
                next(root for root, _pattern in roots if manifest_path.is_relative_to(root))
            ).parts
            build_department = _normalize_work_option(
                str(manifest.get("department") or relative_parts[0])
            )
            build_task = _normalize_work_option(
                str(manifest.get("task") or relative_parts[-3])
            )
            build_dcc = _normalize_work_option(
                str(manifest.get("dcc") or (relative_parts[1] if len(relative_parts) >= 5 else "maya"))
            )
            if department_filter and build_department != department_filter:
                continue
            if task_filter and build_task != task_filter:
                continue
            scene = Path(str(manifest.get("scene") or ""))
            if not scene.is_file():
                continue
            validation_path = manifest_path.with_name("validation.json")
            validation = read_json(validation_path, {}) or {}
            records.append(
                {
                    "mode": "build",
                    "path": str(scene),
                    "manifest": str(manifest_path),
                    "validation": str(validation_path),
                    "validation_state": str(validation.get("status") or "missing").lower(),
                    "department": build_department,
                    "task": build_task,
                    "dcc": build_dcc,
                    "version": scene.parent.name,
                    "updated": datetime.fromtimestamp(scene.stat().st_mtime).isoformat(timespec="seconds"),
                    "sequence": True,
                }
            )
        return sorted(
            records,
            key=lambda item: (
                int(str(item.get("version") or "v000")[1:])
                if str(item.get("version") or "")[1:].isdigit()
                else -1,
                str(item.get("updated") or ""),
            ),
            reverse=True,
        )

    def _shot_work_option_dirs(
        self,
        identity: ShotIdentity,
        department: str,
        option: str,
        tool_name: str = "maya",
        task: str = "main",
    ) -> list[Path]:
        option_name = _normalize_work_option(option)
        directories = [
            self.shot_work_dir(
                identity,
                department,
                option_name,
                tool_name=tool_name,
                task=task,
            )
        ]
        include_legacy = task == self.shot_tasks(department)[0]
        if option_name == "main":
            directories.append(
                self.shot_work_dir(
                    identity,
                    department,
                    tool_name=tool_name,
                    task=task,
                )
            )
        if include_legacy and option_name == "main":
            directories.append(
                self.pre_task_shot_work_dir(
                    identity,
                    department,
                    tool_name=tool_name,
                )
            )
            directories.append(self.legacy_shot_tool_work_dir(identity, department, tool_name=tool_name))
            directories.append(self.legacy_shot_work_dir(identity, department))
        if include_legacy:
            directories.append(
                self.pre_task_shot_work_dir(
                    identity,
                    department,
                    option_name,
                    tool_name=tool_name,
                )
            )
            directories.append(self.legacy_shot_tool_work_dir(identity, department, option_name, tool_name=tool_name))
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
        task: str = "main",
        scene_info: dict[str, Any] | None = None,
        comment: str = "",
        thumbnail: str = "",
        token_context: dict[str, Any] | None = None,
        construct_data: dict[str, Any] | None = None,
    ) -> Path:
        work_path = Path(path)
        parsed = parse_shot_work_file(work_path.name) or {}
        construct = self.construct_snapshot(identity, construct_data) if construct_data is not None else {}
        data = {
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "department": department,
            "tool": "maya",
            "task": _normalize_work_option(task),
            "option": _normalize_work_option(option),
            "version": parsed.get("version"),
            "take": parsed.get("take"),
            "comment": comment,
            "thumbnail": thumbnail,
            "source": work_path.name,
            "scene_info": scene_info or {},
            "tokens": token_context or {},
        }
        if construct:
            data["construct"] = construct
        return write_json(sidecar_path(work_path), data)

    def list_construct_build_scenes(
        self,
        identity: ShotIdentity,
        *,
        department: str = "",
        task: str = "",
    ) -> list[dict[str, Any]]:
        """List generated, validated Construct scenes without promoting them to Work."""

        records = []
        department_filter = _normalize_work_option(department) if department else ""
        task_filter = _normalize_work_option(task) if task else ""
        roots = [
            (self.shot_build_root(identity), "*/*/*/v*/build_manifest.json"),
            (self.shot_build_root(identity), "*/*/v*/build_manifest.json"),
            (self.legacy_shot_build_root(identity), "*/*/v*/build_manifest.json"),
        ]
        for manifest_path in (
            manifest_path
            for root, pattern in roots
            for manifest_path in (root.glob(pattern) if root.is_dir() else [])
        ):
            manifest = read_json(manifest_path, {}) or {}
            relative_parts = manifest_path.relative_to(
                next(root for root, _pattern in roots if manifest_path.is_relative_to(root))
            ).parts
            build_department = _normalize_work_option(
                str(manifest.get("department") or relative_parts[0])
            )
            build_task = _normalize_work_option(
                str(manifest.get("task") or relative_parts[-3])
            )
            build_dcc = _normalize_work_option(
                str(manifest.get("dcc") or (relative_parts[1] if len(relative_parts) >= 5 else "maya"))
            )
            if department_filter and build_department != department_filter:
                continue
            if task_filter and build_task != task_filter:
                continue
            scene = Path(str(manifest.get("scene") or ""))
            if not scene.is_file():
                continue
            validation_path = manifest_path.with_name("validation.json")
            validation = read_json(validation_path, {}) or {}
            state = str(validation.get("status") or "missing").lower()
            records.append(
                {
                    "mode": "build",
                    "path": str(scene),
                    "manifest": str(manifest_path),
                    "validation": str(validation_path),
                    "validation_state": state,
                    "department": build_department,
                    "task": build_task,
                    "dcc": build_dcc,
                    "version": scene.parent.name,
                    "updated": datetime.fromtimestamp(scene.stat().st_mtime).isoformat(
                        timespec="seconds"
                    ),
                }
            )
        return sorted(
            records,
            key=lambda item: (
                int(str(item.get("version") or "v000")[1:])
                if str(item.get("version") or "")[1:].isdigit()
                else -1,
                str(item.get("updated") or ""),
            ),
            reverse=True,
        )

    def write_sequence_work_metadata(
        self,
        path: str | Path,
        identity: SequenceIdentity,
        department: str,
        tool_name: str = "maya",
        scene_info: dict[str, Any] | None = None,
        comment: str = "",
        thumbnail: str = "",
        token_context: dict[str, Any] | None = None,
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
            "tokens": token_context or {},
        }
        return write_json(sidecar_path(work_path), data)

    @staticmethod
    def thumbnail_path_for_workfile(path: str | Path) -> Path:
        work_path = Path(path)
        return work_path.parent / ".thumbnails" / f"{work_path.stem}.jpg"

    def next_shot_scene_archive_path(
        self,
        identity: ShotIdentity,
        department: str,
        ext: str = "ma",
    ) -> Path:
        base_dir = self.shot_publish_root(identity) / department / "scene"
        version_label = self._next_publish_version(base_dir)
        clean_ext = str(ext or "ma").lower().lstrip(".")
        return base_dir / version_label / f"scene.{clean_ext}"

    def register_shot_scene_archive(
        self,
        identity: ShotIdentity,
        department: str,
        scene_path: str | Path,
        *,
        source_workfile: str | Path | None = None,
        option: str = "main",
        scene_info: dict[str, Any] | None = None,
        comment: str = "",
        token_context: dict[str, Any] | None = None,
    ) -> Path:
        scene_file = Path(scene_path)
        version_label = scene_file.parent.name
        base_dir = scene_file.parent.parent
        file_key = scene_file.suffix.lower().lstrip(".") or "ma"
        publish_data = {
            "publish_type": department,
            "subset": "scene",
            "snapshot_type": "scene_archive",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "department": department,
            "option": _normalize_work_option(option),
            "version": version_label,
            "files": {
                file_key: scene_file.name,
            },
            "source_workfile": self._relative_to_project(Path(source_workfile)) if source_workfile else "",
            "scene_info": scene_info or {},
            "tokens": token_context or {},
            "archived_at": datetime.now().isoformat(timespec="seconds"),
            "comment": comment,
        }
        publish_json = write_json(scene_file.parent / "publish.json", publish_data)
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/{scene_file.name}"})
        self._update_versions(base_dir / "versions.json", version_label)
        return publish_json

    def plan_preview_render_publish(
        self,
        identity: ShotIdentity,
        settings: dict[str, Any],
        *,
        department: str = "",
    ) -> dict[str, Any]:
        """Resolve the next non-published Preview Render output."""
        rows = [
            dict(row)
            for row in (settings.get("rows") or [])
            if isinstance(row, dict) and bool(row.get("enabled", True))
        ]
        if not rows:
            raise ValueError("Smart Playblast settings contain no enabled rows.")
        clean_department = _clean_publish_token(
            department or str(settings.get("department") or "default")
        )
        base_dir = self.shot_output_root(identity) / "preview_render" / clean_department
        groups = []
        used_groups: set[str] = set()
        filename_template = str(
            (
                self.project_config.load("naming.yml").get(
                    "smart_playblast"
                ) or {}
            ).get("filename")
            or (
                "{project}_{episode}_{sequence}_{shot}_{dept}_"
                "{preview}_v{version}_t{take}_####.{ext}"
            )
        )
        for order, row in enumerate(rows):
            layer_name = str(row.get("layer") or "").strip()
            group_name = layer_name
            if group_name.lower().startswith("review_"):
                group_name = group_name[7:]
            clean_group = _clean_publish_token(group_name or f"group_{order + 1}")
            base_group = clean_group
            suffix = 2
            while clean_group in used_groups:
                clean_group = f"{base_group}_{suffix}"
                suffix += 1
            used_groups.add(clean_group)
            requested_version = max(1, int(row.get("version") or 1))
            version_label = format_version(requested_version)
            requested_take = max(1, int(row.get("take") or 1))
            take_number = requested_take
            camera_name = str(row.get("camera") or "").replace("\\", "|").rsplit("|", 1)[-1]
            output_override = str(row.get("output_override") or "").strip()
            pattern_template = output_override or filename_template
            output = None
            while True:
                take_label = f"t{take_number:03d}"
                output = self.output_resolver.resolve(
                    "__preview_render_override__" if output_override else "preview_render",
                    {
                        "shot_root": self.shot_root(identity).as_posix(),
                        "episode": identity.episode,
                        "sequence": identity.sequence,
                        "shot": identity.shot,
                        "department": clean_department,
                        "preview": clean_group,
                        "cam": camera_name,
                        "version": f"{requested_version:03d}",
                        "take": f"{take_number:03d}",
                        "frame": "####",
                        "ext": "png",
                    },
                    default_directory="{shot_root}/output/preview_render/{department}/layers/{preview}/v{version}/t{take}",
                    default_filename=pattern_template.replace("*", "_"),
                )
                if not output.directory.exists():
                    break
                take_number += 1
            pattern = output.filename
            groups.append(
                {
                    "group": clean_group,
                    "source_layer": str(row.get("display_layer") or layer_name),
                    "order": order,
                    "version": version_label,
                    "take": take_label,
                    "output_dir": str(output.directory),
                    "pattern": pattern,
                    "camera": str(row.get("camera") or ""),
                    "frame_range": [int(row.get("start", 1)), int(row.get("end", 1))],
                    "resolution": [int(row.get("width", 1280)), int(row.get("height", 720))],
                    "playblast_preset": str(row.get("preset") or ""),
                }
            )
        return {
            "schema": "preview_render_plan/v1",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "base_dir": str(base_dir),
            "department": clean_department,
            "groups": groups,
        }

    def publish_renderlayer_settings(
        self,
        identity: ShotIdentity,
        settings: dict[str, Any],
        *,
        source_scene: str | Path = "",
        comment: str = "",
    ) -> Path:
        """Publish the legacy versioned Smart Playblast settings contract."""

        base_dir = self.shot_publish_root(identity) / "renderlayer"
        version = self._next_publish_version(base_dir)
        version_dir = base_dir / version
        version_dir.mkdir(parents=True, exist_ok=False)
        rows = [dict(row) for row in (settings.get("rows") or [])]
        layers = [
            {
                **row,
                "resolution": [int(row.get("width") or 0), int(row.get("height") or 0)],
                "frame_range": [int(row.get("start") or 0), int(row.get("end") or 0)],
            }
            for row in rows
        ]
        output = write_json(
            version_dir / "playblast.json",
            {
                "schema": "smartpipeline.playblast_settings.v1",
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "department": str(settings.get("department") or "anim"),
                "version": version,
                "layer_order": [str(row.get("layer") or "") for row in rows],
                "layers": layers,
                "source_scene": self._relative_to_project(Path(source_scene)) if source_scene else "",
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/playblast.json"})
        self._update_versions(base_dir / "versions.json", version)
        return output

    def record_preview_render_outputs(
        self,
        plan: dict[str, Any],
        results: dict[str, dict[str, Any]],
        *,
        source_scene: str = "",
    ) -> list[Path]:
        """Record generated output takes without creating a publish manifest."""
        written = []
        for group in plan.get("groups") or []:
            group_name = str(group.get("group") or "")
            result = results.get(group_name)
            if not result:
                continue
            first_file = Path(str(result.get("first_file") or ""))
            last_file = Path(str(result.get("last_file") or ""))
            file_count = int(result.get("file_count") or 0)
            if file_count <= 0 or not first_file.is_file():
                raise RuntimeError(f"Preview Render output validation failed: {group_name}")
            version_label = str(group.get("version") or "")
            take_label = str(group.get("take") or "")
            output_dir = Path(str(group.get("output_dir") or ""))
            pattern_name = str(group.get("pattern") or "")
            output_data = {
                "schema": "preview_render_group_output/v1",
                "group": group_name,
                "version": version_label,
                "take": take_label,
                "pattern": pattern_name,
                "camera": str(group.get("camera") or ""),
                "frame_range": list(group.get("frame_range") or []),
                "resolution": list(group.get("resolution") or []),
                "order": int(group.get("order", 0)),
                "file_count": file_count,
                "first_file": first_file.name,
                "last_file": last_file.name if last_file.is_file() else "",
                "members": list(result.get("members") or []),
                "source_scene": self._relative_to_project(Path(source_scene))
                if source_scene
                else "",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
            written.append(write_json(output_dir / "output.json", output_data))
            group_base = output_dir.parent.parent
            write_json(
                group_base / "latest.json",
                {
                    "version": version_label,
                    "take": take_label,
                    "path": f"{version_label}/{take_label}/output.json",
                },
            )
            versions_path = group_base / "versions.json"
            versions = read_json(versions_path, []) or []
            if not isinstance(versions, list):
                versions = []
            updated = []
            found = False
            for item in versions:
                if not isinstance(item, dict):
                    continue
                item = dict(item)
                if item.get("version") == version_label:
                    takes = list(item.get("takes") or [])
                    if take_label not in takes:
                        takes.append(take_label)
                    item.update(
                        {
                            "status": "latest",
                            "latest_take": take_label,
                            "takes": takes,
                        }
                    )
                    found = True
                elif item.get("status") == "latest":
                    item["status"] = "available"
                updated.append(item)
            if not found:
                updated.append(
                    {
                        "version": version_label,
                        "status": "latest",
                        "latest_take": take_label,
                        "takes": [take_label],
                    }
                )
            write_json(versions_path, updated)
        if not written:
            raise RuntimeError("Preview Render produced no recordable outputs.")
        return written

    def latest_preview_render_outputs(
        self,
        identity: ShotIdentity,
        *,
        department: str = "anim",
    ) -> dict[str, dict[str, Any]]:
        """Return validated latest non-published output metadata by layer."""
        layers_root = (
            self.shot_root(identity)
            / "output"
            / "preview_render"
            / _clean_publish_token(department or "anim")
            / "layers"
        )
        if not layers_root.is_dir():
            legacy_root = layers_root.parent / "groups"
            layers_root = legacy_root if legacy_root.is_dir() else layers_root
        outputs: dict[str, dict[str, Any]] = {}
        if not layers_root.is_dir():
            return outputs
        for layer_dir in layers_root.iterdir():
            if not layer_dir.is_dir():
                continue
            latest = read_json(layer_dir / "latest.json", {}) or {}
            output_path = layer_dir / str(latest.get("path") or "")
            output = read_json(output_path, {}) or {}
            if not output_path.is_file() or not isinstance(output, dict):
                continue
            version = str(output.get("version") or latest.get("version") or "")
            take = str(output.get("take") or latest.get("take") or "")
            if not re.fullmatch(r"v\d+", version) or not re.fullmatch(r"t\d+", take):
                continue
            pattern = str(output.get("pattern") or "")
            extension = Path(pattern).suffix.lower() or ".png"
            if not any(
                path.is_file() and path.suffix.lower() == extension
                for path in output_path.parent.iterdir()
            ):
                continue
            outputs[layer_dir.name] = {
                **output,
                "version": version,
                "take": take,
                "output_json": str(output_path),
                "output_dir": str(output_path.parent),
            }
        return outputs

    def finalize_preview_render_publish(
        self,
        identity: ShotIdentity,
        plan: dict[str, Any],
        results: dict[str, dict[str, Any]],
        *,
        source_scene: str = "",
        comment: str = "",
    ) -> Path:
        """Register rendered group outputs and write a fixed package manifest."""
        if not results:
            raise RuntimeError("Preview Render produced no image sequences.")
        base_dir = Path(str(plan.get("base_dir") or ""))
        packages_dir = Path(str(plan.get("packages_dir") or ""))
        package_version = str(plan.get("package_version") or "")
        department = str(plan.get("department") or "")
        if not base_dir or not packages_dir or not package_version:
            raise ValueError("Preview Render plan is incomplete.")
        package_dir = packages_dir / package_version
        manifest_groups: dict[str, Any] = {}
        for group in plan.get("groups") or []:
            group_name = str(group.get("group") or "")
            result = results.get(group_name)
            if not result:
                continue
            first_file = str(result.get("first_file") or "")
            last_file = str(result.get("last_file") or "")
            file_count = int(result.get("file_count") or 0)
            if file_count <= 0 or not first_file or not Path(first_file).exists():
                raise RuntimeError(f"Preview Render output validation failed: {group_name}")
            version_label = str(group.get("version") or "")
            take_label = str(group.get("take") or "")
            group_base = base_dir / "layers" / group_name
            pattern_path = Path(str(group.get("output_dir") or "")) / str(group.get("pattern") or "")
            write_json(
                Path(str(group.get("output_dir") or "")) / "output.json",
                {
                    "schema": "preview_render_group_output/v1",
                    "group": group_name,
                    "version": version_label,
                    "take": take_label,
                    "pattern": pattern_path.name,
                    "camera": str(group.get("camera") or ""),
                    "frame_range": list(group.get("frame_range") or []),
                    "resolution": list(group.get("resolution") or []),
                    "file_count": file_count,
                    "first_file": Path(first_file).name,
                    "last_file": Path(last_file).name if last_file else "",
                    "members": list(result.get("members") or []),
                },
            )
            write_json(
                group_base / "latest.json",
                {
                    "version": version_label,
                    "take": take_label,
                    "path": f"{version_label}/{take_label}/{pattern_path.name}",
                },
            )
            versions_path = group_base / "versions.json"
            versions = read_json(versions_path, []) or []
            if not isinstance(versions, list):
                versions = []
            next_versions = []
            found = False
            for item in versions:
                if not isinstance(item, dict):
                    continue
                item = dict(item)
                if item.get("version") == version_label:
                    takes = list(item.get("takes") or [])
                    if take_label not in takes:
                        takes.append(take_label)
                    item.update({"status": "latest", "latest_take": take_label, "takes": takes})
                    found = True
                elif item.get("status") == "latest":
                    item["status"] = "available"
                next_versions.append(item)
            if not found:
                next_versions.append(
                    {
                        "version": version_label,
                        "status": "latest",
                        "latest_take": take_label,
                        "takes": [take_label],
                    }
                )
            write_json(versions_path, next_versions)
            manifest_groups[group_name] = {
                "order": int(group.get("order", 0)),
                "version": version_label,
                "take": take_label,
                "pattern": self._relative_path(package_dir, pattern_path),
                "camera": str(group.get("camera") or ""),
                "frame_range": list(group.get("frame_range") or []),
                "resolution": list(group.get("resolution") or []),
                "file_count": file_count,
                "first_file": self._relative_path(package_dir, Path(first_file)) if first_file else "",
                "last_file": self._relative_path(package_dir, Path(last_file)) if last_file else "",
                "members": list(result.get("members") or []),
            }
        if not manifest_groups:
            raise RuntimeError("All Preview Render groups were skipped or empty.")
        source_scene_value = self._relative_to_project(Path(source_scene)) if source_scene else ""
        template_candidates = [
            self.project_config.project_root
            / "settings"
            / "templates"
            / "ae"
            / "review"
            / f"review_{department}.aep",
            self.project_config.project_root
            / "settings"
            / "templates"
            / "ae"
            / "review"
            / "review_base.aep",
            _pipeline_root() / "templates" / "ae" / "review" / "review_base.aep",
        ]
        template_project = next(
            (candidate.as_posix() for candidate in template_candidates if candidate.is_file()),
            "",
        )
        review_spec, review_spec_path = self.resolved_review_spec(
            identity,
            department=department,
        )
        manifest = {
            "schema": "preview_render_manifest/v1",
            "publish_type": "preview_render",
            "name": f"{identity.shot}_{department}_{package_version}",
            "project": self.project_config.project_name,
            "projectRoot": self.project_config.project_root.as_posix() if self.project_config.project_root else "",
            "configDir": self.project_config.config_dir.as_posix(),
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "department": department,
            "version": package_version,
            "package_root": package_dir.as_posix(),
            "resolution": list((self.project_config.base.get("anchors") or {}).get("resolution") or []),
            "template_project": template_project,
            "source_scene": source_scene_value,
            "review_spec": {
                "version": str(review_spec.get("version") or ""),
                "path": self._relative_path(package_dir, review_spec_path),
            },
            "layers": manifest_groups,
            "groups": manifest_groups,
            "layer_order": [
                name
                for name, _data in sorted(
                    manifest_groups.items(), key=lambda item: int(item[1].get("order", 0))
                )
            ],
            "group_order": [
                name
                for name, _data in sorted(
                    manifest_groups.items(), key=lambda item: int(item[1].get("order", 0))
                )
            ],
            "comment": comment,
            "published_at": datetime.now().isoformat(timespec="seconds"),
        }
        manifest["exported_at"] = manifest["published_at"]
        manifest["items"] = [
            {
                "id": group_name,
                "name": group_name,
                "layer": group_name,
                "sourcePath": str(results.get(group_name, {}).get("source_first_file") or ""),
                "outputPath": group_data["pattern"],
                "first_frame_file": group_data["first_file"],
                "version": group_data["version"],
                "latestVersion": group_data["version"],
                "take": group_data["take"],
                "latestTake": group_data["take"],
                "status": "ready",
                "camera": group_data["camera"],
                "frame_range": group_data["frame_range"],
                "resolution": group_data["resolution"],
            }
            for group_name, group_data in sorted(
                manifest_groups.items(), key=lambda item: int(item[1].get("order", 0))
            )
        ]
        manifest_path = write_json(package_dir / "render_manifest.json", manifest)
        write_json(
            package_dir / "publish.json",
            {
                "publish_type": "preview_render",
                "department": department,
                "version": package_version,
                "files": {"manifest": "render_manifest.json"},
                "source_scene": source_scene_value,
                "comment": comment,
            },
        )
        write_json(
            packages_dir / "latest.json",
            {"version": package_version, "path": f"{package_version}/render_manifest.json"},
        )
        self._update_versions(packages_dir / "versions.json", package_version)
        return manifest_path

    def publish_preview_render_outputs(
        self,
        identity: ShotIdentity,
        *,
        department: str = "anim",
        comment: str = "",
        groups: list[str] | None = None,
    ) -> Path:
        """Promote the latest output take for each Review Layer to publish."""
        clean_department = _clean_publish_token(department or "anim")
        output_base = (
            self.shot_root(identity)
            / "output"
            / "preview_render"
            / clean_department
        )
        output_layers = output_base / "layers"
        if not output_layers.is_dir():
            output_layers = output_base / "groups"
        if not output_layers.is_dir():
            raise RuntimeError(
                f"Preview Render output was not found: {output_base / 'layers'}"
            )
        publish_base = (
            self.shot_root(identity)
            / "publish"
            / "preview_render"
            / clean_department
        )
        packages_dir = publish_base / "packages"
        package_version = self._next_publish_version(packages_dir)
        plan_groups = []
        results = {}
        review_layers = self.review_layers(identity, clean_department)
        review_order = {
            str(name): int((data or {}).get("order", index * 10))
            for index, (name, data) in enumerate(review_layers.items())
        }
        requested_groups = {
            _clean_publish_token(name) for name in (groups or []) if name
        }
        group_dirs = sorted(
            (
                path
                for path in output_layers.iterdir()
                if path.is_dir()
                and (not requested_groups or path.name in requested_groups)
            ),
            key=lambda path: (
                review_order.get(path.name, 1_000_000),
                path.name.lower(),
            ),
        )
        for order, group_dir in enumerate(group_dirs):
            latest = read_json(group_dir / "latest.json", {}) or {}
            output_json = group_dir / str(latest.get("path") or "")
            output_data = read_json(output_json, {}) or {}
            if not output_json.is_file() or not output_data:
                continue
            version_label = str(output_data.get("version") or latest.get("version") or "")
            take_label = str(output_data.get("take") or latest.get("take") or "")
            source_dir = output_json.parent
            pattern = str(output_data.get("pattern") or "")
            extension = Path(pattern).suffix.lower() or ".png"
            source_files = sorted(
                path
                for path in source_dir.iterdir()
                if path.is_file() and path.suffix.lower() == extension
            )
            if not source_files:
                raise RuntimeError(
                    f"Preview Render footage was not found: {source_dir}"
                )
            publish_dir = (
                publish_base
                / "layers"
                / group_dir.name
                / version_label
                / take_label
            )
            publish_dir.mkdir(parents=True, exist_ok=True)
            published_files = []
            for source in source_files:
                destination = publish_dir / source.name
                if destination.exists():
                    if destination.stat().st_size != source.stat().st_size:
                        raise RuntimeError(
                            f"Immutable Preview Render already differs: {destination}"
                        )
                else:
                    shutil.copy2(source, destination)
                published_files.append(destination)
            source_scene = str(output_data.get("source_scene") or "")
            plan_groups.append(
                {
                    "group": group_dir.name,
                    "source_layer": group_dir.name,
                    "order": review_order.get(
                        group_dir.name,
                        int(output_data.get("order", order * 10)),
                    ),
                    "version": version_label,
                    "take": take_label,
                    "output_dir": str(publish_dir),
                    "pattern": pattern,
                    "camera": str(output_data.get("camera") or ""),
                    "frame_range": list(output_data.get("frame_range") or []),
                    "resolution": list(output_data.get("resolution") or []),
                }
            )
            results[group_dir.name] = {
                "file_count": len(published_files),
                "first_file": str(published_files[0]),
                "last_file": str(published_files[-1]),
                "members": list(output_data.get("members") or []),
                "source_first_file": str(source_files[0]),
                "source_scene": source_scene,
            }
        if not plan_groups:
            raise RuntimeError("No Preview Render output is ready to publish.")
        plan = {
            "schema": "preview_render_publish_plan/v1",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "base_dir": str(publish_base),
            "packages_dir": str(packages_dir),
            "package_version": package_version,
            "department": clean_department,
            "groups": plan_groups,
        }
        source_scene = next(
            (
                str(result.get("source_scene") or "")
                for result in results.values()
                if result.get("source_scene")
            ),
            "",
        )
        return self.finalize_preview_render_publish(
            identity,
            plan,
            results,
            source_scene=source_scene,
            comment=comment,
        )

    def list_preview_render_versions(
        self,
        identity: ShotIdentity,
        *,
        department: str = "",
    ) -> list[ShotDataVersion]:
        publish_root = self.shot_publish_root(identity) / "preview_render"
        rows: list[ShotDataVersion] = []
        if not publish_root.exists():
            return rows
        clean_department = _clean_publish_token(department) if department else ""
        department_dirs = [publish_root / clean_department] if clean_department else list(publish_root.iterdir())
        for department_dir in department_dirs:
            packages_dir = department_dir / "packages"
            if not packages_dir.is_dir():
                continue
            latest = read_json(packages_dir / "latest.json", {}) or {}
            latest_version = str(latest.get("version") or "")
            for version_dir in packages_dir.glob("v*"):
                if not version_dir.is_dir() or not version_dir.name[1:].isdigit():
                    continue
                manifest_path = version_dir / "render_manifest.json"
                if not manifest_path.exists():
                    continue
                metadata = read_json(version_dir / "publish.json", {}) or {}
                updated = ""
                try:
                    updated = datetime.fromtimestamp(manifest_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
                rows.append(
                    ShotDataVersion(
                        name=f"preview_render/{department_dir.name}",
                        version=version_dir.name,
                        path=str(manifest_path),
                        updated=updated,
                        comment=str(metadata.get("comment") or ""),
                        latest=version_dir.name == latest_version,
                    )
                )
        return sorted(rows, key=lambda row: (row.name, parse_version(row.version)), reverse=True)

    def list_review_spec_versions(
        self,
        identity: ShotIdentity,
        *,
        department: str = "",
    ) -> list[ShotDataVersion]:
        data_root = self.shot_data_root(identity) / "review_spec"
        rows: list[ShotDataVersion] = []
        if not data_root.exists():
            return rows
        clean_department = _clean_publish_token(department) if department else ""
        department_dirs = (
            [data_root / clean_department]
            if clean_department
            else [path for path in data_root.iterdir() if path.is_dir()]
        )
        for department_dir in department_dirs:
            latest = read_json(department_dir / "latest.json", {}) or {}
            latest_version = str(latest.get("version") or "")
            for version_dir in department_dir.glob("v*") if department_dir.exists() else []:
                if not version_dir.is_dir() or not version_dir.name[1:].isdigit():
                    continue
                spec_path = version_dir / "review_spec.json"
                if not spec_path.is_file():
                    continue
                metadata = read_json(version_dir / "data.json", {}) or {}
                updated = datetime.fromtimestamp(spec_path.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                )
                rows.append(
                    ShotDataVersion(
                        name=f"review_spec/{department_dir.name}",
                        version=version_dir.name,
                        path=str(spec_path),
                        updated=updated,
                        comment=str(metadata.get("comment") or ""),
                        latest=version_dir.name == latest_version,
                    )
                )
        return sorted(
            rows,
            key=lambda row: (row.name, parse_version(row.version) or 0),
            reverse=True,
        )

    @staticmethod
    def _relative_path(root: Path, path: Path) -> str:
        try:
            return Path(os.path.relpath(path, root)).as_posix()
        except (OSError, ValueError):
            return path.as_posix()

    def validate_cast(self, identity: ShotIdentity) -> list[ValidationIssue]:
        return validate_cast_data(self.load_cast(identity))

    def build_preview(
        self,
        identity: ShotIdentity,
        department: str = "default",
        *,
        cast_contexts: dict[str, str] | None = None,
        exclude_cast: list[str] | None = None,
    ) -> list[BuildPreviewItem]:
        cast_data = self.load_cast(identity)
        excluded = {str(value) for value in (exclude_cast or [])}
        if excluded and isinstance(cast_data.get("cast"), dict):
            cast_data = deepcopy(cast_data)
            cast_data["cast"] = {
                key: value
                for key, value in cast_data["cast"].items()
                if str(key) not in excluded
            }
        return self._build_preview_from_cast(
            cast_data,
            department=department,
            cast_contexts=cast_contexts,
        )

    def build_sequence_preview(self, identity: SequenceIdentity) -> list[BuildPreviewItem]:
        return self._build_preview_from_cast(
            self.load_sequence_cast(identity.episode, identity.sequence),
            consumer="sequence",
            department="layout",
        )

    def latest_anim_input(self, identity: ShotIdentity) -> Path | None:
        base_dir = self.shot_publish_root(identity) / "anim_input" / "main"
        latest = read_json(base_dir / "latest.json", {}) or {}
        path = base_dir / str(latest.get("path") or "")
        return path if path.exists() else None

    def latest_sequence_stage_input(
        self,
        identity: SequenceIdentity,
        department: str = "layout",
    ) -> Path | None:
        base_dir = (
            self.sequence_workspace_root(identity.episode, identity.sequence)
            / "publish"
            / "stage_input"
            / _normalize_work_option(department)
        )
        latest = read_json(base_dir / "latest.json", {}) or {}
        path = base_dir / str(latest.get("path") or "")
        return path if path.is_file() else None

    def build_sequence_stage_input(
        self,
        identity: SequenceIdentity,
        *,
        department: str = "layout",
        comment: str = "",
        overrides: dict[str, Any] | None = None,
    ) -> Path:
        sequence_data = self.load_sequence(identity)
        cast_data = self.load_sequence_cast(identity.episode, identity.sequence)
        if not sequence_data:
            raise RuntimeError(f"Sequence metadata was not found for {identity.code}.")
        if not (cast_data.get("cast") or {}):
            raise RuntimeError(f"Sequence cast is empty for {identity.code}.")
        override_data = deepcopy(overrides or {})
        use_placements = override_data.get("use_placements") is not False
        sequence_root = self.sequence_workspace_root(identity.episode, identity.sequence)
        placements = ""
        if use_placements:
            placements_root = sequence_root / "publish" / "layout" / "placements"
            latest = read_json(placements_root / "latest.json", {}) or {}
            placements_path = placements_root / str(latest.get("path") or "")
            if not placements_path.is_file():
                raise RuntimeError(
                    f"Sequence placements were not found: {placements_root}"
                )
            placements = self._relative_to_project(placements_path)
        cast_publish = self.publish_sequence_cast(
            identity.episode,
            identity.sequence,
            comment=comment,
        )
        department = _normalize_work_option(department)
        base_dir = sequence_root / "publish" / "stage_input" / department
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)
        editorial = sequence_data.get("editorial") or {}
        data = {
            "package_type": "sequence_input",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "department": department,
            "version": version_label,
            "fps": editorial.get("fps") or self.project_fps,
            "cut_range": [
                editorial.get("cut_in"),
                editorial.get("cut_out"),
            ],
            "cast": self._relative_to_project(cast_publish),
            "placements": placements,
            "placement_usage": "apply" if placements else "disabled",
            "editorial": self._relative_to_project(
                self.paths.project_root
                / "editorial"
                / "publish"
                / identity.episode
                / identity.sequence
                / "latest.json"
            ),
            "context": str(override_data.get("context") or "WORK"),
            "comment": comment,
            "overrides": override_data,
        }
        input_path = write_json(version_dir / "sequence_input.json", data)
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": "stage_input",
                "subset": department,
                "episode": identity.episode,
                "sequence": identity.sequence,
                "version": version_label,
                "files": {"sequence_input": "sequence_input.json"},
                "comment": comment,
            },
        )
        write_json(
            base_dir / "latest.json",
            {"version": version_label, "path": f"{version_label}/sequence_input.json"},
        )
        self._update_versions(base_dir / "versions.json", version_label)
        return input_path

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
        excluded = {
            str(value)
            for value in (data.get("exclude_cast") or [])
            if str(value).strip()
        }
        overrides = data.get("overrides") or {}
        cast_contexts = overrides.get("cast_contexts") or data.get("cast_contexts") or {}
        if excluded and isinstance(cast_data.get("cast"), dict):
            cast_data = deepcopy(cast_data)
            cast_data["cast"] = {
                key: value
                for key, value in cast_data["cast"].items()
                if str(key) not in excluded
            }
        return self._build_preview_from_cast(
            cast_data,
            department="anim",
            cast_contexts=cast_contexts,
        )

    def construct_path(self, identity: ShotIdentity) -> Path:
        return self.shot_root(identity) / "construct.json"

    def load_construct(self, identity: ShotIdentity) -> dict[str, Any]:
        data = read_json(self.construct_path(identity), {}) or {}
        if not isinstance(data, dict):
            data = {}
        components = data.get("components") or []
        normalized = [
            asdict(component)
            for component in self.normalize_construct_components(components)
        ]
        return {
            "construct_type": str(data.get("construct_type") or "shot_construct"),
            "episode": str(data.get("episode") or identity.episode),
            "sequence": str(data.get("sequence") or identity.sequence),
            "shot": str(data.get("shot") or identity.shot),
            "components": normalized,
        }

    def construct_components(self, identity: ShotIdentity) -> list[ConstructComponent]:
        return self.normalize_construct_components(self.load_construct(identity).get("components") or [])

    def construct_snapshot(self, identity: ShotIdentity, construct_data: dict[str, Any] | None = None) -> dict[str, Any]:
        data = construct_data if construct_data is not None else self.load_construct(identity)
        components = self.normalize_construct_components((data or {}).get("components") or [])
        return {
            "schema": "smart_construct_snapshot",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "components": [asdict(component) for component in components],
        }

    def construct_component_enabled(
        self,
        identity: ShotIdentity,
        component_type: str,
        name: str = "",
        source_field: str = "",
        *,
        default: bool = True,
    ) -> bool:
        component_type = component_type.strip().lower()
        name = name.strip()
        source_field = source_field.strip()
        components = [component for component in self.construct_components(identity) if component.component_type == component_type]
        if not components:
            return default
        matches = []
        for component in components:
            component_source_field = str(component.source.get("field") or "").strip()
            if source_field and component_source_field == source_field:
                matches.append(component)
                continue
            if name and component.name == name:
                matches.append(component)
        if not matches:
            return default
        return any(component.enabled for component in matches)

    def filter_preview_items_for_construct(self, identity: ShotIdentity, preview_items: list[BuildPreviewItem]) -> list[BuildPreviewItem]:
        rig_components = [
            component
            for component in self.construct_components(identity)
            if component.component_type == "rig"
        ]
        if not rig_components:
            return list(preview_items)
        enabled_components = [component for component in rig_components if component.enabled]
        filtered = []
        for item in preview_items:
            for component in enabled_components:
                if _construct_rig_matches_preview(component, item):
                    filtered.append(item)
                    break
        return filtered

    def write_construct(self, identity: ShotIdentity, construct_data: dict[str, Any]) -> Path:
        components = self.normalize_construct_components(construct_data.get("components") or [])
        data = {
            "construct_type": "shot_construct",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "components": [asdict(component) for component in components],
        }
        return write_json(self.construct_path(identity), data)

    def construct_from_cast(self, identity: ShotIdentity) -> dict[str, Any]:
        components: list[dict[str, Any]] = []
        for item in self.build_preview(identity):
            components.append(
                asdict(
                    ConstructComponent(
                        component_type="rig",
                        name=item.cast_key,
                        version=_version_from_path(Path(item.publish_path))
                        or item.asset_publish
                        or "approved",
                        mode="reference",
                        namespace=item.namespace or item.cast_key,
                        path=item.publish_path,
                        required=item.required,
                        enabled=True,
                        note=item.message if item.status != "resolved" else "",
                        source={
                            "kind": "cast",
                            "asset": item.asset,
                            "variant": item.variant,
                            "role": item.role,
                            "status": item.status,
                            "asset_publish": item.asset_publish or "approved",
                        },
                    )
                )
            )
        return {
            "construct_type": "shot_construct",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "components": components,
        }

    def construct_from_stage_inputs(
        self,
        identity: ShotIdentity,
        *,
        cast_contexts: dict[str, str] | None = None,
        exclude_cast: list[str] | None = None,
        representation: str = "project",
    ) -> dict[str, Any]:
        components: list[dict[str, Any]] = []
        anim_input_path = self.latest_anim_input(identity)
        anim_input = read_json(anim_input_path, {}) if anim_input_path else {}
        if anim_input_path:
            for field, component_type, name, mode, required in (
                ("placements", "placement", "placements", "file", True),
                ("layout_overlay", "layout_overlay", "layout_overlay", "reference", False),
            ):
                path = self._project_path_from_text(str(anim_input.get(field) or ""))
                components.append(
                    asdict(
                        ConstructComponent(
                            component_type=component_type,
                            name=name,
                            version=_version_from_path(path) or "latest",
                            mode=mode,
                            namespace=name if component_type in {"camera", "layout_overlay"} else "",
                            path=str(path) if path else "",
                            required=required,
                            enabled=bool(path and path.exists()) or not required,
                            note="" if path and path.exists() else f"{field} was not found in latest anim input.",
                            source={"kind": "anim_input", "field": field},
                        )
                    )
                )

        existing_keys = {
            _construct_component_key(component) for component in components
        }

        def add_published_component(
            component_type: str,
            name: str,
            version: str,
            path: str | Path,
            *,
            required: bool = False,
            mode: str = "file",
            source: dict[str, Any] | None = None,
        ) -> None:
            component = asdict(
                ConstructComponent(
                    component_type=component_type,
                    name=name,
                    version=version or "latest",
                    mode=mode,
                    namespace=name if component_type == "camera" else "",
                    path=str(path),
                    required=required,
                    enabled=bool(path and Path(path).exists()),
                    note="" if path and Path(path).exists() else "Published data was not found.",
                    source=dict(source or {}),
                )
            )
            key = _construct_component_key(component)
            if key not in existing_keys:
                components.append(component)
                existing_keys.add(key)

        latest_scene_data_rows = [
            row for row in self.list_shot_data_versions(identity) if row.latest
        ]
        # ``light/lights_grp`` was the original monolithic package. Once
        # per-light publishes exist, do not load that compatibility package as
        # well or the same lights would be constructed twice.
        has_per_light_data = any(
            (parts := str(row.name or "").split("/"))[0] == "light"
            and len(parts) > 1
            and parts[1] != "lights_grp"
            for row in latest_scene_data_rows
        )
        has_camera_data = any(
            str(row.name or "").split("/")[0] == "camera"
            for row in latest_scene_data_rows
        )
        for camera_path_text in ([] if has_camera_data else self._latest_review_camera_paths(identity)):
            camera_path = Path(camera_path_text)
            camera_name = camera_path.parents[2].name if len(camera_path.parents) > 2 else camera_path.stem
            add_published_component(
                "camera",
                camera_name,
                camera_path.parent.name,
                camera_path,
                mode="import",
                source={"kind": "published_camera"},
            )

        # Root-based Camera/Light Data Publishes are direct WORK Construct
        # inputs. Department publishes remain available and take precedence
        # when they use the same component key.
        for row in latest_scene_data_rows:
            parts = str(row.name or "").split("/")
            component_type = parts[0] if parts else ""
            if component_type not in {"camera", "light", "playblast_settings"}:
                continue
            name = parts[1] if len(parts) > 1 else "main"
            if component_type == "light" and name == "lights_grp" and has_per_light_data:
                continue
            version_dir = Path(row.path)
            manifest_path = (
                version_dir / f"{component_type}.json"
                if version_dir.is_dir() else version_dir
            )
            add_published_component(
                component_type,
                name,
                row.version,
                manifest_path,
                mode="apply" if component_type == "playblast_settings" else "import",
                source={"kind": "scene_data", "scope": "shot"},
            )

        for row in self.list_set_dress_publish_versions(identity):
            if row.latest:
                add_published_component(
                    "set_dress", row.name.removeprefix("set_dress/"), row.version,
                    row.path, source={"kind": "published_set_dress"},
                )

        cast_data = self.load_cast(identity)
        if not (cast_data.get("cast") or {}):
            cast_data = self.load_sequence_cast(identity.episode, identity.sequence)
        for cast_key, entry in sorted((cast_data.get("cast") or {}).items()):
            curve_path = self.latest_animation_curve_path(
                identity,
                target=str(cast_key),
                subset="curves",
            )
            if not curve_path:
                continue
            curve_data = read_json(curve_path, {}) or {}
            add_published_component(
                "animation_curve",
                str(cast_key),
                str(curve_data.get("version") or curve_path.parent.name),
                curve_path,
                required=bool((entry or {}).get("required", True)),
                source={
                    "kind": "animation_curve_data",
                    "namespace": str((entry or {}).get("namespace") or cast_key),
                    "asset": str((entry or {}).get("asset") or ""),
                },
            )

        try:
            preview = (
                self.build_preview_from_anim_input(identity)
                if anim_input_path and not (cast_contexts or exclude_cast)
                else self.build_preview(
                    identity,
                    cast_contexts=cast_contexts,
                    exclude_cast=exclude_cast,
                )
            )
        except Exception:
            preview = self.build_preview(identity)
        for item in preview:
            role = _normalize_role(item.role)
            requested_context = str((cast_contexts or {}).get(item.cast_key) or "WORK")
            asset_metadata = self._asset_workspace_metadata(item)
            load_policy = {}
            project_config = getattr(self, "project_config", None)
            if project_config is not None:
                try:
                    load_policy = (
                        project_config.load("templates_assets.yml").get("workspace_load_policy")
                        or {}
                    )
                except Exception:
                    load_policy = {}
            decision = resolve_asset_load_policy(
                asset_metadata,
                role=role,
                requested_context=requested_context,
                policy=load_policy,
            )
            requested_representation = str(representation or "project").strip().lower()
            if requested_representation in {"", "project", "default"}:
                requested_representation = str(
                    load_policy.get("representation") or "maya"
                ).strip().lower()
            if requested_representation == "maya":
                decision = type(decision)(
                    "reference", requested_context.upper(), "stage representation: maya"
                )
            elif requested_representation == "usd":
                decision = type(decision)(
                    "payload", requested_context.upper(), "stage representation: usd"
                )
            uses_payload = decision.mode == "payload"
            usd_source = (
                self._asset_usd_for_preview(item, profile=decision.context)
                if uses_payload
                else None
            )
            components.append(
                asdict(
                    ConstructComponent(
                        component_type=decision.component_type,
                        name=item.cast_key,
                        version=(
                            usd_source.parent.name
                            if uses_payload and usd_source
                            else _version_from_path(Path(item.publish_path))
                            or item.asset_publish
                            or "approved"
                        ),
                        mode=decision.mode,
                        namespace="" if uses_payload else (item.namespace or item.cast_key),
                        path=(
                            str(usd_source or "")
                            if uses_payload
                            else item.publish_path
                        ),
                        # A background may enter production before its formal
                        # Asset USD exists. Keep it visible as MISSING without
                        # blocking construction of the remaining scene.
                        required=False if uses_payload else item.required,
                        enabled=True,
                        note=(
                            "MISSING: Compose/Pack Asset USD required"
                            if uses_payload and not usd_source
                            else item.message if item.status != "resolved" else ""
                        ),
                        source={
                            "kind": "cast_entry",
                            "asset": item.asset,
                            "variant": item.variant,
                            "role": item.role,
                            "status": item.status,
                            "asset_publish": item.asset_publish or "approved",
                            "context": decision.context,
                            "load_policy": decision.mode,
                            "load_policy_reason": decision.reason,
                        },
                    )
                )
            )
        return {
            "construct_type": "shot_construct",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "components": components,
        }

    def _asset_workspace_metadata(self, item: BuildPreviewItem) -> dict[str, Any]:
        """Merge asset and variant metadata used by workspace load policy."""

        variant_root = Path(str(item.variant_root or "")) if item.variant_root else None
        asset_root = variant_root.parent if variant_root else self.find_asset_root(item.asset)
        metadata: dict[str, Any] = {}
        if asset_root:
            metadata.update(read_json(asset_root / "asset.json", {}) or {})
        if variant_root:
            variant_data = read_json(variant_root / "variant.json", {}) or {}
            for key in ("workspace_representation", "load_policy", "capabilities"):
                if key in variant_data:
                    if isinstance(metadata.get(key), dict) and isinstance(variant_data[key], dict):
                        merged = dict(metadata[key])
                        merged.update(variant_data[key])
                        metadata[key] = merged
                    else:
                        metadata[key] = variant_data[key]
        metadata.setdefault("asset", item.asset)
        return metadata

    def resolved_construct(
        self,
        identity: ShotIdentity,
        *,
        cast_contexts: dict[str, str] | None = None,
        exclude_cast: list[str] | None = None,
        representation: str = "project",
    ) -> dict[str, Any]:
        """Resolve published inputs and overlay the persisted Construct choices."""

        persisted = self.load_construct(identity)
        saved_contexts = {
            str(component.get("name") or ""): str(
                (component.get("source") or {}).get("context") or ""
            )
            for component in (persisted.get("components") or [])
            if isinstance(component, dict)
            and str((component.get("source") or {}).get("context") or "")
        }
        saved_contexts.update(cast_contexts or {})
        generated = self.construct_from_stage_inputs(
            identity,
            cast_contexts=saved_contexts,
            exclude_cast=exclude_cast,
            representation=representation,
        )
        persisted_by_key = {
            _construct_component_key(component): component
            for component in (persisted.get("components") or [])
            if isinstance(component, dict)
        }
        excluded = {str(value) for value in (exclude_cast or [])}
        components = []
        seen = set()
        generated_cast_types = {
            str(component.get("name") or ""): str(component.get("component_type") or "").lower()
            for component in (generated.get("components") or [])
            if str((component.get("source") or {}).get("kind") or "") == "cast_entry"
        }
        for component in generated.get("components") or []:
            key = _construct_component_key(component)
            saved = persisted_by_key.get(key) or {}
            merged = dict(component)
            for field in ("enabled", "mode", "namespace", "required"):
                if field in saved:
                    merged[field] = saved[field]
            generated_source = component.get("source") or {}
            is_optional_background = (
                str(component.get("component_type") or "").lower() == "usd"
                and _normalize_role(str(generated_source.get("role") or ""))
                in {"BGA", "ENV"}
            )
            # Older construct files may have persisted backgrounds as required
            # rig inputs. Formal Asset USD is intentionally optional while an
            # assembly is still being composed, so stale choices must not turn
            # the missing background back into a build blocker.
            if is_optional_background:
                merged["required"] = False
            if saved.get("note") and not is_optional_background:
                merged["note"] = saved["note"]
            source = dict(component.get("source") or {})
            source.update(saved.get("source") or {})
            merged["source"] = source
            if str(merged.get("name") or "") in excluded:
                merged["enabled"] = False
            components.append(merged)
            seen.add(key)
        for component in persisted.get("components") or []:
            if not isinstance(component, dict):
                continue
            source = component.get("source") or {}
            if (
                str(source.get("kind") or "") == "anim_input"
                and str(component.get("component_type") or "").lower()
                in {"animation", "cast", "camera"}
            ):
                continue
            if str(source.get("kind") or "") in {
                "published_preview_render",
                "published_animation_package",
            }:
                continue
            if (
                str(source.get("kind") or "") == "cast_entry"
                and str(component.get("name") or "") in generated_cast_types
                and str(component.get("component_type") or "").lower()
                != generated_cast_types[str(component.get("name") or "")]
            ):
                # The generated policy row is authoritative when metadata or
                # project policy changes an existing cast representation.
                continue
            key = _construct_component_key(component)
            if key not in seen:
                components.append(dict(component))
        return {
            "construct_type": "shot_construct",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "components": components,
        }

    def ensure_stage_construct(self, identity: ShotIdentity) -> Path:
        existing = self.load_construct(identity)
        incoming = self.construct_from_stage_inputs(identity)
        components = list(existing.get("components") or [])
        seen = {_construct_component_key(component) for component in components}
        changed = False
        for component in incoming.get("components") or []:
            key = _construct_component_key(component)
            if key in seen:
                continue
            components.append(component)
            seen.add(key)
            changed = True
        if not changed and self.construct_path(identity).exists():
            return self.construct_path(identity)
        return self.write_construct(identity, {"components": components})

    def _project_path_from_text(self, path_text: str) -> Path | None:
        if not path_text:
            return None
        path = Path(path_text)
        return path if path.is_absolute() else self.paths.project_root / path

    def normalize_construct_components(self, components: list[Any]) -> list[ConstructComponent]:
        normalized: list[ConstructComponent] = []
        for index, raw in enumerate(components):
            if not isinstance(raw, dict):
                continue
            component_type = str(raw.get("component_type") or raw.get("type") or "").strip().lower()
            if component_type not in CONSTRUCT_TYPES:
                component_type = "rig"
            name = str(raw.get("name") or raw.get("target") or f"{component_type}_{index + 1:03d}").strip()
            version = str(raw.get("version") or "latest").strip()
            mode = str(raw.get("mode") or "").strip().lower()
            if not mode:
                mode = "reference_cache" if component_type == "fx" else "reference"
            if mode not in CONSTRUCT_MODES:
                mode = "reference"
            path = str(raw.get("path") or "").strip()
            note = str(raw.get("note") or "").strip()
            if component_type == "fx" and path:
                suffix = Path(path).suffix.lower()
                if suffix and suffix not in FX_CACHE_EXTENSIONS:
                    note = (note + " " if note else "") + "FX cache should be abc/usd/usda/usdc."
            normalized.append(
                ConstructComponent(
                    component_type=component_type,
                    name=name,
                    version=version,
                    mode=mode,
                    namespace=str(raw.get("namespace") or "").strip(),
                    path=path,
                    required=_truthy(raw.get("required"), default=True),
                    enabled=_truthy(raw.get("enabled"), default=True),
                    note=note,
                    source=dict(raw.get("source") or {}) if isinstance(raw.get("source"), dict) else {},
                )
            )
        return normalized

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
        base_dir = self.shot_data_root(identity) / "animation" / clean_target / clean_subset
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
        base_dir = self.shot_data_root(identity) / "animation" / clean_target / clean_subset
        latest = read_json(base_dir / "latest.json", {}) or {}
        path = base_dir / str(latest.get("path") or "")
        if path.exists():
            return path
        legacy_base_dir = self.shot_publish_root(identity) / "animation" / clean_target / clean_subset
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
        base_dir = self.shot_data_root(identity) / "animation" / clean_target / clean_subset
        rows = self._list_animation_curve_versions_from_dir(base_dir, clean_target, clean_subset, legacy=False)
        legacy_base_dir = self.shot_publish_root(identity) / "animation" / clean_target / clean_subset
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
        base_dir = self.shot_publish_root(identity) / "animation" / clean_target / clean_subset
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

    def plan_animation_cache_publish(
        self,
        identity: ShotIdentity,
        *,
        target: str,
        subset: str = "cache",
    ) -> dict[str, Any]:
        clean_target = _clean_publish_token(target or "main")
        clean_subset = _clean_publish_token(subset or "cache")
        base_dir = self.shot_publish_root(identity) / "animation" / clean_target / clean_subset
        version = self._next_publish_version(base_dir)
        return {
            "target": clean_target,
            "subset": clean_subset,
            "version": version,
            "base_dir": base_dir,
            "version_dir": base_dir / version,
        }

    def finalize_animation_cache_publish(
        self,
        identity: ShotIdentity,
        export_result: dict[str, Any],
        *,
        target: str,
        asset: str = "",
        variant: str = "default",
        namespace: str = "",
        source_workfile: str | Path = "",
        curve_data_path: str | Path = "",
        rig_dependency: dict[str, Any] | None = None,
        comment: str = "",
        version: str,
        subset: str = "cache",
    ) -> Path:
        clean_target = _clean_publish_token(target or "main")
        clean_subset = _clean_publish_token(subset or "cache")
        base_dir = self.shot_publish_root(identity) / "animation" / clean_target / clean_subset
        version_dir = base_dir / version
        files = dict(export_result.get("files") or {})
        missing = [name for name in files.values() if not (version_dir / str(name)).exists()]
        if not files or missing:
            raise RuntimeError(f"Animation cache export is incomplete. Missing: {', '.join(missing) or 'files'}")

        if rig_dependency and str(export_result.get("usd_kind") or "") == "usd_skel_animation":
            animation_usd = version_dir / str(files.get("usd") or "")
            asset_usd = Path(self.project_config.project_root) / str(rig_dependency.get("path") or "")
            if animation_usd.is_file() and asset_usd.is_file():
                from smartlib.dcc.maya.animation_curves import (
                    rebase_skel_animation_to_asset,
                    validate_skel_animation_compatibility,
                )

                skeleton_bindings = rebase_skel_animation_to_asset(
                    asset_usd,
                    animation_usd,
                    list(export_result.get("skeleton_bindings") or []),
                )
                export_result["skeleton_bindings"] = skeleton_bindings

                usd_validation = validate_skel_animation_compatibility(
                    asset_usd,
                    animation_usd,
                    skeleton_bindings,
                )
                if not usd_validation["ok"]:
                    raise RuntimeError(
                        "USD Skel animation is incompatible with the Asset USD:\n- "
                        + "\n- ".join(usd_validation["errors"])
                    )
                export_result["usd_validation"] = usd_validation
                frame_range = list(export_result.get("frame_range") or self.shot_frame_range(identity))
                start, end = int(frame_range[0]), int(frame_range[1])
                shot_data = self.load_shot(identity)
                fps = int((shot_data.get("editorial") or {}).get("fps") or shot_data.get("fps") or self.project_fps)
                timing_path = version_dir / "timing.usda"
                timing_path.write_text(
                    "\n".join(
                        [
                            "#usda 1.0",
                            "(",
                            f"    startTimeCode = {start}",
                            f"    endTimeCode = {end}",
                            f"    framesPerSecond = {fps}",
                            f"    timeCodesPerSecond = {fps}",
                            ")",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
                binding_path = version_dir / "animation_binding.usda"
                binding_path.write_text(
                    _animation_binding_layer_text(export_result.get("skeleton_bindings") or []),
                    encoding="utf-8",
                )
                composed_path = version_dir / "animation_asset.usda"
                animation_ref = os.path.relpath(animation_usd, composed_path.parent).replace("\\", "/")
                asset_ref = os.path.relpath(asset_usd, composed_path.parent).replace("\\", "/")
                asset_default_prim = _usd_default_prim_name(asset_usd)
                composed_path.write_text(
                    "\n".join(
                        [
                            "#usda 1.0",
                            "(",
                            f'    defaultPrim = "{asset_default_prim}"',
                            f"    startTimeCode = {start}",
                            f"    endTimeCode = {end}",
                            f"    framesPerSecond = {fps}",
                            f"    timeCodesPerSecond = {fps}",
                            "    subLayers = [",
                            "        @animation_binding.usda@,",
                            f"        @{animation_ref}@,",
                            "        @timing.usda@,",
                            f"        @{asset_ref}@",
                            "    ]",
                            ")",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
                files["composed_usd"] = composed_path.name
                files["timing_usd"] = timing_path.name
                files["binding_usd"] = binding_path.name

        cache_data = {
            "schema": "smartpipeline.animation_cache.v1",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "target": clean_target,
            "subset": clean_subset,
            "asset": asset,
            "variant": variant or "default",
            "namespace": namespace or clean_target,
            "version": version,
            "frame_range": list(export_result.get("frame_range") or []),
            "source_set": str(export_result.get("source_set") or ""),
            "source_nodes": list(export_result.get("source_nodes") or []),
            "geometry": list(export_result.get("geometry") or []),
            "topology_signature": str(export_result.get("topology_signature") or ""),
            "files": files,
            "usd_kind": str(export_result.get("usd_kind") or "point_cache"),
            "source_skeleton_set": str(export_result.get("source_skeleton_set") or ""),
            "source_skeleton_roots": list(export_result.get("source_skeleton_roots") or []),
            "skeleton_bindings": list(export_result.get("skeleton_bindings") or []),
            "usd_validation": dict(export_result.get("usd_validation") or {}),
            "comment": comment,
        }
        if source_workfile:
            cache_data["source_workfile"] = self._relative_to_project(Path(source_workfile))
        curve_path = Path(curve_data_path) if curve_data_path else self.latest_animation_curve_path(
            identity,
            target=clean_target,
        )
        curve_dependency = self._animation_curve_dependency(curve_path)
        if curve_dependency:
            cache_data["curve_dependency"] = curve_dependency
        if rig_dependency:
            cache_data["rig_dependency"] = dict(rig_dependency)
            cache_data["asset_usd_dependency"] = dict(rig_dependency)
        cache_path = write_json(version_dir / "cache.json", cache_data)
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": "animation",
                "target": clean_target,
                "subset": clean_subset,
                "version": version,
                "files": {**files, "cache": "cache.json"},
                "asset_dependency": {
                    "asset": asset,
                    "variant": variant or "default",
                    "namespace": namespace or clean_target,
                },
                "curve_dependency": curve_dependency,
                "rig_dependency": dict(rig_dependency or {}),
                "asset_usd_dependency": dict(rig_dependency or {}),
                "usd_kind": cache_data["usd_kind"],
                "topology_signature": cache_data["topology_signature"],
                "source_workfile": cache_data.get("source_workfile", ""),
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/cache.json"})
        self._update_versions(base_dir / "versions.json", version)
        return cache_path

    def resolve_asset_rig_usd_dependency(
        self,
        asset: str,
        variant: str = "default",
        subset: str = "anim",
        *,
        preferred_context: str = "work",
    ) -> dict[str, str]:
        """Resolve the composed Asset USD entry, falling back to the Rig USD layer."""

        asset_root = self.find_asset_root(asset)
        if not asset_root:
            return {}
        variant_root = asset_root / (variant or "default")
        context_order = []
        for context_subset in (preferred_context, "work", "anim", "final", "fast"):
            clean_context = str(context_subset or "").strip().lower()
            if clean_context and clean_context not in context_order:
                context_order.append(clean_context)
        for context_subset in context_order:
            asset_base = variant_root / "publish" / "asset" / context_subset
            latest_asset = read_json(asset_base / "latest.json", {}) or {}
            asset_version = str(latest_asset.get("version") or "")
            asset_candidate = asset_base / str(latest_asset.get("usd") or latest_asset.get("path") or "")
            if not asset_candidate.is_file() and asset_version:
                asset_candidate = asset_base / asset_version / "asset.usda"
            if not asset_candidate.is_file() and asset_version:
                asset_candidate = asset_base / asset_version / "asset.usd"
            if asset_candidate.is_file() and asset_candidate.suffix.lower() in {".usd", ".usda", ".usdc"}:
                publish_data = read_json(asset_candidate.parent / "publish.json", {}) or {}
                return {
                    "asset": asset,
                    "variant": variant or "default",
                    "publish_type": "asset",
                    "subset": context_subset,
                    "version": str(publish_data.get("version") or asset_version),
                    "path": self._relative_to_project(asset_candidate),
                    "root_joint": str(((publish_data.get("usd_skel") or {}).get("root_joint")) or ""),
                    "fallback": "false",
                }
        base_dir = variant_root / "publish" / "rig" / (subset or "anim")
        latest = read_json(base_dir / "latest.json", {}) or {}
        version = str(latest.get("version") or "")
        candidate = base_dir / str(latest.get("path") or "")
        if not candidate.is_file() and version:
            candidate = base_dir / version / "rig.usd"
        if not candidate.is_file():
            versions = sorted(path for path in base_dir.glob("v[0-9]*") if path.is_dir())
            candidate = versions[-1] / "rig.usd" if versions else Path()
            version = versions[-1].name if versions else ""
        if not candidate.is_file() or candidate.suffix.lower() not in {".usd", ".usda", ".usdc"}:
            return {}
        publish_data = read_json(candidate.parent / "publish.json", {}) or {}
        dependency_version = str(publish_data.get("version") or version)
        if dependency_version.isdigit():
            dependency_version = f"v{int(dependency_version):03d}"
        return {
            "asset": asset,
            "variant": variant or "default",
            "publish_type": "rig",
            "subset": subset or "anim",
            "version": dependency_version,
            "path": self._relative_to_project(candidate),
            "root_joint": str((publish_data.get("usd_skel") or {}).get("root_joint") or ""),
            "fallback": "true",
        }

    def _animation_curve_dependency(self, curve_path: Path | None) -> dict[str, str]:
        if not curve_path or not curve_path.is_file():
            return {}
        curve_data = read_json(curve_path, {}) or {}
        return {
            "version": str(curve_data.get("version") or curve_path.parent.name),
            "path": self._relative_to_project(curve_path),
        }

    def list_animation_cache_versions(
        self,
        identity: ShotIdentity,
        *,
        target: str = "main",
        subset: str = "cache",
    ) -> list[ShotDataVersion]:
        animation_root = self.shot_publish_root(identity) / "animation"
        clean_subset = _clean_publish_token(subset or "cache")
        if target:
            base_dirs = [animation_root / _clean_publish_token(target) / clean_subset]
        else:
            base_dirs = sorted(animation_root.glob(f"*/{clean_subset}"))
        rows = []
        for base_dir in base_dirs:
            clean_target = base_dir.parent.name
            latest = read_json(base_dir / "latest.json", {}) or {}
            latest_version = str(latest.get("version") or "")
            for version_dir in base_dir.glob("v*"):
                cache_path = version_dir / "cache.json"
                if not version_dir.is_dir() or not cache_path.exists():
                    continue
                metadata = read_json(version_dir / "publish.json", {}) or {}
                updated = datetime.fromtimestamp(cache_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                rows.append(
                    ShotDataVersion(
                        name=f"publish/animation/{clean_target}/{clean_subset}",
                        version=version_dir.name,
                        path=str(cache_path),
                        updated=updated,
                        comment=str(metadata.get("comment") or ""),
                        latest=version_dir.name == latest_version,
                    )
                )
        return sorted(rows, key=lambda row: parse_version(row.version) or 0, reverse=True)

    def build_animation_package_snapshot(
        self,
        identity: ShotIdentity,
        *,
        comment: str = "",
        preferred_format: str = "abc",
    ) -> Path:
        cast_data = self.load_cast(identity)
        cast_entries = cast_data.get("cast") or {}
        resolved: dict[str, Any] = {}
        missing: list[dict[str, str]] = []
        for cast_key, entry in sorted(cast_entries.items()):
            clean_target = _clean_publish_token(cast_key)
            cache_root = self.shot_publish_root(identity) / "animation" / clean_target / "cache"
            latest = read_json(cache_root / "latest.json", {}) or {}
            cache_path = cache_root / str(latest.get("path") or "")
            if not cache_path.exists():
                missing.append(
                    {
                        "cast_key": cast_key,
                        "asset": str(entry.get("asset") or ""),
                        "reason": "animation cache latest was not found",
                    }
                )
                continue
            cache = read_json(cache_path, {}) or {}
            files = dict(cache.get("files") or {})
            version_dir = cache_path.parent
            fixed_files = {}
            for file_type in ("abc", "usd"):
                candidate = version_dir / str(files.get(file_type) or "")
                if candidate.is_file():
                    fixed_files[file_type] = self._relative_to_project(candidate)
            if not fixed_files:
                missing.append(
                    {
                        "cast_key": cast_key,
                        "asset": str(entry.get("asset") or ""),
                        "reason": f"cache files were not found in {cache_path.parent}",
                    }
                )
                continue
            resolved[cast_key] = {
                "asset": str(entry.get("asset") or cache.get("asset") or ""),
                "variant": str(entry.get("variant") or cache.get("variant") or "default"),
                "namespace": str(entry.get("namespace") or cache.get("namespace") or cast_key),
                "cache_version": str(cache.get("version") or latest.get("version") or version_dir.name),
                "cache_metadata": self._relative_to_project(cache_path),
                "files": fixed_files,
                "topology_signature": str(cache.get("topology_signature") or ""),
                "curve_dependency": dict(cache.get("curve_dependency") or {}),
            }
        if not resolved:
            raise RuntimeError(
                "No published animation caches were found for this shot. "
                "Publish Cache for at least one cast first."
            )

        base_dir = self.shot_publish_root(identity) / "animation" / "package" / "main"
        version = self._next_publish_version(base_dir)
        version_dir = base_dir / version
        version_dir.mkdir(parents=True, exist_ok=True)
        frame_range = self.shot_frame_range(identity)
        preferred = str(preferred_format or "abc").lower()
        if preferred not in {"abc", "usd"}:
            preferred = "abc"
        manifest = {
            "schema": "smartpipeline.animation_package.v1",
            "package_type": "animation",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "version": version,
            "frame_range": list(frame_range),
            "preferred_format": preferred,
            "casts": resolved,
            "missing_casts": missing,
            "comment": comment,
        }
        manifest_path = write_json(version_dir / "animation_manifest.json", manifest)
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": "animation",
                "target": "package",
                "subset": "main",
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "version": version,
                "files": {"manifest": "animation_manifest.json"},
                "preferred_format": preferred,
                "cast_versions": {
                    cast_key: data["cache_version"]
                    for cast_key, data in resolved.items()
                },
                "curve_data_versions": {
                    cast_key: data["curve_dependency"].get("version", "")
                    for cast_key, data in resolved.items()
                },
                "missing_casts": [item["cast_key"] for item in missing],
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/animation_manifest.json"})
        self._update_versions(base_dir / "versions.json", version)
        return manifest_path

    def list_animation_package_versions(self, identity: ShotIdentity) -> list[ShotDataVersion]:
        base_dir = self.shot_publish_root(identity) / "animation" / "package" / "main"
        latest = read_json(base_dir / "latest.json", {}) or {}
        latest_version = str(latest.get("version") or "")
        rows = []
        for version_dir in base_dir.glob("v*"):
            manifest_path = version_dir / "animation_manifest.json"
            if not version_dir.is_dir() or not manifest_path.exists():
                continue
            metadata = read_json(version_dir / "publish.json", {}) or {}
            updated = datetime.fromtimestamp(manifest_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            rows.append(
                ShotDataVersion(
                    name="publish/animation/package/main",
                    version=version_dir.name,
                    path=str(manifest_path),
                    updated=updated,
                    comment=str(metadata.get("comment") or ""),
                    latest=version_dir.name == latest_version,
                )
            )
        return sorted(rows, key=lambda row: parse_version(row.version) or 0, reverse=True)

    def latest_animation_package_path(self, identity: ShotIdentity) -> Path | None:
        base_dir = self.shot_publish_root(identity) / "animation" / "package" / "main"
        latest = read_json(base_dir / "latest.json", {}) or {}
        path = base_dir / str(latest.get("path") or "")
        return path if path.is_file() else None

    def animation_review_build_plan(
        self,
        identity: ShotIdentity,
        *,
        package_path: str | Path | None = None,
    ) -> dict[str, Any]:
        manifest_path = Path(package_path) if package_path else self.latest_animation_package_path(identity)
        if not manifest_path or not manifest_path.is_file():
            raise RuntimeError("Animation Package was not found. Build Package first.")
        manifest = read_json(manifest_path, {}) or {}
        if manifest.get("schema") != "smartpipeline.animation_package.v1":
            raise RuntimeError(f"Unsupported Animation Package: {manifest_path}")

        package_version = str(manifest.get("version") or manifest_path.parent.name)
        _review_spec, review_spec_path = self.resolved_review_spec(
            identity,
            department="anim",
        )
        camera_paths = self._latest_review_camera_paths(identity)
        set_dress_paths = [
            row.path
            for row in self.list_set_dress_publish_versions(identity)
            if row.latest and Path(row.path).is_file()
        ]
        output = self.output_resolver.resolve(
            "animation_review_scene",
            {
                "shot_root": self.shot_root(identity).as_posix(),
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "department": "animation",
                "version": package_version,
                "ext": "mb",
            },
            default_directory="{shot_root}/output/review/animation/{version}",
            default_filename="{shot}_animation_review_{version}.mb",
        )
        output_dir = output.directory
        return {
            "schema": "smartpipeline.animation_review_build.v1",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "package_version": package_version,
            "animation_manifest": str(manifest_path),
            "review_spec": str(review_spec_path),
            "preferred_format": str(manifest.get("preferred_format") or "abc"),
            "frame_range": list(manifest.get("frame_range") or self.shot_frame_range(identity)),
            "camera_paths": camera_paths,
            "set_dress_paths": set_dress_paths,
            "output_dir": str(output_dir),
            "scene_path": str(output.path),
        }

    def write_animation_review_build_manifest(
        self,
        plan: dict[str, Any],
        result: dict[str, Any],
    ) -> Path:
        output_dir = Path(str(plan["output_dir"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(plan)
        payload.update(
            {
                "built_at": datetime.now().isoformat(timespec="seconds"),
                "result": result,
            }
        )
        return write_json(output_dir / "build_manifest.json", payload)

    def _latest_review_camera_paths(self, identity: ShotIdentity) -> list[str]:
        camera_root = self.shot_publish_root(identity) / "camera"
        by_target: dict[str, Path] = {}
        for latest_path in camera_root.glob("*/*/latest.json") if camera_root.exists() else []:
            latest = read_json(latest_path, {}) or {}
            path = latest_path.parent / str(latest.get("path") or "")
            if path.is_file():
                by_target[latest_path.parent.parent.name] = path
        preferred = {
            target: path
            for target, path in by_target.items()
            if target.lower().startswith("cam_")
        }
        selected = preferred or by_target
        if not selected:
            sequence_camera = self._latest_shot_camera_publish(identity)
            if sequence_camera:
                selected["main"] = sequence_camera
        return [str(path) for _target, path in sorted(selected.items())]

    def published_animation_source_paths(self, identity: ShotIdentity) -> set[str]:
        root = self.shot_publish_root(identity) / "animation"
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
        return self._list_data_versions(self.shot_data_root(identity))

    def list_set_dress_data(
        self,
        identity: ShotIdentity,
    ) -> list[ShotDataVersion]:
        root = self.shot_data_root(identity) / "setdress"
        rows = []
        for path in root.glob("*.setdress.json") if root.is_dir() else []:
            rows.append(
                ShotDataVersion(
                    name=f"set_dress_data/{path.name.removesuffix('.setdress.json')}",
                    version="WORK",
                    path=str(path),
                    updated=datetime.fromtimestamp(path.stat().st_mtime).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    comment="editable work data",
                    latest=True,
                )
            )
        return sorted(rows, key=lambda row: row.name.lower())

    def list_set_dress_publish_versions(self, identity: ShotIdentity) -> list[ShotDataVersion]:
        from smartlib.setdress import SetDressIdentity, SetDressPublishService

        service = SetDressPublishService(self.project_config)
        rows = service.list_versions(
            SetDressIdentity(identity.episode, identity.sequence, identity.shot)
        )
        return [
            ShotDataVersion(
                name=f"set_dress/{row.package}",
                version=row.version,
                path=row.path,
                updated=row.updated.replace("T", " ")[:16],
                comment=row.comment,
                latest=row.latest,
            )
            for row in rows
        ]

    def list_placement_publish_versions(
        self,
        identity: ShotIdentity,
    ) -> list[ShotDataVersion]:
        base_dir = self.shot_publish_root(identity) / "layout" / "placements"
        latest = read_json(base_dir / "latest.json", {}) or {}
        latest_version = str(latest.get("version") or "")
        rows = []
        for version_dir in base_dir.glob("v*") if base_dir.is_dir() else []:
            if not version_dir.is_dir() or parse_version(version_dir.name) is None:
                continue
            path = version_dir / "placements.json"
            if not path.is_file():
                continue
            metadata = read_json(version_dir / "publish.json", {}) or {}
            rows.append(
                ShotDataVersion(
                    name="placements/main",
                    version=version_dir.name,
                    path=str(path),
                    updated=datetime.fromtimestamp(path.stat().st_mtime).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    comment=str(metadata.get("comment") or ""),
                    latest=version_dir.name == latest_version,
                )
            )
        return sorted(
            rows,
            key=lambda row: parse_version(row.version) or 0,
            reverse=True,
        )

    def list_sequence_set_dress_publish_versions(
        self, identity: SequenceIdentity
    ) -> list[ShotDataVersion]:
        from smartlib.setdress import SetDressIdentity, SetDressPublishService

        rows = SetDressPublishService(self.project_config).list_versions(
            SetDressIdentity(identity.episode, identity.sequence), scope="sequence"
        )
        return [
            ShotDataVersion(
                name=f"set_dress/{row.package}",
                version=row.version,
                path=row.path,
                updated=row.updated.replace("T", " ")[:16],
                comment=row.comment,
                latest=row.latest,
            )
            for row in rows
        ]

    def export_shot_scene_data(
        self,
        identity: ShotIdentity,
        data_type: str,
        payload: dict[str, Any],
        *,
        target: str = "main",
        subset: str = "main",
        filename: str | None = None,
        source_workfile: str | Path = "",
        comment: str = "",
    ) -> Path:
        """Write a DCC scene component as versioned shot data."""
        clean_type = _clean_publish_token(data_type)
        if clean_type not in {"camera", "light", "playblast_settings"}:
            raise ValueError(f"Unsupported shot scene data type: {data_type}")
        clean_target = _clean_publish_token(target or "main")
        clean_subset = _clean_publish_token(subset or "main")
        output_name = filename or f"{clean_type}.json"
        base_dir = self.shot_data_root(identity) / clean_type / clean_target / clean_subset
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)

        data = dict(payload)
        data.update(
            {
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "data_type": clean_type,
                "target": clean_target,
                "subset": clean_subset,
                "version": version_label,
                "comment": comment,
            }
        )
        if source_workfile:
            data["source_workfile"] = self._relative_to_project(Path(source_workfile))
        output_path = write_json(version_dir / output_name, data)
        write_json(
            version_dir / "data.json",
            {
                "data_type": clean_type,
                "target": clean_target,
                "subset": clean_subset,
                "version": version_label,
                "files": {clean_type: output_name},
                "source_workfile": data.get("source_workfile", ""),
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/{output_name}"})
        self._update_versions(base_dir / "versions.json", version_label)
        return output_path

    def export_sequence_scene_data(
        self,
        identity: SequenceIdentity,
        data_type: str,
        payload: dict[str, Any],
        *,
        department: str = "layout",
        target: str = "main",
        subset: str = "main",
        filename: str | None = None,
        source_workfile: str | Path = "",
        comment: str = "",
    ) -> Path:
        """Write a root-based Camera/Light package as sequence Data Publish."""

        clean_type = _clean_publish_token(data_type)
        if clean_type not in {"camera", "light", "playblast_settings"}:
            raise ValueError(f"Unsupported sequence scene data type: {data_type}")
        clean_target = _clean_publish_token(target or "main")
        clean_subset = _clean_publish_token(subset or "main")
        clean_department = _clean_publish_token(department or "layout")
        base_dir = (
            self.sequence_workspace_root(identity.episode, identity.sequence)
            / clean_department / "data" / clean_type / clean_target / clean_subset
        )
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)
        output_name = filename or f"{clean_type}.json"
        data = dict(payload)
        data.update({
            "episode": identity.episode,
            "sequence": identity.sequence,
            "scope": "sequence",
            "department": clean_department,
            "data_type": clean_type,
            "target": clean_target,
            "subset": clean_subset,
            "version": version_label,
            "comment": comment,
        })
        if source_workfile:
            data["source_workfile"] = self._relative_to_project(Path(source_workfile))
        output_path = write_json(version_dir / output_name, data)
        write_json(version_dir / "data.json", {
            "data_type": clean_type,
            "scope": "sequence",
            "department": clean_department,
            "target": clean_target,
            "subset": clean_subset,
            "version": version_label,
            "files": {clean_type: output_name},
            "source_workfile": data.get("source_workfile", ""),
            "comment": comment,
        })
        write_json(base_dir / "latest.json", {
            "version": version_label, "path": f"{version_label}/{output_name}"
        })
        self._update_versions(base_dir / "versions.json", version_label)
        return output_path

    def register_scene_data_files(
        self,
        data_path: str | Path,
        files: dict[str, str],
        *,
        errors: dict[str, str] | None = None,
    ) -> Path:
        """Register native files exported beside a Camera/Light data manifest."""

        path = Path(data_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        for metadata_path in (path, path.parent / "data.json"):
            data = read_json(metadata_path, {}) or {}
            merged = dict(data.get("files") or {})
            merged.update({str(key): str(value) for key, value in files.items()})
            data["files"] = merged
            if errors:
                data["export_errors"] = dict(errors)
            write_json(metadata_path, data)
        return path

    def publish_shot_scene_data(
        self,
        identity: ShotIdentity,
        data_path: str | Path,
        *,
        data_type: str,
        target: str = "main",
        subset: str = "main",
        comment: str = "",
    ) -> Path:
        """Publish an immutable camera or render-layer snapshot from shot data."""
        source_path = Path(data_path)
        if not source_path.exists():
            raise FileNotFoundError(f"Shot data was not found: {source_path}")
        clean_type = _clean_publish_token(data_type)
        if clean_type not in {"camera", "light"}:
            raise ValueError(f"Unsupported shot scene data type: {data_type}")
        clean_target = _clean_publish_token(target or "main")
        clean_subset = _clean_publish_token(subset or "main")
        source_data = read_json(source_path, {}) or {}
        base_dir = self.shot_publish_root(identity) / clean_type / clean_target / clean_subset
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)

        published_data = dict(source_data)
        published_data.update(
            {
                "publish_type": clean_type,
                "target": clean_target,
                "subset": clean_subset,
                "version": version_label,
                "source_data": self._relative_to_project(source_path),
                "comment": comment,
                "published_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        output_path = write_json(version_dir / source_path.name, published_data)
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": clean_type,
                "target": clean_target,
                "subset": clean_subset,
                "version": version_label,
                "files": {clean_type: source_path.name},
                "source_data": self._relative_to_project(source_path),
                "source_workfile": source_data.get("source_workfile", ""),
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/{source_path.name}"})
        self._update_versions(base_dir / "versions.json", version_label)
        return output_path

    def publish_shot_scene_snapshot(
        self,
        identity: ShotIdentity,
        payload: dict[str, Any],
        *,
        data_type: str,
        target: str = "main",
        subset: str = "main",
        source_workfile: str | Path = "",
        comment: str = "",
    ) -> Path:
        """Publish a camera or render-layer snapshot directly from a DCC scene."""
        clean_type = _clean_publish_token(data_type)
        if clean_type not in {"camera", "light"}:
            raise ValueError(f"Unsupported shot scene publish type: {data_type}")
        clean_target = _clean_publish_token(target or "main")
        clean_subset = _clean_publish_token(subset or "main")
        base_dir = self.shot_publish_root(identity) / clean_type / clean_target / clean_subset
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)
        output_name = f"{clean_type}.json"

        published_data = dict(payload)
        published_data.update(
            {
                "publish_type": clean_type,
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "target": clean_target,
                "subset": clean_subset,
                "version": version_label,
                "comment": comment,
                "published_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if source_workfile:
            published_data["source_workfile"] = self._relative_to_project(Path(source_workfile))
        output_path = write_json(version_dir / output_name, published_data)
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": clean_type,
                "target": clean_target,
                "subset": clean_subset,
                "version": version_label,
                "files": {clean_type: output_name},
                "source_workfile": published_data.get("source_workfile", ""),
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/{output_name}"})
        self._update_versions(base_dir / "versions.json", version_label)
        return output_path

    def register_shot_scene_publish_files(
        self,
        snapshot_path: str | Path,
        files: dict[str, str],
        *,
        errors: dict[str, str] | None = None,
    ) -> Path:
        """Register DCC-native files exported beside a scene snapshot."""

        snapshot = Path(snapshot_path)
        if not snapshot.is_file():
            raise FileNotFoundError(snapshot)
        snapshot_data = read_json(snapshot, {}) or {}
        snapshot_files = dict(snapshot_data.get("files") or {})
        snapshot_files.update({str(key): str(value) for key, value in files.items()})
        snapshot_data["files"] = snapshot_files
        if errors:
            snapshot_data["export_errors"] = dict(errors)
        write_json(snapshot, snapshot_data)

        publish_path = snapshot.parent / "publish.json"
        publish_data = read_json(publish_path, {}) or {}
        publish_files = dict(publish_data.get("files") or {})
        publish_files.update(snapshot_files)
        publish_data["files"] = publish_files
        if errors:
            publish_data["export_errors"] = dict(errors)
        return write_json(publish_path, publish_data)

    def list_shot_scene_publish_versions(
        self,
        identity: ShotIdentity,
        data_type: str,
    ) -> list[ShotDataVersion]:
        clean_type = _clean_publish_token(data_type)
        publish_root = self.shot_publish_root(identity) / clean_type
        rows: list[ShotDataVersion] = []
        if not publish_root.exists():
            return rows
        for latest_json in publish_root.glob("*/*/latest.json"):
            base_dir = latest_json.parent
            latest = read_json(latest_json, {}) or {}
            latest_version = str(latest.get("version") or "")
            try:
                target = base_dir.parent.name
                subset = base_dir.name
            except Exception:
                continue
            for version_dir in base_dir.glob("v*"):
                if not version_dir.is_dir() or not version_dir.name[1:].isdigit():
                    continue
                data_path = version_dir / f"{clean_type}.json"
                if not data_path.exists():
                    continue
                metadata = read_json(version_dir / "publish.json", {}) or {}
                updated = ""
                try:
                    updated = datetime.fromtimestamp(data_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
                rows.append(
                    ShotDataVersion(
                        name=f"{clean_type}/{target}/{subset}",
                        version=version_dir.name,
                        path=str(data_path),
                        updated=updated,
                        comment=str(metadata.get("comment") or ""),
                        latest=version_dir.name == latest_version,
                    )
                )
        return sorted(rows, key=lambda row: (row.name, parse_version(row.version)), reverse=True)

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
            metadata = read_json(version_dir / "data.json", {}) or {}
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
                    comment=str(metadata.get("comment") or ", ".join(files)),
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
        shot_cast = self.load_cast(identity)
        sequence_cast = self.load_sequence_cast(identity.episode, identity.sequence)
        cast_ready = bool((shot_cast.get("cast") or {}) or (sequence_cast.get("cast") or {}))
        camera_publish = self._latest_work_stage_camera(identity)
        placements_publish = self._latest_work_stage_placements(identity)
        layout_overlay = self._latest_layout_overlay_usd(identity)
        statuses = [
            self._status_from_file(
                "cast",
                self.shot_root(identity) / "cast.json",
                exists=cast_ready,
                message="No shot or sequence cast entries were found.",
            ),
            LayoutPublishStatusItem(
                name="placements",
                state="READY" if placements_publish else "MISSING",
                version=placements_publish.parent.name if placements_publish else "",
                path=str(placements_publish or self.shot_data_root(identity) / "placements"),
                message="" if placements_publish else "Shot Placement Publish/Data was not found.",
            ),
            LayoutPublishStatusItem(
                name="camera",
                state="READY" if camera_publish else "MISSING",
                version=camera_publish.parent.name if camera_publish else "",
                path=str(camera_publish or self.shot_data_root(identity) / "camera"),
                message="" if camera_publish else "Shot Camera Data Publish was not found.",
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
        overrides: dict[str, Any] | None = None,
    ) -> AnimInputBuildResult:
        statuses = self.shot_anim_input_status(identity)
        blocking = [item for item in statuses if item.state == "MISSING" and item.name != "layout_overlay"]
        if str((overrides or {}).get("camera") or "").strip():
            blocking = [item for item in blocking if item.name != "camera"]
        use_placements = (overrides or {}).get("use_placements") is not False
        if not use_placements:
            blocking = [item for item in blocking if item.name != "placements"]
        if blocking:
            names = ", ".join(item.name for item in blocking)
            raise RuntimeError(f"WORK STAGE inputs are blocked by missing shot data: {names}")
        shot_data = self.load_shot(identity)
        sequence_data = sequence_data or self.load_sequence(SequenceIdentity(identity.episode, identity.sequence))
        cast_publish = self.publish_shot_cast_from_sequence(identity, comment=comment)
        placements_publish = self._latest_work_stage_placements(identity) if use_placements else None
        if placements_publish and (overrides or {}).get("layout_overlay") is not False:
            self.publish_shot_layout_overlay(
                identity,
                cast_publish=cast_publish,
                placements_publish=placements_publish,
                shot_data=shot_data,
                sequence_data=sequence_data,
                comment=comment,
            )
        anim_input = self.publish_shot_anim_input(
            identity,
            cast_publish=cast_publish,
            placements_publish=placements_publish,
            shot_data=shot_data,
            sequence_data=sequence_data,
            comment=comment,
            overrides=overrides,
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
        base_dir = self.shot_publish_root(identity) / "cast" / "main"
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
        base_dir = self.shot_publish_root(identity) / "layout" / "placements"
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

    def publish_shot_layout_overlay(
        self,
        identity: ShotIdentity,
        *,
        cast_publish: Path,
        placements_publish: Path | None,
        shot_data: dict[str, Any],
        sequence_data: dict[str, Any],
        comment: str = "",
    ) -> Path:
        base_dir = self.shot_publish_root(identity) / "layout" / "proxy"
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)
        layout_path = version_dir / "layout.usda"
        editorial = (shot_data.get("editorial") or {}) if shot_data else {}
        sequence_editorial = sequence_data.get("editorial") or {}
        camera_publish = self._latest_work_stage_camera(identity)
        cast_data = read_json(cast_publish, {}) or {}
        cache_paths = self.publish_shot_layout_proxy_caches(
            identity,
            cast_data=cast_data,
            shot_data=shot_data,
            sequence_data=sequence_data,
            comment=comment,
        )
        cache_prims = []
        cache_references = {}
        for target, cache_path in cache_paths.items():
            relative_cache = self._relative_path(layout_path.parent, cache_path)
            virtual_cache = self._shot_virtual_path(cache_path)
            cache_references[target] = {
                "virtual": virtual_cache,
                "resolved": relative_cache,
                "project_path": self._relative_to_project(cache_path),
            }
            prim_name = self._usd_identifier(target)
            cache_prims.extend(
                [
                    f'    def Xform "{prim_name}" (',
                    f"        references = @{relative_cache}@",
                    "    )",
                    "    {",
                    f'        custom string target = "{target}"',
                    "    }",
                ]
            )
        content = "\n".join(
            [
                "#usda 1.0",
                "(",
                '    defaultPrim = "layout"',
                ")",
                "",
                'def Xform "layout"',
                "{",
                '    custom string smartpipeline_publish_type = "layout"',
                '    custom string smartpipeline_subset = "proxy"',
                '    custom string smartpipeline_usage = "anim_reference_overlay"',
                f'    custom string episode = "{identity.episode}"',
                f'    custom string sequence = "{identity.sequence}"',
                f'    custom string shot = "{identity.shot}"',
                f'    custom string version = "{version_label}"',
                f'    custom string cast = "{self._relative_to_project(cast_publish)}"',
                f'    custom string placements = "{self._relative_to_project(placements_publish)}"',
                f'    custom string camera = "{self._relative_to_project(camera_publish) if camera_publish else ""}"',
                f'    custom string fps = "{editorial.get("fps") or sequence_editorial.get("fps") or self.project_fps}"',
                f'    custom string cut_in = "{editorial.get("cut_in", "")}"',
                f'    custom string cut_out = "{editorial.get("cut_out", "")}"',
                *cache_prims,
                "}",
                "",
            ]
        )
        layout_path.write_text(content, encoding="utf-8")
        write_json(
            version_dir / "publish.json",
            {
                "publish_type": "layout",
                "subset": "proxy",
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
                "version": version_label,
                "files": {"usd": "layout.usda"},
                "usage": "anim_reference_overlay",
                "source_cast": self._relative_to_project(cast_publish),
                "source_placements": self._relative_to_project(placements_publish),
                "source_camera": self._relative_to_project(camera_publish) if camera_publish else "",
                "caches": {target: self._relative_to_project(path) for target, path in cache_paths.items()},
                "references": cache_references,
                "comment": comment,
            },
        )
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/layout.usda"})
        self._update_versions(base_dir / "versions.json", version_label)
        return layout_path

    def publish_shot_layout_proxy_caches(
        self,
        identity: ShotIdentity,
        *,
        cast_data: dict[str, Any],
        shot_data: dict[str, Any],
        sequence_data: dict[str, Any],
        comment: str = "",
    ) -> dict[str, Path]:
        caches: dict[str, Path] = {}
        editorial = (shot_data.get("editorial") or {}) if shot_data else {}
        sequence_editorial = sequence_data.get("editorial") or {}
        for target, entry in sorted((cast_data.get("cast") or {}).items()):
            if not self._is_character_layout_cache_entry(entry):
                continue
            clean_target = self._clean_publish_name(target)
            base_dir = self.shot_data_root(identity) / "layout" / "proxy" / clean_target / "cache"
            version_label = self._next_publish_version(base_dir)
            version_dir = base_dir / version_label
            version_dir.mkdir(parents=True, exist_ok=True)
            cache_path = version_dir / f"{clean_target}.usd"
            frame_range = self._layout_cache_frame_range(editorial, sequence_editorial)
            content = "\n".join(
                [
                    "#usda 1.0",
                    "(",
                    f'    defaultPrim = "{self._usd_identifier(clean_target)}"',
                    ")",
                    "",
                    f'def Xform "{self._usd_identifier(clean_target)}"',
                    "{",
                    '    custom string smartpipeline_data_type = "layout_cache"',
                    '    custom string smartpipeline_subset = "proxy"',
                    f'    custom string episode = "{identity.episode}"',
                    f'    custom string sequence = "{identity.sequence}"',
                    f'    custom string shot = "{identity.shot}"',
                    f'    custom string target = "{target}"',
                    f'    custom string asset = "{entry.get("asset", "")}"',
                    f'    custom string variant = "{entry.get("variant", "default")}"',
                    f'    custom string namespace = "{entry.get("namespace", target)}"',
                    f'    custom string fps = "{editorial.get("fps") or sequence_editorial.get("fps") or self.project_fps}"',
                    f'    custom string cut_in = "{editorial.get("cut_in", "")}"',
                    f'    custom string cut_out = "{editorial.get("cut_out", "")}"',
                    "}",
                    "",
                ]
            )
            cache_path.write_text(content, encoding="utf-8")
            export_result = self._export_maya_layout_cache(
                namespace=str(entry.get("namespace") or target),
                cache_path=cache_path,
                frame_range=frame_range,
            )
            write_json(
                version_dir / "data.json",
                {
                    "data_type": "layout_cache",
                    "subset": "proxy",
                    "episode": identity.episode,
                    "sequence": identity.sequence,
                    "shot": identity.shot,
                    "target": target,
                    "version": version_label,
                    "files": {"usd": cache_path.name},
                    "asset": entry.get("asset", ""),
                    "variant": entry.get("variant", "default"),
                    "namespace": entry.get("namespace", target),
                    "frame_range": list(frame_range) if frame_range else [],
                    "export_status": export_result.get("export_status", "placeholder"),
                    "export_error": export_result.get("export_error", ""),
                    "export_content": "transform_geometry_only",
                    "export_source_set": f"{entry.get('namespace', target)}:rig_geo_grp",
                    "source_nodes": export_result.get("source_nodes", []),
                    "comment": comment,
                },
            )
            write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/{cache_path.name}"})
            self._update_versions(base_dir / "versions.json", version_label)
            caches[target] = cache_path
        return caches

    @staticmethod
    def _is_character_layout_cache_entry(entry: dict[str, Any]) -> bool:
        role = _normalize_role(entry.get("role") or "")
        return role in {"CHA", "CHB", "CHARACTER"}

    @staticmethod
    def _layout_cache_frame_range(editorial: dict[str, Any], sequence_editorial: dict[str, Any]) -> tuple[int, int] | None:
        cut_in = editorial.get("cut_in", sequence_editorial.get("cut_in"))
        cut_out = editorial.get("cut_out", sequence_editorial.get("cut_out"))
        try:
            return int(cut_in), int(cut_out)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _export_maya_layout_cache(namespace: str, cache_path: Path, frame_range: tuple[int, int] | None) -> dict[str, Any]:
        try:
            from smartlib.dcc.maya.layout_cache import export_layout_cache_for_cast
        except Exception as exc:
            return {
                "export_status": "skipped",
                "export_error": f"Layout cache exporter is unavailable: {exc}",
                "source_nodes": [],
            }
        return export_layout_cache_for_cast(
            namespace=namespace,
            output_path=cache_path,
            frame_range=frame_range,
        )

    def publish_shot_anim_input(
        self,
        identity: ShotIdentity,
        *,
        cast_publish: Path,
        placements_publish: Path,
        shot_data: dict[str, Any],
        sequence_data: dict[str, Any],
        comment: str = "",
        overrides: dict[str, Any] | None = None,
    ) -> Path:
        base_dir = self.shot_publish_root(identity) / "anim_input" / "main"
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=True)
        editorial = (shot_data.get("editorial") or {}) if shot_data else {}
        sequence_editorial = sequence_data.get("editorial") or {}
        camera_publish = self._latest_work_stage_camera(identity)
        layout_overlay = self._latest_layout_overlay_usd(identity)
        context_profile = str((overrides or {}).get("context") or "WORK").strip().upper()
        representation = str(
            (overrides or {}).get("representation") or "project"
        ).strip().lower()
        if representation in {"", "project", "default"}:
            try:
                representation = str(
                    (
                        self.project_config.load("templates_assets.yml").get(
                            "workspace_load_policy"
                        ) or {}
                    ).get("representation") or "maya"
                ).strip().lower()
            except Exception:
                representation = "maya"
        context_usd = None
        context_version = ""
        if representation == "usd":
            context_rows = self.shot_context_components(
                identity,
                department="anim",
                profile=context_profile,
                cast_contexts=(overrides or {}).get("cast_contexts") or {},
                exclude_cast=(overrides or {}).get("exclude_cast") or [],
            )
            if context_rows:
                self.build_shot_context(
                    identity,
                    department="anim",
                    profile=context_profile,
                    components=context_rows,
                    comment=comment or "Generated with anim input package",
                )
            context_usd, context_version = self.latest_shot_context(
                identity,
                department="anim",
                profile=context_profile,
            )
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
            "placements": self._relative_to_project(placements_publish) if placements_publish else "",
            "placement_usage": "apply" if placements_publish else "disabled",
            "camera": self._relative_to_project(camera_publish) if camera_publish else "",
            "layout_overlay": self._relative_to_project(layout_overlay) if layout_overlay else "",
            "layout_overlay_usage": "reference_only",
            "context_usd": self._relative_to_project(context_usd) if context_usd else "",
            "context_version": context_version,
            "context_profile": context_profile,
            "editorial": self._relative_to_project(self.paths.project_root / "editorial" / "publish" / identity.episode / identity.sequence / "latest.json"),
            "comment": comment,
        }
        override_data = deepcopy(overrides or {})
        if override_data:
            context = str(override_data.get("context") or "").strip()
            camera = str(override_data.get("camera") or "").strip()
            if context:
                anim_input["context"] = context
            if camera:
                anim_input["camera"] = camera
            if override_data.get("layout_overlay") is False:
                anim_input["layout_overlay"] = ""
                anim_input["layout_overlay_usage"] = "disabled"
            excluded_cast = [
                str(value)
                for value in (override_data.get("exclude_cast") or [])
                if str(value).strip()
            ]
            if excluded_cast:
                anim_input["exclude_cast"] = excluded_cast
            anim_input["overrides"] = override_data
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

    def shot_context_root(
        self,
        identity: ShotIdentity,
        *,
        department: str = "anim",
        profile: str = "WORK",
    ) -> Path:
        """Return the version root for a generated shot workspace context."""

        return (
            self.shot_root(identity)
            / "data"
            / "context"
            / _normalize_work_option(department)
            / _clean_publish_token(profile).upper()
        )

    def latest_shot_context(
        self,
        identity: ShotIdentity,
        *,
        department: str = "anim",
        profile: str = "WORK",
    ) -> tuple[Path | None, str]:
        root = self.shot_context_root(identity, department=department, profile=profile)
        latest = read_json(root / "latest.json", {}) or {}
        version = str(latest.get("version") or "")
        path = root / str(latest.get("path") or "")
        if path.is_file():
            return path, version
        fallback = root / version / "context.usda" if version else None
        return (fallback, version) if fallback and fallback.is_file() else (None, "")

    def shot_context_components(
        self,
        identity: ShotIdentity,
        *,
        department: str = "anim",
        profile: str = "WORK",
        cast_contexts: dict[str, str] | None = None,
        exclude_cast: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve lightweight, non-editable workspace components for a shot."""

        components: list[dict[str, Any]] = []
        for item in self.build_preview(
            identity,
            department=department,
            cast_contexts=cast_contexts,
            exclude_cast=exclude_cast,
        ):
            role = _normalize_role(item.role)
            component_type = ""
            if role in {"BGA", "ENV"}:
                component_type = "background"
            elif role == "CROWD":
                component_type = "crowd"
            elif role == "FX":
                component_type = "fx"
            if not component_type:
                continue
            source = self._asset_usd_for_preview(item, profile=profile)
            components.append(
                {
                    "use": bool(source),
                    "type": component_type,
                    "name": item.cast_key,
                    "subset": profile.lower(),
                    "version": source.parent.name if source else "",
                    "load_policy": "payload",
                    "source": str(source) if source else "",
                    "state": "READY" if source else "MISSING",
                }
            )
        overlay = self._latest_layout_overlay_usd(identity)
        components.append(
            {
                "use": bool(overlay),
                "type": "layout_overlay",
                "name": "layout_overlay",
                "subset": "proxy",
                "version": overlay.parent.name if overlay else "",
                "load_policy": "payload",
                "source": str(overlay) if overlay else "",
                "state": "READY" if overlay else "OPTIONAL",
            }
        )
        return components

    def build_shot_context(
        self,
        identity: ShotIdentity,
        *,
        department: str = "anim",
        profile: str = "WORK",
        components: list[dict[str, Any]] | None = None,
        comment: str = "",
    ) -> Path:
        """Create an immutable workspace-reconstruction context snapshot."""

        root = self.shot_context_root(identity, department=department, profile=profile)
        version = self._next_publish_version(root)
        version_dir = root / version
        version_dir.mkdir(parents=True, exist_ok=False)
        rows = deepcopy(
            components
            if components is not None
            else self.shot_context_components(
                identity, department=department, profile=profile
            )
        )
        enabled = []
        missing = []
        for row in rows:
            source = Path(str(row.get("source") or ""))
            row["use"] = bool(row.get("use"))
            row["source"] = self._relative_to_project(source) if source.is_file() else ""
            if row["use"] and source.is_file():
                enabled.append((row, source))
            elif row["use"]:
                missing.append(str(row.get("name") or row.get("type") or "component"))
        if missing:
            raise RuntimeError("Shot Context has missing enabled components: " + ", ".join(missing))

        context_path = version_dir / "context.usda"
        context_path.write_text(
            self._shot_context_usda(version_dir, enabled), encoding="utf-8"
        )
        context_data = {
            "schema_version": 1,
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "department": _normalize_work_option(department),
            "profile": _clean_publish_token(profile).upper(),
            "version": version,
            "components": rows,
            "comment": comment,
        }
        context_json = write_json(version_dir / "context.json", context_data)
        digest = hashlib.sha256(
            json.dumps(context_data, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        write_json(
            version_dir / "build_manifest.json",
            {
                "manifest_type": "shot_context",
                "version": version,
                "context": "context.usda",
                "context_metadata": context_json.name,
                "metadata_hash": digest,
                "resolved_components": [row for row, _source in enabled],
            },
        )
        write_json(root / "latest.json", {"version": version, "path": f"{version}/context.usda"})
        self._update_versions(root / "versions.json", version)
        return context_path

    def list_shot_context_versions(
        self,
        identity: ShotIdentity,
        *,
        department: str = "anim",
        profile: str = "WORK",
    ) -> list[dict[str, Any]]:
        root = self.shot_context_root(identity, department=department, profile=profile)
        latest = read_json(root / "latest.json", {}) or {}
        rows = []
        for version_dir in sorted(root.glob("v*"), reverse=True):
            metadata = read_json(version_dir / "context.json", {}) or {}
            rows.append(
                {
                    "version": version_dir.name,
                    "state": "LATEST" if latest.get("version") == version_dir.name else "AVAILABLE",
                    "comment": str(metadata.get("comment") or ""),
                    "path": str(version_dir / "context.usda"),
                }
            )
        return rows

    def _shot_context_usda(
        self,
        version_dir: Path,
        components: list[tuple[dict[str, Any], Path]],
    ) -> str:
        groups = {"background": [], "crowd": [], "fx": [], "layout_overlay": []}
        for row, source in components:
            groups.setdefault(str(row.get("type") or "other"), []).append((row, source))
        lines = ["#usda 1.0", "(", '    defaultPrim = "Context"', ")", "", 'def Xform "Context"', "{"]
        for component_type, label in (
            ("background", "Background"),
            ("crowd", "Crowd"),
            ("fx", "FX"),
            ("layout_overlay", "LayoutOverlay"),
        ):
            lines.append(f'    def Scope "{label}"')
            lines.append("    {")
            for row, source in groups.get(component_type, []):
                name = _clean_publish_token(row.get("name") or component_type)
                relative = self._relative_path(version_dir, source)
                arc = "payload" if row.get("load_policy") == "payload" else "references"
                lines.extend(
                    [
                        f'        def Xform "{name}" (',
                        f'            {arc} = @{relative}@',
                        "        )",
                        "        {",
                        "        }",
                    ]
                )
            lines.append("    }")
        lines.extend(["}", ""])
        return "\n".join(lines)

    @staticmethod
    def _usd_companion_for_publish(path: Path) -> Path | None:
        if str(path) in {"", "."}:
            return None
        if path.suffix.lower() in {".usd", ".usda", ".usdc"} and path.is_file():
            return path
        directory = path if path.is_dir() else path.parent
        for name in ("asset.usda", "asset.usd", "model.usd", "model.usda"):
            candidate = directory / name
            if candidate.is_file():
                return candidate
        return None

    def _asset_usd_for_preview(
        self,
        item: BuildPreviewItem,
        *,
        profile: str = "WORK",
    ) -> Path | None:
        """Resolve the formal packed Asset USD used by shot construction.

        Component model/assembly publishes are deliberately not accepted here.
        A background becomes available to shots only after Compose/Pack has
        produced ``publish/asset/{context}/v###/asset.usda``.
        """

        variant_root = Path(item.variant_root) if item.variant_root else None
        if not variant_root or not variant_root.is_dir():
            return None
        profile_name = str(profile or "WORK").upper()
        preferred_contexts = tuple(
            dict.fromkeys((profile_name.lower(), "final", "work", "fast"))
        )
        asset_root = variant_root / "publish" / "asset"
        for context_name in preferred_contexts:
            base = asset_root / context_name
            latest = read_json(base / "latest.json", {}) or {}
            latest_path = base / str(latest.get("path") or "")
            if latest_path.is_file() and latest_path.name.lower() == "asset.usda":
                return latest_path
            latest_version = str(latest.get("version") or "")
            if latest_version:
                candidate = base / latest_version / "asset.usda"
                if candidate.is_file():
                    return candidate
            for version_dir in sorted(base.glob("v*"), reverse=True) if base.is_dir() else []:
                candidate = version_dir / "asset.usda"
                if candidate.is_file():
                    return candidate
        return None

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

        # Current layout publishing writes named cameras below the shot.
        # Preserve the legacy sequence/main lookup above, then fall back to
        # the same direct publishes exposed by the Construct resolver.
        camera_root = self.shot_publish_root(identity) / "camera"
        candidates: list[tuple[str, Path]] = []
        for latest_path in camera_root.glob("*/*/latest.json") if camera_root.exists() else []:
            latest_data = read_json(latest_path, {}) or {}
            candidate = latest_path.parent / str(latest_data.get("path") or "")
            if candidate.is_file():
                candidates.append((latest_path.parent.parent.name, candidate))
        if candidates:
            option = str(camera_option or "main").lower()

            def camera_priority(item: tuple[str, Path]) -> tuple[int, str]:
                target = item[0].lower()
                if target == option:
                    return 0, target
                if "cha" in target:
                    return 1, target
                return 2, target

            return sorted(candidates, key=camera_priority)[0][1]
        return None

    def _latest_work_stage_camera(self, identity: ShotIdentity) -> Path | None:
        """Resolve Camera Data Publish first, with legacy publish fallback."""

        candidates: list[tuple[int, str, Path]] = []
        for row in self.list_shot_data_versions(identity):
            parts = str(row.name or "").split("/")
            if not row.latest or not parts or parts[0] != "camera":
                continue
            version_dir = Path(row.path)
            manifest = version_dir / "camera.json" if version_dir.is_dir() else version_dir
            if not manifest.is_file():
                continue
            target = parts[1].lower() if len(parts) > 1 else ""
            priority = 0 if target in {"main", "cam", "camera"} else 1
            candidates.append((priority, target, manifest))
        if candidates:
            return sorted(candidates, key=lambda item: (item[0], item[1]))[0][2]
        return self._latest_shot_camera_publish(identity)

    def _latest_work_stage_placements(self, identity: ShotIdentity) -> Path | None:
        """Resolve shot-local Placement Publish/Data without sequence dependency."""

        published = [row for row in self.list_placement_publish_versions(identity) if row.latest]
        if published:
            path = Path(published[0].path)
            if path.is_file():
                return path
        data_root = self.shot_data_root(identity) / "placements"
        latest = read_json(data_root / "latest.json", {}) or {}
        path = data_root / str(latest.get("path") or "")
        if path.is_file():
            return path
        fallback = data_root / "placements.json"
        return fallback if fallback.is_file() else None

    @staticmethod
    def _latest_publish_version_label(base_dir: Path) -> str:
        latest = read_json(base_dir / "latest.json", {}) or {}
        return str(latest.get("version") or "")

    def _latest_layout_overlay_usd(self, identity: ShotIdentity) -> Path | None:
        direct_candidates = [
            self.shot_publish_root(identity) / "usd" / "layout.usda",
            self.shot_publish_root(identity) / "usd" / "layout.usd",
            self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "usd" / identity.shot / "layout.usda",
            self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "usd" / identity.shot / "layout.usd",
        ]
        for candidate in direct_candidates:
            if candidate.exists():
                return candidate

        publish_dirs = [
            self.shot_publish_root(identity) / "layout" / "usd",
            self.shot_publish_root(identity) / "layout" / "proxy",
            self.shot_publish_root(identity) / "layout" / "main",
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

    @staticmethod
    def _relative_path(from_dir: Path, target: Path) -> str:
        try:
            return os.path.relpath(target.resolve(), from_dir.resolve()).replace("\\", "/")
        except Exception:
            return target.as_posix()

    def _shot_virtual_path(self, path: Path) -> str:
        try:
            rel = path.resolve().relative_to(self.paths.shots_root().resolve()).as_posix()
            return f"shot://{rel}"
        except Exception:
            return path.as_posix()

    @staticmethod
    def _clean_publish_name(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip())
        cleaned = cleaned.strip("_")
        return cleaned or "target"

    @classmethod
    def _usd_identifier(cls, value: str) -> str:
        cleaned = cls._clean_publish_name(value)
        if cleaned[0].isdigit():
            return f"n_{cleaned}"
        return cleaned

    def _build_preview_from_cast(
        self,
        cast_data: dict[str, Any],
        *,
        consumer: str = "shot",
        department: str = "default",
        cast_contexts: dict[str, str] | None = None,
    ) -> list[BuildPreviewItem]:
        cast = cast_data.get("cast") or {}
        review_layers = cast_data.get("review_layers") or {}
        member_to_layer = {}
        for layer_name, layer in review_layers.items():
            for member in layer.get("members", []):
                member_to_layer[member] = layer_name

        items: list[BuildPreviewItem] = []
        context_overrides = {
            str(key): str(value).strip().lower()
            for key, value in (cast_contexts or {}).items()
            if str(value).strip()
        }
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

            explicit_context = context_overrides.get(str(cast_key), "")
            if explicit_context:
                publish_path = self.asset_publish_resolver.resolve_context(
                    variant_root,
                    explicit_context,
                    version=asset_publish,
                )
            else:
                publish_path = self.resolve_asset_context_work_publish(
                    variant_root,
                    asset_publish,
                    consumer=consumer,
                    department=department,
                )
            if not publish_path and not explicit_context:
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
        matches = []
        for asset_json in assets_root.glob("**/asset.json"):
            asset_root = asset_json.parent
            data = read_json(asset_json, {}) or {}
            if str(data.get("asset") or data.get("name") or asset_root.name) == asset_name:
                matches.append(asset_root)
        if not matches:
            matches = sorted(path for path in assets_root.glob(f"**/{asset_name}") if path.is_dir())
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

    def resolve_asset_context_work_publish(
        self,
        variant_root: Path,
        asset_publish: str,
        *,
        consumer: str = "shot",
        department: str = "default",
    ) -> Path | None:
        configured = self.asset_publish_resolver.resolve(
            variant_root,
            consumer=consumer,
            department=department,
            version=asset_publish,
        )
        if configured:
            return configured
        context_root = variant_root / "publish" / "asset" / "work"
        legacy_context_root = variant_root / "publish" / "asset" / "asset_work"
        if not context_root.exists() and legacy_context_root.exists():
            context_root = legacy_context_root
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
            self.shot_data_root(identity),
            self.shot_publish_root(identity),
            self.shot_output_root(identity),
            self.shot_render_root(identity),
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
        structure_source = folder_structure_source(self.project_config, "shot")
        if structure_source is not None:
            copy_entity_folder_structure(
                structure_source,
                shot_root,
                self.paths.shot_work_root(request.episode, request.sequence, request.shot),
                self.paths.shot_work_root(request.episode, request.sequence, request.shot).parent,
            )
        self.write_shot_json(request)
        self.ensure_cast_json(request.identity)
        self.ensure_dependencies_json(request.identity)
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
        structure_source = folder_structure_source(self.project_config, "shot")
        if structure_source is not None:
            copy_entity_folder_structure(
                structure_source,
                shot_root,
                self.paths.shot_work_root(identity.episode, identity.sequence, identity.shot),
                self.paths.shot_work_root(identity.episode, identity.sequence, identity.shot).parent,
            )
        self.write_shot_json(request)
        self.ensure_cast_json(identity)
        self.ensure_dependencies_json(identity)
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
        return write_json(path, {"cast": {}})

    def ensure_dependencies_json(self, identity: ShotIdentity | SequenceIdentity) -> Path:
        path = self.dependencies_path(identity)
        if path.exists():
            return path
        target = identity.shot if isinstance(identity, ShotIdentity) else identity.sequence
        return write_json(path, {"schema_version": 1, "shot": target, "dependencies": []})

    def load_cast(self, identity: ShotIdentity) -> dict[str, Any]:
        return read_json(self.shot_root(identity) / "cast.json", {"cast": {}})

    def write_cast(self, identity: ShotIdentity, cast_data: dict[str, Any]) -> Path:
        clean_data = dict(cast_data)
        legacy_layers = clean_data.pop("review_layers", None)
        issues = validate_cast_data(clean_data)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            messages = ", ".join(issue.message for issue in errors)
            raise ValueError(f"Invalid cast data: {messages}")
        path = write_json(self.shot_root(identity) / "cast.json", clean_data)
        if legacy_layers is not None:
            self.write_review_layers(identity, legacy_layers)
        return path

    def review_spec_data_root(
        self,
        identity: ShotIdentity,
        department: str = "anim",
    ) -> Path:
        return (
            self.shot_root(identity)
            / "data"
            / "review_spec"
            / _clean_publish_token(department or "anim")
        )

    def review_spec_path(
        self,
        identity: ShotIdentity,
        department: str = "anim",
    ) -> Path:
        base_dir = self.review_spec_data_root(identity, department)
        latest = read_json(base_dir / "latest.json", {}) or {}
        latest_path = base_dir / str(latest.get("path") or "")
        if latest_path.is_file():
            return latest_path
        return self.shot_root(identity) / "review_spec.json"

    def load_review_spec(
        self,
        identity: ShotIdentity,
        department: str = "anim",
    ) -> dict[str, Any]:
        path = self.review_spec_path(identity, department)
        if path.is_file():
            return read_json(path, {}) or {}
        legacy = self.load_cast(identity).get("review_layers") or {}
        layers = _defaulted_review_layers(legacy)
        legacy_manifest = self._latest_legacy_preview_render_manifest(identity)
        for layer_name, group in (legacy_manifest.get("groups") or {}).items():
            layer = layers.setdefault(str(layer_name), {"members": []})
            if group.get("order") is not None:
                layer["order"] = int(group["order"])
            camera_name = str(group.get("camera") or "").strip()
            if camera_name:
                camera = dict(layer.get("camera") or {})
                camera.update({"publish_type": "camera", "version": "latest", "name": camera_name})
                layer["camera"] = camera
            frame_range = group.get("frame_range")
            if isinstance(frame_range, (list, tuple)) and len(frame_range) >= 2:
                layer["frame_range"] = [int(frame_range[0]), int(frame_range[1])]
            resolution = group.get("resolution")
            if isinstance(resolution, (list, tuple)) and len(resolution) >= 2:
                layer["resolution"] = {
                    "width": int(resolution[0]),
                    "height": int(resolution[1]),
                    "scale": 1.0,
                }
        return self._review_spec_data(identity, layers)

    def _latest_legacy_preview_render_manifest(
        self,
        identity: ShotIdentity,
    ) -> dict[str, Any]:
        root = self.shot_publish_root(identity) / "preview_render"
        candidates: list[Path] = []
        if root.exists():
            for latest_path in root.glob("*/packages/latest.json"):
                latest = read_json(latest_path, {}) or {}
                path = latest_path.parent / str(latest.get("path") or "")
                if path.is_file():
                    candidates.append(path)
        if not candidates:
            return {}
        path = max(candidates, key=lambda item: item.stat().st_mtime)
        return read_json(path, {}) or {}

    def write_review_spec(
        self,
        identity: ShotIdentity,
        spec: dict[str, Any],
        *,
        department: str = "anim",
        comment: str = "",
    ) -> Path:
        layers = _defaulted_review_layers(spec.get("layers"))
        payload = self._review_spec_data(identity, layers)
        payload.update(
            {
                key: value
                for key, value in spec.items()
                if key not in {"schema", "episode", "sequence", "shot", "layers"}
            }
        )
        payload["layers"] = layers
        clean_department = _clean_publish_token(department or "anim")
        base_dir = self.review_spec_data_root(identity, clean_department)
        version_label = self._next_publish_version(base_dir)
        version_dir = base_dir / version_label
        version_dir.mkdir(parents=True, exist_ok=False)
        payload["department"] = clean_department
        payload["version"] = version_label
        payload["comment"] = comment
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        path = write_json(version_dir / "review_spec.json", payload)
        write_json(
            version_dir / "data.json",
            {
                "data_type": "review_spec",
                "department": clean_department,
                "version": version_label,
                "files": {"review_spec": "review_spec.json"},
                "comment": comment,
            },
        )
        write_json(
            base_dir / "latest.json",
            {"version": version_label, "path": f"{version_label}/review_spec.json"},
        )
        self._update_versions(base_dir / "versions.json", version_label)
        return path

    def publish_review_spec(
        self,
        identity: ShotIdentity,
        *,
        department: str = "anim",
        comment: str = "",
        source_scene: str = "",
    ) -> Path:
        spec = self.load_review_spec(identity, department)
        return self.write_review_spec(
            identity,
            spec,
            department=department,
            comment=comment,
        )

    def latest_review_spec_path(
        self,
        identity: ShotIdentity,
        *,
        department: str = "anim",
    ) -> Path | None:
        base_dir = self.review_spec_data_root(identity, department)
        latest = read_json(base_dir / "latest.json", {}) or {}
        path = base_dir / str(latest.get("path") or "")
        return path if path.is_file() else None

    def resolved_review_spec(
        self,
        identity: ShotIdentity,
        *,
        department: str = "anim",
    ) -> tuple[dict[str, Any], Path]:
        path = self.latest_review_spec_path(identity, department=department)
        if path:
            return read_json(path, {}) or {}, path
        draft = self.review_spec_path(identity, department)
        return self.load_review_spec(identity, department), draft

    @staticmethod
    def _review_spec_data(
        identity: ShotIdentity,
        layers: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema": "smartpipeline.review_spec.v1",
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "layers": _defaulted_review_layers(layers),
        }

    def sequence_cast_path(self, episode: str, sequence: str) -> Path:
        return self.sequence_workspace_root(episode, sequence) / "cast.json"

    def load_sequence_cast(self, episode: str, sequence: str) -> dict[str, Any]:
        return read_json(self.sequence_cast_path(episode, sequence), {"cast": {}})

    def write_sequence_cast(self, episode: str, sequence: str, cast_data: dict[str, Any]) -> Path:
        root = self.sequence_workspace_root(episode, sequence)
        root.mkdir(parents=True, exist_ok=True)
        return write_json(
            root / "cast.json",
            {"cast": cast_data.get("cast") or {}},
        )

    def review_layers(
        self,
        identity: ShotIdentity,
        department: str = "anim",
    ) -> dict[str, dict[str, Any]]:
        return _defaulted_review_layers(
            self.load_review_spec(identity, department).get("layers")
        )

    def write_review_layers(
        self,
        identity: ShotIdentity,
        review_layers: dict[str, Any],
        *,
        department: str = "anim",
        comment: str = "",
    ) -> Path:
        spec = self.load_review_spec(identity, department)
        spec["layers"] = _defaulted_review_layers(review_layers)
        path = self.write_review_spec(
            identity,
            spec,
            department=department,
            comment=comment,
        )
        cast_path = self.shot_root(identity) / "cast.json"
        cast_data = read_json(cast_path, {}) or {}
        if "review_layers" in cast_data:
            cast_data.pop("review_layers", None)
            write_json(cast_path, cast_data)
        return path

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
            publish_root=self.shot_publish_root(identity),
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
        version = latest_review_version(
            shot_root, department, publish_root=self.shot_publish_root(identity)
        ) or 1
        version_label = f"v{version:03d}"
        take = next_review_take(self.shot_publish_root(identity) / "review" / department / version_label)
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
            publish_root=self.shot_publish_root(identity),
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
        return {"cast": cast}

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
        namespace = _unique_namespace(existing_cast or {}, selected.get("asset"))
        return {
            "cast_key": cast_key,
            "asset": selected.get("asset", ""),
            "variant": selected.get("variant", "default") or "default",
            "role": role,
            "namespace": namespace,
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
            added_rows.append(row)
        if not added_rows:
            raise ValueError("No valid asset selections were provided.")
        return self.write_sequence_cast(episode, sequence, {"cast": cast}), added_rows


def _dependency_type_from_path(parts: tuple[str, ...], suffix: str) -> str:
    root = str(parts[0] if parts else "").lower().replace("-", "_").replace(" ", "_")
    if root in DEPENDENCY_TYPES:
        return root
    if root in {"vcam", "camera", "virtualcamera"}:
        return "virtual_camera"
    if root in {"sound", "dialogue", "music"} or suffix.lower() in {".wav", ".aif", ".aiff", ".mp3"}:
        return "audio"
    return "reference"


def _dependency_id(dependency_type: str, target: str, name: str, representation: str) -> str:
    raw = "_".join((dependency_type, target, name, representation))
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_")


def validate_dependencies_data(data: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if data.get("schema_version") != 1:
        issues.append("schema_version must be 1")
    entries = data.get("dependencies")
    if not isinstance(entries, list):
        return issues + ["dependencies must be a list"]
    seen_ids: set[str] = set()
    selected_groups: set[tuple[str, str, str]] = set()
    required = ("id", "type", "role", "source")
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            issues.append(f"dependencies[{index}] must be an object")
            continue
        missing = [key for key in required if not str(item.get(key) or "").strip()]
        if missing:
            issues.append(f"dependencies[{index}] missing: {', '.join(missing)}")
        dependency_id = str(item.get("id") or "").strip()
        if dependency_id in seen_ids:
            issues.append(f"duplicate dependency id: {dependency_id}")
        seen_ids.add(dependency_id)
        dependency_type = str(item.get("type") or "").strip()
        if dependency_type not in DEPENDENCY_TYPES:
            issues.append(f"unsupported dependency type: {dependency_type}")
        status = str(item.get("status") or "alternate").strip()
        if status not in DEPENDENCY_STATUSES:
            issues.append(f"unsupported dependency status: {status}")
        if status == "selected":
            target = str(item.get("target") or item.get("asset") or "Shot").strip()
            group = (target, dependency_type, str(item.get("role") or "").strip())
            if group in selected_groups:
                issues.append(f"multiple selected dependencies for target/type/role: {group[0]}/{group[1]}/{group[2]}")
            selected_groups.add(group)
    return issues


def validate_cast_data(cast_data: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    cast = cast_data.get("cast") or {}
    namespaces: dict[str, str] = {}

    for cast_key, entry in cast.items():
        namespace = str(entry.get("namespace") or "")
        asset_publish = str(entry.get("asset_publish") or "")
        if namespace in namespaces:
            issues.append(ValidationIssue("namespace_duplicate", f"namespace is duplicated: {namespace}", "error"))
        namespaces[namespace] = cast_key
        if asset_publish not in VALID_ASSET_PUBLISH and not _is_version_label(asset_publish):
            issues.append(ValidationIssue("invalid_asset_publish", f"asset_publish is invalid: {asset_publish}", "error"))

    return issues


SHOT_WORK_RE = re.compile(
    r"^(?P<shot>.+?)_(?P<department>[^_]+)_v(?P<version>\d+)_(?P<take>\d+)\.(?P<ext>[^.]+)$"
)
GENERIC_WORK_VERSION_RE = re.compile(
    r"^.+?_v(?P<version>\d+)_t?(?P<take>\d+)\.(?P<ext>[^.]+)$"
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
        generic = GENERIC_WORK_VERSION_RE.match(filename)
        if not generic:
            return None
        data = generic.groupdict()
        data.update({"shot": "", "department": "", "generic": True})
        data["version"] = int(data["version"])
        data["take"] = int(data["take"])
        return data
    data = match.groupdict()
    data["version"] = int(data["version"])
    data["take"] = int(data["take"])
    return data


def _defaulted_review_layers(review_layers: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    merged = {}
    for name, layer in (review_layers or {}).items():
        normalized_name = _normalize_role(name)
        incoming = dict(layer or {})
        legacy = LEGACY_DEFAULT_REVIEW_LAYERS.get(normalized_name)
        if (
            legacy
            and not incoming.get("members")
            and not incoming.get("objects")
            and set(incoming).issubset({"members", "order"})
            and int(incoming.get("order", legacy["order"])) == int(legacy["order"])
        ):
            continue
        merged[normalized_name] = incoming
        merged[normalized_name].setdefault("members", [])
        merged[normalized_name].setdefault("order", len(merged) * 10)
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


def _unique_namespace(existing_cast: dict[str, Any], asset_name: Any) -> str:
    base = re.sub(r"[^0-9A-Za-z_]+", "_", str(asset_name or "asset")).strip("_") or "asset"
    namespaces = {
        str(entry.get("namespace") or key)
        for key, entry in existing_cast.items()
        if isinstance(entry, dict)
    }
    if base not in namespaces:
        return base
    index = 2
    while f"{base}_{index:02d}" in namespaces:
        index += 1
    return f"{base}_{index:02d}"


def _is_version_label(value: str) -> bool:
    return len(value) >= 4 and value.lower().startswith("v") and value[1:].isdigit()


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    return _parse_bool(value)


def _version_from_path(path: Path | None) -> str:
    if not path:
        return ""
    for part in reversed(path.parts):
        if _is_version_label(str(part)):
            return str(part)
    return ""


def _construct_component_key(component: dict[str, Any]) -> tuple[str, str, str]:
    source = component.get("source") if isinstance(component.get("source"), dict) else {}
    source_kind = str(source.get("kind") or "")
    source_field = str(source.get("field") or "")
    source_key = f"{source_kind}:{source_field}" if source_kind or source_field else ""
    return (
        str(component.get("component_type") or component.get("type") or "").strip().lower(),
        str(component.get("name") or "").strip(),
        source_key,
    )


def _construct_rig_matches_preview(component: ConstructComponent, item: BuildPreviewItem) -> bool:
    names = {
        str(component.name or "").strip(),
        str(component.namespace or "").strip(),
    }
    if component.path:
        names.add(Path(str(component.path)).as_posix().lower())
    source_asset = str(component.source.get("asset") or "").strip()
    source_variant = str(component.source.get("variant") or "").strip()
    if source_asset:
        names.add(source_asset)
    candidates = {
        str(item.cast_key or "").strip(),
        str(item.namespace or "").strip(),
        str(item.asset or "").strip(),
    }
    if item.publish_path:
        candidates.add(Path(str(item.publish_path)).as_posix().lower())
    names.discard("")
    candidates.discard("")
    if source_asset and source_asset == item.asset and (not source_variant or source_variant == item.variant):
        return True
    return bool(names & candidates)


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


def _usd_default_prim_name(path: Path) -> str:
    try:
        from pxr import Usd
    except ImportError as exc:
        raise RuntimeError(
            "Python pxr is required to inspect the Asset USD default prim."
        ) from exc

    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise RuntimeError(f"Could not open Asset USD: {path}")
    default_prim = stage.GetDefaultPrim()
    if not default_prim:
        raise RuntimeError(
            f"Asset USD has no default prim and cannot be composed: {path}"
        )
    return default_prim.GetName()


def _animation_binding_layer_text(bindings: list[dict[str, Any]]) -> str:
    if not bindings:
        raise RuntimeError("USD Skel animation export did not report any skeleton bindings.")

    tree: dict[str, Any] = {}
    for binding in bindings:
        target = str(binding.get("target_skeleton") or "").strip("/")
        animation = str(binding.get("animation_source") or "").strip()
        if not target or not animation.startswith("/"):
            continue
        branch = tree
        for token in target.split("/"):
            branch = branch.setdefault(token, {})
        branch["__animation_source__"] = animation
    if not tree:
        raise RuntimeError("USD Skel animation bindings contain no valid target paths.")

    lines = ["#usda 1.0", ""]

    def write_branch(branch: dict[str, Any], depth: int) -> None:
        indent = "    " * depth
        for name, children in branch.items():
            if name == "__animation_source__":
                continue
            animation = children.get("__animation_source__")
            if animation:
                lines.extend(
                    [
                        f'{indent}over "{name}" (',
                        f'{indent}    prepend apiSchemas = ["SkelBindingAPI"]',
                        f"{indent})",
                        f"{indent}{{",
                        f"{indent}    rel skel:animationSource = <{animation}>",
                        f'{indent}    uniform token visibility = "invisible"',
                    ]
                )
            else:
                lines.extend([f'{indent}over "{name}"', f"{indent}{{"])
            write_branch(children, depth + 1)
            lines.append(f"{indent}}}")

    write_branch(tree, 0)
    lines.append("")
    return "\n".join(lines)


def _pipeline_root() -> Path:
    return Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or Path(__file__).resolve().parents[4]
    )
