from __future__ import annotations

from typing import Any
from pathlib import Path


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
LIGHT_TYPES = {
    "ambientLight", "areaLight", "directionalLight", "pointLight",
    "spotLight", "volumeLight", "aiAreaLight", "aiMeshLight",
    "aiPhotometricLight", "VRayLightRectShape", "VRayLightDomeShape",
}


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


def list_scene_component_roots(data_type: str) -> list[str]:
    """Return useful DAG roots for Camera/Light Data Publish."""

    cmds = _maya_cmds()
    clean_type = str(data_type or "").strip().lower()
    selected = cmds.ls(selection=True, long=True, type="transform") or []
    selected = [node for node in selected if _root_contains_component(cmds, node, clean_type)]
    if selected:
        return sorted(set(selected), key=str.lower)
    candidates = []
    for transform in cmds.ls(type="transform", long=True) or []:
        if _root_contains_component(cmds, transform, clean_type):
            candidates.append(transform)
    tagged = [
        node for node in candidates
        if cmds.objExists(f"{node}.smartpipelineDataType")
        and str(cmds.getAttr(f"{node}.smartpipelineDataType") or "").strip().lower() == clean_type
    ]
    if tagged:
        return sorted(set(tagged), key=str.lower)
    preferred = []
    for node in candidates:
        leaf = node.rsplit("|", 1)[-1].split(":")[-1].lower()
        if clean_type == "camera" and (
            leaf.startswith(("cam", "camera", "vcam", "virtual_camera", "shot_camera"))
            and not leaf.startswith(("light", "lights"))
            and (
                leaf.endswith(("_grp", "_rig"))
                or leaf in {"camera", "vcam", "virtual_camera", "shot_camera"}
                or any(
                    cmds.nodeType(shape) == "camera"
                    for shape in (cmds.listRelatives(node, shapes=True, fullPath=True) or [])
                )
            )
        ):
            preferred.append(node)
        elif clean_type == "light":
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            parent_leaf = parents[0].rsplit("|", 1)[-1].split(":")[-1].lower() if parents else ""
            # Publish each light/rig below the template container separately.
            if parent_leaf == "lights_grp":
                preferred.append(node)
    if preferred:
        return sorted(set(preferred), key=str.lower)
    # Prefer top-most matching roots so a rig appears once instead of once per child.
    candidate_set = set(candidates)
    roots = [node for node in candidates if not any(parent in candidate_set for parent in _dag_parents(node))]
    return sorted(set(roots), key=str.lower)


def _root_contains_component(cmds, root: str, data_type: str) -> bool:
    shapes = cmds.listRelatives(root, allDescendents=True, fullPath=True, shapes=True) or []
    shapes.extend(cmds.listRelatives(root, shapes=True, fullPath=True) or [])
    node_types = {str(cmds.nodeType(shape)) for shape in shapes}
    return "camera" in node_types if data_type == "camera" else bool(node_types & LIGHT_TYPES)


def collect_scene_component_data(root: str, data_type: str) -> dict[str, Any]:
    """Collect a root-based Maya Camera or Light data-package manifest."""

    cmds = _maya_cmds()
    clean_type = str(data_type or "").strip().lower()
    if clean_type not in {"camera", "light"}:
        raise ValueError(f"Unsupported scene component data type: {data_type}")
    if not cmds.objExists(root) or cmds.nodeType(root) != "transform":
        raise RuntimeError(f"{clean_type.title()} root was not found: {root}")
    root = (cmds.ls(root, long=True) or [root])[0]
    descendants = cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
    dag_nodes = {root, *descendants}
    shapes = [node for node in descendants if cmds.nodeType(node) != "transform"]
    component_shapes = [
        node for node in shapes
        if (cmds.nodeType(node) == "camera" if clean_type == "camera" else cmds.nodeType(node) in LIGHT_TYPES)
    ]
    if not component_shapes:
        raise RuntimeError(f"No {clean_type} shapes were found below: {root}")
    external_constraints = []
    for node in dag_nodes:
        for constraint in cmds.listConnections(node, source=True, destination=False, type="constraint") or []:
            constraint_long = (cmds.ls(constraint, long=True) or [constraint])[0]
            if constraint_long not in dag_nodes:
                external_constraints.append(str(constraint))
    if external_constraints:
        raise RuntimeError(
            f"{clean_type.title()} Data Publish is blocked by constraints outside '{root}': "
            + ", ".join(sorted(set(external_constraints)))
        )
    start = int(round(cmds.playbackOptions(query=True, minTime=True)))
    end = int(round(cmds.playbackOptions(query=True, maxTime=True)))
    return {
        "schema": f"maya_{clean_type}_package/v1",
        "data_type": clean_type,
        "root": root.rsplit("|", 1)[-1],
        "frame_range": [start, end],
        "nodes": [node.rsplit("|", 1)[-1] for node in sorted(dag_nodes)],
        "shapes": [
            {"name": node.rsplit("|", 1)[-1], "type": str(cmds.nodeType(node))}
            for node in sorted(component_shapes)
        ],
        "external_constraints": [],
    }


