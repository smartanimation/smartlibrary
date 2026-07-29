from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
import json
import time
from typing import Any, Mapping

from smartlib.core.metadata import read_json, write_json


SMART_PLAYBLAST_NODE = "smartPlayblastInfo"
SMART_PLAYBLAST_ATTR = "settingsJson"
SMART_PLAYBLAST_FILE_INFO = "smartPlayblastSettings"
SMART_PLAYBLAST_OPTION_VAR = "smartPlayblastSettings"
SMART_PLAYBLAST_EXCLUDED_ATTR = "smartPlayblastExcluded"
SMART_PLAYBLAST_ROW_ATTR = "smartPlayblastRowSettings"
SMART_PLAYBLAST_ORDER_ATTR = "smartPlayblastOrder"
ALL_DISPLAY_LAYERS = "__SMART_PLAYBLAST_ALL__"


def load_scene_playblast_settings(cmds=None) -> dict[str, Any]:
    if cmds is None:
        import maya.cmds as cmds
    plug = f"{SMART_PLAYBLAST_NODE}.{SMART_PLAYBLAST_ATTR}"
    values = []
    try:
        if cmds.optionVar(exists=SMART_PLAYBLAST_OPTION_VAR):
            values.append((cmds.optionVar(query=SMART_PLAYBLAST_OPTION_VAR), False))
    except Exception:
        pass
    try:
        file_info = cmds.fileInfo(SMART_PLAYBLAST_FILE_INFO, query=True) or []
        if isinstance(file_info, (list, tuple)):
            # Maya may retain duplicate fileInfo keys in older scenes. The
            # most recently written value is returned last.
            values.extend((value, True) for value in reversed(file_info))
        elif file_info:
            values.append((file_info, True))
    except Exception:
        pass
    if cmds.objExists(plug):
        try:
            values.append((cmds.getAttr(plug) or "", True))
        except Exception:
            pass
    candidates = []
    for value, scene_scoped in values:
        try:
            data = json.loads(value)
            if isinstance(data, dict):
                candidates.append((data, scene_scoped))
        except (TypeError, ValueError):
            continue
    if not candidates:
        return {}
    try:
        current_scene = cmds.file(query=True, sceneName=True) or ""
    except Exception:
        current_scene = ""
    matching = []
    for item, scene_scoped in candidates:
        recorded_scene = item.get("_smart_playblast_scene")
        # network/fileInfo values belong to the opened scene even when Save As
        # changed its filename after Smart Playblast was executed.
        if scene_scoped or not recorded_scene or recorded_scene == current_scene:
            matching.append(item)
    if not matching:
        return {}
    latest = max(
        matching,
        key=lambda item: int(item.get("_smart_playblast_saved_at") or 0),
    )
    latest = dict(latest)
    latest.pop("_smart_playblast_saved_at", None)
    latest.pop("_smart_playblast_scene", None)
    return latest


def save_scene_playblast_settings(settings: Mapping[str, Any], cmds=None) -> str:
    """Persist Smart Playblast UI state in a Maya network node."""
    if cmds is None:
        import maya.cmds as cmds
    payload = dict(settings)
    payload["_smart_playblast_saved_at"] = time.time_ns()
    try:
        payload["_smart_playblast_scene"] = cmds.file(
            query=True, sceneName=True
        ) or ""
    except Exception:
        payload["_smart_playblast_scene"] = ""
    value = json.dumps(payload, ensure_ascii=False)
    saved = False
    try:
        cmds.optionVar(stringValue=(SMART_PLAYBLAST_OPTION_VAR, value))
        saved = True
    except Exception:
        pass
    try:
        if not cmds.objExists(SMART_PLAYBLAST_NODE):
            cmds.createNode("network", name=SMART_PLAYBLAST_NODE)
        plug = f"{SMART_PLAYBLAST_NODE}.{SMART_PLAYBLAST_ATTR}"
        if not cmds.objExists(plug):
            cmds.addAttr(SMART_PLAYBLAST_NODE, longName=SMART_PLAYBLAST_ATTR, dataType="string")
        cmds.setAttr(plug, value, type="string")
        saved = True
    except Exception:
        pass
    try:
        try:
            cmds.fileInfo(SMART_PLAYBLAST_FILE_INFO, remove=True)
        except Exception:
            pass
        cmds.fileInfo(SMART_PLAYBLAST_FILE_INFO, value)
        saved = True
    except Exception:
        pass
    if not saved:
        raise RuntimeError("Could not save Smart Playblast settings into the Maya scene.")
    return SMART_PLAYBLAST_NODE


