from __future__ import annotations

from pathlib import Path
from typing import Any


WORLD_CONTROL_CANDIDATES = (
    "world_ctl",
    "global_ctl",
    "root_ctl",
    "world_ctrl",
    "global_ctrl",
    "root_ctrl",
    "move_ctl",
    "main_ctl",
)


def collect_rig_metadata(
    *,
    asset_name: str,
    subset: str,
    source_workfile: str | Path,
    dependency_info: dict | None = None,
) -> dict[str, Any]:
    cmds = _maya_cmds()
    world_control = _first_existing_node(cmds, WORLD_CONTROL_CANDIDATES)
    controls = _control_nodes(cmds)
    joints = cmds.ls(type="joint", long=False) or []
    root_joint = _root_joint(cmds, joints)
    export_sets = _export_sets(cmds)
    metadata: dict[str, Any] = {
        "asset": asset_name,
        "publish_type": "rig",
        "subset": subset,
        "source_workfile": str(source_workfile).replace("\\", "/"),
        "rig_type": "unknown",
        "joint_count": len(joints),
        "control_count": len(controls),
        "root_joint": root_joint,
        "world_control": world_control,
        "placement": {
            "attach_target": world_control,
            "attach_mode": "parentConstraint",
            "fallback": "root_transform",
        },
        "supports": _supports_for_subset(subset),
        "export_sets": export_sets,
    }
    model_dependency = _model_dependency(dependency_info)
    if model_dependency:
        metadata["model_dependency"] = model_dependency
    if not world_control:
        metadata.setdefault("validation", {}).setdefault("warnings", []).append(
            "No world/global/root control was detected. Placement will fall back to root transform."
        )
    return metadata


def _control_nodes(cmds: Any) -> list[str]:
    controls = set()
    for node in cmds.ls(type="nurbsCurve", long=False) or []:
        parents = cmds.listRelatives(node, parent=True, fullPath=False) or []
        controls.update(parents)
    for pattern in ("*_ctl", "*_ctrl", "*CTL", "*CTRL"):
        controls.update(cmds.ls(pattern, type="transform", long=False) or [])
    return sorted(controls)


def _root_joint(cmds: Any, joints: list[str]) -> str:
    for joint in joints:
        parents = cmds.listRelatives(joint, parent=True, fullPath=False) or []
        if not any(parent in joints for parent in parents):
            return joint
    return joints[0] if joints else ""


def _export_sets(cmds: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for set_name in cmds.ls(type="objectSet", long=False) or []:
        lower = set_name.lower()
        if not any(token in lower for token in ("export", "geo", "skel", "rig")):
            continue
        members = cmds.sets(set_name, query=True) or []
        result[set_name] = sorted(str(member) for member in members)
    return result


def _first_existing_node(cmds: Any, names: tuple[str, ...]) -> str:
    for name in names:
        matches = cmds.ls(name, type="transform", long=False) or []
        if matches:
            return matches[0]
    for name in names:
        matches = cmds.ls(f"*:{name}", type="transform", long=False) or []
        if matches:
            return matches[0].split(":")[-1]
    return ""


def _supports_for_subset(subset: str) -> list[str]:
    clean = str(subset or "").strip()
    supports = [clean] if clean else []
    for item in ("layout", "anim"):
        if item not in supports:
            supports.append(item)
    return supports


def _model_dependency(dependency_info: dict | None) -> dict[str, Any]:
    if not dependency_info:
        return {}
    dependencies = dependency_info.get("dependencies") or {}
    references = dependencies.get("references") or []
    if references:
        return {
            "type": "model",
            "source": references[0],
        }
    return {}


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Rig metadata collection is available inside Maya.") from exc
    return cmds
