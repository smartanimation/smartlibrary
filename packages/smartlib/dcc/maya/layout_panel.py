from __future__ import annotations

import fnmatch
from typing import Any


SMART_GATE_GUIDE_NODE_TYPE = "SmartViewportGateGuide"
SMART_GATE_GUIDE_PATTERNS = ("SmartGateGuide*", "*SmartGateGuide*")
DEFAULT_CAMERA_NAMES = {"persp", "top", "front", "side", "perspShape", "topShape", "frontShape", "sideShape"}


def toggle_viewport_lights() -> str:
    cmds = _maya_cmds()
    panel = _active_model_panel(cmds)
    current = _query_model_editor(cmds, panel, "displayLights") or "default"
    next_value = "default" if current == "all" else "all"
    cmds.modelEditor(panel, edit=True, displayLights=next_value)
    return next_value


def set_viewport_lights(enabled: bool) -> str:
    cmds = _maya_cmds()
    panel = _active_model_panel(cmds)
    value = "all" if enabled else "default"
    cmds.modelEditor(panel, edit=True, displayLights=value)
    return value


def toggle_viewport_textures() -> bool:
    return _toggle_model_editor_bool("displayTextures")


def set_viewport_textures(enabled: bool) -> bool:
    cmds = _maya_cmds()
    panel = _active_model_panel(cmds)
    cmds.modelEditor(panel, edit=True, displayTextures=bool(enabled))
    return bool(enabled)


def toggle_active_camera_dof() -> bool:
    cmds = _maya_cmds()
    camera_shape = _active_camera_shape(cmds)
    if not camera_shape:
        raise RuntimeError("No active camera was found.")
    attr = f"{camera_shape}.depthOfField"
    if not cmds.objExists(attr):
        raise RuntimeError(f"Camera does not have depthOfField: {camera_shape}")
    next_value = not bool(cmds.getAttr(attr))
    cmds.setAttr(attr, next_value)
    return next_value


def set_active_camera_dof(enabled: bool) -> bool:
    cmds = _maya_cmds()
    camera_shape = _active_camera_shape(cmds)
    if not camera_shape:
        raise RuntimeError("No active camera was found.")
    attr = f"{camera_shape}.depthOfField"
    if not cmds.objExists(attr):
        raise RuntimeError(f"Camera does not have depthOfField: {camera_shape}")
    cmds.setAttr(attr, bool(enabled))
    return bool(enabled)


def current_camera_shape() -> str:
    return _active_camera_shape(_maya_cmds())


def apply_camera_display_defaults(mode: str = "selected", pattern_text: str = "") -> int:
    cameras = _camera_shapes_for_mode(_maya_cmds(), mode, pattern_text)
    for camera in cameras:
        _set_attr_if_exists(camera, "displayResolution", True)
        _set_attr_if_exists(camera, "displayGateMask", True)
        _set_attr_if_exists(camera, "displayFilmGate", False)
        _set_attr_if_exists(camera, "filmFit", 3)
        _set_attr_if_exists(camera, "nearClipPlane", 0.1)
        _set_attr_if_exists(camera, "farClipPlane", 10000.0)
        _set_attr_if_exists(camera, "overscan", 1.0)
    return len(cameras)


def set_resolution_gate(mode: str = "selected", enabled: bool = True, pattern_text: str = "") -> int:
    return _set_camera_attr_for_mode(mode, "displayResolution", enabled, pattern_text)


def set_gate_mask(mode: str = "selected", enabled: bool = True, pattern_text: str = "") -> int:
    return _set_camera_attr_for_mode(mode, "displayGateMask", enabled, pattern_text)


def set_film_fit_overscan(mode: str = "selected", pattern_text: str = "") -> int:
    return _set_camera_attr_for_mode(mode, "filmFit", 3, pattern_text)


def set_film_fit(mode: str = "selected", value: int = 3, pattern_text: str = "") -> int:
    return _set_camera_attr_for_mode(mode, "filmFit", int(value), pattern_text)


def set_near_clip(mode: str = "selected", value: float = 0.1, pattern_text: str = "") -> int:
    return _set_camera_attr_for_mode(mode, "nearClipPlane", float(value), pattern_text)


def set_far_clip(mode: str = "selected", value: float = 10000.0, pattern_text: str = "") -> int:
    return _set_camera_attr_for_mode(mode, "farClipPlane", float(value), pattern_text)


def set_display_overscan(mode: str = "selected", value: float = 1.0, pattern_text: str = "") -> int:
    return _set_camera_attr_for_mode(mode, "overscan", float(value), pattern_text)


def set_lens(value: float, mode: str = "selected", pattern_text: str = "") -> int:
    return _set_camera_attr_for_mode(mode, "focalLength", float(value), pattern_text)


def set_fstop(value: float, mode: str = "selected", pattern_text: str = "") -> int:
    return _set_camera_attr_for_mode(mode, "fStop", float(value), pattern_text)


