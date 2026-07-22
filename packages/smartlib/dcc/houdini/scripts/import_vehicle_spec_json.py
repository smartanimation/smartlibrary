"""Import Maya vehicle spec JSON into Houdini as point geometry.

Run inside Houdini:

    from smartlib.dcc.houdini import smart_menu
    smart_menu.import_vehicle_spec_json()

The script writes a .bgeo.sc file and creates a Geometry node with a File SOP.
Connect that File SOP output to input 2 of smart::car_path_locators::1.0.
"""

from __future__ import annotations

import json
import os


LOCATOR_NAMES = ("car_root", "wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR")
WHEEL_NAMES = ("wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR")


def _read_spec(path):
    with open(path, "r") as handle:
        data = json.load(handle)

    if data.get("schema") != "smart_vehicle_spec.v1":
        raise ValueError("Unexpected vehicle spec schema: {}".format(data.get("schema")))

    missing = [name for name in LOCATOR_NAMES if name not in data.get("locators", {})]
    if missing:
        raise ValueError("Missing vehicle spec locators: {}".format(", ".join(missing)))

    return data


def import_vehicle_spec_json():
    import hou

    json_path = hou.ui.selectFile(
        title="Import Vehicle Spec JSON",
        file_type=hou.fileType.Any,
        chooser_mode=hou.fileChooserMode.Read,
    )
    if not json_path:
        return

    json_path = hou.expandString(json_path)
    data = _read_spec(json_path)
    default_scale = "0.01" if data.get("linear_unit") == "cm" else "1.0"
    result, scale_text = hou.ui.readInput(
        "Input scale for vehicle spec positions",
        buttons=("OK", "Cancel"),
        initial_contents=default_scale,
        title="Vehicle Spec Input Scale",
    )
    if result != 0:
        return

    input_scale = float(scale_text)
    target_node = _select_target_car_path_node(hou)

    base = os.path.splitext(os.path.basename(json_path))[0]

    if target_node is not None:
        namespace = _read_namespace(hou, base)
        vehicle_index = _append_vehicle_spec(target_node, data, json_path, base, namespace, input_scale)
        applied = _apply_maya_export_settings(target_node, data)
        target_node.parent().layoutChildren()
    else:
        workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        out_dir = os.path.join(workspace, "generated_vehicle_specs")
        os.makedirs(out_dir, exist_ok=True)
        bgeo_path = os.path.join(out_dir, "{}.bgeo.sc".format(base))
        _write_vehicle_spec_bgeo(hou, data, bgeo_path, input_scale)

        obj = hou.node("/obj")
        node_name = "vehicle_spec_{}".format(base)
        existing = obj.node(node_name)
        if existing is not None:
            existing.destroy()

        geo_node = obj.createNode("geo", node_name)
        for child in geo_node.children():
            child.destroy()

        file_sop = geo_node.createNode("file", "vehicle_spec_points")
        file_sop.parm("file").set(bgeo_path)
        file_sop.setDisplayFlag(True)
        file_sop.setRenderFlag(True)
        geo_node.layoutChildren()
        applied = False
        vehicle_index = None

    print("Imported vehicle spec JSON:")
    print("  {}".format(json_path.replace("\\", "/")))
    print("Input scale:")
    print("  {}".format(input_scale))
    if target_node is not None:
        print("Updated Smart Car Path Locators:")
        print("  {}".format(target_node.path()))
        print("Added to Vehicles list:")
        print("  #{}".format(vehicle_index))
        print("Applied Maya controller settings:")
        print("  {}".format(applied))
    else:
        print("Wrote:")
        print("  {}".format(bgeo_path.replace("\\", "/")))
        print("Connect this SOP to input 2 of smart::car_path_locators::1.0:")
        print("  {}".format(file_sop.path()))
        print("No Smart Car Path Locators target was selected/found; Maya controller settings were not applied.")


def _set_first_existing_parm(node, names, value):
    for name in names:
        parm = node.parm(name)
        if parm is not None:
            parm.set(value)
            return True
    return False


def _read_namespace(hou, default):
    result, namespace = hou.ui.readInput(
        "Maya namespace for this referenced vehicle",
        buttons=("OK", "Skip"),
        initial_contents=default,
        title="Vehicle Spec Namespace",
    )
    if result != 0:
        return ""
    return namespace.strip().strip(":")


