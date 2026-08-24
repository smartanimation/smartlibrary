from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.apps.shot_manager import ShotIdentity
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import configured_project_paths


PLACEMENT_ATTR = "smartPlacementLocator"
MEMBER_ATTR = "smartPlacementMember"
CAST_ID_ATTR = "smartPlacementCastId"
ATTACH_ROOT_ATTR = "smartPlacementAttachRoot"
CATEGORY_ATTR = "smartPlacementCategory"
GROUP_ATTR = "smartPlacementGroup"
MOTION_ATTR = "smartPlacementMotion"
MOTION_MODES = ("STATIC", "CURVE")


@dataclass(frozen=True)
class CastMember:
    name: str
    asset: str
    category: str = ""
    group: str = ""
    variant: str = "default"
    role: str = ""
    namespace: str = ""


@dataclass(frozen=True)
class PlacementLocator:
    node: str
    name: str
    category: str = ""
    group: str = ""
    member: str = ""
    attach_root: str = ""
    parent: str = ""
    motion: str = "STATIC"


@dataclass(frozen=True)
class PlacementAttachTarget:
    member: str
    namespace: str
    target: str
    mode: str
    source: str
    warning: str = ""


def list_cast_members(project_config: ProjectConfig) -> list[CastMember]:
    project_root = _project_root(project_config)
    cast_data = _context_cast_data(project_root)
    cast = cast_data.get("cast") or {}
    rows: list[CastMember] = []
    for cast_key, entry in sorted(cast.items()):
        if not isinstance(entry, dict):
            continue
        asset_name = str(entry.get("asset") or "").strip()
        asset_info = _asset_info(project_root, asset_name)
        rows.append(
            CastMember(
                name=str(cast_key),
                asset=asset_name,
                category=str(asset_info.get("category") or entry.get("category") or ""),
                group=str(asset_info.get("group") or entry.get("group") or ""),
                variant=str(entry.get("variant") or "default") or "default",
                role=str(entry.get("role") or ""),
                namespace=str(entry.get("namespace") or cast_key),
            )
        )
    return rows


def list_placement_locators() -> list[PlacementLocator]:
    cmds = _maya_cmds()
    locators: list[PlacementLocator] = []
    for transform in sorted(cmds.ls(type="transform") or []):
        if not _is_placement_locator(cmds, transform):
            continue
        parent = ""
        parents = cmds.listRelatives(transform, parent=True, fullPath=False) or []
        for candidate in parents:
            if _is_placement_locator(cmds, candidate):
                parent = candidate
                break
        locators.append(
            PlacementLocator(
                node=transform,
                name=transform.split("|")[-1],
                category=_get_string_attr(cmds, transform, CATEGORY_ATTR),
                group=_get_string_attr(cmds, transform, GROUP_ATTR),
                member=_get_string_attr(cmds, transform, MEMBER_ATTR),
                attach_root=_get_string_attr(cmds, transform, ATTACH_ROOT_ATTR),
                parent=parent,
                motion=_placement_motion(cmds, transform),
            )
        )
    return locators


def create_placement_locator(
    cast_id: str,
    *,
    category: str = "",
    group: str = "",
    parent: str = "",
) -> str:
    cmds = _maya_cmds()
    parent = parent or _ensure_layout_group(cmds)
    base_name = _placement_name(cast_id)
    if cmds.objExists(base_name):
        node = base_name
    else:
        node = cmds.spaceLocator(name=base_name)[0]
    _tag_placement_locator(cmds, node)
    _set_string_attr(cmds, node, CAST_ID_ATTR, cast_id)
    _set_string_attr(cmds, node, CATEGORY_ATTR, category)
    _set_string_attr(cmds, node, GROUP_ATTR, group)
    if not _get_string_attr(cmds, node, MOTION_ATTR):
        _set_string_attr(cmds, node, MOTION_ATTR, "STATIC")
    try:
        cmds.setAttr(f"{node}.localScaleX", 2.0)
        cmds.setAttr(f"{node}.localScaleY", 2.0)
        cmds.setAttr(f"{node}.localScaleZ", 2.0)
    except Exception:
        pass
    if parent and cmds.objExists(parent) and parent != node:
        try:
            cmds.parent(node, parent, absolute=True)
        except Exception:
            pass
    try:
        cmds.select(node, replace=True)
    except Exception:
        pass
    return node