def publish_sequence_metadata(plan, results: Mapping[str, Mapping[str, Any]]) -> Path:
    """Publish metadata for image sequences that have already been exported."""
    from smartlib.core.publish import PublishRecord, write_publish_json

    review_data = dict(plan.review_data)
    review_data.pop("publish", None)
    review_data["export_status"] = "image_sequence_published"
    review_data["beauty_exports"] = dict(results)
    for layer_name, result in results.items():
        layer = (review_data.get("layers") or {}).get(layer_name)
        if isinstance(layer, dict):
            layer["actual_outputs"] = {"beauty": dict(result)}
    write_json(plan.review_json, review_data)
    record = PublishRecord(
        publish_type="review",
        subset=str(plan.subset),
        version=int(plan.version),
        files=dict(plan.files),
        source_workfile=str((plan.review_data.get("publish") or {}).get("source_workfile") or ""),
        comment="Smart Playblast image sequence metadata",
        status="published",
    )
    write_publish_json(plan.version_dir, record)
    write_json(plan.publish_json, record.to_dict())
    write_json(
        Path(plan.version_dir) / "metadata" / "playblast.json",
        {"status": "published", "version": review_data.get("version"), "take": review_data.get("take")},
    )
    write_json(
        Path(plan.version_dir) / "metadata" / "source_scene.json",
        {"source_workfile": record.source_workfile},
    )
    version_label = str(review_data.get("version") or f"v{int(plan.version):03d}")
    take_label = str(review_data.get("take") or Path(plan.version_dir).name)
    base_dir = Path(plan.version_dir).parents[1]
    write_json(
        base_dir / "latest.json",
        {
            "version": version_label,
            "take": take_label,
            "path": f"{version_label}/{take_label}/metadata/review.json",
        },
    )
    versions_path = base_dir / "versions.json"
    versions = read_json(versions_path, [])
    if not isinstance(versions, list):
        versions = []
    next_versions = [
        item for item in versions
        if isinstance(item, dict) and item.get("version") != version_label
    ]
    next_versions.append({"version": version_label, "status": "published"})
    write_json(versions_path, next_versions)
    return Path(plan.review_json)


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
            compression="png",
            widthHeight=[int(resolution[0]), int(resolution[1])],
        )
        _normalize_playblast_sequence(output_stem, start_frame, end_frame, ".png")
        files = _sequence_files(output_stem, start_frame, end_frame, ".png")
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


def set_display_layer_excluded(layer: str, excluded: bool = True, cmds=None) -> None:
    if cmds is None:
        import maya.cmds as cmds
    plug = f"{layer}.{SMART_PLAYBLAST_EXCLUDED_ATTR}"
    if not cmds.objExists(plug):
        cmds.addAttr(layer, longName=SMART_PLAYBLAST_EXCLUDED_ATTR, attributeType="bool")
    cmds.setAttr(plug, bool(excluded))


def is_display_layer_excluded(layer: str, cmds=None) -> bool:
    if cmds is None:
        import maya.cmds as cmds
    plug = f"{layer}.{SMART_PLAYBLAST_EXCLUDED_ATTR}"
    if not cmds.objExists(plug):
        return False
    try:
        return bool(cmds.getAttr(plug))
    except Exception:
        return False


def save_display_layer_row_settings(layer: str, settings: Mapping[str, Any], cmds=None) -> None:
    if cmds is None:
        import maya.cmds as cmds
    plug = f"{layer}.{SMART_PLAYBLAST_ROW_ATTR}"
    if not cmds.objExists(plug):
        cmds.addAttr(layer, longName=SMART_PLAYBLAST_ROW_ATTR, dataType="string")
    cmds.setAttr(plug, json.dumps(dict(settings), ensure_ascii=False), type="string")


def load_display_layer_row_settings(layer: str, cmds=None) -> dict[str, Any]:
    if cmds is None:
        import maya.cmds as cmds
    plug = f"{layer}.{SMART_PLAYBLAST_ROW_ATTR}"
    if not cmds.objExists(plug):
        return {}
    try:
        data = json.loads(cmds.getAttr(plug) or "")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def set_display_layer_order(layer: str, order: int, cmds=None) -> None:
    if cmds is None:
        import maya.cmds as cmds
    plug = f"{layer}.{SMART_PLAYBLAST_ORDER_ATTR}"
    if not cmds.objExists(plug):
        cmds.addAttr(layer, longName=SMART_PLAYBLAST_ORDER_ATTR, attributeType="long")
    cmds.setAttr(plug, int(order))


