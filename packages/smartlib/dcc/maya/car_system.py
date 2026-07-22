from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOCATOR_NAMES = ("car_root", "wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR")
WHEEL_NAMES = ("wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR")
DEFAULT_CONTROLLER_NODES = {
    "car_root": "CTLALT_allWorld",
    "wheel_FL": "A_L_frontWheel",
    "wheel_FR": "A_R_frontWheel",
    "wheel_RL": "A_L_rearWheel",
    "wheel_RR": "A_R_rearWheel",
}
DEFAULT_CONTROLLER_ROLL_ATTRS = {
    "wheel_FL": "",
    "wheel_FR": "",
    "wheel_RL": "",
    "wheel_RR": "",
}
DEFAULT_CONTROLLER_STEER_ATTRS = {
    "wheel_FL": "",
    "wheel_FR": "",
}


@dataclass(frozen=True)
class ImportOptions:
    use_json_settings: bool = True
    create_validation_locators: bool | None = None
    validation_locator_prefix: str | None = None
    translate_scale: float | None = None
    parent_validation_locators: bool | None = None
    key_controllers: bool | None = None


def import_car_locator_anim_json(path: str | Path, options: ImportOptions | None = None) -> dict[str, Any]:
    cmds, om = _maya_modules()
    path = str(path)
    options = options or ImportOptions()

    with open(path, "r") as handle:
        data = json.load(handle)

    if data.get("schema") not in ("smart_car_locator_anim.v1", "smart_car_locator_anim.v2", "smart_car_locator_anim.v3"):
        cmds.warning("Unexpected schema: {}".format(data.get("schema")))

    maya_export = dict(data.get("maya_export") or {})
    if not options.use_json_settings:
        maya_export = {}

    create_validation_locators = _option_value(
        options.create_validation_locators,
        maya_export.get("create_validation_locators"),
        True,
    )
    validation_prefix = _option_value(
        options.validation_locator_prefix,
        maya_export.get("validation_locator_prefix"),
        "hda_",
    )
    translate_scale = float(_option_value(options.translate_scale, maya_export.get("translate_scale"), 1.0))
    parent_validation_locators = bool(
        _option_value(options.parent_validation_locators, maya_export.get("parent_validation_locators"), True)
    )
    key_controllers = bool(_option_value(options.key_controllers, maya_export.get("key_controllers"), False))
    wheel_roll_multiplier = float(maya_export.get("wheel_roll_multiplier", 0.00277778))
    normalize_roll_to_first_frame = bool(maya_export.get("normalize_roll_to_first_frame", True))
    roll_offsets = _first_frame_roll_offsets(data) if normalize_roll_to_first_frame and not data.get("roll_offsets") else {}

    uses_vehicle_frames = bool(data.get("vehicles")) or any("vehicles" in frame for frame in data.get("frames", []))
    locators = {}
    if create_validation_locators and not uses_vehicle_frames:
        for name in LOCATOR_NAMES:
            loc_name = "{}{}".format(validation_prefix, name)
            loc = _ensure_locator(cmds, loc_name)
            _ensure_double_attr(cmds, loc, "roll")
            _ensure_double_attr(cmds, loc, "roll_degrees")
            _ensure_double_attr(cmds, loc, "steer")
            _ensure_double_attr(cmds, loc, "steer_degrees")
            locators[name] = loc
        if parent_validation_locators:
            _parent_validation_locators(cmds, locators)

    keyed_controller_count = 0
    keyed_roll_attr_count = 0
    keyed_steer_attr_count = 0
    frame_count = 0
    original_time = cmds.currentTime(query=True)
    try:
        for frame_data in data.get("frames", []):
            frame_count += 1
            frame = frame_data["frame"]
            if key_controllers:
                cmds.currentTime(frame, edit=True)
            for vehicle in _frame_vehicle_entries(frame_data):
                vehicle_id = str(vehicle.get("id") or "vehicle_001")
                namespace = str(vehicle.get("namespace") or "").strip().strip(":")
                vehicle_export = _merged_vehicle_export(maya_export, vehicle)
                vehicle_locators = vehicle.get("locators") or {}
                root_values = vehicle_locators.get("car_root", {})
                root_translate = _scale_translate(root_values.get("translate", (0.0, 0.0, 0.0)), translate_scale)
                root_rotate = _quat_xyzw_to_euler_degrees(om, root_values.get("orient", (0.0, 0.0, 0.0, 1.0)))
                root_matrix = _compose_matrix(om, root_translate, root_rotate)
                root_inverse = root_matrix.inverse()

                validation_locators = {}
                if create_validation_locators:
                    for locator_name in LOCATOR_NAMES:
                        if locator_name not in vehicle_locators:
                            continue
                        loc_name = _validation_locator_name(validation_prefix, vehicle_id, locator_name, uses_vehicle_frames)
                        loc = _ensure_locator(cmds, loc_name)
                        _ensure_double_attr(cmds, loc, "roll")
                        _ensure_double_attr(cmds, loc, "roll_degrees")
                        _ensure_double_attr(cmds, loc, "steer")
                        _ensure_double_attr(cmds, loc, "steer_degrees")
                        validation_locators[locator_name] = loc
                    if parent_validation_locators and validation_locators:
                        _parent_validation_locators(cmds, validation_locators)

                for name, values in vehicle_locators.items():
                    if name not in LOCATOR_NAMES:
                        continue

                    tx, ty, tz = values["translate"]
                    rx, ry, rz = _quat_xyzw_to_euler_degrees(om, values["orient"])
                    translate = _scale_translate((tx, ty, tz), translate_scale)
                    rotate = (rx, ry, rz)

                    if create_validation_locators and name in validation_locators:
                        loc = validation_locators[name]
                        key_translate = translate
                        key_rotate = rotate
                        if parent_validation_locators and name != "car_root":
                            local_matrix = _compose_matrix(om, translate, rotate) * root_inverse
                            key_translate, key_rotate = _matrix_to_translate_rotate(om, local_matrix)

                        _set_transform_keys(cmds, loc, frame, key_translate, key_rotate)
                        cmds.setKeyframe(loc, attribute="roll", time=frame, value=values.get("roll", 0.0))
                        cmds.setKeyframe(loc, attribute="roll_degrees", time=frame, value=values.get("roll_degrees", 0.0))
                        cmds.setKeyframe(loc, attribute="steer", time=frame, value=values.get("steer", 0.0))
                        cmds.setKeyframe(loc, attribute="steer_degrees", time=frame, value=values.get("steer_degrees", 0.0))

                    if key_controllers:
                        controller_node, controller = _controller_node(cmds, vehicle_export, name, namespace=namespace)
                        if controller_node is None:
                            continue

                        if _controller_should_key_transform(name, controller):
                            _set_world_transform_keys(cmds, controller_node, frame, translate, rotate)

                        if _set_keyed_attr(
                            cmds,
                            controller_node,
                            controller.get("roll_attr", ""),
                            frame,
                            (float(values.get("roll_degrees", 0.0)) - float(roll_offsets.get(name, 0.0))) * wheel_roll_multiplier,
                        ):
                            keyed_roll_attr_count += 1
                        if _set_keyed_attr(
                            cmds,
                            controller_node,
                            controller.get("steer_attr", ""),
                            frame,
                            values.get("steer_degrees", 0.0),
                        ):
                            keyed_steer_attr_count += 1
                        keyed_controller_count += 1
    finally:
        cmds.currentTime(original_time, edit=True)

    start = data.get("start_frame")
    end = data.get("end_frame")
    if start is not None and end is not None:
        cmds.playbackOptions(minTime=start, maxTime=end, animationStartTime=start, animationEndTime=end)

    return {
        "path": path,
        "frame_count": frame_count,
        "start_frame": start,
        "end_frame": end,
        "translate_scale": translate_scale,
        "created_validation_locators": bool(create_validation_locators),
        "parent_validation_locators": bool(parent_validation_locators),
        "key_controllers": bool(key_controllers),
        "keyed_controller_count": keyed_controller_count,
        "keyed_roll_attr_count": keyed_roll_attr_count,
        "keyed_steer_attr_count": keyed_steer_attr_count,
        "normalized_roll_to_first_frame": normalize_roll_to_first_frame,
        "wheel_roll_multiplier": wheel_roll_multiplier,
    }