def assign_member_to_placement(locator: str, member: CastMember, attach_root: str = "") -> str:
    cmds = _maya_cmds()
    if not locator or not cmds.objExists(locator):
        raise RuntimeError("Select a placement locator.")
    _tag_placement_locator(cmds, locator)
    _set_string_attr(cmds, locator, CAST_ID_ATTR, member.role or member.name)
    _set_string_attr(cmds, locator, MEMBER_ATTR, member.name)
    _set_string_attr(cmds, locator, CATEGORY_ATTR, member.category)
    _set_string_attr(cmds, locator, GROUP_ATTR, member.group)
    if not _get_string_attr(cmds, locator, MOTION_ATTR):
        _set_string_attr(cmds, locator, MOTION_ATTR, "STATIC")
    if attach_root:
        _set_string_attr(cmds, locator, ATTACH_ROOT_ATTR, attach_root)
    return locator


def set_placement_motion(locator: str, motion: str) -> str:
    """Declare whether this placement instance requires animation curves."""

    cmds = _maya_cmds()
    if not locator or not cmds.objExists(locator) or not _is_placement_locator(cmds, locator):
        raise RuntimeError("Select a placement locator.")
    clean_motion = str(motion or "").strip().upper()
    if clean_motion not in MOTION_MODES:
        raise ValueError(f"Unsupported placement motion: {motion}")
    _tag_placement_locator(cmds, locator)
    _set_string_attr(cmds, locator, MOTION_ATTR, clean_motion)
    return clean_motion


def add_assets_to_context_cast(project_config: ProjectConfig, assets: list[Any]) -> tuple[Path, list[dict[str, Any]]]:
    project_root = _project_root(project_config)
    context_root = _context_root(project_root)
    selections = [_asset_selection_payload(asset) for asset in assets]
    selections = [selection for selection in selections if selection.get("asset")]
    if not selections:
        raise RuntimeError("Select assets to add to cast.")

    from smartlib.apps.shot_manager import ShotManagerService

    service = ShotManagerService(project_config)
    if _is_sequence_context(context_root):
        return service.add_asset_selections_to_sequence_cast(
            context_root.parent.name,
            context_root.name,
            selections,
        )
    identity = ShotIdentity(
        episode=context_root.parent.parent.name,
        sequence=context_root.parent.name,
        shot=context_root.name,
    )
    return service.add_asset_selections_to_cast(identity, selections)


def reference_asset_to_scene(
    project_config: ProjectConfig,
    asset: Any,
    *,
    namespace: str = "",
) -> Path:
    cmds = _maya_cmds()
    from smartlib.dcc.maya import asset_assembly

    payload = _asset_selection_payload(asset)
    if not payload.get("asset"):
        raise RuntimeError("Select an asset to reference.")
    publish_path = asset_assembly.latest_asset_maya_reference(
        project_config,
        str(payload.get("category") or ""),
        str(payload.get("group") or ""),
        str(payload.get("asset") or ""),
        str(payload.get("variant") or "default"),
    )
    before = set(cmds.ls(assemblies=True) or [])
    resolved_namespace = _unique_namespace(cmds, namespace or payload.get("asset") or publish_path.stem)
    cmds.file(
        str(publish_path).replace("\\", "/"),
        reference=True,
        namespace=resolved_namespace,
        ignoreVersion=True,
        mergeNamespacesOnClash=False,
        options="v=0;",
    )
    _parent_new_assemblies(cmds, before, _ensure_cast_group(cmds))
    return publish_path


def reference_assets_to_scene(project_config: ProjectConfig, assets: list[Any]) -> list[Path]:
    referenced = []
    for asset in assets:
        referenced.append(reference_asset_to_scene(project_config, asset))
    return referenced


