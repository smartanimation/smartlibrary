from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartlib.apps.shot_manager import SequenceIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json


@dataclass(frozen=True)
class ResolvedInput:
    key: str
    label: str
    required: bool
    enabled: bool
    state: str
    version: str = ""
    path: str = ""
    adapter: str = ""
    children: tuple["ResolvedInput", ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    key: str
    label: str
    state: str
    detail: str


@dataclass(frozen=True)
class SequenceBuildPlan:
    project: str
    episode: str
    sequence: str
    recipe: str
    recipe_version: str
    fps: int
    frame_start: int
    frame_end: int
    virtual_camera_take: str
    inputs: tuple[ResolvedInput, ...]
    validation: tuple[ValidationResult, ...]
    output_scene: str
    manifest_path: str

    @property
    def can_build(self) -> bool:
        return not any(item.state == "ERROR" for item in self.validation)


@dataclass(frozen=True)
class BuildResult:
    scene_path: str
    manifest_path: str
    referenced: tuple[str, ...]


class SmartSequenceBuilderService:
    """Resolve, validate and stage a Maya Camera Sequencer scene."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        self.shots = ShotManagerService(project_config)
        if not project_config.project_root:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.project_root = project_config.project_root

    def sequences(self) -> list[SequenceIdentity]:
        return self.shots.list_sequences()

    def recipes(self) -> dict[str, dict[str, Any]]:
        data = self.project_config.load("sequence_builder.yml")
        recipes = data.get("recipes") if isinstance(data, dict) else {}
        if isinstance(recipes, dict) and recipes:
            return {str(key): dict(value or {}) for key, value in recipes.items()}
        return {
            "Mocap + Virtual Camera": {
                "version": "v001",
                "inputs": ["editorial", "mocap", "virtual_camera", "cast", "storyreel", "audio"],
            }
        }

    def plan(
        self,
        episode: str,
        sequence: str,
        recipe: str = "Mocap + Virtual Camera",
        *,
        virtual_camera_take: str = "",
        enabled: dict[str, bool] | None = None,
    ) -> SequenceBuildPlan:
        identity = SequenceIdentity(episode, sequence)
        sequence_data = self.shots.load_sequence(identity)
        recipe_data = self.recipes().get(recipe, {})
        recipe_inputs = {
            str(value) for value in (recipe_data.get("inputs") or []) if str(value)
        }
        enabled_map = (
            {key: key in recipe_inputs for key in (
                "editorial", "mocap", "virtual_camera", "cast", "storyreel", "audio"
            )}
            if recipe_inputs
            else {}
        )
        enabled_map.update(enabled or {})
        inputs = self._resolve_inputs(
            identity, sequence_data, virtual_camera_take, enabled_map
        )
        selected_take = virtual_camera_take or self._active_take(inputs)
        frame_start, frame_end = self._frame_range(sequence_data)
        fps = self._fps(sequence_data)
        output_scene = self._output_scene(identity)
        manifest = output_scene.parent / "sequence_build_manifest.json"
        validation = self._validate(identity, inputs, fps, frame_start, frame_end, selected_take)
        return SequenceBuildPlan(
            project=self.project_config.project_name,
            episode=episode,
            sequence=sequence,
            recipe=recipe,
            recipe_version=str(recipe_data.get("version") or "v001"),
            fps=fps,
            frame_start=frame_start,
            frame_end=frame_end,
            virtual_camera_take=selected_take,
            inputs=inputs,
            validation=validation,
            output_scene=str(output_scene),
            manifest_path=str(manifest),
        )

    def build(self, plan: SequenceBuildPlan) -> BuildResult:
        if not plan.can_build:
            errors = "; ".join(item.detail for item in plan.validation if item.state == "ERROR")
            raise RuntimeError(f"Sequence build validation failed: {errors}")
        from smartlib.dcc.maya.shot_builder import save_current_scene, stage_sequence_layout_from_preview

        identity = SequenceIdentity(plan.episode, plan.sequence)
        sequence_data = self.shots.load_sequence(identity)
        preview = self.shots.build_sequence_preview(identity)
        referenced = stage_sequence_layout_from_preview(
            preview,
            sequence_data,
            project_root=self.project_root,
        )
        save_current_scene(plan.output_scene, sequence_data)
        manifest_data = {
            "schema": "smart_sequence_build",
            "version": 1,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "project": plan.project,
            "episode": plan.episode,
            "sequence": plan.sequence,
            "recipe": {"name": plan.recipe, "version": plan.recipe_version},
            "fps": plan.fps,
            "frame_range": [plan.frame_start, plan.frame_end],
            "virtual_camera_take": plan.virtual_camera_take,
            "scene": plan.output_scene,
            "inputs": [self._input_dict(item) for item in plan.inputs if item.enabled],
            "referenced": list(referenced),
        }
        write_json(plan.manifest_path, manifest_data)
        return BuildResult(plan.output_scene, plan.manifest_path, tuple(referenced))

    def _resolve_inputs(
        self,
        identity: SequenceIdentity,
        sequence_data: dict[str, Any],
        selected_take: str,
        enabled: dict[str, bool],
    ) -> tuple[ResolvedInput, ...]:
        workspace = self.shots.sequence_workspace_root(identity.episode, identity.sequence)
        sequence_root = self.shots.paths.sequence_root(identity.episode, identity.sequence)
        editorial_path = self._first_existing(
            workspace / "sequence.json",
            sequence_root / "sequence.json",
        )
        mocap_root = self._first_existing(workspace / "data" / "mocap", sequence_root / "data" / "mocap")
        camera_root = self._first_existing(
            workspace / "data" / "virtual_camera",
            sequence_root / "data" / "virtual_camera",
            workspace / "publish" / "camera",
            sequence_root / "publish" / "camera",
        )
        takes = self._take_inputs(camera_root, selected_take)
        cast_path = self.shots.sequence_cast_path(identity.episode, identity.sequence)
        storyreel = self._resolve_storyreel(identity)
        audio = self._first_file(workspace / "data" / "audio", sequence_root / "data" / "audio")
        mocap_children = self._directory_children(mocap_root, "Maya Mocap")
        rows = (
            self._input("editorial", "Editorial", editorial_path, False, enabled, "Sequence JSON"),
            ResolvedInput(
                "mocap", "Motion Capture", True, enabled.get("mocap", True),
                "READY" if mocap_children else "MISSING", self._latest_version(mocap_root),
                str(mocap_root or ""), "Maya Mocap", tuple(mocap_children),
            ),
            ResolvedInput(
                "virtual_camera", "Virtual Camera", False, enabled.get("virtual_camera", True),
                "READY" if takes else "MISSING", self._latest_version(camera_root),
                str(camera_root or ""), "Camera Sequencer", tuple(takes),
            ),
            self._input("cast", "Sequence Cast", cast_path, True, enabled, "Maya Reference"),
            self._input("storyreel", "Storyreel", storyreel, False, enabled, "Image Plane"),
            self._input("audio", "Audio", audio, False, enabled, "Maya Audio"),
        )
        return rows

    def _validate(
        self,
        identity: SequenceIdentity,
        inputs: tuple[ResolvedInput, ...],
        fps: int,
        start: int,
        end: int,
        selected_take: str,
    ) -> tuple[ValidationResult, ...]:
        required = [item for item in inputs if item.enabled and item.required]
        missing = [item.label for item in required if item.state != "READY"]
        take_count = len(next((item.children for item in inputs if item.key == "virtual_camera"), ()))
        cast_data = self.shots.load_sequence_cast(identity.episode, identity.sequence)
        namespaces = [
            str(value.get("namespace") or key)
            for key, value in (cast_data.get("cast") or {}).items()
            if isinstance(value, dict)
        ]
        duplicates = sorted({name for name in namespaces if namespaces.count(name) > 1})
        return (
            ValidationResult("identity", "Identity", "READY", f"{identity.episode} / {identity.sequence}"),
            ValidationResult(
                "required", "Required Inputs", "ERROR" if missing else "READY",
                ", ".join(missing) + " missing" if missing else f"{len(required)} resolved",
            ),
            ValidationResult("fps", "Frame Rate", "READY" if fps > 0 else "ERROR", f"{fps} fps"),
            ValidationResult(
                "range", "Frame Range", "READY" if end >= start else "ERROR", f"{start} - {end}",
            ),
            ValidationResult(
                "takes", "Camera Takes",
                "WARNING" if take_count > 1 and not selected_take else "READY",
                f"{take_count} available" + (f", {selected_take} selected" if selected_take else ""),
            ),
            ValidationResult(
                "namespace", "Namespace Check", "ERROR" if duplicates else "READY",
                ", ".join(duplicates) if duplicates else "No conflicts",
            ),
        )

    def _output_scene(self, identity: SequenceIdentity) -> Path:
        root = self.shots.sequence_workspace_root(identity.episode, identity.sequence)
        return root / "layout" / "work" / "maya" / "main" / f"{identity.code}_layout_v001_01.ma"

    def _resolve_storyreel(self, identity: SequenceIdentity) -> Path | None:
        roots = [
            self.project_root / "editorial" / "publish" / identity.episode / identity.sequence,
            self.project_root / "editorial" / identity.episode / identity.sequence,
        ]
        for root in roots:
            result = self._first_file(root)
            if result:
                return result
        return None

    @staticmethod
    def _frame_range(data: dict[str, Any]) -> tuple[int, int]:
        editorial = data.get("editorial") if isinstance(data.get("editorial"), dict) else {}
        shots = data.get("shots") or []
        starts = [row.get("cut_in") for row in shots if isinstance(row, dict) and row.get("cut_in") is not None]
        ends = [row.get("cut_out") for row in shots if isinstance(row, dict) and row.get("cut_out") is not None]
        start = editorial.get("cut_in", min(starts) if starts else 1001)
        end = editorial.get("cut_out", max(ends) if ends else start)
        return int(start), int(end)

    def _fps(self, data: dict[str, Any]) -> int:
        return int(data.get("fps") or (data.get("editorial") or {}).get("fps") or self.shots.project_fps)

    @staticmethod
    def _first_existing(*paths: Path) -> Path | None:
        return next((path for path in paths if path.exists()), None)

    @staticmethod
    def _first_file(*roots: Path) -> Path | None:
        extensions = {".wav", ".aif", ".aiff", ".mov", ".mp4", ".jpg", ".jpeg", ".png", ".exr"}
        for root in roots:
            if root.is_file():
                return root
            if root.exists():
                match = next((path for path in sorted(root.rglob("*")) if path.is_file() and path.suffix.lower() in extensions), None)
                if match:
                    return match
        return None

    @staticmethod
    def _latest_version(path: Path | None) -> str:
        if not path:
            return ""
        latest = read_json(path / "latest.json", {}) if path.is_dir() else {}
        if isinstance(latest, dict) and latest.get("version"):
            return str(latest["version"])
        candidates = sorted((item.name for item in path.glob("v[0-9]*") if item.is_dir()), reverse=True) if path.is_dir() else []
        return candidates[0] if candidates else ""

    def _directory_children(self, root: Path | None, adapter: str) -> list[ResolvedInput]:
        if not root or not root.exists():
            return []
        children = [path for path in sorted(root.iterdir()) if path.name != "latest.json"]
        return [
            ResolvedInput(path.name, path.name, True, True, "READY", self._latest_version(path), str(path), adapter)
            for path in children
        ]

    def _take_inputs(self, root: Path | None, selected: str) -> list[ResolvedInput]:
        if not root or not root.exists():
            return []
        paths = [path for path in sorted(root.iterdir()) if path.is_dir() and path.name != "latest"]
        return [
            ResolvedInput(
                path.name, path.name, False, True, "ACTIVE" if path.name == selected else "AVAILABLE",
                self._latest_version(path), str(path), "Camera Sequencer",
            )
            for path in paths
        ]

    def _input(self, key: str, label: str, path: Path | None, required: bool, enabled: dict[str, bool], adapter: str) -> ResolvedInput:
        use = enabled.get(key, True)
        return ResolvedInput(
            key, label, required, use, "READY" if path and path.exists() else "MISSING",
            self._latest_version(path.parent if path and path.is_file() else path), str(path or ""), adapter,
        )

    @staticmethod
    def _active_take(inputs: tuple[ResolvedInput, ...]) -> str:
        camera = next((item for item in inputs if item.key == "virtual_camera"), None)
        if not camera:
            return ""
        active = next((item.key for item in camera.children if item.state == "ACTIVE"), "")
        return active or (camera.children[-1].key if camera.children else "")

    @classmethod
    def _input_dict(cls, item: ResolvedInput) -> dict[str, Any]:
        data = asdict(item)
        data["children"] = [cls._input_dict(child) for child in item.children]
        return data

    @classmethod
    def input_payload(cls, item: ResolvedInput) -> dict[str, Any]:
        """Serialize one resolved input for a queued build manifest."""
        return cls._input_dict(item)
