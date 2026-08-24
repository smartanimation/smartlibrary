from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig, expand_config_tokens
from smartlib.core.metadata import read_json
from smartlib.core.path_resolver import ProjectPaths


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
    package_type: str = ""

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
        self.paths = ProjectPaths(
            self.project_root,
            templates=project_config.templates,
            project_name=project_config.project_name,
            shot_dept_partitions=(project_config.base.get("shot_dept_partitions") or {}),
        )

    def list_review_packages(self) -> list[ReviewPackage]:
        reviews = []
        shots_root = self.paths.shots_root()
        if not shots_root.exists():
            return []
        latest_paths = list(shots_root.glob("*/*/*/publish/review/*/latest.json"))
        latest_paths.extend(shots_root.glob("*/*/*/publish/review/*/*/latest.json"))
        latest_paths.extend(shots_root.glob("*/*/*/output/review/*/latest.json"))
        latest_paths.extend(shots_root.glob("*/*/*/output/review/*/*/latest.json"))
        sequences_root = self.paths.sequences_root()
        latest_paths.extend(sequences_root.glob("*/*/publish/review/*/latest.json"))
        latest_paths.extend(sequences_root.glob("*/*/publish/review/*/*/latest.json"))
        latest_paths.extend(sequences_root.glob("*/*/output/review/*/latest.json"))
        latest_paths.extend(sequences_root.glob("*/*/output/review/*/*/latest.json"))
        for latest_json in latest_paths:
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
        version_dir = review_json.parent.parent if review_json.parent.name == "metadata" else review_json.parent
        if data.get("type") == "quick_preview":
            layer_order, layers = _quick_preview_layers(version_dir, data)
        elif data.get("rv_mode") == "editorial_sequence" and data.get("shots"):
            layer_order, layers = _editorial_sequence_layers(version_dir, data)
        else:
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
            shot=str(data.get("shot") or data.get("asset") or version_dir.parents[3].name),
            department=str(data.get("department") or data.get("subset") or version_dir.parent.name),
            version=str(data.get("version") or version_dir.name),
            review_json=str(review_json),
            version_dir=str(version_dir),
            fps=int(data.get("fps") or 24),
            frame_range=list(data.get("frame_range") or []),
            layer_order=layer_order,
            layers=layers,
            package_type=str(data.get("type") or ""),
        )

    def rv_executable(self) -> Path | None:
        config_path = (((self.project_config.load("tools.yml").get("tools") or {}).get("openrv") or {}).get("path") or "").strip()
        if config_path:
            config_path = expand_config_tokens(config_path, self.project_config)
        if config_path and Path(config_path).exists():
            return Path(config_path)
        env_path = os.environ.get("OPENRV_PATH") or os.environ.get("RV_PATH")
        if env_path and Path(env_path).exists():
            return Path(env_path)
        found = shutil.which("rv.exe") or shutil.which("rv")
        return Path(found) if found else None

    def rvpush_executable(self) -> Path | None:
        env_path = os.environ.get("RVPUSH_PATH")
        if env_path and Path(env_path).exists():
            return Path(env_path)
        rv = self.rv_executable()
        if rv:
            for executable in ("rvpush.exe", "rvpush"):
                sibling = rv.with_name(executable)
                if sibling.exists():
                    return sibling
        found = shutil.which("rvpush.exe") or shutil.which("rvpush")
        return Path(found) if found else None

    def rv_args_for_package(self, package: ReviewPackage) -> list[str]:
        if package.package_type == "quick_preview":
            inputs = [
                str(_rv_input_path(package.version_dir, layer))
                for layer in package.layers
                if _rv_input_path(package.version_dir, layer)
            ]
            if inputs:
                return ["-tile", "-layout", "packed", "-view", "defaultLayout", *inputs]
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

    def rv_session_for_quick_preview(self, package: ReviewPackage) -> Path | None:
        """Create an RV layout session so beauty/wireframe/bbox open as a grid."""
        media = [
            (layer.layer, _rv_input_path(package.version_dir, layer))
            for layer in package.layers
            if _rv_input_path(package.version_dir, layer)
        ]
        if len(media) <= 1:
            return None
        session_path = Path(package.version_dir) / "quick_preview_grid.rv"
        try:
            session = _new_rv_session(self.rv_executable())
            if session is None:
                return None
            layout = session.newNode("Layout", f"{package.shot} Quick Preview")
            layout.setLayoutMode("packed")
            for label, path in media:
                source = session.newNode("Source", label)
                source.setMedia(str(path).replace("\\", "/"))
                layout.addInput(source)
            session.setViewNode(layout)
            session.write(str(session_path))
        except Exception:
            return None
        return session_path if session_path.exists() else None


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


def _quick_preview_layers(version_dir: Path, data: dict[str, Any]) -> tuple[list[str], list[ReviewLayerMedia]]:
    outputs = data.get("outputs") or {}
    layer_order = [name for name in ("beauty", "wireframe", "bbox") if outputs.get(name)]
    layer_order.extend(name for name in outputs if name not in layer_order and outputs.get(name))
    layers = []
    for order, layer_name in enumerate(layer_order):
        paths = [Path(path) for path in outputs.get(layer_name) or []]
        files = [path for path in paths if path.exists()]
        if not files:
            continue
        first_file = files[0]
        last_file = files[-1]
        layers.append(
            ReviewLayerMedia(
                layer=layer_name,
                take="",
                output=layer_name,
                pattern=_relative_media_path(version_dir, first_file),
                first_file=_relative_media_path(version_dir, first_file),
                last_file=_relative_media_path(version_dir, last_file),
                file_count=len(files),
                frame_range=[1, len(files)],
                order=order,
                ae_slot=layer_name,
            )
        )
    return layer_order, layers


def _editorial_sequence_layers(version_dir: Path, data: dict[str, Any]) -> tuple[list[str], list[ReviewLayerMedia]]:
    rows = sorted((data.get("shots") or {}).items(), key=lambda item: int((item[1] or {}).get("sequence_range", [0])[0]))
    layer_order = [name for name, _row in rows]
    layers = []
    for order, (shot_name, row) in enumerate(rows):
        if not isinstance(row, dict):
            continue
        first_file = str(row.get("first_file") or "")
        last_file = str(row.get("last_file") or "")
        pattern = str(row.get("file") or first_file)
        layers.append(
            ReviewLayerMedia(
                layer=shot_name,
                take=str(row.get("version") or ""),
                output="beauty",
                pattern=pattern,
                first_file=first_file,
                last_file=last_file,
                file_count=int(row.get("file_count") or 0),
                frame_range=list(row.get("frame_range") or []),
                order=order,
                ae_slot=shot_name,
            )
        )
    return layer_order, layers


def _relative_media_path(version_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(version_dir).as_posix()
    except ValueError:
        return path.as_posix()


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


def _new_rv_session(rv_executable: Path | None):
    search_roots = []
    if rv_executable:
        rv_root = Path(rv_executable).resolve().parent.parent
        search_roots.append(rv_root / "src" / "python")
    search_roots.extend(
        [
            Path(os.environ.get("RV_HOME", "")) / "src" / "python",
            Path("C:/Program Files/ShotGrid/RV-2023.0.2/src/python"),
        ]
    )
    for root in search_roots:
        if root and root.exists():
            root_text = str(root)
            if root_text not in sys.path:
                sys.path.insert(0, root_text)
    try:
        from rvSession.rvSession import Session
    except Exception:
        return None
    return Session()