def export_vehicle_spec_json(
    path: str | Path,
    controller_nodes: dict[str, str] | None = None,
    controller_roll_attrs: dict[str, str] | None = None,
    controller_steer_attrs: dict[str, str] | None = None,
    *,
    key_controllers: bool = True,
    translate_scale: float = 100.0,
    wheel_roll_multiplier: float = 0.00277778,
) -> dict[str, Any]:
    cmds, _om = _maya_modules()
    path = str(path)
    if not path.lower().endswith(".json"):
        path += ".json"

    controller_nodes = _normalized_controller_nodes(controller_nodes)
    controller_roll_attrs = _normalized_controller_attrs(controller_roll_attrs, DEFAULT_CONTROLLER_ROLL_ATTRS)
    controller_steer_attrs = _normalized_controller_attrs(controller_steer_attrs, DEFAULT_CONTROLLER_STEER_ATTRS)
    selected = cmds.ls(selection=True, long=True) or []
    locators = {}
    missing = []

    for name in LOCATOR_NAMES:
        controller_node = controller_nodes.get(name, "")
        node = _resolve_controller_or_locator(cmds, name, controller_node, selected)
        if node is None:
            missing.append("{} ({})".format(name, controller_node or "locator"))
            continue
        locators[name] = {
            "maya_node": node,
            "translate": _world_position(cmds, node),
        }

    if missing:
        raise RuntimeError("Missing vehicle spec locators: {}".format(", ".join(missing)))

    wheel_source = _resolve_locator(cmds, "wheel_FL", selected) or "wheel_FL"
    if controller_nodes.get("wheel_FL") and cmds.objExists(controller_nodes["wheel_FL"]):
        wheel_source = controller_nodes["wheel_FL"]
    wheel_radius = _attr_or_default(cmds, wheel_source, "wheel_radius", 0.34)
    wheel_center_height = _attr_or_default(cmds, wheel_source, "wheel_center_height", wheel_radius)

    data = {
        "schema": "smart_vehicle_spec.v1",
        "source": "maya",
        "linear_unit": cmds.currentUnit(query=True, linear=True),
        "locators": locators,
        "attributes": {
            "wheel_radius": wheel_radius,
            "wheel_center_height": wheel_center_height,
        },
        "maya_export": _vehicle_spec_maya_export_settings(
            controller_nodes,
            controller_roll_attrs,
            controller_steer_attrs,
            key_controllers=key_controllers,
            translate_scale=translate_scale,
            wheel_roll_multiplier=wheel_roll_multiplier,
        ),
    }

    with open(path, "w") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)

    return {
        "path": path,
        "locator_count": len(locators),
        "linear_unit": data["linear_unit"],
        "key_controllers": key_controllers,
    }