def export_scene_component_selection(root: str, data_type: str, output_dir: str | Path) -> dict[str, Any]:
    """Export a complete root hierarchy as the native Data Publish payload."""

    cmds = _maya_cmds()
    clean_type = str(data_type or "").strip().lower()
    collect_scene_component_data(root, clean_type)  # preflight before writing
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    scene_path = output / f"{clean_type}.ma"
    selection = cmds.ls(selection=True, long=True) or []
    root_long = (cmds.ls(root, long=True) or [root])[0]
    parents = cmds.listRelatives(root_long, parent=True, fullPath=True) or []
    original_parent = parents[0] if parents else ""
    try:
        # Export Selected writes the parent chain as well. Temporarily detach
        # the package root so only that root and its descendants are emitted.
        if original_parent:
            root_long = (cmds.parent(root_long, world=True) or [root_long])[0]
        cmds.select(root_long, replace=True)
        cmds.file(
            str(scene_path), force=True, options="v=0;", type="mayaAscii",
            preserveReferences=True, exportSelected=True, channels=True,
            constraints=False, expressions=False, constructionHistory=False,
        )
    finally:
        if original_parent and cmds.objExists(root_long) and cmds.objExists(original_parent):
            try:
                cmds.parent(root_long, original_parent)
            except Exception:
                pass
        cmds.select(selection, replace=True) if selection else cmds.select(clear=True)
    return {"files": {"ma": scene_path.name}, "errors": {}}


def import_scene_component_package(path: str | Path) -> list[str]:
    """Import a Camera/Light native data package into the current Maya scene."""

    cmds = _maya_cmds()
    source = Path(path)
    if source.suffix.lower() == ".json":
        data = _read_json(source)
        from .camera_publish import SUPPORTED_SCHEMAS, restore_package
        if data.get('schema') in SUPPORTED_SCHEMAS:
            return [restore_package(data, cmds=cmds, provenance=str(source))]
        filename = str((data.get("files") or {}).get("ma") or f"{data.get('data_type', source.stem)}.ma")
        source = source.parent / filename
    if not source.is_file():
        raise FileNotFoundError(source)
    before = set(cmds.ls(assemblies=True, long=True) or [])
    cmds.file(str(source), i=True, type="mayaAscii", ignoreVersion=True, mergeNamespacesOnClash=False, namespace=":")
    after = set(cmds.ls(assemblies=True, long=True) or [])
    return sorted(after - before)


def _dag_parents(node: str) -> list[str]:
    parts = [part for part in str(node).split("|") if part]
    return ["|" + "|".join(parts[:index]) for index in range(1, len(parts))]


def _read_json(path: Path) -> dict[str, Any]:
    import json
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}


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


def export_camera_selection(camera: str, output_dir: str | Path) -> dict[str, Any]:
    """Export a published camera as Maya ASCII and FBX selection files."""

    cmds = _maya_cmds()
    transform, _shape = _camera_nodes(cmds, camera)
    camera_name = transform.rsplit("|", 1)[-1].split(":")[-1]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    ma_path = output / f"{camera_name}.ma"
    fbx_path = output / f"{camera_name}.fbx"
    selection = cmds.ls(selection=True, long=True) or []
    errors = {}
    try:
        cmds.select(transform, replace=True)
        cmds.file(
            str(ma_path),
            force=True,
            options="v=0;",
            type="mayaAscii",
            preserveReferences=True,
            exportSelected=True,
            channels=True,
            constraints=True,
            expressions=True,
            constructionHistory=True,
        )
        try:
            if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
                cmds.loadPlugin("fbxmaya", quiet=True)
            cmds.file(
                str(fbx_path),
                force=True,
                options="v=0;",
                type="FBX export",
                preserveReferences=True,
                exportSelected=True,
            )
        except Exception as exc:
            errors["fbx"] = str(exc)
    finally:
        try:
            cmds.select(selection, replace=True)
        except Exception:
            cmds.select(clear=True)
    files = {"ma": ma_path.name}
    if fbx_path.is_file():
        files["fbx"] = fbx_path.name
    return {"files": files, "errors": errors}


def apply_camera_data(data: dict[str, Any], *, name: str | None = None, provenance: str = '') -> str:
    cmds = _maya_cmds()
    from .camera_publish import SUPPORTED_SCHEMAS, restore_package
    if data.get("schema") in SUPPORTED_SCHEMAS:
        return restore_package(data, cmds=cmds, provenance=provenance)
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