def display_layer_order(layer: str, cmds=None) -> int | None:
    if cmds is None:
        import maya.cmds as cmds
    plug = f"{layer}.{SMART_PLAYBLAST_ORDER_ATTR}"
    if not cmds.objExists(plug):
        return None
    try:
        return int(cmds.getAttr(plug))
    except (TypeError, ValueError):
        return None


def export_display_layer_sequences(
    plan,
    layer_nodes: Mapping[str, str],
    *,
    cmds=None,
    write_metadata: bool = True,
    project_config=None,
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
            if not node or (node != ALL_DISPLAY_LAYERS and node not in scene_layers):
                continue
            beauty_pattern = (layer.get("outputs") or {}).get("beauty")
            if not beauty_pattern:
                continue
            for scene_layer in scene_layers:
                plug = f"{scene_layer}.visibility"
                if cmds.objExists(plug):
                    cmds.setAttr(
                        plug,
                        True if node == ALL_DISPLAY_LAYERS else scene_layer == node,
                    )
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
                "compression": "png",
                "widthHeight": [int(resolution[0]), int(resolution[1])],
            }
            camera = str(layer.get("camera") or "").strip()
            panel = _camera_panel(cmds, camera)
            if panel:
                kwargs["editorPanelName"] = panel
            preset_name = str(layer.get("playblast_preset") or "")
            if project_config is not None and preset_name:
                from smartlib.dcc.maya.playblast_preset import applied_playblast_preset
                with applied_playblast_preset(project_config, preset_name):
                    cmds.playblast(**kwargs)
            else:
                cmds.playblast(**kwargs)
            _normalize_playblast_sequence(output_stem, start_frame, end_frame, ".png")
            files = _sequence_files(output_stem, start_frame, end_frame, ".png")
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

    if write_metadata:
        _write_export_result(plan, review_data, results)
    return results


def export_preview_render_groups(
    plan: Mapping[str, Any],
    *,
    cmds=None,
    project_config=None,
) -> dict[str, dict[str, Any]]:
    """Playblast directly into independently versioned Preview Render groups."""
    if cmds is None:
        try:
            import maya.cmds as cmds
        except ImportError as exc:
            raise RuntimeError("Preview Render is available inside Maya.") from exc
    scene_layers = display_layers(cmds)
    original_visibility = {
        layer: bool(cmds.getAttr(f"{layer}.visibility"))
        for layer in scene_layers
        if cmds.objExists(f"{layer}.visibility")
    }
    original_gate_guide_state = _capture_smart_gate_guide_state(cmds)
    results: dict[str, dict[str, Any]] = {}
    try:
        for group in plan.get("groups") or []:
            group_name = str(group.get("group") or "")
            source_layer = str(group.get("source_layer") or "")
            if not group_name or not source_layer:
                continue
            if source_layer == "ALL":
                members = []
                for layer in scene_layers:
                    members.extend(display_layer_members(layer, cmds))
            elif source_layer in scene_layers:
                members = display_layer_members(source_layer, cmds)
            else:
                continue
            if not members:
                continue
            for layer in scene_layers:
                plug = f"{layer}.visibility"
                if cmds.objExists(plug):
                    cmds.setAttr(plug, source_layer == "ALL" or layer == source_layer)
            output_dir = Path(str(group.get("output_dir") or ""))
            output_dir.mkdir(parents=True, exist_ok=True)
            pattern = str(group.get("pattern") or "beauty_####.png")
            output_stem = _prefix_from_pattern(output_dir / pattern)
            frame_range = group.get("frame_range") or [1, 1]
            start_frame, end_frame = int(frame_range[0]), int(frame_range[1])
            resolution = group.get("resolution") or [1280, 720]
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
                "compression": "png",
                "widthHeight": [int(resolution[0]), int(resolution[1])],
            }
            camera = str(group.get("camera") or "").strip()
            panel = _camera_panel(cmds, camera)
            if panel:
                kwargs["editorPanelName"] = panel
            preset_name = str(group.get("playblast_preset") or "")
            with _visible_smart_gate_guides(
                cmds, panel, int(resolution[1])
            ):
                if project_config is not None and preset_name:
                    from smartlib.dcc.maya.playblast_preset import applied_playblast_preset

                    with applied_playblast_preset(project_config, preset_name):
                        cmds.playblast(**kwargs)
                else:
                    cmds.playblast(**kwargs)
            _normalize_playblast_sequence(output_stem, start_frame, end_frame, ".png")
            files = _sequence_files(output_stem, start_frame, end_frame, ".png")
            if not files:
                continue
            results[group_name] = {
                "file_count": len(files),
                "first_file": str(files[0]),
                "last_file": str(files[-1]),
                "members": sorted(set(str(member) for member in members), key=str.lower),
                "source_first_file": str(files[0]),
            }
    finally:
        for layer, visible in original_visibility.items():
            if cmds.objExists(f"{layer}.visibility"):
                cmds.setAttr(f"{layer}.visibility", visible)
        _restore_smart_gate_guide_state(cmds, original_gate_guide_state)
    return results


