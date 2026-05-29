from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from smartlib.core.config_loader import load_config
from smartlib.core.metadata import read_json


def stage_shot_from_preview(
    preview_items: Iterable,
    shot_data: dict | None = None,
    *,
    department: str,
    project_root: str | Path | None = None,
) -> list[str]:
    """Open a shot work template, then reference resolved cast publishes."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Shot staging is available inside Maya.") from exc

    template = resolve_shot_work_template(department, project_root=project_root)
    if template:
        cmds.file(str(template), open=True, force=True)
    else:
        cmds.file(new=True, force=True)
    referenced = build_shot_from_preview(preview_items, shot_data)
    if _is_sequence_all_layout(shot_data or {}, department):
        referenced.extend(
            build_layout_sequence_all(
                shot_data or {},
                project_root=project_root,
            )
        )
    return referenced


def stage_sequence_layout_from_preview(
    preview_items: Iterable,
    sequence_data: dict,
    *,
    project_root: str | Path | None = None,
) -> list[str]:
    """Open a layout template, reference sequence cast, and build Maya Sequencer shots."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Sequence layout staging is available inside Maya.") from exc

    template = resolve_shot_work_template("layout", project_root=project_root)
    if template:
        cmds.file(str(template), open=True, force=True)
    else:
        cmds.file(new=True, force=True)
    referenced = build_shot_from_preview(preview_items, sequence_data)
    referenced.extend(build_layout_sequence_all(sequence_data, project_root=project_root))
    return referenced


def stage_anim_from_input(
    preview_items: Iterable,
    anim_input_path: str | Path,
    shot_data: dict | None = None,
    *,
    project_root: str | Path | None = None,
) -> list[str]:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Anim staging is available inside Maya.") from exc

    root = Path(project_root) if project_root else None
    if root is None:
        raise RuntimeError("project_root is required for anim staging.")
    anim_input = read_json(Path(anim_input_path), {}) or {}
    template = resolve_shot_work_template("anim", project_root=project_root)
    if template:
        cmds.file(str(template), open=True, force=True)
    else:
        cmds.file(new=True, force=True)
    anim_shot_data = _shot_data_from_anim_input(anim_input)
    referenced = build_shot_from_preview(preview_items, anim_shot_data)
    frame_offset = _anim_frame_offset(anim_input)
    camera_path = _project_path(root, str(anim_input.get("camera") or ""))
    if camera_path and camera_path.exists():
        if camera_path.name == "camera.json":
            camera = _create_camera_from_json(cmds, camera_path, anim_input, frame_offset)
            if camera:
                try:
                    cmds.parent(camera, _ensure_group(cmds, "camera_grp"))
                except Exception:
                    pass
                referenced.append(str(camera_path))
        else:
            camera_scene = _camera_scene_from_publish(camera_path)
            if camera_scene and camera_scene.exists():
                try:
                    imported = _import_file(cmds, camera_scene, _clean_namespace(str(anim_input.get("shot") or "camera")))
                except Exception:
                    imported = []
                if not imported:
                    camera_json = camera_scene.parent / "camera.json"
                    camera = _create_camera_from_json(cmds, camera_json, anim_input, frame_offset)
                    imported = [camera] if camera else []
                _parent_imported_top_nodes(cmds, imported, _ensure_group(cmds, "camera_grp"))
                _offset_animation_keys(cmds, imported, frame_offset)
                referenced.append(str(camera_scene))
    placement_nodes = _apply_anim_placements(cmds, root, anim_input)
    _offset_animation_keys(cmds, placement_nodes, frame_offset)
    _apply_shot_timing(cmds, anim_shot_data)
    return referenced


def resolve_shot_work_template(
    department: str,
    *,
    project_root: str | Path | None = None,
    pipeline_root: str | Path | None = None,
) -> Path | None:
    dept_filename = f"{department}_base.ma"
    filenames = (dept_filename, "shot_base.ma")
    roots = []
    if project_root:
        roots.append(Path(project_root) / "settings" / "templates" / "maya" / "shot")
    roots.append(Path(pipeline_root) if pipeline_root else Path(__file__).resolve().parents[4])
    for root in roots:
        template_root = root if root.name == "shot" else root / "templates" / "maya" / "shot"
        for filename in filenames:
            candidate = template_root / filename
            if candidate.exists():
                return candidate
    return None


