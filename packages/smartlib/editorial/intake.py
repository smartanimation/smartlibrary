from __future__ import annotations

import csv
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from smartlib.apps.shot_manager.service import ShotCreateRequest, ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from smartlib.core.versioning import format_version, next_version, parse_version


EDITORIAL_CSV_COLUMNS = [
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


@dataclass(frozen=True)
class EditorialEvent:
    episode: str
    sequence: str
    shot: str
    cut_in: int
    cut_out: int
    handle_head: int = 8
    handle_tail: int = 8
    source_in: int = 0
    source_out: int = 0
    event_id: str = ""
    clip: str = ""
    retime: float = 1.0
    hold: bool = False
    note: str = ""
    editorial_segments: list[dict[str, Any]] = field(default_factory=list)

    @property
    def identity(self) -> ShotIdentity:
        return ShotIdentity(self.episode, self.sequence, self.shot)

    @property
    def duration(self) -> int:
        return max(0, self.cut_out - self.cut_in + 1)


@dataclass(frozen=True)
class EditorialIntakeRequest:
    csv_path: Path
    offline_mov: Path | None = None
    comment: str = ""
    work_dir: Path | None = None
    publish_episode: str = ""
    publish_sequence: str = ""
    copy_to_work: bool = True
    publish: bool = True
    register_shots: bool = True


@dataclass(frozen=True)
class EditorialIntakeResult:
    work_dir: Path
    publish_dir: Path | None
    editorial_json: Path | None
    cut_otio: Path | None
    offline_mov: Path | None
    registered_shots: list[ShotIdentity]
    events: list[EditorialEvent]


class EditorialIntakeService:
    """MOV+CSV editorial intake for production shot registration."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.project_root = project_root
        self.shots = ShotManagerService(project_config)

    @property
    def fps(self) -> int:
        return self.shots.project_fps

    @property
    def editorial_root(self) -> Path:
        templates = self.project_config.base.get("templates") or {}
        value = templates.get("editorial_root") or "{project_root}/editorial"
        return Path(_resolve_template(value, self.project_root, templates))

    @property
    def incoming_editorial_dir(self) -> Path:
        templates = self.project_config.base.get("templates") or {}
        value = templates.get("incoming_editorial_dir") or "{project_root}/incoming/editorial"
        return Path(_resolve_template(value, self.project_root, templates))

    def write_csv_template(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=EDITORIAL_CSV_COLUMNS)
            writer.writeheader()
        return output

    def read_events_csv(self, path: str | Path) -> list[EditorialEvent]:
        csv_path = Path(path)
        with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        events = []
        for index, row in enumerate(rows, start=2):
            if not any(str(value or "").strip() for value in row.values()):
                continue
            events.append(_event_from_row(row, index))
        if not events:
            raise ValueError(f"No editorial events found: {csv_path}")
        return events

    def intake(self, request: EditorialIntakeRequest) -> EditorialIntakeResult:
        events = self.read_events_csv(request.csv_path)
        work_dir = request.work_dir or self._detect_editorial_work_dir(request.csv_path) or self._next_work_dir()
        work_dir.mkdir(parents=True, exist_ok=True)

        work_csv = work_dir / "events.csv"
        if request.csv_path.resolve() != work_csv.resolve():
            shutil.copy2(request.csv_path, work_csv)
        work_mov = None
        if request.offline_mov:
            if not request.offline_mov.exists():
                raise FileNotFoundError(f"Offline movie was not found: {request.offline_mov}")
            work_mov = work_dir / request.offline_mov.name
            if request.copy_to_work and request.offline_mov.resolve() != work_mov.resolve():
                shutil.copy2(request.offline_mov, work_mov)
            elif not request.copy_to_work:
                work_mov = request.offline_mov

        sequence_roots = self.ensure_sequence_structures(events)
        registered = self.register_shots(events) if request.register_shots else []
        publish_dir = None
        editorial_json = None
        cut_otio = None
        publish_mov = None
        if request.publish:
            work_manifest = read_json(work_dir / "manifest.json", {}) or {}
            publish_dir = self.publish_cut(
                events,
                work_mov or request.offline_mov,
                request.comment,
                episode=request.publish_episode,
                sequence=request.publish_sequence,
                manifest_data=work_manifest,
            )
            editorial_json = publish_dir / "metadata" / "editorial.json"
            cut_otio = publish_dir / "cut.otio"
            publish_mov = publish_dir / "offline.mov" if (publish_dir / "offline.mov").exists() else None
            if request.register_shots:
                self.write_shot_editorial_snapshots(events, publish_dir)

        return EditorialIntakeResult(
            work_dir=work_dir,
            publish_dir=publish_dir,
            editorial_json=editorial_json,
            cut_otio=cut_otio,
            offline_mov=publish_mov or work_mov,
            registered_shots=registered,
            events=events,
        )

    def ensure_sequence_structures(self, events: list[EditorialEvent]) -> list[Path]:
        written = []
        for (episode, sequence), sequence_events in sorted(_events_by_sequence(events).items()):
            root = self.project_root / "sequences" / episode / sequence
            for path in _sequence_structure_paths(root):
                path.mkdir(parents=True, exist_ok=True)
            cut_in = min(event.cut_in for event in sequence_events)
            cut_out = max(event.cut_out for event in sequence_events)
            sequence_json = {
                "episode": episode,
                "sequence": sequence,
                "status": "wip",
                "editorial": {
                    "fps": self.fps,
                    "cut_in": cut_in,
                    "cut_out": cut_out,
                    "duration": max(0, cut_out - cut_in + 1),
                },
                "shots": [
                    {
                        "shot": event.shot,
                        "cut_in": event.cut_in,
                        "cut_out": event.cut_out,
                        "duration": event.duration,
                    }
                    for event in sorted(sequence_events, key=lambda item: (item.cut_in, item.shot))
                ],
            }
            written.append(write_json(root / "sequence.json", sequence_json))
        return written

    def register_shots(self, events: list[EditorialEvent]) -> list[ShotIdentity]:
        registered = []
        for event in events:
            self.shots.create_shot(
                ShotCreateRequest(
                    episode=event.episode,
                    sequence=event.sequence,
                    shot=event.shot,
                    fps=self.fps,
                    cut_in=event.cut_in,
                    cut_out=event.cut_out,
                    status="wip",
                    create_work_dirs=False,
                )
            )
            registered.append(event.identity)
        return registered

    def publish_cut(
        self,
        events: list[EditorialEvent],
        offline_mov: Path | None,
        comment: str = "",
        *,
        episode: str = "",
        sequence: str = "",
        manifest_data: dict[str, Any] | None = None,
    ) -> Path:
        publish_episode, publish_sequence = _publish_identity(events, episode, sequence)
        base_dir = self.editorial_root / "publish" / publish_episode / publish_sequence
        version_label = format_version(next_version(_existing_versions(base_dir)))
        version_dir = base_dir / version_label
        metadata_dir = version_dir / "metadata"
        storyreel_dir = version_dir / "storyreel"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        storyreel_dir.mkdir(parents=True, exist_ok=True)

        if offline_mov and offline_mov.exists():
            self.write_marker_range_offline_mov(events, offline_mov, version_dir / "offline.mov", manifest_data or {})
        write_json(version_dir / "cut.otio", _placeholder_otio(events, self.fps))
        editorial_json = self._editorial_json(events, version_label, comment, publish_episode, publish_sequence)
        write_json(metadata_dir / "editorial.json", editorial_json)
        write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/metadata/editorial.json"})
        self._update_versions(base_dir / "versions.json", version_label)
        return version_dir

    def write_marker_range_offline_mov(
        self,
        events: list[EditorialEvent],
        source_mov: Path,
        output_mov: Path,
        manifest_data: dict[str, Any] | None = None,
    ) -> Path:
        source_mov = Path(source_mov)
        output_mov.parent.mkdir(parents=True, exist_ok=True)
        if not events:
            shutil.copy2(source_mov, output_mov)
            return output_mov
        trim = _marker_range_trim(events, self.fps, manifest_data or {})
        ffmpeg = self._ffmpeg_path()
        command = [
            str(ffmpeg),
            "-y",
            "-ss",
            _seconds(trim["start_offset_frames"], self.fps),
            "-i",
            str(source_mov),
            "-t",
            _seconds(trim["duration_frames"], self.fps),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_mov),
        ]
        try:
            subprocess.run(command, check=True)
        except Exception:
            fallback = [
                str(ffmpeg),
                "-y",
                "-ss",
                _seconds(trim["start_offset_frames"], self.fps),
                "-i",
                str(source_mov),
                "-t",
                _seconds(trim["duration_frames"], self.fps),
                str(output_mov),
            ]
            subprocess.run(fallback, check=True)
        return output_mov

    def _ffmpeg_path(self) -> Path:
        tools = self.project_config.load("tools.yml")
        raw = (((tools.get("tools") or {}).get("ffmpeg") or {}).get("path") or "").strip()
        if raw:
            raw = raw.replace("{smartpipeline_root}", Path(__file__).resolve().parents[3].as_posix())
            raw = raw.replace("{project_root}", self.project_root.as_posix())
            path = Path(raw)
            if path.exists():
                return path
        path = Path(__file__).resolve().parents[3] / "tools" / "ffmpeg" / "ffmpeg.exe"
        if path.exists():
            return path
        raise FileNotFoundError(f"ffmpeg.exe was not found: {path}")

    def write_shot_editorial_snapshots(self, events: list[EditorialEvent], publish_dir: Path) -> list[Path]:
        written = []
        for event in events:
            shot_root = self.shots.shot_root(event.identity)
            data = self._shot_editorial_json(event, publish_dir)
            written.append(write_json(shot_root / "editorial.json", data))
            shot_json = read_json(shot_root / "shot.json", {}) or {}
            shot_json["editorial"] = {
                "source": _relative_to_project(publish_dir / "cut.otio", self.project_root),
                "fps": self.fps,
                "cut_in": event.cut_in,
                "cut_out": event.cut_out,
                "duration": event.duration,
                "handles": {"head": event.handle_head, "tail": event.handle_tail},
            }
            write_json(shot_root / "shot.json", shot_json)
        return written

    def _next_work_dir(self) -> Path:
        base_dir = self.editorial_root / "work"
        stamp = datetime.now().strftime("%Y%m%d")
        index = 1
        while True:
            candidate = base_dir / f"{stamp}_{index:02d}"
            if not candidate.exists():
                return candidate
            index += 1

    def _detect_editorial_work_dir(self, csv_path: Path) -> Path | None:
        path = csv_path.resolve()
        work_root = (self.editorial_root / "work").resolve()
        try:
            relative = path.relative_to(work_root)
        except ValueError:
            return None
        parts = relative.parts
        if len(parts) >= 4 and parts[2].startswith("v"):
            return work_root / parts[0] / parts[1] / parts[2]
        return None

    def _editorial_json(
        self,
        events: list[EditorialEvent],
        version: str,
        comment: str,
        publish_episode: str,
        publish_sequence: str,
    ) -> dict[str, Any]:
        return {
            "fps": self.fps,
            "version": version,
            "episode": publish_episode,
            "sequence": publish_sequence,
            "comment": comment,
            "shots": [self._shot_row(event) for event in events],
        }

    def _shot_editorial_json(self, event: EditorialEvent, publish_dir: Path) -> dict[str, Any]:
        return {
            "editorial_publish": _relative_to_project(publish_dir, self.project_root),
            "fps": self.fps,
            **self._shot_row(event),
            "storyreel": {
                "image_sequence": f"storyreel/{event.shot}/storyreel_####.jpg",
            },
        }

    @staticmethod
    def _shot_row(event: EditorialEvent) -> dict[str, Any]:
        return {
            "episode": event.episode,
            "sequence": event.sequence,
            "shot": event.shot,
            "cut_in": event.cut_in,
            "cut_out": event.cut_out,
            "duration": event.duration,
            "handles": {"head": event.handle_head, "tail": event.handle_tail},
            "source_event": {
                "event_id": event.event_id,
                "clip": event.clip,
                "source_in": event.source_in,
                "source_out": event.source_out,
                "retime": event.retime,
                "hold": event.hold,
                "note": event.note,
            },
            "editorial_segments": _event_segments(event),
        }

    @staticmethod
    def _update_versions(path: Path, version_label: str) -> Path:
        versions = read_json(path, [])
        rows = []
        for row in versions if isinstance(versions, list) else []:
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    "version": row.get("version"),
                    "status": "approved" if row.get("status") == "latest" else row.get("status", ""),
                }
            )
        rows.append({"version": version_label, "status": "latest"})
        return write_json(path, rows)


def _event_from_row(row: dict[str, Any], line_number: int) -> EditorialEvent:
    try:
        episode = _required(row, "episode")
        sequence = _required(row, "sequence")
        shot = _required(row, "shot")
        cut_in = _int(row.get("cut_in"), "cut_in", line_number)
        cut_out = _int(row.get("cut_out"), "cut_out", line_number)
        return EditorialEvent(
            episode=episode,
            sequence=sequence,
            shot=shot,
            cut_in=cut_in,
            cut_out=cut_out,
            handle_head=_int(row.get("handle_head") or 8, "handle_head", line_number),
            handle_tail=_int(row.get("handle_tail") or 8, "handle_tail", line_number),
            source_in=_int(row.get("source_in") or 0, "source_in", line_number),
            source_out=_int(row.get("source_out") or 0, "source_out", line_number),
            event_id=str(row.get("event_id") or ""),
            clip=str(row.get("clip") or ""),
            retime=_float(row.get("retime") or 1.0, "retime", line_number),
            hold=_bool(row.get("hold")),
            note=str(row.get("note") or ""),
            editorial_segments=_segments_from_row(row, cut_in, cut_out, line_number),
        )
    except Exception as exc:
        raise ValueError(f"Invalid editorial CSV row {line_number}: {exc}") from exc


def _required(row: dict[str, Any], key: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _int(value: Any, label: str, line_number: int) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception as exc:
        raise ValueError(f"{label} must be an integer on line {line_number}") from exc


def _float(value: Any, label: str, line_number: int) -> float:
    try:
        return float(str(value).strip())
    except Exception as exc:
        raise ValueError(f"{label} must be a number on line {line_number}") from exc


def _bool(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on", "hold"}


def _segments_from_row(row: dict[str, Any], cut_in: int, cut_out: int, line_number: int) -> list[dict[str, Any]]:
    raw = str(row.get("segments") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"segments must be JSON on line {line_number}") from exc
        if not isinstance(data, list):
            raise ValueError(f"segments must be a JSON list on line {line_number}")
        return [segment for segment in data if isinstance(segment, dict)]
    source_in = _int(row.get("source_in") or 0, "source_in", line_number)
    source_out = _int(row.get("source_out") or source_in + max(0, cut_out - cut_in), "source_out", line_number)
    if _bool(row.get("hold")):
        return [
            {
                "type": "hold",
                "record_in": cut_in,
                "record_out": cut_out,
                "hold_frame": source_out,
            }
        ]
    return [
        {
            "type": "main",
            "record_in": cut_in,
            "record_out": cut_out,
            "source_in": source_in,
            "source_out": source_out,
        }
    ]


def _event_segments(event: EditorialEvent) -> list[dict[str, Any]]:
    if event.editorial_segments:
        return event.editorial_segments
    if event.hold:
        return [
            {
                "type": "hold",
                "record_in": event.cut_in,
                "record_out": event.cut_out,
                "hold_frame": event.source_out,
            }
        ]
    return [
        {
            "type": "main",
            "record_in": event.cut_in,
            "record_out": event.cut_out,
            "source_in": event.source_in,
            "source_out": event.source_out,
        }
    ]


def _existing_versions(base_dir: Path) -> list[int]:
    if not base_dir.exists():
        return []
    return [
        version
        for version in (parse_version(path.name) for path in base_dir.iterdir() if path.is_dir())
        if version is not None
    ]


def _events_by_sequence(events: list[EditorialEvent]) -> dict[tuple[str, str], list[EditorialEvent]]:
    grouped: dict[tuple[str, str], list[EditorialEvent]] = {}
    for event in events:
        grouped.setdefault((event.episode, event.sequence), []).append(event)
    return grouped


def _marker_range_trim(events: list[EditorialEvent], fps: int, manifest_data: dict[str, Any]) -> dict[str, int]:
    segment_ranges = []
    for event in events:
        for segment in event.editorial_segments or []:
            try:
                record_in = int(segment.get("record_in"))
                record_out = int(segment.get("record_out"))
            except (TypeError, ValueError):
                continue
            segment_ranges.append((record_in, record_out))
    if segment_ranges:
        range_in = min(start for start, _end in segment_ranges)
        range_out = max(end for _start, end in segment_ranges)
        timeline_start = _int_or_default(manifest_data.get("timeline_start_frame"), range_in)
        start_offset = max(0, range_in - timeline_start)
        duration = max(1, range_out - range_in + 1)
        return {"start_offset_frames": start_offset, "duration_frames": duration}

    cut_in = min(event.cut_in for event in events)
    cut_out = max(event.cut_out for event in events)
    return {"start_offset_frames": 0, "duration_frames": max(1, cut_out - cut_in + 1)}


def _seconds(frames: int, fps: int) -> str:
    return f"{max(0, int(frames)) / float(max(1, int(fps))):.6f}"


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _sequence_structure_paths(root: Path) -> list[Path]:
    paths = [
        root / "layout" / "work" / "maya",
        root / "layout" / "work" / "houdini",
    ]
    paths.extend(root / "publish" / publish_type for publish_type in ("camera", "blocking", "staging", "layout", "review"))
    return paths


def _publish_identity(events: list[EditorialEvent], episode: str, sequence: str) -> tuple[str, str]:
    if episode and sequence:
        return episode, sequence
    episodes = [event.episode for event in events if event.episode]
    sequences = [event.sequence for event in events if event.sequence]
    unique_episodes = sorted(set(episodes))
    unique_sequences = sorted(set(sequences))
    publish_episode = episode or (unique_episodes[0] if len(unique_episodes) == 1 else "mixed")
    publish_sequence = sequence or (unique_sequences[0] if len(unique_sequences) == 1 else "mixed")
    return publish_episode, publish_sequence


def _placeholder_otio(events: list[EditorialEvent], fps: int) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Timeline.1",
        "metadata": {
            "smartpipeline_placeholder": True,
            "fps": fps,
        },
        "tracks": [
            {
                "name": "video",
                "children": [
                    {
                        "name": event.shot,
                        "metadata": {
                            "episode": event.episode,
                            "sequence": event.sequence,
                            "event_id": event.event_id,
                        },
                        "source_range": {
                            "start_time": event.source_in,
                            "duration": event.duration,
                        },
                    }
                    for event in events
                ],
            }
        ],
    }


def _resolve_template(value: str, project_root: Path, templates: dict[str, str]) -> str:
    resolved = str(value).replace("{project_root}", project_root.as_posix())
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


def _relative_to_project(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()
