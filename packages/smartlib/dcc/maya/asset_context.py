from __future__ import annotations

from pathlib import Path


def write_context_asset_snapshot(source_scene: str | Path, target_scene: str | Path) -> Path:
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