@contextmanager
def _visible_smart_gate_guides(cmds: Any, panel: str, output_height: int):
    nodes = []
    for node_type in ("SmartViewportGateGuide", "SmartGateGuide", "SmartGateGuid"):
        try:
            nodes.extend(cmds.ls(type=node_type, long=True) or [])
        except Exception:
            pass
    for pattern in ("SmartGateGuide*", "*SmartGateGuide*"):
        try:
            nodes.extend(cmds.ls(pattern, long=True) or [])
        except Exception:
            pass
    nodes = _canonical_maya_nodes(cmds, nodes)
    expanded = list(nodes)
    for node in nodes:
        try:
            expanded.extend(cmds.listRelatives(node, parent=True, fullPath=True) or [])
            expanded.extend(cmds.listRelatives(node, shapes=True, fullPath=True) or [])
        except Exception:
            pass
    expanded = _canonical_maya_nodes(cmds, expanded)
    state = {}
    for node in expanded:
        for attr in ("visibility", "overrideEnabled", "overrideVisibility", "fontScale"):
            plug = f"{node}.{attr}"
            if not cmds.objExists(plug):
                continue
            try:
                state[plug] = cmds.getAttr(plug)
            except Exception:
                continue
            try:
                if attr == "fontScale":
                    # Use one review-readable size for every output. Scaling
                    # from output height made mixed-resolution layers differ.
                    cmds.setAttr(plug, 2.0)
                elif attr == "overrideEnabled":
                    cmds.setAttr(plug, True)
                else:
                    cmds.setAttr(plug, True)
            except Exception:
                pass
    locator_state = None
    if panel:
        try:
            locator_state = cmds.modelEditor(panel, query=True, locators=True)
            cmds.modelEditor(panel, edit=True, locators=True)
        except Exception:
            locator_state = None
    try:
        yield
    finally:
        if panel and locator_state is not None:
            try:
                cmds.modelEditor(panel, edit=True, locators=locator_state)
            except Exception:
                pass
        for plug, value in state.items():
            if cmds.objExists(plug):
                try:
                    cmds.setAttr(plug, value)
                except Exception:
                    pass
        try:
            cmds.refresh(force=True)
        except Exception:
            pass


def _canonical_maya_nodes(cmds: Any, nodes) -> list[str]:
    canonical = []
    seen = set()
    for node in nodes:
        try:
            matches = cmds.ls(str(node), long=True) or []
        except Exception:
            matches = [node]
        for match in matches:
            name = str(match)
            if name in seen:
                continue
            seen.add(name)
            canonical.append(name)
    return canonical


def _capture_smart_gate_guide_state(cmds: Any) -> dict[str, Any]:
    nodes = []
    for node_type in ("SmartViewportGateGuide", "SmartGateGuide", "SmartGateGuid"):
        try:
            nodes.extend(cmds.ls(type=node_type, long=True) or [])
        except Exception:
            pass
    for pattern in ("SmartGateGuide*", "*SmartGateGuide*"):
        try:
            nodes.extend(cmds.ls(pattern, long=True) or [])
        except Exception:
            pass
    expanded = list(_canonical_maya_nodes(cmds, nodes))
    for node in list(expanded):
        try:
            expanded.extend(cmds.listRelatives(node, parent=True, fullPath=True) or [])
            expanded.extend(cmds.listRelatives(node, shapes=True, fullPath=True) or [])
        except Exception:
            pass
    state = {}
    for node in _canonical_maya_nodes(cmds, expanded):
        for attr in ("visibility", "overrideEnabled", "overrideVisibility", "fontScale"):
            plug = f"{node}.{attr}"
            if not cmds.objExists(plug):
                continue
            try:
                state[plug] = cmds.getAttr(plug)
            except Exception:
                pass
    return state


def _restore_smart_gate_guide_state(cmds: Any, state: Mapping[str, Any]) -> None:
    for plug, value in state.items():
        if not cmds.objExists(plug):
            continue
        try:
            cmds.setAttr(plug, value)
        except Exception:
            pass
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


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
