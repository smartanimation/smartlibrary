from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path


def _write_status(path: Path, *, state: str, progress: int, task: str, message: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "state": state,
                "progress": int(progress),
                "task": task,
                "message": message,
                "updated": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _clean_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "_-" else "_" for char in value)


def _preferred_camera(cameras: list[str]) -> str:
    return next(
        (camera for camera in cameras if "cam_cha" in camera.lower()),
        cameras[0] if cameras else "",
    )


def _current_maya_scene_format(cmds) -> tuple[str, str]:
    """Keep the opened template format so unknown data does not force conversion."""

    queried = cmds.file(query=True, type=True) or []
    file_type = str(queried[0] if isinstance(queried, (list, tuple)) else queried)
    if file_type == "mayaBinary":
        return ".mb", "mayaBinary"
    return ".ma", "mayaAscii"


def _camera_for_layer(cameras: list[str], layer_name: str, configured_name: str = "") -> str:
    requested = configured_name.strip().lower()
    if requested:
        match = next(
            (
                camera
                for camera in cameras
                if camera.lower() == requested
                or camera.lower().rstrip("0123456789") == requested
            ),
            "",
        )
        if match:
            return match
    token = layer_name.strip().lower()
    match = next((camera for camera in cameras if token and token in camera.lower()), "")
    return match or _preferred_camera(cameras)


def _review_layer_specs(
    cmds,
    *,
    review_spec: dict,
    result: dict,
    cameras: list[str],
    default_resolution: tuple[int, int],
    default_frame_range: tuple[int, int],
    published_contract: dict | None = None,
) -> list[dict]:
    cast_nodes = {}
    cast_nodes.update(result.get("cache_nodes") or {})
    cast_nodes.update(result.get("static_nodes") or {})
    specs = []
    for layer_name, contract in sorted(
        (review_spec.get("layers") or {}).items(),
        key=lambda item: (int((item[1] or {}).get("order") or 0), item[0]),
    ):
        contract = contract or {}
        output_contract = (published_contract or {}).get(str(layer_name)) or {}
        members = [
            str(member)
            for member in (contract.get("members") or [])
            if str(member) in cast_nodes
        ]
        nodes = []
        for member in members:
            nodes.extend(
                node
                for node in (cast_nodes.get(member) or [])
                if node and cmds.objExists(node)
            )
        nodes = list(dict.fromkeys(nodes))
        if not members or not nodes:
            continue
        display_layer = _clean_name(f"review_{layer_name}")
        if cmds.objExists(display_layer):
            cmds.delete(display_layer)
        display_layer = cmds.createDisplayLayer(
            empty=True,
            name=display_layer,
            number=1,
        )
        cmds.editDisplayLayerMembers(display_layer, nodes, noRecurse=False)
        resolution = contract.get("resolution") or output_contract.get("resolution") or {}
        if isinstance(resolution, (list, tuple)):
            width = int(resolution[0]) if resolution else default_resolution[0]
            height = int(resolution[1]) if len(resolution) > 1 else default_resolution[1]
        else:
            width = int(resolution.get("width") or default_resolution[0])
            height = int(resolution.get("height") or default_resolution[1])
        camera_contract = contract.get("camera") or {}
        configured_camera = (
            str(camera_contract.get("name") or "")
            if isinstance(camera_contract, dict)
            else str(camera_contract)
        )
        configured_camera = configured_camera or str(output_contract.get("camera") or "")
        camera = _camera_for_layer(
            cameras,
            str(layer_name),
            configured_camera,
        )
        frame_range = (
            contract.get("export_frame_range")
            or contract.get("frame_range")
            or output_contract.get("frame_range")
        )
        if (
            not isinstance(frame_range, (list, tuple))
            or len(frame_range) < 2
        ):
            frame_range = default_frame_range
        specs.append(
            {
                "name": str(layer_name),
                "order": int(contract.get("order") or 0),
                "members": members,
                "nodes": nodes,
                "display_layer": display_layer,
                "camera": camera,
                "resolution": [max(1, width), max(1, height)],
                "frame_range": [int(frame_range[0]), int(frame_range[1])],
                "ae_slot": str((contract.get("ae") or {}).get("template_slot") or layer_name),
            }
        )
    return specs