def add_and_reference_assets_to_context_cast(project_config: ProjectConfig, assets: list[Any]) -> tuple[Path, list[dict[str, Any]], list[Path]]:
    path, rows = add_assets_to_context_cast(project_config, assets)
    referenced = []
    for asset, row in zip(assets, rows):
        referenced.append(reference_asset_to_scene(project_config, asset, namespace=str(row.get("namespace") or row.get("cast_key") or "")))
    return path, rows, referenced


def constrain_member_to_placement(project_config: ProjectConfig, locator: str) -> PlacementAttachTarget:
    cmds = _maya_cmds()
    project_root = _project_root(project_config)
    if not locator or not cmds.objExists(locator) or not _is_placement_locator(cmds, locator):
        raise RuntimeError("Select a placement locator.")
    member_name = _get_string_attr(cmds, locator, MEMBER_ATTR)
    if not member_name:
        raise RuntimeError(f"Placement has no assigned cast member: {locator}")
    member = _cast_member_by_name(project_root, member_name)
    target = resolve_attach_target(project_config, member)
    if not target.target or not cmds.objExists(target.target):
        raise RuntimeError(f"Attach target was not found for {member.name}. Tried namespace: {member.namespace}")
    constraint_name = f"{_safe_node_name(target.target)}_placement_parentConstraint"
    existing = cmds.ls(constraint_name) or []
    if existing:
        try:
            cmds.delete(existing)
        except Exception:
            pass
    if target.mode in {"parent", "parentConstraint", ""}:
        cmds.parentConstraint(locator, target.target, maintainOffset=False, name=constraint_name)
    elif target.mode == "pointOrientConstraint":
        cmds.pointConstraint(locator, target.target, maintainOffset=False, name=f"{_safe_node_name(target.target)}_placement_pointConstraint")
        cmds.orientConstraint(locator, target.target, maintainOffset=False, name=f"{_safe_node_name(target.target)}_placement_orientConstraint")
    else:
        raise RuntimeError(f"Unsupported placement attach mode: {target.mode}")
    _set_string_attr(cmds, locator, ATTACH_ROOT_ATTR, target.target)
    return target


def resolve_attach_target(project_config: ProjectConfig, member: CastMember) -> PlacementAttachTarget:
    cmds = _maya_cmds()
    project_root = _project_root(project_config)
    metadata = _placement_metadata_for_member(project_root, member)
    placement_data = metadata.get("placement") if isinstance(metadata.get("placement"), dict) else {}
    target_name = str(placement_data.get("attach_target") or metadata.get("world_control") or "").strip()
    mode = str(placement_data.get("attach_mode") or "parentConstraint").strip()
    source = "metadata" if target_name else "fallback"
    warning = ""
    if target_name:
        target = _find_namespaced_node(cmds, member.namespace, target_name)
        if target:
            return PlacementAttachTarget(member=member.name, namespace=member.namespace, target=target, mode=mode, source=source)
        warning = f"Metadata attach target was not found: {target_name}"

    fallback = str(placement_data.get("fallback") or "").strip()
    target = _resolve_fallback_target(cmds, member, fallback)
    if not target:
        raise RuntimeError(f"Could not resolve placement attach target for {member.name}")
    return PlacementAttachTarget(
        member=member.name,
        namespace=member.namespace,
        target=target,
        mode=mode,
        source="fallback",
        warning=warning,
    )


def attach_selected_hierarchy(locator: str, attach_root: str = "") -> list[str]:
    cmds = _maya_cmds()
    if not locator or not cmds.objExists(locator):
        raise RuntimeError("Select a placement locator.")
    selected = [node for node in (cmds.ls(selection=True, long=False) or []) if node != locator]
    if not selected:
        raise RuntimeError("Select scene nodes to attach, then choose a placement locator.")
    attached = []
    for node in selected:
        if not cmds.objExists(node):
            continue
        try:
            cmds.parent(node, locator, absolute=True)
            attached.append(node)
        except Exception:
            continue
    if attach_root or attached:
        _set_string_attr(cmds, locator, ATTACH_ROOT_ATTR, attach_root or attached[0])
    return attached


