from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from smartlib.crowd.animation import AnimationProperties
from smartlib.crowd.schema import load_behavior_schema
from smartlib.crowd.yamlio import write_yaml
from smartlib.dcc.maya.crowd_animation import selected_animation_properties_node
from smartlib.dcc.maya.crowd_interaction import export_gizmo_data, list_interaction_gizmos


def export_interaction_yaml(path: str | Path, *, schema_path: str | Path | None = None) -> dict[str, Any]:
    schema = load_behavior_schema(schema_path)
    data = {"interactions": [export_gizmo_data(node) for node in list_interaction_gizmos()]}
    for interaction in data["interactions"]:
        for point in interaction.get("points") or []:
            schema.require_option("interaction_types", point.get("interaction_type", ""))
    write_yaml(path, data)
    return data


def export_animation_yaml(
    path: str | Path,
    *,
    node: str | None = None,
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    cmds = _maya_cmds()
    schema = load_behavior_schema(schema_path)
    node = node or selected_animation_properties_node()
    animation_type = _enum_value(cmds, node, "animationType", schema.option_ids("animation_types"))
    animation_style = _enum_value(cmds, node, "animationStyle", schema.option_ids("animation_styles"))
    root_motion = _json_attr(cmds, node, "rootMotion", {})
    duration = float(cmds.getAttr(f"{node}.duration"))
    props = AnimationProperties(
        animation_type=animation_type,
        animation_style=animation_style,
        interaction=str(cmds.getAttr(f"{node}.interaction") or "none"),
        duration=duration,
        duration_frames=_float_attr(cmds, node, "durationFrames", float(root_motion.get("duration_frames", duration))),
        duration_seconds=_float_attr(cmds, node, "durationSeconds", float(root_motion.get("duration_seconds", 0.0))),
        fps=_float_attr(cmds, node, "fps", float(root_motion.get("fps", 24.0))),
        unit=_string_attr(cmds, node, "unit", str(root_motion.get("unit", ""))),
        start_frame=float(cmds.getAttr(f"{node}.startFrame")),
        end_frame=float(cmds.getAttr(f"{node}.endFrame")),
        root_motion=root_motion,
        speed=float(cmds.getAttr(f"{node}.speed")),
        speed_houdini=_float_attr(cmds, node, "speedHoudini", float(root_motion.get("speed_houdini", 0.0))),
        loop=bool(cmds.getAttr(f"{node}.loop")),
        bounding_box=_json_attr(cmds, node, "boundingBox", {}),
        foot_contact=_json_attr(cmds, node, "footContact", []),
    )
    data = props.to_yaml_data(schema)
    write_yaml(path, data)
    return data


def _enum_value(cmds: Any, node: str, attr: str, values: list[str]) -> str:
    index = int(cmds.getAttr(f"{node}.{attr}"))
    if index < 0 or index >= len(values):
        raise ValueError(f"Invalid enum index on {node}.{attr}: {index}")
    return values[index]


def _json_attr(cmds: Any, node: str, attr: str, default: Any) -> Any:
    try:
        return json.loads(cmds.getAttr(f"{node}.{attr}") or "")
    except (TypeError, ValueError):
        return default


def _float_attr(cmds: Any, node: str, attr: str, default: float) -> float:
    plug = f"{node}.{attr}"
    if not cmds.objExists(plug):
        return float(default)
    try:
        return float(cmds.getAttr(plug))
    except (TypeError, ValueError):
        return float(default)


def _string_attr(cmds: Any, node: str, attr: str, default: str) -> str:
    plug = f"{node}.{attr}"
    if not cmds.objExists(plug):
        return default
    value = cmds.getAttr(plug)
    return str(value) if value is not None else default


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Smart Crowd Exporter is available inside Maya.") from exc
    return cmds