def _activate_review_layer(cmds, specs: list[dict], active_name: str) -> None:
    for spec in specs:
        layer = spec["display_layer"]
        if cmds.objExists(f"{layer}.visibility"):
            cmds.setAttr(f"{layer}.visibility", spec["name"] == active_name)
    if cmds.objExists("defaultLayer"):
        cmds.editDisplayLayerGlobals(currentDisplayLayer="defaultLayer")


def _render_camera_sequence(
    cmds,
    *,
    camera: str,
    output_dir: Path,
    start: int,
    end: int,
    width: int,
    height: int,
    status_path: Path,
    progress_start: int,
    progress_end: int,
    output_pattern: str = "beauty_####.jpg",
) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    extension = Path(output_pattern).suffix.lower()
    if cmds.objExists("defaultRenderGlobals.imageFormat"):
        cmds.setAttr("defaultRenderGlobals.imageFormat", 32 if extension == ".png" else 8)
    count = max(1, end - start + 1)
    for index, frame in enumerate(range(start, end + 1)):
        cmds.currentTime(frame, edit=True)
        rendered = Path(
            cmds.ogsRender(
                camera=camera,
                currentFrame=True,
                width=width,
                height=height,
                noRenderView=True,
            )
        )
        target = output_dir / output_pattern.replace("####", f"{frame:04d}")
        shutil.copy2(rendered, target)
        progress = progress_start + int(((index + 1) / count) * (progress_end - progress_start))
        _write_status(
            status_path,
            state="BUILDING",
            progress=progress,
            task="Playblast",
            message=f"{camera}: {frame}/{end}",
        )
    return str(output_dir / output_pattern.replace("####", "%04d"))


def _latest_preview_render_contract(shot_service, identity, department: str) -> dict:
    packages_root = (
        shot_service.shot_root(identity)
        / "publish"
        / "preview_render"
        / department
        / "packages"
    )
    manifests = sorted(
        packages_root.glob("v*/render_manifest.json"),
        key=lambda path: int(path.parent.name[1:]) if path.parent.name[1:].isdigit() else -1,
        reverse=True,
    )
    if not manifests:
        return {}
    data = json.loads(manifests[0].read_text(encoding="utf-8-sig"))
    return data.get("layers") or data.get("groups") or {}