def parent_placements(child: str, parent: str) -> None:
    set_parent_placement(child, parent)


def set_parent_placement(child: str, parent: str = "") -> None:
    cmds = _maya_cmds()
    if not child:
        raise RuntimeError("Select a child placement locator.")
    if child == parent:
        raise RuntimeError("Child and parent placement locators must be different.")
    if not cmds.objExists(child) or not _is_placement_locator(cmds, child):
        raise RuntimeError(f"Placement locator was not found: {child}")
    if not parent:
        cmds.parent(child, world=True)
        return
    for node in (parent,):
        if not cmds.objExists(node) or not _is_placement_locator(cmds, node):
            raise RuntimeError(f"Placement locator was not found: {node}")
    cmds.parent(child, parent, absolute=True)


def rename_placement_locator(node: str, new_name: str) -> str:
    cmds = _maya_cmds()
    if not node or not cmds.objExists(node) or not _is_placement_locator(cmds, node):
        raise RuntimeError(f"Placement locator was not found: {node}")
    clean_name = _placement_name(new_name)
    renamed = cmds.rename(node, clean_name)
    _tag_placement_locator(cmds, renamed)
    _set_string_attr(cmds, renamed, CAST_ID_ATTR, _strip_place_suffix(renamed.split("|")[-1]))
    return renamed


def delete_placements(nodes: list[str]) -> None:
    cmds = _maya_cmds()
    targets = [node for node in nodes if node and cmds.objExists(node) and _is_placement_locator(cmds, node)]
    if targets:
        cmds.delete(targets)


def export_metadata(project_config: ProjectConfig) -> tuple[Path, Path]:
    base_dir = _placement_export_dir(_project_root(project_config))
    version_label = _next_data_version(base_dir)
    export_dir = base_dir / version_label
    export_dir.mkdir(parents=True, exist_ok=True)
    placements_data, members_data = _collect_placement_metadata()
    placements_path = export_dir / "placements.json"
    members_path = export_dir / "placement_members.json"
    write_json(placements_path, placements_data)
    write_json(members_path, members_data)
    write_json(base_dir / "placements.json", {**placements_data, "version": version_label})
    write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/placements.json"})
    _update_versions(base_dir / "versions.json", version_label)
    return placements_path, members_path


def publish_placement(project_config: ProjectConfig, comment: str = "") -> Path:
    project_root = _project_root(project_config)
    base_dir = _placement_publish_dir(project_root)
    version_label = _next_data_version(base_dir)
    version_dir = base_dir / version_label
    version_dir.mkdir(parents=True, exist_ok=True)
    placements_data, members_data = _collect_placement_metadata()
    placements_path = version_dir / "placements.json"
    members_path = version_dir / "placement_members.json"
    write_json(placements_path, placements_data)
    write_json(members_path, members_data)
    publish_data = {
        "publish_type": "layout",
        "subset": "placements",
        "version": version_label,
        "files": {
            "placements": placements_path.name,
            "placement_members": members_path.name,
        },
        "comment": comment,
    }
    _add_context_to_publish_record(project_root, publish_data)
    write_json(version_dir / "publish.json", publish_data)
    write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/placements.json"})
    _update_versions(base_dir / "versions.json", version_label)
    return version_dir


def _collect_placement_metadata() -> tuple[dict[str, Any], dict[str, Any]]:
    cmds = _maya_cmds()
    placements = []
    members = []
    for row in list_placement_locators():
        translate = [float(v) for v in cmds.xform(row.node, query=True, worldSpace=True, translation=True)]
        rotate = [float(v) for v in cmds.xform(row.node, query=True, worldSpace=True, rotation=True)]
        scale = [float(v) for v in cmds.xform(row.node, query=True, relative=True, scale=True)]
        placements.append(
            {
                "cast_id": _get_string_attr(cmds, row.node, CAST_ID_ATTR) or _strip_place_suffix(row.name),
                "locator": row.name,
                "parent": row.parent,
                "translate": translate,
                "rotate": rotate,
                "scale": scale,
                "motion": row.motion,
            }
        )
        member = _get_string_attr(cmds, row.node, MEMBER_ATTR)
        if member:
            members.append(
                {
                    "locator": row.name,
                    "member": member,
                    "attach_root": _get_string_attr(cmds, row.node, ATTACH_ROOT_ATTR),
                    "attach_mode": "parentConstraint",
                    "motion": row.motion,
                }
            )
    return {"placements": placements}, {"placements": members}


