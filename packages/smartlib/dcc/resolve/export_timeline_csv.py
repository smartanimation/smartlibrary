from __future__ import annotations

import argparse
import csv
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.versioning import format_version, next_version, parse_version


EDITORIAL_COLUMNS = [
    "episode",
    "sequence",
    "shot",
    "cut_in",
    "cut_out",
    "handle_head",
    "handle_tail",
    "source_in",
    "source_out",
    "event_id",
    "clip",
    "retime",
    "hold",
    "note",
    "segments",
]


def shot_naming_rule(
    project_config: ProjectConfig,
    *,
    profile_name: str | None = None,
    prefix: str = "sh",
    start: int = 10,
    step: int = 10,
    padding: int = 4,
) -> dict[str, Any]:
    naming = project_config.load("naming.yml")
    profile_name = str(profile_name if profile_name is not None else (naming.get("resolve_export") or {}).get("shot_naming_profile") or "").strip()
    profiles = ((naming.get("shot_naming") or {}).get("profiles") or {})
    profile = profiles.get(profile_name) or {}
    return {
        "profile": profile_name,
        "prefix": str(profile.get("prefix", prefix)),
        "start": _int_setting(profile.get("start"), start),
        "step": _int_setting(profile.get("step"), step),
        "padding": _int_setting(profile.get("padding"), padding),
    }


def shot_naming_profile_names(project_config: ProjectConfig) -> list[str]:
    naming = project_config.load("naming.yml")
    profiles = ((naming.get("shot_naming") or {}).get("profiles") or {})
    names = sorted(str(name) for name in profiles.keys())
    default = str((naming.get("resolve_export") or {}).get("shot_naming_profile") or "").strip()
    if default and default in names:
        names.remove(default)
        names.insert(0, default)
    return names or [default or "default"]


