"""Export Smart Car Path Locators animation to JSON.

Usage in Houdini Python Source Editor:

    from smartlib.dcc.houdini import smart_menu
    smart_menu.export_car_locator_anim_json()

Select the Smart Car Path Locators SOP node before running.
"""

from __future__ import annotations

import json
import os


LOCATOR_NAMES = ("car_root", "wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR")
WHEEL_NAMES = ("wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR")


def _node_parm(node, name, default=None):
    parm = node.parm(name)
    if parm is None:
        return default
    return parm.eval()


def _node_parm_bool(node, name, default=False):
    return bool(_node_parm(node, name, int(default)))


def _node_parm_string(node, name, default=""):
    value = _node_parm(node, name, default)
    if value is None:
        return default
    return str(value)


def _node_parm_float(node, name, default=0.0):
    return float(_node_parm(node, name, default))


def _detail_float(geo, name, default=0.0):
    attrib = geo.findGlobalAttrib(name)
    if attrib is None:
        return default
    return float(geo.attribValue(attrib))


def _controller_settings(node):
    settings = {
        "create_validation_locators": _node_parm_bool(node, "maya_create_validation_locators", True),
        "validation_locator_prefix": _node_parm_string(node, "maya_validation_locator_prefix", "hda_"),
        "translate_scale": _node_parm_float(node, "maya_translate_scale", 100.0),
        "wheel_roll_multiplier": _node_parm_float(node, "maya_wheel_roll_multiplier", 0.00277778),
        "parent_validation_locators": _node_parm_bool(node, "maya_parent_validation_locators", True),
        "key_controllers": _node_parm_bool(node, "maya_key_controllers", False),
        "normalize_roll_to_first_frame": True,
        "controllers": {},
    }

    for name in LOCATOR_NAMES:
        parm_suffix = name
        settings["controllers"][name] = {
            "node": _node_parm_string(node, "maya_{}_controller".format(parm_suffix), ""),
            "key_transform": name not in WHEEL_NAMES,
            "roll_attr": "",
            "steer_attr": "",
        }

    for name in WHEEL_NAMES:
        settings["controllers"][name]["roll_attr"] = _node_parm_string(
            node, "maya_{}_roll_attr".format(name), ""
        )

    for name in ("wheel_FL", "wheel_FR"):
        settings["controllers"][name]["steer_attr"] = _node_parm_string(
            node, "maya_{}_steer_attr".format(name), ""
        )

    return settings


def _point_by_name(geo, name):
    name_attrib = geo.findPointAttrib("name")
    if name_attrib is None:
        return None

    for point in geo.points():
        if point.attribValue(name_attrib) == name:
            return point

    return None


def _point_float(point, attrib_name, default=0.0):
    attrib = point.geometry().findPointAttrib(attrib_name)
    if attrib is None:
        return default
    return float(point.attribValue(attrib))


def _point_tuple(point, attrib_name, default):
    attrib = point.geometry().findPointAttrib(attrib_name)
    if attrib is None:
        return tuple(default)
    return tuple(float(v) for v in point.attribValue(attrib))


def _point_string(point, attrib_name, default=""):
    attrib = point.geometry().findPointAttrib(attrib_name)
    if attrib is None:
        return default
    value = point.attribValue(attrib)
    if value is None:
        return default
    return str(value)


def _point_int(point, attrib_name, default=0):
    attrib = point.geometry().findPointAttrib(attrib_name)
    if attrib is None:
        return default
    return int(point.attribValue(attrib))


def _normalize_wheel_roll_to_first_frame(frames):
    if not frames:
        return {}
    first_locators = frames[0].get("locators", {})
    offsets = {}
    for name in WHEEL_NAMES:
        values = first_locators.get(name) or {}
        offsets[name] = {
            "roll": float(values.get("roll", 0.0)),
            "roll_degrees": float(values.get("roll_degrees", 0.0)),
        }

    for frame_data in frames:
        locators = frame_data.get("locators", {})
        for name in WHEEL_NAMES:
            values = locators.get(name)
            if not values:
                continue
            offset = offsets.get(name, {})
            values["roll"] = float(values.get("roll", 0.0)) - float(offset.get("roll", 0.0))
            values["roll_degrees"] = float(values.get("roll_degrees", 0.0)) - float(offset.get("roll_degrees", 0.0))

    return offsets


