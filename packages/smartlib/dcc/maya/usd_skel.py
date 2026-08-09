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
    root_joints = export_data["root_joints"]
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
    # Export only skeleton hierarchies that actually deform publish geometry.
    # Selecting every skel_export_set member can leak guide/face/test skeletons
    # that have no binding relationship to cache_geo_set.
    skeleton_selection = _ordered_unique(root_joints)
    skin_selection = _ordered_unique([*root_joints, *geometry_members])
    root_prim = _usd_identifier(str(rig_metadata.get("asset") or "Asset"))
    _export_usd(
        cmds, outputs["skeleton_usd"], skeleton_selection,
        export_skin=False, root_prim=root_prim,
    )
    _export_usd(
        cmds, outputs["skin_usd"], skin_selection,
        export_skin=True, root_prim=root_prim,
    )
    _export_usd(
        cmds, outputs["rig_usd"], skin_selection,
        export_skin=True, root_prim=root_prim,
    )
    expected_skinned_mesh_count = len(_skinned_mesh_shapes(cmds, geometry_members))
    published_validation = _validate_published_usd(
        outputs["rig_usd"],
        expected_skinned_mesh_count=expected_skinned_mesh_count,
    )
    validation["published_usd"] = published_validation
    validation["status"] = "PASS" if published_validation["status"] == "PASS" else "ERROR"
    validation["issues"].extend(published_validation["issues"])
    _write_json(validation_path, validation)
    if published_validation["issues"]:
        raise RuntimeError(
            "Published USD Skel validation failed:\n- "
            + "\n- ".join(published_validation["issues"])
        )
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
    geometry_members = _set_members(cmds, geometry_set)
    skeleton_members = _set_members(cmds, skeleton_set)
    configured_root_joint = _resolve_root_joint(cmds, rig_metadata, contract)
    root_joints, unused_skeleton_roots = _resolve_bound_root_joints(
        cmds,
        skeleton_members=skeleton_members,
        geometry_members=geometry_members,
    )
    root_joint = (
        configured_root_joint
        if configured_root_joint in root_joints
        else (root_joints[0] if root_joints else configured_root_joint)
    )
    issues = _validate_export_contents(
        cmds,
        geometry_set=geometry_set,
        skeleton_set=skeleton_set,
        geometry_members=geometry_members,
        skeleton_members=skeleton_members,
        root_joint=root_joint,
    )
    if not root_joints:
        issues.append(
            "No bound skeleton roots were resolved from cache_geo_set. "
            "Only joints that influence publish geometry may be exported."
        )
    validation = {
        "schema": "smartpipeline.usd_skel_validation.v1",
        "status": "ERROR" if issues else "PASS",
        "geometry_set": geometry_set,
        "skeleton_set": skeleton_set,
        "root_joint": _leaf(root_joint),
        "root_joints": [_leaf(joint) for joint in root_joints],
        "unused_skeleton_roots": [_leaf(joint) for joint in unused_skeleton_roots],
        "bound_skeleton_root_count": len(root_joints),
        "unused_skeleton_root_count": len(unused_skeleton_roots),
        "geometry_member_count": len(geometry_members),
        "skeleton_member_count": len(skeleton_members),
        "issues": issues,
    }
    return validation, {
        "root_joint": root_joint,
        "root_joints": root_joints,
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


def _resolve_bound_root_joints(
    cmds: Any,
    *,
    skeleton_members: list[str],
    geometry_members: list[str],
) -> tuple[list[str], list[str]]:
    """Return only skeleton roots used by skinClusters on publish geometry."""

    all_joints = _joint_descendants(cmds, skeleton_members)
    set_roots: list[str] = []
    for joint in sorted(all_joints):
        parent = cmds.listRelatives(joint, parent=True, type="joint", fullPath=True) or []
        if not parent or parent[0] not in all_joints:
            set_roots.append(joint)

    used_roots: list[str] = []
    for shape in _skinned_mesh_shapes(cmds, geometry_members):
        history = cmds.listHistory(shape) or []
        skin_clusters = [node for node in history if cmds.nodeType(node) == "skinCluster"]
        for skin_cluster in skin_clusters:
            influences = cmds.skinCluster(skin_cluster, query=True, influence=True) or []
            for influence in influences:
                current = (cmds.ls(influence, long=True) or [influence])[0]
                if current not in all_joints:
                    continue
                while True:
                    parent = cmds.listRelatives(
                        current, parent=True, type="joint", fullPath=True
                    ) or []
                    if not parent or parent[0] not in all_joints:
                        break
                    current = parent[0]
                used_roots.append(current)

    used = _ordered_unique(used_roots)
    unused = [root for root in set_roots if root not in used]
    return used, unused


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
    unresolved_influences: list[str] = []
    for shape in mesh_shapes:
        # SkinClusters can sit above intermediate DAG shapes. Pruning DAG
        # history hides them on otherwise valid production rigs.
        history = cmds.listHistory(shape) or []
        skin_clusters = [node for node in history if cmds.nodeType(node) == "skinCluster"]
        if not skin_clusters:
            continue
        skinned.append(shape)
        influences = cmds.skinCluster(skin_clusters[0], query=True, influence=True) or []
        for influence in influences:
            influence_long = (cmds.ls(influence, long=True) or [influence])[0]
            if influence_long not in skeleton_joints:
                unresolved_influences.append(f"{_leaf(shape)} -> {_leaf(influence_long)}")
    if mesh_shapes and not skinned:
        issues.append(f"No skinCluster-bound meshes were found in {geometry_set}.")
    if unresolved_influences:
        preview = ", ".join(unresolved_influences[:8])
        suffix = (
            f" (+{len(unresolved_influences) - 8} more)"
            if len(unresolved_influences) > 8 else ""
        )
        issues.append(
            f"Skin influences outside {skeleton_set}: {preview}{suffix}. "
            "Add their joint roots to the skeleton export set."
        )
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


def _skinned_mesh_shapes(cmds: Any, members: Iterable[str]) -> list[str]:
    result: list[str] = []
    for shape in _mesh_shapes(cmds, members):
        history = cmds.listHistory(shape) or []
        if any(cmds.nodeType(node) == "skinCluster" for node in history):
            result.append(shape)
    return result


def _export_usd(
    cmds: Any,
    path: Path,
    selection: list[str],
    *,
    export_skin: bool,
    root_prim: str,
) -> None:
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
            # Geometry and independent skeleton roots must share one SkelRoot;
            # otherwise Maya USD exports visible Skeleton prims without binding
            # the meshes to them.
            "rootPrim": root_prim,
            "rootPrimType": "Xform",
        }
        try:
            cmds.mayaUSDExport(**kwargs)
        except (TypeError, RuntimeError) as exc:
            message = str(exc)
            unsupported_root_flag = isinstance(exc, TypeError) or any(
                token in message
                for token in (
                    "Invalid flag 'rootPrim'",
                    "Invalid flag 'rootPrimType'",
                    "rootPrim",
                    "rootPrimType",
                )
            )
            if not unsupported_root_flag:
                raise RuntimeError(f"Maya USD Skel export failed for {path.name}: {exc}") from exc

            # Some Maya 2024 maya-usd builds do not expose the rootPrim flags
            # even though newer command versions do. Retry with the compatible
            # option set; the post-export binding validation still blocks an
            # incomplete USD Skel package.
            kwargs.pop("rootPrim", None)
            kwargs.pop("rootPrimType", None)
            if path.exists():
                path.unlink()
            try:
                cmds.mayaUSDExport(**kwargs)
            except (TypeError, RuntimeError) as retry_exc:
                raise RuntimeError(
                    f"Maya USD Skel export failed for {path.name} "
                    f"after Maya 2024 compatibility retry: {retry_exc}"
                ) from retry_exc
    finally:
        if previous:
            cmds.select(previous, replace=True)
        else:
            cmds.select(clear=True)