def _latest_review_project(shot_service, identity, department: str) -> Path | None:
    root = shot_service.shot_root(identity) / "publish" / "review_project" / department
    candidates = sorted(
        root.glob("v*/review_project.aep"),
        key=lambda path: int(path.parent.name[1:]) if path.parent.name[1:].isdigit() else -1,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _prepare_review_project_copy(
    published_project: Path,
    working_project: Path,
    layer_files: dict[str, dict],
    config,
) -> Path:
    from smartlib.review.ae import find_after_effects_executable

    after_effects = Path(find_after_effects_executable(config))
    command = after_effects.with_name("AfterFX.com")
    if not command.is_file():
        command = after_effects
    working_project.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(published_project, working_project)
    marker = working_project.with_suffix(".relinked")
    marker.unlink(missing_ok=True)
    mappings = [
        {
            "layer": layer,
            "file": str(data["first_file"]).replace("\\", "/"),
            "sequence": int(data.get("file_count") or 0) > 1,
        }
        for layer, data in layer_files.items()
    ]
    script = working_project.with_suffix(".relink.jsx")
    script.write_text(
        "\n".join(
            [
                "(function () {",
                f"  var projectFile = new File({json.dumps(str(working_project).replace(chr(92), '/'))});",
                f"  var markerFile = new File({json.dumps(str(marker).replace(chr(92), '/'))});",
                f"  var mappings = {json.dumps(mappings)};",
                "  app.open(projectFile);",
                "  function norm(value) { return String(value || '').replace(/\\\\/g, '/').toUpperCase(); }",
                "  for (var i = 1; i <= app.project.numItems; i++) {",
                "    var item = app.project.item(i);",
                "    if (!(item instanceof FootageItem) || !item.file) continue;",
                "    var source = norm(item.file.fsName);",
                "    for (var m = 0; m < mappings.length; m++) {",
                "      var mapping = mappings[m];",
                "      var token = '/LAYERS/' + String(mapping.layer).toUpperCase() + '/';",
                "      if (source.indexOf(token) < 0) continue;",
                "      var replacement = new File(mapping.file);",
                "      if (!replacement.exists) throw new Error('Footage not found: ' + mapping.file);",
                "      if (mapping.sequence) item.replaceWithSequence(replacement, false);",
                "      else item.replace(replacement);",
                "      break;",
                "    }",
                "  }",
                "  app.project.save(projectFile);",
                "  markerFile.open('w'); markerFile.write('ok'); markerFile.close();",
                "  app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);",
                "}());",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.Popen(
        [str(command), "-noui", "-r", str(script)],
        cwd=str(working_project.parent),
    )
    deadline = time.time() + 180.0
    while time.time() < deadline:
        if marker.is_file():
            return working_project
        time.sleep(0.5)
    raise RuntimeError(f"After Effects footage relink timed out: {working_project}")


def _render_review_project(project: Path, movie: Path, config) -> str:
    from smartlib.review.ae import find_after_effects_executable

    after_effects = Path(find_after_effects_executable(config))
    aerender = after_effects.with_name("aerender.exe")
    if not aerender.is_file():
        raise RuntimeError(f"aerender.exe was not found beside After Effects: {aerender}")
    movie.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            str(aerender),
            "-project",
            str(project),
            "-rqindex",
            "1",
            "-output",
            str(movie),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    message = "\n".join(
        value.strip()
        for value in (completed.stdout or "", completed.stderr or "")
        if value.strip()
    )
    if completed.returncode != 0 or not movie.is_file():
        raise RuntimeError(
            "Published AE project render failed. "
            f"Exit code: {completed.returncode}\n{message}"
        )
    return message


def _render_maya_sequencer(
    cmds,
    *,
    output_dir: Path,
    start: int,
    end: int,
    width: int,
    height: int,
    status_path: Path,
) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    count = max(1, end - start + 1)
    fallback_cameras = [
        parent
        for shape in (cmds.ls(type="camera", long=True) or [])
        for parent in (cmds.listRelatives(shape, parent=True, fullPath=True) or [])
        if parent.split("|")[-1] not in {"persp", "top", "front", "side"}
    ]
    for index, frame in enumerate(range(start, end + 1)):
        cmds.currentTime(frame, edit=True)
        camera = ""
        try:
            shot = cmds.sequenceManager(query=True, currentShot=True)
            if shot:
                camera = cmds.shot(shot, query=True, currentCamera=True) or ""
        except Exception:
            camera = ""
        camera = camera or _preferred_camera(fallback_cameras)
        if not camera:
            raise RuntimeError(f"No Camera Sequencer camera was resolved at frame {frame}.")
        rendered = Path(
            cmds.ogsRender(
                camera=camera,
                currentFrame=True,
                width=width,
                height=height,
                noRenderView=True,
            )
        )
        shutil.copy2(rendered, output_dir / f"beauty_{frame:04d}.jpg")
        _write_status(
            status_path,
            state="BUILDING",
            progress=40 + int(((index + 1) / count) * 45),
            task="Sequence Playblast",
            message=f"{frame}/{end}: {camera}",
        )
    return str(output_dir / "beauty_%04d.jpg")


def _update_output_history(output_root: Path, output_version: str, movie_path: Path) -> None:
    from smartlib.core.metadata import read_json, write_json

    write_json(
        output_root / "latest.json",
        {
            "version": output_version,
            "path": f"{output_version}/{movie_path.name}",
        },
    )
    versions_path = output_root / "versions.json"
    versions = read_json(versions_path, [])
    if not isinstance(versions, list):
        versions = []
    versions = [
        item
        for item in versions
        if isinstance(item, dict) and item.get("version") != output_version
    ]
    versions.append(
        {
            "version": output_version,
            "status": "complete",
            "movie": f"{output_version}/{movie_path.name}",
        }
    )
    write_json(versions_path, versions)


def _construct_scene_validation(cmds, scene_path: Path, references: list[str]) -> list[dict]:
    results: list[dict] = []
    if not scene_path.is_file():
        results.append(
            {
                "severity": "ERROR",
                "code": "MISSING_CONSTRUCT_SCENE",
                "message": f"Construct scene was not saved: {scene_path}",
            }
        )
    missing_references = [path for path in references if path and not Path(path).is_file()]
    if missing_references:
        results.append(
            {
                "severity": "ERROR",
                "code": "MISSING_REFERENCES",
                "message": "Missing references: " + ", ".join(missing_references),
            }
        )
    unloaded = []
    for path in references:
        if not path or not Path(path).is_file():
            continue
        try:
            if not cmds.referenceQuery(path, isLoaded=True):
                unloaded.append(path)
        except Exception:
            unloaded.append(path)
    if unloaded:
        results.append(
            {
                "severity": "ERROR",
                "code": "UNLOADED_REFERENCES",
                "message": "References were not loaded: " + ", ".join(unloaded),
            }
        )
    try:
        unknown_plugins = sorted(set(cmds.unknownPlugin(query=True, list=True) or []))
    except Exception:
        unknown_plugins = []
    if unknown_plugins:
        results.append(
            {
                "severity": "WARNING",
                "code": "UNKNOWN_PLUGINS",
                "message": "Unavailable Maya plugins: " + ", ".join(unknown_plugins),
            }
        )
    unknown_nodes = cmds.ls(type="unknown") or []
    if unknown_nodes:
        results.append(
            {
                "severity": "WARNING",
                "code": "UNKNOWN_NODES",
                "message": f"Scene contains {len(unknown_nodes)} unknown node(s).",
            }
        )
    return results


def _validation_payload(plan, scene_results: list[dict]) -> dict:
    results = [
        {
            "severity": item.severity,
            "code": item.code,
            "message": item.message,
        }
        for item in plan.validations
    ]
    results.extend(scene_results)
    if any(str(item.get("severity") or "").upper() == "ERROR" for item in results):
        status = "failed"
    elif any(str(item.get("severity") or "").upper() == "WARNING" for item in results):
        status = "warning"
    else:
        status = "passed"
    return {"status": status, "results": results}


def _construct_snapshot_for_preview(shot_service, identity, preview) -> dict:
    """Capture only the components that were actually used by this build."""
    existing = shot_service.load_construct(identity)
    existing_by_name = {
        str(component.get("name") or ""): dict(component)
        for component in (existing.get("components") or [])
        if isinstance(component, dict)
    }
    components = []
    for item in preview:
        component = existing_by_name.get(item.cast_key, {})
        component.update(
            {
                "component_type": component.get("component_type") or "rig",
                "name": item.cast_key,
                "version": item.asset_publish,
                "mode": component.get("mode") or "reference",
                "namespace": item.namespace,
                "path": item.publish_path,
                "required": item.required,
                "enabled": True,
                "note": item.message or component.get("note") or "",
                "source": {
                    "kind": "cast_entry",
                    "asset": item.asset,
                    "variant": item.variant,
                    "role": item.role,
                    "status": item.status,
                },
            }
        )
        components.append(component)
    return shot_service.construct_snapshot(identity, {"components": components})


def _run_scene_construction(
    *,
    args,
    manager,
    identity,
    plan,
    status_path: Path,
) -> int:
    import maya.cmds as cmds

    from smartlib.core.metadata import write_json
    from smartlib.dcc.maya.shot_builder import (
        stage_anim_from_input,
        stage_shot_from_preview,
    )
    shot_service = manager.shots
    try:
        build_overrides = json.loads(args.overrides_json or "{}")
    except (TypeError, ValueError):
        build_overrides = {}
    _write_status(status_path, state="BUILDING", progress=10, task="Resolve Stage Inputs")
    if plan.department == "anim":
        preview = shot_service.build_preview(
            identity,
            department=plan.department,
            cast_contexts=build_overrides.get("cast_contexts") or {},
            exclude_cast=build_overrides.get("exclude_cast") or [],
        )
        preview = shot_service.filter_preview_items_for_construct(identity, preview)
    else:
        preview = shot_service.build_preview(
            identity,
            department=plan.department,
            cast_contexts=build_overrides.get("cast_contexts") or {},
            exclude_cast=build_overrides.get("exclude_cast") or [],
        )
    resolved = [row for row in preview if row.status == "resolved"]
    missing = [row for row in preview if row.required and row.status != "resolved"]
    if missing:
        raise RuntimeError(
            "Required cast is unresolved: "
            + ", ".join(f"{row.cast_key}: {row.message or row.status}" for row in missing)
        )

    _write_status(status_path, state="BUILDING", progress=20, task=plan.resolved_mode.title())
    shot_data = shot_service.load_shot(identity)
    if plan.department == "anim":
        referenced = stage_anim_from_input(
            resolved,
            plan.anim_input,
            shot_data,
            project_root=manager.project_config.project_root,
            construct_data=shot_service.load_construct(identity),
        )
    else:
        referenced = stage_shot_from_preview(
            resolved,
            shot_data,
            department=plan.department,
            project_root=manager.project_config.project_root,
        )
    build_root = (
        shot_service.shot_root(identity)
        / "output"
        / "scene_build"
        / plan.department
        / plan.task
        / args.output_version
    )
    if build_root.exists() and any(build_root.iterdir()):
        raise RuntimeError(
            f"Construct version already exists and cannot be overwritten: {build_root}"
        )
    build_root.mkdir(parents=True, exist_ok=True)
    scene_extension, scene_type = _current_maya_scene_format(cmds)
    scene_path = build_root / (
        f"{identity.shot}_{plan.department}_{plan.task}_{args.output_version}"
        f"{scene_extension}"
    )
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, type=scene_type, force=True)
    validation = _validation_payload(
        plan,
        _construct_scene_validation(cmds, scene_path, referenced),
    )

    editorial = shot_data.get("editorial") or {}
    start = int(editorial.get("cut_in") or shot_data.get("cut_in") or 1001)
    end = int(editorial.get("cut_out") or shot_data.get("cut_out") or start)
    resolution = shot_data.get("resolution") or [960, 540]
    width = max(1, int(resolution[0]))
    height = max(1, int(resolution[1]))
    cameras = [
        parent
        for shape in (cmds.ls(type="camera", long=True) or [])
        for parent in (cmds.listRelatives(shape, parent=True, fullPath=True) or [])
        if parent.split("|")[-1] not in {"persp", "top", "front", "side"}
    ]
    camera = _preferred_camera(cameras) or ""

    manifest = {
        "format": "smartpipeline.scene_build_manifest",
        "format_version": 1,
        "shot": identity.shot,
        "episode": identity.episode,
        "sequence": identity.sequence,
        "operation": plan.resolved_mode,
        "department": plan.department,
        "task": plan.task,
        "source_workfile": plan.source_scene,
        "anim_input": plan.anim_input,
        "scene": str(scene_path),
        "frame_range": [start, end],
        "resolution": [width, height],
        "camera": camera,
        "references": referenced,
        "construct": _construct_snapshot_for_preview(
            shot_service,
            identity,
            resolved,
        ),
        "build_overrides": build_overrides,
        "status": "validated" if validation["status"] != "failed" else "blocked",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    manifest_path = write_json(build_root / "build_manifest.json", manifest)
    write_json(build_root / "validation.json", validation)
    _write_status(
        status_path,
        state="COMPLETE",
        progress=100,
        task="Construct Validated",
        message=str(scene_path),
    )
    print(
        json.dumps(
            {
                "scene": str(scene_path),
                "build_manifest": str(manifest_path),
                "validation": str(build_root / "validation.json"),
            }
        )
    )
    return 0


def _run_sequence_construction(
    *,
    args,
    manager,
    identity,
    plan,
    status_path: Path,
) -> int:
    import maya.cmds as cmds

    from smartlib.core.metadata import write_json
    from smartlib.dcc.maya.shot_builder import stage_sequence_layout_from_preview
    shot_service = manager.shots
    sequence_options = json.loads(args.sequence_options_json or "{}")
    recipe_plan = manager.sequence_recipe_plan(
        identity,
        recipe=str(sequence_options.get("recipe") or ""),
        virtual_camera_take=str(sequence_options.get("virtual_camera_take") or ""),
        enabled_inputs=dict(sequence_options.get("enabled_inputs") or {}),
    )
    if not recipe_plan.can_build:
        errors = "; ".join(
            row.detail for row in recipe_plan.validation if row.state == "ERROR"
        )
        raise RuntimeError(f"Sequence recipe validation failed: {errors}")
    _write_status(status_path, state="BUILDING", progress=12, task="Resolve Sequence Inputs")
    preview = shot_service.build_sequence_preview(identity)
    missing = [row for row in preview if row.required and row.status != "resolved"]
    if missing:
        raise RuntimeError(
            "Required sequence cast is unresolved: "
            + ", ".join(f"{row.cast_key}: {row.message or row.status}" for row in missing)
        )
    resolved = [row for row in preview if row.status == "resolved"]
    sequence_data = shot_service.load_sequence(identity)
    _write_status(status_path, state="BUILDING", progress=20, task="Stage Sequence")
    selected_shots = [
        str(value).strip()
        for value in json.loads(args.shots_json or "[]")
        if str(value).strip()
    ]
    staged_sequence_data = dict(sequence_data)
    if selected_shots:
        selected_set = set(selected_shots)
        selected_rows = [
            row for row in (sequence_data.get("shots") or [])
            if isinstance(row, dict) and str(row.get("shot") or "") in selected_set
        ]
        staged_sequence_data["shots"] = selected_rows
        starts = [int(row["cut_in"]) for row in selected_rows if row.get("cut_in") is not None]
        ends = [int(row["cut_out"]) for row in selected_rows if row.get("cut_out") is not None]
        if starts and ends:
            editorial = dict(sequence_data.get("editorial") or {})
            editorial.update({"cut_in": min(starts), "cut_out": max(ends)})
            staged_sequence_data["editorial"] = editorial
    referenced = stage_sequence_layout_from_preview(
        resolved,
        staged_sequence_data,
        project_root=manager.project_config.project_root,
        shot_names=selected_shots,
    )
    scene_root = (
        shot_service.sequence_workspace_root(identity.episode, identity.sequence)
        / "output"
        / "scene_build"
        / plan.department
        / plan.task
        / args.output_version
    )
    if scene_root.exists() and any(scene_root.iterdir()):
        raise RuntimeError(
            f"Construct version already exists and cannot be overwritten: {scene_root}"
        )
    scene_root.mkdir(parents=True, exist_ok=True)
    scene_extension, scene_type = _current_maya_scene_format(cmds)
    scene_path = scene_root / (
        f"{identity.episode}_{identity.sequence}_{plan.department}_"
        f"{plan.task}_{args.output_version}{scene_extension}"
    )
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, type=scene_type, force=True)
    validation = _validation_payload(
        plan,
        _construct_scene_validation(cmds, scene_path, referenced),
    )
    editorial = staged_sequence_data.get("editorial") or {}
    start = int(editorial.get("cut_in") or recipe_plan.frame_start)
    end = int(editorial.get("cut_out") or recipe_plan.frame_end or start)
    resolution = sequence_data.get("resolution") or [960, 540]
    width, height = max(1, int(resolution[0])), max(1, int(resolution[1]))
    manifest = {
        "format": "smartpipeline.sequence_build_manifest",
        "format_version": 1,
        "episode": identity.episode,
        "sequence": identity.sequence,
        "shots": selected_shots,
        "scope": "sequence",
        "operation": plan.resolved_mode,
        "department": plan.department,
        "task": plan.task,
        "sequence_input": plan.anim_input,
        "recipe": {
            "name": recipe_plan.recipe,
            "version": recipe_plan.recipe_version,
        },
        "virtual_camera_take": recipe_plan.virtual_camera_take,
        "inputs": [
            manager.sequence_builder.input_payload(item)
            for item in recipe_plan.inputs
            if item.enabled
        ],
        "scene": str(scene_path),
        "frame_range": [start, end],
        "resolution": [width, height],
        "references": referenced,
        "status": "validated" if validation["status"] != "failed" else "blocked",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    manifest_path = write_json(scene_root / "build_manifest.json", manifest)
    validation_path = write_json(scene_root / "validation.json", validation)
    _write_status(
        status_path,
        state="COMPLETE",
        progress=100,
        task="Construct Validated",
        message=str(scene_path),
    )
    print(
        json.dumps(
            {
                "scene": str(scene_path),
                "build_manifest": str(manifest_path),
                "validation": str(validation_path),
            }
        )
    )
    return 0


def run(args) -> int:
    status_path = Path(args.status_file)
    _write_status(status_path, state="BUILDING", progress=2, task="Initialize Maya")
    import maya.standalone

    maya.standalone.initialize(name="python")
    import maya.cmds as cmds

    from smartlib.apps.review_build_manager.service import ReviewBuildManagerService
    from smartlib.apps.shot_manager import SequenceIdentity, ShotIdentity
    from smartlib.core.config_loader import ProjectConfig
    from smartlib.core.metadata import read_json
    from smartlib.dcc.maya.shot_builder import build_animation_review_scene
    from smartlib.review.playblast_package import encode_prores_proxy_mov, find_ffmpeg

    config = ProjectConfig(args.config_dir)
    manager = ReviewBuildManagerService(config)
    shot_service = manager.shots
    if args.scope == "sequence":
        sequence_identity = SequenceIdentity(args.episode, args.sequence)
        sequence_plan = manager.sequence_build_plan(
            sequence_identity,
            requested_mode=args.operation,
            department=args.department,
            task=args.task_name,
            input_policy="USE EXISTING",
            recipe=str(json.loads(args.sequence_options_json or "{}").get("recipe") or ""),
            virtual_camera_take=str(json.loads(args.sequence_options_json or "{}").get("virtual_camera_take") or ""),
            enabled_inputs=dict(json.loads(args.sequence_options_json or "{}").get("enabled_inputs") or {}),
        )
        if not sequence_plan.buildable:
            details = "; ".join(
                f"{item.code}: {item.message}" for item in sequence_plan.validations
            )
            raise RuntimeError(f"Sequence build is blocked. {details}")
        return _run_sequence_construction(
            args=args,
            manager=manager,
            identity=sequence_identity,
            plan=sequence_plan,
            status_path=status_path,
        )
    identity = ShotIdentity(args.episode, args.sequence, args.shot)
    orchestration_plan = manager.build_plan(
        identity,
        mode=args.operation,
        department=args.department,
        task=args.task_name,
    )
    if not orchestration_plan.buildable:
        details = "; ".join(
            f"{item.code}: {item.message}" for item in orchestration_plan.validations
        )
        raise RuntimeError(f"Scene build is blocked. {details}")
    if orchestration_plan.resolved_mode != "REVIEW ONLY":
        return _run_scene_construction(
            args=args,
            manager=manager,
            identity=identity,
            plan=orchestration_plan,
            status_path=status_path,
        )
    _write_status(status_path, state="BUILDING", progress=8, task="Resolve")
    plan = shot_service.animation_review_build_plan(identity)
    source_version = str(plan.get("package_version") or "")
    output_root = shot_service.shot_root(identity) / "output" / "review" / "animation"
    output_dir = output_root / args.output_version
    plan["source_package_version"] = source_version
    plan["output_version"] = args.output_version
    plan["output_dir"] = str(output_dir)
    plan["scene_path"] = str(
        output_dir / f"{identity.shot}_animation_review_{args.output_version}.mb"
    )
    manifest = read_json(plan["animation_manifest"], {}) or {}
    animated_cast = set((manifest.get("casts") or {}).keys())
    preview = shot_service.build_preview(identity, department="anim")
    static_items = [
        item
        for item in preview
        if item.status == "resolved" and item.cast_key not in animated_cast
    ]
    unresolved = [
        item
        for item in preview
        if item.required and item.status != "resolved" and item.cast_key not in animated_cast
    ]
    if unresolved:
        details = ", ".join(f"{item.cast_key}: {item.message}" for item in unresolved)
        raise RuntimeError(f"Required static cast is unresolved: {details}")

    _write_status(status_path, state="BUILDING", progress=15, task="Build Scene")
    result = build_animation_review_scene(
        plan,
        shot_service.load_shot(identity),
        static_items,
        project_root=config.project_root,
    )
    cameras = list(result.get("cameras") or [])
    if not cameras:
        raise RuntimeError("No published camera was reconstructed.")

    shot_data = shot_service.load_shot(identity)
    review_spec, review_spec_path = shot_service.resolved_review_spec(
        identity,
        department="anim",
    )
    frame_range = result.get("frame_range") or plan.get("frame_range") or [1001, 1001]
    start, end = int(frame_range[0]), int(frame_range[1])
    resolution = shot_data.get("resolution") or [960, 540]
    width, height = int(resolution[0]), int(resolution[1])
    if width <= 0 or height <= 0:
        width, height = 960, 540
    review_layers = _review_layer_specs(
        cmds,
        review_spec=review_spec,
        result=result,
        cameras=cameras,
        default_resolution=(width, height),
        default_frame_range=(start, end),
        published_contract=_latest_preview_render_contract(
            shot_service,
            identity,
            "anim",
        ),
    )
    if not review_layers:
        raise RuntimeError("No populated Review Layer could be reconstructed from review_spec.json.")
    cmds.file(save=True)

    sequence_patterns = {}
    layer_results = {}
    output_records = {}
    layer_count = max(1, len(review_layers))
    for layer_index, layer in enumerate(review_layers):
        _activate_review_layer(cmds, review_layers, layer["name"])
        progress_start = 30 + int((layer_index / layer_count) * 55)
        progress_end = 30 + int(((layer_index + 1) / layer_count) * 55)
        camera = layer["camera"]
        layer_width, layer_height = layer["resolution"]
        layer_start, layer_end = layer["frame_range"]
        layer_name = _clean_name(layer["name"])
        layer_output_dir = output_dir / "playblast" / layer_name
        output_pattern = (
            f"{config.project_name}_{identity.episode}_{identity.sequence}_"
            f"{identity.shot}_anim_{layer_name}_{args.output_version}_####.png"
        )
        pattern = _render_camera_sequence(
            cmds,
            camera=camera,
            output_dir=layer_output_dir,
            start=layer_start,
            end=layer_end,
            width=layer_width,
            height=layer_height,
            status_path=status_path,
            progress_start=progress_start,
            progress_end=progress_end,
            output_pattern=output_pattern,
        )
        sequence_patterns[layer["name"]] = pattern
        first_file = layer_output_dir / output_pattern.replace(
            "####", f"{layer_start:04d}"
        )
        last_file = layer_output_dir / output_pattern.replace(
            "####", f"{layer_end:04d}"
        )
        output_records[layer["name"]] = {
            "first_file": str(first_file),
            "last_file": str(last_file),
            "file_count": max(1, layer_end - layer_start + 1),
            "members": list(layer.get("members") or []),
        }
        layer_results[layer["name"]] = {
            **layer,
            "sequence": pattern,
            "version": args.output_version,
        }

    primary_layer = next(
        (layer["name"] for layer in review_layers if layer["name"].upper() == "CHA"),
        review_layers[-1]["name"],
    )
    fps = float(shot_data.get("fps") or shot_service.project_fps)
    movie_path = output_dir / f"{identity.shot}_animation_review_{args.output_version}.mov"
    published_review_project = _latest_review_project(shot_service, identity, "anim")
    working_review_project = output_dir / "review_project.aep"
    if published_review_project:
        _write_status(status_path, state="BUILDING", progress=88, task="Relink AE Footage")
        _prepare_review_project_copy(
            published_review_project,
            working_review_project,
            output_records,
            config,
        )
        _write_status(status_path, state="BUILDING", progress=90, task="Render AE MOV")
        _render_review_project(working_review_project, movie_path, config)
        movie_message = str(movie_path)
    else:
        movie_message = "Preview Render output is ready; publish/build it in Smart AE Browser."

    result.update(
        {
            "output_version": args.output_version,
            "source_package_version": source_version,
            "playblast_sequences": sequence_patterns,
            "review_layers": layer_results,
            "primary_layer": primary_layer,
            "primary_camera": layer_results[primary_layer]["camera"],
            "published_review_project": str(published_review_project)
            if published_review_project
            else "",
            "review_project": str(working_review_project)
            if working_review_project.is_file()
            else "",
            "movie": str(movie_path) if movie_path.is_file() else "",
            "resolution": [width, height],
            "fps": fps,
            "review_spec": str(review_spec_path),
            "review_spec_version": str(review_spec.get("version") or "draft"),
        }
    )
    build_manifest = shot_service.write_animation_review_build_manifest(plan, result)
    _update_output_history(output_root, args.output_version, movie_path)
    _write_status(
        status_path,
        state="COMPLETE",
        progress=100,
        task="Complete",
        message=movie_message,
    )
    print(
        json.dumps(
            {
                "movie": str(movie_path) if movie_path.is_file() else "",
                "review_project": str(working_review_project)
                if working_review_project.is_file()
                else "",
                "scene": result["scene_path"],
                "build_manifest": str(build_manifest),
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build one animation review output.")
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--episode", required=True)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--shot", required=True)
    parser.add_argument("--output-version", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--operation", default="AUTO")
    parser.add_argument("--department", default="anim")
    parser.add_argument("--task-name", default="")
    parser.add_argument("--scope", choices=("shot", "sequence"), default="shot")
    parser.add_argument("--shots-json", default="[]")
    parser.add_argument("--sequence-options-json", default="{}")
    parser.add_argument("--overrides-json", default="{}")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as exc:
        details = traceback.format_exc()
        _write_status(
            Path(args.status_file),
            state="FAILED",
            progress=100,
            task="Failed",
            message=details,
        )
        print(f"Review build failed: {exc}\n{details}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
