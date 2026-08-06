from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def publish_usd_skel_package(
    version_dir: str | Path,
    *,
    rig_metadata: dict[str, Any],
    contract: dict[str, str],
    overwrite: bool = False,
) -> dict[str, Path]:
    """Publish a Maya rig as a validated USD Skel package."""

    cmds = _maya_cmds()
    version_path = Path(version_dir)
    version_path.mkdir(parents=True, exist_ok=True)
    validation, export_data = validate_usd_skel_scene(rig_metadata=rig_metadata, contract=contract)
    validation_path = version_path / "validation.json"
    _write_json(validation_path, validation)
    issues = validation.get("issues") or []
    if issues:
        raise RuntimeError("USD Skel validation failed:\n- " + "\n- ".join(issues))

    root_joint = export_data["root_joint"]
    geometry_members = export_data["geometry_members"]
    skeleton_members = export_data["skeleton_members"]

    outputs = {
        "rig_usd": version_path / "rig.usd",
        "skeleton_usd": version_path / "skeleton.usd",
        "skin_usd": version_path / "skin.usd",
        "validation": validation_path,
    }
    existing = [path for key, path in outputs.items() if key != "validation" and path.exists()]
    if existing and not overwrite:
        raise FileExistsError("USD Skel output already exists: " + ", ".join(path.name for path in existing))

    _ensure_maya_usd_plugin(cmds)
    skeleton_selection = _ordered_unique([root_joint, *skeleton_members])
    skin_selection = _ordered_unique([root_joint, *skeleton_members, *geometry_members])
    _export_usd(cmds, outputs["skeleton_usd"], skeleton_selection, export_skin=False)
    _export_usd(cmds, outputs["skin_usd"], skin_selection, export_skin=True)
    _export_usd(cmds, outputs["rig_usd"], skin_selection, export_skin=True)
    return outputs


