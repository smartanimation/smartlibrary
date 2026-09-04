from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import configured_project_paths
from smartlib.core.versioning import format_version, next_version, parse_version
from smartlib.editorial.policy import editorial_handle_policy


SCHEMA = "smartpipeline.editorial_insert.v2"
REGISTRY_SCHEMA = "smartpipeline.cg_shot_registry.v2"
EVENT_CUSTOM_DATA_KEY = "smartpipeline_editorial_event_id"


@dataclass(frozen=True)
class InsertRequest:
    episode: str
    production_sequence: str
    head_handle: int = 8
    tail_handle: int = 8
    choices: tuple["ShotExportChoice", ...] = ()


@dataclass(frozen=True)
class ShotExportChoice:
    occurrence: int
    action: str = "new"
    fixed_revision: str = ""
    output_clean: bool = True
    output_edit: bool = True


@dataclass(frozen=True)
class InsertShot:
    shot: str
    cg_shot_id: str
    marker_start: int
    cut_duration: int
    mark_in: int
    mark_out: int
    source_tc: str
    occurrence: int = 1
    editorial_event_uid: str = ""


def preview_editorial_insert(
    *, resolve_app: Any, project_config: ProjectConfig, request: InsertRequest,
) -> tuple[list[InsertShot], dict[int, tuple[str, ...]]]:
    project = resolve_app.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline() if project else None
    if not timeline:
        raise RuntimeError("No current DaVinci Resolve timeline.")
    _validate_request_policy(project_config, request)
    if project_config.project_root is None:
        raise RuntimeError("project_root is not configured.")
    paths = configured_project_paths(project_config.project_root, project_config)
    registry_path = paths.editorial_identity_registry_path(request.episode)
    registry = read_json(registry_path, {}) or {"schema": REGISTRY_SCHEMA, "shots": {}, "events": {}}
    registry["schema"] = REGISTRY_SCHEMA
    registry.setdefault("shots", {})
    registry.setdefault("events", {})
    markers = dict(timeline.GetMarkers() or {})
    _ensure_marker_event_ids(timeline, markers)
    shots = build_insert_shots(
        markers, registry=registry, episode=request.episode,
        production_sequence=request.production_sequence,
        timeline_start=int(timeline.GetStartFrame()), timeline_end=int(timeline.GetEndFrame()),
        fps=_timeline_fps(timeline, project_config), head_handle=request.head_handle,
        tail_handle=request.tail_handle,
    )
    write_json(registry_path, registry)
    versions = {
        shot.occurrence: tuple(_shot_revisions(paths, request.episode, shot))
        for shot in shots
    }
    return shots, versions


