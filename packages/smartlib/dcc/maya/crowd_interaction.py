from __future__ import annotations

from pathlib import Path
from typing import Any

from smartlib.crowd.schema import load_behavior_schema


GIZMO_NODE_TYPE = "smartCrowdInteractionGizmo"
ROLE_ATTR = "smartCrowdRole"


def create_seat_gizmo(
    name: str = "Bench_A",
    *,
    interaction_type: str | None = None,
    schema_path: str | Path | None = None,
) -> str:
    """Create a curve-based Interaction Gizmo with Seat, Approach and Forward controls."""

    cmds = _maya_cmds()
    schema = load_behavior_schema(schema_path)
    interaction_ids = schema.option_ids("interaction_types")
    selected_type = interaction_type or interaction_ids[0]
    schema.require_option("interaction_types", selected_type)

    group = cmds.group(empty=True, name=name)
    _add_string_attr(cmds, group, "smartCrowdNodeType", GIZMO_NODE_TYPE, locked=True)
    _add_string_attr(cmds, group, "interactionId", name)
    _add_enum_attr(cmds, group, "interactionType", interaction_ids, selected_type)
    _add_bool_attr(cmds, group, "enabled", True)
    _add_long_attr(cmds, group, "priority", 0)
    _add_bool_attr(cmds, group, "occupied", False)

    seat = _create_curve(cmds, f"{name}_Seat_Point", _seat_shape_points(), group, "seat_point", color=17)
    approach = _create_curve(cmds, f"{name}_Approach_Point", _approach_shape_points(), group, "approach_point", color=14)
    forward = _create_curve(cmds, f"{name}_Forward", _forward_shape_points(), group, "forward", color=6)
    cmds.xform(approach, objectSpace=True, translation=(0.0, 0.0, -1.0))
    cmds.xform(forward, objectSpace=True, translation=(0.0, 0.0, 0.0))
    cmds.select(group, replace=True)
    return group


def list_interaction_gizmos() -> list[str]:
    cmds = _maya_cmds()
    result = []
    for node in cmds.ls(type="transform") or []:
        plug = f"{node}.smartCrowdNodeType"
        if cmds.objExists(plug) and cmds.getAttr(plug) == GIZMO_NODE_TYPE:
            result.append(node)
    return sorted(result)


def set_interaction_type(node: str, interaction_type: str, *, schema_path: str | Path | None = None) -> None:
    cmds = _maya_cmds()
    schema = load_behavior_schema(schema_path)
    schema.require_option("interaction_types", interaction_type)
    ids = schema.option_ids("interaction_types")
    if not cmds.objExists(f"{node}.interactionType"):
        _add_enum_attr(cmds, node, "interactionType", ids, interaction_type)
        return
    cmds.setAttr(f"{node}.interactionType", ids.index(interaction_type))


def build_interaction_properties_window(*, schema_path: str | Path | None = None) -> str:
    cmds = _maya_cmds()
    schema = load_behavior_schema(schema_path)
    window = "SmartCrowdInteractionProperties"
    if cmds.window(window, exists=True):
        cmds.deleteUI(window)
    cmds.window(window, title="Smart Crowd Interaction Properties")
    cmds.columnLayout(adjustableColumn=True)
    cmds.text(label="Interaction Type")
    menu = cmds.optionMenu("SmartCrowdInteractionTypeMenu")
    for option_id, label in schema.option_labels("interaction_types").items():
        cmds.menuItem(label=f"{label} ({option_id})", data=option_id)
    cmds.button(label="Apply To Selected", command=lambda *_: _apply_selected_interaction_type(cmds, menu, schema))
    cmds.showWindow(window)
    return window


def export_gizmo_data(node: str) -> dict[str, Any]:
    cmds = _maya_cmds()
    interaction_ids = load_behavior_schema().option_ids("interaction_types")
    type_index = int(cmds.getAttr(f"{node}.interactionType"))
    interaction_type = interaction_ids[type_index]
    children = _role_children(cmds, node)
    position, rotation = _world_transform(cmds, children.get("seat_point", node))
    approach_position, _approach_rotation = _world_transform(cmds, children.get("approach_point", node))
    return {
        "id": _string_attr(cmds, node, "interactionId", node),
        "type": node.split("_")[0].lower(),
        "points": [
            {
                "id": f"{node}_seat_01",
                "interaction_type": interaction_type,
                "position": _vector_mapping(position),
                "rotation": _vector_mapping(rotation),
                "approach_position": _vector_mapping(approach_position),
                "enabled": bool(cmds.getAttr(f"{node}.enabled")),
                "priority": int(cmds.getAttr(f"{node}.priority")),
                "occupied": bool(cmds.getAttr(f"{node}.occupied")),
            }
        ],
    }