def selected_placement_locator() -> str:
    cmds = _maya_cmds()
    for node in cmds.ls(selection=True, long=False) or []:
        if _is_placement_locator(cmds, node):
            return node
    return ""


def _placement_name(cast_id: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in cast_id.strip())
    clean = clean or "placement"
    if clean.lower().endswith("_place_loc"):
        return clean
    return f"{clean}_place_loc"


def _strip_place_suffix(name: str) -> str:
    return name[:-10] if name.lower().endswith("_place_loc") else name


def _context_cast_data(project_root: Path) -> dict[str, Any]:
    path = _context_root(project_root) / "cast.json"
    return read_json(path, {"cast": {}, "review_layers": {}})


def _cast_member_by_name(project_root: Path, member_name: str) -> CastMember:
    cast = (_context_cast_data(project_root).get("cast") or {})
    entry = cast.get(member_name)
    if not isinstance(entry, dict):
        raise RuntimeError(f"Cast member was not found in cast.json: {member_name}")
    asset_name = str(entry.get("asset") or "").strip()
    asset_info = _asset_info(project_root, asset_name)
    return CastMember(
        name=member_name,
        asset=asset_name,
        category=str(asset_info.get("category") or entry.get("category") or ""),
        group=str(asset_info.get("group") or entry.get("group") or ""),
        variant=str(entry.get("variant") or "default") or "default",
        role=str(entry.get("role") or ""),
        namespace=str(entry.get("namespace") or member_name),
    )


def _placement_metadata_for_member(project_root: Path, member: CastMember) -> dict[str, Any]:
    asset_root = _find_asset_root(project_root, member.asset)
    if not asset_root:
        return {}
    candidates = []
    variant_names = []
    cast = (_context_cast_data(project_root).get("cast") or {})
    entry = cast.get(member.name) if isinstance(cast.get(member.name), dict) else {}
    variant_names.append(str(entry.get("variant") or "default"))
    if "default" not in variant_names:
        variant_names.append("default")
    for variant in variant_names:
        variant_root = asset_root / variant
        candidates.extend(_latest_metadata_candidates(variant_root / "publish" / "rig"))
        candidates.extend(_latest_metadata_candidates(variant_root / "publish" / "asset"))
    for path in candidates:
        data = read_json(path, {}) or {}
        if not isinstance(data, dict):
            continue
        if data.get("placement") or data.get("world_control"):
            return data
    return {}


def _latest_metadata_candidates(publish_root: Path) -> list[Path]:
    candidates: list[Path] = []
    if not publish_root.exists():
        return candidates
    for latest_json in publish_root.glob("*/latest.json"):
        latest = read_json(latest_json, {}) or {}
        version = str(latest.get("version") or "").strip()
        version_dir = latest_json.parent / version if version else (latest_json.parent / str(latest.get("path") or "")).parent
        if not version_dir.exists():
            continue
        candidates.extend(
            [
                version_dir / "metadata" / "rig.json",
                version_dir / "metadata" / "placement.json",
                version_dir / "rig.json",
                version_dir / "publish.json",
                version_dir / "build_manifest.json",
            ]
        )
    return [path for path in candidates if path.exists()]


def _find_asset_root(project_root: Path, asset_name: str) -> Path | None:
    if not asset_name:
        return None
    assets_root = configured_project_paths(project_root).assets_root()
    matches = sorted(assets_root.glob(f"*/*/{asset_name}"))
    return matches[0] if matches else None


