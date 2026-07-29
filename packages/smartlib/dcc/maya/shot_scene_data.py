from __future__ import annotations

from typing import Any


DEFAULT_CAMERAS = {"persp", "top", "front", "side"}
CAMERA_SHAPE_ATTRS = (
    "focalLength",
    "cameraScale",
    "nearClipPlane",
    "farClipPlane",
    "horizontalFilmAperture",
    "verticalFilmAperture",
    "filmFit",
    "lensSqueezeRatio",
    "fStop",
    "focusDistance",
    "shutterAngle",
    "orthographic",
    "orthographicWidth",
)


def list_scene_cameras() -> list[str]:
    cmds = _maya_cmds()
    cameras = []
    for shape in cmds.ls(type="camera", long=True) or []:
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if not parents:
            continue
        transform = parents[0]
        if transform.rsplit("|", 1)[-1].split(":")[-1] in DEFAULT_CAMERAS:
            continue
        cameras.append(transform)
    return sorted(set(cameras), key=str.lower)


def collect_camera_data(camera: str) -> dict[str, Any]:
    cmds = _maya_cmds()
    transform, shape = _camera_nodes(cmds, camera)
    start = int(round(cmds.playbackOptions(query=True, minTime=True)))
    end = int(round(cmds.playbackOptions(query=True, maxTime=True)))
    current_time = cmds.currentTime(query=True)
    samples = []
    try:
        for frame in range(start, end + 1):
            cmds.currentTime(frame, edit=True)
            matrix = cmds.xform(transform, query=True, worldSpace=True, matrix=True)
            samples.append({"frame": frame, "world_matrix": [float(value) for value in matrix]})
    finally:
        cmds.currentTime(current_time, edit=True)
    attrs = {}
    for attr in CAMERA_SHAPE_ATTRS:
        plug = f"{shape}.{attr}"
        if cmds.objExists(plug):
            try:
                attrs[attr] = cmds.getAttr(plug)
            except Exception:
                pass
    return {
        "schema": "maya_camera/v1",
        "camera": transform.rsplit("|", 1)[-1],
        "frame_range": [start, end],
        "shape_attributes": attrs,
        "samples": samples,
    }


def apply_camera_data(data: dict[str, Any], *, name: str | None = None) -> str:
    cmds = _maya_cmds()
    camera_name = str(name or data.get("camera") or "camera")
    camera_name = camera_name.rsplit("|", 1)[-1]
    if cmds.objExists(camera_name):
        transform, shape = _camera_nodes(cmds, camera_name)
    else:
        transform, shape = cmds.camera(name=camera_name)
    for attr, value in (data.get("shape_attributes") or {}).items():
        plug = f"{shape}.{attr}"
        if not cmds.objExists(plug):
            continue
        try:
            cmds.setAttr(plug, value)
        except Exception:
            pass
    samples = data.get("samples") or []
    for sample in samples:
        frame = float(sample.get("frame", 1))
        matrix = sample.get("world_matrix") or []
        if len(matrix) != 16:
            continue
        cmds.xform(transform, worldSpace=True, matrix=matrix)
        cmds.setKeyframe(transform, attribute=("translate", "rotate"), time=frame)
    frame_range = data.get("frame_range") or []
    if len(frame_range) == 2:
        cmds.playbackOptions(minTime=frame_range[0], maxTime=frame_range[1])
    return transform


def _camera_nodes(cmds: Any, camera: str) -> tuple[str, str]:
    if not cmds.objExists(camera):
        raise RuntimeError(f"Camera was not found: {camera}")
    if cmds.nodeType(camera) == "camera":
        parents = cmds.listRelatives(camera, parent=True, fullPath=True) or []
        if not parents:
            raise RuntimeError(f"Camera transform was not found: {camera}")
        return parents[0], camera
    shapes = cmds.listRelatives(camera, shapes=True, fullPath=True, type="camera") or []
    if not shapes:
        raise RuntimeError(f"Camera shape was not found under: {camera}")
    return camera, shapes[0]


def _maya_cmds():
    import maya.cmds as cmds

    return cmds
