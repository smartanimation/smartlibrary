from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

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
        output_stem = _prefix_from_pattern(final_pattern)
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
            compression="jpg",
            widthHeight=[int(resolution[0]), int(resolution[1])],
        )
        _normalize_playblast_sequence(output_stem, start_frame, end_frame, ".jpg")
        files = _sequence_files(output_stem, start_frame, end_frame, ".jpg")
        results[layer_name] = {
            "pattern": beauty_pattern,
            "frame_range": [start_frame, end_frame],
            "file_count": len(files),
            "first_file": _relative_to(version_dir, files[0]) if files else "",
            "last_file": _relative_to(version_dir, files[-1]) if files else "",
        }

    _write_export_result(plan, review_data, results)

    return results


def display_layers(cmds=None) -> list[str]:
    """Return user display layers in stable scene order."""
    if cmds is None:
        try:
            import maya.cmds as cmds
        except ImportError as exc:
            raise RuntimeError("Display layers are available inside Maya.") from exc
    return [
        str(layer)
        for layer in (cmds.ls(type="displayLayer") or [])
        if str(layer) != "defaultLayer" and not str(layer).startswith(":")
    ]


def display_layer_members(layer: str, cmds=None) -> list[str]:
    if cmds is None:
        import maya.cmds as cmds
    return [
        str(member)
        for member in (cmds.editDisplayLayerMembers(layer, query=True, fullNames=True) or [])
    ]


def export_display_layer_sequences(
    plan,
    layer_nodes: Mapping[str, str],
    *,
    cmds=None,
) -> dict[str, dict[str, Any]]:
    """Export review layers by isolating existing Maya displayLayer nodes."""
    if cmds is None:
        try:
            import maya.cmds as cmds
        except ImportError as exc:
            raise RuntimeError("Review playblast is available inside Maya.") from exc

    review_data = dict(plan.review_data)
    review_data.pop("publish", None)
    version_dir = Path(plan.version_dir)
    default_range = review_data.get("frame_range") or [1001, 1001]
    scene_layers = display_layers(cmds)
    original_visibility = {
        layer: bool(cmds.getAttr(f"{layer}.visibility"))
        for layer in scene_layers
        if cmds.objExists(f"{layer}.visibility")
    }
    results: dict[str, dict[str, Any]] = {}
    try:
        for layer_name, layer in (review_data.get("layers") or {}).items():
            node = str(layer_nodes.get(layer_name) or "")
            if not node or node not in scene_layers:
                continue
            beauty_pattern = (layer.get("outputs") or {}).get("beauty")
            if not beauty_pattern:
                continue
            for scene_layer in scene_layers:
                plug = f"{scene_layer}.visibility"
                if cmds.objExists(plug):
                    cmds.setAttr(plug, scene_layer == node)
            frame_range = layer.get("frame_range") or default_range
            start_frame, end_frame = int(frame_range[0]), int(frame_range[1])
            final_pattern = version_dir / beauty_pattern
            output_stem = _prefix_from_pattern(final_pattern)
            final_pattern.parent.mkdir(parents=True, exist_ok=True)
            resolution = layer.get("resolution") or [960, 540]
            kwargs = {
                "startTime": start_frame,
                "endTime": end_frame,
                "format": "image",
                "filename": str(output_stem),
                "forceOverwrite": True,
                "sequenceTime": False,
                "clearCache": True,
                "viewer": False,
                "showOrnaments": True,
                "percent": 100,
                "compression": "jpg",
                "widthHeight": [int(resolution[0]), int(resolution[1])],
            }
            camera = str(layer.get("camera") or "").strip()
            panel = _camera_panel(cmds, camera)
            if panel:
                kwargs["editorPanelName"] = panel
            cmds.playblast(**kwargs)
            _normalize_playblast_sequence(output_stem, start_frame, end_frame, ".jpg")
            files = _sequence_files(output_stem, start_frame, end_frame, ".jpg")
            results[layer_name] = {
                "display_layer": node,
                "pattern": beauty_pattern,
                "frame_range": [start_frame, end_frame],
                "file_count": len(files),
                "first_file": _relative_to(version_dir, files[0]) if files else "",
                "last_file": _relative_to(version_dir, files[-1]) if files else "",
            }
    finally:
        for layer, visible in original_visibility.items():
            if cmds.objExists(f"{layer}.visibility"):
                cmds.setAttr(f"{layer}.visibility", visible)

    _write_export_result(plan, review_data, results)
    return results


def _camera_panel(cmds, camera: str) -> str:
    if not camera or not cmds.objExists(camera):
        return ""
    for panel in (cmds.getPanel(type="modelPanel") or []):
        try:
            if cmds.modelPanel(panel, query=True, camera=True) == camera:
                return str(panel)
        except Exception:
            continue
    panels = cmds.getPanel(type="modelPanel") or []
    if not panels:
        return ""
    panel = str(panels[0])
    cmds.modelPanel(panel, edit=True, camera=camera)
    return panel


def _set_review_layer_visibility(cmds, target_layer: str) -> None:
    layers = [layer for layer in (cmds.ls(type="displayLayer") or []) if layer.startswith("review_")]
    for layer in layers:
        if cmds.objExists(f"{layer}.visibility"):
            cmds.setAttr(f"{layer}.visibility", layer == target_layer)


def _prefix_from_pattern(pattern: Path) -> Path:
    name = pattern.name.replace("_####", "").replace(".####", "").replace("####", "")
    return pattern.parent / Path(name).with_suffix("").name


def _normalize_playblast_sequence(prefix: Path, start_frame: int, end_frame: int, suffix: str) -> None:
    for frame in range(start_frame, end_frame + 1):
        frame_text = f"{frame:04d}"
        target = prefix.parent / f"{prefix.name}_{frame_text}{suffix}"
        candidates = [
            prefix.parent / f"{prefix.name}.{frame_text}{suffix}",
            prefix.parent / f"{prefix.name}_.{frame_text}{suffix}",
            prefix.parent / f"{prefix.name}_{frame_text}{suffix}",
        ]
        for source in candidates:
            if source == target or not source.exists():
                continue
            if target.exists():
                target.unlink()
            source.rename(target)
            break


def _sequence_files(prefix: Path, start_frame: int, end_frame: int, suffix: str) -> list[Path]:
    files = []
    for frame in range(start_frame, end_frame + 1):
        path = prefix.parent / f"{prefix.name}_{frame:04d}{suffix}"
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