def json_maya_export_settings(path: str | Path) -> dict[str, Any]:
    with open(path, "r") as handle:
        data = json.load(handle)
    return dict(data.get("maya_export") or {})


def default_controller_nodes() -> dict[str, str]:
    return dict(DEFAULT_CONTROLLER_NODES)


def default_controller_roll_attrs() -> dict[str, str]:
    return dict(DEFAULT_CONTROLLER_ROLL_ATTRS)


def default_controller_steer_attrs() -> dict[str, str]:
    return dict(DEFAULT_CONTROLLER_STEER_ATTRS)


def _maya_modules():
    try:
        import maya.OpenMaya as om
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Smart CarSystem is available inside Maya.") from exc
    return cmds, om


def _option_value(override, json_value, default):
    if override is not None:
        return override
    if json_value is not None:
        return json_value
    return default


def _normalized_controller_nodes(controller_nodes: dict[str, str] | None) -> dict[str, str]:
    values = dict(DEFAULT_CONTROLLER_NODES)
    if controller_nodes:
        for name in LOCATOR_NAMES:
            values[name] = str(controller_nodes.get(name, values.get(name, "")) or "").strip()
    return values


def _normalized_controller_attrs(controller_attrs: dict[str, str] | None, defaults: dict[str, str]) -> dict[str, str]:
    values = dict(defaults)
    if controller_attrs:
        for name in values:
            values[name] = str(controller_attrs.get(name, values.get(name, "")) or "").strip()
    return values


def _vehicle_spec_maya_export_settings(
    controller_nodes: dict[str, str],
    controller_roll_attrs: dict[str, str],
    controller_steer_attrs: dict[str, str],
    *,
    key_controllers: bool,
    translate_scale: float,
    wheel_roll_multiplier: float,
) -> dict[str, Any]:
    settings = {
        "create_validation_locators": True,
        "validation_locator_prefix": "hda_",
        "translate_scale": float(translate_scale),
        "wheel_roll_multiplier": float(wheel_roll_multiplier),
        "parent_validation_locators": True,
        "key_controllers": bool(key_controllers),
        "normalize_roll_to_first_frame": True,
        "controllers": {},
    }
    for name in LOCATOR_NAMES:
        roll_attr = controller_roll_attrs.get(name, "")
        steer_attr = controller_steer_attrs.get(name, "")
        settings["controllers"][name] = {
            "node": controller_nodes.get(name, ""),
            "key_transform": name not in WHEEL_NAMES,
            "roll_attr": roll_attr,
            "steer_attr": steer_attr,
        }
    return settings