def _locator_payload(point):
    return {
        "translate": tuple(float(v) for v in point.position()),
        "orient": _point_tuple(point, "orient", (0.0, 0.0, 0.0, 1.0)),
        "roll": _point_float(point, "roll", 0.0),
        "roll_degrees": _point_float(point, "roll_degrees", 0.0),
        "steer": _point_float(point, "steer", 0.0),
        "steer_degrees": _point_float(point, "steer_degrees", 0.0),
    }


def _controller_payload(point, locator_name):
    return {
        "node": _point_string(point, "maya_controller", ""),
        "key_transform": locator_name not in WHEEL_NAMES,
        "roll_attr": _point_string(point, "maya_roll_attr", ""),
        "steer_attr": _point_string(point, "maya_steer_attr", ""),
    }


def _vehicles_from_geo(geo):
    locator_attrib = geo.findPointAttrib("locator_name")
    vehicle_attrib = geo.findPointAttrib("vehicle_id")
    if locator_attrib is None or vehicle_attrib is None:
        locators = {}
        for name in LOCATOR_NAMES:
            point = _point_by_name(geo, name)
            if point is None:
                return []
            locators[name] = point
        return [
            {
                "id": "vehicle_001",
                "index": 0,
                "spec_index": -1,
                "label": "Preview Car",
                "namespace": "",
                "spec_path": "",
                "points": locators,
            }
        ]

    vehicles = {}
    for point in geo.points():
        locator_name = _point_string(point, "locator_name", "")
        if locator_name not in LOCATOR_NAMES:
            continue
        vehicle_id = _point_string(point, "vehicle_id", "vehicle_001") or "vehicle_001"
        vehicle = vehicles.setdefault(
            vehicle_id,
            {
                "id": vehicle_id,
                "index": _point_int(point, "vehicle_index", len(vehicles)),
                "spec_index": _point_int(point, "vehicle_spec_index", -1),
                "label": _point_string(point, "vehicle_label", ""),
                "namespace": _point_string(point, "vehicle_namespace", ""),
                "spec_path": _point_string(point, "vehicle_spec_path", ""),
                "points": {},
            },
        )
        vehicle["points"][locator_name] = point

    return [vehicles[key] for key in sorted(vehicles, key=lambda item: vehicles[item]["index"])]


def _vehicle_frame_payload(vehicle):
    missing = [name for name in LOCATOR_NAMES if name not in vehicle["points"]]
    if missing:
        raise RuntimeError("Vehicle '{}' is missing locators: {}".format(vehicle["id"], ", ".join(missing)))

    locators = {}
    controllers = {}
    for name in LOCATOR_NAMES:
        point = vehicle["points"][name]
        locators[name] = _locator_payload(point)
        controllers[name] = _controller_payload(point, name)

    return {
        "id": vehicle["id"],
        "index": vehicle["index"],
        "spec_index": vehicle["spec_index"],
        "label": vehicle["label"],
        "namespace": vehicle["namespace"],
        "spec_path": vehicle["spec_path"],
        "maya_export": {"controllers": controllers},
        "locators": locators,
    }


def _normalize_vehicle_roll_to_first_frame(frames):
    if not frames:
        return {}
    offsets = {}
    first_vehicles = frames[0].get("vehicles") or []
    for vehicle in first_vehicles:
        vehicle_id = vehicle.get("id", "vehicle_001")
        offsets[vehicle_id] = {}
        for name in WHEEL_NAMES:
            values = (vehicle.get("locators") or {}).get(name) or {}
            offsets[vehicle_id][name] = {
                "roll": float(values.get("roll", 0.0)),
                "roll_degrees": float(values.get("roll_degrees", 0.0)),
            }

    for frame_data in frames:
        for vehicle in frame_data.get("vehicles") or []:
            vehicle_offsets = offsets.get(vehicle.get("id", "vehicle_001"), {})
            locators = vehicle.get("locators") or {}
            for name in WHEEL_NAMES:
                values = locators.get(name)
                if not values:
                    continue
                offset = vehicle_offsets.get(name, {})
                values["roll"] = float(values.get("roll", 0.0)) - float(offset.get("roll", 0.0))
                values["roll_degrees"] = float(values.get("roll_degrees", 0.0)) - float(offset.get("roll_degrees", 0.0))

    return offsets


