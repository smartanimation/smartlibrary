from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

from smartlib.core.metadata import write_json


def write_context_asset_snapshot(
    source_scene: str | Path,
    target_scene: str | Path,
    look_scenes: Iterable[str | Path] | None = None,
    restore_previous: bool = True,
) -> Path:
    """Write a Maya asset context scene with nested references imported."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Maya asset context snapshots are available inside Maya.") from exc

    source = Path(source_scene)
    target = Path(target_scene)
    if not source.exists():
        raise FileNotFoundError(f"Context source scene was not found: {source}")
    resolved_look_scenes = [Path(path) for path in (look_scenes or []) if Path(path).is_file()]

    # Without an additional look layer, the selected rig/model publish is
    # already the immutable snapshot we need. Preserve it byte-for-byte so
    # unavailable optional Maya plug-ins cannot break an open/resave cycle.
    if not resolved_look_scenes:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    previous_scene = cmds.file(query=True, sceneName=True) or ""
    previous_modified = bool(cmds.file(query=True, modified=True))
    if previous_modified and Path(previous_scene) != source:
        raise RuntimeError("Save or discard the current Maya scene before packing a Context asset snapshot.")

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = f"open source scene: {source}"
    try:
        cmds.file(str(source), open=True, force=True)
        stage = "import source references"
        _import_all_references(cmds)
        stage = "merge imported asset branches"
        _merge_imported_base_asset_branches(cmds)
        for look_scene in resolved_look_scenes:
            stage = f"apply look scene: {look_scene}"
            _apply_look_scene_materials(cmds, Path(look_scene))
        stage = f"save context snapshot: {target}"
        cmds.file(rename=str(target))
        scene_type = "mayaBinary" if target.suffix.lower() == ".mb" else "mayaAscii"
        cmds.file(save=True, type=scene_type)
    except Exception as exc:
        raise RuntimeError(f"Context snapshot failed during {stage}.\n{exc}") from exc
    finally:
        if restore_previous:
            try:
                if previous_scene and Path(previous_scene).exists():
                    cmds.file(previous_scene, open=True, force=True)
                elif not previous_modified:
                    cmds.file(new=True, force=True)
            except Exception as exc:
                raise RuntimeError(
                    f"Context snapshot was written, but Maya could not restore the previous scene:\n"
                    f"{previous_scene or '<untitled>'}\n{exc}"
                ) from exc
    return target


def open_context_asset_assembly(
    scene_path: str | Path,
    asset_name: str,
    template_scene: str | Path | None = None,
) -> Path:
    """Open a verification scene and reference a Context assembly asset."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Maya Context assembly preview is available inside Maya.") from exc

    scene = Path(scene_path)
    template = Path(template_scene) if template_scene else None
    if not scene.exists():
        raise FileNotFoundError(f"Context assembly scene was not found: {scene}")
    if template and template.exists():
        cmds.file(str(template), open=True, force=True)
    else:
        cmds.file(new=True, force=True)
    cmds.file(
        str(scene),
        reference=True,
        namespace=asset_name,
        mergeNamespacesOnClash=False,
    )
    return scene