def _first_frame_roll_offsets(data: dict[str, Any]) -> dict[str, float]:
    frames = data.get("frames") or []
    if not frames:
        return {}
    locators = frames[0].get("locators") or {}
    offsets = {}
    for name in WHEEL_NAMES:
        offsets[name] = float((locators.get(name) or {}).get("roll_degrees", 0.0))
    return offsets


def _frame_vehicle_entries(frame_data: dict[str, Any]) -> list[dict[str, Any]]:
    vehicles = frame_data.get("vehicles")
    if vehicles:
        return [dict(vehicle) for vehicle in vehicles]
    return [
        {
            "id": "vehicle_001",
            "namespace": "",
            "maya_export": {},
            "locators": frame_data.get("locators") or {},
        }
    ]


def _merged_vehicle_export(base_settings: dict[str, Any], vehicle: dict[str, Any]) -> dict[str, Any]:
    settings = dict(base_settings or {})
    base_controllers = dict((base_settings or {}).get("controllers") or {})
    vehicle_export = dict(vehicle.get("maya_export") or {})
    vehicle_controllers = dict(vehicle_export.get("controllers") or {})
    settings.update({key: value for key, value in vehicle_export.items() if key != "controllers"})
    if vehicle_controllers:
        merged_controllers = {}
        for name in LOCATOR_NAMES:
            controller = dict(base_controllers.get(name) or {})
            controller.update(vehicle_controllers.get(name) or {})
            merged_controllers[name] = controller
        settings["controllers"] = merged_controllers
    return settings


def _validation_locator_name(prefix: str, vehicle_id: str, locator_name: str, multi_vehicle: bool) -> str:
    if multi_vehicle:
        return "{}{}_{}".format(prefix, vehicle_id, locator_name)
    return "{}{}".format(prefix, locator_name)


def _ensure_locator(cmds, name: str) -> str:
    if cmds.objExists(name):
        return name
    return cmds.spaceLocator(name=name)[0]


def _ensure_double_attr(cmds, node: str, attr: str) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, attributeType="double", keyable=True)


def _quat_xyzw_to_euler_degrees(om, quat) -> tuple[float, float, float]:
    qx, qy, qz, qw = quat
    maya_quat = om.MQuaternion(qx, qy, qz, qw)
    euler = maya_quat.asEulerRotation()
    return (math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z))


def _set_transform_keys(cmds, node: str, frame, translate, rotate) -> None:
    tx, ty, tz = translate
    rx, ry, rz = rotate
    cmds.setKeyframe(node, attribute="translateX", time=frame, value=tx)
    cmds.setKeyframe(node, attribute="translateY", time=frame, value=ty)
    cmds.setKeyframe(node, attribute="translateZ", time=frame, value=tz)
    cmds.setKeyframe(node, attribute="rotateX", time=frame, value=rx)
    cmds.setKeyframe(node, attribute="rotateY", time=frame, value=ry)
    cmds.setKeyframe(node, attribute="rotateZ", time=frame, value=rz)


def _set_world_transform_keys(cmds, node: str, frame, translate, rotate) -> None:
    cmds.xform(node, worldSpace=True, translation=translate)
    cmds.xform(node, worldSpace=True, rotation=rotate)
    for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
        if _is_keyable_unlocked(cmds, node, attr):
            cmds.setKeyframe(node, attribute=attr, time=frame)


def _is_keyable_unlocked(cmds, node: str, attr: str) -> bool:
    plug = "{}.{}".format(node, attr)
    if not cmds.objExists(plug):
        return False
    try:
        if cmds.getAttr(plug, lock=True):
            return False
        return bool(cmds.getAttr(plug, keyable=True) or cmds.getAttr(plug, channelBox=True))
    except Exception:
        return False


def _scale_translate(translate, scale: float) -> tuple[float, float, float]:
    return tuple(float(v) * scale for v in translate)


def _compose_matrix(om, translate, rotate_degrees):
    matrix = om.MTransformationMatrix()
    matrix.setTranslation(om.MVector(*translate), om.MSpace.kWorld)
    rotate_radians = [float(math.radians(v)) for v in rotate_degrees]
    rotation_util = om.MScriptUtil()
    rotation_util.createFromList(rotate_radians, 3)
    matrix.setRotation(rotation_util.asDoublePtr(), om.MTransformationMatrix.kXYZ)
    return matrix.asMatrix()