def export_editorial_insert(
    *, resolve_app: Any, project_config: ProjectConfig, request: InsertRequest,
    ffmpeg_path: str | Path = "P:/dev/smarttools/ffmpeg/ffmpeg.exe",
    font_path: str | Path = "C:/Windows/Fonts/NotoSansJP-VF.ttf",
) -> Path:
    project = resolve_app.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline() if project else None
    if not timeline:
        raise RuntimeError("No current DaVinci Resolve timeline.")
    _validate_request_policy(project_config, request)
    if project_config.project_root is None:
        raise RuntimeError("project_root is not configured.")
    paths = configured_project_paths(project_config.project_root, project_config)
    shots, _versions = preview_editorial_insert(
        resolve_app=resolve_app, project_config=project_config, request=request
    )
    if not shots:
        raise RuntimeError("No timeline markers found.")

    timeline_revision = _next_revision(paths.editorial_revisions_metadata_root(request.episode))
    metadata_dir = paths.editorial_revision_dir(request.episode, timeline_revision)
    metadata_dir.mkdir(parents=True, exist_ok=False)
    publish_root = paths.editorial_episode_publish_root(request.episode)
    choices = {choice.occurrence: choice for choice in request.choices}
    fps = _timeline_fps(timeline, project_config)
    artifacts: list[dict[str, Any]] = []
    render_rows: list[tuple[str, InsertShot, Path, Path, str, ShotExportChoice]] = []
    new_rows = [
        (shot, choices.get(shot.occurrence, ShotExportChoice(shot.occurrence)))
        for shot in shots
    ]
    new_rows = [(shot, choice) for shot, choice in new_rows if choice.action == "new"]
    codec = "reused"
    if new_rows:
        codec = _configure_prores_proxy(project)
        for shot, choice in new_rows:
            media_version = _next_shot_media_version(paths, request.episode, shot)
            storage_id = event_storage_id(shot)
            clean_dir = paths.editorial_event_media_clean_dir(
                request.episode, storage_id, media_version
            )
            edit_dir = paths.editorial_event_media_edit_dir(
                request.episode, storage_id, media_version
            )
            clean_dir.mkdir(parents=True, exist_ok=False)
            edit_dir.mkdir(parents=True, exist_ok=False)
            clean = clean_dir / media_filename(request, shot, media_version, "clean")
            edit = edit_dir / media_filename(request, shot, media_version, "edit")
            settings = {
                "SelectAllFrames": False, "MarkIn": shot.mark_in, "MarkOut": shot.mark_out,
                "TargetDir": clean.parent.as_posix(), "CustomName": clean.stem,
                "ExportVideo": True, "ExportAudio": True,
            }
            if not project.SetRenderSettings(settings):
                raise RuntimeError(
                    f"Resolve rejected render settings for {shot.shot}: "
                    f"MarkIn={shot.mark_in}, MarkOut={shot.mark_out}, "
                    f"TargetDir={clean.parent.as_posix()}"
                )
            job_id = project.AddRenderJob()
            if not job_id:
                raise RuntimeError(f"Resolve could not add a render job for {shot.shot}.")
            render_rows.append((str(job_id), shot, clean, edit, media_version, choice))
        if render_rows and not project.StartRendering([row[0] for row in render_rows], True):
            raise RuntimeError("Resolve could not start Editorial Insert rendering.")
        while project.IsRenderingInProgress():
            time.sleep(0.5)

    rendered = {shot.occurrence: row for row in render_rows for shot in (row[1],)}
    for shot in shots:
        choice = choices.get(shot.occurrence, ShotExportChoice(shot.occurrence))
        base = _artifact_base(request, shot, choice.action)
        if choice.action == "omit":
            artifacts.append(base)
            continue
        if choice.action == "fixed":
            fixed = _fixed_artifact(paths, request.episode, choice.fixed_revision, shot)
            base.update({
                "media_version": choice.fixed_revision,
                "source_timeline_revision": fixed.get("timeline_revision") or fixed.get("source_revision") or "",
                "clean": str(fixed.get("clean") or ""),
                "editorial_primary": str(fixed.get("editorial_primary") or ""),
            })
            artifacts.append(base)
            continue
        row = rendered.get(shot.occurrence)
        if row is None:
            base["export_action"] = "omit"
            artifacts.append(base)
            continue
        job_id, _shot, clean, edit, media_version, _choice = row
        status = project.GetRenderJobStatus(job_id) or {}
        if str(status.get("JobStatus") or "").lower() not in {"complete", "completed"}:
            raise RuntimeError(f"Resolve render failed for {shot.shot}: {status}")
        if not clean.exists():
            raise FileNotFoundError(f"Rendered Clean movie was not found: {clean}")
        burn_in_hud(
            ffmpeg_path=ffmpeg_path, font_path=font_path, clean=clean, output=edit,
            shot=shot, request=request, revision=media_version, fps=fps,
        )
        base.update({
            "media_version": media_version,
            "clean": clean.relative_to(publish_root).as_posix(),
            "editorial_primary": edit.relative_to(publish_root).as_posix(),
        })
        artifacts.append(base)

    manifest = {
        "schema": SCHEMA, "episode": request.episode,
        "timeline_revision": timeline_revision,
        "render": {"format": "QuickTime", "codec": codec, "fps": fps},
        "handle_policy": {"head": request.head_handle, "tail": request.tail_handle},
        "roles": {"edit": "editorial_insert_master", "clean": "clean_source_master"},
        "shots": artifacts,
    }
    mapping_path = paths.editorial_revision_mapping_path(request.episode, timeline_revision)
    write_json(mapping_path, manifest)
    write_json(paths.editorial_episode_publish_root(request.episode) / "latest.json", {
        "timeline_revision": timeline_revision,
        "mapping": f"revisions/metadata/{timeline_revision}/editorial_mapping.json",
    })
    return metadata_dir


def _validate_request_policy(project_config: ProjectConfig, request: InsertRequest) -> None:
    policy = editorial_handle_policy(project_config)
    if (request.head_handle, request.tail_handle) != (policy.head, policy.tail):
        raise ValueError(
            "Editorial Handle Policy mismatch: "
            f"requested H{request.head_handle}/T{request.tail_handle}, "
            f"configured H{policy.head}/T{policy.tail}. Reload the UI."
        )


