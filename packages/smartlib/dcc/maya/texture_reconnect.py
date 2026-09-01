from __future__ import annotations

from pathlib import Path

from smartlib.core.path_resolver import AssetIdentity
from smartlib.core.texture_reconnect import (
    TextureReconnectItem,
    TextureReference,
    inspect_texture_references,
    plan_texture_reconnect,
    texture_root_from_package,
)


def current_file_node_references(*, cmds_module=None) -> list[TextureReference]:
    if cmds_module is None:
        from maya import cmds as cmds_module
    references = []
    for node in cmds_module.ls(type="file") or []:
        try:
            value = str(cmds_module.getAttr(f"{node}.fileTextureName") or "").strip()
        except RuntimeError:
            continue
        if value:
            references.append(TextureReference(str(node), value))
    return references


def inspect_current_file_nodes(*, cmds_module=None) -> list[TextureReconnectItem]:
    return inspect_texture_references(current_file_node_references(cmds_module=cmds_module))


def ingested_package_candidates(project_config, scene_path: str | Path) -> list[Path]:
    """List resolver-owned vendor/client packages for the scene's Asset."""
    parts = [part for part in str(scene_path).replace("\\", "/").split("/") if part]
    lowered = [part.casefold() for part in parts]
    try:
        start = lowered.index("assets") + 1
    except ValueError:
        return []
    if len(parts) < start + 4:
        return []
    identity = AssetIdentity(*parts[start : start + 4])
    from smartlib.core.path_resolver import ProjectPaths
    paths = ProjectPaths(project_config.project_root, project_config.templates, project_config.project_name)
    candidates = []
    for subset in ("vendor", "client"):
        root = paths.asset_data_dir(identity, "assembly", subset)
        for version in sorted(root.glob("v*"), reverse=True) if root.is_dir() else ():
            if version.is_dir() and texture_root_from_package(version) is not None:
                candidates.append(version)
    return candidates


def apply_reconnect_plan(items: list[TextureReconnectItem], *, cmds_module=None) -> int:
    if cmds_module is None:
        from maya import cmds as cmds_module
    applied = 0
    for item in items:
        if item.status != "ready" or item.resolved_path is None:
            continue
        cmds_module.setAttr(f"{item.node}.fileTextureName", item.resolved_path.as_posix(), type="string")
        applied += 1
    return applied


def reconnect_file_nodes(
    package_root: str | Path,
    *,
    cmds_module=None,
    apply: bool = True,
) -> list[TextureReconnectItem]:
    """Reconnect loaded Maya file nodes to an ingested asset package.

    ``package_root`` must be obtained from ``ProjectPaths``.  Dry-run with
    ``apply=False`` to inspect ambiguous or missing matches before changing Maya.
    """
    if cmds_module is None:
        from maya import cmds as cmds_module

    texture_root = texture_root_from_package(package_root)
    if texture_root is None:
        raise FileNotFoundError(f"Package has no declared texture root: {package_root}")
    references = current_file_node_references(cmds_module=cmds_module)
    plan = plan_texture_reconnect(references, texture_root)
    if apply:
        apply_reconnect_plan(plan, cmds_module=cmds_module)
    return plan