def create_smart_gate_guide() -> str:
    cmds = _maya_cmds()
    _ensure_smart_gate_guide_plugin(cmds)
    if not hasattr(cmds, "SmartGateGuide"):
        raise RuntimeError("SmartGateGuide command is not registered.")
    node = cmds.SmartGateGuide()
    if isinstance(node, (list, tuple)):
        node = node[0] if node else ""
    if node:
        cmds.select(node, replace=True)
    return str(node or "")


def select_smart_gate_guides() -> list[str]:
    cmds = _maya_cmds()
    guides = _smart_gate_guide_transforms(cmds)
    if not guides:
        raise RuntimeError("No SmartGateGuide nodes were found.")
    cmds.select(guides, replace=True)
    return guides


def set_smart_gate_guide_attr(attr: str, enabled: bool) -> int:
    cmds = _maya_cmds()
    shapes = _smart_gate_guide_shapes(cmds)
    if not shapes:
        create_smart_gate_guide()
        shapes = _smart_gate_guide_shapes(cmds)
    for shape in shapes:
        _set_attr_if_exists(shape, attr, bool(enabled))
    return len(shapes)


def toggle_smart_gate_guide_attr(attr: str) -> tuple[bool, int]:
    cmds = _maya_cmds()
    shapes = _smart_gate_guide_shapes(cmds)
    if not shapes:
        create_smart_gate_guide()
        shapes = _smart_gate_guide_shapes(cmds)
    if not shapes:
        raise RuntimeError("No SmartGateGuide nodes were found.")
    first_attr = f"{shapes[0]}.{attr}"
    next_value = True
    if cmds.objExists(first_attr):
        next_value = not bool(cmds.getAttr(first_attr))
    for shape in shapes:
        _set_attr_if_exists(shape, attr, next_value)
    return next_value, len(shapes)


def wildcard_select(pattern_text: str) -> list[str]:
    cmds = _maya_cmds()
    patterns = _split_patterns(pattern_text)
    if not patterns:
        raise RuntimeError("Enter a wildcard pattern such as *_geo or cam_?.")

    matches: list[str] = []
    seen = set()
    for pattern in patterns:
        for node in _nodes_matching_pattern(cmds, pattern):
            if node not in seen:
                matches.append(node)
                seen.add(node)
    if not matches:
        raise RuntimeError(f"No objects matched: {pattern_text}")
    cmds.select(matches, replace=True)
    return matches


def clear_selection() -> None:
    _maya_cmds().select(clear=True)


def set_picture_in_picture(enabled: bool) -> int:
    from smartlib.dcc.maya import smart_shot

    return smart_shot.set_picture_in_picture(enabled)


def set_image_plane_alpha_gain(value: float) -> int:
    cmds = _maya_cmds()
    image_planes = cmds.ls(type="imagePlane", long=True) or []
    for image_plane in image_planes:
        _set_attr_if_exists(image_plane, "alphaGain", float(value))
    return len(image_planes)


def _toggle_model_editor_bool(flag: str) -> bool:
    cmds = _maya_cmds()
    panel = _active_model_panel(cmds)
    current = bool(_query_model_editor(cmds, panel, flag))
    next_value = not current
    cmds.modelEditor(panel, edit=True, **{flag: next_value})
    return next_value


def _query_model_editor(cmds: Any, panel: str, flag: str) -> Any:
    try:
        return cmds.modelEditor(panel, query=True, **{flag: True})
    except Exception as exc:
        raise RuntimeError(f"Could not query model panel setting: {flag}") from exc


def _active_model_panel(cmds: Any) -> str:
    panel = cmds.getPanel(withFocus=True)
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
        return panel
    panels = cmds.getPanel(type="modelPanel") or []
    if not panels:
        raise RuntimeError("No modelPanel was found.")
    return panels[0]


def _active_camera_shape(cmds: Any) -> str:
    sequencer_camera = _sequencer_camera_shape(cmds)
    if sequencer_camera:
        return sequencer_camera
    panel = _active_model_panel(cmds)
    camera = ""
    try:
        camera = cmds.modelPanel(panel, query=True, camera=True) or ""
    except Exception:
        camera = ""
    return _camera_shape(cmds, camera)


def _sequencer_camera_shape(cmds: Any) -> str:
    current_time = float(cmds.currentTime(query=True))
    candidates = []
    for shot_node in cmds.ls(type="shot") or []:
        start = _query_time(cmds, shot_node, "sequenceStartTime", "sequenceStartFrame", "startTime")
        end = _query_time(cmds, shot_node, "sequenceEndTime", "sequenceEndFrame", "endTime")
        if start is None or end is None:
            continue
        if float(start) <= current_time <= float(end):
            camera = _query_shot(cmds, shot_node, "currentCamera") or ""
            camera_shape = _camera_shape(cmds, camera)
            if camera_shape:
                track = _get_optional_number(cmds, shot_node, "track")
                candidates.append((track if track is not None else 0.0, float(start), shot_node, camera_shape))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return candidates[0][3]


