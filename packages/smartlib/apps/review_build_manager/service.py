from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import re

from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json
from smartlib.core.versioning import format_version, parse_version


MOV_EXTENSIONS = {".mov", ".mp4"}


@dataclass(frozen=True)
class ReviewOutput:
    version: str
    directory: str
    scene: str = ""
    movie: str = ""
    updated: str = ""
    state: str = "MISSING"


@dataclass(frozen=True)
class ReviewShotStatus:
    identity: ShotIdentity
    state: str
    output_version: str
    output_label: str
    last_review: str
    thumbnail: str
    comment: str
    source_version: str
    message: str
    outputs: tuple[ReviewOutput, ...] = field(default_factory=tuple)


class ReviewBuildManagerService:
    """Resolve review inputs, outputs, and the background Maya runtime."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        self.shots = ShotManagerService(project_config)

    @property
    def project_name(self) -> str:
        return str(
            getattr(self.project_config, "project_name", "")
            or self.project_config.config_dir.name
        )

    def scan(self) -> list[ReviewShotStatus]:
        return [self.shot_status(identity) for identity in self.shots.list_shots()]

    def next_output_version(self, identity: ShotIdentity) -> str:
        versions = self.list_outputs(identity)
        number = max((parse_version(row.version) or 0 for row in versions), default=0) + 1
        return format_version(number)

    def resolve_mayapy(self) -> Path:
        configured = os.environ.get("SMARTPIPELINE_MAYAPY")
        if configured and Path(configured).is_file():
            return Path(configured)
        candidates = []
        preferred = self.project_config.config_dir / "software_maya2024.yml"
        if preferred.is_file():
            candidates.append(preferred)
        candidates.extend(
            path
            for path in sorted(self.project_config.config_dir.glob("software_maya*.yml"))
            if path not in candidates
        )
        for config_path in candidates:
            data = self.project_config.load(config_path.name)
            maya_path = Path(str(data.get("path") or ""))
            if maya_path.suffix.lower() in {".bat", ".cmd"} and maya_path.is_file():
                text = maya_path.read_text(encoding="utf-8-sig", errors="ignore")
                match = re.search(
                    r"^\s*set\s+MAYAINSTPATH\s*=\s*(.+?)\s*$",
                    text,
                    flags=re.IGNORECASE | re.MULTILINE,
                )
                if match:
                    install_root = Path(match.group(1).strip().strip('"'))
                    mayapy = install_root / "bin" / "mayapy.exe"
                    if mayapy.is_file():
                        return mayapy
            mayapy = maya_path.with_name("mayapy.exe")
            if mayapy.is_file():
                return mayapy
        raise FileNotFoundError(
            "mayapy.exe was not resolved. Set SMARTPIPELINE_MAYAPY or configure software_maya*.yml."
        )

    def shot_status(self, identity: ShotIdentity) -> ReviewShotStatus:
        source = self.shots.latest_animation_package_path(identity)
        source_version = source.parent.name if source else ""
        outputs = tuple(self.list_outputs(identity))
        latest_output = outputs[0] if outputs else None
        shot_data = self.shots.load_shot(identity)
        thumbnail = self.shots.shot_root(identity) / "thumbnail.jpg"

        if not source:
            state = "MISSING"
            message = "Animation Package is missing."
        elif not latest_output:
            state = "READY"
            message = "No review output has been generated."
        elif self._is_dirty(source, latest_output):
            state = "DIRTY"
            message = "Published inputs are newer than the last review build."
        elif not latest_output.movie:
            state = "READY"
            message = "Review scene exists, but the output movie is missing."
        else:
            state = "UP TO DATE"
            message = "Output movie matches the current Animation Package."

        output_version = latest_output.version if latest_output else ""
        if latest_output and latest_output.movie:
            output_label = output_version
        elif latest_output:
            output_label = f"{output_version} / MOV missing"
        else:
            output_label = "-"
        return ReviewShotStatus(
            identity=identity,
            state=state,
            output_version=output_version,
            output_label=output_label,
            last_review=latest_output.updated if latest_output else "-",
            thumbnail=str(thumbnail) if thumbnail.is_file() else "",
            comment=str(shot_data.get("status") or ""),
            source_version=source_version,
            message=message,
            outputs=outputs,
        )

    def list_outputs(self, identity: ShotIdentity) -> list[ReviewOutput]:
        root = (
            self.shots.shot_root(identity)
            / "output"
            / "review"
            / "animation"
        )
        rows: list[ReviewOutput] = []
        for version_dir in root.glob("v*") if root.is_dir() else []:
            if not version_dir.is_dir() or parse_version(version_dir.name) is None:
                continue
            scene = self._first_file(version_dir, {".ma", ".mb"})
            movie = self._first_file(version_dir, MOV_EXTENSIONS)
            manifest = version_dir / "build_manifest.json"
            timestamps = [
                path.stat().st_mtime
                for path in (scene, movie, manifest)
                if path and path.is_file()
            ]
            updated = (
                datetime.fromtimestamp(max(timestamps)).strftime("%Y-%m-%d %H:%M")
                if timestamps
                else ""
            )
            state = "COMPLETE" if movie else ("SCENE ONLY" if scene else "MISSING")
            rows.append(
                ReviewOutput(
                    version=version_dir.name,
                    directory=str(version_dir),
                    scene=str(scene) if scene else "",
                    movie=str(movie) if movie else "",
                    updated=updated,
                    state=state,
                )
            )
        return sorted(
            rows,
            key=lambda row: parse_version(row.version) or 0,
            reverse=True,
        )

    @staticmethod
    def _first_file(root: Path, extensions: set[str]) -> Path | None:
        matches = sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in extensions
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return matches[0] if matches else None

    @staticmethod
    def _is_dirty(source: Path, output: ReviewOutput) -> bool:
        output_dir = Path(output.directory)
        build_manifest = read_json(output_dir / "build_manifest.json", {}) or {}
        recorded = Path(str(build_manifest.get("animation_manifest") or ""))
        if recorded and recorded.as_posix().lower() != source.as_posix().lower():
            return True
        comparison = Path(output.movie or output.scene)
        if not comparison.is_file():
            return True
        return source.stat().st_mtime > comparison.stat().st_mtime
