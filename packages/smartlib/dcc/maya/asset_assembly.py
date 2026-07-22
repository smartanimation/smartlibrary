from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.apps.asset_manager.service import AssetCreateRequest, AssetManagerService
from smartlib.core.config_loader import ProjectConfig, load_config
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import AssetIdentity, ProjectPaths
from smartlib.core.resolver import SmartPathResolver
from smartlib.core.selection_context import read_selected_asset
from smartlib.core.versioning import format_version, next_version, parse_version


ASSEMBLY_LOC_ATTR = "smartAssemblyLocator"
TARGET_ATTR = "smartAssemblyTarget"
ASSET_ATTR = "smartAssemblyAsset"
CATEGORY_ATTR = "smartAssemblyCategory"
GROUP_ATTR = "smartAssemblyGroup"
VARIANT_ATTR = "smartAssemblyVariant"
SOURCE_NODES_ATTR = "smartAssemblySourceNodes"
USD_MODE_ATTR = "smartAssemblyUsdMode"
LOCAL_OFFSET_Y_ATTR = "smartAssemblyLocalOffsetY"
ASSEMBLY_ROOT = "ASSEMBLY_GRP"
ASSEMBLY_USD_PROXY = "ASSEMBLY_USD_PROXY"


@dataclass(frozen=True)
class AssemblyContext:
    category: str
    group: str
    asset: str
    variant: str = "default"

    @property
    def identity(self) -> AssetIdentity:
        return AssetIdentity(self.category, self.group, self.asset, self.variant)


@dataclass(frozen=True)
class AssemblyComponent:
    target: str
    asset: str
    category: str
    group: str
    variant: str
    locator: str
    source_nodes: list[str]
    usd_mode: str = "reference"
    local_offset_y: float = 0.0


@dataclass(frozen=True)
class ExtractedComponent:
    component: AssemblyComponent
    workfile: Path
    publishfile: Path | None
    assembly_usd_path: Path | None
    asset_root: Path
    variant_root: Path
    warning: str = ""
    extract_rule: dict[str, Any] | None = None


@dataclass(frozen=True)
class PublishedComponent:
    component: AssemblyComponent
    version: str
    publish_dir: Path
    ma_path: Path
    usd_path: Path
    asset_usd_path: Path | None = None
    usd_error: str = ""
    extract_rule: dict[str, Any] | None = None


DEFAULT_EXTRACT_RULE: dict[str, Any] = {
    "department": "model",
    "subset": "proxy",
    "center_to_origin": True,
    "bottom_to_ground": False,
    "publish_after_extract": True,
    "reference_after_publish": True,
    "preserve_materials": True,
    "delete_history": False,
    "freeze_transforms": False,
}


def current_assembly_context(project_config: ProjectConfig) -> AssemblyContext:
    path_context = _context_from_current_scene(project_config)
    if path_context:
        return path_context
    scene_context = _scene_assembly_context()
    if scene_context:
        return scene_context
    selected = read_selected_asset(project_config)
    return AssemblyContext(
        category=str(selected.get("category") or "env"),
        group=str(selected.get("group") or "set"),
        asset=str(selected.get("asset") or selected.get("name") or "kitchen_set"),
        variant=str(selected.get("variant") or "default"),
    )


def select_node(node: str) -> None:
    cmds = _maya_cmds()
    if not node or not cmds.objExists(node):
        raise RuntimeError(f"Node was not found: {node}")
    cmds.select(node, replace=True)


def selected_place_locator() -> str:
    cmds = _maya_cmds()
    for node in cmds.ls(selection=True, long=True) or []:
        if node and cmds.objExists(node) and _is_assembly_locator(cmds, node):
            return node
    return ""


def set_assembly_context(
    category: str,
    group: str,
    asset: str,
    variant: str = "default",
) -> AssemblyContext:
    cmds = _maya_cmds()
    root = _ensure_group(cmds, ASSEMBLY_ROOT)
    context = AssemblyContext(
        category=_safe_name(category or "env"),
        group=_safe_name(group or "set"),
        asset=_safe_name(asset or "kitchen"),
        variant=_safe_name(variant or "default"),
    )
    _set_string_attr(cmds, root, CATEGORY_ATTR, context.category)
    _set_string_attr(cmds, root, GROUP_ATTR, context.group)
    _set_string_attr(cmds, root, ASSET_ATTR, context.asset)
    _set_string_attr(cmds, root, VARIANT_ATTR, context.variant)
    return context


def list_components() -> list[AssemblyComponent]:
    cmds = _maya_cmds()
    rows = []
    for node in sorted(cmds.ls(type="transform") or []):
        if not _is_assembly_locator(cmds, node):
            continue
        rows.append(
            AssemblyComponent(
                target=_get_string_attr(cmds, node, TARGET_ATTR) or node.replace("_place_LOC", ""),
                asset=_get_string_attr(cmds, node, ASSET_ATTR),
                category=_get_string_attr(cmds, node, CATEGORY_ATTR),
                group=_get_string_attr(cmds, node, GROUP_ATTR),
                variant=_get_string_attr(cmds, node, VARIANT_ATTR) or "default",
                locator=node,
                source_nodes=_decode_source_nodes(_get_string_attr(cmds, node, SOURCE_NODES_ATTR)),
                usd_mode=_get_string_attr(cmds, node, USD_MODE_ATTR) or "reference",
                local_offset_y=_get_float_attr(cmds, node, LOCAL_OFFSET_Y_ATTR),
            )
        )
    return rows


def create_place_locator(target: str = "component", *, parent: str = "") -> str:
    cmds = _maya_cmds()
    parent = parent or _ensure_placements_group(cmds)
    name = _unique_node(cmds, f"{_safe_name(target)}_place_LOC")
    node = cmds.spaceLocator(name=name)[0]
    _tag_locator(cmds, node)
    _set_string_attr(cmds, node, TARGET_ATTR, _safe_name(target))
    try:
        cmds.setAttr(f"{node}.localScaleX", 3.0)
        cmds.setAttr(f"{node}.localScaleY", 3.0)
        cmds.setAttr(f"{node}.localScaleZ", 3.0)
    except Exception:
        pass
    if parent and cmds.objExists(parent):
        cmds.parent(node, parent, absolute=True)
    cmds.select(node, replace=True)
    return node


def rename_place_locator(locator: str, new_name: str) -> str:
    cmds = _maya_cmds()
    if not locator or not cmds.objExists(locator) or not _is_assembly_locator(cmds, locator):
        raise RuntimeError("Select a valid assembly place locator.")
    clean = _safe_name(new_name)
    if not clean.endswith("_place_LOC"):
        clean = f"{clean}_place_LOC"
    renamed = cmds.rename(locator, _unique_node(cmds, clean))
    _tag_locator(cmds, renamed)
    return renamed


def match_locator_to_viewport_selection(locator: str) -> str:
    cmds = _maya_cmds()
    if not locator or not cmds.objExists(locator) or not _is_assembly_locator(cmds, locator):
        raise RuntimeError("Select a valid assembly place locator.")
    selected = cmds.ls(selection=True, long=True) or []
    targets = [node for node in selected if node and not _same_node(cmds, node, locator)]
    if not targets:
        raise RuntimeError("Select an object in the viewport to match transform from.")
    target = targets[0]
    matrix = cmds.xform(target, query=True, matrix=True, worldSpace=True)
    cmds.xform(locator, matrix=matrix, worldSpace=True)
    cmds.select(locator, replace=True)
    return target


def restore_saved_assembly_locators(project_config: ProjectConfig) -> int:
    context = current_assembly_context(project_config)
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    base_dir = paths.asset_variant_root(context.identity) / "data" / "assembly" / "placements"
    latest = read_json(base_dir / "latest.json", {}) or {}
    path_text = str(latest.get("path") or "").strip()
    data_path = base_dir / path_text if path_text else base_dir / "assembly_placements.json"
    if not data_path.exists():
        candidates = sorted(
            (path for path in base_dir.glob("v*/assembly_placements.json") if parse_version(path.parent.name) is not None),
            key=lambda path: parse_version(path.parent.name) or 0,
        )
        if not candidates:
            raise RuntimeError(f"Saved assembly locator data was not found: {base_dir}")
        data_path = candidates[-1]
    data = read_json(data_path, {}) or {}
    return _restore_assembly_data(data)


def register_selected_component(
    target: str,
    *,
    asset: str = "",
    category: str = "prop",
    group: str = "bp",
    variant: str = "default",
) -> AssemblyComponent:
    cmds = _maya_cmds()
    nodes = _selected_mesh_roots(cmds)
    if not nodes:
        raise RuntimeError("Select mesh nodes to register as a component.")
    asset_name = asset or _safe_name(target)
    locator = create_place_locator(asset_name)
    component_target = _target_from_locator(locator)
    center = _bbox_center(cmds, nodes)
    try:
        cmds.xform(locator, worldSpace=True, translation=center)
    except Exception:
        pass
    _set_string_attr(cmds, locator, TARGET_ATTR, component_target)
    _set_string_attr(cmds, locator, ASSET_ATTR, asset_name)
    _set_string_attr(cmds, locator, CATEGORY_ATTR, category)
    _set_string_attr(cmds, locator, GROUP_ATTR, group)
    _set_string_attr(cmds, locator, VARIANT_ATTR, variant or "default")
    _set_string_attr(cmds, locator, SOURCE_NODES_ATTR, _encode_source_nodes(nodes))
    _set_string_attr(cmds, locator, USD_MODE_ATTR, "reference")
    _set_float_attr(cmds, locator, LOCAL_OFFSET_Y_ATTR, 0.0)
    return AssemblyComponent(component_target, asset_name, category, group, variant or "default", locator, nodes)


def place_published_asset_at_selection(
    project_config: ProjectConfig,
    *,
    category: str,
    group: str,
    asset: str,
    variant: str = "default",
    usd_mode: str = "reference",
) -> AssemblyComponent:
    cmds = _maya_cmds()
    selected = _selected_mesh_roots(cmds)
    if not selected:
        selected = [node for node in (cmds.ls(selection=True, long=True) or []) if cmds.objExists(node)]
    if not selected:
        raise RuntimeError("Select scene nodes to replace with a placed asset.")
    locator = create_place_locator(asset)
    target = _target_from_locator(locator)
    matrix = _source_world_matrix(cmds, selected)
    if matrix:
        _set_locator_to_source_matrix(cmds, locator, matrix)
    component = AssemblyComponent(
        target=target,
        asset=_safe_name(asset),
        category=_safe_name(category or "prop"),
        group=_safe_name(group or "bp"),
        variant=_safe_name(variant or "default"),
        locator=locator,
        source_nodes=selected,
        usd_mode=_normalize_usd_mode(usd_mode),
    )
    _set_component_metadata(
        locator,
        target=component.target,
        asset=component.asset,
        category=component.category,
        group=component.group,
        variant=component.variant,
        source_nodes=selected,
        usd_mode=component.usd_mode,
    )
    latest_asset_usd(project_config, component.category, component.group, component.asset, component.variant)
    refresh_assembly_preview_usd(project_config, reload=True)
    for node in selected:
        if cmds.objExists(node):
            try:
                cmds.setAttr(f"{node}.visibility", False)
            except Exception:
                pass
    cmds.select(locator, replace=True)
    return component