def _apply_selected_interaction_type(cmds: Any, menu: str, schema) -> None:
    selected = cmds.optionMenu(menu, query=True, select=True)
    option_ids = schema.option_ids("interaction_types")
    if selected < 1 or selected > len(option_ids):
        return
    for node in cmds.ls(selection=True, type="transform") or []:
        if cmds.objExists(f"{node}.smartCrowdNodeType"):
            set_interaction_type(node, option_ids[selected - 1], schema_path=schema.path)


def _create_curve(cmds: Any, name: str, points: list[tuple[float, float, float]], parent: str, role: str, color: int) -> str:
    curve = cmds.curve(name=name, degree=1, point=points)
    curve = cmds.parent(curve, parent)[0]
    _add_string_attr(cmds, curve, ROLE_ATTR, role, locked=True)
    shapes = cmds.listRelatives(curve, shapes=True, fullPath=True) or []
    for shape in shapes:
        cmds.setAttr(f"{shape}.overrideEnabled", 1)
        cmds.setAttr(f"{shape}.overrideColor", color)
    return curve


def _seat_shape_points() -> list[tuple[float, float, float]]:
    return [
        (-0.35, 0.0, -0.35),
        (0.35, 0.0, -0.35),
        (0.35, 0.0, 0.35),
        (-0.35, 0.0, 0.35),
        (-0.35, 0.0, -0.35),
        (0.0, 0.0, -0.5),
        (0.0, 0.0, 0.5),
    ]


def _approach_shape_points() -> list[tuple[float, float, float]]:
    return [(-0.25, 0.0, 0.0), (0.25, 0.0, 0.0), (0.0, 0.0, -0.25), (0.0, 0.0, 0.25)]


def _forward_shape_points() -> list[tuple[float, float, float]]:
    return [(0.0, 0.02, 0.0), (0.0, 0.02, 1.0), (-0.18, 0.02, 0.78), (0.0, 0.02, 1.0), (0.18, 0.02, 0.78)]


def _add_string_attr(cmds: Any, node: str, attr: str, value: str, *, locked: bool = False) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, dataType="string")
    cmds.setAttr(f"{node}.{attr}", value, type="string", lock=locked)


def _add_enum_attr(cmds: Any, node: str, attr: str, values: list[str], selected: str) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, attributeType="enum", enumName=":".join(values), keyable=True)
    cmds.setAttr(f"{node}.{attr}", values.index(selected))


def _add_bool_attr(cmds: Any, node: str, attr: str, value: bool) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, attributeType="bool", keyable=True)
    cmds.setAttr(f"{node}.{attr}", bool(value))


def _add_long_attr(cmds: Any, node: str, attr: str, value: int) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, attributeType="long", keyable=True)
    cmds.setAttr(f"{node}.{attr}", int(value))


def _role_children(cmds: Any, node: str) -> dict[str, str]:
    result = {}
    for child in cmds.listRelatives(node, children=True, type="transform", fullPath=False) or []:
        plug = f"{child}.{ROLE_ATTR}"
        if cmds.objExists(plug):
            result[str(cmds.getAttr(plug))] = child
    return result


def _world_transform(cmds: Any, node: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    position = tuple(float(v) for v in cmds.xform(node, query=True, worldSpace=True, translation=True))
    rotation = tuple(float(v) for v in cmds.xform(node, query=True, worldSpace=True, rotation=True))
    return position, rotation


def _vector_mapping(value: tuple[float, float, float]) -> dict[str, float]:
    return {"x": value[0], "y": value[1], "z": value[2]}


def _string_attr(cmds: Any, node: str, attr: str, default: str) -> str:
    plug = f"{node}.{attr}"
    return str(cmds.getAttr(plug)) if cmds.objExists(plug) else default


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Smart Crowd Interaction tools are available inside Maya.") from exc
    return cmds