def build_shot_from_preview(preview_items: Iterable, shot_data: dict | None = None) -> list[str]:
    """Reference resolved cast publishes into the current Maya scene."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Build Shot From Cast is available inside Maya.") from exc

    referenced = []
    for item in preview_items:
        if getattr(item, "status", "") != "resolved":
            continue
        publish_path = Path(getattr(item, "publish_path", ""))
        if not publish_path.exists():
            continue
        namespace = _clean_namespace(getattr(item, "namespace", "") or getattr(item, "cast_key", "") or publish_path.stem)
        before = set(cmds.ls(assemblies=True) or [])
        _reference_file(cmds, publish_path, namespace)
        group_name = _maya_reference_group_from_publish(publish_path)
        if group_name:
            _parent_new_assemblies(cmds, before, _ensure_group(cmds, group_name))
        referenced.append(str(publish_path))

    _apply_shot_timing(cmds, shot_data or {})
    return referenced


def build_layout_sequence_all(
    shot_data: dict,
    *,
    project_root: str | Path | None = None,
) -> list[str]:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Layout sequence staging is available inside Maya.") from exc

    root = Path(project_root) if project_root else None
    if root is None:
        raise RuntimeError("project_root is required for all-shot layout staging.")

    episode = str(shot_data.get("episode") or "").strip()
    sequence = str(shot_data.get("sequence") or "").strip()
    if not episode or not sequence:
        raise RuntimeError("all-shot layout staging requires episode and sequence in shot.json.")

    camera_rig = _resolve_camera_rig(root)
    if not camera_rig:
        raise FileNotFoundError(
            "Camera rig was not found. Expected: "
            f"{root / 'library' / 'layout' / 'camerarig' / 'camerarig.ma'}"
        )

    shots = _sequence_shot_rows(root, episode, sequence)
    storyreel_root = _latest_storyreel_root(root, episode, sequence)
    shots_grp = _ensure_group(cmds, "shots_grp")
    referenced = []
    for index, row in enumerate(shots):
        shot_name = row["shot"]
        namespace = _clean_namespace(shot_name)
        before = set(cmds.ls(assemblies=True) or [])
        _reference_file(cmds, camera_rig, namespace)
        _parent_new_assemblies(cmds, before, shots_grp)
        referenced.append(str(camera_rig))
        camera = _first_camera_in_namespace(cmds, namespace)
        storyreel = _storyreel_first_frame(storyreel_root, shot_name, row["cut_in"])
        image_plane = ""
        if camera and storyreel:
            try:
                cmds.currentTime(float(row["cut_in"]), edit=True)
            except Exception:
                pass
            image_plane = _attach_image_plane(cmds, camera, storyreel) or ""
        if camera:
            shot_node = _create_camera_sequencer_shot(cmds, row, camera, track=(index % 2) + 1)
            if shot_node and image_plane and storyreel:
                _connect_storyreel_to_camera_sequencer(cmds, shot_node, image_plane, storyreel, row)
    if shots:
        try:
            cmds.currentTime(float(shots[0]["cut_in"]), edit=True)
        except Exception:
            pass
    return referenced


def save_current_scene(path: str | Path, shot_data: dict | None = None) -> dict:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Shot work scene save is available inside Maya.") from exc
    from smartlib.dcc.maya.scene_info import collect_scene_info

    scene_path = Path(path)
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    _apply_shot_timing(cmds, shot_data or {})
    scene_type = "mayaBinary" if scene_path.suffix.lower() == ".mb" else "mayaAscii"
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, type=scene_type)
    return collect_scene_info(cmds)


def thumbnail_path_for_workfile(path: str | Path) -> Path:
    scene_path = Path(path)
    return scene_path.parent / ".thumbnails" / f"{scene_path.stem}.jpg"


def open_work_scene(path: str | Path, shot_data: dict | None = None) -> None:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Open Work Scene is available inside Maya.") from exc

    scene_path = Path(path)
    if not scene_path.exists():
        raise FileNotFoundError(f"Work scene was not found: {scene_path}")

    if cmds.file(query=True, modified=True):
        result = cmds.confirmDialog(
            title="Open Work Scene",
            message="Current scene has unsaved changes. Open selected work scene?",
            button=["Open", "Cancel"],
            defaultButton="Open",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if result != "Open":
            return

    cmds.file(str(scene_path), open=True, force=True)
    _apply_shot_timing(cmds, shot_data or {})


def create_review_display_layers(cast_data: dict) -> dict[str, int]:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Review layer creation is available inside Maya.") from exc

    cast = cast_data.get("cast") or {}
    review_layers = cast_data.get("review_layers") or {}
    created = {}
    for layer_name, layer in review_layers.items():
        layer_node = f"review_{layer_name}"
        if cmds.objExists(layer_node):
            cmds.delete(layer_node)
        cmds.createDisplayLayer(name=layer_node, empty=True)
        members = []
        for cast_key in layer.get("members", []):
            entry = cast.get(cast_key) or {}
            candidates = [
                str(entry.get("namespace") or ""),
                str(cast_key or ""),
                str(entry.get("asset") or ""),
            ]
            members.extend(_nodes_for_cast_entry(cmds, candidates))
        members = _unique_nodes(members)
        if members:
            cmds.editDisplayLayerMembers(layer_node, members, noRecurse=True)
        created[layer_node] = len(members)
    return created


def _nodes_for_cast_entry(cmds, candidates: list[str]) -> list[str]:
    for candidate in candidates:
        nodes = _namespace_nodes(cmds, candidate)
        if nodes:
            return nodes
    return []


def _namespace_nodes(cmds, namespace: str) -> list[str]:
    namespace = namespace.strip(":")
    if not namespace:
        return []

    for resolved_namespace in _matching_namespaces(cmds, namespace):
        nodes = _top_transforms_in_namespace(cmds, resolved_namespace)
        if nodes:
            return nodes
    return []


def _matching_namespaces(cmds, namespace: str) -> list[str]:
    exact = namespace.strip(":")
    matches = []
    if cmds.namespace(exists=exact):
        matches.append(exact)

    try:
        all_namespaces = cmds.namespaceInfo(":", listOnlyNamespaces=True, recurse=True) or []
    except RuntimeError:
        all_namespaces = []

    for item in all_namespaces:
        candidate = str(item).strip(":")
        leaf = candidate.rsplit(":", 1)[-1]
        if candidate == exact or leaf == exact or leaf.startswith(exact):
            if candidate not in matches:
                matches.append(candidate)
    return matches


def _top_transforms_in_namespace(cmds, namespace: str) -> list[str]:
    transforms = cmds.ls(f"{namespace}:*", type="transform", long=True) or []
    if not transforms:
        return []
    transform_set = set(transforms)
    roots = []
    for node in transforms:
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        if not parents or parents[0] not in transform_set:
            roots.append(node)
    return roots or transforms


def _unique_nodes(nodes: list[str]) -> list[str]:
    unique = []
    seen = set()
    for node in nodes:
        if node in seen:
            continue
        seen.add(node)
        unique.append(node)
    return unique


def _reference_file(cmds, path: Path, namespace: str) -> None:
    namespace = _unique_namespace(cmds, namespace)
    cmds.file(
        str(path),
        reference=True,
        namespace=namespace,
        ignoreVersion=True,
        mergeNamespacesOnClash=False,
        options="v=0;",
    )


def _import_file(cmds, path: Path, namespace: str) -> list[str]:
    before = set(cmds.ls(long=True) or [])
    cmds.file(
        str(path),
        i=True,
        namespace=namespace,
        ignoreVersion=True,
        mergeNamespacesOnClash=False,
        options="v=0;",
    )
    after = set(cmds.ls(long=True) or [])
    return sorted(after - before)


def _is_sequence_all_layout(shot_data: dict, department: str) -> bool:
    return str(shot_data.get("shot") or "").strip() == "all" and str(department or "").strip().lower() == "layout"


def _resolve_camera_rig(project_root: Path) -> Path | None:
    candidates = [
        project_root / "library" / "layout" / "camerarig" / "camerarig.ma",
    ]
    return next((path for path in candidates if path.exists()), None)


def _asset_metadata_from_publish(publish_path: Path) -> dict:
    for parent in [publish_path.parent, *publish_path.parents]:
        asset_json = parent / "asset.json"
        if not asset_json.exists():
            continue
        return read_json(asset_json, {}) or {}
    return {}


def _project_path(project_root: Path, path_text: str) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text)
    return path if path.is_absolute() else project_root / path


def _camera_scene_from_publish(path: Path) -> Path | None:
    if path.suffix.lower() in {".ma", ".mb"}:
        return path
    if path.is_dir():
        for filename in ("camera.ma", "camera.mb"):
            candidate = path / filename
            if candidate.exists():
                return candidate
        return None
    data = read_json(path, {}) or {}
    files = data.get("files") or {}
    for key in ("ma", "mb"):
        filename = str(files.get(key) or "")
        candidate = path.parent / filename
        if candidate.exists():
            return candidate
    if path.name == "camera.json":
        candidate = path.parent / "camera.ma"
        return candidate if candidate.exists() else None
    return None


def _create_camera_from_json(cmds, camera_json: Path, anim_input: dict, frame_offset: float) -> str:
    if not camera_json.exists():
        return ""
    data = read_json(camera_json, {}) or {}
    shot_name = _clean_namespace(str(anim_input.get("shot") or data.get("shot") or "shot"))
    camera_name = f"{shot_name}_anim_cam"
    if cmds.objExists(camera_name):
        cmds.delete(camera_name)
    camera, camera_shape = cmds.camera(name=camera_name)
    if data.get("lens") is not None:
        try:
            cmds.setAttr(f"{camera_shape}.focalLength", float(data["lens"]))
        except Exception:
            pass
    if data.get("fstop") is not None and cmds.objExists(f"{camera_shape}.fStop"):
        try:
            cmds.setAttr(f"{camera_shape}.fStop", float(data["fstop"]))
        except Exception:
            pass
    samples = data.get("animation") or []
    if samples:
        for sample in samples:
            try:
                frame = float(sample.get("frame")) + frame_offset
            except (TypeError, ValueError):
                continue
            matrix = sample.get("world_matrix")
            if isinstance(matrix, list) and len(matrix) == 16:
                try:
                    cmds.currentTime(frame, edit=True)
                    cmds.xform(camera, worldSpace=True, matrix=[float(value) for value in matrix])
                except Exception:
                    pass
            for attr, value in (("focalLength", sample.get("lens")), ("fStop", sample.get("fstop"))):
                target_attr = f"{camera_shape}.{attr}"
                if value is None or not cmds.objExists(target_attr):
                    continue
                try:
                    cmds.setAttr(target_attr, float(value))
                    cmds.setKeyframe(camera_shape, attribute=attr, time=frame)
                except Exception:
                    pass
            try:
                cmds.setKeyframe(camera, attribute=["translate", "rotate"], time=frame)
            except Exception:
                pass
    else:
        cut_range = anim_input.get("cut_range") or []
        if len(cut_range) >= 1:
            try:
                cmds.currentTime(float(cut_range[0]), edit=True)
                cmds.setKeyframe(camera, attribute=["translate", "rotate"], time=float(cut_range[0]))
            except Exception:
                pass
    return camera


def _shot_data_from_anim_input(anim_input: dict) -> dict:
    cut_range = anim_input.get("work_range") or anim_input.get("cut_range") or []
    editorial = {
        "fps": anim_input.get("fps"),
    }
    if len(cut_range) >= 2:
        editorial["cut_in"] = cut_range[0]
        editorial["cut_out"] = cut_range[1]
    return {
        "episode": anim_input.get("episode"),
        "sequence": anim_input.get("sequence"),
        "shot": anim_input.get("shot"),
        "editorial": editorial,
    }


def _apply_anim_placements(cmds, project_root: Path, anim_input: dict) -> list[str]:
    placements_path = _project_path(project_root, str(anim_input.get("placements") or ""))
    if not placements_path or not placements_path.exists():
        return []
    members_path = placements_path.parent / "placement_members.json"
    placements = read_json(placements_path, {}) or {}
    members = read_json(members_path, {}) or {}
    layout_grp = _ensure_group(cmds, "layout_grp")
    locator_by_name = {}
    created_locators = []
    for row in placements.get("placements") or []:
        locator_name = _clean_namespace(str(row.get("locator") or row.get("cast_id") or "placement"))
        if not locator_name.lower().endswith("_place_loc"):
            locator_name = f"{locator_name}_place_loc"
        locator = locator_name if cmds.objExists(locator_name) else cmds.spaceLocator(name=locator_name)[0]
        created_locators.append(locator)
        locator_by_name[str(row.get("locator") or locator_name)] = locator
    for row in placements.get("placements") or []:
        locator = locator_by_name.get(str(row.get("locator") or ""))
        if not locator:
            continue
        parent_name = str(row.get("parent") or "")
        parent = locator_by_name.get(parent_name) or layout_grp
        try:
            cmds.parent(locator, parent, absolute=True)
        except Exception:
            pass
    for row in placements.get("placements") or []:
        locator = locator_by_name.get(str(row.get("locator") or ""))
        if not locator:
            continue
        _apply_transform_from_placement(cmds, locator, row)
    for row in members.get("placements") or []:
        locator = locator_by_name.get(str(row.get("locator") or ""))
        member = str(row.get("member") or "")
        attach_root = str(row.get("attach_root") or "")
        if not locator or not member:
            continue
        target = _find_member_attach_target(cmds, member, attach_root)
        if not target:
            continue
        constraint_name = f"{target.replace(':', '_')}_placement_parentConstraint"
        existing = cmds.ls(constraint_name) or []
        if existing:
            try:
                cmds.delete(existing)
            except Exception:
                pass
        try:
            constraints = cmds.parentConstraint(locator, target, maintainOffset=False, name=constraint_name) or []
            if constraints:
                cmds.delete(constraints)
        except Exception:
            pass
    return created_locators


def _apply_transform_from_placement(cmds, node: str, row: dict) -> None:
    translate = row.get("translate") or [0, 0, 0]
    rotate = row.get("rotate") or [0, 0, 0]
    scale = row.get("scale") or [1, 1, 1]
    for attr, values in (("translate", translate), ("rotate", rotate), ("scale", scale)):
        if not isinstance(values, list) or len(values) < 3:
            continue
        for axis, value in zip("XYZ", values):
            try:
                cmds.setAttr(f"{node}.{attr}{axis}", float(value))
            except Exception:
                pass
    try:
        cmds.xform(node, edit=True, worldSpace=True, translation=translate)
    except Exception:
        pass
    try:
        cmds.xform(node, edit=True, worldSpace=True, rotation=rotate)
    except Exception:
        pass


def _find_member_attach_target(cmds, member: str, attach_root: str = "") -> str:
    candidates = []
    if attach_root:
        candidates.append(attach_root)
        if ":" not in attach_root:
            candidates.append(f"{member}:{attach_root}")
    candidates.extend(
        [
            f"{member}:world_ctl",
            f"{member}:*:world_ctl",
            f"{member}:global_ctl",
            f"{member}:*:global_ctl",
            f"{member}:root_ctl",
            f"{member}:*:root_ctl",
            f"{member}:root_grp",
            f"{member}:*:root_grp",
            f"{member}:ROOT",
            f"{member}:*:ROOT",
            f"{member}:*",
        ]
    )
    for pattern in candidates:
        matches = cmds.ls(pattern, type="transform", long=False) or []
        if matches:
            return matches[0]
    return ""


def _anim_frame_offset(anim_input: dict) -> float:
    source_range = anim_input.get("source_cut_range") or []
    cut_range = anim_input.get("cut_range") or []
    try:
        return float(cut_range[0]) - float(source_range[0])
    except (TypeError, ValueError, IndexError):
        return 0.0


def _offset_animation_keys(cmds, nodes: list[str], frame_offset: float) -> None:
    if not nodes or not frame_offset:
        return
    curves = set()
    for node in nodes:
        if not cmds.objExists(node):
            continue
        try:
            connections = cmds.listConnections(node, source=True, destination=False, type="animCurve") or []
            curves.update(connections)
        except Exception:
            pass
        descendants = cmds.listRelatives(node, allDescendents=True, fullPath=True) or []
        for descendant in descendants:
            try:
                connections = cmds.listConnections(descendant, source=True, destination=False, type="animCurve") or []
                curves.update(connections)
            except Exception:
                pass
    for curve in curves:
        try:
            cmds.keyframe(curve, edit=True, relative=True, timeChange=frame_offset)
        except Exception:
            pass


def _parent_imported_top_nodes(cmds, imported_nodes: list[str], parent: str) -> None:
    imported_set = set(imported_nodes)
    top_nodes = []
    for node in imported_nodes:
        if not cmds.objExists(node) or cmds.nodeType(node) != "transform":
            continue
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        if not parents or parents[0] not in imported_set:
            top_nodes.append(node)
    for node in sorted(set(top_nodes)):
        try:
            cmds.parent(node, parent)
        except Exception:
            pass


def _asset_category_from_publish(publish_path: Path) -> str:
    return str(_asset_metadata_from_publish(publish_path).get("category") or "").strip()


def _maya_reference_group_from_publish(publish_path: Path) -> str:
    metadata = _asset_metadata_from_publish(publish_path)
    if not metadata:
        return ""
    return _resolve_maya_reference_group(metadata, _maya_reference_group_config())


def _maya_reference_group_config() -> dict:
    config_dir = os.environ.get("PROJECT_CONFIG_DIR")
    if not config_dir:
        return {}
    return load_config(Path(config_dir) / "templates_assets.yml").get("maya_reference_groups") or {}


def _resolve_maya_reference_group(metadata: dict, config: dict) -> str:
    category = str(metadata.get("category") or "").strip()
    group_name = str(metadata.get("group") or metadata.get("group_name") or "").strip()
    asset_type = str(metadata.get("type") or metadata.get("asset_type") or "").strip()
    for rule in config.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        target = str(rule.get("group") or "").strip()
        if not target:
            continue
        if _rule_matches(rule, "category", category) or _rule_matches(rule, "group_name", group_name) or _rule_matches(rule, "asset_type", asset_type):
            return _clean_namespace(target)
    default = str(config.get("default") or "").strip()
    if default:
        return _clean_namespace(default)
    if category:
        return f"{_clean_namespace(category)}_grp"
    return ""


def _rule_matches(rule: dict, key: str, value: str) -> bool:
    if not value or key not in rule:
        return False
    expected = rule.get(key)
    if isinstance(expected, str):
        expected_values = [expected]
    elif isinstance(expected, list):
        expected_values = expected
    else:
        return False
    return value in {str(item).strip() for item in expected_values}


def _sequence_shot_rows(project_root: Path, episode: str, sequence: str) -> list[dict]:
    sequence_root = project_root / "shots" / episode / sequence
    rows = []
    for shot_json in sequence_root.glob("*/shot.json"):
        if shot_json.parent.name == "all":
            continue
        data = read_json(shot_json, {}) or {}
        editorial = data.get("editorial") or {}
        try:
            cut_in = int(editorial.get("cut_in"))
            cut_out = int(editorial.get("cut_out"))
        except (TypeError, ValueError):
            continue
        if cut_out < cut_in:
            continue
        rows.append(
            {
                "shot": str(data.get("shot") or shot_json.parent.name),
                "cut_in": cut_in,
                "cut_out": cut_out,
            }
        )
    return sorted(rows, key=lambda row: (row["cut_in"], row["shot"]))


def _latest_storyreel_root(project_root: Path, episode: str, sequence: str) -> Path | None:
    publish_root = project_root / "editorial" / "publish" / episode / sequence
    latest = read_json(publish_root / "latest.json", {}) or {}
    version = str(latest.get("version") or "").strip()
    if not version:
        return None
    storyreel = publish_root / version / "storyreel"
    return storyreel if storyreel.exists() else None


def _storyreel_first_frame(storyreel_root: Path | None, shot: str, cut_in: int) -> Path | None:
    if not storyreel_root:
        return None
    shot_dir = storyreel_root / shot
    preferred = shot_dir / f"storyreel_{cut_in:04d}.jpg"
    if _is_nonempty_file(preferred) and _qt_can_load_image(preferred):
        return preferred
    matches = sorted(shot_dir.glob("storyreel_*.jpg"))
    return next((path for path in matches if _is_nonempty_file(path) and _qt_can_load_image(path)), None)


def _first_camera_in_namespace(cmds, namespace: str) -> str:
    shapes = cmds.ls(f"{namespace}:*", type="camera") or []
    if not shapes:
        return ""
    parents = cmds.listRelatives(shapes[0], parent=True, fullPath=False) or []
    return parents[0] if parents else shapes[0]


def _attach_image_plane(cmds, camera: str, image_path: Path) -> str:
    if not _is_nonempty_file(image_path):
        return ""
    if not _qt_can_load_image(image_path):
        return ""
    try:
        result = cmds.imagePlane(camera=camera)
        shape = result[-1] if result else ""
        if shape and cmds.objExists(shape):
            cmds.setAttr(f"{shape}.displayMode", 3)
            if cmds.attributeQuery("displayOnlyIfCurrent", node=shape, exists=True):
                cmds.setAttr(f"{shape}.displayOnlyIfCurrent", True)
            if cmds.attributeQuery("depth", node=shape, exists=True):
                cmds.setAttr(f"{shape}.depth", 10)
            # Set the frame before enabling sequence loading, otherwise Maya may
            # try to resolve frame 1 and throw "Unable to load the image file".
            frame_number = _frame_number_from_path(image_path)
            if cmds.attributeQuery("frameExtension", node=shape, exists=True):
                if frame_number is not None:
                    cmds.setAttr(f"{shape}.frameExtension", frame_number)
            if cmds.attributeQuery("frameOffset", node=shape, exists=True):
                cmds.setAttr(f"{shape}.frameOffset", 0)
            if cmds.attributeQuery("imageName", node=shape, exists=True):
                cmds.setAttr(f"{shape}.imageName", image_path.as_posix(), type="string")
            if cmds.attributeQuery("useFrameExtension", node=shape, exists=True):
                cmds.setAttr(f"{shape}.useFrameExtension", False)
            return shape
    except Exception:
        return ""
    return ""


def _qt_can_load_image(path: Path) -> bool:
    try:
        from PySide6 import QtGui
    except ImportError:
        try:
            from PySide2 import QtGui
        except ImportError:
            return True
    image = QtGui.QImage(path.as_posix())
    return not image.isNull()


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _create_camera_sequencer_shot(cmds, row: dict, camera: str, track: int) -> str:
    shot_node = _unique_node_name(cmds, f"{row['shot']}_shot")
    try:
        cmds.shot(
            shot_node,
            startTime=float(row["cut_in"]),
            endTime=float(row["cut_out"]),
            sequenceStartTime=float(row["cut_in"]),
            sequenceEndTime=float(row["cut_out"]),
            currentCamera=camera,
            track=int(track),
        )
        return shot_node
    except Exception:
        return ""


def _connect_storyreel_to_camera_sequencer(cmds, shot_node: str, image_plane: str, image_path: Path, row: dict) -> None:
    first = _frame_number_from_path(image_path)
    if first is None:
        first = int(row.get("cut_in") or 1)
    last = first + max(0, int(row.get("cut_out") or row.get("cut_in") or first) - int(row.get("cut_in") or first))
    if cmds.attributeQuery("frameOffset", node=image_plane, exists=True):
        cmds.setAttr(f"{image_plane}.frameOffset", 0)
    if cmds.attributeQuery("frameExtension", node=image_plane, exists=True):
        cmds.setAttr(f"{image_plane}.frameExtension", first)
    if cmds.attributeQuery("clipZeroOffset", node=shot_node, exists=True):
        cmds.setAttr(f"{shot_node}.clipZeroOffset", first - 1)
    expression_name = _unique_node_name(cmds, f"{shot_node}_storyreel_expr")
    sequence_manager = _sequence_manager_node(cmds)
    expression = _storyreel_expression(sequence_manager, shot_node, image_plane, first, last)
    try:
        cmds.expression(name=expression_name, string=expression, alwaysEvaluate=True, unitConversion="all")
        if cmds.attributeQuery("useFrameExtension", node=image_plane, exists=True):
            cmds.setAttr(f"{image_plane}.useFrameExtension", True)
    except Exception:
        if cmds.attributeQuery("useFrameExtension", node=image_plane, exists=True):
            cmds.setAttr(f"{image_plane}.useFrameExtension", False)


def _sequence_manager_node(cmds) -> str:
    managers = cmds.ls(type="sequenceManager") or []
    return managers[0] if managers else "sequenceManager1"


def _storyreel_expression(sequence_manager: str, shot_node: str, image_plane: str, first: int, last: int) -> str:
    _ = sequence_manager
    return f"""
{{
    float $driverFrame = frame;
    float $elS = $driverFrame - {shot_node}.sequenceStartFrame;
    float $elE = {shot_node}.sequenceEndFrame - $driverFrame;
    float $scl = {shot_node}.clipScale / {shot_node}.scale;
    float $first = {shot_node}.clipZeroOffset + 1;
    if ({shot_node}.clipPreHold > 0)
        $first = {shot_node}.clipPreHold;
    if ($elS <= {shot_node}.preHold)
        {image_plane}.frameExtension = $first;
    else if ($elE < {shot_node}.postHold)
        {image_plane}.frameExtension = $first + (
            {shot_node}.sequenceEndFrame -
            {shot_node}.sequenceStartFrame -
            {shot_node}.postHold -
            {shot_node}.preHold + 1) * $scl - 1;
    else
        {image_plane}.frameExtension = $first + (
            $driverFrame -
            {shot_node}.sequenceStartFrame -
            {shot_node}.preHold) * $scl;
    if ({image_plane}.frameExtension < {first})
        {image_plane}.frameExtension = {first};
    if ({image_plane}.frameExtension > {last})
        {image_plane}.frameExtension = {last};
}}
""".strip()


def _unique_node_name(cmds, name: str) -> str:
    if not cmds.objExists(name):
        return name
    index = 1
    while cmds.objExists(f"{name}{index}"):
        index += 1
    return f"{name}{index}"


def _ensure_group(cmds, name: str) -> str:
    if cmds.objExists(name):
        return name
    return cmds.group(empty=True, name=name)


def _parent_new_assemblies(cmds, before: set[str], parent: str) -> None:
    after = set(cmds.ls(assemblies=True) or [])
    for node in sorted(after - before):
        if node == parent:
            continue
        try:
            cmds.parent(node, parent)
        except Exception:
            continue


def _frame_number_from_path(path: Path) -> int | None:
    stem = path.stem
    digits = ""
    for char in reversed(stem):
        if not char.isdigit():
            break
        digits = char + digits
    if not digits:
        return None
    return int(digits)


def _unique_namespace(cmds, namespace: str) -> str:
    namespace = _clean_namespace(namespace)
    if not cmds.namespace(exists=namespace):
        return namespace
    index = 1
    while cmds.namespace(exists=f"{namespace}{index}"):
        index += 1
    return f"{namespace}{index}"


def _clean_namespace(namespace: str) -> str:
    cleaned = "".join(char if char.isalnum() or char == "_" else "_" for char in namespace)
    if not cleaned:
        return "asset"
    if cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    return cleaned


def _apply_shot_timing(cmds, shot_data: dict) -> None:
    editorial = shot_data.get("editorial") or {}
    cut_in = editorial.get("cut_in")
    cut_out = editorial.get("cut_out")
    fps = editorial.get("fps")
    if fps:
        fps_map = {
            24: "film",
            25: "pal",
            30: "ntsc",
            48: "show",
            50: "palf",
            60: "ntscf",
        }
        cmds.currentUnit(time=fps_map.get(int(fps), f"{int(fps)}fps"))
    if cut_in is not None and cut_out is not None:
        cmds.playbackOptions(minTime=float(cut_in), animationStartTime=float(cut_in))
        cmds.playbackOptions(maxTime=float(cut_out), animationEndTime=float(cut_out))
