from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig, current_project_config
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import configured_project_paths
from smartlib.core.versioning import format_version, next_version, parse_version


@dataclass(frozen=True)
class SequencerShot:
    node: str
    shot: str
    camera: str
    camera_shape: str
    lens: float | None
    fstop: float | None
    start: int
    end: int
    duration: int
    track: int | None = None
    preview_locked: bool = False


@dataclass(frozen=True)
class EditorialShot:
    shot: str
    cut_in: int
    cut_out: int
    duration: int


@dataclass(frozen=True)
class ValidationIssue:
    shot: str
    severity: str
    message: str


def list_sequencer_shots() -> list[SequencerShot]:
    cmds = _maya_cmds()
    locks = {}
    try:
        project_config = current_project_config()
        if project_config and project_config.project_root:
            episode, sequence = scene_episode_sequence(project_config.project_root)
            locks = _preview_locks(project_config.project_root, episode, sequence)
    except Exception:
        locks = {}
    rows = []
    for node in sorted(cmds.ls(type="shot") or [], key=_shot_sort_key):
        camera = _query_shot(cmds, node, "currentCamera") or ""
        camera_shape = _camera_shape(cmds, camera)
        start = int(round(_query_time(cmds, node, "sequenceStartTime", "sequenceStartFrame", "startTime")))
        end = int(round(_query_time(cmds, node, "sequenceEndTime", "sequenceEndFrame", "endTime")))
        rows.append(
            SequencerShot(
                node=node,
                shot=_display_shot_name(node),
                camera=camera,
                camera_shape=camera_shape,
                lens=_get_optional_attr(cmds, camera_shape, "focalLength"),
                fstop=_get_optional_attr(cmds, camera_shape, "fStop"),
                start=start,
                end=end,
                duration=max(0, end - start + 1),
                track=_get_optional_int_attr(cmds, node, "track"),
                preview_locked=_display_shot_name(node) in locks,
            )
        )
    return rows


def set_camera_lens(shot_node: str, value: float) -> None:
    cmds = _maya_cmds()
    camera_shape = _camera_shape(cmds, _query_shot(cmds, shot_node, "currentCamera") or "")
    if not camera_shape:
        raise RuntimeError(f"Camera was not found for shot: {shot_node}")
    cmds.setAttr(f"{camera_shape}.focalLength", float(value))


def set_camera_fstop(shot_node: str, value: float) -> None:
    cmds = _maya_cmds()
    camera_shape = _camera_shape(cmds, _query_shot(cmds, shot_node, "currentCamera") or "")
    if not camera_shape:
        raise RuntimeError(f"Camera was not found for shot: {shot_node}")
    attr = f"{camera_shape}.fStop"
    if not cmds.objExists(attr):
        raise RuntimeError(f"Camera does not have fStop attribute: {camera_shape}")
    cmds.setAttr(attr, float(value))


def set_sequence_range(shots: list[SequencerShot] | None = None) -> tuple[int, int]:
    cmds = _maya_cmds()
    start, end = sequence_range(shots)
    cmds.playbackOptions(minTime=start, maxTime=end, animationStartTime=start, animationEndTime=end)
    cmds.currentTime(start, edit=True)
    return start, end


def set_selected_range(shot_nodes: list[str]) -> tuple[int, int]:
    cmds = _maya_cmds()
    start, end = selected_shot_range(shot_nodes)
    cmds.playbackOptions(minTime=start, maxTime=end, animationStartTime=start, animationEndTime=end)
    cmds.currentTime(start, edit=True)
    return start, end


def sequence_range(shots: list[SequencerShot] | None = None) -> tuple[int, int]:
    shots = shots or list_sequencer_shots()
    if not shots:
        raise RuntimeError("No camera sequencer shots were found.")
    return min(shot.start for shot in shots), max(shot.end for shot in shots)


def selected_shot_range(shot_nodes: list[str]) -> tuple[int, int]:
    selected_nodes = set(shot_nodes)
    selected = [shot for shot in list_sequencer_shots() if shot.node in selected_nodes]
    if not selected:
        raise RuntimeError("Select one or more sequencer shots.")
    return min(shot.start for shot in selected), max(shot.end for shot in selected)


def move_time_to_sequence_start() -> int:
    shots = list_sequencer_shots()
    start, _end = sequence_range(shots)
    target = min(shots, key=lambda shot: shot.start)
    _set_current_time(start, target)
    return start


def move_time_to_sequence_end() -> int:
    shots = list_sequencer_shots()
    _start, end = sequence_range(shots)
    target = max(shots, key=lambda shot: shot.end)
    _set_current_time(end, target)
    return end


