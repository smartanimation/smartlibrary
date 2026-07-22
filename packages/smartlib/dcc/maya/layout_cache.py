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
    return _minimal_transform_roots(_geometry_roots_from_members(cmds, members))


def _geometry_roots_from_members(cmds: Any, members: list[str]) -> list[str]:
    roots = []
    for member in members:
        if not cmds.objExists(member):
            continue
        try:
            node_type = cmds.nodeType(member)
        except Exception:
            continue
        if node_type == "mesh":
            roots.extend(cmds.listRelatives(member, parent=True, fullPath=True) or [])
            continue
        if node_type != "transform":
            continue
        long_member = (cmds.ls(member, long=True) or [member])[0]
        if _has_mesh_geometry(cmds, long_member):
            roots.append(long_member)
    return sorted(set(roots))


def _minimal_transform_roots(nodes: list[str]) -> list[str]:
    ordered = sorted(set(nodes), key=lambda value: value.count("|"))
    result = []
    for node in ordered:
        if any(node == parent or node.startswith(f"{parent}|") for parent in result):
            continue
        result.append(node)
    return result


def _has_mesh_geometry(cmds: Any, transform: str) -> bool:
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, noIntermediate=True) or []
    if any(_is_exportable_mesh(cmds, shape) for shape in shapes):
        return True
    descendants = cmds.listRelatives(transform, allDescendents=True, fullPath=True) or []
    return any(_is_exportable_mesh(cmds, node) for node in descendants)


def _is_exportable_mesh(cmds: Any, node: str) -> bool:
    if not cmds.objExists(node):
        return False
    try:
        if cmds.nodeType(node) != "mesh":
            return False
        if cmds.getAttr(f"{node}.intermediateObject"):
            return False
    except Exception:
        return False
    return True


def _export_selected_usd(cmds: Any, output: Path, frame_range: tuple[int, int] | None) -> str:
    try:
        if hasattr(cmds, "mayaUSDExport"):
            kwargs = {
                "file": str(output),
                "selection": True,
                "mergeTransformAndShape": True,
                "exportSkels": "none",
                "exportSkin": "none",
                "exportBlendShapes": False,
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
        options = (
            "exportUVs=1;"
            "exportColorSets=1;"
            "exportSkels=none;"
            "exportSkin=none;"
            "exportBlendShapes=0;"
            "shadingMode=none;"
            "exportMaterials=0;"
            "exportAssignedMaterials=0;"
        )
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