def _ensure_marker_event_ids(timeline: Any, markers: dict[Any, Any]) -> None:
    updater = getattr(timeline, "UpdateMarkerCustomData", None)
    for key, marker in markers.items():
        raw = str((marker or {}).get("customData") or "")
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {"legacy_custom_data": raw}
        if not isinstance(payload, dict):
            payload = {"legacy_custom_data": raw}
        event_uid = str(payload.get(EVENT_CUSTOM_DATA_KEY) or "")
        if not event_uid:
            event_uid = str(uuid.uuid4())
            payload[EVENT_CUSTOM_DATA_KEY] = event_uid
            if not callable(updater) or not updater(key, json.dumps(payload, ensure_ascii=False)):
                raise RuntimeError(
                    f"Could not persist stable Editorial Event ID on marker at {key}."
                )
        marker["_editorial_event_uid"] = event_uid


def _artifact_base(request: InsertRequest, shot: InsertShot, action: str) -> dict[str, Any]:
    return {
        "shot": shot.shot, "cg_shot_id": shot.cg_shot_id,
        "editorial_event_uid": shot.editorial_event_uid,
        "editorial_event_id": f"E{shot.occurrence:03d}",
        "event_storage_id": event_storage_id(shot),
        "production_sequence": request.production_sequence,
        "cut_duration": shot.cut_duration,
        "head_handle": shot.marker_start - shot.mark_in,
        "tail_handle": shot.mark_out - (shot.marker_start + shot.cut_duration - 1),
        "source_tc": shot.source_tc, "export_action": action,
    }


def event_storage_id(shot: InsertShot) -> str:
    cg_short = shot.cg_shot_id.replace("-", "")[:8]
    event_short = shot.editorial_event_uid.replace("-", "")[:8]
    return f"CGID-{cg_short}_EVID-{event_short}"


def _iter_editorial_mappings(paths: Any, episode: str) -> Iterable[tuple[str, dict[str, Any]]]:
    metadata_root = paths.editorial_revisions_metadata_root(episode)
    for revision_dir in sorted(metadata_root.glob("v*"), reverse=True) if metadata_root.is_dir() else []:
        data = read_json(paths.editorial_revision_mapping_path(episode, revision_dir.name), {}) or {}
        yield revision_dir.name, data
    legacy_root = paths.editorial_episode_revisions_root(episode)
    for revision_dir in sorted(legacy_root.glob("v*"), reverse=True) if legacy_root.is_dir() else []:
        data = read_json(paths.legacy_editorial_revision_mapping_path(episode, revision_dir.name), {}) or {}
        if data:
            yield revision_dir.name, data


def _artifact_matches_shot(row: dict[str, Any], shot: InsertShot) -> bool:
    event_uid = str(row.get("editorial_event_uid") or "")
    if event_uid:
        return event_uid == shot.editorial_event_uid
    return (
        row.get("cg_shot_id") == shot.cg_shot_id
        and row.get("editorial_event_id") == f"E{shot.occurrence:03d}"
    )


def _shot_revisions(paths: Any, episode: str, shot: InsertShot) -> list[str]:
    expected_head = shot.marker_start - shot.mark_in
    expected_tail = shot.mark_out - (shot.marker_start + shot.cut_duration - 1)
    versions: set[str] = set()
    for timeline_revision, data in _iter_editorial_mappings(paths, episode):
        for row in data.get("shots") or []:
            if not _artifact_matches_shot(row, shot) or row.get("export_action") == "omit":
                continue
            if int(row.get("head_handle", -1)) != expected_head or int(row.get("tail_handle", -1)) != expected_tail:
                continue
            versions.add(str(row.get("media_version") or timeline_revision))
    return sorted(versions, key=lambda value: parse_version(value) or -1, reverse=True)


def _fixed_artifact(paths: Any, episode: str, media_version: str, shot: InsertShot) -> dict[str, Any]:
    if not media_version:
        raise ValueError(f"Fixed media version is required for {shot.shot} E{shot.occurrence:03d}.")
    for timeline_revision, data in _iter_editorial_mappings(paths, episode):
        for row in data.get("shots") or []:
            row_version = str(row.get("media_version") or timeline_revision)
            if row_version == media_version and _artifact_matches_shot(row, shot):
                result = dict(row)
                result["timeline_revision"] = timeline_revision
                if not row.get("media_version"):
                    result["clean"] = _legacy_media_reference(timeline_revision, row.get("clean"))
                    result["editorial_primary"] = _legacy_media_reference(
                        timeline_revision, row.get("editorial_primary")
                    )
                return result
    raise ValueError(f"{shot.shot} media {media_version} is not available.")