def move_time_to_selected_start(shot_nodes: list[str]) -> int:
    selected = _selected_shots(shot_nodes)
    start = min(shot.start for shot in selected)
    target = min(selected, key=lambda shot: shot.start)
    _set_current_time(start, target, [shot.node for shot in selected])
    return start


def move_time_to_selected_end(shot_nodes: list[str]) -> int:
    selected = _selected_shots(shot_nodes)
    end = max(shot.end for shot in selected)
    target = max(selected, key=lambda shot: shot.end)
    _set_current_time(end, target, [shot.node for shot in selected])
    return end


def _selected_shots(shot_nodes: list[str]) -> list[SequencerShot]:
    selected_nodes = set(shot_nodes)
    selected = [shot for shot in list_sequencer_shots() if shot.node in selected_nodes]
    if not selected:
        raise RuntimeError("Select one or more sequencer shots.")
    return selected


def _set_current_time(frame: int, target_shot: SequencerShot | None = None, shot_nodes: list[str] | None = None) -> None:
    cmds = _maya_cmds()
    frame = int(frame)
    cmds.currentTime(frame, edit=True)
    if shot_nodes:
        try:
            cmds.select(shot_nodes, replace=True)
        except Exception:
            pass
    elif target_shot:
        try:
            cmds.select(target_shot.node, replace=True)
        except Exception:
            pass
    if target_shot and target_shot.camera:
        _prepare_playblast_view(cmds, target_shot.camera)
    _sync_camera_sequencer(cmds, frame, target_shot)


def _sync_camera_sequencer(cmds: Any, frame: int, target_shot: SequencerShot | None = None) -> None:
    for manager in cmds.ls(type="sequenceManager") or []:
        for attr in ("currentTime", "time", "sequenceTime"):
            if cmds.objExists(f"{manager}.{attr}"):
                try:
                    cmds.setAttr(f"{manager}.{attr}", frame)
                except Exception:
                    pass
        for args, kwargs in (
            ((manager,), {"edit": True, "currentTime": frame}),
            ((manager,), {"edit": True, "currentShot": target_shot.node if target_shot else ""}),
            ((), {"edit": True, "currentTime": frame}),
            ((), {"edit": True, "currentShot": target_shot.node if target_shot else ""}),
        ):
            if not kwargs.get("currentShot") and "currentShot" in kwargs:
                continue
            try:
                cmds.sequenceManager(*args, **kwargs)
            except Exception:
                pass
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def move_selected_shots(shot_nodes: list[str], frame_delta: int) -> None:
    if not shot_nodes:
        raise RuntimeError("Select one or more sequencer shots.")
    if frame_delta == 0:
        return
    cmds = _maya_cmds()
    shots = [shot for shot in list_sequencer_shots() if shot.node in set(shot_nodes)]
    for shot in shots:
        _set_shot_range(cmds, shot.node, shot.start + frame_delta, shot.end + frame_delta)
        _move_keys(cmds, shot.start, shot.end, frame_delta)
    moved = _selected_shots(shot_nodes)
    target = min(moved, key=lambda shot: shot.start)
    _set_current_time(target.start, target, [shot.node for shot in moved])


def scale_selected_shot_duration(shot_node: str, new_duration: int) -> None:
    if new_duration <= 0:
        raise RuntimeError("Scale duration must be greater than zero.")
    cmds = _maya_cmds()
    shots = list_sequencer_shots()
    shot = next((row for row in shots if row.node == shot_node), None)
    if shot is None:
        raise RuntimeError(f"Shot was not found: {shot_node}")
    old_duration = max(1, shot.duration)
    delta = new_duration - old_duration
    new_end = shot.start + new_duration - 1
    scale = float(new_duration) / float(old_duration)

    _scale_keys(cmds, shot.start, shot.end, scale)
    _set_shot_range(cmds, shot.node, shot.start, new_end)
    if delta:
        for later in shots:
            if later.start > shot.start and later.node != shot.node:
                _set_shot_range(cmds, later.node, later.start + delta, later.end + delta)
        _move_keys(cmds, shot.end + 1, 10000000, delta)


def validate_against_editorial(project_config: ProjectConfig) -> tuple[dict[str, EditorialShot], list[ValidationIssue]]:
    editorial = official_editorial_shots(project_config)
    by_shot = {row.shot: row for row in editorial}
    issues: list[ValidationIssue] = []
    scene_shots = list_sequencer_shots()
    scene_by_name = {row.shot: row for row in scene_shots}

    for scene_shot in scene_shots:
        official = by_shot.get(scene_shot.shot)
        if official is None:
            issues.append(ValidationIssue(scene_shot.shot, "WARNING", "not found in official editorial"))
            continue
        if scene_shot.start != official.cut_in or scene_shot.end != official.cut_out:
            issues.append(
                ValidationIssue(
                    scene_shot.shot,
                    "WARNING",
                    f"range differs: scene {scene_shot.start}-{scene_shot.end}, official {official.cut_in}-{official.cut_out}",
                )
            )

    for official in editorial:
        if official.shot not in scene_by_name:
            issues.append(ValidationIssue(official.shot, "WARNING", "missing in Maya sequencer"))
    return by_shot, issues