def write_context_asset_usd_snapshot(
    source_scene: str | Path,
    target_usd: str | Path,
    *,
    asset_name: str,
    contract: dict[str, str],
) -> Path:
    """Export a Context snapshot as one validated, skinned USD asset."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Maya Context USD snapshots are available inside Maya.") from exc

    from smartlib.dcc.maya.rig_metadata import collect_rig_metadata
    from smartlib.dcc.maya.usd_skel import (
        _ensure_maya_usd_plugin,
        _export_usd,
        _skinned_mesh_shapes,
        _validate_published_usd,
        validate_usd_skel_scene,
    )

    source = Path(source_scene)
    target = Path(target_usd)
    payload = target.with_name("payload.usd")
    validation_path = target.with_name("validation.json")
    if not source.is_file():
        raise FileNotFoundError(f"Context source scene was not found: {source}")

    previous_scene = cmds.file(query=True, sceneName=True) or ""
    previous_modified = bool(cmds.file(query=True, modified=True))
    if previous_modified and Path(previous_scene) != source:
        raise RuntimeError("Save or discard the current Maya scene before packing a Context asset USD.")

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = f"open context snapshot: {source}"
    try:
        cmds.file(str(source), open=True, force=True)
        stage = "load Maya USD plug-in"
        _ensure_maya_usd_plugin(cmds)
        stage = "collect rig metadata"
        rig_metadata = collect_rig_metadata(
            asset_name=asset_name,
            subset="context",
            source_workfile=source,
        )
        stage = "validate skin and skeleton contract"
        validation, export_data = validate_usd_skel_scene(
            rig_metadata=rig_metadata,
            contract=contract,
        )
        issues = list(validation.get("issues") or [])
        if issues:
            write_json(validation_path, validation)
            raise RuntimeError("Context USD Skel validation failed:\n- " + "\n- ".join(issues))

        geometry_members = list(export_data["geometry_members"])
        root_joints = list(export_data["root_joints"])
        expected_mesh_count = len(_skinned_mesh_shapes(cmds, geometry_members))
        stage = "prepare one USD asset root"
        export_root = _prepare_usd_export_root(
            cmds,
            asset_name=asset_name,
            geometry_members=geometry_members,
            root_joints=root_joints,
        )
        stage = f"export skinned payload: {payload}"
        _export_usd(
            cmds,
            payload,
            [export_root],
            export_skin=True,
            root_prim=_usd_identifier(asset_name),
        )
        stage = "validate exported payload"
        published = _validate_published_usd(
            payload,
            expected_skinned_mesh_count=expected_mesh_count,
        )
        validation["published_usd"] = published
        validation["status"] = published.get("status", "ERROR")
        validation["issues"] = list(published.get("issues") or [])
        write_json(validation_path, validation)
        if validation["issues"]:
            raise RuntimeError(
                "Context payload validation failed:\n- " + "\n- ".join(validation["issues"])
            )
        stage = "write asset.usda entry layer"
        _write_asset_entry_layer(
            target,
            asset_name=asset_name,
            payload_name=payload.name,
            payload_root=f"/{_leaf_name(export_root)}",
        )
    except Exception as exc:
        raise RuntimeError(f"Context USD snapshot failed during {stage}.\n{exc}") from exc
    finally:
        try:
            if previous_scene and Path(previous_scene).is_file():
                cmds.file(previous_scene, open=True, force=True)
            elif not previous_modified:
                cmds.file(new=True, force=True)
        except Exception:
            pass
    return target


def write_context_static_usd_snapshot(
    source_scene: str | Path,
    target_usd: str | Path,
    *,
    asset_name: str,
) -> Path:
    """Export a background/prop Context as a static assembly USD package."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Maya static Context USD snapshots are available inside Maya.") from exc

    from smartlib.dcc.maya.usd_skel import _ensure_maya_usd_plugin

    source = Path(source_scene)
    target = Path(target_usd)
    payload = target.with_name("payload.usd")
    if not source.is_file():
        raise FileNotFoundError(f"Context source scene was not found: {source}")

    previous_scene = cmds.file(query=True, sceneName=True) or ""
    previous_modified = bool(cmds.file(query=True, modified=True))
    if previous_modified and Path(previous_scene) != source:
        raise RuntimeError("Save or discard the current Maya scene before packing a static Context USD.")

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = f"open context snapshot: {source}"
    try:
        cmds.file(str(source), open=True, force=True)
        stage = "load Maya USD plug-in"
        _ensure_maya_usd_plugin(cmds)
        roots = [
            node for node in (cmds.ls(assemblies=True, type="transform", long=True) or [])
            if _leaf_name(node).lower() not in {"persp", "top", "front", "side"}
        ]
        if not roots or not _mesh_shapes(cmds):
            raise RuntimeError("Static Context contains no exportable background geometry.")
        stage = "prepare static assembly root"
        identifier = _usd_identifier(asset_name)
        export_root = cmds.group(empty=True, name=f"__SMART_STATIC_{identifier}_ROOT__")
        for root in roots:
            if cmds.objExists(root) and root != export_root:
                cmds.parent(root, export_root, absolute=True)
        export_root = (cmds.rename(export_root, identifier) or identifier)
        stage = f"export static payload: {payload}"
        previous_selection = cmds.ls(selection=True, long=True) or []
        try:
            cmds.select(export_root, replace=True, noExpand=True)
            cmds.mayaUSDExport(
                file=str(payload).replace("\\", "/"),
                selection=True,
                exportSkels="none",
                exportSkin="none",
                exportBlendShapes=False,
                exportInstances=True,
                mergeTransformAndShape=True,
                stripNamespaces=False,
            )
        finally:
            cmds.select(previous_selection, replace=True) if previous_selection else cmds.select(clear=True)
        if not payload.is_file():
            raise RuntimeError(f"Static Context USD export did not create a file: {payload}")
        stage = "write static asset.usda entry layer"
        _write_asset_entry_layer(
            target,
            asset_name=asset_name,
            payload_name=payload.name,
            payload_root=f"/{identifier}",
            root_type="Xform",
        )
    except Exception as exc:
        raise RuntimeError(f"Static Context USD snapshot failed during {stage}.\n{exc}") from exc
    finally:
        try:
            if previous_scene and Path(previous_scene).is_file():
                cmds.file(previous_scene, open=True, force=True)
            elif not previous_modified:
                cmds.file(new=True, force=True)
        except Exception:
            pass
    return target