def _camera_shapes_for_mode(cmds: Any, mode: str, pattern_text: str = "") -> list[str]:
    normalized = str(mode or "selected").strip().lower()
    if normalized == "all":
        cameras = [
            camera
            for camera in (cmds.ls(type="camera", long=True) or [])
            if not _is_default_camera(cmds, camera)
        ]
    elif normalized == "active":
        cameras = [_active_camera_shape(cmds)]
    elif normalized == "pattern":
        cameras = []
        for pattern in _split_patterns(pattern_text):
            for node in _nodes_matching_pattern(cmds, pattern):
                shape = _camera_shape(cmds, node)
                if shape:
                    cameras.append(shape)
    else:
        cameras = []
        for node in cmds.ls(selection=True, long=True) or []:
            shape = _camera_shape(cmds, node)
            if shape:
                cameras.append(shape)
    cameras = [camera for camera in cameras if camera]
    if not cameras:
        raise RuntimeError(f"No camera targets found for mode: {mode}")
    return sorted(set(cameras))


def _is_default_camera(cmds: Any, camera_shape: str) -> bool:
    if not camera_shape:
        return False
    shape_name = camera_shape.split("|")[-1].split(":")[-1]
    if shape_name in DEFAULT_CAMERA_NAMES:
        return True
    parents = cmds.listRelatives(camera_shape, parent=True, fullPath=True) or []
    if not parents:
        return False
    transform_name = parents[0].split("|")[-1].split(":")[-1]
    return transform_name in DEFAULT_CAMERA_NAMES


def _set_camera_attr_for_mode(mode: str, attr: str, value: Any, pattern_text: str = "") -> int:
    cmds = _maya_cmds()
    cameras = _camera_shapes_for_mode(cmds, mode, pattern_text)
    for camera in cameras:
        _set_attr_if_exists(camera, attr, value)
    return len(cameras)


def _camera_shape(cmds: Any, node: str) -> str:
    if not node:
        return ""
    try:
        if cmds.nodeType(node) == "camera":
            return node
    except Exception:
        return ""
    shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    for shape in shapes:
        try:
            if cmds.nodeType(shape) == "camera":
                return shape
        except Exception:
            continue
    return ""


def _query_shot(cmds: Any, node: str, flag: str) -> Any:
    try:
        return cmds.shot(node, query=True, **{flag: True})
    except Exception:
        return None


def _query_time(cmds: Any, node: str, *flags_or_attrs: str) -> float | None:
    for name in flags_or_attrs:
        value = _query_shot(cmds, node, name)
        if value is not None:
            return float(value)
        attr = f"{node}.{name}"
        if cmds.objExists(attr):
            try:
                return float(cmds.getAttr(attr))
            except Exception:
                pass
    return None


def _get_optional_number(cmds: Any, node: str, attr: str) -> float | None:
    full = f"{node}.{attr}"
    if not cmds.objExists(full):
        return None
    try:
        return float(cmds.getAttr(full))
    except Exception:
        return None


def _set_attr_if_exists(node: str, attr: str, value: Any) -> bool:
    cmds = _maya_cmds()
    full_attr = f"{node}.{attr}"
    if not cmds.objExists(full_attr):
        return False
    try:
        cmds.setAttr(full_attr, value)
        return True
    except Exception:
        return False


def _smart_gate_guide_shapes(cmds: Any) -> list[str]:
    shapes = cmds.ls(type=SMART_GATE_GUIDE_NODE_TYPE, long=True) or []
    if shapes:
        return sorted(set(shapes))
    matches: list[str] = []
    for pattern in SMART_GATE_GUIDE_PATTERNS:
        for node in cmds.ls(pattern, long=True) or []:
            try:
                if cmds.nodeType(node) == SMART_GATE_GUIDE_NODE_TYPE:
                    matches.append(node)
                    continue
            except Exception:
                pass
            shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
            for shape in shapes:
                try:
                    if cmds.nodeType(shape) == SMART_GATE_GUIDE_NODE_TYPE:
                        matches.append(shape)
                except Exception:
                    continue
    return sorted(set(matches))


def _smart_gate_guide_transforms(cmds: Any) -> list[str]:
    transforms = []
    for shape in _smart_gate_guide_shapes(cmds):
        parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        transforms.append(parent[0] if parent else shape)
    return sorted(set(transforms))


def _split_patterns(pattern_text: str) -> list[str]:
    text = str(pattern_text or "").replace(",", " ").replace(";", " ")
    return [part.strip() for part in text.split() if part.strip()]


def _nodes_matching_pattern(cmds: Any, pattern: str) -> list[str]:
    direct = cmds.ls(pattern, long=True) or []
    if "?" not in pattern:
        return direct

    all_nodes = cmds.ls(long=True) or []
    matches = list(direct)
    seen = set(matches)
    for node in all_nodes:
        short_name = node.split("|")[-1]
        plain_name = short_name.split(":")[-1]
        if (
            fnmatch.fnmatchcase(node, pattern)
            or fnmatch.fnmatchcase(short_name, pattern)
            or fnmatch.fnmatchcase(plain_name, pattern)
        ):
            if node not in seen:
                matches.append(node)
                seen.add(node)
    return matches


def _ensure_smart_gate_guide_plugin(cmds: Any) -> None:
    from smartlib.dcc.maya import smart_menu

    smart_menu.ensure_smart_gate_guide_plugin(cmds, required=True)


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("MAYA Layout Panel is available inside Maya.") from exc
    return cmds