def _context_root(project_root: Path) -> Path:
    cmds = _maya_cmds()
    scene_text = cmds.file(query=True, sceneName=True) or ""
    if not scene_text:
        raise RuntimeError("Save or stage the scene inside a shot/sequence workspace first.")
    scene = Path(scene_text).resolve()
    paths = configured_project_paths(project_root)
    sequence_root = paths.sequences_root().resolve()
    shot_root = paths.shots_root().resolve()
    try:
        relative = scene.relative_to(sequence_root)
        if len(relative.parts) >= 2:
            return sequence_root / relative.parts[0] / relative.parts[1]
    except Exception:
        pass
    try:
        relative = scene.relative_to(shot_root)
        if len(relative.parts) >= 3:
            return shot_root / relative.parts[0] / relative.parts[1] / relative.parts[2]
    except Exception:
        pass
    raise RuntimeError(f"Scene is not under shots or sequences: {scene}")


def _placement_export_dir(project_root: Path) -> Path:
    root = _context_root(project_root)
    if root.parent.parent.name == "sequences":
        return root / "layout" / "data" / "placements"
    return root / "data" / "placements"


def _is_sequence_context(context_root: Path) -> bool:
    return context_root.parent.parent.name == "sequences"


def _placement_publish_dir(project_root: Path) -> Path:
    root = _context_root(project_root)
    if root.parent.parent.name == "sequences":
        return root / "publish" / "layout" / "placements"
    return root / "publish" / "layout" / "placements"


def _add_context_to_publish_record(project_root: Path, publish_data: dict[str, Any]) -> None:
    root = _context_root(project_root)
    if root.parent.parent.name == "sequences":
        publish_data["episode"] = root.parent.name
        publish_data["sequence"] = root.name
        publish_data["scope"] = "sequence"
        return
    publish_data["episode"] = root.parent.parent.name
    publish_data["sequence"] = root.parent.name
    publish_data["shot"] = root.name
    publish_data["scope"] = "shot"


def _next_data_version(base_dir: Path) -> str:
    max_version = 0
    if base_dir.exists():
        for path in base_dir.iterdir():
            if path.is_dir() and path.name.lower().startswith("v") and path.name[1:].isdigit():
                max_version = max(max_version, int(path.name[1:]))
    return f"v{max_version + 1:03d}"


def _update_versions(path: Path, version_label: str) -> None:
    versions = read_json(path, []) if path.exists() else []
    if not isinstance(versions, list):
        versions = []
    next_versions = []
    seen = False
    for item in versions:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        if item.get("version") == version_label:
            item["status"] = "latest"
            seen = True
        elif item.get("status") == "latest":
            item["status"] = "available"
        next_versions.append(item)
    if not seen:
        next_versions.append({"version": version_label, "status": "latest"})
    write_json(path, next_versions)


def _asset_info(project_root: Path, asset_name: str) -> dict[str, Any]:
    if not asset_name:
        return {}
    assets_root = configured_project_paths(project_root).assets_root()
    if not assets_root.exists():
        return {}
    for asset_json in assets_root.glob("*/*/*/asset.json"):
        data = read_json(asset_json, {}) or {}
        if str(data.get("asset") or asset_json.parent.name) == asset_name:
            return data
    return {}


def _find_namespaced_node(cmds: Any, namespace: str, node_name: str) -> str:
    if not node_name:
        return ""
    candidates = []
    if namespace:
        candidates.extend([f"{namespace}:{node_name}", f"{namespace}:*:{node_name}", f"{namespace}:*{node_name}"])
    candidates.append(node_name)
    for pattern in candidates:
        matches = cmds.ls(pattern, long=False) or []
        if matches:
            return matches[0]
    return ""


def _resolve_fallback_target(cmds: Any, member: CastMember, fallback: str = "") -> str:
    fallback_names = []
    if fallback and fallback not in {"root_transform", "top_node"}:
        fallback_names.append(fallback)
    if member.category in {"prop", "env", "environment", "set"}:
        fallback_names.extend([member.asset, "root", "root_grp", "ROOT", "asset_root"])
    else:
        fallback_names.extend(["world_ctl", "global_ctl", "root_ctl", member.asset, "root_grp", "ROOT"])
    for name in fallback_names:
        target = _find_namespaced_node(cmds, member.namespace, name)
        if target:
            return target
    if member.namespace:
        assemblies = cmds.ls(f"{member.namespace}:*", assemblies=True) or []
        if assemblies:
            return assemblies[0]
    return ""


