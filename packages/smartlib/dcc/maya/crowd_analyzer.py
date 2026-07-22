from __future__ import annotations

import json
from math import dist
import re
from typing import Any

from smartlib.dcc.maya.crowd_animation import selected_animation_properties_node


def analyze_animation_publish(node: str | None = None, *, root: str | None = None) -> dict[str, Any]:
    cmds = _maya_cmds()
    node = node or selected_animation_properties_node()
    start = float(cmds.playbackOptions(query=True, minTime=True))
    end = float(cmds.playbackOptions(query=True, maxTime=True))
    duration_frames = max(0.0, end - start + 1.0)
    fps = _scene_fps(cmds)
    duration_seconds = duration_frames / fps if fps else 0.0
    unit = str(cmds.currentUnit(query=True, linear=True))
    unit_scale_to_houdini = _unit_scale_to_houdini(unit)
    root_node = _resolve_root(cmds, root, node)
    root_motion = _root_motion(cmds, root_node, start, end, unit, unit_scale_to_houdini, fps) if root_node else {}
    speed = float(root_motion.get("distance", 0.0)) / duration_seconds if duration_seconds else 0.0
    speed_houdini = speed * unit_scale_to_houdini
    if root_motion:
        root_motion["duration_frames"] = duration_frames
        root_motion["duration_seconds"] = duration_seconds
        root_motion["speed"] = speed
        root_motion["speed_houdini"] = speed_houdini
    bbox = _bounding_box(cmds, root_node)
    foot_contact = _foot_contact(cmds, start, end)
    loop = _is_looping(cmds, root_node, start, end) if root_node else False

    _set_number(cmds, node, "duration", duration_frames)
    _set_number(cmds, node, "durationFrames", duration_frames)
    _set_number(cmds, node, "durationSeconds", duration_seconds)
    _set_number(cmds, node, "fps", fps)
    _set_string(cmds, node, "unit", unit)
    _set_number(cmds, node, "startFrame", start)
    _set_number(cmds, node, "endFrame", end)
    _set_string(cmds, node, "rootMotion", json.dumps(root_motion, sort_keys=True))
    _set_number(cmds, node, "speed", speed)
    _set_number(cmds, node, "speedHoudini", speed_houdini)
    _set_bool(cmds, node, "loop", loop)
    _set_string(cmds, node, "boundingBox", json.dumps(bbox, sort_keys=True))
    _set_string(cmds, node, "footContact", json.dumps(foot_contact, sort_keys=True))

    return {
        "duration": duration_frames,
        "durationFrames": duration_frames,
        "durationSeconds": duration_seconds,
        "fps": fps,
        "unit": unit,
        "startFrame": start,
        "endFrame": end,
        "rootMotion": root_motion,
        "speed": speed,
        "speed_houdini": speed_houdini,
        "loop": loop,
        "boundingBox": bbox,
        "footContact": foot_contact,
    }


def _resolve_root(cmds: Any, root: str | None, animation_node: str | None = None) -> str:
    if root and cmds.objExists(root):
        return root
    source = _animation_root_motion_source(cmds, animation_node)
    if source:
        return source
    source = _metadata_root_motion_source(cmds)
    if source:
        return source
    selected = cmds.ls(selection=True, type="transform") or []
    if selected:
        return selected[0]
    for candidate in (
        "local_C0_ctl",
        "*:local_C0_ctl",
        "*:*:local_C0_ctl",
        "root",
        "Root",
        "Hips",
        "COG",
        "global_CTRL",
        "*:global_CTRL",
    ):
        matches = cmds.ls(candidate, type="transform") or []
        if matches:
            return matches[0]
    return ""


def _animation_root_motion_source(cmds: Any, animation_node: str | None) -> str:
    if not animation_node:
        return ""
    plug = f"{animation_node}.rootMotionSource"
    if not cmds.objExists(plug):
        return ""
    source = str(cmds.getAttr(plug) or "").strip()
    if source and cmds.objExists(source):
        return source
    return ""


def _metadata_root_motion_source(cmds: Any) -> str:
    for node in cmds.ls(type="transform") or []:
        role_plug = f"{node}.smartCrowdRole"
        if cmds.objExists(role_plug) and str(cmds.getAttr(role_plug) or "") == "root_motion":
            return node
        bool_plug = f"{node}.smartCrowdRootMotion"
        if cmds.objExists(bool_plug) and bool(cmds.getAttr(bool_plug)):
            return node
    return ""


def _root_motion(
    cmds: Any,
    node: str,
    start: float,
    end: float,
    unit: str,
    unit_scale_to_houdini: float,
    fps: float,
) -> dict[str, Any]:
    original = cmds.currentTime(query=True)
    try:
        cmds.currentTime(start, edit=True)
        start_pos = tuple(float(v) for v in cmds.xform(node, query=True, worldSpace=True, translation=True))
        start_local = _local_translate(cmds, node)
        cmds.currentTime(end, edit=True)
        end_pos = tuple(float(v) for v in cmds.xform(node, query=True, worldSpace=True, translation=True))
        end_local = _local_translate(cmds, node)
    finally:
        cmds.currentTime(original, edit=True)
    delta = tuple(end_pos[index] - start_pos[index] for index in range(3))
    local_delta = tuple(end_local[index] - start_local[index] for index in range(3))
    namespace, source_node = _split_namespace(node)
    distance_value = dist(start_pos, end_pos)
    return {
        "source": node,
        "source_namespace": namespace,
        "source_node": source_node,
        "axis": _dominant_translate_axis(local_delta if any(abs(v) > 1e-8 for v in local_delta) else delta),
        "unit": unit,
        "unit_scale_to_houdini": unit_scale_to_houdini,
        "fps": fps,
        "start": _vector_mapping(start_pos),
        "end": _vector_mapping(end_pos),
        "delta": _vector_mapping(delta),
        "distance": distance_value,
        "start_houdini": _scaled_vector_mapping(start_pos, unit_scale_to_houdini),
        "end_houdini": _scaled_vector_mapping(end_pos, unit_scale_to_houdini),
        "delta_houdini": _scaled_vector_mapping(delta, unit_scale_to_houdini),
        "distance_houdini": distance_value * unit_scale_to_houdini,
        "local": {
            "start": _vector_mapping(start_local),
            "end": _vector_mapping(end_local),
            "delta": _vector_mapping(local_delta),
        },
    }


