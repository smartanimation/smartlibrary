from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.apps.asset_manager.service import AssetCreateRequest, AssetManagerService
from smartlib.core.config_loader import ProjectConfig
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


@dataclass(frozen=True)
class ExtractedComponent:
    component: AssemblyComponent
    workfile: Path
    asset_root: Path
    variant_root: Path
    warning: str = ""


@dataclass(frozen=True)
class PublishedComponent:
    component: AssemblyComponent
    version: str
    publish_dir: Path
    ma_path: Path
    usd_path: Path
    asset_usd_path: Path | None = None
    usd_error: str = ""


def current_assembly_context(project_config: ProjectConfig) -> AssemblyContext:
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
    locator = create_place_locator(target)
    center = _bbox_center(cmds, nodes)
    try:
        cmds.xform(locator, worldSpace=True, translation=center)
    except Exception:
        pass
    asset_name = asset or _safe_name(target)
    _set_string_attr(cmds, locator, TARGET_ATTR, _safe_name(target))
    _set_string_attr(cmds, locator, ASSET_ATTR, asset_name)
    _set_string_attr(cmds, locator, CATEGORY_ATTR, category)
    _set_string_attr(cmds, locator, GROUP_ATTR, group)
    _set_string_attr(cmds, locator, VARIANT_ATTR, variant or "default")
    _set_string_attr(cmds, locator, SOURCE_NODES_ATTR, _encode_source_nodes(nodes))
    return AssemblyComponent(_safe_name(target), asset_name, category, group, variant or "default", locator, nodes)


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
    )
    cmds.select(duplicate, replace=True)
    return AssemblyComponent(target, component.asset, component.category, component.group, component.variant, duplicate, component.source_nodes)


def update_component_asset(
    component: AssemblyComponent,
    *,
    asset: str | None = None,
    category: str | None = None,
    group: str | None = None,
    variant: str | None = None,
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
    )
    _set_component_metadata(
        component.locator,
        target=updated.target,
        asset=updated.asset,
        category=updated.category,
        group=updated.group,
        variant=updated.variant,
        source_nodes=updated.source_nodes,
    )
    return updated


def save_assembly(project_config: ProjectConfig, comment: str = "") -> Path:
    context = current_assembly_context(project_config)
    _ensure_assembly_asset(project_config, context, comment=comment)
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    base_dir = paths.asset_variant_root(context.identity) / "data" / "assembly" / "placements"
    version = _next_version_label(base_dir)
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)
    data = _assembly_data(context, version=version, comment=comment)
    path = write_json(version_dir / "assembly_placements.json", data)
    write_json(base_dir / "latest.json", {"version": version, "path": f"{version}/assembly_placements.json"})
    _update_versions(base_dir / "versions.json", version)
    return path


def publish_assembly(project_config: ProjectConfig, comment: str = "") -> Path:
    context = current_assembly_context(project_config)
    _ensure_assembly_asset(project_config, context, comment=comment)
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
    data_path = save_assembly(project_config, comment=comment)
    data = read_json(data_path, {}) or {}
    base_dir = paths.asset_variant_root(context.identity) / "publish" / "assembly" / "render"
    version = _next_version_label(base_dir)
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)
    local_usd_path = version_dir / "assembly_local.usd"
    local_nodes = _local_assembly_mesh_roots(data)
    local_usd_error = ""
    if local_nodes:
        local_usd_error = _export_nodes_usd(_maya_cmds(), local_nodes, local_usd_path)
    usda_path = version_dir / "assembly.usda"
    usda_path.write_text(
        _assembly_usda(
            data,
            project_root=project_root,
            usda_path=usda_path,
            local_layer="assembly_local.usd" if local_usd_path.exists() else "",
        ),
        encoding="utf-8",
    )
    files = {"usd": "assembly.usda"}
    if local_usd_path.exists():
        files["local_usd"] = "assembly_local.usd"
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
            "files": files,
            "local_geometry": {
                "nodes": local_nodes,
                "file": "assembly_local.usd" if local_usd_path.exists() else "",
                "status": "exported" if local_nodes and not local_usd_error else ("placeholder" if local_usd_path.exists() else "empty"),
                "error": local_usd_error,
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
        local_geometry_file="assembly_local.usd" if local_usd_path.exists() else "",
        comment=comment,
    )
    return usda_path


def latest_assembly_usd(project_config: ProjectConfig) -> Path:
    context = current_assembly_context(project_config)
    project_root = _project_root(project_config)
    paths = ProjectPaths(project_root)
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