def validate_usd_skel_scene(
    *,
    rig_metadata: dict[str, Any],
    contract: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the current Maya scene without writing publish files."""

    cmds = _maya_cmds()
    geometry_set = _resolve_unique_set(cmds, contract.get("geometry_set", "cache_geo_set"))
    skeleton_set = _resolve_unique_set(cmds, contract.get("skeleton_set", "skel_export_set"))
    root_joint = _resolve_root_joint(cmds, rig_metadata, contract)
    geometry_members = _set_members(cmds, geometry_set)
    skeleton_members = _set_members(cmds, skeleton_set)
    issues = _validate_export_contents(
        cmds,
        geometry_set=geometry_set,
        skeleton_set=skeleton_set,
        geometry_members=geometry_members,
        skeleton_members=skeleton_members,
        root_joint=root_joint,
    )
    validation = {
        "schema": "smartpipeline.usd_skel_validation.v1",
        "status": "ERROR" if issues else "PASS",
        "geometry_set": geometry_set,
        "skeleton_set": skeleton_set,
        "root_joint": _leaf(root_joint),
        "geometry_member_count": len(geometry_members),
        "skeleton_member_count": len(skeleton_members),
        "issues": issues,
    }
    return validation, {
        "root_joint": root_joint,
        "geometry_members": geometry_members,
        "skeleton_members": skeleton_members,
    }


def _resolve_unique_set(cmds: Any, configured_name: str) -> str:
    name = str(configured_name or "").strip()
    matches = cmds.ls(name, type="objectSet", long=False) or []
    matches.extend(cmds.ls(f"*:{name}", type="objectSet", long=False) or [])
    matches = _ordered_unique(matches)
    if not matches:
        raise RuntimeError(f"Required USD Skel set was not found: {name}")
    if len(matches) > 1:
        raise RuntimeError(f"More than one USD Skel set matches {name}: {', '.join(matches)}")
    return matches[0]


def _resolve_root_joint(cmds: Any, metadata: dict[str, Any], contract: dict[str, str]) -> str:
    key = str(contract.get("root_joint_metadata_key") or "root_joint")
    configured = str(metadata.get(key) or "").strip()
    if not configured:
        raise RuntimeError(f"Rig metadata does not define {key}.")
    matches = cmds.ls(configured, type="joint", long=True) or []
    if not matches:
        matches = cmds.ls(f"*:{configured}", type="joint", long=True) or []
    if len(matches) != 1:
        detail = "not found" if not matches else f"ambiguous: {', '.join(matches)}"
        raise RuntimeError(f"Rig metadata root_joint '{configured}' is {detail}.")
    return matches[0]


def _set_members(cmds: Any, set_name: str) -> list[str]:
    members = cmds.sets(set_name, query=True) or []
    expanded: list[str] = []
    for member in members:
        if cmds.nodeType(member) == "objectSet":
            expanded.extend(_set_members(cmds, member))
        else:
            expanded.extend(cmds.ls(member, long=True) or [member])
    return _ordered_unique(expanded)


def _validate_export_contents(
    cmds: Any,
    *,
    geometry_set: str,
    skeleton_set: str,
    geometry_members: list[str],
    skeleton_members: list[str],
    root_joint: str,
) -> list[str]:
    issues: list[str] = []
    if not geometry_members:
        issues.append(f"Geometry set is empty: {geometry_set}")
    if not skeleton_members:
        issues.append(f"Skeleton set is empty: {skeleton_set}")
    skeleton_joints = _joint_descendants(cmds, skeleton_members)
    root_long = (cmds.ls(root_joint, long=True) or [root_joint])[0]
    if root_long not in skeleton_joints:
        issues.append(
            f"Metadata root_joint '{_leaf(root_joint)}' is not included in {skeleton_set}. "
            "Add the root joint (or its hierarchy) to the skeleton export set."
        )
    mesh_shapes = _mesh_shapes(cmds, geometry_members)
    if not mesh_shapes:
        issues.append(f"No mesh shapes were found in {geometry_set}.")
    skinned = []
    for shape in mesh_shapes:
        history = cmds.listHistory(shape, pruneDagObjects=True) or []
        if any(cmds.nodeType(node) == "skinCluster" for node in history):
            skinned.append(shape)
    if mesh_shapes and not skinned:
        issues.append(f"No skinCluster-bound meshes were found in {geometry_set}.")
    return issues


def _joint_descendants(cmds: Any, members: Iterable[str]) -> set[str]:
    joints: set[str] = set()
    for member in members:
        if cmds.nodeType(member) == "joint":
            joints.update(cmds.ls(member, long=True) or [member])
        joints.update(cmds.listRelatives(member, allDescendents=True, type="joint", fullPath=True) or [])
    return joints


def _mesh_shapes(cmds: Any, members: Iterable[str]) -> list[str]:
    shapes: list[str] = []
    for member in members:
        if cmds.nodeType(member) == "mesh":
            shapes.extend(cmds.ls(member, long=True) or [member])
        shapes.extend(cmds.listRelatives(member, allDescendents=True, type="mesh", fullPath=True) or [])
        shapes.extend(cmds.listRelatives(member, shapes=True, type="mesh", fullPath=True) or [])
    return _ordered_unique(shapes)


def _export_usd(cmds: Any, path: Path, selection: list[str], *, export_skin: bool) -> None:
    previous = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(selection, replace=True, noExpand=True)
        kwargs = {
            "file": str(path).replace("\\", "/"),
            "selection": True,
            "exportSkels": "auto",
            "exportSkin": "auto" if export_skin else "none",
            "exportBlendShapes": False,
            "exportInstances": True,
            "mergeTransformAndShape": True,
            "stripNamespaces": False,
        }
        try:
            cmds.mayaUSDExport(**kwargs)
        except (TypeError, RuntimeError) as exc:
            raise RuntimeError(f"Maya USD Skel export failed for {path.name}: {exc}") from exc
    finally:
        if previous:
            cmds.select(previous, replace=True)
        else:
            cmds.select(clear=True)


def _ensure_maya_usd_plugin(cmds: Any) -> None:
    if not cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
        cmds.loadPlugin("mayaUsdPlugin")


def _ordered_unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value)
        if clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _leaf(node: str) -> str:
    return str(node).split("|")[-1].split(":")[-1]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("USD Skel publishing is available inside Maya.") from exc
    return cmds