def _legacy_media_reference(revision: str, value: Any) -> str:
    return f"revisions/{revision}/{value}" if value else ""


def _fixed_media_reference(revision: str, value: Any) -> str:
    """Legacy helper retained for callers of the v1 mapping layout."""
    return f"../{revision}/{value}" if value else ""


def _next_shot_media_version(paths: Any, episode: str, shot: InsertShot) -> str:
    versions = [
        parse_version(path.name)
        for path in paths.editorial_event_media_root(episode, event_storage_id(shot)).glob("v*")
        if path.is_dir()
    ]
    for timeline_revision, data in _iter_editorial_mappings(paths, episode):
        for row in data.get("shots") or []:
            if _artifact_matches_shot(row, shot) and row.get("export_action") != "omit":
                versions.append(parse_version(str(row.get("media_version") or timeline_revision)))
    return format_version(next_version([value for value in versions if value is not None]))


def build_insert_shots(
    markers: dict[Any, Any], *, registry: dict[str, Any], episode: str,
    production_sequence: str, timeline_start: int, timeline_end: int, fps: float,
    head_handle: int, tail_handle: int,
) -> list[InsertShot]:
    registry.setdefault("shots", {})
    registry.setdefault("events", {})
    result = []
    for occurrence, key in enumerate(sorted(markers, key=lambda value: int(float(value))), start=1):
        marker = markers[key] or {}
        shot = str(marker.get("name") or "").strip()
        if not shot:
            continue
        marker_start = timeline_start + int(float(key))
        duration = max(1, int(float(marker.get("duration") or 1)))
        mark_in = max(timeline_start, marker_start - max(0, int(head_handle)))
        mark_out = min(timeline_end - 1, marker_start + duration + max(0, int(tail_handle)) - 1)
        registry_key = f"{episode}/{production_sequence}/{shot}"
        event_uid = str(marker.get("_editorial_event_uid") or uuid.uuid4())
        event_entry = registry["events"].get(event_uid) or {}
        entry = registry["shots"].get(registry_key)
        if event_entry.get("cg_shot_id"):
            entry = {
                "cg_shot_id": str(event_entry["cg_shot_id"]), "episode": episode,
                "production_sequence": production_sequence, "shot": shot,
            }
            registry["shots"].setdefault(registry_key, entry)
        elif not entry:
            entry = {
                "cg_shot_id": str(uuid.uuid4()), "episode": episode,
                "production_sequence": production_sequence, "shot": shot,
            }
            registry["shots"][registry_key] = entry
        registry["events"].setdefault(event_uid, {
            "editorial_event_uid": event_uid, "cg_shot_id": str(entry["cg_shot_id"]),
            "episode": episode, "production_sequence": production_sequence, "shot": shot,
        })
        result.append(InsertShot(
            shot=shot, cg_shot_id=str(entry["cg_shot_id"]), marker_start=marker_start,
            cut_duration=duration, mark_in=mark_in, mark_out=mark_out,
            source_tc=frame_to_timecode(mark_in, fps), occurrence=occurrence,
            editorial_event_uid=event_uid,
        ))
    return result


def media_filename(request: InsertRequest, shot: InsertShot, revision: str, role: str) -> str:
    cg_short = shot.cg_shot_id.replace("-", "")[:8]
    event = f"E{shot.occurrence:03d}"
    return (
        f"{request.episode}_{request.production_sequence}_{shot.shot}"
        f"_CGID-{cg_short}_{event}_{role}_{revision}.mov"
    )