def _asset_selection_payload(asset: Any) -> dict[str, Any]:
    if isinstance(asset, dict):
        return {
            "category": str(asset.get("category") or ""),
            "group": str(asset.get("group") or ""),
            "asset": str(asset.get("asset") or asset.get("name") or ""),
            "variant": str(asset.get("variant") or "default") or "default",
        }
    return {
        "category": str(getattr(asset, "category", "") or ""),
        "group": str(getattr(asset, "group", "") or ""),
        "asset": str(getattr(asset, "asset", "") or getattr(asset, "name", "") or ""),
        "variant": str(getattr(asset, "variant", "default") or "default") or "default",
    }


def _ensure_cast_group(cmds: Any) -> str:
    group = "cast_grp"
    if cmds.objExists(group):
        return group
    return cmds.group(empty=True, name=group)


def _parent_new_assemblies(cmds: Any, before: set[str], parent: str) -> None:
    after = set(cmds.ls(assemblies=True) or [])
    for node in sorted(after - before):
        if not cmds.objExists(node) or node == parent:
            continue
        try:
            cmds.parent(node, parent, absolute=True)
        except Exception:
            pass


def _unique_namespace(cmds: Any, namespace: Any) -> str:
    base = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(namespace or "asset")).strip("_") or "asset"
    candidate = base
    index = 2
    while cmds.namespace(exists=candidate):
        candidate = f"{base}{index}"
        index += 1
    return candidate


def _safe_node_name(node: str) -> str:
    return node.replace("|", "_").replace(":", "_")


def _ensure_layout_group(cmds: Any) -> str:
    group = "layout_grp"
    if cmds.objExists(group):
        return group
    return cmds.group(empty=True, name=group)


def _tag_placement_locator(cmds: Any, node: str) -> None:
    if not cmds.objExists(f"{node}.{PLACEMENT_ATTR}"):
        cmds.addAttr(node, longName=PLACEMENT_ATTR, attributeType="bool")
    try:
        cmds.setAttr(f"{node}.{PLACEMENT_ATTR}", True)
    except Exception:
        pass
    for attr in (CAST_ID_ATTR, MEMBER_ATTR, ATTACH_ROOT_ATTR, CATEGORY_ATTR, GROUP_ATTR, MOTION_ATTR):
        if not cmds.objExists(f"{node}.{attr}"):
            cmds.addAttr(node, longName=attr, dataType="string")


def _placement_motion(cmds: Any, node: str) -> str:
    motion = _get_string_attr(cmds, node, MOTION_ATTR).strip().upper()
    return motion if motion in MOTION_MODES else "STATIC"


def _is_placement_locator(cmds: Any, node: str) -> bool:
    if not cmds.objExists(node):
        return False
    if cmds.objExists(f"{node}.{PLACEMENT_ATTR}"):
        try:
            return bool(cmds.getAttr(f"{node}.{PLACEMENT_ATTR}"))
        except Exception:
            return True
    return node.split("|")[-1].lower().endswith("_place_loc")


def _set_string_attr(cmds: Any, node: str, attr: str, value: str) -> None:
    if not cmds.objExists(f"{node}.{attr}"):
        cmds.addAttr(node, longName=attr, dataType="string")
    cmds.setAttr(f"{node}.{attr}", str(value or ""), type="string")


def _get_string_attr(cmds: Any, node: str, attr: str) -> str:
    if not cmds.objExists(f"{node}.{attr}"):
        return ""
    try:
        return str(cmds.getAttr(f"{node}.{attr}") or "")
    except Exception:
        return ""


def _project_root(project_config: ProjectConfig) -> Path:
    root = project_config.project_root
    if root is None:
        raise RuntimeError("project_root is not set in templates_base.yml")
    return root


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Smart Maker is available inside Maya.") from exc
    return cmds