def export_current_timeline_csv(
    output_csv: str | Path,
    *,
    episode: str,
    sequence: str,
    resolve_app: Any = None,
    track_index: int = 1,
    handle_head: int = 8,
    handle_tail: int = 8,
    shot_prefix: str = "sh",
    shot_start: int = 10,
    shot_step: int = 10,
    shot_padding: int = 4,
    shot_naming_profile: str | None = None,
    cut_start_frame: int = 1001,
) -> Path:
    """Export the current Resolve timeline video track for Editorial Intake."""

    resolve = _resolve_app(resolve_app)
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("No current DaVinci Resolve project.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise RuntimeError("No current DaVinci Resolve timeline.")

    items = timeline.GetItemListInTrack("video", int(track_index)) or []
    timeline_start = int(items[0].GetStart()) if items else 0
    rows = []
    for index, item in enumerate(items, start=0):
        start = int(item.GetStart())
        end = int(item.GetEnd()) - 1
        source_start = int(_call_or_default(item, "GetSourceStart", 0))
        source_end = int(_call_or_default(item, "GetSourceEnd", source_start + max(0, end - start))) - 1
        cut_in = cut_start_frame + (start - timeline_start)
        cut_out = cut_in + max(0, end - start)
        shot_number = shot_start + index * shot_step
        rows.append(
            {
                "episode": episode,
                "sequence": sequence,
                "shot": _format_shot_code(shot_prefix, shot_number, shot_padding),
                "cut_in": cut_in,
                "cut_out": cut_out,
                "handle_head": int(handle_head),
                "handle_tail": int(handle_tail),
                "source_in": source_start,
                "source_out": source_end,
                "event_id": f"E{index + 1:04d}",
                "clip": item.GetName() or "",
                "retime": _retime_value(item),
                "hold": False,
                "note": "",
            }
        )

    output = Path(output_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EDITORIAL_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return output


def export_current_timeline_to_work(
    *,
    project_config: ProjectConfig,
    episode: str,
    sequence: str,
    resolve_app: Any = None,
    track_index: int = 1,
    handle_head: int = 8,
    handle_tail: int = 8,
    shot_prefix: str = "sh",
    shot_start: int = 10,
    shot_step: int = 10,
    shot_padding: int = 4,
    shot_naming_profile: str | None = None,
    cut_start_frame: int = 1001,
) -> Path:
    rule = shot_naming_rule(project_config, profile_name=shot_naming_profile, prefix=shot_prefix, start=shot_start, step=shot_step, padding=shot_padding)
    version_dir = next_editorial_work_version_dir(project_config, episode, sequence)
    return export_current_timeline_csv(
        version_dir / "events.csv",
        episode=episode,
        sequence=sequence,
        resolve_app=resolve_app,
        track_index=track_index,
        handle_head=handle_head,
        handle_tail=handle_tail,
        shot_prefix=rule["prefix"],
        shot_start=rule["start"],
        shot_step=rule["step"],
        shot_padding=rule["padding"],
        cut_start_frame=cut_start_frame,
    )


def export_marker_events_to_work(
    *,
    project_config: ProjectConfig,
    episode: str,
    sequence: str,
    resolve_app: Any = None,
    work_dir: str | Path | None = None,
    handle_head: int = 8,
    handle_tail: int = 8,
    cut_start_frame: int = 1001,
    shot_prefix: str = "sh",
    shot_start: int = 10,
    shot_step: int = 10,
    shot_padding: int = 4,
    shot_naming_profile: str | None = None,
    manifest_data: dict[str, Any] | None = None,
) -> Path:
    rule = shot_naming_rule(project_config, profile_name=shot_naming_profile, prefix=shot_prefix, start=shot_start, step=shot_step, padding=shot_padding)
    version_dir = Path(work_dir) if work_dir else next_editorial_work_version_dir(project_config, episode, sequence)
    rows = marker_event_rows(
        resolve_app=resolve_app,
        episode=episode,
        sequence=sequence,
        handle_head=handle_head,
        handle_tail=handle_tail,
        cut_start_frame=cut_start_frame,
        shot_prefix=rule["prefix"],
        shot_start=rule["start"],
        shot_step=rule["step"],
        shot_padding=rule["padding"],
    )
    output = version_dir / "events.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EDITORIAL_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    write_work_manifest(
        version_dir,
        episode=episode,
        sequence=sequence,
        manifest_data=manifest_data,
    )
    return output


def create_cutting_markers_from_timeline(
    *,
    resolve_app: Any = None,
    track_index: int = 1,
    sequence_note: str = "",
    shot_prefix: str = "sh",
    shot_start: int = 10,
    shot_step: int = 10,
    shot_padding: int = 4,
    marker_color: str = "Blue",
) -> int:
    timeline = _current_timeline(resolve_app)
    items = timeline.GetItemListInTrack("video", int(track_index)) or []
    if not items:
        raise RuntimeError(f"No video clips found on track {track_index}.")
    timeline_start = _timeline_start_frame(timeline)
    count = 0
    for index, item in enumerate(items):
        start = int(item.GetStart())
        end = int(item.GetEnd())
        duration = max(1, end - start)
        shot = _format_shot_code(shot_prefix, shot_start + index * shot_step, shot_padding)
        sequence_name = sequence_note or "sequence"
        custom_data = json.dumps(
            {
                "smartpipeline": "cutting_marker",
                "shot": shot,
                "sequence": sequence_name,
                "clip": item.GetName() or "",
                "source_in": int(_call_or_default(item, "GetSourceStart", 0)),
                "source_out": int(_call_or_default(item, "GetSourceEnd", 0)) - 1,
                "editorial_segments": _default_editorial_segments(start, duration, item),
            },
            ensure_ascii=False,
        )
        frames = _candidate_marker_frames(start, timeline_start)
        ok = _add_marker_any_frame(timeline, frames, marker_color, sequence_name, "", duration, custom_data)
        count += 1 if ok else 0
    if count == 0:
        raise RuntimeError(
            "No cutting markers were created. Check that the timeline is active and marker creation is allowed."
        )
    return count


def marker_event_rows(
    *,
    resolve_app: Any = None,
    episode: str,
    sequence: str,
    handle_head: int = 8,
    handle_tail: int = 8,
    cut_start_frame: int = 1001,
    shot_prefix: str = "sh",
    shot_start: int = 10,
    shot_step: int = 10,
    shot_padding: int = 4,
) -> list[dict[str, Any]]:
    timeline = _current_timeline(resolve_app)
    markers = timeline.GetMarkers() or {}
    if not markers:
        raise RuntimeError("No timeline markers found. Run Markers > New Cutting Marker first.")
    frame_keys = sorted(markers.keys(), key=lambda value: int(float(value)))
    timeline_start = int(float(frame_keys[0]))
    rows = []
    sequence_counts: dict[str, int] = {}
    for index, frame_key in enumerate(frame_keys, start=1):
        marker = markers[frame_key] or {}
        frame = int(float(frame_key))
        duration = int(marker.get("duration") or 1)
        custom = _marker_custom_data(marker)
        marker_sequence = _marker_sequence_name(marker, sequence)
        sequence_index = sequence_counts.get(marker_sequence, 0)
        sequence_counts[marker_sequence] = sequence_index + 1
        shot = _format_shot_code(shot_prefix, shot_start + sequence_index * shot_step, shot_padding)
        source_in = int(custom.get("source_in") or 0)
        source_out = int(custom.get("source_out") or max(0, source_in + duration - 1))
        cut_in = cut_start_frame + (frame - timeline_start)
        segments = _marker_editorial_segments(custom, cut_in, duration, source_in, source_out)
        rows.append(
            {
                "episode": episode,
                "sequence": marker_sequence,
                "shot": shot,
                "cut_in": cut_in,
                "cut_out": cut_in + duration - 1,
                "handle_head": int(handle_head),
                "handle_tail": int(handle_tail),
                "source_in": source_in,
                "source_out": source_out,
                "event_id": f"E{index:04d}",
                "clip": str(custom.get("clip") or ""),
                "retime": 1.0,
                "hold": False,
                "note": str(marker.get("note") or ""),
                "segments": json.dumps(segments, ensure_ascii=False),
            }
        )
    return rows


def stage_editorial_source(
    *,
    project_config: ProjectConfig,
    episode: str,
    sequence: str,
    movie_path: str | Path,
    resolve_app: Any = None,
    reference_path: str | Path | None = None,
    reference_type: str = "",
    work_dir: str | Path | None = None,
    shot_naming_profile: str | None = None,
) -> Path:
    version_dir = Path(work_dir) if work_dir else next_editorial_work_version_dir(project_config, episode, sequence)
    version_dir.mkdir(parents=True, exist_ok=True)
    movie = Path(movie_path)
    _import_media(resolve_app, movie)
    if reference_path:
        rule = shot_naming_rule(project_config, profile_name=shot_naming_profile)
        _create_markers_from_reference_or_timeline(
            resolve_app=resolve_app,
            reference_path=Path(reference_path),
            reference_type=reference_type,
            sequence_note=sequence,
            shot_prefix=rule["prefix"],
            shot_start=rule["start"],
            shot_step=rule["step"],
            shot_padding=rule["padding"],
        )
    write_work_manifest(
        version_dir,
        episode=episode,
        sequence=sequence,
        manifest_data={
            **resolve_project_manifest_data(resolve_app),
            "movie": movie.name,
            "movie_path": movie.as_posix(),
            "reference_file": Path(reference_path).name if reference_path else "",
            "reference_path": Path(reference_path).as_posix() if reference_path else "",
            "reference_type": reference_type,
        },
    )
    return version_dir


def resolve_project_manifest_data(resolve_app: Any = None) -> dict[str, Any]:
    resolve = _resolve_app(resolve_app)
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager else None
    if not project:
        return {}

    data: dict[str, Any] = {
        "resolve_project_name": _call_or_default(project, "GetName", ""),
        "resolve_project_file_path": _project_file_path(project),
    }
    timeline = project.GetCurrentTimeline()
    if timeline:
        data["timeline_start_frame"] = _timeline_start_frame(timeline)
        data["timeline_name"] = _call_or_default(timeline, "GetName", "")
    database = _call_or_default(manager, "GetCurrentDatabase", None)
    if isinstance(database, dict):
        data["resolve_database"] = database
    return data


def write_work_manifest(
    version_dir: str | Path,
    *,
    episode: str,
    sequence: str,
    manifest_data: dict[str, Any] | None = None,
) -> Path:
    version_path = Path(version_dir)
    version_path.mkdir(parents=True, exist_ok=True)
    manifest_path = version_path / "manifest.json"
    existing = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    manifest = {
        "received_at": existing.get("received_at") or datetime.now().replace(microsecond=0).isoformat(),
        "updated_at": datetime.now().replace(microsecond=0).isoformat(),
        "episode": episode,
        "sequence": sequence,
        "movie": "",
        "movie_path": "",
        "reference_file": "",
        "reference_path": "",
        "reference_type": "",
        "resolve_project_name": "",
        "resolve_project_file_path": "",
        "events": "events.csv",
    }
    manifest.update(existing)
    if manifest_data:
        manifest.update({key: value for key, value in manifest_data.items() if value is not None})
    manifest["episode"] = episode
    manifest["sequence"] = sequence
    manifest["updated_at"] = datetime.now().replace(microsecond=0).isoformat()
    with manifest_path.open("w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return manifest_path


def next_editorial_work_version_dir(project_config: ProjectConfig, episode: str, sequence: str) -> Path:
    base_dir = editorial_work_sequence_dir(project_config, episode, sequence)
    return base_dir / format_version(next_version(_editorial_work_versions(base_dir)))


def editorial_work_sequence_dir(project_config: ProjectConfig, episode: str, sequence: str) -> Path:
    project_root = project_config.project_root
    if project_root is None:
        raise RuntimeError("project_root is not set in templates_base.yml")
    templates = project_config.base.get("templates") or {}
    editorial_root = _resolve_template(
        str(templates.get("editorial_root") or "{project_root}/editorial"),
        project_root,
        templates,
    )
    return Path(editorial_root) / "work" / episode / sequence


def editorial_work_versions(project_config: ProjectConfig, episode: str, sequence: str) -> list[str]:
    base_dir = editorial_work_sequence_dir(project_config, episode, sequence)
    return [format_version(version) for version in sorted(_editorial_work_versions(base_dir), reverse=True)]


def latest_editorial_work_version_dir(project_config: ProjectConfig, episode: str, sequence: str) -> Path | None:
    base_dir = editorial_work_sequence_dir(project_config, episode, sequence)
    versions = _editorial_work_versions(base_dir)
    if not versions:
        return None
    return base_dir / format_version(max(versions))


def _editorial_work_versions(base_dir: Path) -> list[int]:
    if not base_dir.exists():
        return []
    return [
        version
        for version in (parse_version(path.name) for path in base_dir.iterdir() if path.is_dir())
        if version is not None
    ]


def current_timeline_media_info(resolve_app: Any = None, track_index: int = 1) -> dict[str, str]:
    timeline = _current_timeline(resolve_app)
    info: dict[str, str] = {
        "Frame Rate": str(timeline.GetSetting("timelineFrameRate") or ""),
        "Resolution": _timeline_resolution(timeline),
        "Duration": _timeline_duration(timeline),
    }
    items = timeline.GetItemListInTrack("video", int(track_index)) or []
    if items:
        media_pool_item = _call_or_default(items[0], "GetMediaPoolItem", None)
        properties = media_pool_item.GetClipProperty() if media_pool_item else {}
        if isinstance(properties, dict):
            info.update(
                {
                    "Video Codec": str(properties.get("Video Codec") or properties.get("Codec") or ""),
                    "Audio Codec": str(properties.get("Audio Codec") or ""),
                    "Color Space": str(properties.get("Color Space") or properties.get("Gamma") or ""),
                }
            )
            if not info.get("Resolution"):
                info["Resolution"] = str(properties.get("Resolution") or "")
            if not info.get("Frame Rate"):
                info["Frame Rate"] = str(properties.get("FPS") or properties.get("Frame Rate") or "")
            if not info.get("Duration"):
                info["Duration"] = str(properties.get("Duration") or "")
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export current DaVinci Resolve timeline to Editorial Intake CSV.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--episode", required=True)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--track", type=int, default=1)
    parser.add_argument("--handle-head", type=int, default=8)
    parser.add_argument("--handle-tail", type=int, default=8)
    parser.add_argument("--shot-prefix", default="sh")
    parser.add_argument("--shot-start", type=int, default=10)
    parser.add_argument("--shot-step", type=int, default=10)
    parser.add_argument("--shot-padding", type=int, default=4)
    parser.add_argument("--cut-start-frame", type=int, default=1001)
    args = parser.parse_args(argv)
    path = export_current_timeline_csv(
        args.output,
        episode=args.episode,
        sequence=args.sequence,
        track_index=args.track,
        handle_head=args.handle_head,
        handle_tail=args.handle_tail,
        shot_prefix=args.shot_prefix,
        shot_start=args.shot_start,
        shot_step=args.shot_step,
        shot_padding=args.shot_padding,
        cut_start_frame=args.cut_start_frame,
    )
    print(f"Exported timeline CSV: {path}")
    return 0


def _resolve_app(explicit_resolve: Any = None) -> Any:
    if explicit_resolve:
        return explicit_resolve

    try:
        resolve = bmd.scriptapp("Resolve")  # type: ignore[name-defined]
        if resolve:
            return resolve
    except NameError:
        pass

    try:
        resolve = app.GetResolve()  # type: ignore[name-defined]
        if resolve:
            return resolve
    except NameError:
        pass

    main_globals = vars(sys.modules.get("__main__"))
    for name in ("resolve", "Resolve"):
        candidate = main_globals.get(name)
        if candidate:
            return candidate
    for name in ("app", "fusion", "fu"):
        candidate = main_globals.get(name)
        getter = getattr(candidate, "GetResolve", None)
        if callable(getter):
            resolve = getter()
            if resolve:
                return resolve
    bmd_module = main_globals.get("bmd")
    scriptapp = getattr(bmd_module, "scriptapp", None)
    if callable(scriptapp):
        resolve = scriptapp("Resolve")
        if resolve:
            return resolve
        fusion = scriptapp("Fusion")
        getter = getattr(fusion, "GetResolve", None)
        if callable(getter):
            resolve = getter()
            if resolve:
                return resolve

    try:
        if resolve:  # type: ignore[name-defined]
            return resolve  # type: ignore[name-defined]
    except NameError:
        pass

    raise RuntimeError(
        "Could not get DaVinci Resolve API object. Run this inside Resolve/Fusion Python "
        "or pass resolve_app=app.GetResolve() explicitly."
    )


def _current_timeline(resolve_app: Any = None) -> Any:
    resolve = _resolve_app(resolve_app)
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("No current DaVinci Resolve project.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise RuntimeError("No current DaVinci Resolve timeline.")
    return timeline


def _project_file_path(project: Any) -> str:
    for method_name in ("GetProjectFilePath", "GetFilePath", "GetPath"):
        value = _call_or_default(project, method_name, "")
        if value:
            return str(value)
    return ""


def _import_media(resolve_app: Any, movie: Path) -> None:
    if not movie.exists():
        raise FileNotFoundError(f"Movie was not found: {movie}")
    resolve = _resolve_app(resolve_app)
    project = resolve.GetProjectManager().GetCurrentProject()
    media_pool = project.GetMediaPool() if project else None
    if not media_pool:
        raise RuntimeError("No Resolve media pool.")
    media_pool.ImportMedia([movie.as_posix()])


def _create_markers_from_reference_or_timeline(
    *,
    resolve_app: Any,
    reference_path: Path,
    reference_type: str,
    sequence_note: str,
    shot_prefix: str = "sh",
    shot_start: int = 10,
    shot_step: int = 10,
    shot_padding: int = 4,
) -> int:
    if reference_type.lower() == "edl":
        markers = _parse_edl_markers(reference_path)
        if markers:
            return _add_reference_markers(resolve_app, markers, sequence_note, shot_prefix, shot_start, shot_step, shot_padding)
    if reference_type.lower() == "xml":
        markers = _parse_xml_markers(reference_path)
        if markers:
            return _add_reference_markers(resolve_app, markers, sequence_note, shot_prefix, shot_start, shot_step, shot_padding)
    return create_cutting_markers_from_timeline(
        resolve_app=resolve_app,
        sequence_note=sequence_note,
        shot_prefix=shot_prefix,
        shot_start=shot_start,
        shot_step=shot_step,
        shot_padding=shot_padding,
    )


def _add_reference_markers(
    resolve_app: Any,
    markers: list[dict[str, Any]],
    sequence_note: str,
    shot_prefix: str,
    shot_start: int,
    shot_step: int,
    shot_padding: int,
) -> int:
    timeline = _current_timeline(resolve_app)
    timeline_start = _timeline_start_frame(timeline)
    count = 0
    for index, marker in enumerate(markers):
        shot = _format_shot_code(shot_prefix, shot_start + index * shot_step, shot_padding)
        start = int(marker["start"])
        duration = max(1, int(marker["duration"]))
        custom_data = json.dumps(
            {
                "smartpipeline": "cutting_marker",
                "shot": shot,
                "sequence": sequence_note,
                "clip": marker.get("clip", ""),
                "source_in": int(marker.get("source_in") or 0),
                "source_out": int(marker.get("source_out") or 0),
                "editorial_segments": marker.get("editorial_segments") or [],
            },
            ensure_ascii=False,
        )
        ok = _add_marker_any_frame(
            timeline,
            _candidate_marker_frames(start, timeline_start),
            "Blue",
            sequence_note,
            "",
            duration,
            custom_data,
        )
        count += 1 if ok else 0
    if count == 0:
        raise RuntimeError("No reference markers were created.")
    return count


def _parse_edl_markers(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    markers = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) < 8 or not parts[0].isdigit():
            continue
        record_in = _timecode_to_frames(parts[-2])
        record_out = _timecode_to_frames(parts[-1])
        if record_in is None or record_out is None:
            continue
        markers.append({"start": record_in, "duration": max(1, record_out - record_in), "clip": parts[1]})
    return markers


def _parse_xml_markers(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    markers = []
    for clip in root.findall(".//clipitem"):
        start = _xml_int(clip.findtext("start"))
        end = _xml_int(clip.findtext("end"))
        if start is None or end is None or end <= start:
            continue
        source_in = _xml_int(clip.findtext("in")) or 0
        source_out = _xml_int(clip.findtext("out")) or max(0, source_in + (end - start) - 1)
        markers.append(
            {
                "start": start,
                "duration": end - start,
                "clip": clip.findtext("name") or "",
                "source_in": source_in,
                "source_out": source_out,
            }
        )
    return markers


def _xml_int(value: str | None) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _timecode_to_frames(value: str, fps: int = 24) -> int | None:
    parts = value.replace(";", ":").split(":")
    if len(parts) != 4:
        return None
    try:
        hours, minutes, seconds, frames = [int(part) for part in parts]
    except ValueError:
        return None
    return (((hours * 60 + minutes) * 60 + seconds) * fps) + frames


def _format_shot_code(prefix: str, number: int, padding: int) -> str:
    return f"{prefix}{int(number):0{max(1, int(padding))}d}"


def _int_setting(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _marker_custom_data(marker: dict[str, Any]) -> dict[str, Any]:
    raw = marker.get("customData") or marker.get("custom_data") or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _marker_sequence_name(marker: dict[str, Any], fallback: str) -> str:
    name = str(marker.get("name") or "").strip()
    if name and not _looks_like_shot_name(name):
        return name
    custom = _marker_custom_data(marker)
    sequence = str(custom.get("sequence") or "").strip()
    return sequence or fallback


def _looks_like_shot_name(value: str) -> bool:
    text = value.strip().lower()
    if text.startswith("sh") and text[2:].isdigit():
        return True
    if text.startswith("c") and text[1:].isdigit():
        return True
    return False


def _default_editorial_segments(record_in: int, duration: int, item: Any) -> list[dict[str, Any]]:
    source_in = int(_call_or_default(item, "GetSourceStart", 0))
    source_out = int(_call_or_default(item, "GetSourceEnd", source_in + duration)) - 1
    return [
        {
            "type": "main",
            "record_in": record_in,
            "record_out": record_in + duration - 1,
            "source_in": source_in,
            "source_out": source_out,
        }
    ]


def _marker_editorial_segments(
    custom: dict[str, Any],
    cut_in: int,
    duration: int,
    source_in: int,
    source_out: int,
) -> list[dict[str, Any]]:
    segments = custom.get("editorial_segments")
    if isinstance(segments, list) and segments:
        return [_normalize_segment(segment, cut_in) for segment in segments if isinstance(segment, dict)]
    return [
        {
            "type": "main",
            "record_in": cut_in,
            "record_out": cut_in + duration - 1,
            "source_in": source_in,
            "source_out": source_out,
        }
    ]


def _normalize_segment(segment: dict[str, Any], cut_in: int) -> dict[str, Any]:
    data = dict(segment)
    if "record_in" in data and "record_out" in data:
        return data
    if "offset_in" in data and "offset_out" in data:
        data["record_in"] = cut_in + int(data.pop("offset_in"))
        data["record_out"] = cut_in + int(data.pop("offset_out"))
    return data


def _call_or_default(item: Any, method_name: str, default: Any) -> Any:
    method = getattr(item, method_name, None)
    if not callable(method):
        return default
    try:
        value = method()
    except Exception:
        return default
    return default if value is None else value


def _retime_value(item: Any) -> float:
    speed = _call_or_default(item, "GetClipProperty", {}) or {}
    if isinstance(speed, dict):
        for key in ("Speed", "speed"):
            value = speed.get(key)
            if value:
                try:
                    return float(str(value).strip("%")) / 100.0
                except ValueError:
                    pass
    return 1.0


def _timeline_resolution(timeline: Any) -> str:
    width = timeline.GetSetting("timelineResolutionWidth") or ""
    height = timeline.GetSetting("timelineResolutionHeight") or ""
    return f"{width}x{height}" if width and height else ""


def _timeline_duration(timeline: Any) -> str:
    try:
        start = int(timeline.GetStartFrame())
        end = int(timeline.GetEndFrame())
        frames = max(0, end - start + 1)
        return f"{frames} frames"
    except Exception:
        return ""


def _timeline_start_frame(timeline: Any) -> int:
    try:
        return int(timeline.GetStartFrame())
    except Exception:
        return 0


def _add_marker(
    timeline: Any,
    frame: int,
    color: str,
    name: str,
    note: str,
    duration: int,
    custom_data: str,
) -> bool:
    if frame < 0:
        return False
    try:
        return bool(timeline.AddMarker(int(frame), color, name, note, int(duration), custom_data))
    except Exception:
        return False


def _add_marker_any_frame(
    timeline: Any,
    frames: list[int],
    color: str,
    name: str,
    note: str,
    duration: int,
    custom_data: str,
) -> bool:
    for frame in frames:
        _delete_marker_if_possible(timeline, frame)
        if _add_marker(timeline, frame, color, name, note, duration, custom_data):
            if _marker_exists(timeline, frame, name):
                return True
    return False


def _candidate_marker_frames(start: int, timeline_start: int) -> list[int]:
    frames = []
    if timeline_start and start >= timeline_start:
        frames.append(start - timeline_start)
    frames.append(start)
    unique = []
    for frame in frames:
        if frame >= 0 and frame not in unique:
            unique.append(frame)
    return unique


def _marker_exists(timeline: Any, frame: int, name: str) -> bool:
    try:
        marker = (timeline.GetMarkers() or {}).get(frame)
        if not marker:
            marker = (timeline.GetMarkers() or {}).get(float(frame))
        return bool(marker) and str(marker.get("name") or "") == name
    except Exception:
        return False


def _delete_marker_if_possible(timeline: Any, frame: int) -> None:
    delete = getattr(timeline, "DeleteMarkerAtFrame", None)
    if not callable(delete):
        return
    try:
        delete(int(frame))
    except Exception:
        pass


def _resolve_template(value: str, project_root: Path, templates: dict[str, str]) -> str:
    resolved = value.replace("{project_root}", project_root.as_posix())
    for _ in range(5):
        changed = False
        for key, template in templates.items():
            token = "{" + key + "}"
            if token in resolved:
                resolved = resolved.replace(token, _resolve_template(str(template), project_root, templates))
                changed = True
        if not changed:
            break
    return resolved


if __name__ == "__main__":
    raise SystemExit(main())