def official_editorial_shots(project_config: ProjectConfig) -> list[EditorialShot]:
    project_root = project_config.project_root
    if project_root is None:
        raise RuntimeError("project_root is not set in templates_base.yml")
    episode, sequence = scene_episode_sequence(project_root)
    latest = configured_project_paths(
        project_root, project_config
    ).editorial_sequence_publish_root(episode, sequence) / "latest.json"
    latest_data = read_json(latest, {}) or {}
    path_text = str(latest_data.get("path") or "").strip()
    editorial_json = (latest.parent / path_text) if path_text else None
    if editorial_json and editorial_json.name == "editorial.json" and editorial_json.exists():
        data = read_json(editorial_json, {}) or {}
    else:
        version = str(latest_data.get("version") or "").strip()
        if not version:
            raise FileNotFoundError(f"Editorial latest.json was not found or invalid: {latest}")
        data = read_json(latest.parent / version / "metadata" / "editorial.json", {}) or {}
    shots = []
    for row in data.get("shots") or []:
        try:
            cut_in = int(row["cut_in"])
            cut_out = int(row["cut_out"])
        except Exception:
            continue
        shots.append(
            EditorialShot(
                shot=str(row.get("shot") or ""),
                cut_in=cut_in,
                cut_out=cut_out,
                duration=max(0, cut_out - cut_in + 1),
            )
        )
    if not shots:
        raise RuntimeError("No shots were found in official editorial data.")
    return shots


def scene_episode_sequence(project_root: Path) -> tuple[str, str]:
    cmds = _maya_cmds()
    scene = Path(cmds.file(query=True, sceneName=True) or "")
    paths = configured_project_paths(project_root)
    if scene:
        try:
            relative = scene.resolve().relative_to(paths.sequences_root().resolve())
            if len(relative.parts) >= 2:
                return relative.parts[0], relative.parts[1]
        except Exception:
            pass
        try:
            relative = scene.resolve().relative_to(paths.shots_root().resolve())
            if len(relative.parts) >= 2:
                return relative.parts[0], relative.parts[1]
        except Exception:
            pass
    shots = list_sequencer_shots()
    if shots:
        # Fall back to the first shot metadata if a later build embeds it.
        for attr_name in ("smartEpisode", "episode"):
            value = _get_string_attr(cmds, shots[0].node, attr_name)
            if value:
                sequence = _get_string_attr(cmds, shots[0].node, "smartSequence") or _get_string_attr(cmds, shots[0].node, "sequence")
                if sequence:
                    return value, sequence
    raise RuntimeError("Could not resolve episode/sequence from current scene path.")


def export_selected_preview(
    project_config: ProjectConfig,
    shot_nodes: list[str],
    dept: str = "layout",
    playblast_preset: str = "",
) -> Path:
    cmds = _maya_cmds()
    project_root = project_config.project_root
    if project_root is None:
        raise RuntimeError("project_root is not set in templates_base.yml")
    episode, sequence = scene_episode_sequence(project_root)
    shots = [shot for shot in list_sequencer_shots() if shot.node in set(shot_nodes)]
    if not shots:
        raise RuntimeError("Select one or more sequencer shots.")
    if scene_is_sequence_workspace(project_root) or scene_shot_name(project_root) == "all":
        return export_all_layout_preview(project_config, shots, dept=dept, playblast_preset=playblast_preset)
    written = []
    for shot in shots:
        version_dir = _next_review_version(project_root, episode, sequence, shot.shot, dept)
        take_dir = version_dir / "01"
        take_dir.mkdir(parents=True, exist_ok=True)
        output_stem = take_dir / "beauty"
        if shot.camera:
            cmds.lookThru(shot.camera)
        from smartlib.dcc.maya.playblast_preset import applied_playblast_preset

        with applied_playblast_preset(project_config, playblast_preset):
            _prepare_playblast_view(cmds, shot.camera)
            cmds.playblast(
                startTime=shot.start,
                endTime=shot.end,
                format="image",
                filename=str(output_stem),
                forceOverwrite=True,
                sequenceTime=False,
                clearCache=True,
                viewer=False,
                showOrnaments=False,
                percent=100,
                compression="jpg",
                widthHeight=[1280, 720],
            )
        _normalize_playblast_sequence(take_dir, "beauty", shot.start, shot.end, ".jpg")
        review_json = {
            "episode": episode,
            "sequence": sequence,
            "shot": shot.shot,
            "dept": dept,
            "playblast_preset": playblast_preset,
            "record_type": "output",
            "output_type": "review",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "camera_publish_state": _preview_locks(project_root, episode, sequence).get(shot.shot, {}),
            "frame_range": [shot.start, shot.end],
            "layers": {
                "camera": {
                    "file": "01/beauty_####.jpg",
                    "camera": shot.camera,
                    "resolution": [1280, 720],
                    "order": 0,
                }
            },
        }
        write_json(version_dir / "review.json", review_json)
        write_json(version_dir / "output.json", {"record_type": "output", "output_type": "review", "subset": dept, "version": version_dir.name})
        write_json(version_dir.parent / "latest.json", {"version": version_dir.name, "path": f"{version_dir.name}/review.json"})
        _update_versions(version_dir.parent / "versions.json", version_dir.name)
        written.append(str(version_dir))
    return Path(written[-1])


