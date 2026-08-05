from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from smartlib.apps.shot_manager import (
    SequenceIdentity,
    ShotIdentity,
    ShotManagerService,
)


BUILD_MODES = ("AUTO", "STAGE", "UPDATE", "REBUILD", "REVIEW ONLY")


@dataclass(frozen=True)
class BuildValidation:
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class SceneBuildPlan:
    identity: ShotIdentity | SequenceIdentity
    requested_mode: str
    resolved_mode: str
    department: str
    task: str
    source_scene: str = ""
    anim_input: str = ""
    animation_package: str = ""
    input_policy: str = "GENERATE MISSING"
    state: str = "READY"
    summary: str = ""
    validations: tuple[BuildValidation, ...] = field(default_factory=tuple)

    @property
    def buildable(self) -> bool:
        return self.state not in {"BLOCKED", "SKIP"} and not any(
            row.severity == "ERROR" for row in self.validations
        )


class SceneBuildOrchestrator:
    """Create reproducible build plans without modifying artist work scenes."""

    def __init__(self, shots: ShotManagerService):
        self.shots = shots

    def plan(
        self,
        identity: ShotIdentity,
        *,
        requested_mode: str = "AUTO",
        department: str = "anim",
        task: str = "",
        input_policy: str = "GENERATE MISSING",
        overrides: dict | None = None,
    ) -> SceneBuildPlan:
        mode = str(requested_mode or "AUTO").strip().upper()
        if mode not in BUILD_MODES:
            raise ValueError(f"Unsupported build mode: {requested_mode}")
        department = str(department or "anim").strip().lower()
        tasks = self.shots.shot_tasks(department)
        task = str(task or (tasks[0] if tasks else "main")).strip()
        input_policy = str(input_policy or "GENERATE MISSING").strip().upper()
        latest_work = self._latest_work(identity, department, task)
        anim_input = self.shots.latest_anim_input(identity)
        animation_package = self.shots.latest_animation_package_path(identity)
        resolved = self._resolve_mode(
            mode,
            identity=identity,
            latest_work=latest_work,
            anim_input=anim_input,
            animation_package=animation_package,
        )
        validations = list(
            self._validate(
                identity,
                resolved,
                department=department,
                latest_work=latest_work,
                anim_input=anim_input,
                animation_package=animation_package,
                input_policy=input_policy,
                overrides=overrides or {},
            )
        )
        state = "READY"
        if resolved == "SKIP":
            state = "SKIP"
        elif any(row.severity == "ERROR" for row in validations):
            state = "BLOCKED"
        elif any(row.severity == "WARNING" for row in validations):
            state = "WARNING"
        return SceneBuildPlan(
            identity=identity,
            requested_mode=mode,
            resolved_mode=resolved,
            department=department,
            task=task,
            source_scene=latest_work.path if latest_work else "",
            anim_input=str(anim_input or ""),
            animation_package=str(animation_package or ""),
            input_policy=input_policy,
            state=state,
            summary=self._summary(resolved, latest_work),
            validations=tuple(validations),
        )

    def plan_many(
        self,
        identities: Iterable[ShotIdentity],
        **kwargs,
    ) -> list[SceneBuildPlan]:
        return [self.plan(identity, **kwargs) for identity in identities]

    def plan_sequence(
        self,
        identity: SequenceIdentity,
        *,
        requested_mode: str = "STAGE",
        department: str = "layout",
        task: str = "",
        input_policy: str = "GENERATE MISSING",
        overrides: dict | None = None,
    ) -> SceneBuildPlan:
        mode = str(requested_mode or "STAGE").strip().upper()
        if mode == "AUTO":
            mode = "STAGE"
        if mode == "REVIEW ONLY":
            mode = "STAGE"
        tasks = self.shots.shot_tasks(department)
        task = str(task or (tasks[0] if tasks else "main"))
        input_policy = str(input_policy or "GENERATE MISSING").strip().upper()
        current = self.shots.latest_sequence_stage_input(identity, department)
        validations = []
        sequence_data = self.shots.load_sequence(identity)
        cast_data = self.shots.load_sequence_cast(identity.episode, identity.sequence)
        if not sequence_data:
            validations.append(
                BuildValidation(
                    "ERROR",
                    "MISSING_SEQUENCE",
                    f"sequence.json was not found for {identity.code}.",
                )
            )
        if not (cast_data.get("cast") or {}):
            validations.append(
                BuildValidation(
                    "ERROR",
                    "MISSING_SEQUENCE_CAST",
                    "Sequence cast has no entries.",
                )
            )
        if not current and input_policy == "USE EXISTING":
            validations.append(
                BuildValidation(
                    "ERROR",
                    "MISSING_SEQUENCE_INPUT",
                    "sequence_input.json is required by the Use Existing policy.",
                )
            )
        if (
            (overrides or {}).get("use_placements") is not False
            and (not current or input_policy != "USE EXISTING")
        ):
            placements_root = (
                self.shots.sequence_workspace_root(identity.episode, identity.sequence)
                / "publish"
                / "layout"
                / "placements"
            )
            placement_latest = placements_root / "latest.json"
            if not placement_latest.is_file():
                validations.append(
                    BuildValidation(
                        "ERROR",
                        "MISSING_SEQUENCE_PLACEMENTS",
                        f"Sequence placements were not found: {placements_root}",
                    )
                )
        if not current and input_policy != "USE EXISTING" and not any(
            row.severity == "ERROR" for row in validations
        ):
            validations.append(
                BuildValidation(
                    "INFO",
                    "WILL_GENERATE_SEQUENCE_INPUT",
                    "A new sequence_input.json will be generated before Stage.",
                )
            )
        state = (
            "BLOCKED"
            if any(row.severity == "ERROR" for row in validations)
            else "WARNING"
            if any(row.severity == "WARNING" for row in validations)
            else "READY"
        )
        # SceneBuildPlan only relies on the identity fields shared by Shot and Sequence.
        return SceneBuildPlan(
            identity=identity,
            requested_mode=requested_mode,
            resolved_mode=mode,
            department=department,
            task=task,
            anim_input=str(current or ""),
            input_policy=input_policy,
            state=state,
            summary="Build a sequence-wide Maya Sequencer verification scene.",
            validations=tuple(validations),
        )

    def _latest_work(self, identity: ShotIdentity, department: str, task: str):
        rows = self.shots.list_shot_work_files(
            identity,
            department=department,
            option="all",
            tool_name="maya",
            task=task,
        )
        return max(
            rows,
            key=lambda row: (
                int(row.version or 0),
                int(row.take or 0),
                Path(row.path).stat().st_mtime if Path(row.path).is_file() else 0,
            ),
            default=None,
        )

    def _resolve_mode(
        self,
        requested: str,
        *,
        identity: ShotIdentity,
        latest_work,
        anim_input: Path | None,
        animation_package: Path | None,
    ) -> str:
        if requested != "AUTO":
            return requested
        if latest_work is None:
            return "STAGE"
        metadata_paths = [
            self.shots.shot_root(identity) / "shot.json",
            self.shots.shot_root(identity) / "cast.json",
            Path(anim_input) if anim_input else None,
        ]
        work_path = Path(latest_work.path)
        if work_path.is_file() and any(
            path and path.is_file() and path.stat().st_mtime > work_path.stat().st_mtime
            for path in metadata_paths
        ):
            return "UPDATE"
        if animation_package:
            return "REVIEW ONLY"
        return "UPDATE"

    def _validate(
        self,
        identity: ShotIdentity,
        mode: str,
        *,
        department: str,
        latest_work,
        anim_input: Path | None,
        animation_package: Path | None,
        input_policy: str,
        overrides: dict,
    ):
        shot_path = self.shots.shot_root(identity) / "shot.json"
        cast_path = self.shots.shot_root(identity) / "cast.json"
        if not shot_path.is_file():
            yield BuildValidation("ERROR", "MISSING_SHOT", f"shot.json was not found: {shot_path}")
        if not cast_path.is_file():
            yield BuildValidation("ERROR", "MISSING_CAST", f"cast.json was not found: {cast_path}")
        if mode in {"STAGE", "UPDATE", "REBUILD"} and department == "anim" and not anim_input:
            if input_policy == "USE EXISTING":
                yield BuildValidation(
                    "ERROR",
                    "MISSING_ANIM_INPUT",
                    "Animation input package is required by the Use Existing policy.",
                )
            else:
                blocking = [
                    row
                    for row in self.shots.shot_anim_input_status(identity)
                    if row.state == "MISSING" and row.name != "layout_overlay"
                ]
                if str(overrides.get("camera") or "").strip():
                    blocking = [row for row in blocking if row.name != "camera"]
                if overrides.get("use_placements") is False:
                    blocking = [row for row in blocking if row.name != "placements"]
                if blocking:
                    for row in blocking:
                        yield BuildValidation(
                            "ERROR",
                            "ANIM_INPUT_SOURCE_MISSING",
                            f"{row.name}: {row.message or 'required source is missing'}",
                        )
                else:
                    yield BuildValidation(
                        "INFO",
                        "WILL_GENERATE_ANIM_INPUT",
                        "A new Animation Input Package will be generated before Stage.",
                    )
        if mode == "UPDATE" and latest_work is None:
            yield BuildValidation(
                "WARNING",
                "NO_WORK_SCENE",
                "No existing work scene was found; the worker will perform an initial Stage.",
            )
        if mode == "REVIEW ONLY" and not animation_package:
            yield BuildValidation(
                "ERROR",
                "MISSING_ANIMATION_PACKAGE",
                "Animation Package is required for Review Only.",
            )
        if mode in {"STAGE", "UPDATE", "REBUILD"} and anim_input:
            preview = self.shots.build_preview_from_anim_input(identity)
            missing = [
                row
                for row in preview
                if row.required and row.status != "resolved"
            ]
            for row in missing:
                yield BuildValidation(
                    "ERROR",
                    "UNRESOLVED_CAST",
                    f"{row.cast_key}: {row.message or row.status}",
                )

    @staticmethod
    def _summary(mode: str, latest_work) -> str:
        if mode == "STAGE":
            return "Create a verification scene from metadata."
        if mode == "UPDATE":
            source = Path(latest_work.path).name if latest_work else "no work scene"
            return f"Reconstruct changes for review; source: {source}."
        if mode == "REBUILD":
            return "Reconstruct the entire verification scene from metadata."
        if mode == "REVIEW ONLY":
            return "Keep scene inputs unchanged and regenerate review output."
        return "No build is required."
