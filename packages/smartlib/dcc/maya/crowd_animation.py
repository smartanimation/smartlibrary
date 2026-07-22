from __future__ import annotations

from pathlib import Path
from typing import Any

from smartlib.crowd.schema import load_behavior_schema


ANIMATION_NODE_TYPE = "smartCrowdAnimationProperties"


def create_animation_properties_node(
    name: str = "smartCrowdAnimationProperties",
    *,
    animation_type: str | None = None,
    animation_style: str | None = None,
    interaction: str = "none",
    root_motion_source: str = "",
    schema_path: str | Path | None = None,
) -> str:
    cmds = _maya_cmds()
    schema = load_behavior_schema(schema_path)
    type_ids = schema.option_ids("animation_types")
    style_ids = schema.option_ids("animation_styles")
    selected_type = animation_type or type_ids[0]
    selected_style = animation_style or style_ids[0]
    schema.require_option("animation_types", selected_type)
    schema.require_option("animation_styles", selected_style)
    if interaction not in {"", "none"}:
        schema.require_option("interaction_types", interaction)

    node = cmds.createNode("network", name=name)
    _add_string_attr(cmds, node, "smartCrowdNodeType", ANIMATION_NODE_TYPE, locked=True)
    _add_enum_attr(cmds, node, "animationType", type_ids, selected_type)
    _add_enum_attr(cmds, node, "animationStyle", style_ids, selected_style)
    _add_string_attr(cmds, node, "interaction", interaction or "none")
    _add_double_attr(cmds, node, "duration", 0.0)
    _add_double_attr(cmds, node, "durationFrames", 0.0)
    _add_double_attr(cmds, node, "durationSeconds", 0.0)
    _add_double_attr(cmds, node, "fps", 24.0)
    _add_string_attr(cmds, node, "unit", "")
    _add_double_attr(cmds, node, "startFrame", 0.0)
    _add_double_attr(cmds, node, "endFrame", 0.0)
    _add_string_attr(cmds, node, "rootMotionSource", root_motion_source)
    _add_string_attr(cmds, node, "rootMotion", "{}")
    _add_double_attr(cmds, node, "speed", 0.0)
    _add_double_attr(cmds, node, "speedHoudini", 0.0)
    _add_bool_attr(cmds, node, "loop", False)
    _add_string_attr(cmds, node, "boundingBox", "{}")
    _add_string_attr(cmds, node, "footContact", "[]")
    cmds.select(node, replace=True)
    return node


def build_animation_properties_window(*, schema_path: str | Path | None = None) -> str:
    cmds = _maya_cmds()
    schema = load_behavior_schema(schema_path)
    window = "SmartCrowdAnimationPropertiesWindow"
    if cmds.window(window, exists=True):
        cmds.deleteUI(window)
    cmds.window(window, title="Smart Crowd Animation Properties")
    cmds.columnLayout(adjustableColumn=True)
    cmds.text(label="Animation Type")
    type_menu = cmds.optionMenu("SmartCrowdAnimationTypeMenu")
    for option_id, label in schema.option_labels("animation_types").items():
        cmds.menuItem(label=f"{label} ({option_id})", data=option_id)
    cmds.text(label="Animation Style")
    style_menu = cmds.optionMenu("SmartCrowdAnimationStyleMenu")
    for option_id, label in schema.option_labels("animation_styles").items():
        cmds.menuItem(label=f"{label} ({option_id})", data=option_id)
    cmds.text(label="Interaction")
    interaction_menu = cmds.optionMenu("SmartCrowdAnimationInteractionMenu")
    cmds.menuItem(label="None (none)", data="none")
    for option_id, label in schema.option_labels("interaction_types").items():
        cmds.menuItem(label=f"{label} ({option_id})", data=option_id)
    cmds.text(label="Root Motion Source")
    root_motion_field = cmds.textField("SmartCrowdRootMotionSourceField", text="")
    cmds.button(
        label="Create Node",
        command=lambda *_: _create_from_window(
            cmds,
            type_menu,
            style_menu,
            interaction_menu,
            root_motion_field,
            schema,
        ),
    )
    cmds.showWindow(window)
    return window


def selected_animation_properties_node() -> str:
    cmds = _maya_cmds()
    for node in cmds.ls(selection=True) or []:
        if _is_animation_properties_node(cmds, node):
            return node
    matches = []
    for node in cmds.ls(type="network") or []:
        plug = f"{node}.smartCrowdNodeType"
        if cmds.objExists(plug) and cmds.getAttr(plug) == ANIMATION_NODE_TYPE:
            matches.append(node)
    if not matches:
        raise RuntimeError("No Smart Crowd Animation Properties node was found.")
    return sorted(matches)[0]