def export_node(node):
    import hou
    start = int(hou.playbar.frameRange()[0])
    end = int(hou.playbar.frameRange()[1])
    fps = float(hou.fps())

    hip_dir = hou.expandString("$HIP")
    if not hip_dir or hip_dir == "$HIP":
        hip_dir = os.path.expanduser("~/Desktop")

    default_path = os.path.join(hip_dir, "car_locator_anim.json")
    output_path = hou.ui.selectFile(
        title="Export Car Locator Animation JSON",
        default_value=default_path,
        file_type=hou.fileType.Any,
        chooser_mode=hou.fileChooserMode.Write,
    )
    if not output_path:
        return

    output_path = hou.expandString(output_path)
    if not output_path.lower().endswith(".json"):
        output_path += ".json"

    original_frame = hou.frame()
    frames = []
    first_geo = None

    try:
        for frame in range(start, end + 1):
            hou.setFrame(frame)
            geo = node.geometry()
            if first_geo is None:
                first_geo = geo
            vehicles = [_vehicle_frame_payload(vehicle) for vehicle in _vehicles_from_geo(geo)]
            if not vehicles:
                raise hou.Error("No Smart CarSystem locator points were found at frame {}.".format(frame))

            frame_payload = {"frame": frame, "vehicles": vehicles}
            if vehicles:
                frame_payload["locators"] = vehicles[0]["locators"]
            frames.append(frame_payload)
    finally:
        hou.setFrame(original_frame)

    roll_offsets = _normalize_vehicle_roll_to_first_frame(frames)
    first_vehicles = frames[0].get("vehicles", []) if frames else []

    data = {
        "schema": "smart_car_locator_anim.v3",
        "source_node": node.path(),
        "fps": fps,
        "start_frame": start,
        "end_frame": end,
        "locator_names": LOCATOR_NAMES,
        "maya_export": _controller_settings(node),
        "vehicles": [
            {
                "id": vehicle.get("id", ""),
                "index": vehicle.get("index", 0),
                "spec_index": vehicle.get("spec_index", -1),
                "label": vehicle.get("label", ""),
                "namespace": vehicle.get("namespace", ""),
                "spec_path": vehicle.get("spec_path", ""),
                "maya_export": vehicle.get("maya_export", {}),
            }
            for vehicle in first_vehicles
        ],
        "vehicle_dimensions": {
            "vehicle_source_used": bool(_detail_float(first_geo, "vehicle_source_used", 0.0)) if first_geo else False,
            "wheelbase": _detail_float(first_geo, "wheelbase", 0.0) if first_geo else 0.0,
            "track_width": _detail_float(first_geo, "track_width", 0.0) if first_geo else 0.0,
            "wheel_radius": _detail_float(first_geo, "wheel_radius", 0.0) if first_geo else 0.0,
        },
        "roll_offsets": roll_offsets,
        "frames": frames,
    }

    with open(output_path, "w") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)

    print("Exported car locator animation:")
    print("  {}".format(output_path.replace("\\", "/")))
    print("Frames:")
    print("  {}-{}".format(start, end))


def export_selected_node():
    import hou

    selected = hou.selectedNodes()
    if not selected:
        raise hou.Error("Select the Smart Car Path Locators SOP node before exporting.")
    export_node(selected[0])


if globals().get("SMART_CAR_EXPORT_NODE") is not None:
    export_node(globals()["SMART_CAR_EXPORT_NODE"])
else:
    export_selected_node()
