from __future__ import annotations

import os
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig, expand_config_tokens, load_config
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import configured_project_paths


@dataclass(frozen=True)
class StoryreelShotResult:
    shot: str
    output_dir: Path
    first_file: Path
    thumbnail: Path
    command: list[str]
    executed: bool
    frame_count: int


@dataclass(frozen=True)
class StoryreelBuildResult:
    publish_dir: Path
    offline_mov: Path
    results: list[StoryreelShotResult]
    storyreel_json: Path


class StoryreelBuilder:
    """Build shot image sequences from an editorial cut publish."""

    def __init__(self, project_config: ProjectConfig, pipeline_root: str | Path | None = None):
        self.project_config = project_config
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.project_root = project_root
        self.pipeline_root = Path(pipeline_root) if pipeline_root else _pipeline_root()

    @property
    def fps(self) -> int:
        fps = (self.project_config.base.get("anchors") or {}).get("fps", 24)
        try:
            return int(fps)
        except (TypeError, ValueError):
            return 24

    def build_from_publish(
        self,
        publish_dir: str | Path,
        *,
        execute: bool = True,
        width: int = 960,
        image_ext: str = "jpg",
    ) -> StoryreelBuildResult:
        version_dir = Path(publish_dir)
        editorial_json = version_dir / "metadata" / "editorial.json"
        offline_mov = version_dir / "offline.mov"
        if not editorial_json.exists():
            raise FileNotFoundError(f"editorial.json was not found: {editorial_json}")
        if not offline_mov.exists():
            raise FileNotFoundError(f"offline.mov was not found: {offline_mov}")
        return self.build(version_dir, editorial_json, offline_mov, execute=execute, width=width, image_ext=image_ext)

    def build_latest_cut(
        self,
        *,
        execute: bool = True,
        width: int = 960,
        image_ext: str = "jpg",
    ) -> StoryreelBuildResult:
        latest_json = self._latest_cut_manifest()
        latest = read_json(latest_json, {}) or {}
        version = str(latest.get("version") or "")
        if not version:
            raise FileNotFoundError(f"No latest editorial cut publish: {latest_json}")
        return self.build_from_publish(latest_json.parent / version, execute=execute, width=width, image_ext=image_ext)

    def build(
        self,
        publish_dir: Path,
        editorial_json: Path,
        offline_mov: Path,
        *,
        execute: bool = True,
        width: int = 960,
        image_ext: str = "jpg",
    ) -> StoryreelBuildResult:
        data = read_json(editorial_json, {}) or {}
        fps = int(data.get("fps") or self.fps)
        ffmpeg = self._ffmpeg_path()
        results = []
        shots = [shot for shot in (data.get("shots") or []) if isinstance(shot, dict)]
        timeline_start = min((int(shot.get("cut_in") or 0) for shot in shots), default=0)
        for shot in shots:
            shot_name = str(shot.get("shot") or "")
            if not shot_name:
                continue
            duration = int(shot.get("duration") or 0)
            if duration <= 0:
                continue

            output_dir = publish_dir / "storyreel" / shot_name
            if execute:
                output_dir.mkdir(parents=True, exist_ok=True)
            first_frame = int(shot.get("cut_in") or 1001)
            first_file = output_dir / f"storyreel_{first_frame:04d}.{image_ext.lstrip('.')}"
            pattern = output_dir / f"storyreel_%04d.{image_ext.lstrip('.')}"
            command = [
                str(ffmpeg),
                "-y",
                "-ss",
                _seconds(max(0, first_frame - timeline_start), fps),
                "-i",
                str(offline_mov),
                "-frames:v",
                str(duration),
                "-vf",
                f"fps={fps},scale={int(width)}:-2",
                "-start_number",
                str(first_frame),
                str(pattern),
            ]
            if execute:
                subprocess.run(command, check=True)
            thumbnail = self._write_shot_thumbnail(shot, first_file, execute=execute)
            results.append(
                StoryreelShotResult(
                    shot=shot_name,
                    output_dir=output_dir,
                    first_file=first_file,
                    thumbnail=thumbnail,
                    command=command,
                    executed=execute,
                    frame_count=duration,
                )
            )

        storyreel_json = self._write_storyreel_json(publish_dir, results, fps, width, execute=execute)
        return StoryreelBuildResult(
            publish_dir=publish_dir,
            offline_mov=offline_mov,
            results=results,
            storyreel_json=storyreel_json,
        )

    def _ffmpeg_path(self) -> Path:
        tools = self.project_config.load("tools.yml") or load_config(self.pipeline_root / "config" / "default" / "tools.yml")
        raw = (((tools.get("tools") or {}).get("ffmpeg") or {}).get("path") or "").strip()
        if raw:
            raw = expand_config_tokens(raw, self.project_config)
            path = Path(raw)
            if path.exists():
                return path
        path = Path(os.environ.get("SMARTPIPELINE_TOOLS") or self.pipeline_root.parent / "smarttools") / "ffmpeg" / "ffmpeg.exe"
        if path.exists():
            return path
        raise FileNotFoundError(f"ffmpeg.exe was not found: {path}")

    def _latest_cut_manifest(self) -> Path:
        publish_root = configured_project_paths(
            self.project_root, self.project_config
        ).editorial_publish_root()
        legacy = publish_root / "cut" / "latest.json"
        candidates = [path for path in publish_root.glob("*/*/latest.json") if path.is_file()]
        if legacy.exists():
            candidates.append(legacy)
        if not candidates:
            return legacy
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _write_shot_thumbnail(self, shot: dict[str, Any], first_file: Path, *, execute: bool) -> Path:
        episode = str(shot.get("episode") or "").strip()
        sequence = str(shot.get("sequence") or "").strip()
        shot_name = str(shot.get("shot") or "").strip()
        if not episode or not sequence or not shot_name:
            return first_file
        shot_root = configured_project_paths(self.project_root, self.project_config).shot_root(episode, sequence, shot_name)
        thumbnail = shot_root / "thumbnail.jpg"
        if not execute:
            return thumbnail
        if first_file.exists():
            shot_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(first_file, thumbnail)
            shot_json_path = shot_root / "shot.json"
            shot_json = read_json(shot_json_path, {}) or {}
            shot_json["thumbnail"] = "thumbnail.jpg"
            write_json(shot_json_path, shot_json)
        return thumbnail

    @staticmethod
    def _write_storyreel_json(
        publish_dir: Path,
        results: list[StoryreelShotResult],
        fps: int,
        width: int,
        *,
        execute: bool,
    ) -> Path:
        data: dict[str, Any] = {
            "fps": fps,
            "width": width,
            "shots": {
                result.shot: {
                    "image_sequence": str((result.output_dir / "storyreel_####.jpg").relative_to(publish_dir).as_posix()),
                    "first_file": str(result.first_file.relative_to(publish_dir).as_posix()),
                    "thumbnail": str(result.thumbnail),
                    "frame_count": result.frame_count,
                    "executed": result.executed,
                }
                for result in results
            },
        }
        path = publish_dir / "metadata" / "storyreel.json"
        if execute:
            return write_json(path, data)
        return path


def _seconds(frame: int, fps: int) -> str:
    return f"{frame / float(fps):.6f}"


def _pipeline_root() -> Path:
    env_root = os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[3]