def place_published_asset_at_locator(
    project_config: ProjectConfig,
    locator: str,
    *,
    category: str,
    group: str,
    asset: str,
    variant: str = "default",
    usd_mode: str = "reference",
) -> AssemblyComponent:
    cmds = _maya_cmds()
    if not locator or not cmds.objExists(locator) or not _is_assembly_locator(cmds, locator):
        raise RuntimeError("Select a valid assembly place locator.")
    component = AssemblyComponent(
        target=_target_from_locator(locator),
        asset=_safe_name(asset),
        category=_safe_name(category or "prop"),
        group=_safe_name(group or "bp"),
        variant=_safe_name(variant or "default"),
        locator=locator,
        source_nodes=_decode_source_nodes(_get_string_attr(cmds, locator, SOURCE_NODES_ATTR)),
        usd_mode=_normalize_usd_mode(usd_mode),
        local_offset_y=_get_float_attr(cmds, locator, LOCAL_OFFSET_Y_ATTR),
    )
    latest_asset_usd(project_config, component.category, component.group, component.asset, component.variant)
    _set_component_metadata(
        locator,
        target=component.target,
        asset=component.asset,
        category=component.category,
        group=component.group,
        variant=component.variant,
        source_nodes=component.source_nodes,
        usd_mode=component.usd_mode,
        local_offset_y=component.local_offset_y,
    )
    refresh_assembly_preview_usd(project_config, reload=True)
    cmds.select(locator, replace=True)
    return component


def selected_extract_target() -> tuple[str, list[str]]:
    cmds = _maya_cmds()
    nodes = _selected_mesh_roots(cmds)
    if not nodes:
        return "", []
    target = nodes[0]
    return target, nodes


def duplicate_placement(component: AssemblyComponent, new_target: str = "") -> AssemblyComponent:
    cmds = _maya_cmds()
    if not cmds.objExists(component.locator):
        raise RuntimeError(f"Placement locator was not found: {component.locator}")
    target = _safe_name(new_target or _next_target_name(component.target))
    duplicate = cmds.duplicate(component.locator, returnRootsOnly=True)[0]
    duplicate = cmds.rename(duplicate, _unique_node(cmds, f"{target}_place_LOC"))
    parent = _ensure_placements_group(cmds)
    try:
        cmds.parent(duplicate, parent, absolute=True)
    except Exception:
        pass
    _tag_locator(cmds, duplicate)
    _set_component_metadata(
        duplicate,
        target=target,
        asset=component.asset,
        category=component.category,
        group=component.group,
        variant=component.variant,
        source_nodes=component.source_nodes,
        usd_mode=component.usd_mode,
        local_offset_y=component.local_offset_y,
    )
    cmds.select(duplicate, replace=True)
    return AssemblyComponent(
        target,
        component.asset,
        component.category,
        component.group,
        component.variant,
        duplicate,
        component.source_nodes,
        component.usd_mode,
        component.local_offset_y,
    )


def update_component_asset(
    component: AssemblyComponent,
    *,
    asset: str | None = None,
    category: str | None = None,
    group: str | None = None,
    variant: str | None = None,
    usd_mode: str | None = None,
) -> AssemblyComponent:
    cmds = _maya_cmds()
    if not cmds.objExists(component.locator):
        raise RuntimeError(f"Placement locator was not found: {component.locator}")
    updated = AssemblyComponent(
        target=component.target,
        asset=_safe_name(asset or component.asset),
        category=_safe_name(category or component.category or "prop"),
        group=_safe_name(group or component.group or "bp"),
        variant=_safe_name(variant or component.variant or "default"),
        locator=component.locator,
        source_nodes=component.source_nodes,
        usd_mode=_normalize_usd_mode(usd_mode or component.usd_mode),
        local_offset_y=component.local_offset_y,
    )
    _set_component_metadata(
        component.locator,
        target=updated.target,
        asset=updated.asset,
        category=updated.category,
        group=updated.group,
        variant=updated.variant,
        source_nodes=updated.source_nodes,
        usd_mode=updated.usd_mode,
        local_offset_y=updated.local_offset_y,
    )
    return updated