def burn_in_hud(
    *, ffmpeg_path: str | Path, font_path: str | Path, clean: Path, output: Path,
    shot: InsertShot, request: InsertRequest, revision: str, fps: float,
) -> None:
    ffmpeg = Path(ffmpeg_path)
    font = Path(font_path)
    if not ffmpeg.is_file():
        raise FileNotFoundError(f"FFmpeg was not found: {ffmpeg}")
    if not font.is_file():
        raise FileNotFoundError(f"HUD font was not found: {font}")
    head = shot.marker_start - shot.mark_in
    total = shot.mark_out - shot.mark_in + 1
    tail_start = head + shot.cut_duration
    identity = f"{request.episode} / {request.production_sequence} / {shot.shot}"
    top_right = f"EDIT REF {revision}"
    cg_short = shot.cg_shot_id.split("-")[0]
    vf = _hud_filter(
        font=font, identity=identity, top_right=top_right, cg_short=cg_short,
        source_tc=shot.source_tc, fps=fps, head=head, cut=shot.cut_duration,
        tail_start=tail_start, total=total,
    )
    command = [
        str(ffmpeg), "-hide_banner", "-y", "-i", str(clean), "-vf", vf,
        "-c:v", "prores_ks", "-profile:v", "0", "-pix_fmt", "yuv422p10le",
        "-vendor", "apl0", "-c:a", "copy", "-timecode", shot.source_tc,
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0 or not output.exists():
        raise RuntimeError(f"FFmpeg HUD generation failed for {shot.shot}:\n{completed.stderr[-4000:]}")


def _hud_filter(
    *, font: Path, identity: str, top_right: str, cg_short: str,
    source_tc: str, fps: float, head: int, cut: int, tail_start: int, total: int,
) -> str:
    font_value = font.as_posix().replace(":", "\\:")
    common = f"fontfile='{font_value}':fontcolor=white:fontsize=h/36"
    parts = [
        "drawbox=x=0:y=0:w=iw:h=ih*0.07:color=black@0.55:t=fill",
        "drawbox=x=0:y=ih*0.91:w=iw:h=ih*0.09:color=black@0.55:t=fill",
        f"drawtext={common}:text='{_fftext(identity)}':x=w*0.025:y=h*0.02",
        f"drawtext={common}:text='{_fftext(top_right)}':x=w-tw-w*0.025:y=h*0.02",
        f"drawtext={common}:text='CGID\\:{cg_short}  H\\:{head:02d} T\\:{max(0,total-tail_start):02d}':x=w*0.025:y=h*0.935",
        f"drawtext={common}:text='HEAD -%{{eif\\:{head}-n\\:d\\:02}}':x=w*0.38:y=h*0.935:enable='lt(n,{head})'",
        f"drawtext={common}:text='CUT %{{eif\\:n-{head}+1\\:d\\:04}}/{cut:04d}':x=w*0.38:y=h*0.935:enable='between(n,{head},{tail_start-1})'",
        f"drawtext={common}:text='TAIL +%{{eif\\:n-{tail_start}+1\\:d\\:02}}':x=w*0.38:y=h*0.935:enable='gte(n,{tail_start})'",
        f"drawtext={common}:timecode='{source_tc.replace(':', r'\:')}':rate={fps}:x=w-tw-w*0.025:y=h*0.935",
    ]
    return ",".join(parts)


def _configure_prores_proxy(project: Any) -> str:
    formats = project.GetRenderFormats() or {}
    candidates: list[str] = []
    for label, identifier in formats.items():
        if "quicktime" in str(label).lower() or str(identifier).lower() == "mov":
            candidates.extend((str(identifier), str(label)))
    candidates.extend(("mov", "QuickTime"))
    tried: list[str] = []
    for format_id in dict.fromkeys(candidates):
        codecs = project.GetRenderCodecs(format_id) or {}
        tried.append(f"{format_id}: {list(codecs)}")
        match = next(
            (
                (str(description), codec_id)
                for description, codec_id in codecs.items()
                if "prores 422 proxy" in str(description).lower()
                or "prores422proxy" in str(codec_id).replace(" ", "").lower()
            ),
            None,
        )
        if not match:
            continue
        if not project.SetCurrentRenderFormatAndCodec(format_id, match[1]):
            continue
        project.SetCurrentRenderMode(1)
        return match[0]
    raise RuntimeError(
        "Apple ProRes 422 Proxy is not available. Resolve reported " + "; ".join(tried)
    )


def _timeline_fps(timeline: Any, config: ProjectConfig) -> float:
    value = timeline.GetSetting("timelineFrameRate")
    if value not in (None, ""):
        return float(value)
    return float((config.base.get("anchors") or {}).get("fps") or 24)


def frame_to_timecode(frame: int, fps: float) -> str:
    rate = max(1, int(round(float(fps))))
    hours, remain = divmod(max(0, int(frame)), rate * 3600)
    minutes, remain = divmod(remain, rate * 60)
    seconds, frames = divmod(remain, rate)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def _next_revision(root: Path) -> str:
    versions = [parse_version(path.name) for path in root.glob("v*") if path.is_dir()]
    return format_version(next_version([value for value in versions if value is not None]))


def _fftext(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