def _prepare_usd_export_root(
    cmds,
    *,
    asset_name: str,
    geometry_members: list[str],
    root_joints: list[str],
) -> str:
    root_name = f"__SMART_{_usd_identifier(asset_name)}_ROOT__"
    if cmds.objExists(root_name):
        cmds.delete(root_name)
    export_root = cmds.group(empty=True, name=root_name)
    geo_root = cmds.group(empty=True, name="geo", parent=export_root)

    geometry_roots = _minimal_dag_roots(cmds, geometry_members)
    for node in geometry_roots:
        if cmds.objExists(node):
            cmds.parent(node, geo_root, absolute=True)
    for joint in _minimal_dag_roots(cmds, root_joints):
        if cmds.objExists(joint):
            cmds.parent(joint, export_root, absolute=True)
    return (cmds.ls(export_root, long=True) or [export_root])[0]


def _minimal_dag_roots(cmds, nodes: Iterable[str]) -> list[str]:
    resolved = []
    for node in nodes:
        matches = cmds.ls(node, long=True) or []
        if matches:
            resolved.append(matches[0])
    selected = set(resolved)
    result = []
    for node in resolved:
        parents = cmds.listRelatives(node, allParents=True, fullPath=True) or []
        if not any(parent in selected for parent in parents):
            result.append(node)
    return result