def capture_component_thumbnail(project_config: ProjectConfig, component: AssemblyComponent) -> Path:
    if not component.asset:
        raise RuntimeError("Component asset name is empty.")
    cmds = _maya_cmds()
    request = AssetCreateRequest(
        category=component.category or "prop",
        group=component.group or "bp",
        name=component.asset,
        variant=component.variant or "default",
        description="",
    )
    AssetManagerService(project_config).create_asset(request)
    project_root = _project_root(project_config)
    asset_root = ProjectPaths(project_root).asset_root(request.identity)
    thumbnail_path = asset_root / "thumbnail.jpg"
    from smartlib.dcc.maya.thumbnail import capture_viewport_thumbnail

    capture_nodes = _thumbnail_mesh_roots(cmds, component.source_nodes)
    if not capture_nodes:
        capture_nodes = _thumbnail_mesh_roots(cmds, cmds.ls(selection=True, long=True) or [])
    if not capture_nodes:
        raise RuntimeError("Component has no visible mesh nodes for thumbnail capture.")
    capture_viewport_thumbnail(thumbnail_path, isolate_nodes=capture_nodes)
    _ensure_thumbnail_file(thumbnail_path)
    asset_json = asset_root / "asset.json"
    metadata = read_json(asset_json, {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.update(
        {
            "asset": component.asset,
            "category": component.category or "prop",
            "group": component.group or "bp",
            "thumbnail": thumbnail_path.name,
        }
    )
    write_json(asset_json, metadata)
    return thumbnail_path


def capture_asset_viewport_thumbnail(
    project_config: ProjectConfig,
    *,
    category: str,
    group: str,
    asset: str,
    variant: str = "default",
) -> Path:
    if not asset:
        raise RuntimeError("Asset name is empty.")
    request = AssetCreateRequest(
        category=_safe_name(category or "prop"),
        group=_safe_name(group or "bp"),
        name=_safe_name(asset),
        variant=_safe_name(variant or "default"),
        description="",
    )
    AssetManagerService(project_config).create_asset(request)
    project_root = _project_root(project_config)
    asset_root = ProjectPaths(project_root).asset_root(request.identity)
    thumbnail_path = asset_root / "thumbnail.jpg"
    from smartlib.dcc.maya.thumbnail import capture_viewport_thumbnail

    capture_viewport_thumbnail(thumbnail_path)
    _ensure_thumbnail_file(thumbnail_path)
    asset_json = asset_root / "asset.json"
    metadata = read_json(asset_json, {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.update(
        {
            "asset": request.name,
            "category": request.category,
            "group": request.group,
            "thumbnail": thumbnail_path.name,
        }
    )
    write_json(asset_json, metadata)
    return thumbnail_path


def _ensure_thumbnail_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Thumbnail was not created: {path}")
    try:
        if path.stat().st_size <= 0:
            raise RuntimeError(f"Thumbnail is empty: {path}")
    except OSError as exc:
        raise RuntimeError(f"Could not inspect thumbnail: {path}") from exc


def save_assembly(project_config: ProjectConfig, comment: str = "") -> Path:
    context = current_assembly_context(project_config)
    _ensure_assembly_asset(project_config, context, comment=comment)
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    data_path = _save_assembly_data(project_config, context, paths, comment=comment)
    data = read_json(data_path, {}) or {}
    files = _write_working_assembly_usd(
        project_config,
        context,
        paths,
        data,
        data_path=data_path,
        comment=comment,
        export_local=True,
        reload=True,
    )
    return files["entry"]


def publish_assembly(project_config: ProjectConfig, comment: str = "") -> Path:
    context = current_assembly_context(project_config)
    _ensure_assembly_asset(project_config, context, comment=comment)
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    data_path = _save_assembly_data(project_config, context, paths, comment=comment)
    data = read_json(data_path, {}) or {}
    base_dir = paths.asset_variant_root(context.identity) / "publish" / "assembly" / "render"
    version = _next_version_label(base_dir)
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)
    files = _write_assembly_usd_layers(
        data,
        project_root=project_root,
        output_dir=version_dir,
        export_local=True,
    )
    usda_path = files["entry"]
    record_files = {
        "usd": files["entry"].name,
        "assets_usd": files["assets"].name,
    }
    if files["local"].exists():
        record_files["local_usd"] = files["local"].name
    write_json(
        version_dir / "publish.json",
        {
            "publish_type": "assembly",
            "subset": "render",
            "asset": context.asset,
            "category": context.category,
            "group": context.group,
            "variant": context.variant,
            "version": version,
            "files": record_files,
            "local_geometry": {
                "nodes": files["local_nodes"],
                "file": files["local"].name if files["local"].exists() else "",
                "status": "exported"
                if files["local_nodes"] and not files["local_error"]
                else ("placeholder" if files["local_error"] else "empty"),
                "error": files["local_error"],
            },
            "source_data": _relative_to_project(data_path, project_root),
            "comment": comment,
        },
    )
    write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/assembly.usda"})
    _update_versions(base_dir / "versions.json", version)
    _publish_assembly_usd_entry(
        paths,
        context.identity,
        assembly_version=version,
        assembly_usd_path=usda_path,
        data_path=data_path,
        component_count=len(data.get("components") or []),
        local_geometry_file=files["local"].name if files["local"].exists() else "",
        comment=comment,
    )
    return usda_path


def refresh_assembly_preview_usd(project_config: ProjectConfig, comment: str = "", *, reload: bool = True) -> Path:
    context = current_assembly_context(project_config)
    _ensure_assembly_asset(project_config, context, comment=comment)
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    data = _assembly_data(context, version="preview", comment=comment)
    files = _write_working_assembly_usd(
        project_config,
        context,
        paths,
        data,
        data_path=None,
        comment=comment,
        export_local=False,
        reload=reload,
    )
    return files["entry"]


def _save_assembly_data(
    project_config: ProjectConfig,
    context: AssemblyContext,
    paths: ProjectPaths,
    *,
    comment: str = "",
) -> Path:
    base_dir = paths.asset_variant_root(context.identity) / "data" / "assembly" / "placements"
    version = _next_version_label(base_dir)
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)
    data = _assembly_data(context, version=version, comment=comment)
    path = write_json(version_dir / "assembly_placements.json", data)
    write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/assembly_placements.json"})
    _update_versions(base_dir / "versions.json", version)
    return path


def _write_working_assembly_usd(
    project_config: ProjectConfig,
    context: AssemblyContext,
    paths: ProjectPaths,
    data: dict[str, Any],
    *,
    data_path: Path | None = None,
    comment: str = "",
    export_local: bool = False,
    reload: bool = False,
) -> dict[str, Any]:
    project_root = _project_root(project_config)
    preview_dir = paths.asset_variant_root(context.identity) / "data" / "assembly" / "preview"
    files = _write_assembly_usd_layers(
        data,
        project_root=project_root,
        output_dir=preview_dir,
        export_local=export_local,
    )
    metadata = {
        "assembly": context.asset,
        "category": context.category,
        "group": context.group,
        "variant": context.variant,
        "components": len(data.get("components") or []),
        "source_data": _relative_to_project(data_path, project_root) if data_path else "",
        "usd": files["entry"].name,
        "assets_usd": files["assets"].name,
        "local_geometry": files["local"].name if files["local"].exists() else "",
        "local_geometry_status": "exported"
        if files["local_nodes"] and not files["local_error"]
        else ("placeholder" if files["local_error"] else ("empty" if export_local else ("cached" if files["local"].exists() else "empty"))),
        "local_geometry_error": files["local_error"],
        "comment": comment,
    }
    write_json(preview_dir / "assembly_preview.json", metadata)
    if reload:
        _set_assembly_usd_proxy(_maya_cmds(), files["entry"], reload=True)
    return files


def _write_assembly_usd_layers(
    data: dict[str, Any],
    *,
    project_root: Path,
    output_dir: Path,
    export_local: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    entry_path = output_dir / "assembly.usda"
    assets_path = output_dir / "assembly_assets.usd"
    local_path = output_dir / "assembly_local.usd"

    assets_path.write_text(
        _assembly_assets_usda(
            data,
            project_root=project_root,
            usda_path=assets_path,
        ),
        encoding="utf-8",
    )

    local_nodes: list[str] = []
    local_error = ""
    if export_local:
        local_nodes = _local_assembly_mesh_roots(data)
        if local_nodes:
            local_error = _export_nodes_usd(_maya_cmds(), local_nodes, local_path)
        else:
            _write_empty_usd_layer(local_path)

    sublayers = [assets_path.name]
    if local_path.exists():
        sublayers.append(local_path.name)
    entry_path.write_text(_assembly_entry_usda(data, sublayers=sublayers), encoding="utf-8")
    return {
        "entry": entry_path,
        "assets": assets_path,
        "local": local_path,
        "local_nodes": local_nodes,
        "local_error": local_error,
    }


def latest_assembly_usd(project_config: ProjectConfig) -> Path:
    context = current_assembly_context(project_config)
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    preview_path = paths.asset_variant_root(context.identity) / "data" / "assembly" / "preview" / "assembly.usda"
    if preview_path.exists():
        return preview_path

    assembly_base = paths.asset_variant_root(context.identity) / "publish" / "assembly" / "render"
    latest = read_json(assembly_base / "latest.json", {}) or {}
    path_text = str(latest.get("path") or "").strip()
    if path_text:
        latest_path = assembly_base / path_text
        if latest_path.exists():
            return latest_path

    base_dir = paths.asset_variant_root(context.identity) / "publish" / "usd"
    asset_name = _safe_name(context.asset)
    latest = read_json(base_dir / "latest.json", {}) or {}
    path_text = str(latest.get("path") or "").strip()
    if path_text:
        latest_path = base_dir / path_text
        if latest_path.exists():
            return latest_path

    candidates: list[tuple[int, Path]] = []
    for version_dir in base_dir.glob("v*"):
        parsed = parse_version(version_dir.name)
        if parsed is None:
            continue
        entry_path = version_dir / f"{asset_name}.usd"
        if entry_path.exists():
            candidates.append((parsed, entry_path))
    if candidates:
        parsed_version, entry_path = max(candidates, key=lambda item: item[0])
        version = format_version(parsed_version)
        write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/{entry_path.name}"})
        _update_versions(base_dir / "versions.json", version)
        return entry_path

    raise RuntimeError(f"Latest assembly USD was not found: {base_dir}")


def latest_asset_usd(
    project_config: ProjectConfig,
    category: str,
    group: str,
    asset: str,
    variant: str = "default",
) -> Path:
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    identity = AssetIdentity(category, group, asset, variant or "default")
    base_dir = paths.asset_variant_root(identity) / "publish" / "usd"
    latest = read_json(base_dir / "latest.json", {}) or {}
    path_text = str(latest.get("path") or "").strip()
    if path_text:
        latest_path = base_dir / path_text
        if latest_path.exists():
            return latest_path
    asset_name = _safe_name(asset)
    candidates: list[tuple[int, Path]] = []
    for version_dir in base_dir.glob("v*"):
        parsed = parse_version(version_dir.name)
        if parsed is None:
            continue
        for candidate in (version_dir / f"{asset_name}.usd", version_dir / f"{asset_name}.usda"):
            if candidate.exists():
                candidates.append((parsed, candidate))
    if candidates:
        parsed_version, entry_path = max(candidates, key=lambda item: item[0])
        version = format_version(parsed_version)
        write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/{entry_path.name}"})
        _update_versions(base_dir / "versions.json", version)
        return entry_path
    for subset in ("proxy", "render"):
        fallback_base = paths.asset_publish_dir(identity, "model", subset)
        latest = read_json(fallback_base / "latest.json", {}) or {}
        path_text = str(latest.get("path") or "").strip()
        if path_text:
            fallback = fallback_base / path_text
            if fallback.exists():
                return fallback
        model_candidates: list[tuple[int, Path]] = []
        for version_dir in fallback_base.glob("v*"):
            parsed = parse_version(version_dir.name)
            if parsed is None:
                continue
            candidate = version_dir / "model.usd"
            if candidate.exists():
                model_candidates.append((parsed, candidate))
        if model_candidates:
            return max(model_candidates, key=lambda item: item[0])[1]
    raise RuntimeError(f"Latest asset USD was not found: {base_dir}")


def latest_asset_maya_reference(
    project_config: ProjectConfig,
    category: str,
    group: str,
    asset: str,
    variant: str = "default",
) -> Path:
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    identity = AssetIdentity(category, group, asset, variant or "default")
    for subset in ("proxy", "render"):
        base_dir = paths.asset_publish_dir(identity, "model", subset)
        latest = read_json(base_dir / "latest.json", {}) or {}
        path_text = str(latest.get("path") or "").strip()
        if path_text:
            latest_path = base_dir / path_text
            candidates = [latest_path]
            if latest_path.name != "model.ma":
                candidates.append(latest_path.parent / "model.ma")
            for candidate in candidates:
                if candidate.exists() and candidate.suffix.lower() in {".ma", ".mb"}:
                    return candidate
        version_candidates: list[tuple[int, Path]] = []
        for version_dir in base_dir.glob("v*"):
            parsed = parse_version(version_dir.name)
            if parsed is None:
                continue
            for candidate in (version_dir / "model.ma", version_dir / "model.mb"):
                if candidate.exists():
                    version_candidates.append((parsed, candidate))
        if version_candidates:
            return max(version_candidates, key=lambda item: item[0])[1]
    raise RuntimeError(
        "Latest Maya model publish was not found for "
        f"{category}/{group}/{asset}/{variant}. Publish the component model first."
    )


def open_assembly_usd(project_config: ProjectConfig, *, reload: bool = False) -> Path:
    usd_path = latest_assembly_usd(project_config)
    cmds = _maya_cmds()
    return _set_assembly_usd_proxy(cmds, usd_path, reload=reload)


def _set_assembly_usd_proxy(cmds: Any, usd_path: Path, *, reload: bool = False) -> Path:
    if not usd_path.exists():
        raise RuntimeError(f"Assembly USD was not found: {usd_path}")
    _load_maya_usd_plugin(cmds)
    proxy, shape = _assembly_usd_proxy(cmds)
    if not proxy:
        proxy = ASSEMBLY_USD_PROXY
        if cmds.objExists(proxy):
            proxy = _unique_node(cmds, proxy)
        proxy = cmds.createNode("transform", name=proxy)
        shape = cmds.createNode("mayaUsdProxyShape", name=f"{proxy}Shape", parent=proxy)
    if not shape or not cmds.objExists(shape):
        shape = cmds.createNode("mayaUsdProxyShape", name=f"{proxy}Shape", parent=proxy)
    _show_assembly_usd_proxy(cmds, proxy, shape)
    path_text = str(usd_path).replace("\\", "/")
    current_path = ""
    try:
        current_path = str(cmds.getAttr(f"{shape}.filePath") or "")
    except Exception:
        current_path = ""
    if current_path != path_text:
        cmds.setAttr(f"{shape}.filePath", path_text, type="string")
    if reload:
        _reload_maya_usd_proxy_shape(cmds, shape, path_text)
    _refresh_maya_viewports(cmds)
    cmds.select(proxy, replace=True)
    return usd_path


def publish_component_model(
    project_config: ProjectConfig,
    component: AssemblyComponent,
    *,
    subset: str = "render",
    comment: str = "",
) -> PublishedComponent:
    cmds = _maya_cmds()
    if not component.asset:
        raise RuntimeError("Component asset name is empty.")
    rule = resolve_extract_rule(
        project_config,
        component,
        fallback={
            "subset": subset or "render",
            "department": "model",
        },
    )
    subset = str(rule.get("subset") or subset or "render")
    nodes = [node for node in component.source_nodes if cmds.objExists(node)]
    if not nodes:
        raise RuntimeError("Component has no valid source nodes. Register selected mesh again.")

    request = AssetCreateRequest(
        category=component.category or "prop",
        group=component.group or "bp",
        name=component.asset,
        variant=component.variant or "default",
        description=comment,
    )
    service = AssetManagerService(project_config)
    service.create_asset(request)
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    base_dir = paths.asset_publish_dir(request.identity, "model", subset or "render")
    version = _next_version_label(base_dir)
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)

    ma_path = version_dir / "model.ma"
    usd_path = version_dir / "model.usd"
    top_node, warning, _bottom_offset = _build_extract_export_hierarchy(
        cmds,
        component,
        nodes,
        center_to_origin=_rule_bool(rule, "center_to_origin", True),
        bottom_to_ground=_rule_bool(rule, "bottom_to_ground", _rule_bool(rule, "center_to_origin", True)),
        freeze_transforms=_rule_bool(rule, "freeze_transforms", False),
    )
    previous_selection = cmds.ls(selection=True, long=True) or []
    usd_error = ""
    try:
        cmds.select(top_node, replace=True)
        cmds.file(
            str(ma_path).replace("\\", "/"),
            force=True,
            options="v=0;",
            type="mayaAscii",
            exportSelected=True,
        )
        usd_error = _export_selected_usd(cmds, usd_path)
    finally:
        if cmds.objExists(top_node):
            cmds.delete(top_node)
        _restore_selection(cmds, previous_selection)

    record = {
        "asset": component.asset,
        "category": component.category,
        "group": component.group,
        "variant": component.variant or "default",
        "publish_type": "model",
        "subset": subset or "render",
        "version": parse_version(version),
        "files": {"ma": "model.ma", "usd": "model.usd"},
        "source_nodes": component.source_nodes,
        "extract_rule": rule,
        "comment": comment,
    }
    if warning:
        record["warning"] = warning
    if usd_error:
        record["usd_export_error"] = usd_error
        record["usd_export_status"] = "placeholder"
    else:
        record["usd_export_status"] = "exported"
    write_json(version_dir / "publish.json", record)
    write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/model.usd"})
    _update_versions(base_dir / "versions.json", version)
    asset_usd_path = _publish_asset_usd_entry(
        paths,
        request.identity,
        model_version=version,
        model_usd_path=usd_path,
        subset=subset or "render",
        comment=comment,
    )
    _set_string_attr(cmds, component.locator, ASSET_ATTR, component.asset)
    _set_string_attr(cmds, component.locator, CATEGORY_ATTR, component.category)
    _set_string_attr(cmds, component.locator, GROUP_ATTR, component.group)
    _set_string_attr(cmds, component.locator, VARIANT_ATTR, component.variant or "default")
    _set_string_attr(cmds, component.locator, USD_MODE_ATTR, _normalize_usd_mode(component.usd_mode))
    return PublishedComponent(
        component,
        version,
        version_dir,
        ma_path,
        usd_path,
        asset_usd_path=asset_usd_path,
        usd_error=usd_error,
        extract_rule=rule,
    )


def compose_component_asset_usd(
    project_config: ProjectConfig,
    component: AssemblyComponent,
    *,
    subset: str = "render",
    comment: str = "",
) -> Path:
    if not component.asset:
        raise RuntimeError("Component asset name is empty.")
    identity = AssetIdentity(
        component.category or "prop",
        component.group or "bp",
        component.asset,
        component.variant or "default",
    )
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    model_base = paths.asset_publish_dir(identity, "model", subset or "render")
    model_version, model_usd_path = _latest_model_usd(model_base)
    if not model_version or not model_usd_path:
        raise RuntimeError(f"Latest model USD was not found under: {model_base}")
    if not model_usd_path.exists():
        raise RuntimeError(f"Latest model USD does not exist: {model_usd_path}")
    return _publish_asset_usd_entry(
        paths,
        identity,
        model_version=model_version,
        model_usd_path=model_usd_path,
        subset=subset or "render",
        comment=comment,
    )


def resolve_extract_rule(
    project_config: ProjectConfig,
    component: AssemblyComponent,
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve project and asset-local extraction rules for a component.

    Merge order:
    builtin default -> project default/category/group -> call-site fallback
    -> assembly asset local default/component.
    """

    rule = dict(DEFAULT_EXTRACT_RULE)
    project_rules = _project_extract_rules(project_config)
    rule = _deep_merge(rule, _dict_value(project_rules, "default"))
    category_rules = _dict_value(project_rules, "categories")
    group_rules = _dict_value(project_rules, "groups")
    if component.category:
        rule = _deep_merge(rule, _dict_value(category_rules, component.category))
    if component.group:
        rule = _deep_merge(rule, _dict_value(group_rules, component.group))
    if fallback:
        rule = _deep_merge(rule, {key: value for key, value in fallback.items() if value not in (None, "")})

    local_rules = _asset_local_extract_rules(project_config)
    rule = _deep_merge(rule, _dict_value(local_rules, "default"))
    local_components = _dict_value(local_rules, "components")
    for key in (component.target, component.asset, component.locator):
        if key:
            rule = _deep_merge(rule, _dict_value(local_components, key))
    rule["source"] = _extract_rule_source(project_config)
    return rule


def _project_extract_rules(project_config: ProjectConfig) -> dict[str, Any]:
    try:
        data = project_config.load("templates_assets.yml")
    except Exception:
        config_dir = getattr(project_config, "config_dir", "")
        data = ProjectConfig(config_dir).load("templates_assets.yml") if config_dir else {}
    rules = data.get("extract_rules") if isinstance(data, dict) else {}
    return rules if isinstance(rules, dict) else {}


def _asset_local_extract_rules(project_config: ProjectConfig) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in _asset_local_extract_rule_paths(project_config):
        data = load_config(path)
        if not isinstance(data, dict):
            continue
        rules = data.get("extract") or data.get("extract_rules") or data
        if isinstance(rules, dict):
            merged = _deep_merge(merged, rules)
    return merged


def _asset_local_extract_rule_paths(project_config: ProjectConfig) -> list[Path]:
    try:
        context = current_assembly_context(project_config)
        paths = ProjectPaths(_project_root(project_config))
        asset_root = paths.asset_root(context.identity)
        variant_root = paths.asset_variant_root(context.identity)
    except Exception:
        return []
    candidates = [
        asset_root / "asset_extract.yml",
        asset_root / "asset_extract.yaml",
        asset_root / "asset_extract.json",
        variant_root / "asset_extract.yml",
        variant_root / "asset_extract.yaml",
        variant_root / "asset_extract.json",
    ]
    return [path for path in candidates if path.exists()]


def _extract_rule_source(project_config: ProjectConfig) -> dict[str, Any]:
    return {
        "project": "templates_assets.yml:extract_rules",
        "local": [str(path).replace("\\", "/") for path in _asset_local_extract_rule_paths(project_config)],
    }


def _dict_value(data: Any, key: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _rule_bool(rule: dict[str, Any], key: str, default: bool) -> bool:
    value = rule.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _latest_model_usd(model_base: Path) -> tuple[str, Path | None]:
    latest = read_json(model_base / "latest.json", {}) or {}
    version = str(latest.get("version") or "").strip()
    path_text = str(latest.get("path") or "").strip()
    if version and path_text:
        return version, model_base / path_text

    candidates: list[tuple[int, Path]] = []
    for version_dir in model_base.glob("v*"):
        parsed = parse_version(version_dir.name)
        if parsed is None:
            continue
        model_usd = version_dir / "model.usd"
        if model_usd.exists():
            candidates.append((parsed, model_usd))
    if not candidates:
        return "", None
    parsed_version, path = max(candidates, key=lambda item: item[0])
    version_label = format_version(parsed_version)
    write_json(model_base / "latest.json", {"version": version_label, "path": f"{version_label}/model.usd"})
    _update_versions(model_base / "versions.json", version_label)
    return version_label, path


def extract_component(
    project_config: ProjectConfig,
    component: AssemblyComponent,
    *,
    department: str = "model",
    subset: str = "render",
    version: int = 1,
    take: int = 1,
    center_to_origin: bool = True,
    description: str = "",
) -> ExtractedComponent:
    cmds = _maya_cmds()
    if not component.asset:
        raise RuntimeError("Component asset name is empty.")
    rule = resolve_extract_rule(
        project_config,
        component,
        fallback={
            "department": department,
            "subset": subset,
            "center_to_origin": center_to_origin,
        },
    )
    department = str(rule.get("department") or department or "model")
    subset = str(rule.get("subset") or subset or "proxy")
    center_to_origin = _rule_bool(rule, "center_to_origin", center_to_origin)
    bottom_to_ground = False
    rule["bottom_to_ground"] = False
    publish_after_extract = _rule_bool(rule, "publish_after_extract", True)
    reference_after_publish = _rule_bool(rule, "reference_after_publish", True)
    nodes = [node for node in component.source_nodes if cmds.objExists(node)]
    if not nodes:
        raise RuntimeError("Component has no valid source nodes. Register selected mesh again.")

    request = AssetCreateRequest(
        category=component.category or "prop",
        group=component.group or "bp",
        name=component.asset,
        variant=component.variant or "default",
        description=description,
    )
    service = AssetManagerService(project_config)
    created = service.create_asset(request)
    workfile = _component_workfile(
        project_config,
        request.identity,
        department=department,
        subset=subset,
        version=version,
        take=take,
    )
    workfile.parent.mkdir(parents=True, exist_ok=True)
    source_matrix = _source_world_matrix(cmds, nodes)
    top_node, warning, bottom_offset = _build_extract_export_hierarchy(
        cmds,
        component,
        nodes,
        center_to_origin=center_to_origin,
        bottom_to_ground=bottom_to_ground,
        freeze_transforms=_rule_bool(rule, "freeze_transforms", False),
    )
    previous_selection = cmds.ls(selection=True, long=True) or []
    published: PublishedComponent | None = None
    try:
        cmds.select(top_node, replace=True)
        cmds.file(
            str(workfile).replace("\\", "/"),
            force=True,
            options="v=0;",
            type="mayaAscii",
            exportSelected=True,
        )
        if publish_after_extract:
            cmds.select(top_node, replace=True)
            published = _publish_extracted_component_outputs(
                cmds,
                project_config,
                request.identity,
                component,
                workfile,
                subset=subset,
                comment=description,
                extract_rule=rule,
            )
    finally:
        if cmds.objExists(top_node):
            cmds.delete(top_node)
        _restore_selection(cmds, previous_selection)
    publishfile = published.asset_usd_path if published and published.asset_usd_path else None
    if published and not publishfile and not published.usd_error:
        publishfile = published.usd_path
    if source_matrix and cmds.objExists(component.locator):
        _set_locator_to_source_matrix(cmds, component.locator, source_matrix)
    local_offset_y = 0.0
    _set_string_attr(cmds, component.locator, TARGET_ATTR, component.target)
    _set_string_attr(cmds, component.locator, ASSET_ATTR, component.asset)
    _set_string_attr(cmds, component.locator, CATEGORY_ATTR, component.category)
    _set_string_attr(cmds, component.locator, GROUP_ATTR, component.group)
    _set_string_attr(cmds, component.locator, VARIANT_ATTR, component.variant or "default")
    _set_string_attr(cmds, component.locator, SOURCE_NODES_ATTR, _encode_source_nodes(nodes))
    _set_string_attr(cmds, component.locator, USD_MODE_ATTR, _normalize_usd_mode(component.usd_mode))
    _set_float_attr(cmds, component.locator, LOCAL_OFFSET_Y_ATTR, local_offset_y)
    assembly_usd_path = None
    if published and reference_after_publish:
        if published.usd_error:
            warning = _join_errors(
                warning,
                f"USD export failed; kept original source visible and skipped assembly preview update: {published.usd_error}",
            )
        else:
            try:
                assembly_usd_path = refresh_assembly_preview_usd(project_config, comment=description, reload=True)
            except Exception as exc:
                warning = _join_errors(
                    warning,
                    f"Assembly USD refresh failed; kept original source visible: {exc}",
                )
            else:
                for node in nodes:
                    if cmds.objExists(node):
                        try:
                            cmds.setAttr(f"{node}.visibility", False)
                        except Exception:
                            pass
    return ExtractedComponent(
        component=component,
        workfile=workfile,
        publishfile=publishfile,
        assembly_usd_path=assembly_usd_path,
        asset_root=created.asset_root,
        variant_root=created.variant_root,
        warning=warning,
        extract_rule=rule,
    )


def _assembly_data(context: AssemblyContext, *, version: str, comment: str) -> dict[str, Any]:
    return {
        "asset": context.asset,
        "category": context.category,
        "group": context.group,
        "variant": context.variant,
        "version": version,
        "comment": comment,
        "components": [_component_data(row) for row in list_components()],
    }


def _publish_extracted_component_workfile(
    project_config: ProjectConfig,
    identity: AssetIdentity,
    component: AssemblyComponent,
    workfile: Path,
    *,
    subset: str,
    comment: str = "",
    extract_rule: dict[str, Any] | None = None,
) -> Path:
    if not workfile.exists():
        raise RuntimeError(f"Extracted workfile was not found: {workfile}")
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    base_dir = paths.asset_publish_dir(identity, "model", subset or "proxy")
    version = _next_version_label(base_dir)
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)
    publishfile = version_dir / "model.ma"
    shutil.copy2(workfile, publishfile)
    write_json(
        version_dir / "publish.json",
        {
            "asset": identity.name,
            "category": identity.category,
            "group": identity.group,
            "variant": identity.variant,
            "publish_type": "model",
            "subset": subset or "proxy",
            "version": parse_version(version),
            "files": {"ma": publishfile.name},
            "source_workfile": _relative_to_project(workfile, project_root),
            "source_nodes": component.source_nodes,
            "extract_rule": extract_rule or {},
            "comment": comment,
        },
    )
    write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/{publishfile.name}"})
    _update_versions(base_dir / "versions.json", version)
    return publishfile


def _publish_extracted_component_outputs(
    cmds: Any,
    project_config: ProjectConfig,
    identity: AssetIdentity,
    component: AssemblyComponent,
    workfile: Path,
    *,
    subset: str,
    comment: str = "",
    extract_rule: dict[str, Any] | None = None,
) -> PublishedComponent:
    if not workfile.exists():
        raise RuntimeError(f"Extracted workfile was not found: {workfile}")
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    subset = subset or "proxy"
    base_dir = paths.asset_publish_dir(identity, "model", subset)
    version = _next_version_label(base_dir)
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)

    ma_path = version_dir / "model.ma"
    usd_path = version_dir / "model.usd"
    shutil.copy2(workfile, ma_path)
    usd_error = _export_selected_usd(cmds, usd_path)
    if usd_error:
        try:
            if usd_path.exists():
                usd_path.unlink()
        except OSError:
            pass

    files = {"ma": ma_path.name}
    if not usd_error and usd_path.exists():
        files["usd"] = usd_path.name
    record = {
        "asset": identity.name,
        "category": identity.category,
        "group": identity.group,
        "variant": identity.variant,
        "publish_type": "model",
        "subset": subset,
        "version": parse_version(version),
        "files": files,
        "source_workfile": _relative_to_project(workfile, project_root),
        "source_nodes": component.source_nodes,
        "extract_rule": extract_rule or {},
        "comment": comment,
    }
    if usd_error:
        record["usd_export_error"] = usd_error
        record["usd_export_status"] = "failed"
    else:
        record["usd_export_status"] = "exported"
        write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/model.usd"})
        _update_versions(base_dir / "versions.json", version)

    write_json(version_dir / "publish.json", record)
    asset_usd_path = None
    if not usd_error:
        asset_usd_path = _publish_asset_usd_entry(
            paths,
            identity,
            model_version=version,
            model_usd_path=usd_path,
            subset=subset,
            comment=comment,
        )
    return PublishedComponent(
        component,
        version,
        version_dir,
        ma_path,
        usd_path,
        asset_usd_path=asset_usd_path,
        usd_error=usd_error,
        extract_rule=extract_rule or {},
    )


def _ensure_assembly_asset(project_config: ProjectConfig, context: AssemblyContext, *, comment: str = "") -> None:
    request = AssetCreateRequest(
        category=context.category,
        group=context.group,
        name=context.asset,
        variant=context.variant,
        description=comment,
    )
    AssetManagerService(project_config).create_asset(request)


def _scene_assembly_context() -> AssemblyContext | None:
    try:
        cmds = _maya_cmds()
    except RuntimeError:
        return None
    if not cmds.objExists(ASSEMBLY_ROOT):
        return None
    asset = _get_string_attr(cmds, ASSEMBLY_ROOT, ASSET_ATTR)
    if not asset:
        return None
    return AssemblyContext(
        category=_get_string_attr(cmds, ASSEMBLY_ROOT, CATEGORY_ATTR) or "env",
        group=_get_string_attr(cmds, ASSEMBLY_ROOT, GROUP_ATTR) or "set",
        asset=asset,
        variant=_get_string_attr(cmds, ASSEMBLY_ROOT, VARIANT_ATTR) or "default",
    )


def _context_from_current_scene(project_config: ProjectConfig) -> AssemblyContext | None:
    try:
        cmds = _maya_cmds()
        scene_path = Path(cmds.file(query=True, sceneName=True) or "")
    except Exception:
        return None
    if not scene_path:
        return None
    project_root = _project_root(project_config)
    try:
        relative = scene_path.resolve().relative_to((project_root / "assets").resolve())
    except Exception:
        return None
    parts = relative.parts
    if len(parts) < 4:
        return None
    category, group, asset, variant = parts[:4]
    return AssemblyContext(category=category, group=group, asset=asset, variant=variant or "default")


def _component_data(row: AssemblyComponent) -> dict[str, Any]:
    cmds = _maya_cmds()
    matrix = cmds.xform(row.locator, query=True, matrix=True, worldSpace=True) if cmds.objExists(row.locator) else []
    translate = cmds.xform(row.locator, query=True, translation=True, worldSpace=True) if cmds.objExists(row.locator) else []
    rotate = cmds.xform(row.locator, query=True, rotation=True, worldSpace=True) if cmds.objExists(row.locator) else []
    scale = cmds.xform(row.locator, query=True, scale=True, relative=True) if cmds.objExists(row.locator) else []
    return {
        "target": row.target,
        "asset": row.asset,
        "category": row.category,
        "group": row.group,
        "variant": row.variant,
        "locator": row.locator,
        "source_nodes": row.source_nodes,
        "usd_mode": _normalize_usd_mode(row.usd_mode),
        "local_offset_y": float(row.local_offset_y),
        "translate": [float(value) for value in translate],
        "rotate": [float(value) for value in rotate],
        "scale": [float(value) for value in scale],
        "matrix": [float(value) for value in matrix],
        "publish": "model/render/latest",
    }


def _assembly_entry_usda(data: dict[str, Any], *, sublayers: list[str]) -> str:
    asset_name = _usd_identifier(str(data.get("asset") or "assembly"))
    lines = [
        "#usda 1.0",
        "(",
        f'    defaultPrim = "{asset_name}"',
    ]
    layers = [layer for layer in sublayers if layer]
    if layers:
        lines.extend(["    subLayers = ["])
        for index, layer in enumerate(layers):
            suffix = "," if index < len(layers) - 1 else ""
            lines.append(f"        @{layer}@{suffix}")
        lines.append("    ]")
    lines.extend([")", ""])
    return "\n".join(lines)


def _assembly_assets_usda(
    data: dict[str, Any],
    *,
    project_root: Path | None = None,
    usda_path: Path | None = None,
) -> str:
    asset_name = _usd_identifier(str(data.get("asset") or "assembly"))
    lines = [
        "#usda 1.0",
        "(",
        f'    defaultPrim = "{asset_name}"',
    ]
    lines.extend(
        [
            ")",
            "",
            f'def Xform "{asset_name}"',
            "{",
            '    custom string smartpipeline_publish_type = "assembly"',
        ]
    )
    for component in data.get("components") or []:
        target = _usd_identifier(str(component.get("target") or component.get("asset") or "component"))
        virtual = (
            f"asset://{component.get('category')}/{component.get('group')}/{component.get('asset')}/"
            f"{component.get('variant', 'default')}/publish/usd/latest/{component.get('asset')}.usd"
        )
        reference_path = _resolve_usd_reference(virtual, project_root=project_root, usda_path=usda_path)
        usd_mode = _normalize_usd_mode(str(component.get("usd_mode") or "reference"))
        header = [f'    def Xform "{target}"']
        if reference_path:
            header = [
                f'    def Xform "{target}"',
                "    (",
                f"        prepend references = @{reference_path}@",
                '        instanceable = true' if usd_mode == "instance" else "",
                "    )",
            ]
            header = [line for line in header if line]
        translate = component.get("translate") or [0.0, 0.0, 0.0]
        rotate = component.get("rotate") or [0.0, 0.0, 0.0]
        scale = component.get("scale") or [1.0, 1.0, 1.0]
        xform_order = ['"xformOp:translate"', '"xformOp:rotateXYZ"', '"xformOp:scale"']
        lines.extend(
            [
                *header,
                "    {",
                f'        custom string asset = "{component.get("asset", "")}"',
                f'        custom string variant = "{component.get("variant", "default")}"',
                f'        custom string smartUsdMode = "{usd_mode}"',
                f"        custom double smartLocalOffsetY = {_float_value(component.get('local_offset_y')):.6g}",
                f'        custom string reference_virtual = "{virtual}"',
                f"        double3 xformOp:translate = ({_float3(translate)})",
                f"        double3 xformOp:rotateXYZ = ({_float3(rotate)})",
                f"        double3 xformOp:scale = ({_float3(scale, default=(1.0, 1.0, 1.0))})",
                f"        uniform token[] xformOpOrder = [{', '.join(xform_order)}]",
            ]
        )
        lines.append("    }")
    lines.extend(["}", ""])
    return "\n".join(lines)


def _resolve_usd_reference(virtual_path: str, *, project_root: Path | None, usda_path: Path | None) -> str:
    if not project_root or not usda_path:
        return ""
    try:
        result = SmartPathResolver(project_root).resolve(virtual_path)
    except Exception:
        return ""
    if not result.exists:
        return ""
    try:
        return Path(os.path.relpath(result.resolved_path.resolve(), usda_path.parent.resolve())).as_posix()
    except Exception:
        try:
            return result.resolved_path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            return result.resolved_path.as_posix()


def _float3(values: Any, default: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> str:
    try:
        items = list(values)
        if len(items) >= 3:
            return f"{float(items[0]):.6g}, {float(items[1]):.6g}, {float(items[2]):.6g}"
    except Exception:
        pass
    return f"{default[0]:.6g}, {default[1]:.6g}, {default[2]:.6g}"


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _export_selected_usd(cmds: Any, output: Path) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    maya_usd_error = ""
    try:
        _load_maya_usd_plugin(cmds)
    except Exception as exc:
        maya_usd_error = str(exc)
    try:
        if hasattr(cmds, "mayaUSDExport"):
            cmds.mayaUSDExport(
                file=str(output).replace("\\", "/"),
                selection=True,
                mergeTransformAndShape=True,
            )
            return ""
    except Exception as exc:
        maya_usd_error = _join_errors(maya_usd_error, str(exc))
    try:
        cmds.file(
            str(output).replace("\\", "/"),
            force=True,
            type="USD Export",
            exportSelected=True,
            preserveReferences=False,
            options="exportUVs=1;exportSkels=none;exportSkin=none;exportBlendShapes=0;",
        )
        return ""
    except Exception as exc:
        maya_usd_error = _join_errors(maya_usd_error, str(exc))
    _write_placeholder_usd(output, maya_usd_error)
    return maya_usd_error or "USD export failed; placeholder file was written."


def _load_maya_usd_plugin(cmds: Any) -> None:
    if not cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
        cmds.loadPlugin("mayaUsdPlugin", quiet=True)


def _assembly_usd_proxy(cmds: Any) -> tuple[str, str]:
    if not cmds.objExists(ASSEMBLY_USD_PROXY):
        return "", ""
    shapes = cmds.listRelatives(ASSEMBLY_USD_PROXY, shapes=True, fullPath=False) or []
    for shape in shapes:
        try:
            if cmds.nodeType(shape) == "mayaUsdProxyShape":
                return ASSEMBLY_USD_PROXY, shape
        except Exception:
            continue
    return ASSEMBLY_USD_PROXY, ""


def _show_assembly_usd_proxy(cmds: Any, proxy: str, shape: str) -> None:
    for node in (proxy, shape):
        if node and cmds.objExists(node):
            for attr, value in (("visibility", True), ("template", False)):
                if cmds.objExists(f"{node}.{attr}"):
                    try:
                        cmds.setAttr(f"{node}.{attr}", value)
                    except Exception:
                        pass
    for attr in ("drawProxyPurpose", "drawRenderPurpose", "loadPayloads"):
        if cmds.objExists(f"{shape}.{attr}"):
            try:
                cmds.setAttr(f"{shape}.{attr}", True)
            except Exception:
                pass


def _reload_maya_usd_proxy_shape(cmds: Any, shape: str, path_text: str) -> None:
    file_attr = f"{shape}.filePath"
    if not path_text:
        return
    if _reload_proxy_with_mayausd_helper(file_attr):
        return
    if _reload_proxy_stage_directly(cmds, shape, path_text):
        return
    try:
        cmds.setAttr(file_attr, "", type="string")
        cmds.setAttr(file_attr, path_text, type="string")
    except Exception:
        pass


def _reload_proxy_with_mayausd_helper(file_attr: str) -> bool:
    try:
        helper = __import__("AETemplateHelpers")
        helper.ProxyShapeFilePathRefresh(file_attr)
        return True
    except Exception:
        return False


def _reload_proxy_stage_directly(cmds: Any, shape: str, path_text: str) -> bool:
    try:
        maya_usd_ufe = __import__("mayaUsd.ufe", fromlist=["getStage"])
        full_shape = (cmds.ls(shape, long=True) or [shape])[0]
        stage = maya_usd_ufe.getStage(full_shape)
        if stage:
            stage.Reload()
            try:
                cmds.mayaUsdLayerEditor(path_text, edit=True, refreshSystemLock=(full_shape, True))
            except Exception:
                pass
            return True
    except Exception:
        return False
    return False


def _refresh_maya_viewports(cmds: Any) -> None:
    try:
        cmds.refresh(force=True)
        return
    except Exception:
        pass
    try:
        cmds.evalDeferred("import maya.cmds as cmds; cmds.refresh(force=True)")
    except Exception:
        pass


def _export_nodes_usd(cmds: Any, nodes: list[str], output: Path) -> str:
    existing = [node for node in nodes if cmds.objExists(node)]
    if not existing:
        return ""
    previous_selection = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(existing, replace=True)
        return _export_selected_usd(cmds, output)
    finally:
        _restore_selection(cmds, previous_selection)


def _local_assembly_mesh_roots(data: dict[str, Any]) -> list[str]:
    cmds = _maya_cmds()
    registered = set()
    for component in data.get("components") or []:
        for node in component.get("source_nodes") or []:
            registered.add(str(node))
    local_nodes = []
    for node in _all_mesh_roots(cmds):
        if _is_registered_or_child(node, registered):
            continue
        if _is_under_assembly_controls(cmds, node):
            continue
        local_nodes.append(node)
    return sorted(set(local_nodes))


def _all_mesh_roots(cmds: Any) -> list[str]:
    roots = []
    for shape in cmds.ls(type="mesh", long=True) or []:
        if _is_intermediate_shape(cmds, shape):
            continue
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        roots.extend(parents)
    return roots


def _is_registered_or_child(node: str, registered: set[str]) -> bool:
    return any(node == source or node.startswith(f"{source}|") for source in registered)


def _is_under_assembly_controls(cmds: Any, node: str) -> bool:
    for control in (ASSEMBLY_ROOT, "PLACEMENTS_GRP"):
        if not cmds.objExists(control):
            continue
        full = (cmds.ls(control, long=True) or [control])[0]
        if node == full or node.startswith(f"{full}|"):
            return True
    return False


def _is_intermediate_shape(cmds: Any, shape: str) -> bool:
    try:
        return bool(cmds.getAttr(f"{shape}.intermediateObject"))
    except Exception:
        return False


def _write_placeholder_usd(path: Path, error: str = "") -> None:
    path.write_text(
        "\n".join(
            [
                "#usda 1.0",
                "(",
                '    defaultPrim = "placeholder"',
                ")",
                "",
                'def Xform "placeholder"',
                "{",
                '    custom string smartpipeline_status = "usd_export_failed"',
                f'    custom string error = "{_escape_usd_string(error)}"',
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_empty_usd_layer(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#usda 1.0\n\n", encoding="utf-8")


def _join_errors(*messages: str) -> str:
    return " | ".join(message for message in messages if message)


def _escape_usd_string(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _component_workfile(
    project_config: ProjectConfig,
    identity: AssetIdentity,
    *,
    department: str,
    subset: str,
    version: int,
    take: int,
) -> Path:
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    version_label = format_version(version)
    take_label = str(take).zfill(2)
    filename = f"{project_config.project_name}_{identity.name}_{department}_{identity.variant}_{version_label}_{take_label}.ma"
    return paths.asset_variant_root(identity) / "work" / department / "maya" / subset / filename


def _publish_asset_usd_entry(
    paths: ProjectPaths,
    identity: AssetIdentity,
    *,
    model_version: str,
    model_usd_path: Path,
    subset: str,
    comment: str = "",
) -> Path:
    base_dir = paths.asset_variant_root(identity) / "publish" / "usd"
    version = _next_version_label(base_dir)
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)
    asset_name = _safe_name(identity.name)
    entry_path = version_dir / f"{asset_name}.usd"
    payload_path = version_dir / f"{asset_name}.payload.usd"
    payload_relative = Path(os.path.relpath(model_usd_path.resolve(), version_dir.resolve())).as_posix()
    payload_path.write_text(
        _asset_payload_usd(asset_name, payload_relative),
        encoding="utf-8",
    )
    entry_path.write_text(
        _asset_entry_usd(asset_name, payload_path.name),
        encoding="utf-8",
    )
    write_json(
        version_dir / "publish.json",
        {
            "asset": identity.name,
            "category": identity.category,
            "group": identity.group,
            "variant": identity.variant,
            "publish_type": "usd",
            "subset": "asset",
            "version": parse_version(version),
            "files": {
                "usd": entry_path.name,
                "payload": payload_path.name,
            },
            "dependencies": {
                "model": {
                    "subset": subset,
                    "version": model_version,
                    "path": _relative_to_project(model_usd_path, paths.project_root),
                }
            },
            "comment": comment,
        },
    )
    write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/{entry_path.name}"})
    _update_versions(base_dir / "versions.json", version)
    return entry_path


def _publish_assembly_usd_entry(
    paths: ProjectPaths,
    identity: AssetIdentity,
    *,
    assembly_version: str,
    assembly_usd_path: Path,
    data_path: Path,
    component_count: int,
    local_geometry_file: str = "",
    comment: str = "",
) -> Path:
    base_dir = paths.asset_variant_root(identity) / "publish" / "usd"
    version = _next_version_label(base_dir)
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)
    asset_name = _safe_name(identity.name)
    entry_path = version_dir / f"{asset_name}.usd"
    payload_path = version_dir / f"{asset_name}.payload.usd"
    json_path = version_dir / f"{asset_name}.json"
    payload_relative = Path(os.path.relpath(assembly_usd_path.resolve(), version_dir.resolve())).as_posix()
    payload_path.write_text(
        _asset_payload_usd(asset_name, payload_relative, publish_type="assembly_payload"),
        encoding="utf-8",
    )
    entry_path.write_text(
        _asset_entry_usd(asset_name, payload_path.name, publish_type="assembly_usd"),
        encoding="utf-8",
    )
    metadata = {
        "asset": identity.name,
        "category": identity.category,
        "group": identity.group,
        "variant": identity.variant,
        "publish_type": "usd",
        "subset": "assembly",
        "version": parse_version(version),
        "assembly": {
            "version": assembly_version,
            "path": _relative_to_project(assembly_usd_path, paths.project_root),
            "data": _relative_to_project(data_path, paths.project_root),
            "components": component_count,
            "local_geometry": local_geometry_file,
        },
        "comment": comment,
    }
    write_json(json_path, metadata)
    write_json(
        version_dir / "publish.json",
        {
            **metadata,
            "files": {
                "usd": entry_path.name,
                "payload": payload_path.name,
                "metadata": json_path.name,
            },
        },
    )
    write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/{entry_path.name}"})
    _update_versions(base_dir / "versions.json", version)
    return entry_path


def _asset_entry_usd(asset_name: str, payload_file: str, *, publish_type: str = "asset_usd") -> str:
    prim = _usd_identifier(asset_name)
    return "\n".join(
        [
            "#usda 1.0",
            "(",
            f'    defaultPrim = "{prim}"',
            ")",
            "",
            f'def Xform "{prim}"',
            "(",
            f"    prepend references = @{payload_file}@",
            ")",
            "{",
            f'    custom string smartpipeline_publish_type = "{publish_type}"',
            "}",
            "",
        ]
    )


def _asset_payload_usd(asset_name: str, model_usd_relative: str, *, publish_type: str = "asset_payload") -> str:
    prim = _usd_identifier(asset_name)
    return "\n".join(
        [
            "#usda 1.0",
            "(",
            f'    defaultPrim = "{prim}"',
            ")",
            "",
            f'def Xform "{prim}"',
            "(",
            f"    payload = @{model_usd_relative}@",
            ")",
            "{",
            f'    custom string smartpipeline_publish_type = "{publish_type}"',
            "}",
            "",
        ]
    )


def _build_extract_export_hierarchy(
    cmds: Any,
    component: AssemblyComponent,
    nodes: list[str],
    *,
    center_to_origin: bool,
    bottom_to_ground: bool | None = None,
    freeze_transforms: bool = False,
) -> tuple[str, str, float]:
    if bottom_to_ground is None:
        bottom_to_ground = center_to_origin
    warning = ""
    asset_name = _safe_name(component.asset)
    top_node = _unique_node(cmds, asset_name)
    if top_node != asset_name:
        warning = f"Could not create exact top node '{asset_name}'. Maya created '{top_node}'."
    top_node = cmds.group(empty=True, name=top_node)
    geo_node = cmds.group(empty=True, name="geo", parent=top_node)
    duplicates = cmds.duplicate(nodes, returnRootsOnly=True) or []
    for node in duplicates:
        try:
            cmds.parent(node, geo_node, absolute=True)
            _reset_transform(cmds, node)
        except Exception:
            pass
    bottom_offset = 0.0
    if bottom_to_ground and duplicates:
        try:
            bbox = cmds.exactWorldBoundingBox(duplicates)
            bottom_offset = -float(bbox[1])
        except Exception:
            bottom_offset = 0.0
    if abs(bottom_offset) > 1.0e-6:
        for node in duplicates:
            try:
                translation = cmds.xform(node, query=True, worldSpace=True, translation=True)
                cmds.xform(
                    node,
                    worldSpace=True,
                    translation=[
                        float(translation[0]),
                        float(translation[1]) + bottom_offset,
                        float(translation[2]),
                    ],
                )
            except Exception:
                pass
    for node in duplicates:
        try:
            if center_to_origin or freeze_transforms:
                _freeze_transform(cmds, node)
        except Exception:
            pass
    return top_node, warning, bottom_offset


def _place_usd_component_proxy(
    cmds: Any,
    component: AssemblyComponent,
    usd_path: Path,
    original_nodes: list[str],
    *,
    local_offset_y: float = 0.0,
) -> str:
    _log_asset_assembly(f"usd placement start locator={component.locator} usd={usd_path}")
    if not usd_path.exists():
        _log_asset_assembly(f"usd placement missing file {usd_path}")
        return f"Published USD was not found for placement: {usd_path}"
    if not cmds.objExists(component.locator):
        _log_asset_assembly(f"usd placement missing locator {component.locator}")
        return f"Placement locator was not found for USD placement: {component.locator}"
    warning = ""
    try:
        proxy = _create_usd_proxy_under_locator(cmds, component.locator, usd_path)
        if local_offset_y:
            try:
                cmds.setAttr(f"{proxy}.translateY", -float(local_offset_y))
            except Exception as exc:
                warning = _join_errors(warning, f"Could not apply USD proxy offset: {exc}")
    except Exception as exc:
        _log_asset_assembly(f"usd placement failed {exc}")
        return f"Could not place published USD: {exc}"

    for node in original_nodes:
        if cmds.objExists(node):
            try:
                cmds.setAttr(f"{node}.visibility", False)
            except Exception:
                pass
    if cmds.objExists(proxy):
        _set_string_attr(cmds, component.locator, SOURCE_NODES_ATTR, _encode_source_nodes([proxy]))
        cmds.select(proxy, replace=True)
        _log_asset_assembly(f"usd placement complete proxy={proxy}")
    return warning


def _reference_extracted_component(
    cmds: Any,
    component: AssemblyComponent,
    workfile: Path,
    original_nodes: list[str],
    *,
    local_offset_y: float = 0.0,
) -> str:
    _log_asset_assembly(f"reference start locator={component.locator} workfile={workfile}")
    if not workfile.exists():
        _log_asset_assembly(f"reference missing workfile {workfile}")
        return f"Extracted workfile was not found for reference: {workfile}"
    if not cmds.objExists(component.locator):
        _log_asset_assembly(f"reference missing locator {component.locator}")
        return f"Placement locator was not found for reference: {component.locator}"
    warning = ""
    namespace = _unique_namespace(cmds, component.asset or component.target)
    group_name = cmds.group(empty=True, name=_unique_node(cmds, f"{_safe_name(component.target)}_REF_GRP"))
    _log_asset_assembly(f"reference group created {group_name} namespace={namespace}")
    try:
        new_nodes = cmds.file(
            str(workfile).replace("\\", "/"),
            reference=True,
            namespace=namespace,
            returnNewNodes=True,
        ) or []
        _log_asset_assembly(f"reference loaded new_nodes={len(new_nodes)}")
    except Exception as exc:
        if cmds.objExists(group_name):
            cmds.delete(group_name)
        _log_asset_assembly(f"reference failed {exc}")
        return f"Could not reference extracted workfile: {exc}"
    roots = _reference_transform_roots(cmds, new_nodes)
    if not roots:
        roots = _reference_mesh_roots(cmds, new_nodes)
    _log_asset_assembly(f"reference roots={roots}")
    if not roots:
        warning = _join_errors(warning, f"Referenced component was loaded, but no transform roots were found: {workfile}")
    for root in roots:
        try:
            cmds.parent(root, group_name, absolute=True)
        except Exception as exc:
            warning = _join_errors(warning, f"Could not place referenced root {root}: {exc}")
    try:
        _match_world_matrix(cmds, group_name, component.locator)
        cmds.parent(group_name, component.locator, absolute=True)
        for attr, value in (
            ("translate", (0, -float(local_offset_y), 0)),
            ("rotate", (0, 0, 0)),
            ("scale", (1, 1, 1)),
        ):
            for axis, axis_value in zip("XYZ", value):
                cmds.setAttr(f"{group_name}.{attr}{axis}", axis_value)
    except Exception as exc:
        warning = _join_errors(warning, f"Could not parent reference group {group_name}: {exc}")
    for node in original_nodes:
        if cmds.objExists(node):
            try:
                cmds.setAttr(f"{node}.visibility", False)
            except Exception:
                pass
    if cmds.objExists(group_name):
        _set_string_attr(cmds, component.locator, SOURCE_NODES_ATTR, _encode_source_nodes([group_name]))
        cmds.select(group_name, replace=True)
        _log_asset_assembly(f"reference complete group={group_name}")
    return warning


def _reference_transform_roots(cmds: Any, nodes: list[str]) -> list[str]:
    transforms = []
    for node in nodes:
        if not cmds.objExists(node):
            continue
        try:
            if cmds.nodeType(node) != "transform":
                continue
            transforms.append((cmds.ls(node, long=True) or [node])[0])
        except Exception:
            continue
    transform_set = set(transforms)
    roots = []
    for node in transforms:
        parent = _parent_transform(cmds, node)
        if not parent or parent not in transform_set:
            roots.append(node)
    return sorted(set(roots), key=lambda value: value.count("|"))


def _parent_transform(cmds: Any, node: str) -> str:
    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    if not parents:
        return ""
    try:
        if cmds.nodeType(parents[0]) == "transform":
            return (cmds.ls(parents[0], long=True) or [parents[0]])[0]
    except Exception:
        return ""
    return ""


def _reference_mesh_roots(cmds: Any, nodes: list[str]) -> list[str]:
    roots = []
    for node in nodes:
        if not cmds.objExists(node):
            continue
        try:
            node_type = cmds.nodeType(node)
        except Exception:
            continue
        if node_type == "mesh":
            parents = cmds.listRelatives(node, parent=True, fullPath=False) or []
            roots.extend(parents)
        elif node_type == "transform":
            shapes = cmds.listRelatives(node, shapes=True, fullPath=False, noIntermediate=True) or []
            if shapes:
                roots.append(node)
    return sorted(set(root for root in roots if root and cmds.objExists(root)))


def _restore_assembly_data(data: dict[str, Any]) -> int:
    cmds = _maya_cmds()
    context = AssemblyContext(
        category=str(data.get("category") or "env"),
        group=str(data.get("group") or "set"),
        asset=str(data.get("asset") or "kitchen"),
        variant=str(data.get("variant") or "default"),
    )
    set_assembly_context(context.category, context.group, context.asset, context.variant)
    parent = _ensure_placements_group(cmds)
    count = 0
    for row in data.get("components") or []:
        if not isinstance(row, dict):
            continue
        locator = _safe_name(str(row.get("locator") or row.get("target") or "component"))
        if not locator.endswith("_place_LOC"):
            locator = f"{locator}_place_LOC"
        if not cmds.objExists(locator):
            locator = cmds.spaceLocator(name=_unique_node(cmds, locator))[0]
        if parent and cmds.objExists(parent):
            try:
                cmds.parent(locator, parent, absolute=True)
            except Exception:
                pass
        _tag_locator(cmds, locator)
        _set_component_metadata(
            locator,
            target=str(row.get("target") or locator.replace("_place_LOC", "")),
            asset=str(row.get("asset") or ""),
            category=str(row.get("category") or "prop"),
            group=str(row.get("group") or "bp"),
            variant=str(row.get("variant") or "default"),
            source_nodes=[str(node) for node in row.get("source_nodes") or []],
            usd_mode=str(row.get("usd_mode") or "reference"),
            local_offset_y=_float_value(row.get("local_offset_y")),
        )
        _apply_transform(cmds, locator, row)
        count += 1
    return count


def _apply_transform(cmds: Any, node: str, row: dict[str, Any]) -> None:
    if row.get("matrix"):
        try:
            cmds.xform(node, matrix=[float(value) for value in row["matrix"]], worldSpace=True)
            return
        except Exception:
            pass
    for attr, values, default in (
        ("translation", row.get("translate"), (0.0, 0.0, 0.0)),
        ("rotation", row.get("rotate"), (0.0, 0.0, 0.0)),
        ("scale", row.get("scale"), (1.0, 1.0, 1.0)),
    ):
        try:
            items = list(values or default)
            cmds.xform(node, **{attr: [float(items[0]), float(items[1]), float(items[2])], "worldSpace": True})
        except Exception:
            pass


def _selected_mesh_roots(cmds: Any) -> list[str]:
    selected = cmds.ls(selection=True, long=True) or []
    roots = []
    for node in selected:
        if cmds.nodeType(node) == "mesh":
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            roots.extend(parents)
        elif cmds.nodeType(node) == "transform":
            shapes = cmds.listRelatives(node, shapes=True, fullPath=True, noIntermediate=True) or []
            descendants = cmds.listRelatives(node, allDescendents=True, fullPath=True) or []
            if any(cmds.nodeType(shape) == "mesh" for shape in [*shapes, *descendants] if cmds.objExists(shape)):
                roots.append(node)
    return sorted(set(roots))


def _thumbnail_mesh_roots(cmds: Any, nodes: list[str]) -> list[str]:
    roots = []
    for node in nodes:
        if not node or not cmds.objExists(node):
            continue
        try:
            node_type = cmds.nodeType(node)
        except Exception:
            continue
        if node_type == "mesh":
            if _is_intermediate_shape(cmds, node):
                continue
            roots.extend(cmds.listRelatives(node, parent=True, fullPath=True) or [])
            continue
        if node_type != "transform":
            continue
        direct_shapes = cmds.listRelatives(node, shapes=True, fullPath=True, noIntermediate=True) or []
        if any(cmds.nodeType(shape) == "mesh" for shape in direct_shapes if cmds.objExists(shape)):
            roots.extend(cmds.ls(node, long=True) or [node])
        descendants = cmds.listRelatives(node, allDescendents=True, fullPath=True) or []
        for descendant in descendants:
            if not descendant or not cmds.objExists(descendant):
                continue
            try:
                if cmds.nodeType(descendant) != "mesh" or _is_intermediate_shape(cmds, descendant):
                    continue
            except Exception:
                continue
            roots.extend(cmds.listRelatives(descendant, parent=True, fullPath=True) or [])
    return _unique_existing_nodes(cmds, roots)


def _unique_existing_nodes(cmds: Any, nodes: list[str]) -> list[str]:
    unique = []
    seen = set()
    for node in nodes:
        if not node or not cmds.objExists(node):
            continue
        long_name = (cmds.ls(node, long=True) or [node])[0]
        if long_name in seen:
            continue
        seen.add(long_name)
        unique.append(long_name)
    return unique


def _restore_selection(cmds: Any, nodes: list[str]) -> None:
    existing = [node for node in nodes if node and cmds.objExists(node)]
    try:
        if existing:
            cmds.select(existing, replace=True)
        else:
            cmds.select(clear=True)
    except Exception:
        try:
            cmds.select(clear=True)
        except Exception:
            pass


def _match_world_matrix(cmds: Any, node: str, target: str) -> None:
    matrix = cmds.xform(target, query=True, matrix=True, worldSpace=True)
    cmds.xform(node, matrix=matrix, worldSpace=True)


def _set_locator_to_center(cmds: Any, locator: str, center: list[float]) -> None:
    cmds.xform(locator, worldSpace=True, translation=[float(center[0]), float(center[1]), float(center[2])])
    try:
        for attr, value in (("rotate", (0, 0, 0)), ("scale", (1, 1, 1))):
            for axis, axis_value in zip("XYZ", value):
                cmds.setAttr(f"{locator}.{attr}{axis}", axis_value)
    except Exception:
        pass


def _source_world_matrix(cmds: Any, nodes: list[str]) -> list[float]:
    for node in nodes:
        if node and cmds.objExists(node):
            return [float(value) for value in cmds.xform(node, query=True, matrix=True, worldSpace=True)]
    return []


def _set_locator_to_source_matrix(cmds: Any, locator: str, matrix: list[float]) -> None:
    cmds.xform(locator, matrix=matrix, worldSpace=True)


def _reset_transform(cmds: Any, node: str) -> None:
    for attr, value in (("translate", (0, 0, 0)), ("rotate", (0, 0, 0)), ("scale", (1, 1, 1))):
        for axis, axis_value in zip("XYZ", value):
            try:
                cmds.setAttr(f"{node}.{attr}{axis}", axis_value)
            except Exception:
                pass


def _freeze_transform(cmds: Any, node: str) -> None:
    try:
        cmds.makeIdentity(node, apply=True, translate=True, rotate=True, scale=True, normal=False)
    except Exception:
        pass


def _bbox_center(cmds: Any, nodes: list[str]) -> list[float]:
    bbox = cmds.exactWorldBoundingBox(nodes)
    return [(bbox[0] + bbox[3]) * 0.5, (bbox[1] + bbox[4]) * 0.5, (bbox[2] + bbox[5]) * 0.5]


def _ensure_placements_group(cmds: Any) -> str:
    root = _ensure_group(cmds, ASSEMBLY_ROOT)
    return _ensure_group(cmds, "PLACEMENTS_GRP", parent=root)


def _ensure_group(cmds: Any, name: str, parent: str = "") -> str:
    if cmds.objExists(name):
        return name
    node = cmds.group(empty=True, name=name)
    if parent and cmds.objExists(parent):
        cmds.parent(node, parent)
    return node


def _tag_locator(cmds: Any, node: str) -> None:
    if not cmds.objExists(f"{node}.{ASSEMBLY_LOC_ATTR}"):
        cmds.addAttr(node, longName=ASSEMBLY_LOC_ATTR, attributeType="bool")
    cmds.setAttr(f"{node}.{ASSEMBLY_LOC_ATTR}", True)


def _set_component_metadata(
    locator: str,
    *,
    target: str,
    asset: str,
    category: str,
    group: str,
    variant: str,
    source_nodes: list[str],
    usd_mode: str = "reference",
    local_offset_y: float = 0.0,
) -> None:
    cmds = _maya_cmds()
    _set_string_attr(cmds, locator, TARGET_ATTR, _safe_name(target))
    _set_string_attr(cmds, locator, ASSET_ATTR, _safe_name(asset))
    _set_string_attr(cmds, locator, CATEGORY_ATTR, _safe_name(category))
    _set_string_attr(cmds, locator, GROUP_ATTR, _safe_name(group))
    _set_string_attr(cmds, locator, VARIANT_ATTR, _safe_name(variant or "default"))
    _set_string_attr(cmds, locator, SOURCE_NODES_ATTR, _encode_source_nodes(source_nodes))
    _set_string_attr(cmds, locator, USD_MODE_ATTR, _normalize_usd_mode(usd_mode))
    _set_float_attr(cmds, locator, LOCAL_OFFSET_Y_ATTR, local_offset_y)


def _normalize_usd_mode(value: str) -> str:
    text = str(value or "").strip().lower()
    return "instance" if text in {"instance", "inst", "instanceable"} else "reference"


def _target_from_locator(locator: str) -> str:
    name = str(locator or "").split("|")[-1]
    return re.sub(r"_place_LOC(?=\d*$)", "", name) or "component"


def _create_usd_proxy_under_locator(cmds: Any, locator: str, usd_path: Path) -> str:
    _load_maya_usd_plugin(cmds)
    locator_name = str(locator or "").split("|")[-1]
    proxy = _unique_node(cmds, f"{locator_name.replace('_place_LOC', '')}_USD_PROXY")
    proxy = cmds.createNode("transform", name=proxy, parent=locator)
    shape = cmds.createNode("mayaUsdProxyShape", name=f"{proxy}Shape", parent=proxy)
    cmds.setAttr(f"{shape}.filePath", str(usd_path).replace("\\", "/"), type="string")
    for attr, value in (("translate", (0, 0, 0)), ("rotate", (0, 0, 0)), ("scale", (1, 1, 1))):
        for axis, axis_value in zip("XYZ", value):
            try:
                cmds.setAttr(f"{proxy}.{attr}{axis}", axis_value)
            except Exception:
                pass
    return proxy


def _is_assembly_locator(cmds: Any, node: str) -> bool:
    return bool(cmds.objExists(f"{node}.{ASSEMBLY_LOC_ATTR}") and cmds.getAttr(f"{node}.{ASSEMBLY_LOC_ATTR}"))


def _set_string_attr(cmds: Any, node: str, attr: str, value: str) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, dataType="string")
    cmds.setAttr(f"{node}.{attr}", str(value), type="string")


def _get_string_attr(cmds: Any, node: str, attr: str) -> str:
    if not cmds.objExists(f"{node}.{attr}"):
        return ""
    return str(cmds.getAttr(f"{node}.{attr}") or "")


def _set_float_attr(cmds: Any, node: str, attr: str, value: float) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, attributeType="double")
    cmds.setAttr(f"{node}.{attr}", float(value))


def _get_float_attr(cmds: Any, node: str, attr: str, default: float = 0.0) -> float:
    if not cmds.objExists(f"{node}.{attr}"):
        return default
    try:
        return float(cmds.getAttr(f"{node}.{attr}") or default)
    except Exception:
        return default


def _encode_source_nodes(nodes: list[str]) -> str:
    return json.dumps(nodes, ensure_ascii=False)


def _decode_source_nodes(value: str) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [str(item) for item in data]
    except Exception:
        pass
    return [value] if value.startswith("|") else [item for item in value.split("\n") if item]


def _next_version_label(base_dir: Path) -> str:
    versions = [parse_version(path.name) for path in base_dir.glob("v*") if path.is_dir()]
    return format_version(next_version([version for version in versions if version]))


def _update_versions(path: Path, version: str) -> None:
    versions = read_json(path, []) if path.exists() else []
    rows = []
    seen = False
    for item in versions if isinstance(versions, list) else []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["status"] = "latest" if row.get("version") == version else ("available" if row.get("status") == "latest" else row.get("status", "available"))
        seen = seen or row.get("version") == version
        rows.append(row)
    if not seen:
        rows.append({"version": version, "status": "latest"})
    write_json(path, rows)


def _project_root(project_config: ProjectConfig) -> Path:
    root = project_config.project_root
    if root is None:
        raise RuntimeError("project_root is not set.")
    return root


def _relative_to_project(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _unique_node(cmds: Any, name: str) -> str:
    if not cmds.objExists(name):
        return name
    index = 1
    while cmds.objExists(f"{name}{index}"):
        index += 1
    return f"{name}{index}"


def _unique_namespace(cmds: Any, name: str) -> str:
    base = _safe_name(name)
    namespace = base
    index = 1
    while cmds.namespace(exists=namespace):
        namespace = f"{base}{index}"
        index += 1
    return namespace


def _same_node(cmds: Any, a: str, b: str) -> bool:
    try:
        left = (cmds.ls(a, long=True) or [a])[0]
        right = (cmds.ls(b, long=True) or [b])[0]
        return left == right
    except Exception:
        return a == b


def _next_target_name(target: str) -> str:
    cmds = _maya_cmds()
    base = re.sub(r"_[A-Z]$", "", _safe_name(target))
    for index in range(1, 1000):
        suffix = _alpha_suffix(index)
        candidate = f"{base}_{suffix}"
        if not cmds.objExists(f"{candidate}_place_LOC"):
            return candidate
    return f"{base}_copy"


def _alpha_suffix(index: int) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    value = index
    result = ""
    while value >= 0:
        result = letters[value % 26] + result
        value = value // 26 - 1
        if value < 0:
            break
    return result


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", str(value or "component")).strip("_") or "component"


def _usd_identifier(value: str) -> str:
    clean = _safe_name(value)
    return f"n_{clean}" if clean[0].isdigit() else clean


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Asset Assembly is available inside Maya.") from exc
    return cmds


def _log_asset_assembly(message: str) -> None:
    try:
        root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path.cwd())
        log_path = root / "runtime" / "logs" / "asset_assembly.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        with log_path.open("a", encoding="utf-8", errors="replace") as stream:
            stream.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        pass
