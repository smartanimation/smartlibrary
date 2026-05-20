from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json


@dataclass(frozen=True)
class ReviewLayerMedia:
    layer: str
    take: str
    output: str
    pattern: str
    first_file: str = ""
    last_file: str = ""
    file_count: int = 0
    frame_range: list[int] = field(default_factory=list)
    order: int = 0
    ae_slot: str = ""


@dataclass(frozen=True)
class ReviewPackage:
    episode: str
    sequence: str
    shot: str
    department: str
    version: str
    review_json: str
    version_dir: str
    fps: int
    frame_range: list[int]
    layer_order: list[str]
    layers: list[ReviewLayerMedia]

    @property
    def code(self) -> str:
        return f"{self.episode}_{self.sequence}_{self.shot}_{self.department}_{self.version}"


class ViewerService:
    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.project_root = Path(project_root)

    def list_review_packages(self) -> list[ReviewPackage]:
        reviews = []
        shots_root = self.project_root / "shots"
        if not shots_root.exists():
            return []
        for latest_json in shots_root.glob("*/*/*/publish/review/*/latest.json"):
            latest = read_json(latest_json, {})
            if not isinstance(latest, dict) or not latest.get("path"):
                continue
            review_json = latest_json.parent / latest["path"]
            if review_json.exists():
                package = self.review_package_from_json(review_json)
                if package:
                    reviews.append(package)
        return sorted(reviews, key=lambda item: (item.episode, item.sequence, item.shot, item.department, item.version))

    def review_package_from_json(self, path: str | Path) -> ReviewPackage | None:
        review_json = Path(path)
        data = read_json(review_json, {})
        if not isinstance(data, dict):
            return None
        version_dir = review_json.parent
        layer_order = list((data.get("ae") or {}).get("layer_order") or (data.get("layers") or {}).keys())
        layers = []
        for layer_name in layer_order:
            layer_data = (data.get("layers") or {}).get(layer_name) or {}
            media = _layer_media(version_dir, layer_name, layer_data)
            if media:
                layers.append(media)
        return ReviewPackage(
            episode=str(data.get("episode") or ""),
            sequence=str(data.get("sequence") or ""),
            shot=str(data.get("shot") or version_dir.parents[3].name),
            department=str(data.get("department") or data.get("subset") or version_dir.parent.name),
            version=str(data.get("version") or version_dir.name),
            review_json=str(review_json),
            version_dir=str(version_dir),
            fps=int(data.get("fps") or 24),
            frame_range=list(data.get("frame_range") or []),
            layer_order=layer_order,
            layers=layers,
        )

    def rv_executable(self) -> Path | None:
        env_path = os.environ.get("OPENRV_PATH") or os.environ.get("RV_PATH")
        if env_path and Path(env_path).exists():
            return Path(env_path)
        config_path = (((self.project_config.load("tools.yml").get("tools") or {}).get("openrv") or {}).get("path") or "").strip()
        if config_path and Path(config_path).exists():
            return Path(config_path)
        found = shutil.which("rv.exe") or shutil.which("rv")
        return Path(found) if found else None

    def rv_args_for_package(self, package: ReviewPackage) -> list[str]:
        return [str(_rv_input_path(package.version_dir, layer)) for layer in package.layers if _rv_input_path(package.version_dir, layer)]

    def rv_args_for_layer(self, package: ReviewPackage, layer_name: str) -> list[str]:
        return [
            str(_rv_input_path(package.version_dir, layer))
            for layer in package.layers
            if layer.layer == layer_name and _rv_input_path(package.version_dir, layer)
        ]

    def hud_data(self, package: ReviewPackage) -> dict[str, Any]:
        return {
            "shot": package.shot,
            "episode": package.episode,
            "sequence": package.sequence,
            "department": package.department,
            "version": package.version,
            "fps": package.fps,
            "frame_range": package.frame_range,
            "layers": [layer.layer for layer in package.layers],
            "review_json": package.review_json,
        }


def _layer_media(version_dir: Path, layer_name: str, layer_data: dict[str, Any]) -> ReviewLayerMedia | None:
    actual = (layer_data.get("actual_outputs") or {}).get("beauty") or {}
    outputs = layer_data.get("outputs") or {}
    pattern = str(actual.get("pattern") or outputs.get("beauty") or "")
    if not pattern:
        return None
    return ReviewLayerMedia(
        layer=layer_name,
        take=str(layer_data.get("take") or ""),
        output="beauty",
        pattern=pattern,
        first_file=str(actual.get("first_file") or ""),
        last_file=str(actual.get("last_file") or ""),
        file_count=int(actual.get("file_count") or 0),
        frame_range=list(actual.get("frame_range") or []),
        order=int(layer_data.get("order") or 0),
        ae_slot=str(layer_data.get("ae_slot") or layer_name),
    )


def _rv_input_path(version_dir: str | Path, layer: ReviewLayerMedia) -> Path | None:
    version_dir = Path(version_dir)
    if layer.first_file:
        path = version_dir / layer.first_file
        return _sequence_pattern_from_first_file(path) if path.exists() else None
    pattern = version_dir / layer.pattern
    parent = pattern.parent
    if not parent.exists():
        return None
    matches = sorted(parent.glob("beauty_*.png"))
    return _sequence_pattern_from_first_file(matches[0]) if matches else None


def _sequence_pattern_from_first_file(path: Path) -> Path:
    stem = path.stem
    if "_" not in stem:
        return path
    prefix, frame = stem.rsplit("_", 1)
    if not frame.isdigit():
        return path
    return path.with_name(f"{prefix}_%0{len(frame)}d{path.suffix}")