def open_assembly_usd(project_config: ProjectConfig, *, reload: bool = False) -> Path:
    usd_path = latest_assembly_usd(project_config)
    cmds = _maya_cmds()
    _load_maya_usd_plugin(cmds)
    proxy, shape = _assembly_usd_proxy(cmds)
    if reload and proxy and cmds.objExists(proxy):
        cmds.delete(proxy)
        proxy, shape = "", ""
    if not proxy:
        proxy = ASSEMBLY_USD_PROXY
        if cmds.objExists(proxy):
            proxy = _unique_node(cmds, proxy)
        proxy = cmds.createNode("transform", name=proxy)
        shape = cmds.createNode("mayaUsdProxyShape", name=f"{proxy}Shape", parent=proxy)
    if not shape or not cmds.objExists(shape):
        shape = cmds.createNode("mayaUsdProxyShape", name=f"{proxy}Shape", parent=proxy)
    cmds.setAttr(f"{shape}.filePath", str(usd_path).replace("\\", "/"), type="string")
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
    top_node, warning = _build_extract_export_hierarchy(cmds, component, nodes, center_to_origin=True)
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
        if previous_selection:
            cmds.select(previous_selection, replace=True)
        else:
            cmds.select(clear=True)

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
    return PublishedComponent(component, version, version_dir, ma_path, usd_path, asset_usd_path=asset_usd_path, usd_error=usd_error)


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
    top_node, warning = _build_extract_export_hierarchy(cmds, component, nodes, center_to_origin=center_to_origin)
    previous_selection = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(top_node, replace=True)
        cmds.file(
            str(workfile).replace("\\", "/"),
            force=True,
            options="v=0;",
            type="mayaAscii",
            exportSelected=True,
        )
    finally:
        if cmds.objExists(top_node):
            cmds.delete(top_node)
        if previous_selection:
            cmds.select(previous_selection, replace=True)
        else:
            cmds.select(clear=True)
    _set_string_attr(cmds, component.locator, ASSET_ATTR, component.asset)
    _set_string_attr(cmds, component.locator, CATEGORY_ATTR, component.category)
    _set_string_attr(cmds, component.locator, GROUP_ATTR, component.group)
    _set_string_attr(cmds, component.locator, VARIANT_ATTR, component.variant or "default")
    return ExtractedComponent(
        component=component,
        workfile=workfile,
        asset_root=created.asset_root,
        variant_root=created.variant_root,
        warning=warning,
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
        "translate": [float(value) for value in translate],
        "rotate": [float(value) for value in rotate],
        "scale": [float(value) for value in scale],
        "matrix": [float(value) for value in matrix],
        "publish": "model/render/latest",
    }


def _assembly_usda(
    data: dict[str, Any],
    *,
    project_root: Path | None = None,
    usda_path: Path | None = None,
    local_layer: str = "",
) -> str:
    asset_name = _usd_identifier(str(data.get("asset") or "assembly"))
    lines = [
        "#usda 1.0",
        "(",
        f'    defaultPrim = "{asset_name}"',
    ]
    if local_layer:
        lines.extend(
            [
                "    subLayers = [",
                f"        @{local_layer}@",
                "    ]",
            ]
        )
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
        header = [f'    def Xform "{target}"']
        if reference_path:
            header = [
                f'    def Xform "{target}"',
                "    (",
                f"        prepend references = @{reference_path}@",
                "    )",
            ]
        translate = component.get("translate") or [0.0, 0.0, 0.0]
        rotate = component.get("rotate") or [0.0, 0.0, 0.0]
        scale = component.get("scale") or [1.0, 1.0, 1.0]
        lines.extend(
            [
                *header,
                "    {",
                f'        custom string asset = "{component.get("asset", "")}"',
                f'        custom string variant = "{component.get("variant", "default")}"',
                f'        custom string reference_virtual = "{virtual}"',
                f"        double3 xformOp:translate = ({_float3(translate)})",
                f"        double3 xformOp:rotateXYZ = ({_float3(rotate)})",
                f"        double3 xformOp:scale = ({_float3(scale, default=(1.0, 1.0, 1.0))})",
                '        uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]',
                "    }",
            ]
        )
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


def _export_nodes_usd(cmds: Any, nodes: list[str], output: Path) -> str:
    existing = [node for node in nodes if cmds.objExists(node)]
    if not existing:
        return ""
    previous_selection = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(existing, replace=True)
        return _export_selected_usd(cmds, output)
    finally:
        if previous_selection:
            cmds.select(previous_selection, replace=True)
        else:
            cmds.select(clear=True)


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
    return paths.asset_variant_root(identity) / department / "work" / "maya" / subset / filename


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
) -> tuple[str, str]:
    warning = ""
    asset_name = _safe_name(component.asset)
    top_node = _unique_node(cmds, asset_name)
    if top_node != asset_name:
        warning = f"Could not create exact top node '{asset_name}'. Maya created '{top_node}'."
    top_node = cmds.group(empty=True, name=top_node)
    geo_node = cmds.group(empty=True, name="geo", parent=top_node)
    render_node = cmds.group(empty=True, name="render", parent=geo_node)
    duplicates = cmds.duplicate(nodes, returnRootsOnly=True) or []
    center = _bbox_center(cmds, duplicates) if center_to_origin and duplicates else [0.0, 0.0, 0.0]
    for node in duplicates:
        try:
            cmds.parent(node, render_node, absolute=True)
            if center_to_origin:
                translation = cmds.xform(node, query=True, worldSpace=True, translation=True)
                cmds.xform(
                    node,
                    worldSpace=True,
                    translation=[
                        float(translation[0]) - center[0],
                        float(translation[1]) - center[1],
                        float(translation[2]) - center[2],
                    ],
                )
        except Exception:
            pass
    return top_node, warning


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
) -> None:
    cmds = _maya_cmds()
    _set_string_attr(cmds, locator, TARGET_ATTR, _safe_name(target))
    _set_string_attr(cmds, locator, ASSET_ATTR, _safe_name(asset))
    _set_string_attr(cmds, locator, CATEGORY_ATTR, _safe_name(category))
    _set_string_attr(cmds, locator, GROUP_ATTR, _safe_name(group))
    _set_string_attr(cmds, locator, VARIANT_ATTR, _safe_name(variant or "default"))
    _set_string_attr(cmds, locator, SOURCE_NODES_ATTR, _encode_source_nodes(source_nodes))


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