def publish_selected_cameras(
    project_config: ProjectConfig,
    shot_nodes: list[str],
    camera_variant: str = "main",
) -> Path:
    cmds = _maya_cmds()
    project_root = project_config.project_root
    if project_root is None:
        raise RuntimeError("project_root is not set in templates_base.yml")
    episode, sequence = scene_episode_sequence(project_root)
    camera_variant = _clean_folder_name(camera_variant or "main")
    shots = [shot for shot in list_sequencer_shots() if shot.node in set(shot_nodes)]
    if not shots:
        raise RuntimeError("Select one or more sequencer shots.")

    written = []
    for shot in sorted(shots, key=lambda item: item.start):
        if not shot.camera or not cmds.objExists(shot.camera):
            raise RuntimeError(f"Camera was not found for shot: {shot.shot}")
        version_dir = _next_sequence_camera_version(project_root, episode, sequence, shot.shot, camera_variant)
        version_label = version_dir.name
        version_dir.mkdir(parents=True, exist_ok=True)
        ma_path = version_dir / "camera.ma"
        usd_path = version_dir / "camera.usd"
        camera_json_path = version_dir / "camera.json"

        _export_camera_ma(cmds, shot.camera, ma_path)
        usd_error = _export_camera_usd(cmds, shot.camera, usd_path)
        camera_data = {
            "publish_type": "camera",
            "subset": camera_variant,
            "version": version_label,
            "episode": episode,
            "sequence": sequence,
            "shot": shot.shot,
            "camera": shot.camera,
            "camera_shape": shot.camera_shape,
            "lens": shot.lens,
            "fstop": shot.fstop,
            "frame_range": [shot.start, shot.end],
            "duration": shot.duration,
            "source_scene": str(Path(cmds.file(query=True, sceneName=True) or "")),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "animation": _camera_animation_samples(cmds, shot.camera, shot.camera_shape, shot.start, shot.end),
        }
        if usd_error:
            camera_data["usd_export_error"] = usd_error
        write_json(camera_json_path, camera_data)

        files = {
            "ma": "camera.ma",
            "json": "camera.json",
        }
        if usd_path.exists():
            files["usd"] = "camera.usd"
        publish_data = {
            "publish_type": "camera",
            "subset": camera_variant,
            "version": version_label,
            "episode": episode,
            "sequence": sequence,
            "shot": shot.shot,
            "files": files,
            "source_scene": camera_data["source_scene"],
            "status": "latest",
        }
        write_json(version_dir / "publish.json", publish_data)
        _update_latest_and_versions(version_dir.parent, version_label, filename="camera.json")
        _lock_preview_for_camera(project_root, episode, sequence, shot, camera_variant, version_label, version_dir)
        written.append(version_dir)
    return written[-1]


def set_picture_in_picture(enabled: bool) -> int:
    cmds = _maya_cmds()
    image_planes = cmds.ls(type="imagePlane") or []
    for image_plane in image_planes:
        if enabled:
            _store_image_plane_state(cmds, image_plane)
            _set_if_exists(cmds, image_plane, "sizeX", 0.28)
            _set_if_exists(cmds, image_plane, "sizeY", 0.1575)
            _set_if_exists(cmds, image_plane, "offsetX", 0.34)
            _set_if_exists(cmds, image_plane, "offsetY", 0.18)
            _set_if_exists(cmds, image_plane, "depth", 10.0)
            _set_if_exists(cmds, image_plane, "displayOnlyIfCurrent", True)
        else:
            _restore_image_plane_state(cmds, image_plane)
    return len(image_planes)