def _write_asset_entry_layer(
    target: Path,
    *,
    asset_name: str,
    payload_name: str,
    payload_root: str,
    root_type: str = "SkelRoot",
) -> Path:
    identifier = _usd_identifier(asset_name)
    target.write_text(
        "\n".join(
            [
                "#usda 1.0",
                "(",
                f'    defaultPrim = "{identifier}"',
                ")",
                "",
                f'def {root_type} "{identifier}" (',
                f'    references = @{payload_name}@<{payload_root}>',
                ")",
                "{",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return target


def _usd_identifier(value: str) -> str:
    clean = "".join(char if char.isalnum() or char == "_" else "_" for char in str(value))
    if not clean:
        return "Asset"
    return clean if not clean[0].isdigit() else f"_{clean}"


def _import_all_references(cmds) -> None:
    for _iteration in range(100):
        references = cmds.file(query=True, reference=True) or []
        if not references:
            return
        progressed = False
        for reference in references:
            try:
                cmds.file(reference, importReference=True)
                progressed = True
            except Exception:
                continue
        if not progressed:
            break
    remaining = cmds.file(query=True, reference=True) or []
    if remaining:
        raise RuntimeError("Could not import all context asset references: " + ", ".join(remaining))


def _apply_look_scene_materials(cmds, look_scene: Path) -> None:
    if not look_scene.exists():
        raise FileNotFoundError(f"Context look scene was not found: {look_scene}")

    target_shapes = _mesh_shapes_by_leaf(cmds)
    assemblies_before = set(cmds.ls(assemblies=True, long=True) or [])
    shapes_before = set(_mesh_shapes(cmds))
    cmds.file(
        str(look_scene),
        i=True,
        namespace="__contextLook",
        mergeNamespacesOnClash=False,
        preserveReferences=True,
    )
    _import_all_references(cmds)

    imported_shapes = [
        shape
        for shape in _mesh_shapes(cmds)
        if shape not in shapes_before and cmds.objExists(shape)
    ]
    applied = 0
    for source_shape in imported_shapes:
        target_shape = _matching_mesh_shape(target_shapes, source_shape)
        if not target_shape:
            continue
        for shading_engine in _shape_shading_engines(cmds, source_shape):
            try:
                cmds.sets(target_shape, edit=True, forceElement=shading_engine)
                applied += 1
            except RuntimeError:
                continue

    _delete_imported_assemblies(cmds, assemblies_before)
    if imported_shapes and not applied:
        raise RuntimeError(
            f"Look scene was imported but no matching mesh material assignments were applied: {look_scene}"
        )


def _mesh_shapes_by_leaf(cmds) -> dict[str, list[str]]:
    shapes: dict[str, list[str]] = {}
    for shape in _mesh_shapes(cmds):
        shapes.setdefault(_mesh_leaf(shape), []).append(shape)
    return shapes


def _mesh_shapes(cmds) -> list[str]:
    shapes = []
    for shape in cmds.ls(type="mesh", long=True) or []:
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        shapes.append(shape)
    return shapes


def _matching_mesh_shape(target_shapes: dict[str, list[str]], source_shape: str) -> str | None:
    matches = target_shapes.get(_mesh_leaf(source_shape)) or []
    return matches[0] if len(matches) == 1 else None


def _shape_shading_engines(cmds, shape: str) -> list[str]:
    return [
        node
        for node in cmds.listConnections(shape, type="shadingEngine") or []
        if node not in {"initialShadingGroup", "initialParticleSE"}
    ]


def _delete_imported_assemblies(cmds, assemblies_before: set[str]) -> None:
    for assembly in cmds.ls(assemblies=True, long=True) or []:
        if assembly not in assemblies_before and cmds.objExists(assembly):
            try:
                cmds.delete(assembly)
            except RuntimeError:
                continue


def _mesh_leaf(shape: str) -> str:
    parent = str(shape).rsplit("|", 1)[0] if "|" in str(shape) else str(shape)
    return _leaf_name(parent)


def _merge_imported_base_asset_branches(cmds) -> None:
    roots = cmds.ls(assemblies=True, type="transform", long=True) or []
    by_leaf = {}
    for root in roots:
        by_leaf.setdefault(_leaf_name(root), []).append(root)
    for nodes in by_leaf.values():
        target = next((node for node in nodes if ":" not in _short_name(node)), None)
        if not target:
            continue
        for source in nodes:
            if source == target or not cmds.objExists(source):
                continue
            if ":" not in _short_name(source):
                continue
            _move_safe_base_branches(cmds, source, target)


def _move_safe_base_branches(cmds, source: str, target: str) -> None:
    for branch in cmds.listRelatives(source, children=True, type="transform", fullPath=True) or []:
        branch_leaf = _leaf_name(branch)
        if branch_leaf not in {"geo", "groom", "look"}:
            # Model publishes can carry empty top-level placeholders such as
            # rig. Drop only empty branches so imported rig content is never
            # merged or removed by the context snapshot builder.
            if not _transform_children(cmds, branch) and not _shape_children(cmds, branch):
                cmds.delete(branch)
            continue
        existing = _child_by_leaf(cmds, target, branch_leaf)
        if existing:
            _merge_safe_branch_children(cmds, branch, existing)
            if cmds.objExists(branch) and not _transform_children(cmds, branch):
                cmds.delete(branch)
            continue
        moved = (cmds.parent(branch, target) or [branch])[0]
        _strip_leaf_namespace(cmds, moved)
    if cmds.objExists(source) and not _transform_children(cmds, source):
        cmds.delete(source)


def _merge_safe_branch_children(cmds, source: str, target: str) -> None:
    for child in _transform_children(cmds, source):
        existing = _child_by_leaf(cmds, target, _leaf_name(child))
        if existing:
            continue
        moved = (cmds.parent(child, target) or [child])[0]
        _strip_leaf_namespace(cmds, moved)


def _strip_leaf_namespace(cmds, node: str) -> str:
    short_name = _short_name(node)
    if ":" not in short_name:
        return node
    try:
        return cmds.rename(node, _leaf_name(node))
    except RuntimeError:
        return node


def _child_by_leaf(cmds, parent: str, leaf: str) -> str | None:
    for child in _transform_children(cmds, parent):
        if _leaf_name(child) == leaf:
            return child
    return None


def _transform_children(cmds, parent: str) -> list[str]:
    return cmds.listRelatives(parent, children=True, type="transform", fullPath=True) or []


def _shape_children(cmds, parent: str) -> list[str]:
    return cmds.listRelatives(parent, children=True, shapes=True, fullPath=True) or []


def _short_name(node: str) -> str:
    return str(node).rsplit("|", 1)[-1]


def _leaf_name(node: str) -> str:
    return _short_name(node).rsplit(":", 1)[-1]
