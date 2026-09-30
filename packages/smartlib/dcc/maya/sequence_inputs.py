"""Apply the resolved Dependencies / Inputs snapshot to a staged sequence."""
from pathlib import Path


def camera_input_paths(inputs):
    """None selects the rig workflow; a mapping selects direct FBX staging."""
    group = next((row for row in inputs if row.key == "virtual_camera" and row.enabled), None)
    if group is None:
        return None
    return {row.key: row.path for row in group.children if row.state == "READY"}


def import_virtual_camera(cmds, source, shot):
    from .shot_builder import _clean_namespace, _import_file

    path = Path(source)
    if not path.is_file() or path.suffix.lower() != ".fbx":
        raise RuntimeError(f"{shot}: Virtual Camera FBX is missing: {path}")
    namespace = _clean_namespace(path.stem)
    base = namespace
    suffix = 1
    while cmds.namespace(exists=namespace):
        namespace = f"{base}_{suffix}"
        suffix += 1
    before = set(cmds.ls(type="camera", long=True) or [])
    _import_file(cmds, path, namespace)
    cameras = sorted(set(cmds.ls(type="camera", long=True) or []) - before)
    if len(cameras) != 1:
        raise RuntimeError(f"{shot}: expected one camera in {path}, found {len(cameras)}")
    transforms = cmds.listRelatives(cameras[0], parent=True, fullPath=True) or []
    if not transforms:
        raise RuntimeError(f"{shot}: imported camera has no transform")
    return transforms[0]


def apply_sequence_inputs(inputs, *, shot_names=(), cmds=None, include_camera=True,
                          frame_range=None, require_retarget=False):
    if cmds is None:
        import maya.cmds as cmds
    from .shot_builder import _clean_namespace, _import_file

    selected = set(shot_names)
    imported = []
    for group in inputs:
        if not group.enabled or group.key not in {"mocap", "virtual_camera"}:
            continue
        if group.key == "virtual_camera" and not include_camera:
            continue
        for row in group.children:
            if group.key == "virtual_camera" and selected and row.key not in selected:
                continue
            if row.state != "READY":
                if group.required:
                    raise RuntimeError(f"Unresolved sequence input: {group.label} / {row.label}")
                continue
            path = Path(row.path)
            if not path.is_file() or path.suffix.lower() != ".fbx":
                raise RuntimeError(f"Sequence FBX is missing: {path}")
            if group.key == "mocap" and getattr(row, "retarget", None):
                from .mcr_to_anim import bake_received_mcr
                binding = row.retarget
                if not frame_range:
                    raise RuntimeError("Retarget requires a sequence frame range")
                profile = dict(binding["profile"], mocap_fbx=str(path), frame_range=list(frame_range))
                bake_received_mcr(profile, cmds=cmds, target_namespace=_clean_namespace(binding["namespace"]))
                imported.extend([str(path), binding["profile_path"], binding["rig"]])
                continue
            if group.key == "mocap" and require_retarget:
                raise RuntimeError(f"{row.key}: verified Retarget Publish is required")
            namespace = _clean_namespace(f"input_{group.key}_{row.key}")
            cameras_before = set(cmds.ls(type="camera", long=True) or [])
            _import_file(cmds, path, namespace)
            if group.key == "virtual_camera":
                cameras = sorted(set(cmds.ls(type="camera", long=True) or []) - cameras_before)
                if len(cameras) != 1:
                    raise RuntimeError(f"{row.key}: expected one camera in {path}, found {len(cameras)}")
                transforms = cmds.listRelatives(cameras[0], parent=True, fullPath=True) or []
                if not transforms:
                    raise RuntimeError(f"{row.key}: imported camera has no transform")
                shot_node = f"{row.key}_shot"
                if not cmds.objExists(shot_node):
                    raise RuntimeError(f"Camera Sequencer shot is missing: {shot_node}")
                cmds.shot(shot_node, edit=True, currentCamera=transforms[0])
            imported.append(str(path))
    return imported
