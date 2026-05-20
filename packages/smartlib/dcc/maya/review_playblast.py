from __future__ import annotations

from pathlib import Path
from typing import Any

from smartlib.core.metadata import write_json


def export_beauty_sequences(plan) -> dict[str, str]:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Review playblast is available inside Maya.") from exc

    version_dir = Path(plan.version_dir)
    review_data = dict(plan.review_data)
    review_data.pop("publish", None)
    frame_range = review_data.get("frame_range") or [1001, 1001]
    start_frame = int(frame_range[0])
    end_frame = int(frame_range[1])
    results = {}

    for layer_name, layer in (review_data.get("layers") or {}).items():
        if not layer.get("members"):
            continue
        outputs = layer.get("outputs") or {}
        beauty_pattern = outputs.get("beauty")
        if not beauty_pattern:
            continue
        final_pattern = version_dir / beauty_pattern
        output_stem = final_pattern.parent / "beauty"
        final_pattern.parent.mkdir(parents=True, exist_ok=True)
        resolution = layer.get("resolution") or [960, 540]
        _set_review_layer_visibility(cmds, f"review_{layer_name}")
        cmds.playblast(
            startTime=start_frame,
            endTime=end_frame,
            format="image",
            filename=str(output_stem),
            forceOverwrite=True,
            sequenceTime=False,
            clearCache=True,
            viewer=False,
            showOrnaments=True,
            percent=100,
            compression="png",
            widthHeight=[int(resolution[0]), int(resolution[1])],
        )
        _normalize_playblast_sequence(final_pattern.parent, "beauty", start_frame, end_frame)
        files = _sequence_files(final_pattern.parent, "beauty", start_frame, end_frame)
        results[layer_name] = {
            "pattern": beauty_pattern,
            "frame_range": [start_frame, end_frame],
            "file_count": len(files),
            "first_file": _relative_to(version_dir, files[0]) if files else "",
            "last_file": _relative_to(version_dir, files[-1]) if files else "",
        }

    _write_export_result(plan, review_data, results)

    return results


def _set_review_layer_visibility(cmds, target_layer: str) -> None:
    layers = [layer for layer in (cmds.ls(type="displayLayer") or []) if layer.startswith("review_")]
    for layer in layers:
        if cmds.objExists(f"{layer}.visibility"):
            cmds.setAttr(f"{layer}.visibility", layer == target_layer)


def _normalize_playblast_sequence(directory: Path, stem: str, start_frame: int, end_frame: int) -> None:
    for frame in range(start_frame, end_frame + 1):
        frame_text = f"{frame:04d}"
        target = directory / f"{stem}_{frame_text}.png"
        candidates = [
            directory / f"{stem}.{frame_text}.png",
            directory / f"{stem}_.{frame_text}.png",
            directory / f"{stem}_{frame_text}.png",
        ]
        for source in candidates:
            if source == target or not source.exists():
                continue
            if target.exists():
                target.unlink()
            source.rename(target)
            break


def _sequence_files(directory: Path, stem: str, start_frame: int, end_frame: int) -> list[Path]:
    files = []
    for frame in range(start_frame, end_frame + 1):
        path = directory / f"{stem}_{frame:04d}.png"
        if path.exists():
            files.append(path)
    return files


def _write_export_result(plan, review_data: dict[str, Any], results: dict[str, dict[str, Any]]) -> None:
    next_data = dict(review_data)
    next_data["export_status"] = "beauty_exported"
    next_data["beauty_exports"] = results
    for layer_name, result in results.items():
        layer_data = (next_data.get("layers") or {}).get(layer_name)
        if isinstance(layer_data, dict):
            layer_data["actual_outputs"] = {
                "beauty": result,
            }
    write_json(Path(plan.review_json), next_data)


def _relative_to(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
