from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from smartlib.core.config_loader import ProjectConfig, load_config
from smartlib.core.metadata import read_json
from smartlib.core.path_resolver import configured_project_paths


def stage_shot_from_preview(
    preview_items: Iterable,
    shot_data: dict | None = None,
    *,
    department: str,
    project_root: str | Path | None = None,
    construct_data: dict | None = None,
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
    _apply_scene_policy(cmds, shot_data or {})
    if _construct_enabled(construct_data, "audio", "main"):
        _load_shot_audio(cmds, Path(project_root) if project_root else None, shot_data or {})
    referenced = build_shot_from_preview(preview_items, shot_data)
    if project_root and construct_data:
        _apply_construct_cameras(
            cmds,
            Path(project_root),
            shot_data or {},
            construct_data,
            0.0,
        )
        # WORK STAGE is data-driven regardless of department or Rig publish
        # representation. Apply curves after Rig references exist and resolve
        # only the destinations actually present in the selected Rig.
        _apply_construct_animation_curves(Path(project_root), construct_data)
    if project_root and str(department or "").strip().lower() == "layout":
        _attach_shot_storyreel_picture_in_picture(
            cmds,
            Path(project_root),
            shot_data or {},
        )
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
    shot_names: Iterable[str] | None = None,
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
    _apply_scene_policy(cmds, sequence_data)
    referenced = build_shot_from_preview(preview_items, sequence_data)
    referenced.extend(
        build_layout_sequence_all(
            sequence_data,
            project_root=project_root,
            shot_names=shot_names,
        )
    )
    return referenced


def stage_anim_from_input(
    preview_items: Iterable,
    anim_input_path: str | Path,
    shot_data: dict | None = None,
    *,
    project_root: str | Path | None = None,
    construct_data: dict | None = None,
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
    # The versioned Editorial Timing Data is authoritative when supplied by
    # Review Build Manager. Older Animation Inputs remain the compatibility
    # fallback for shots that have not migrated yet.
    anim_shot_data = shot_data or _shot_data_from_anim_input(anim_input)
    _apply_scene_policy(cmds, anim_shot_data)
    referenced = build_shot_from_preview(preview_items, anim_shot_data)
    # Shot Context USD is an alternative cast representation. Loading it next
    # to Maya rig references duplicates backgrounds and makes Maya-first
    # projects appear to contain an unloaded reference.
    context_proxy = (
        _load_context_usd(cmds, root, anim_input)
        if _construct_uses_usd(construct_data)
        else ""
    )
    if context_proxy:
        referenced.append(context_proxy)
    frame_offset = _anim_frame_offset(anim_input)
    camera_path = _project_path(root, str(anim_input.get("camera") or ""))
    direct_cameras = [
        component
        for component in _enabled_construct_components(construct_data, "camera")
        if str((component.get("source") or {}).get("kind") or "")
        in {"published_camera", "scene_data", "shot_dependency"}
    ]
    if not direct_cameras and _construct_enabled(construct_data, "camera", "camera", "camera") and camera_path and camera_path.exists():
        if camera_path.name == "camera.json":
            camera = _create_camera_from_json(cmds, camera_path, anim_input, frame_offset)
            if camera:
                try:
                    cmds.parent(camera, _ensure_group(cmds, "camera_grp"))
                except Exception:
                    pass
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
    if _construct_enabled(construct_data, "placement", "placements", "placements"):
        placement_nodes = _apply_anim_placements(cmds, root, anim_input)
        _offset_animation_keys(cmds, placement_nodes, frame_offset)
    if not context_proxy and _construct_enabled(construct_data, "layout_overlay", "layout_overlay", "layout_overlay"):
        layout_overlay = _load_layout_overlay_usd(cmds, root, anim_input)
        if layout_overlay:
            referenced.append(layout_overlay)
    _apply_construct_cameras(cmds, root, anim_input, construct_data, frame_offset)
    _apply_construct_lights(cmds, root, construct_data)
    _apply_construct_playblast_settings(cmds, root, construct_data)
    _apply_construct_set_dress(root, construct_data)
    _apply_construct_animation_curves(root, construct_data)
    _apply_shot_timing(cmds, anim_shot_data)
    if _construct_enabled(construct_data, "audio", "main"):
        _load_shot_audio(cmds, root, anim_shot_data)
    return referenced


def update_anim_construct(
    scene_path: str | Path,
    preview_items: Iterable,
    anim_input_path: str | Path,
    shot_data: dict | None,
    *,
    project_root: str | Path,
    construct_data: dict,
    construct_diff: list[dict],
) -> list[str]:
    """Open an existing Construct and apply only selected component changes."""
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Construct update is available inside Maya.") from exc

    scene = Path(scene_path)
    if not scene.is_file():
        raise FileNotFoundError(scene)
    root = Path(project_root)
    anim_input = read_json(Path(anim_input_path), {}) or {}
    selected = [
        row for row in construct_diff
        if bool(row.get("selected", True)) and row.get("change") != "UNCHANGED"
    ]
    selected_types = {
        str(((row.get("after") or row.get("before") or {}).get("component_type") or "")).lower()
        for row in selected
    }
    selected_rigs = {
        str(((row.get("after") or row.get("before") or {}).get("name") or ""))
        for row in selected
        if str(((row.get("after") or row.get("before") or {}).get("component_type") or "")).lower() == "rig"
    }

    cmds.file(str(scene), open=True, force=True)
    for row in selected:
        component = row.get("before") or row.get("after") or {}
        if str(component.get("component_type") or "").lower() != "rig":
            continue
        namespace = str(component.get("namespace") or "").strip(":")
        if not namespace:
            continue
        for reference_node in cmds.ls(type="reference") or []:
            if reference_node == "sharedReferenceNode":
                continue
            try:
                reference_namespace = str(
                    cmds.referenceQuery(reference_node, namespace=True) or ""
                ).strip(":")
                if reference_namespace == namespace:
                    cmds.file(removeReference=True, referenceNode=reference_node)
            except Exception:
                continue
    changed_preview = [
        item for item in preview_items
        if str(getattr(item, "cast_key", "")) in selected_rigs
    ]
    referenced = build_shot_from_preview(changed_preview, shot_data or {})
    frame_offset = _anim_frame_offset(anim_input)
    if "camera" in selected_types:
        _apply_construct_cameras(cmds, root, anim_input, construct_data, frame_offset)
    if "light" in selected_types:
        _apply_construct_lights(cmds, root, construct_data)
    if "playblast_settings" in selected_types:
        _apply_construct_playblast_settings(cmds, root, construct_data)
    if "set_dress" in selected_types:
        _apply_construct_set_dress(root, construct_data)
    if "animation_curve" in selected_types:
        _apply_construct_animation_curves(root, construct_data)
    _apply_shot_timing(cmds, shot_data or _shot_data_from_anim_input(anim_input))
    if _construct_enabled(construct_data, "audio", "main"):
        _load_shot_audio(cmds, root, shot_data or _shot_data_from_anim_input(anim_input))
    return referenced


def build_animation_review_scene(
    plan: dict,
    shot_data: dict,
    static_preview_items: Iterable,
    *,
    project_root: str | Path,
) -> dict:
    """Reconstruct a generated Maya review scene from an Animation Package."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Animation Review Scene build is available inside Maya.") from exc

    root = Path(project_root)
    manifest_path = Path(str(plan.get("animation_manifest") or ""))
    manifest = read_json(manifest_path, {}) or {}
    if not manifest.get("casts"):
        raise RuntimeError(f"Animation Package has no cast caches: {manifest_path}")

    cmds.file(new=True, force=True)
    _apply_scene_policy(cmds, shot_data or {})
    _apply_shot_timing(cmds, shot_data or {})
    animation_group = _ensure_group(cmds, "animation_grp")
    shots_group = _ensure_group(cmds, "shots_grp")

    static_items = list(static_preview_items)
    static_references = build_shot_from_preview(static_items, shot_data or {}) if static_items else []
    static_nodes: dict[str, list[str]] = {}
    for item in static_items:
        cast_key = str(getattr(item, "cast_key", "") or "").strip()
        namespace = _clean_namespace(
            getattr(item, "namespace", "") or cast_key
        )
        if not cast_key or not namespace:
            continue
        transforms = cmds.ls(f"{namespace}:*", type="transform", long=True) or []
        roots = []
        for node in transforms:
            parent = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if not parent or not str(parent[0]).split("|")[-1].startswith(f"{namespace}:"):
                roots.append(node)
        static_nodes[cast_key] = sorted(set(roots or transforms))
    imported_caches = []
    cache_nodes: dict[str, list[str]] = {}
    try:
        if not cmds.pluginInfo("AbcImport", query=True, loaded=True):
            cmds.loadPlugin("AbcImport", quiet=True)
    except Exception as exc:
        raise RuntimeError(f"Could not load AbcImport: {exc}") from exc

    preferred = str(plan.get("preferred_format") or manifest.get("preferred_format") or "abc").lower()
    for cast_key, cast_data in sorted((manifest.get("casts") or {}).items()):
        files = dict(cast_data.get("files") or {})
        order = [preferred] + [name for name in ("abc", "usd") if name != preferred]
        cache_path = None
        cache_type = ""
        for file_type in order:
            candidate = _project_path(root, str(files.get(file_type) or ""))
            if candidate and candidate.is_file():
                cache_path = candidate
                cache_type = file_type
                break
        if not cache_path:
            raise RuntimeError(f"No cache file was found for cast: {cast_key}")
        if cache_type != "abc":
            raise RuntimeError(
                f"Animation Review Scene currently requires Alembic. "
                f"ABC was not found for {cast_key}: {cache_path.parent}"
            )
        before = set(cmds.ls(assemblies=True, long=True) or [])
        cmds.AbcImport(str(cache_path), mode="import")
        after = set(cmds.ls(assemblies=True, long=True) or [])
        created = sorted(after - before)
        cast_group = cmds.group(
            empty=True,
            name=_unique_node_name(cmds, f"{_clean_namespace(cast_key)}_cache_grp"),
            parent=animation_group,
        )
        parented = []
        for node in created:
            if node == f"|{animation_group}" or node == animation_group:
                continue
            try:
                cmds.parent(node, cast_group)
            except Exception:
                pass
        parented = cmds.listRelatives(
            cast_group,
            children=True,
            fullPath=True,
        ) or []
        cache_nodes[cast_key] = parented
        imported_caches.append(str(cache_path))

    cameras = []
    from smartlib.dcc.maya.shot_scene_data import apply_camera_data

    for raw_path in plan.get("camera_paths") or []:
        camera_path = Path(str(raw_path))
        if camera_path.name == "publish.json":
            publish_data = read_json(camera_path, {}) or {}
            camera_name = str((publish_data.get("files") or {}).get("camera") or "camera.json")
            camera_path = camera_path.parent / camera_name
        if camera_path.name != "camera.json" or not camera_path.is_file():
            continue
        camera = apply_camera_data(read_json(camera_path, {}) or {})
        try:
            cmds.parent(camera, shots_group)
        except Exception:
            pass
        cameras.append(camera)

    set_dress_warnings = []
    if plan.get("set_dress_paths"):
        from smartlib.dcc.maya import set_dress, set_dress_usd

        for raw_path in plan.get("set_dress_paths") or []:
            package = set_dress.load_package(raw_path)
            for target, backend in (("maya", set_dress), ("usd", set_dress_usd)):
                layers = [layer for layer in package.layers if layer.target == target]
                is_usd = target == "usd"
                base = [state for state in package.base if ("," in state.node_id) == is_usd]
                if layers or base:
                    set_dress_warnings.extend(backend.apply_stack(layers, base=base))

    frame_range = plan.get("frame_range") or []
    if len(frame_range) >= 2:
        cmds.playbackOptions(
            minTime=float(frame_range[0]),
            maxTime=float(frame_range[1]),
            animationStartTime=float(frame_range[0]),
            animationEndTime=float(frame_range[1]),
        )
        cmds.currentTime(float(frame_range[0]), edit=True)

    scene_path = Path(str(plan.get("scene_path") or ""))
    if not scene_path:
        raise RuntimeError("Animation Review Scene output path was not resolved.")
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=str(scene_path))
    scene_type = "mayaBinary" if scene_path.suffix.lower() == ".mb" else "mayaAscii"
    cmds.file(save=True, type=scene_type)
    return {
        "scene_path": str(scene_path),
        "animation_manifest": str(manifest_path),
        "imported_caches": imported_caches,
        "cache_nodes": cache_nodes,
        "static_nodes": static_nodes,
        "static_references": static_references,
        "cameras": cameras,
        "set_dress_warnings": set_dress_warnings,
        "frame_range": list(frame_range),
    }


def resolve_shot_work_template(
    department: str,
    *,
    project_root: str | Path | None = None,
    pipeline_root: str | Path | None = None,
) -> Path | None:
    dept_filename = f"{department}_base.ma"
    filenames = (dept_filename, "shot_base.ma")
    configured_candidates = []
    config_dir = os.environ.get("PROJECT_CONFIG_DIR")
    if config_dir:
        template_config = load_config(Path(config_dir) / "templates_base.yml")
        shot_templates = (
            ((template_config.get("template_files") or {}).get("maya") or {}).get("shot")
            or {}
        )
        configured_values = [
            (shot_templates.get("departments") or {}).get(str(department).lower()),
            shot_templates.get("base"),
        ]
        resolved_project_root = str(Path(project_root)) if project_root else ""
        resolved_pipeline_root = str(
            Path(pipeline_root) if pipeline_root else Path(__file__).resolve().parents[4]
        )
        for value in configured_values:
            text = str(value or "").strip()
            if not text:
                continue
            text = text.replace("{project_root}", resolved_project_root)
            text = text.replace("{pipeline_root}", resolved_pipeline_root)
            configured_candidates.append(Path(os.path.expandvars(text)))
    for candidate in configured_candidates:
        if candidate.is_file():
            return candidate

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
    shot_names: Iterable[str] | None = None,
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
    selected_shots = {
        str(shot).strip() for shot in (shot_names or []) if str(shot).strip()
    }
    if selected_shots:
        shots = [row for row in shots if str(row.get("shot") or "") in selected_shots]
        resolved_shots = {str(row.get("shot") or "") for row in shots}
        missing_shots = sorted(selected_shots - resolved_shots)
        if missing_shots:
            raise RuntimeError(
                "Selected shots were not found in the sequence: "
                + ", ".join(missing_shots)
            )
    storyreel_root = _latest_storyreel_root(root, episode, sequence)
    shots_grp = _ensure_group(cmds, "shots_grp")
    referenced = []
    created_shots = []
    for index, row in enumerate(shots):
        shot_name = row["shot"]
        namespace = _clean_namespace(shot_name)
        before = set(cmds.ls(assemblies=True) or [])
        cameras_before = set(cmds.ls(type="camera", long=True) or [])
        actual_namespace = _reference_file(cmds, camera_rig, namespace)
        _parent_new_assemblies(cmds, before, shots_grp)
        referenced.append(str(camera_rig))
        camera = _first_new_camera(cmds, cameras_before)
        if not camera:
            camera = _first_camera_in_namespace(cmds, actual_namespace)
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
            if shot_node:
                created_shots.append(shot_node)
            if shot_node and image_plane and storyreel:
                _connect_storyreel_to_camera_sequencer(cmds, shot_node, image_plane, storyreel, row)
    if len(created_shots) != len(shots):
        missing = [
            str(row["shot"])
            for row in shots
            if not any(node == f"{row['shot']}_shot" or node.startswith(f"{row['shot']}_shot") for node in created_shots)
        ]
        raise RuntimeError(
            "Camera Sequencer construction was incomplete. "
            f"Expected {len(shots)} shots, created {len(created_shots)}. "
            f"Missing: {', '.join(missing) or 'unknown'}"
        )
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


def archive_current_scene(path: str | Path, shot_data: dict | None = None) -> dict:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Shot scene archive is available inside Maya.") from exc
    from smartlib.dcc.maya.scene_info import collect_scene_info

    archive_path = Path(path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    original_path = cmds.file(query=True, sceneName=True) or ""
    _apply_shot_timing(cmds, shot_data or {})
    scene_type = "mayaBinary" if archive_path.suffix.lower() == ".mb" else "mayaAscii"
    cmds.file(rename=str(archive_path))
    cmds.file(save=True, type=scene_type)
    scene_info = collect_scene_info(cmds)
    if original_path:
        cmds.file(rename=original_path)
    return scene_info


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
    _repair_sequence_camera_namespaces(cmds, shot_data or {})
    reconnect_scene_audio_to_time_slider(cmds)


def ensure_smart_gate_guide(cmds=None) -> str:
    """Ensure exactly one build-owned SmartGateGuide is present in the scene."""

    if cmds is None:
        try:
            import maya.cmds as cmds
        except ImportError as exc:
            raise RuntimeError("SmartGateGuide creation is available inside Maya.") from exc

    try:
        shapes = cmds.ls(type="SmartViewportGateGuide", long=True) or []
    except Exception:
        shapes = []
    if shapes:
        parents = cmds.listRelatives(shapes[0], parent=True, fullPath=True) or []
        return str(parents[0] if parents else shapes[0])

    from smartlib.dcc.maya.smart_menu import ensure_smart_gate_guide_plugin

    ensure_smart_gate_guide_plugin(cmds, required=True)
    if not hasattr(cmds, "SmartGateGuide"):
        raise RuntimeError("SmartGateGuide command is not registered after plugin load.")
    try:
        selection = list(cmds.ls(selection=True, long=True) or [])
    except Exception:
        selection = []
    result = cmds.SmartGateGuide()
    node = result[0] if isinstance(result, (list, tuple)) and result else result
    try:
        if selection:
            cmds.select(selection, replace=True)
        else:
            cmds.select(clear=True)
    except Exception:
        pass
    return str(node or "")


def reconnect_scene_audio_to_time_slider(cmds=None, mel_eval=None) -> str:
    """Bind the scene's preferred audio node to Maya's playback slider.

    The binding is UI state and is not reliably preserved by a scene assembled in
    mayapy.  Shot Manager calls this after opening both work and construct scenes.
    """

    if cmds is None:
        try:
            import maya.cmds as cmds
        except ImportError:
            return ""

    try:
        audio_nodes = [str(node) for node in (cmds.ls(type="audio") or [])]
    except Exception:
        return ""
    if not audio_nodes:
        return ""

    preferred_name = "smartEditorialAudio"
    node = next(
        (
            candidate
            for candidate in audio_nodes
            if candidate.rsplit("|", 1)[-1].rsplit(":", 1)[-1] == preferred_name
        ),
        audio_nodes[0],
    )

    try:
        if mel_eval is None:
            import maya.mel as mel

            mel_eval = mel.eval
        slider = mel_eval("$tmpVar=$gPlayBackSlider")
        if not slider:
            return ""
        cmds.timeControl(slider, edit=True, sound=node, displaySound=True)
    except Exception:
        # mayapy has no playback slider. The audio node remains valid in the scene
        # and will be connected the next time Shot Manager opens it in Maya.
        return ""
    return node


def _repair_sequence_camera_namespaces(cmds, sequence_data: dict) -> dict[str, str]:
    """Restore the shot-code namespace contract after Maya file-open callbacks."""

    shots = sequence_data.get("shots") or []
    if not isinstance(shots, list):
        return {}
    repaired = {}
    for row in shots:
        if not isinstance(row, dict):
            continue
        shot_name = _clean_namespace(str(row.get("shot") or ""))
        if not shot_name:
            continue
        reference_node = f"{shot_name}RN"
        if not cmds.objExists(reference_node):
            continue
        try:
            current = str(cmds.referenceQuery(reference_node, namespace=True) or "").strip(":")
        except Exception:
            continue
        if current == shot_name:
            continue
        try:
            if cmds.namespace(exists=shot_name):
                members = cmds.namespaceInfo(
                    shot_name,
                    listOnlyDependencyNodes=True,
                    dagPath=True,
                ) or []
                if not members:
                    cmds.namespace(removeNamespace=shot_name)
            referenced_file = str(
                cmds.referenceQuery(reference_node, filename=True) or ""
            )
            if not referenced_file:
                raise RuntimeError(f"Reference path was not found for {reference_node}")
            cmds.file(referenced_file, edit=True, namespace=shot_name)
            resolved = str(cmds.referenceQuery(reference_node, namespace=True) or "").strip(":")
        except Exception as exc:
            raise RuntimeError(
                f"Could not restore camera namespace '{shot_name}' on {reference_node}: {exc}"
            ) from exc
        if resolved != shot_name:
            raise RuntimeError(
                f"Camera namespace repair failed for {reference_node}. "
                f"Expected '{shot_name}', got '{resolved or 'none'}'."
            )
        repaired[shot_name] = current
    return repaired


def create_review_display_layers(cast_data: dict) -> dict[str, int]:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Review layer creation is available inside Maya.") from exc

    cast = cast_data.get("cast") or {}
    review_layers = cast_data.get("review_layers") or {}
    created = {}
    for legacy_layer_node in cmds.ls(type="displayLayer") or []:
        if str(legacy_layer_node).startswith("review_"):
            cmds.delete(legacy_layer_node)
    for layer_name, layer in review_layers.items():
        layer_node = str(layer.get("display_layer") or layer_name)
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
        members.extend(_nodes_for_review_objects(cmds, layer.get("objects") or []))
        members = _unique_nodes(members)
        if members:
            cmds.editDisplayLayerMembers(layer_node, members, noRecurse=True)
        created[str(layer_name)] = len(members)
    return created


def _nodes_for_review_objects(cmds, objects) -> list[str]:
    nodes = []
    for entry in objects:
        if isinstance(entry, str):
            path = entry
            uuid = ""
        elif isinstance(entry, dict):
            path = str(entry.get("dag_path") or "")
            uuid = str(entry.get("maya_uuid") or "")
        else:
            continue
        matches = (cmds.ls(uuid, long=True) or []) if uuid else []
        if matches:
            nodes.extend(matches)
        elif path and cmds.objExists(path):
            nodes.extend(cmds.ls(path, long=True) or [path])
    return nodes


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


def _reference_file(cmds, path: Path, namespace: str) -> str:
    namespace = _unique_namespace(cmds, namespace)
    references_before = set(cmds.ls(type="reference") or [])
    referenced_file = cmds.file(
        str(path),
        reference=True,
        deferReference=False,
        namespace=namespace,
        defaultNamespace=False,
        ignoreVersion=True,
        mergeNamespacesOnClash=False,
        options="v=0;",
    )
    references_after = set(cmds.ls(type="reference") or [])
    new_references = sorted(
        node for node in references_after - references_before if node != "sharedReferenceNode"
    )
    if new_references:
        reference_node = new_references[0]
        try:
            actual_namespace = str(cmds.referenceQuery(reference_node, namespace=True) or "").strip(":")
        except Exception:
            actual_namespace = ""
        try:
            if actual_namespace != namespace:
                # The same camera-rig file is referenced once per shot. Editing by
                # file path is ambiguous in that case and Maya may rename the first
                # matching reference instead of the newly-created one.
                unique_reference_path = str(
                    cmds.referenceQuery(reference_node, filename=True) or referenced_file
                )
                cmds.file(unique_reference_path, edit=True, namespace=namespace)
        except Exception as exc:
            raise RuntimeError(
                f"Could not assign namespace '{namespace}' to reference "
                f"'{path.name}': {exc}"
            ) from exc
        try:
            actual_namespace = str(cmds.referenceQuery(reference_node, namespace=True) or "").strip(":")
        except Exception:
            actual_namespace = ""
        if actual_namespace and actual_namespace != namespace:
            raise RuntimeError(
                f"Camera rig namespace mismatch. Expected '{namespace}', "
                f"but Maya assigned '{actual_namespace}'."
            )
    return namespace


def ensure_scene_references_loaded(cmds=None) -> list[str]:
    """Load all scene references required for Review Layer membership."""

    if cmds is None:
        try:
            import maya.cmds as cmds
        except ImportError as exc:
            raise RuntimeError("Reference loading is available inside Maya.") from exc
    loaded = []
    failed = []
    for reference_node in cmds.ls(type="reference") or []:
        reference_node = str(reference_node)
        if reference_node == "sharedReferenceNode":
            continue
        try:
            is_loaded = bool(cmds.referenceQuery(reference_node, isLoaded=True))
        except Exception:
            is_loaded = False
        if not is_loaded:
            try:
                cmds.file(loadReference=reference_node)
            except Exception as exc:
                failed.append(f"{reference_node}: {exc}")
                continue
        try:
            if not cmds.referenceQuery(reference_node, isLoaded=True):
                failed.append(f"{reference_node}: remained deferred")
                continue
        except Exception as exc:
            failed.append(f"{reference_node}: {exc}")
            continue
        loaded.append(reference_node)
    if failed:
        raise RuntimeError("Required Maya references could not be loaded: " + "; ".join(failed))
    return loaded


def _import_file(cmds, path: Path, namespace: str) -> list[str]:
    before = set(cmds.ls(long=True) or [])
    options = {
        "i": True,
        "namespace": namespace,
        "ignoreVersion": True,
        "mergeNamespacesOnClash": False,
        "options": "v=0;",
    }
    if path.suffix.lower() == ".fbx":
        cmds.loadPlugin("fbxmaya", quiet=True)
        options["type"] = "FBX"
    cmds.file(str(path), **options)
    after = set(cmds.ls(long=True) or [])
    return sorted(after - before)


def _is_sequence_all_layout(shot_data: dict, department: str) -> bool:
    return str(shot_data.get("shot") or "").strip() == "all" and str(department or "").strip().lower() == "layout"


def _resolve_camera_rig(project_root: Path) -> Path | None:
    configured = ""
    config_dir = os.environ.get("PROJECT_CONFIG_DIR")
    if config_dir:
        template_config = load_config(Path(config_dir) / "templates_base.yml")
        configured = str(
            ((template_config.get("template_files") or {}).get("maya") or {}).get(
                "camera_rig"
            )
            or ""
        ).strip()
    candidates = [
        Path(
            os.path.expandvars(
                configured
                .replace("{project_root}", str(project_root))
                .replace(
                    "{pipeline_root}",
                    str(Path(__file__).resolve().parents[4]),
                )
            )
        )
        if configured
        else None,
        project_root / "library" / "layout" / "camerarig" / "camerarig.ma",
    ]
    return next((path for path in candidates if path and path.exists()), None)


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


def _construct_enabled(
    construct_data: dict | None,
    component_type: str,
    name: str = "",
    source_field: str = "",
    *,
    default: bool = True,
) -> bool:
    if not construct_data:
        return default
    components = construct_data.get("components") or []
    if not isinstance(components, list):
        return default
    component_type = component_type.strip().lower()
    name = name.strip()
    source_field = source_field.strip()
    typed_components = [
        component
        for component in components
        if isinstance(component, dict)
        and str(component.get("component_type") or component.get("type") or "").strip().lower() == component_type
    ]
    if not typed_components:
        return default
    matches = []
    for component in typed_components:
        source = component.get("source") if isinstance(component.get("source"), dict) else {}
        if source_field and str(source.get("field") or "").strip() == source_field:
            matches.append(component)
            continue
        if name and str(component.get("name") or "").strip() == name:
            matches.append(component)
    if not matches:
        return default
    return any(_truthy(component.get("enabled"), default=True) for component in matches)


def _truthy(value, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "none"}


def _load_layout_overlay_usd(cmds, project_root: Path, anim_input: dict) -> str:
    usd_path = _project_path(project_root, str(anim_input.get("layout_overlay") or ""))
    if not usd_path or not usd_path.exists():
        return ""
    try:
        cmds.loadPlugin("mayaUsdPlugin", quiet=True)
    except Exception:
        return ""
    group = _ensure_group(cmds, "layout_overlay_grp")
    transform = cmds.createNode("transform", name=_unique_node_name(cmds, "layout_overlay_USD"), parent=group)
    shape = cmds.createNode("mayaUsdProxyShape", name=f"{transform}Shape", parent=transform)
    try:
        cmds.setAttr(f"{shape}.filePath", str(usd_path).replace("\\", "/"), type="string")
    except Exception:
        pass
    for attr, value in (
        ("smartpipelineRole", "layout_overlay"),
        ("smartpipelineUsage", str(anim_input.get("layout_overlay_usage") or "reference_only")),
    ):
        try:
            cmds.addAttr(transform, longName=attr, dataType="string")
            cmds.setAttr(f"{transform}.{attr}", value, type="string")
        except Exception:
            pass
    return str(usd_path)


def _load_context_usd(cmds, project_root: Path, anim_input: dict) -> str:
    usd_path = _project_path(project_root, str(anim_input.get("context_usd") or ""))
    if not usd_path or not usd_path.is_file():
        return ""
    try:
        cmds.loadPlugin("mayaUsdPlugin", quiet=True)
    except Exception:
        return ""
    group = _ensure_group(cmds, "context_grp")
    transform = cmds.createNode("transform", name=_unique_node_name(cmds, "context_USD"), parent=group)
    shape = cmds.createNode("mayaUsdProxyShape", name=f"{transform}Shape", parent=transform)
    cmds.setAttr(f"{shape}.filePath", str(usd_path).replace("\\", "/"), type="string")
    stage_profile = str(anim_input.get("context_profile") or "WORK").upper()
    if stage_profile == "FINAL":
        stage_profile = "REND"
    configured_policy = anim_input.get("context_stage_policy") or {}
    purpose = str(configured_policy.get("purpose") or "").strip().lower()
    default_policy = {
        "FAST": {"loadPayloads": False, "drawProxyPurpose": True, "drawRenderPurpose": False},
        "WORK": {"loadPayloads": True, "drawProxyPurpose": True, "drawRenderPurpose": False},
        "REND": {"loadPayloads": True, "drawProxyPurpose": False, "drawRenderPurpose": True},
    }.get(stage_profile, {})
    purpose_policy = dict(default_policy)
    if configured_policy:
        purpose_policy["loadPayloads"] = bool(configured_policy.get("load_payloads", True))
        purpose_policy["drawProxyPurpose"] = purpose in {"proxy", "bbox"}
        purpose_policy["drawRenderPurpose"] = purpose == "render"
    for attr, value in purpose_policy.items():
        plug = f"{shape}.{attr}"
        if cmds.objExists(plug):
            try:
                cmds.setAttr(plug, value)
            except Exception:
                pass
    for attr, value in (
        ("smartpipelineRole", "shot_context"),
        ("smartpipelineContextVersion", str(anim_input.get("context_version") or "")),
        ("smartpipelineContextProfile", stage_profile),
    ):
        try:
            cmds.addAttr(transform, longName=attr, dataType="string")
            cmds.setAttr(f"{transform}.{attr}", value, type="string")
        except Exception:
            pass
    return str(usd_path)


def _construct_uses_usd(construct_data: dict | None) -> bool:
    return any(
        str(component.get("component_type") or "").strip().lower() == "usd"
        and bool(component.get("enabled", True))
        for component in ((construct_data or {}).get("components") or [])
        if isinstance(component, dict)
    )


def _camera_scene_from_publish(path: Path) -> Path | None:
    if path.suffix.lower() in {".ma", ".mb", ".fbx"}:
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


def _create_camera_from_json(
    cmds,
    camera_json: Path,
    anim_input: dict,
    frame_offset: float,
    *,
    camera_name: str = "",
) -> str:
    if not camera_json.exists():
        return ""
    data = read_json(camera_json, {}) or {}
    shot_name = _clean_namespace(str(anim_input.get("shot") or data.get("shot") or "shot"))
    camera_name = _clean_namespace(camera_name) if camera_name else f"{shot_name}_anim_cam"
    if cmds.objExists(camera_name):
        cmds.delete(camera_name)
    camera, camera_shape = cmds.camera(name=camera_name)
    for attr, value in (data.get("shape_attributes") or {}).items():
        target_attr = f"{camera_shape}.{attr}"
        if not cmds.objExists(target_attr):
            continue
        try:
            cmds.setAttr(target_attr, value)
        except Exception:
            pass
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
    # Support both the legacy camera package (animation/lens/fstop) and the
    # current maya_camera/v1 package (samples/shape_attributes).
    samples = data.get("animation") or data.get("samples") or []
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


def _enabled_construct_components(construct_data: dict | None, component_type: str) -> list[dict]:
    if not construct_data:
        return []
    expected = str(component_type).strip().lower()
    return [
        component
        for component in (construct_data.get("components") or [])
        if isinstance(component, dict)
        and str(component.get("component_type") or component.get("type") or "").strip().lower() == expected
        and _truthy(component.get("enabled"), default=True)
    ]


def _apply_construct_cameras(
    cmds,
    project_root: Path,
    anim_input: dict,
    construct_data: dict | None,
    frame_offset: float,
) -> list[str]:
    cameras = []
    primary_path = _project_path(project_root, str(anim_input.get("camera") or ""))
    direct_components = [
        component
        for component in _enabled_construct_components(construct_data, "camera")
        if str((component.get("source") or {}).get("kind") or "")
        in {"published_camera", "scene_data", "shot_dependency"}
    ]
    primary_enabled = _construct_enabled(
        construct_data, "camera", "camera", "camera"
    ) and not direct_components
    seen = {
        primary_path.resolve()
        for _value in (primary_path,)
        if primary_enabled and primary_path and primary_path.exists()
    }
    for component in direct_components:
        camera_path = _project_path(project_root, str(component.get("path") or ""))
        if not camera_path or not camera_path.is_file() or camera_path.resolve() in seen:
            continue
        seen.add(camera_path.resolve())
        camera_name = str(component.get("name") or "")
        source_kind = str((component.get("source") or {}).get("kind") or "")
        group = _ensure_group(cmds, "camera_grp")
        try:
            existing = cmds.ls(f"|{group}|{camera_name}", long=True) or []
            if existing:
                cmds.delete(existing)
        except Exception:
            pass
        camera_scene = _camera_scene_from_publish(camera_path)
        if camera_scene and camera_scene.is_file():
            cameras_before = set(cmds.ls(type="camera", long=True) or [])
            try:
                imported = _import_file(cmds, camera_scene, ":")
                cameras_after = set(cmds.ls(type="camera", long=True) or [])
                if source_kind == "shot_dependency" and not (
                    cameras_after - cameras_before
                ):
                    raise RuntimeError(
                        f"Virtual Camera FBX did not create a camera: {camera_scene}"
                    )
                _parent_imported_top_nodes(cmds, imported, group)
                cameras.append(camera_name)
                continue
            except Exception as exc:
                if source_kind == "shot_dependency":
                    raise RuntimeError(
                        f"Virtual Camera FBX import failed: {camera_scene}: {exc}"
                    ) from exc
                # Older or incompatible Maya files still have a portable JSON
                # snapshot beside them, used below as the fallback.
                pass
        camera = _create_camera_from_json(
            cmds,
            camera_path,
            anim_input,
            frame_offset,
            camera_name=camera_name,
        )
        if not camera:
            continue
        try:
            parented = cmds.parent(camera, group) or []
            camera = str(parented[0]) if parented else camera
            camera = str(cmds.rename(camera, camera_name))
        except Exception:
            pass
        cameras.append(camera)
    return cameras


def _apply_construct_lights(cmds, project_root: Path, construct_data: dict | None) -> list[str]:
    """Import versioned lights below the template ``lights_grp`` container."""

    from smartlib.dcc.maya.shot_scene_data import import_scene_component_package

    components = _enabled_construct_components(construct_data, "light")
    if not components:
        return []
    imported = []
    container = "lights_grp"
    if not cmds.objExists(container):
        container = cmds.group(empty=True, name=container)
    for component in components:
        data_path = _project_path(project_root, str(component.get("path") or ""))
        if not data_path or not data_path.exists():
            continue
        package_data = (read_json(data_path, {}) or {}) if data_path.is_file() else {}
        package_root = str(package_data.get("root") or component.get("name") or "light").split("|")[-1]
        if package_root != "lights_grp":
            existing = cmds.ls(f"|{container}|{package_root}", long=True) or []
            if existing:
                cmds.delete(existing)
        created = import_scene_component_package(data_path)
        if package_root == "lights_grp":
            imported_container = next(
                (node for node in created if node.rsplit("|", 1)[-1].startswith("lights_grp")),
                "",
            )
            if imported_container and cmds.objExists(imported_container):
                for child in cmds.listRelatives(imported_container, children=True, fullPath=True) or []:
                    child_name = child.rsplit("|", 1)[-1]
                    existing = cmds.ls(f"|{container}|{child_name}", long=True) or []
                    if existing:
                        cmds.delete(existing)
                    parented = cmds.parent(child, container) or []
                    imported.extend(str(node) for node in parented)
                if cmds.objExists(imported_container):
                    cmds.delete(imported_container)
            continue
        imported_root = next(
            (node for node in created if node.rsplit("|", 1)[-1].startswith(package_root)),
            created[0] if created else "",
        )
        if imported_root and cmds.objExists(imported_root):
            parented = cmds.parent(imported_root, container) or []
            imported.extend(str(node) for node in parented)
    return imported


def _apply_construct_playblast_settings(cmds, project_root: Path, construct_data: dict | None) -> str:
    """Restore the latest enabled Smart Playblast settings Data Publish."""

    from smartlib.dcc.maya.review_playblast import save_scene_playblast_settings

    components = _enabled_construct_components(construct_data, "playblast_settings")
    if not components:
        return ""
    path = _project_path(project_root, str(components[-1].get("path") or ""))
    if not path or not path.is_file():
        return ""
    data = read_json(path, {}) or {}
    for key in (
        "episode", "sequence", "shot", "scope", "department", "data_type",
        "target", "subset", "version", "comment", "source_workfile", "files",
    ):
        data.pop(key, None)
    save_scene_playblast_settings(data, cmds)
    return str(path)


def _apply_construct_set_dress(project_root: Path, construct_data: dict | None) -> list[str]:
    warnings = []
    from smartlib.dcc.maya import set_dress

    for component in _enabled_construct_components(construct_data, "set_dress"):
        package_path = _project_path(project_root, str(component.get("path") or ""))
        if not package_path or not package_path.is_file():
            continue
        package = set_dress.load_package(package_path)
        warnings.extend(set_dress.apply_stack(package.layers, base=package.base))
    return warnings


def _apply_construct_animation_curves(project_root: Path, construct_data: dict | None) -> list[dict]:
    from smartlib.dcc.maya.animation_curves import apply_animation_curves_from_file

    reports = []
    direct_components = _enabled_construct_components(
        construct_data, "animation_curve"
    )
    for component in direct_components:
        source = component.get("source") or {}
        curve_path = _project_path(project_root, str(component.get("path") or ""))
        if not curve_path or not curve_path.is_file():
            continue
        reports.append(
            apply_animation_curves_from_file(
                curve_path,
                namespace=str(source.get("namespace") or "") or None,
                clear_existing=True,
                strict_destinations=False,
            )
        )
    if direct_components:
        _store_animation_curve_apply_report(reports)
        _validate_animation_curve_apply_reports(reports)
        return reports

    # Compatibility for older constructs where curves were only linked from
    # an Animation Package/cache dependency.
    for component in _enabled_construct_components(construct_data, "animation"):
        source = component.get("source") or {}
        if str(source.get("kind") or "") != "published_animation_package":
            continue
        manifest_path = _project_path(project_root, str(component.get("path") or ""))
        manifest = read_json(manifest_path, {}) if manifest_path and manifest_path.is_file() else {}
        for cast_data in (manifest.get("casts") or {}).values():
            dependency = cast_data.get("curve_dependency") or {}
            curve_path = _project_path(project_root, str(dependency.get("path") or ""))
            if not curve_path or not curve_path.is_file():
                continue
            reports.append(
                apply_animation_curves_from_file(
                    curve_path,
                    namespace=str(cast_data.get("namespace") or "") or None,
                    clear_existing=True,
                    strict_destinations=False,
                )
            )
    _store_animation_curve_apply_report(reports)
    _validate_animation_curve_apply_reports(reports)
    return reports


def _store_animation_curve_apply_report(reports: list[dict]) -> None:
    """Persist the Curve application report in-scene for worker manifests."""

    try:
        from maya import cmds

        cmds.fileInfo(
            "smartAnimationCurveApplyReport",
            json.dumps(reports, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception:
        pass


def _validate_animation_curve_apply_reports(reports: list[dict]) -> None:
    """Reject a Curve component when none of its published values resolved."""

    failed = []
    for index, report in enumerate(reports):
        missing = int(report.get("missing_destinations") or 0)
        handled = sum(
            int(report.get(key) or 0)
            for key in (
                "applied_destinations",
                "applied_static_values",
                "skipped_destinations",
            )
        )
        if missing and handled == 0:
            failed.append(f"component {index + 1}: {missing} destinations missing")
    if failed:
        raise RuntimeError(
            "Animation Curve data resolved no scene destinations ("
            + "; ".join(failed)
            + "). Check the referenced Rig namespace and DAG hierarchy."
        )


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
    return ProjectConfig(config_dir).load("templates_assets.yml").get("maya_reference_groups") or {}


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
    sequence_root = configured_project_paths(project_root).sequence_root(episode, sequence)
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
    paths = configured_project_paths(project_root)
    publish_roots = (
        paths.editorial_sequence_publish_root(episode, sequence),
        *paths.legacy_editorial_sequence_publish_roots(episode, sequence),
    )
    for publish_root in publish_roots:
        latest = read_json(publish_root / "latest.json", {}) or {}
        version = str(latest.get("version") or "").strip()
        if not version:
            continue
        storyreel = publish_root / version / "storyreel"
        if storyreel.exists():
            return storyreel
    return None


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
    shapes = cmds.ls(f"{namespace}:*", type="camera", recursive=True, long=True) or []
    if not shapes:
        return ""
    parents = cmds.listRelatives(shapes[0], parent=True, fullPath=False) or []
    return parents[0] if parents else shapes[0]


def _first_new_camera(cmds, cameras_before: set[str]) -> str:
    cameras_after = set(cmds.ls(type="camera", long=True) or [])
    for shape in sorted(cameras_after - cameras_before):
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parents:
            return parents[0]
        return shape
    return ""


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
            _apply_picture_in_picture(cmds, shape)
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


def _apply_picture_in_picture(cmds, image_plane: str) -> None:
    values = {
        "sizeX": 0.28,
        "sizeY": 0.1575,
        "offsetX": 0.34,
        "offsetY": 0.18,
        "depth": 10.0,
        "displayOnlyIfCurrent": True,
    }
    for name, value in values.items():
        if not cmds.attributeQuery(name, node=image_plane, exists=True):
            continue
        try:
            cmds.setAttr(f"{image_plane}.{name}", value)
        except Exception:
            pass


def _attach_shot_storyreel_picture_in_picture(
    cmds,
    project_root: Path,
    shot_data: dict,
) -> list[str]:
    """Attach the latest editorial Storyreel to layout shot cameras as PiP."""

    episode = str(shot_data.get("episode") or "").strip()
    sequence = str(shot_data.get("sequence") or shot_data.get("seq") or "").strip()
    shot = str(shot_data.get("shot") or "").strip()
    if not episode or not sequence or not shot or shot.lower() == "all":
        return []
    storyreel_root = _latest_storyreel_root(project_root, episode, sequence)
    storyreel = _storyreel_first_frame(storyreel_root, shot, 0)
    if not storyreel:
        return []

    camera_shapes = cmds.ls(type="camera", long=True) or []
    default_cameras = {"persp", "top", "front", "side"}
    candidates = []
    for shape in camera_shapes:
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        camera = str(parents[0] if parents else shape)
        leaf = camera.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        if leaf in default_cameras:
            continue
        candidates.append(camera)
    grouped = [camera for camera in candidates if "|camera_grp|" in f"|{camera.strip('|')}|"]
    cameras = grouped or candidates

    first = _frame_number_from_path(storyreel)
    matches = sorted(storyreel.parent.glob("storyreel_*.jpg"))
    numbered = [value for value in (_frame_number_from_path(path) for path in matches) if value is not None]
    last = max(numbered) if numbered else first
    editorial = shot_data.get("editorial") or {}
    work_range = editorial.get("work_range") or editorial.get("cut_range") or []
    work_start = int(work_range[0]) if isinstance(work_range, (list, tuple)) and work_range else int(editorial.get("cut_in") or first or 1)

    attached = []
    for camera in dict.fromkeys(cameras):
        image_plane = _attach_image_plane(cmds, camera, storyreel)
        if not image_plane:
            continue
        if first is not None and last is not None:
            expression_name = _unique_node_name(cmds, f"{shot}_storyreel_pip_expr")
            expression = (
                f"{image_plane}.frameExtension = clamp({first}, {last}, "
                f"frame - {work_start} + {first});"
            )
            try:
                cmds.expression(
                    name=expression_name,
                    string=expression,
                    alwaysEvaluate=True,
                    unitConversion="all",
                )
                if cmds.attributeQuery("useFrameExtension", node=image_plane, exists=True):
                    cmds.setAttr(f"{image_plane}.useFrameExtension", True)
            except Exception:
                pass
        attached.append(str(image_plane))
    return attached


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
        )
    except Exception as exc:
        raise RuntimeError(
            f"Could not create Camera Sequencer shot '{shot_node}' "
            f"({row['cut_in']}-{row['cut_out']}): {exc}"
        ) from exc

    # Some Maya versions reject a new track number during shot creation.
    # Assigning it after the node exists keeps every editorial shot intact.
    try:
        cmds.shot(shot_node, edit=True, track=int(track))
    except Exception:
        try:
            if cmds.attributeQuery("track", node=shot_node, exists=True):
                cmds.setAttr(f"{shot_node}.track", int(track))
        except Exception:
            pass
    if not cmds.objExists(shot_node):
        return ""
    return shot_node


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
    work_range = editorial.get("work_range") or []
    if isinstance(work_range, (list, tuple)) and len(work_range) >= 2:
        cut_in, cut_out = work_range[0], work_range[1]
    else:
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


def _load_shot_audio(cmds, project_root: Path | None, shot_data: dict) -> str:
    audio = shot_data.get("audio") or {}
    raw_path = str(audio.get("path") or "").strip()
    if not raw_path:
        return ""
    path = Path(raw_path)
    if not path.is_absolute() and project_root is not None:
        path = project_root / path
    if not path.is_file():
        return ""
    node_name = "smartEditorialAudio"
    try:
        if cmds.objExists(node_name):
            cmds.delete(node_name)
    except Exception:
        pass
    editorial = shot_data.get("editorial") or {}
    cut_range = editorial.get("cut_range") or []
    if isinstance(cut_range, (list, tuple)) and cut_range:
        offset = int(cut_range[0])
    else:
        offset = int(audio.get("cut_in") or editorial.get("cut_in") or 1001)
    node = str(cmds.sound(file=str(path), offset=offset, name=node_name))
    reconnect_scene_audio_to_time_slider(cmds)
    return node


def _apply_scene_policy(cmds, shot_data: dict) -> None:
    try:
        from smartlib.dcc.maya.scene_policy import apply_scene_policy

        editorial = shot_data.get("editorial") or {}
        frame_range = None
        work_range = editorial.get("work_range") or []
        if isinstance(work_range, (list, tuple)) and len(work_range) >= 2:
            frame_range = (work_range[0], work_range[1])
        elif editorial.get("cut_in") is not None and editorial.get("cut_out") is not None:
            frame_range = (editorial.get("cut_in"), editorial.get("cut_out"))
        apply_scene_policy(
            cmds,
            fps=editorial.get("fps") or shot_data.get("fps"),
            frame_range=frame_range,
        )
    except Exception:
        pass
