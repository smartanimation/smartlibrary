from __future__ import annotations

from pathlib import Path
from typing import Iterable


def write_context_asset_snapshot(
    source_scene: str | Path,
    target_scene: str | Path,
    look_scenes: Iterable[str | Path] | None = None,
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

    previous_scene = cmds.file(query=True, sceneName=True) or ""
    previous_modified = bool(cmds.file(query=True, modified=True))
    if previous_modified and Path(previous_scene) != source:
        raise RuntimeError("Save or discard the current Maya scene before packing a Context asset snapshot.")

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        cmds.file(str(source), open=True, force=True)
        _import_all_references(cmds)
        _merge_imported_base_asset_branches(cmds)
        for look_scene in look_scenes or []:
            _apply_look_scene_materials(cmds, Path(look_scene))
        cmds.file(rename=str(target))
        cmds.file(save=True, type="mayaAscii")
    finally:
        if previous_scene and Path(previous_scene).exists():
            cmds.file(previous_scene, open=True, force=True)
        elif not previous_modified:
            cmds.file(new=True, force=True)
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