def _matrix_to_translate_rotate(om, matrix) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    transform = om.MTransformationMatrix(matrix)
    translation = transform.translation(om.MSpace.kWorld)
    rotation = transform.eulerRotation()
    return (
        (translation.x, translation.y, translation.z),
        (math.degrees(rotation.x), math.degrees(rotation.y), math.degrees(rotation.z)),
    )


def _parent_validation_locators(cmds, locators: dict[str, str]) -> None:
    root = locators.get("car_root")
    if not root:
        return
    for name, loc in locators.items():
        if name == "car_root":
            continue
        current_parent = cmds.listRelatives(loc, parent=True, fullPath=False) or []
        if current_parent and current_parent[0] == root:
            continue
        if current_parent:
            loc = cmds.parent(loc, world=True)[0]
            locators[name] = loc
        locators[name] = cmds.parent(loc, root)[0]


def _set_keyed_attr(cmds, node: str, attr: str, frame, value) -> bool:
    if not attr:
        return False
    attr, multiplier = _parse_keyed_attr(attr)
    if not attr:
        return False
    plug = attr if "." in attr else "{}.{}".format(node, attr)
    if not cmds.objExists(plug):
        cmds.warning("Missing target attribute: {}".format(plug))
        return False
    if cmds.getAttr(plug, lock=True):
        cmds.warning("Locked target attribute: {}".format(plug))
        return False
    keyed_value = float(value) * multiplier
    cmds.setAttr(plug, keyed_value)
    cmds.setKeyframe(plug, time=frame)
    return True


def _parse_keyed_attr(attr: str) -> tuple[str, float]:
    text = str(attr or "").strip()
    multiplier = 1.0
    while text.startswith(("+", "-")):
        if text[0] == "-":
            multiplier *= -1.0
        text = text[1:].strip()
    return text, multiplier


def _controller_node(cmds, settings: dict[str, Any], locator_name: str, namespace: str = ""):
    controller = settings.get("controllers", {}).get(locator_name, {})
    node = _resolve_maya_node(cmds, controller.get("node", ""), namespace=namespace)
    if not node:
        return None, controller
    return node, controller


def _controller_should_key_transform(locator_name: str, controller: dict[str, Any]) -> bool:
    if locator_name in WHEEL_NAMES and (controller.get("roll_attr") or controller.get("steer_attr")):
        return False
    return bool(controller.get("key_transform", True))


def _world_position(cmds, node: str) -> tuple[float, float, float]:
    return tuple(float(v) for v in cmds.xform(node, query=True, worldSpace=True, translation=True))


def _attr_or_default(cmds, node: str, attr: str, default: float) -> float:
    if cmds.objExists("{}.{}".format(node, attr)):
        return float(cmds.getAttr("{}.{}".format(node, attr)))
    return float(default)


def _resolve_locator(cmds, name: str, selected: list[str]) -> str | None:
    if cmds.objExists(name):
        return name
    matches = [node for node in selected if node.split("|")[-1].split(":")[-1] == name]
    if matches:
        return matches[0]
    contains = [node for node in selected if name.lower() in node.split("|")[-1].lower()]
    if contains:
        return contains[0]
    return None


def _resolve_controller_or_locator(cmds, locator_name: str, controller_node: str, selected: list[str]) -> str | None:
    resolved = _resolve_maya_node(cmds, controller_node)
    if resolved:
        return resolved
    if controller_node:
        matches = [node for node in selected if node.split("|")[-1].split(":")[-1] == controller_node]
        if matches:
            return matches[0]
    return _resolve_locator(cmds, locator_name, selected)


def _resolve_maya_node(cmds, node: str, namespace: str = "") -> str:
    node = str(node or "").strip()
    namespace = str(namespace or "").strip().strip(":")
    if not node:
        return ""
    if namespace and ":" not in node.split("|")[-1]:
        for candidate in (
            "{}:{}".format(namespace, node),
            "{}:*:{}".format(namespace, node),
            "{}:*{}".format(namespace, node),
        ):
            matches = cmds.ls(candidate, long=True) or []
            if matches:
                return matches[0]
    if cmds.objExists(node):
        return node
    leaf = node.split("|")[-1].split(":")[-1]
    matches = cmds.ls(leaf, long=True) or []
    if not matches:
        matches = cmds.ls("*:{}".format(leaf), long=True) or []
    if matches:
        return matches[0]
    return ""