def _validate_published_usd(
    path: Path,
    *,
    expected_skinned_mesh_count: int,
) -> dict[str, Any]:
    try:
        from pxr import Usd, UsdGeom, UsdSkel
    except ImportError as exc:
        raise RuntimeError("Maya USD Python bindings are required for publish validation.") from exc

    stage = Usd.Stage.Open(str(path).replace("\\", "/"))
    if not stage:
        raise RuntimeError(f"Could not open published USD for validation: {path}")

    skeletons: list[str] = []
    meshes: list[str] = []
    bound_meshes: list[str] = []
    weighted_meshes: list[str] = []
    missing: list[str] = []
    targets: set[str] = set()
    for prim in stage.Traverse():
        if prim.IsA(UsdSkel.Skeleton):
            skeletons.append(str(prim.GetPath()))
        if not prim.IsA(UsdGeom.Mesh):
            continue
        prim_path = str(prim.GetPath())
        meshes.append(prim_path)
        binding = UsdSkel.BindingAPI(prim)
        skeleton_targets = [str(item) for item in binding.GetSkeletonRel().GetTargets()]
        indices = binding.GetJointIndicesPrimvar()
        weights = binding.GetJointWeightsPrimvar()
        if skeleton_targets:
            bound_meshes.append(prim_path)
            targets.update(skeleton_targets)
        if indices and weights and indices.HasAuthoredValue() and weights.HasAuthoredValue():
            weighted_meshes.append(prim_path)
        if not skeleton_targets or prim_path not in weighted_meshes:
            missing.append(prim_path)

    issues: list[str] = []
    if not skeletons:
        issues.append("No UsdSkel Skeleton prims were exported.")
    if not meshes:
        issues.append("No Mesh prims were exported for skin binding.")
    if len(bound_meshes) < expected_skinned_mesh_count:
        issues.append(
            "USD skeleton relationships are incomplete: "
            f"expected at least {expected_skinned_mesh_count}, found {len(bound_meshes)}."
        )
    if len(weighted_meshes) < expected_skinned_mesh_count:
        issues.append(
            "USD joint indices/weights are incomplete: "
            f"expected at least {expected_skinned_mesh_count}, found {len(weighted_meshes)}."
        )
    return {
        "status": "ERROR" if issues else "PASS",
        "skeleton_count": len(skeletons),
        "mesh_count": len(meshes),
        "bound_mesh_count": len(bound_meshes),
        "weighted_mesh_count": len(weighted_meshes),
        "expected_skinned_mesh_count": expected_skinned_mesh_count,
        "unbound_meshes": missing,
        "skeletons": skeletons,
        "skeleton_targets": sorted(targets),
        "issues": issues,
    }


def _usd_identifier(value: str) -> str:
    clean = "".join(char if (char.isalnum() or char == "_") else "_" for char in value)
    if not clean:
        return "Asset"
    if clean[0].isdigit():
        clean = "_" + clean
    return clean


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