def _write_vehicle_spec_bgeo(hou, data, bgeo_path, input_scale):
    geo = hou.Geometry()
    name_attrib = geo.addAttrib(hou.attribType.Point, "name", "")
    pscale_attrib = geo.addAttrib(hou.attribType.Point, "pscale", 0.1)
    geo.addAttrib(hou.attribType.Global, "wheel_radius", 0.0)
    geo.addAttrib(hou.attribType.Global, "wheel_center_height", 0.0)

    for name in LOCATOR_NAMES:
        point = geo.createPoint()
        point.setPosition(tuple(float(v) * input_scale for v in data["locators"][name]["translate"]))
        point.setAttribValue(name_attrib, name)
        point.setAttribValue(pscale_attrib, 0.1)

    attrs = data.get("attributes", {})
    geo.setGlobalAttribValue("wheel_radius", float(attrs.get("wheel_radius", 0.34)) * input_scale)
    geo.setGlobalAttribValue(
        "wheel_center_height",
        float(attrs.get("wheel_center_height", attrs.get("wheel_radius", 0.34))) * input_scale,
    )
    geo.saveToFile(bgeo_path)


def _append_vehicle_spec(target_node, data, json_path, label, namespace, input_scale):
    count_parm = target_node.parm("vehicles")
    if count_parm is None:
        raise RuntimeError("Target HDA does not have a Vehicles multiparm. Recreate the Smart Car Path HDA.")

    current_count = int(count_parm.eval())
    index = current_count + 1
    count_parm.set(index)

    dims = _vehicle_dimensions(data, input_scale)
    _set_first_existing_parm(target_node, ("vehicle{}_label".format(index),), label)
    _set_first_existing_parm(target_node, ("vehicle{}_namespace".format(index),), namespace)
    _set_first_existing_parm(target_node, ("vehicle{}_spec_path".format(index),), json_path)
    for key, value in dims.items():
        _set_first_existing_parm(target_node, ("vehicle{}_{}".format(index, key),), value)

    settings = data.get("maya_export") or {}
    controllers = settings.get("controllers") or {}
    for name in LOCATOR_NAMES:
        controller = controllers.get(name) or {}
        _set_first_existing_parm(
            target_node,
            ("vehicle{}_{}_controller".format(index, name),),
            controller.get("node", ""),
        )
        _set_first_existing_parm(
            target_node,
            ("vehicle{}_{}_roll_attr".format(index, name),),
            controller.get("roll_attr", ""),
        )
        _set_first_existing_parm(
            target_node,
            ("vehicle{}_{}_steer_attr".format(index, name),),
            controller.get("steer_attr", ""),
        )

    return index


def _vehicle_dimensions(data, input_scale):
    locators = data.get("locators") or {}

    def pos(name):
        return tuple(float(v) * input_scale for v in locators[name]["translate"])

    def sub(a, b):
        return tuple(float(a[i]) - float(b[i]) for i in range(3))

    def add(a, b):
        return tuple(float(a[i]) + float(b[i]) for i in range(3))

    def mul(a, scalar):
        return tuple(float(v) * scalar for v in a)

    def dot(a, b):
        return sum(float(a[i]) * float(b[i]) for i in range(3))

    def length(a):
        return sum(float(v) * float(v) for v in a) ** 0.5

    def normalize(a, fallback):
        size = length(a)
        if size <= 1e-8:
            return fallback
        return tuple(float(v) / size for v in a)

    fl = pos("wheel_FL")
    fr = pos("wheel_FR")
    rl = pos("wheel_RL")
    rr = pos("wheel_RR")
    root = pos("car_root")
    rear = mul(add(rl, rr), 0.5)
    front = mul(add(fl, fr), 0.5)
    fwd = normalize(sub(front, rear), (0.0, 0.0, 1.0))
    world_up = (0.0, 1.0, 0.0)
    attrs = data.get("attributes", {})
    wheel_radius = float(attrs.get("wheel_radius", 0.34)) * input_scale

    return {
        "wheelbase": max(length(sub(front, rear)), 1e-3),
        "track_width": max((length(sub(fl, fr)) + length(sub(rl, rr))) * 0.5, 1e-3),
        "wheel_radius": max(wheel_radius, 1e-4),
        "root_from_rear": dot(sub(root, rear), fwd),
        "root_height": dot(sub(root, rear), world_up),
        "wheel_center_height": float(attrs.get("wheel_center_height", attrs.get("wheel_radius", 0.34))) * input_scale,
    }


