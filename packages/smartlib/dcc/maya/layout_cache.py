from __future__ import annotations

from pathlib import Path
from typing import Any


def export_layout_cache_for_cast(
    *,
    namespace: str,
    output_path: str | Path,
    frame_range: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Export a lightweight USD cache for a layout cast namespace.

    Returns a metadata dictionary. When called outside Maya or when export
    fails, the caller can keep a placeholder USD and record the error.
    """

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        return {
            "export_status": "skipped",
            "export_error": f"Maya is not available: {exc}",
            "source_nodes": [],
        }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    source_nodes = _layout_export_roots(cmds, namespace)
    if not source_nodes:
        return {
            "export_status": "skipped",
            "export_error": f"No rig_geo_grp set members were found for namespace: {namespace}",
            "source_nodes": [],
        }

    previous_selection = cmds.ls(selection=True, long=True) or []
    try:
        from smartlib.dcc.maya.plugins import ensure_required_plugins

        ensure_required_plugins(cmds)
        cmds.select(source_nodes, replace=True)
        error = _export_selected_usd(cmds, output, frame_range)
        if error:
            return {
                "export_status": "failed",
                "export_error": error,
                "source_nodes": source_nodes,
            }
        return {
            "export_status": "exported",
            "export_error": "",
            "source_nodes": source_nodes,
        }
    finally:
        try:
            if previous_selection:
                cmds.select(previous_selection, replace=True)
            else:
                cmds.select(clear=True)
        except Exception:
            pass


def _layout_export_roots(cmds: Any, namespace: str) -> list[str]:
    namespace = str(namespace or "").strip()
    if not namespace:
        return []
    set_name = f"{namespace}:rig_geo_grp"
    if not cmds.objExists(set_name):
        return []
    members = cmds.sets(set_name, query=True) or []
    transforms = _mesh_transforms_from_members(cmds, members)
    roots = {_top_namespace_parent(cmds, transform, namespace) for transform in transforms}
    return sorted(root for root in roots if root)


def _mesh_transforms_from_members(cmds: Any, members: list[str]) -> list[str]:
    mesh_transforms = []
    for member in members:
        if not cmds.objExists(member):
            continue
        nodes = [member]
        nodes.extend(cmds.listRelatives(member, allDescendents=True, fullPath=True) or [])
        for node in nodes:
            if not cmds.objExists(node):
                continue
            if cmds.nodeType(node) == "mesh":
                parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
                mesh_transforms.extend(parents)
                continue
            if cmds.nodeType(node) != "transform":
                continue
            shapes = cmds.listRelatives(node, shapes=True, fullPath=True, noIntermediate=True) or []
            if any(cmds.nodeType(shape) == "mesh" for shape in shapes):
                mesh_transforms.append(node)
    return sorted(set(mesh_transforms))


def _top_namespace_parent(cmds: Any, node: str, namespace: str) -> str:
    current = node
    while True:
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        if not parents:
            return current
        parent = parents[0]
        short_name = parent.rsplit("|", 1)[-1]
        if not short_name.startswith(f"{namespace}:"):
            return current
        current = parent


def _export_selected_usd(cmds: Any, output: Path, frame_range: tuple[int, int] | None) -> str:
    try:
        if hasattr(cmds, "mayaUSDExport"):
            kwargs = {
                "file": str(output),
                "selection": True,
                "mergeTransformAndShape": True,
            }
            if frame_range:
                kwargs["frameRange"] = frame_range
            cmds.mayaUSDExport(**kwargs)
            return ""
    except Exception as exc:
        maya_usd_error = str(exc)
    else:
        maya_usd_error = ""

    try:
        options = "exportUVs=1;exportSkels=none;exportSkin=none;exportBlendShapes=0;"
        if frame_range:
            options += f"frameRange={frame_range[0]} {frame_range[1]};"
        cmds.file(
            str(output),
            force=True,
            type="USD Export",
            exportSelected=True,
            preserveReferences=False,
            options=options,
        )
        return ""
    except Exception as exc:
        return str(exc) or maya_usd_error