def export_all_layout_preview(
    project_config: ProjectConfig,
    shots: list[SequencerShot],
    dept: str = "layout",
    playblast_preset: str = "",
) -> Path:
    cmds = _maya_cmds()
    project_root = project_config.project_root
    if project_root is None:
        raise RuntimeError("project_root is not set in templates_base.yml")
    episode, sequence = scene_episode_sequence(project_root)
    project_name = project_config.project_name
    paths = configured_project_paths(project_root, project_config)
    sequence_review_root = paths.sequence_workspace_root(episode, sequence) / "output" / "review" / dept
    main_version_dir = _next_all_review_version(sequence_review_root)
    version_label = main_version_dir.name
    main_version_dir.mkdir(parents=True, exist_ok=True)

    editorial_latest = paths.editorial_sequence_publish_root(
        episode, sequence
    ) / "latest.json"
    editorial_latest_data = read_json(editorial_latest, {}) or {}
    editorial_otio = ""
    if editorial_latest_data.get("version"):
        candidate = editorial_latest.parent / str(editorial_latest_data["version"]) / "cut.otio"
        if candidate.exists():
            editorial_otio = _relative_to_project(candidate, project_root)

    review_shots = {}
    exported_shots = []
    frame_start = min(shot.start for shot in shots)
    frame_end = max(shot.end for shot in shots)
    for shot in sorted(shots, key=lambda item: item.start):
        exported_shots.append(shot.shot)
        camera_name = "cam"
        shot_version_dir = _next_all_review_shot_version(sequence_review_root, shot.shot, camera_name)
        shot_version_label = shot_version_dir.name
        take_dir = shot_version_dir / "01"
        take_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{project_name}_{dept}_{shot.shot}_{shot_version_label}"
        output_stem = take_dir / stem
        if shot.camera:
            cmds.lookThru(shot.camera)
        from smartlib.dcc.maya.playblast_preset import applied_playblast_preset

        with applied_playblast_preset(project_config, playblast_preset):
            _prepare_playblast_view(cmds, shot.camera)
            cmds.playblast(
                startTime=shot.start,
                endTime=shot.end,
                format="image",
                filename=str(output_stem),
                forceOverwrite=True,
                sequenceTime=False,
                clearCache=True,
                viewer=False,
                showOrnaments=False,
                percent=100,
                compression="png",
                widthHeight=[1280, 720],
            )
        _normalize_playblast_sequence(take_dir, stem, shot.start, shot.end, ".png")
        first_file = take_dir / f"{stem}_{shot.start:04d}.png"
        last_file = take_dir / f"{stem}_{shot.end:04d}.png"
        pattern = _relative_to(main_version_dir, take_dir / f"{stem}_####.png")
        review_shots[shot.shot] = {
            "shot": shot.shot,
            "camera": shot.camera,
            "camera_folder": camera_name,
            "take": "01",
            "sequence_range": [shot.start, shot.end],
            "frame_range": [shot.start, shot.end],
            "duration": shot.duration,
            "version": shot_version_label,
            "main_version": version_label,
            "file": pattern,
            "first_file": _relative_to(main_version_dir, first_file),
            "last_file": _relative_to(main_version_dir, last_file),
            "file_count": _count_sequence_files(take_dir, stem, shot.start, shot.end, ".png"),
            "camera_publish_state": _preview_locks(project_root, episode, sequence).get(shot.shot, {}),
        }
        write_json(
            shot_version_dir / "output.json",
            {
                "record_type": "output",
                "output_type": "review",
                "subset": dept,
                "version": shot_version_label,
                "take": "01",
                "shot": shot.shot,
                "files": {"beauty": f"01/{stem}_####.png"},
                "source_package": _relative_to_project(main_version_dir / "review.json", project_root),
            },
        )
        _update_latest_and_versions(sequence_review_root / shot.shot / camera_name, shot_version_label)

    review_json = {
        "record_type": "output",
        "output_type": "review",
        "subset": dept,
        "version": version_label,
        "episode": episode,
        "sequence": sequence,
        "shot": "sequence",
        "department": dept,
        "playblast_preset": playblast_preset,
        "fps": int(_get_fps(cmds)),
        "frame_range": [frame_start, frame_end],
        "editorial_otio": editorial_otio,
        "rv_mode": "editorial_sequence",
        "shots": review_shots,
        "exported_shots": exported_shots,
        "skipped_locked_shots": [],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(main_version_dir / "review.json", review_json)
    write_json(
        main_version_dir / "output.json",
        {
            "record_type": "output",
            "output_type": "review",
            "subset": dept,
            "version": version_label,
            "files": {"review_json": "review.json"},
            "status": "latest",
        },
    )
    _update_latest_and_versions(sequence_review_root / "main", version_label)
    return main_version_dir


def _next_review_version(project_root: Path, episode: str, sequence: str, shot: str, dept: str) -> Path:
    base = configured_project_paths(project_root).shot_output_root(episode, sequence, shot) / "review" / dept / "cam"
    versions = [version for version in (parse_version(path.name) for path in base.glob("v*") if path.is_dir()) if version]
    return base / format_version(next_version(versions))


def _next_all_review_version(all_review_root: Path) -> Path:
    base = all_review_root / "main"
    versions = [version for version in (parse_version(path.name) for path in base.glob("v*") if path.is_dir()) if version]
    return base / format_version(next_version(versions))


def _next_all_review_shot_version(all_review_root: Path, shot: str, camera: str = "camera") -> Path:
    base = all_review_root / shot / camera
    versions = [version for version in (parse_version(path.name) for path in base.glob("v*") if path.is_dir()) if version]
    return base / format_version(next_version(versions))


def _next_sequence_camera_version(project_root: Path, episode: str, sequence: str, shot: str, camera_variant: str) -> Path:
    base = configured_project_paths(project_root).sequence_publish_dir(episode, sequence, "camera") / shot / camera_variant
    versions = [version for version in (parse_version(path.name) for path in base.glob("v*") if path.is_dir()) if version]
    return base / format_version(next_version(versions))


def _preview_lock_path(project_root: Path, episode: str, sequence: str) -> Path:
    return configured_project_paths(project_root).sequence_publish_dir(episode, sequence, "review") / "layout" / "preview_locks.json"


def _preview_locks(project_root: Path, episode: str, sequence: str) -> dict[str, Any]:
    data = read_json(_preview_lock_path(project_root, episode, sequence), {}) or {}
    return data if isinstance(data, dict) else {}


def _lock_preview_for_camera(
    project_root: Path,
    episode: str,
    sequence: str,
    shot: SequencerShot,
    camera_variant: str,
    camera_version: str,
    camera_version_dir: Path,
) -> None:
    locks = _preview_locks(project_root, episode, sequence)
    locks[shot.shot] = {
        "locked": True,
        "reason": "camera_published",
        "camera_option": camera_variant,
        "camera_version": camera_version,
        "camera_publish": _relative_to_project(camera_version_dir / "publish.json", project_root),
        "camera": shot.camera,
        "frame_range": [shot.start, shot.end],
        "locked_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(_preview_lock_path(project_root, episode, sequence), locks)


def _update_latest_and_versions(base_dir: Path, version_label: str, filename: str = "review.json") -> None:
    write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/{filename}"})
    _update_versions(base_dir / "versions.json", version_label)


def _update_versions(path: Path, version_label: str) -> None:
    rows = read_json(path, []) or []
    next_rows = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        next_rows.append({"version": row.get("version"), "status": "approved" if row.get("status") == "latest" else row.get("status", "")})
    next_rows.append({"version": version_label, "status": "latest"})
    write_json(path, next_rows)


def scene_shot_name(project_root: Path) -> str:
    cmds = _maya_cmds()
    scene = Path(cmds.file(query=True, sceneName=True) or "")
    if scene:
        try:
            relative = scene.resolve().relative_to(configured_project_paths(project_root).shots_root().resolve())
            if len(relative.parts) >= 3:
                return relative.parts[2]
        except Exception:
            pass
    return ""


def scene_is_sequence_workspace(project_root: Path) -> bool:
    cmds = _maya_cmds()
    scene = Path(cmds.file(query=True, sceneName=True) or "")
    if not scene:
        return False
    try:
        relative = scene.resolve().relative_to(configured_project_paths(project_root).sequences_root().resolve())
    except Exception:
        return False
    return len(relative.parts) >= 2


def _normalize_playblast_sequence(directory: Path, stem: str, start_frame: int, end_frame: int, suffix: str) -> None:
    for frame in range(start_frame, end_frame + 1):
        frame_text = f"{frame:04d}"
        target = directory / f"{stem}_{frame_text}{suffix}"
        candidates = [
            directory / f"{stem}.{frame_text}{suffix}",
            directory / f"{stem}_.{frame_text}{suffix}",
            directory / f"{stem}_{frame_text}{suffix}",
        ]
        for source in candidates:
            if source == target or not source.exists():
                continue
            if target.exists():
                target.unlink()
            source.rename(target)
            break


def _count_sequence_files(directory: Path, stem: str, start_frame: int, end_frame: int, suffix: str) -> int:
    return sum(1 for frame in range(start_frame, end_frame + 1) if (directory / f"{stem}_{frame:04d}{suffix}").exists())


def _relative_to(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _relative_to_project(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def _get_fps(cmds: Any) -> int:
    unit = str(cmds.currentUnit(query=True, time=True))
    return {
        "film": 24,
        "pal": 25,
        "ntsc": 30,
        "show": 48,
        "palf": 50,
        "ntscf": 60,
    }.get(unit, 24)


def _camera_folder_name(camera: str) -> str:
    name = str(camera or "camera").split("|")[-1].split(":")[-1]
    return _clean_folder_name(name or "camera")


def _clean_folder_name(value: str) -> str:
    clean = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in str(value or ""))
    return clean.strip("_") or "main"


def _export_camera_ma(cmds: Any, camera: str, path: Path) -> None:
    selection = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(camera, replace=True)
        cmds.file(
            str(path),
            force=True,
            options="v=0;",
            type="mayaAscii",
            preserveReferences=True,
            exportSelected=True,
            channels=True,
            constraints=True,
            expressions=True,
            constructionHistory=True,
        )
    finally:
        try:
            cmds.select(selection, replace=True)
        except Exception:
            cmds.select(clear=True)


def _camera_animation_samples(cmds: Any, camera: str, camera_shape: str, start: int, end: int) -> list[dict[str, Any]]:
    current = cmds.currentTime(query=True)
    samples: list[dict[str, Any]] = []
    try:
        for frame in range(int(start), int(end) + 1):
            cmds.currentTime(frame, edit=True)
            try:
                matrix = cmds.xform(camera, query=True, worldSpace=True, matrix=True)
            except Exception:
                matrix = []
            samples.append(
                {
                    "frame": frame,
                    "world_matrix": [float(value) for value in matrix] if matrix else [],
                    "lens": _get_optional_attr(cmds, camera_shape, "focalLength"),
                    "fstop": _get_optional_attr(cmds, camera_shape, "fStop"),
                }
            )
    finally:
        try:
            cmds.currentTime(current, edit=True)
        except Exception:
            pass
    return samples


def _export_camera_usd(cmds: Any, camera: str, path: Path) -> str:
    selection = cmds.ls(selection=True, long=True) or []
    try:
        try:
            cmds.loadPlugin("mayaUsdPlugin", quiet=True)
        except Exception:
            pass
        cmds.select(camera, replace=True)
        try:
            cmds.file(
                str(path),
                force=True,
                type="USD Export",
                exportSelected=True,
                preserveReferences=True,
            )
            return ""
        except Exception as exc:
            if hasattr(cmds, "mayaUSDExport"):
                try:
                    cmds.mayaUSDExport(file=str(path), selection=True)
                    return ""
                except Exception as usd_exc:
                    return str(usd_exc)
            return str(exc)
    finally:
        try:
            cmds.select(selection, replace=True)
        except Exception:
            cmds.select(clear=True)


def _store_image_plane_state(cmds: Any, image_plane: str) -> None:
    attr = f"{image_plane}.smartPipOriginal"
    if not cmds.objExists(attr):
        try:
            cmds.addAttr(image_plane, longName="smartPipOriginal", dataType="string")
        except Exception:
            return
    existing = ""
    try:
        existing = cmds.getAttr(attr) or ""
    except Exception:
        existing = ""
    if existing:
        return
    data = {}
    for name in ("sizeX", "sizeY", "offsetX", "offsetY", "depth", "displayOnlyIfCurrent"):
        full = f"{image_plane}.{name}"
        if cmds.objExists(full):
            try:
                data[name] = cmds.getAttr(full)
            except Exception:
                pass
    try:
        cmds.setAttr(attr, json.dumps(data), type="string")
    except Exception:
        pass


def _restore_image_plane_state(cmds: Any, image_plane: str) -> None:
    attr = f"{image_plane}.smartPipOriginal"
    raw = ""
    if cmds.objExists(attr):
        try:
            raw = cmds.getAttr(attr) or ""
        except Exception:
            raw = ""
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    for name, value in data.items():
        _set_if_exists(cmds, image_plane, name, value)
    if cmds.objExists(attr):
        try:
            cmds.setAttr(attr, "", type="string")
        except Exception:
            pass


def _set_if_exists(cmds: Any, node: str, attr: str, value: Any) -> None:
    full = f"{node}.{attr}"
    if not cmds.objExists(full):
        return
    try:
        cmds.setAttr(full, value)
    except Exception:
        pass


def _prepare_playblast_view(cmds: Any, camera: str) -> None:
    panel = _active_model_panel(cmds)
    if panel:
        try:
            cmds.setFocus(panel)
        except Exception:
            pass
        if camera:
            try:
                cmds.lookThru(panel, camera)
            except Exception:
                try:
                    cmds.lookThru(camera)
                except Exception:
                    pass
    elif camera:
        try:
            cmds.lookThru(camera)
        except Exception:
            pass


def _active_model_panel(cmds: Any) -> str:
    panel = cmds.getPanel(withFocus=True)
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
        return panel
    panels = cmds.getPanel(type="modelPanel") or []
    return panels[0] if panels else ""


def _set_shot_range(cmds: Any, node: str, start: int, end: int) -> None:
    start = int(start)
    end = int(end)
    for start_flag, end_flag in (
        ("startTime", "endTime"),
        ("sequenceStartTime", "sequenceEndTime"),
    ):
        try:
            cmds.shot(node, edit=True, **{start_flag: start, end_flag: end})
        except Exception:
            continue
    for attr, value in (
        ("startFrame", start),
        ("endFrame", end),
        ("sequenceStartFrame", start),
        ("sequenceEndFrame", end),
        ("startTime", start),
        ("endTime", end),
        ("sequenceStartTime", start),
        ("sequenceEndTime", end),
    ):
        if cmds.objExists(f"{node}.{attr}"):
            try:
                cmds.setAttr(f"{node}.{attr}", value)
            except Exception:
                pass


def _move_keys(cmds: Any, start: int, end: int, frame_delta: int) -> None:
    if frame_delta == 0:
        return
    try:
        cmds.keyframe(edit=True, time=(start, end), relative=True, timeChange=frame_delta)
    except Exception:
        pass


def _scale_keys(cmds: Any, start: int, end: int, scale: float) -> None:
    try:
        cmds.scaleKey(t=(start, end), ts=scale, tp=start)
        return
    except Exception:
        pass
    _scale_keys_manually(cmds, start, end, scale)


def _scale_keys_manually(cmds: Any, start: int, end: int, scale: float) -> None:
    anim_curve_types = (
        "animCurveTA",
        "animCurveTL",
        "animCurveTT",
        "animCurveTU",
        "animCurveUA",
        "animCurveUL",
        "animCurveUT",
        "animCurveUU",
    )
    curves: list[str] = []
    for curve_type in anim_curve_types:
        curves.extend(cmds.ls(type=curve_type) or [])
    for curve in sorted(set(curves)):
        try:
            key_times = cmds.keyframe(curve, query=True, time=(start, end), timeChange=True) or []
        except Exception:
            continue
        ordered_times = sorted({float(time) for time in key_times}, reverse=scale >= 1.0)
        for key_time in ordered_times:
            new_time = start + ((key_time - start) * scale)
            try:
                cmds.keyframe(curve, edit=True, time=(key_time, key_time), timeChange=new_time)
            except Exception:
                continue


def _query_shot(cmds: Any, node: str, flag: str) -> Any:
    try:
        return cmds.shot(node, query=True, **{flag: True})
    except Exception:
        return None


def _query_time(cmds: Any, node: str, *flags_or_attrs: str) -> float:
    for name in flags_or_attrs:
        value = _query_shot(cmds, node, name)
        if value is not None:
            return float(value)
        attr = f"{node}.{name}"
        if cmds.objExists(attr):
            try:
                return float(cmds.getAttr(attr))
            except Exception:
                pass
    return 0.0


def _camera_shape(cmds: Any, camera: str) -> str:
    if not camera:
        return ""
    if cmds.nodeType(camera) == "camera":
        return camera
    shapes = cmds.listRelatives(camera, shapes=True, fullPath=False) or []
    for shape in shapes:
        if cmds.nodeType(shape) == "camera":
            return shape
    return ""


def _get_optional_attr(cmds: Any, node: str, attr: str) -> float | None:
    if not node or not cmds.objExists(f"{node}.{attr}"):
        return None
    try:
        return float(cmds.getAttr(f"{node}.{attr}"))
    except Exception:
        return None


def _get_optional_int_attr(cmds: Any, node: str, attr: str) -> int | None:
    value = _get_optional_attr(cmds, node, attr)
    return int(value) if value is not None else None


def _get_string_attr(cmds: Any, node: str, attr: str) -> str:
    full = f"{node}.{attr}"
    if not cmds.objExists(full):
        return ""
    try:
        return str(cmds.getAttr(full) or "")
    except Exception:
        return ""


def _display_shot_name(node: str) -> str:
    name = node.split(":")[-1]
    return name[:-5] if name.endswith("_shot") else name


def _shot_sort_key(node: str) -> tuple[int, str]:
    cmds = _maya_cmds()
    try:
        return int(_query_time(cmds, node, "sequenceStartTime", "sequenceStartFrame", "startTime")), node
    except Exception:
        return 0, node


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Smart Shot is available inside Maya.") from exc
    return cmds
