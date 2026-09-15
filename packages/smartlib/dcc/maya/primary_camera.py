"""Authoritative, review-independent Primary Camera publish."""
from __future__ import annotations

import json
from pathlib import Path

from . import camera_native, camera_output as co


SCHEMA = "smartpipeline.primary_camera.v1"
IMPORT_NAMESPACE = "smartPrimary"


def collect(primary, frame_range, cmds):
    primary, shape = co.camera_nodes(primary, cmds)
    if cmds.objExists(primary + "." + co.OWNER_ATTR):
        raise ValueError("Primary cannot be a derived Review camera.")
    co._check_supported(cmds, shape)
    start, end = (int(value) for value in frame_range)
    if end < start:
        raise ValueError("Primary Camera frame range is invalid.")
    nodes = camera_native.dependencies(primary, cmds)
    return {
        "schema": SCHEMA,
        "role": "primary",
        "camera": primary.rsplit("|", 1)[-1].split(":")[-1],
        "primary_path": primary,
        "dependency_nodes": nodes,
        "frame_range": [start, end],
        "units": {
            unit: cmds.currentUnit(query=True, **{unit: True})
            for unit in ("time", "linear", "angle")
        },
        "portable_export": {
            "status": "pending",
            "camera_name": "primary_cam",
            "formats": ["usd", "fbx"],
        },
    }


def export_native(payload, directory, cmds):
    return camera_native.export_native(payload, directory, cmds)


def restore_with_root(data, *, cmds, provenance="", frame_offset=0.0):
    if data.get("schema") != SCHEMA:
        raise ValueError("Unsupported Primary Camera schema.")
    if frame_offset:
        raise ValueError("Unbaked Primary Camera requires original timing; Build offset is unsupported.")
    for unit, value in (data.get("units") or {}).items():
        if cmds.currentUnit(query=True, **{unit: True}) != value:
            raise ValueError("Primary Camera scene units do not match: " + unit)
    filename = str((data.get("files") or {}).get("ma") or "")
    path = Path(provenance).parent / filename
    if not filename or Path(filename).name != filename or not path.is_file():
        raise FileNotFoundError("Primary Camera native file is missing: " + str(path))
    if cmds.namespace(exists=IMPORT_NAMESPACE):
        raise ValueError("Primary Camera namespace is occupied: " + IMPORT_NAMESPACE)
    if cmds.objExists(":smartPrimaryPublish"):
        raise ValueError("A Primary Camera Publish is already active in this scene.")
    imported = cmds.file(
        str(path), i=True, type="mayaAscii", namespace=IMPORT_NAMESPACE,
        mergeNamespacesOnClash=False, returnNewNodes=True, executeScriptNodes=False,
    )
    original = str(data.get("primary_path") or "")
    expected = (
        "|" + "|".join(IMPORT_NAMESPACE + ":" + part for part in original.split("|") if part)
        if original.startswith("|") else IMPORT_NAMESPACE + ":" + original
    )
    primary, _shape = co.camera_nodes(expected, cmds)
    if primary not in (cmds.ls(imported, long=True) or []):
        raise ValueError("Primary Camera was not uniquely restored.")
    primary_uuid = cmds.ls(primary, uuid=True)[0]
    imported_set = set(cmds.ls(imported, long=True) or [])
    roots = [
        node for node in imported_set
        if cmds.objectType(node, isAType="dagNode")
        and not cmds.listRelatives(node, parent=True, fullPath=True)
    ]
    root = cmds.group(empty=True, name=":smartPrimaryPublish")
    for node in roots:
        cmds.parent(node, root, relative=True)
    primary = cmds.ls(primary_uuid, long=True)[0]
    co._string_attr(cmds, primary, "smartCameraRole", "primary")
    co._string_attr(cmds, primary, "smartCameraPublishSource", str(provenance))
    return root, primary


def restore(data, *, cmds, provenance="", frame_offset=0.0):
    root, _primary = restore_with_root(
        data, cmds=cmds, provenance=provenance, frame_offset=frame_offset
    )
    return root
