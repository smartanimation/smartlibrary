from __future__ import annotations

import argparse
import json
import shutil
import sys
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
    cast_data: dict,
    result: dict,
    cameras: list[str],
    default_resolution: tuple[int, int],
) -> list[dict]:
    cast_nodes = {}
    cast_nodes.update(result.get("cache_nodes") or {})
    cast_nodes.update(result.get("static_nodes") or {})
    specs = []
    for layer_name, contract in sorted(
        (cast_data.get("review_layers") or {}).items(),
        key=lambda item: (int((item[1] or {}).get("order") or 0), item[0]),
    ):
        contract = contract or {}
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
        resolution = contract.get("resolution") or {}
        width = int(resolution.get("width") or default_resolution[0])
        height = int(resolution.get("height") or default_resolution[1])
        camera_contract = contract.get("camera") or {}
        camera = _camera_for_layer(
            cameras,
            str(layer_name),
            str(camera_contract.get("name") or ""),
        )
        specs.append(
            {
                "name": str(layer_name),
                "order": int(contract.get("order") or 0),
                "members": members,
                "nodes": nodes,
                "display_layer": display_layer,
                "camera": camera,
                "resolution": [max(1, width), max(1, height)],
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
) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    if cmds.objExists("defaultRenderGlobals.imageFormat"):
        cmds.setAttr("defaultRenderGlobals.imageFormat", 8)
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
        target = output_dir / f"beauty_{frame:04d}.jpg"
        shutil.copy2(rendered, target)
        progress = progress_start + int(((index + 1) / count) * (progress_end - progress_start))
        _write_status(
            status_path,
            state="BUILDING",
            progress=progress,
            task="Playblast",
            message=f"{camera}: {frame}/{end}",
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


def run(args) -> int:
    status_path = Path(args.status_file)
    _write_status(status_path, state="BUILDING", progress=2, task="Initialize Maya")
    import maya.standalone

    maya.standalone.initialize(name="python")
    import maya.cmds as cmds

    from smartlib.apps.review_build_manager.service import ReviewBuildManagerService
    from smartlib.apps.shot_manager import ShotIdentity
    from smartlib.core.config_loader import ProjectConfig
    from smartlib.core.metadata import read_json
    from smartlib.dcc.maya.shot_builder import build_animation_review_scene
    from smartlib.review.playblast_package import encode_prores_proxy_mov, find_ffmpeg

    config = ProjectConfig(args.config_dir)
    manager = ReviewBuildManagerService(config)
    shot_service = manager.shots
    identity = ShotIdentity(args.episode, args.sequence, args.shot)
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
    cast_data = shot_service.load_cast(identity)
    frame_range = result.get("frame_range") or plan.get("frame_range") or [1001, 1001]
    start, end = int(frame_range[0]), int(frame_range[1])
    resolution = shot_data.get("resolution") or [960, 540]
    width, height = int(resolution[0]), int(resolution[1])
    if width <= 0 or height <= 0:
        width, height = 960, 540
    review_layers = _review_layer_specs(
        cmds,
        cast_data=cast_data,
        result=result,
        cameras=cameras,
        default_resolution=(width, height),
    )
    if not review_layers:
        raise RuntimeError("No populated Review Layer could be reconstructed from cast.json.")
    cmds.file(save=True)

    sequence_patterns = {}
    layer_results = {}
    layer_count = max(1, len(review_layers))
    for layer_index, layer in enumerate(review_layers):
        _activate_review_layer(cmds, review_layers, layer["name"])
        progress_start = 30 + int((layer_index / layer_count) * 55)
        progress_end = 30 + int(((layer_index + 1) / layer_count) * 55)
        camera = layer["camera"]
        layer_width, layer_height = layer["resolution"]
        pattern = _render_camera_sequence(
            cmds,
            camera=camera,
            output_dir=(
                output_dir
                / "playblast"
                / _clean_name(layer["name"])
                / _clean_name(camera)
            ),
            start=start,
            end=end,
            width=layer_width,
            height=layer_height,
            status_path=status_path,
            progress_start=progress_start,
            progress_end=progress_end,
        )
        sequence_patterns[layer["name"]] = pattern
        layer_results[layer["name"]] = {
            **layer,
            "sequence": pattern,
        }

    primary_layer = next(
        (layer["name"] for layer in review_layers if layer["name"].upper() == "CHA"),
        review_layers[-1]["name"],
    )
    primary_camera = layer_results[primary_layer]["camera"]
    fps = float(shot_data.get("fps") or shot_service.project_fps)
    movie_path = output_dir / f"{identity.shot}_animation_review_{args.output_version}.mov"
    _write_status(status_path, state="BUILDING", progress=90, task="Encode MOV")
    ok, encode_message = encode_prores_proxy_mov(
        image_pattern=sequence_patterns[primary_layer],
        mov_path=movie_path,
        start_frame=start,
        fps=fps,
        ffmpeg=find_ffmpeg(config),
    )
    if not ok:
        raise RuntimeError(f"MOV encode failed: {encode_message}")

    result.update(
        {
            "output_version": args.output_version,
            "source_package_version": source_version,
            "playblast_sequences": sequence_patterns,
            "review_layers": layer_results,
            "primary_layer": primary_layer,
            "primary_camera": primary_camera,
            "movie": str(movie_path),
            "resolution": [width, height],
            "fps": fps,
        }
    )
    build_manifest = shot_service.write_animation_review_build_manifest(plan, result)
    _update_output_history(output_root, args.output_version, movie_path)
    _write_status(
        status_path,
        state="COMPLETE",
        progress=100,
        task="Complete",
        message=str(movie_path),
    )
    print(
        json.dumps(
            {
                "movie": str(movie_path),
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