def _create_file_sop_next_to_target(target_node, bgeo_path):
    parent = target_node.parent()
    file_sop = parent.createNode("file", "vehicle_spec_points", run_init_scripts=True, load_contents=True)
    file_sop.setName("vehicle_spec_points", unique_name=True)
    file_sop.parm("file").set(bgeo_path)
    return file_sop


def _car_path_candidates(hou):
    candidates = []
    obj = hou.node("/obj")
    if obj is None:
        return candidates
    for node in obj.allSubChildren():
        if node.parm("maya_car_root_controller") is not None:
            candidates.append(node)
    return candidates


def _select_target_car_path_node(hou):
    explicit_node = globals().get("SMART_CAR_IMPORT_TARGET_NODE")
    if explicit_node is not None and explicit_node.parm("maya_car_root_controller") is not None:
        return explicit_node

    selected = [node for node in hou.selectedNodes() if node.parm("maya_car_root_controller") is not None]
    if selected:
        return selected[0]

    candidates = _car_path_candidates(hou)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        labels = [node.path() for node in candidates]
        result = hou.ui.selectFromList(
            labels,
            message="Apply vehicle spec and Maya controller settings to:",
            title="Smart CarSystem Target",
            exclusive=True,
        )
        if result:
            return candidates[result[0]]
    return None


def _apply_maya_export_settings(target_node, data):
    settings = data.get("maya_export") or {}
    if not settings:
        return False

    _set_first_existing_parm(target_node, ("maya_create_validation_locators",), int(settings.get("create_validation_locators", True)))
    _set_first_existing_parm(target_node, ("maya_validation_locator_prefix",), settings.get("validation_locator_prefix", "hda_"))
    _set_first_existing_parm(target_node, ("maya_translate_scale",), float(settings.get("translate_scale", 100.0)))
    _set_first_existing_parm(target_node, ("maya_wheel_roll_multiplier",), float(settings.get("wheel_roll_multiplier", 0.00277778)))
    _set_first_existing_parm(target_node, ("maya_parent_validation_locators",), int(settings.get("parent_validation_locators", True)))
    _set_first_existing_parm(target_node, ("maya_key_controllers",), int(settings.get("key_controllers", True)))

    controllers = settings.get("controllers") or {}
    for name in LOCATOR_NAMES:
        controller = controllers.get(name) or {}
        _set_first_existing_parm(target_node, ("maya_{}_controller".format(name),), controller.get("node", ""))
    for name in WHEEL_NAMES:
        controller = controllers.get(name) or {}
        _set_maya_axis_direction_attr(target_node, name, "roll", controller.get("roll_attr", ""))
    for name in ("wheel_FL", "wheel_FR"):
        controller = controllers.get(name) or {}
        _set_maya_axis_direction_attr(target_node, name, "steer", controller.get("steer_attr", ""))
    return True


def _parse_maya_attr(attr):
    text = str(attr or "").strip()
    direction = "pos"
    while text.startswith(("+", "-")):
        if text[0] == "-":
            direction = "neg" if direction == "pos" else "pos"
        text = text[1:].strip()

    lowered = text.lower()
    if "rotatex" in lowered:
        return "x", direction
    if "rotatey" in lowered:
        return "y", direction
    if "rotatez" in lowered:
        return "z", direction
    return "off", "pos"


def _compose_maya_attr(axis, direction):
    if axis == "off":
        return ""
    attr = "rotate{}".format(str(axis).upper())
    if direction == "neg":
        attr = "-{}".format(attr)
    return attr


def _set_maya_axis_direction_attr(target_node, wheel, kind, attr):
    axis, direction = _parse_maya_attr(attr)
    _set_first_existing_parm(target_node, ("maya_{}_{}_axis".format(wheel, kind),), axis)
    _set_first_existing_parm(target_node, ("maya_{}_{}_direction".format(wheel, kind),), direction)
    _set_first_existing_parm(target_node, ("maya_{}_{}_attr".format(wheel, kind),), _compose_maya_attr(axis, direction))


import_vehicle_spec_json()
