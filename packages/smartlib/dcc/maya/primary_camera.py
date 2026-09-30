"""Authoritative, review-independent Primary Camera publish."""
from __future__ import annotations

import json
from pathlib import Path

from . import camera_native, camera_output as co


SCHEMA = "smartpipeline.primary_camera.v1"
IMPORT_NAMESPACE = "smartPrimary"


def collect(primary, frame_range, cmds, *, motion_mode='animated', sample_frame=None, role='primary'):
    primary, shape = co.camera_nodes(primary, cmds)
    if role not in ('primary', 'derived'):
        raise ValueError('Invalid camera role')
    if role == 'primary' and cmds.objExists(primary + "." + co.OWNER_ATTR):
        raise ValueError("Primary cannot be a derived Review camera.")
    co._check_supported(cmds, shape)
    start, end = (int(value) for value in frame_range)
    if end < start:
        raise ValueError("Primary Camera frame range is invalid.")
    if motion_mode not in ('animated', 'static'):
        raise ValueError('Invalid camera motion mode: ' + motion_mode)
    motion = dict(mode=motion_mode, animation_required=motion_mode != 'static')
    if motion_mode == 'static':
        from .camera_portable import bake_primary
        sample_frame = start if sample_frame is None else int(sample_frame)
        motion['sample_frame'] = sample_frame
        # Freeze before native export too, so Maya / FBX / USD agree.
        primary, shape = bake_primary(dict(primary_path=primary, frame_range=[start, end],
            camera_motion=motion), cmds, camera_name='static_primary#')
        primary = cmds.ls(primary, long=True)[0]
    nodes = camera_native.dependencies(primary, cmds)
    return {
        "schema": SCHEMA,
        "role": role,
        "camera": primary.rsplit("|", 1)[-1].split(":")[-1],
        "primary_path": primary,
        "dependency_nodes": nodes,
        "frame_range": [start, end],
        "camera_motion": motion,
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


def restore_with_root(data, *, cmds, provenance="", frame_offset=0.0,
                      namespace=IMPORT_NAMESPACE, root_name=':smartPrimaryPublish'):
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
    if cmds.namespace(exists=namespace):
        raise ValueError("Primary Camera namespace is occupied: " + namespace)
    if cmds.objExists(root_name):
        raise ValueError("A Primary Camera Publish is already active in this scene.")
    imported = cmds.file(
        str(path), i=True, type="mayaAscii", namespace=namespace,
        mergeNamespacesOnClash=False, returnNewNodes=True, executeScriptNodes=False,
    )
    original = str(data.get("primary_path") or "")
    expected = (
        "|" + "|".join(namespace + ":" + part for part in original.split("|") if part)
        if original.startswith("|") else namespace + ":" + original
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
    root = cmds.group(empty=True, name=root_name)
    for node in roots:
        cmds.parent(node, root, relative=True)
    primary = cmds.ls(primary_uuid, long=True)[0]
    co._string_attr(cmds, primary, "smartCameraRole", "primary")
    co._string_attr(cmds, primary, "smartCameraPublishSource", str(provenance))
    return root, primary


def restore(data, *, cmds, provenance="", frame_offset=0.0):
    if data.get('role') == 'derived':
        return _restore_derived(data, cmds=cmds, provenance=provenance, frame_offset=frame_offset)
    root, _primary = restore_with_root(
        data, cmds=cmds, provenance=provenance, frame_offset=frame_offset
    )
    return root


def _restore_derived(data, *, cmds, provenance, frame_offset):
    """Sample the pinned native dependency graph without retaining another Primary."""
    import re
    from .camera_portable import bake_primary
    name = str((data.get('camera_settings') or {}).get('target') or data.get('target') or '')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        raise ValueError('Invalid derived Camera target: ' + name)
    if cmds.objExists(name):
        raise ValueError('Derived Camera already exists: ' + name)
    namespace = 'smartDerived_' + name
    root, camera = restore_with_root(data, cmds=cmds, provenance=provenance,
        frame_offset=frame_offset, namespace=namespace, root_name=':smartDerivedSource_' + name)
    try:
        baked, _shape = bake_primary(dict(data, primary_path=camera), cmds, camera_name=name)
    finally:
        cmds.delete(root)
        cmds.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
    co._string_attr(cmds, baked, 'smartCameraRole', 'derived')
    co._string_attr(cmds, baked, 'smartCameraPublishSource', str(provenance))
    return baked