def _bounding_box(cmds: Any, node: str) -> dict[str, Any]:
    if not node:
        return {}
    values = [float(v) for v in cmds.exactWorldBoundingBox(node)]
    return {
        "min": _vector_mapping((values[0], values[1], values[2])),
        "max": _vector_mapping((values[3], values[4], values[5])),
    }


def _foot_contact(cmds: Any, start: float, end: float) -> list[dict[str, Any]]:
    foot_nodes = []
    for pattern in ("*foot*", "*Foot*", "*ankle*", "*Ankle*"):
        foot_nodes.extend(cmds.ls(pattern, type="transform") or [])
    foot_nodes = sorted(set(foot_nodes))
    if not foot_nodes:
        return []
    original = cmds.currentTime(query=True)
    samples = {node: [] for node in foot_nodes}
    try:
        for frame in range(int(start), int(end) + 1):
            cmds.currentTime(frame, edit=True)
            for node in foot_nodes:
                pos = cmds.xform(node, query=True, worldSpace=True, translation=True)
                samples[node].append((frame, float(pos[1])))
    finally:
        cmds.currentTime(original, edit=True)
    contacts = []
    for node, values in samples.items():
        low = min(y for _frame, y in values)
        frames = [frame for frame, y in values if abs(y - low) <= 0.01]
        if frames:
            contacts.append({"node": node, "frames": frames})
    return contacts


def _is_looping(cmds: Any, node: str, start: float, end: float) -> bool:
    unit = str(cmds.currentUnit(query=True, linear=True))
    motion = _root_motion(cmds, node, start, end, unit, _unit_scale_to_houdini(unit), _scene_fps(cmds))
    return float(motion.get("distance", 0.0)) <= 0.01


def _set_number(cmds: Any, node: str, attr: str, value: float) -> None:
    if cmds.objExists(f"{node}.{attr}"):
        cmds.setAttr(f"{node}.{attr}", float(value))


def _set_string(cmds: Any, node: str, attr: str, value: str) -> None:
    if cmds.objExists(f"{node}.{attr}"):
        cmds.setAttr(f"{node}.{attr}", value, type="string")


def _set_bool(cmds: Any, node: str, attr: str, value: bool) -> None:
    if cmds.objExists(f"{node}.{attr}"):
        cmds.setAttr(f"{node}.{attr}", bool(value))


def _vector_mapping(value: tuple[float, float, float]) -> dict[str, float]:
    return {"x": value[0], "y": value[1], "z": value[2]}


def _scaled_vector_mapping(value: tuple[float, float, float], scale: float) -> dict[str, float]:
    return {"x": value[0] * scale, "y": value[1] * scale, "z": value[2] * scale}


def _local_translate(cmds: Any, node: str) -> tuple[float, float, float]:
    values = []
    for attr in ("translateX", "translateY", "translateZ"):
        plug = f"{node}.{attr}"
        values.append(float(cmds.getAttr(plug)) if cmds.objExists(plug) else 0.0)
    return (values[0], values[1], values[2])


def _dominant_translate_axis(delta: tuple[float, float, float]) -> str:
    axes = ("tx", "ty", "tz")
    index = max(range(3), key=lambda item: abs(delta[item]))
    return axes[index]


def _split_namespace(node: str) -> tuple[str, str]:
    leaf = str(node).split("|")[-1]
    parts = leaf.split(":")
    if len(parts) <= 1:
        return "", leaf
    return ":".join(parts[:-1]), parts[-1]


def _unit_scale_to_houdini(unit: str) -> float:
    return {
        "mm": 0.001,
        "millimeter": 0.001,
        "cm": 0.01,
        "centimeter": 0.01,
        "m": 1.0,
        "meter": 1.0,
        "km": 1000.0,
        "kilometer": 1000.0,
        "in": 0.0254,
        "inch": 0.0254,
        "ft": 0.3048,
        "foot": 0.3048,
        "yd": 0.9144,
        "yard": 0.9144,
    }.get(str(unit).lower(), 1.0)


def _scene_fps(cmds: Any) -> float:
    unit = str(cmds.currentUnit(query=True, time=True))
    named = {
        "game": 15.0,
        "film": 24.0,
        "pal": 25.0,
        "ntsc": 30.0,
        "show": 48.0,
        "palf": 50.0,
        "ntscf": 60.0,
    }
    if unit in named:
        return named[unit]
    match = re.match(r"([0-9]+(?:\.[0-9]+)?)fps", unit)
    if match:
        return float(match.group(1))
    return 24.0


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Smart Crowd Analyzer is available inside Maya.") from exc
    return cmds