def set_root_motion_source(node: str | None = None, source: str | None = None) -> str:
    """Store a shot-specific root motion source on an Animation Properties node."""
    cmds = _maya_cmds()
    if node and not _is_animation_properties_node(cmds, node):
        if source is None and cmds.objExists(node) and cmds.nodeType(node) == "transform":
            source = node
            node = None
        else:
            raise RuntimeError(
                "node must be a Smart Crowd Animation Properties node. "
                "Pass the controller as source='nodeName' instead."
            )
    node = node or selected_animation_properties_node()
    _ensure_string_attr(cmds, node, "rootMotionSource", "")
    source = source or _selected_transform_excluding(cmds, node)
    if not source:
        raise RuntimeError("Select a root motion controller or pass source='nodeName'.")
    if not cmds.objExists(source):
        raise RuntimeError(f"Root motion source does not exist: {source}")
    cmds.setAttr(f"{node}.rootMotionSource", source, type="string")
    return source


def set_root_motion_source_from_selection(node: str | None = None) -> str:
    return set_root_motion_source(node=node)


def mark_root_motion_role(source: str | None = None) -> str:
    """Mark a rig transform with reusable Smart Crowd metadata."""
    cmds = _maya_cmds()
    source = source or _selected_transform_excluding(cmds, "")
    if not source:
        raise RuntimeError("Select a root motion controller or pass source='nodeName'.")
    if not cmds.objExists(source):
        raise RuntimeError(f"Root motion source does not exist: {source}")
    _ensure_string_attr(cmds, source, "smartCrowdRole", "")
    cmds.setAttr(f"{source}.smartCrowdRole", "root_motion", type="string")
    return source


def _create_from_window(
    cmds: Any,
    type_menu: str,
    style_menu: str,
    interaction_menu: str,
    root_motion_field: str,
    schema,
) -> None:
    type_ids = schema.option_ids("animation_types")
    style_ids = schema.option_ids("animation_styles")
    interaction_ids = ["none"] + schema.option_ids("interaction_types")
    animation_type = type_ids[int(cmds.optionMenu(type_menu, query=True, select=True)) - 1]
    animation_style = style_ids[int(cmds.optionMenu(style_menu, query=True, select=True)) - 1]
    interaction = interaction_ids[int(cmds.optionMenu(interaction_menu, query=True, select=True)) - 1]
    root_motion_source = str(cmds.textField(root_motion_field, query=True, text=True) or "")
    create_animation_properties_node(
        animation_type=animation_type,
        animation_style=animation_style,
        interaction=interaction,
        root_motion_source=root_motion_source,
        schema_path=schema.path,
    )


def _add_string_attr(cmds: Any, node: str, attr: str, value: str, *, locked: bool = False) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, dataType="string")
    cmds.setAttr(f"{node}.{attr}", value, type="string", lock=locked)


def _ensure_string_attr(cmds: Any, node: str, attr: str, value: str) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, dataType="string")
        cmds.setAttr(f"{node}.{attr}", value, type="string")


def _is_animation_properties_node(cmds: Any, node: str) -> bool:
    plug = f"{node}.smartCrowdNodeType"
    return bool(cmds.objExists(plug) and cmds.getAttr(plug) == ANIMATION_NODE_TYPE)


def _selected_transform_excluding(cmds: Any, excluded_node: str) -> str:
    excluded = {excluded_node} if excluded_node else set()
    for node in cmds.ls(selection=True) or []:
        if node in excluded:
            continue
        if cmds.objExists(node) and cmds.nodeType(node) == "transform":
            return node
    transforms = cmds.ls(selection=True, type="transform") or []
    for node in transforms:
        if node not in excluded:
            return node
    return ""


def _add_enum_attr(cmds: Any, node: str, attr: str, values: list[str], selected: str) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, attributeType="enum", enumName=":".join(values), keyable=True)
    cmds.setAttr(f"{node}.{attr}", values.index(selected))


def _add_double_attr(cmds: Any, node: str, attr: str, value: float) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, attributeType="double", keyable=True)
    cmds.setAttr(f"{node}.{attr}", float(value))


def _add_bool_attr(cmds: Any, node: str, attr: str, value: bool) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, attributeType="bool", keyable=True)
    cmds.setAttr(f"{node}.{attr}", bool(value))


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Smart Crowd Animation tools are available inside Maya.") from exc
    return cmds
