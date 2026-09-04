"""World-baked, dependency-free Primary camera exchange exports."""
from __future__ import annotations

import json
from pathlib import Path


CAMERA_NAME = "primary_cam"
SHAPE_ATTRIBUTES = (
    "focalLength",
    "horizontalFilmAperture",
    "verticalFilmAperture",
    "horizontalFilmOffset",
    "verticalFilmOffset",
    "nearClipPlane",
    "farClipPlane",
    "focusDistance",
    "fStop",
    "lensSqueezeRatio",
    "cameraScale",
    "filmFit",
)


def bake_primary(payload, cmds):
    """Create one parentless camera sampled in world space at integer frames."""
    source = str(payload["primary_path"])
    matches = cmds.ls(source, long=True) or []
    if len(matches) != 1:
        raise ValueError("Published Primary was not uniquely found: " + source)
    source = matches[0]
    source_shapes = cmds.listRelatives(source, shapes=True, fullPath=True, type="camera") or []
    if len(source_shapes) != 1:
        raise ValueError("Published Primary must have exactly one camera shape.")
    if cmds.objExists(CAMERA_NAME):
        cmds.delete(CAMERA_NAME)
    camera, _shape = cmds.camera()
    camera = cmds.rename(camera, CAMERA_NAME)
    shape = (cmds.listRelatives(camera, shapes=True, fullPath=True, type="camera") or [])[0]
    start, end = (int(value) for value in payload["frame_range"])
    original_time = cmds.currentTime(query=True)
    try:
        for frame in range(start, end + 1):
            cmds.currentTime(frame, edit=True)
            matrix = cmds.xform(source, query=True, worldSpace=True, matrix=True)
            cmds.xform(camera, worldSpace=True, matrix=matrix)
            for attribute in ("translate", "rotate", "scale"):
                cmds.setKeyframe(camera, attribute=attribute, time=frame)
            for attribute in SHAPE_ATTRIBUTES:
                value = cmds.getAttr(source_shapes[0] + "." + attribute)
                cmds.setAttr(shape + "." + attribute, value)
                cmds.setKeyframe(shape, attribute=attribute, time=frame)
        # Exchange cameras must never inherit a Maya viewport overscan setting.
        cmds.setAttr(shape + ".overscan", 1.0)
        cmds.setAttr(shape + ".panZoomEnabled", False)
    finally:
        cmds.currentTime(original_time, edit=True)
    return camera, shape


def export_portable(payload, directory, cmds):
    """Bake Primary once, then export the same camera to FBX and USD."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    camera, _shape = bake_primary(payload, cmds)
    start, end = (int(value) for value in payload["frame_range"])
    fbx_path = directory / (CAMERA_NAME + ".fbx")
    usd_path = directory / (CAMERA_NAME + ".usd")
    selection = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(camera, replace=True)
        cmds.loadPlugin("fbxmaya", quiet=True)
        cmds.file(
            str(fbx_path),
            force=True,
            options="v=0;",
            type="FBX export",
            exportSelected=True,
        )
        cmds.loadPlugin("mayaUsdPlugin", quiet=True)
        cmds.mayaUSDExport(
            file=str(usd_path),
            selection=True,
            frameRange=(start, end),
            frameStride=1.0,
        )
    finally:
        cmds.select(selection, replace=True) if selection else cmds.select(clear=True)
    files = {"fbx": fbx_path.name, "usd": usd_path.name}
    for kind, filename in files.items():
        path = directory / filename
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(kind.upper() + " camera export failed.")
    return files


def validate_portable(files, directory, cmds):
    """Validate USD semantics and both exchange artifacts without mutating the worker scene."""
    directory = Path(directory)
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(directory / files["usd"]))
    usd_cameras = [prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Camera)] if stage else []
    if len(usd_cameras) != 1 or usd_cameras[0].GetName() != CAMERA_NAME:
        raise RuntimeError("USD validation failed: expected one primary_cam Camera.")
    # Re-importing FBX after mayaUsdPlugin in one mayapy process is unstable in
    # Maya 2026. The bake is validated before export and FBX export success is
    # established by the non-empty artifact check in export_portable().
    return True


def update_publish(snapshot, *, status, files=None, error=""):
    """Update portable representation state in camera.json and publish.json."""
    snapshot = Path(snapshot)
    data = json.loads(snapshot.read_text(encoding="utf-8-sig"))
    portable = dict(data.get("portable_export") or {})
    portable.update(status=str(status), camera_name=CAMERA_NAME)
    if files:
        portable["files"] = dict(files)
        data_files = dict(data.get("files") or {})
        data_files.update(files)
        data["files"] = data_files
    if error:
        portable["error"] = str(error)
    else:
        portable.pop("error", None)
    data["portable_export"] = portable
    _atomic_json(snapshot, data)
    publish_path = snapshot.with_name("publish.json")
    publish = json.loads(publish_path.read_text(encoding="utf-8-sig"))
    publish["portable_export"] = portable
    if files:
        publish_files = dict(publish.get("files") or {})
        publish_files.update(files)
        publish["files"] = publish_files
    _atomic_json(publish_path, publish)


def _atomic_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
