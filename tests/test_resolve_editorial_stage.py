from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartlib.core.config_loader import ProjectConfig
from smartlib.apps.editorial_intake.service import SmartEditorialIntakeService
from smartlib.dcc.resolve.export_timeline_csv import (
    _import_editorial_timeline,
    _link_timeline_to_shot_media,
    create_cutting_markers_from_timeline,
    ingested_editorial_files,
    ingested_editorial_episode_sequences,
    latest_ingested_offline_movie,
    latest_ingested_shot_media,
    marker_event_rows,
)
from smartlib.editorial import EditorialEvent, EditorialIntakeRequest, EditorialIntakeService


def _write_config(config_dir: Path, project_root: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "templates_base.yml").write_text(
        "\n".join(
            [
                "anchors:",
                "  project_name: TEST",
                f"  project_root: '{project_root.as_posix()}'",
                "  resolution: [1920, 1080]",
            ]
        ),
        encoding="utf-8",
    )


def test_ingested_editorial_latest_files_are_resolved(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    _write_config(config_dir, project_root)
    edit_root = project_root / "production" / "editorial" / "data" / "ep02" / "s027" / "edit_source"
    offline_root = project_root / "production" / "editorial" / "data" / "ep02" / "s027" / "offline"
    for version in ("v001", "v002"):
        version_dir = edit_root / version
        version_dir.mkdir(parents=True)
        (version_dir / "ep02_s027.aaf").write_bytes(version.encode("ascii"))
    (offline_root / "v001").mkdir(parents=True)
    (offline_root / "v001" / "ep02_s027.mov").write_bytes(b"mov")

    config = ProjectConfig(config_dir)

    assert ingested_editorial_files(
        config,
        "ep02",
        "s027",
        "edit_source",
        extension="aaf",
    ) == [edit_root / "v002" / "ep02_s027.aaf"]
    assert latest_ingested_offline_movie(config, "ep02", "s027") == (
        offline_root / "v001" / "ep02_s027.mov"
    )
    assert ingested_editorial_episode_sequences(config) == [("ep02", "s027")]


def test_ingested_editorial_identities_ignore_files_and_sort(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    _write_config(config_dir, project_root)
    data_root = project_root / "production" / "editorial" / "data"
    for episode, sequence in (("ep03", "s010"), ("ep02", "s027"), ("ep02", "s001")):
        (data_root / episode / sequence).mkdir(parents=True)
    (data_root / "README.txt").write_text("not an episode", encoding="utf-8")
    (data_root / "ep02" / "notes.txt").write_text("not a sequence", encoding="utf-8")

    assert ingested_editorial_episode_sequences(ProjectConfig(config_dir)) == [
        ("ep02", "s001"),
        ("ep02", "s027"),
        ("ep03", "s010"),
    ]


def test_aaf_is_imported_as_current_resolve_timeline(tmp_path: Path) -> None:
    reference = tmp_path / "ep02_s027.aaf"
    reference.write_bytes(b"aaf")
    timeline = object()

    class MediaPool:
        def __init__(self):
            self.calls = []

        def ImportTimelineFromFile(self, path, options=None):
            self.calls.append((path, options))
            return timeline

    class Project:
        def __init__(self):
            self.media_pool = MediaPool()
            self.current = None

        def GetMediaPool(self):
            return self.media_pool

        def SetCurrentTimeline(self, value):
            self.current = value
            return True

    class ProjectManager:
        def __init__(self, project):
            self.project = project

        def GetCurrentProject(self):
            return self.project

    class Resolve:
        def __init__(self, project):
            self.manager = ProjectManager(project)

        def GetProjectManager(self):
            return self.manager

    project = Project()
    result, imported_path = _import_editorial_timeline(
        Resolve(project),
        reference,
        timeline_name="ep02_s027",
    )

    assert result is timeline
    assert imported_path == reference
    assert project.current is timeline
    assert project.media_pool.calls == [
        (
            reference.as_posix(),
            {"timelineName": "ep02_s027", "importSourceClips": True},
        )
    ]


def test_timeline_import_retries_with_options_when_standard_call_returns_none(tmp_path: Path) -> None:
    reference = tmp_path / "ep02_s027.aaf"
    reference.write_bytes(b"aaf")
    timeline = object()

    class MediaPool:
        def __init__(self):
            self.calls = []

        def ImportTimelineFromFile(self, path, options=None):
            self.calls.append((path, options))
            return timeline if options is None else None

    class Project:
        def __init__(self):
            self.media_pool = MediaPool()

        def GetMediaPool(self):
            return self.media_pool

        def SetCurrentTimeline(self, _value):
            return True

    class Manager:
        def __init__(self, project):
            self.project = project

        def GetCurrentProject(self):
            return self.project

    class Resolve:
        def __init__(self, project):
            self.manager = Manager(project)

        def GetProjectManager(self):
            return self.manager

    project = Project()
    result, imported_path = _import_editorial_timeline(
        Resolve(project),
        reference,
        timeline_name="ep02_s027",
    )

    assert result is timeline
    assert imported_path == reference
    assert project.media_pool.calls == [
        (
            reference.as_posix(),
            {"timelineName": "ep02_s027", "importSourceClips": True},
        ),
        (reference.as_posix(), None),
    ]


def test_aaf_falls_back_to_import_into_empty_timeline(tmp_path: Path) -> None:
    reference = tmp_path / "ep02_s027.aaf"
    reference.write_bytes(b"aaf")
    media_dir = tmp_path / "offline" / "v001"
    media_dir.mkdir(parents=True)

    class Timeline:
        def __init__(self):
            self.calls = []

        def ImportIntoTimeline(self, path, options):
            self.calls.append((path, options))
            return True

    class MediaPool:
        def __init__(self):
            self.timeline = Timeline()

        def ImportTimelineFromFile(self, _path, _options=None):
            return None

        def CreateEmptyTimeline(self, name):
            assert name == "ep02_s027"
            return self.timeline

    class Project:
        def __init__(self):
            self.media_pool = MediaPool()
            self.current = None

        def GetMediaPool(self):
            return self.media_pool

        def SetCurrentTimeline(self, value):
            self.current = value
            return True

    class Manager:
        def __init__(self, project):
            self.project = project

        def GetCurrentProject(self):
            return self.project

    class Resolve:
        def __init__(self, project):
            self.manager = Manager(project)

        def GetProjectManager(self):
            return self.manager

    project = Project()
    result, imported_path = _import_editorial_timeline(
        Resolve(project),
        reference,
        timeline_name="ep02_s027",
        source_clips_path=media_dir,
    )

    assert result is project.media_pool.timeline
    assert imported_path == reference
    assert project.current is result
    assert result.calls == [
        (
            reference.as_posix(),
            {
                "autoImportSourceClipsIntoMediaPool": True,
                "ignoreFileExtensionsWhenMatching": True,
                "insertAdditionalTracks": True,
                "sourceClipsPath": media_dir.as_posix(),
            },
        )
    ]


def test_rejected_aaf_falls_back_to_sibling_xml(tmp_path: Path) -> None:
    reference = tmp_path / "ep02_s027.aaf"
    fallback = tmp_path / "ep02_s027.xml"
    reference.write_bytes(b"aaf")
    fallback.write_text("<xmeml/>", encoding="utf-8")
    timeline = object()

    class EmptyTimeline:
        def ImportIntoTimeline(self, _path, _options):
            return False

    class MediaPool:
        def ImportTimelineFromFile(self, path, _options=None):
            return timeline if path.endswith(".xml") else None

        def CreateEmptyTimeline(self, _name):
            return EmptyTimeline()

        def DeleteTimelines(self, _timelines):
            return True

    class Project:
        def __init__(self):
            self.media_pool = MediaPool()

        def GetMediaPool(self):
            return self.media_pool

        def SetCurrentTimeline(self, _value):
            return True

    class Manager:
        def __init__(self, project):
            self.project = project

        def GetCurrentProject(self):
            return self.project

    class Resolve:
        def __init__(self, project):
            self.manager = Manager(project)

        def GetProjectManager(self):
            return self.manager

    result, imported_path = _import_editorial_timeline(
        Resolve(Project()),
        reference,
        timeline_name="ep02_s027",
    )

    assert result is timeline
    assert imported_path == fallback


def test_latest_shot_media_is_resolved_per_shot(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    _write_config(config_dir, project_root)
    root = project_root / "production" / "editorial" / "data" / "ep02" / "s027" / "shot_media"
    expected = []
    for shot in ("c001", "c002"):
        for version in ("v001", "v002"):
            version_dir = root / shot / version
            version_dir.mkdir(parents=True)
            movie = version_dir / f"ep02_s027_{shot}.mov"
            movie.write_bytes(version.encode("ascii"))
        expected.append((shot, root / shot / "v002" / f"ep02_s027_{shot}.mov"))

    assert latest_ingested_shot_media(ProjectConfig(config_dir), "ep02", "s027") == expected


def test_timeline_items_are_linked_to_shot_media_in_record_order(tmp_path: Path) -> None:
    media_paths = []
    for shot in ("c001", "c002"):
        path = tmp_path / f"{shot}.mov"
        path.write_bytes(b"mov")
        media_paths.append((shot, path))

    class MediaPoolItem:
        def __init__(self):
            self.replaced = ""

        def ReplaceClip(self, path):
            self.replaced = path
            return True

    class TimelineItem:
        def __init__(self, start, end):
            self.start = start
            self.end = end
            self.media = MediaPoolItem()

        def GetStart(self):
            return self.start

        def GetEnd(self):
            return self.end

        def GetMediaPoolItem(self):
            return self.media

    second = TimelineItem(1135, 1250)
    first = TimelineItem(1001, 1134)

    class Timeline:
        def GetItemListInTrack(self, track_type, track_index):
            assert (track_type, track_index) == ("video", 1)
            return [second, first]

    links = _link_timeline_to_shot_media(Timeline(), media_paths)

    assert first.media.replaced == media_paths[0][1].as_posix()
    assert second.media.replaced == media_paths[1][1].as_posix()
    assert [link["shot"] for link in links] == ["c001", "c002"]


def test_marker_in_out_controls_events_and_allows_transition_overlap() -> None:
    class Timeline:
        def GetMarkers(self):
            return {
                0: {"duration": 10, "name": "s027"},
                8: {"duration": 5, "name": "s027"},
            }

        def GetStartFrame(self):
            return 86400

    class Project:
        def GetCurrentTimeline(self):
            return Timeline()

    class Manager:
        def GetCurrentProject(self):
            return Project()

    class Resolve:
        def GetProjectManager(self):
            return Manager()

    rows = marker_event_rows(
        resolve_app=Resolve(),
        episode="ep02",
        sequence="s027",
        cut_start_frame=1001,
    )

    assert [(row["cut_in"], row["cut_out"]) for row in rows] == [
        (1001, 1010),
        (1009, 1013),
    ]

    auto_rows = marker_event_rows(
        resolve_app=Resolve(),
        episode="ep02",
        sequence="s027",
    )

    assert [(row["cut_in"], row["cut_out"]) for row in auto_rows] == [
        (86400, 86409),
        (86408, 86412),
    ]


def test_aaf_timeline_clips_create_shot_named_markers_with_edit_durations() -> None:
    class Item:
        def __init__(self, name, start, end, source_start, source_end):
            self.values = name, start, end, source_start, source_end

        def GetName(self):
            return self.values[0]

        def GetStart(self):
            return self.values[1]

        def GetEnd(self):
            return self.values[2]

        def GetSourceStart(self):
            return self.values[3]

        def GetSourceEnd(self):
            return self.values[4]

    class Timeline:
        def __init__(self):
            self.markers = {}

        def GetItemListInTrack(self, track_type, track_index):
            assert (track_type, track_index) == ("video", 1)
            return [
                Item("AAF clip A", 86400, 86534, 10, 144),
                Item("AAF clip B", 86534, 86620, 20, 106),
            ]

        def GetStartFrame(self):
            return 86400

        def AddMarker(self, frame, color, name, note, duration, custom_data):
            self.markers[frame] = {
                "color": color,
                "name": name,
                "note": note,
                "duration": duration,
                "customData": custom_data,
            }
            return True

        def GetMarkers(self):
            return self.markers

        def DeleteMarkerAtFrame(self, frame):
            self.markers.pop(frame, None)
            return True

    timeline = Timeline()

    class Project:
        def GetCurrentTimeline(self):
            return timeline

    class Manager:
        def GetCurrentProject(self):
            return Project()

    class Resolve:
        def GetProjectManager(self):
            return Manager()

    count = create_cutting_markers_from_timeline(
        resolve_app=Resolve(),
        sequence_note="s027",
        shot_prefix="c",
        shot_start=1,
        shot_step=1,
        shot_padding=3,
    )

    assert count == 2
    assert [(frame, marker["name"], marker["note"], marker["duration"]) for frame, marker in timeline.markers.items()] == [
        (0, "c001", "s027", 134),
        (134, "c002", "s027", 86),
    ]
    first_data = json.loads(timeline.markers[0]["customData"])
    assert first_data["sequence"] == "s027"
    assert first_data["shot"] == "c001"
    assert first_data["clip"] == "AAF clip A"
    assert (first_data["source_in"], first_data["source_out"]) == (10, 143)


def test_missing_offline_is_rejected_before_work_or_shot_folders_are_created(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "preflight_config"
    _write_config(config_dir, project_root)
    csv_path = tmp_path / "events.csv"
    csv_path.write_text(
        "episode,sequence,shot,cut_in,cut_out\n"
        "ep02,s027,sh0010,1001,1010\n",
        encoding="utf-8",
    )
    work_dir = project_root / "editorial" / "work" / "ep02" / "s027" / "v001"

    with pytest.raises(FileNotFoundError, match="Offline movie was not found"):
        EditorialIntakeService(ProjectConfig(config_dir)).intake(
            EditorialIntakeRequest(
                csv_path=csv_path,
                offline_mov=tmp_path / "missing.mov",
                work_dir=work_dir,
            )
        )

    assert not work_dir.exists()
    assert not (project_root / "shots").exists()


def test_invalid_marker_duration_is_rejected_before_folders_are_created(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "duration_config"
    _write_config(config_dir, project_root)
    csv_path = tmp_path / "events.csv"
    csv_path.write_text(
        "episode,sequence,shot,cut_in,cut_out\n"
        "ep02,s027,sh0010,1010,1009\n",
        encoding="utf-8",
    )
    work_dir = project_root / "editorial" / "work" / "ep02" / "s027" / "v001"

    with pytest.raises(ValueError, match="invalid marker duration"):
        EditorialIntakeService(ProjectConfig(config_dir)).intake(
            EditorialIntakeRequest(csv_path=csv_path, work_dir=work_dir)
        )

    assert not work_dir.exists()
    assert not (project_root / "shots").exists()


def test_editorial_export_publishes_versioned_shot_timing(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "timing_config"
    _write_config(config_dir, project_root)
    service = EditorialIntakeService(ProjectConfig(config_dir))
    publish_v1 = project_root / "editorial" / "publish" / "ep02" / "s027" / "v001"
    publish_v2 = project_root / "editorial" / "publish" / "ep02" / "s027" / "v002"
    for publish_dir in (publish_v1, publish_v2):
        publish_dir.mkdir(parents=True)
        (publish_dir / "cut.otio").write_text("{}", encoding="utf-8")
    first = EditorialEvent(
        "ep02", "s027", "c001", 278, 411,
        handle_head=12,
        handle_tail=12,
        source_in=20,
        source_out=153,
        event_id="E0001",
        clip="c001.mov",
    )

    service.register_shots([first])
    service.write_shot_editorial_snapshots([first], publish_v1)
    timing_root = service.shots.editorial_timing_root(first.identity)
    timing_v1 = json.loads(
        (timing_root / "v001" / "editorial_timing.json").read_text(encoding="utf-8")
    )
    assert timing_v1["handles"] == {"head": 12, "tail": 12}
    assert timing_v1["source"]["kind"] == "smart_editorial_export"
    assert timing_v1["source"]["editorial_version"] == "v001"
    assert timing_v1["source"]["event_id"] == "E0001"

    # Re-exporting an unchanged event reuses the timing version even when the
    # sequence Editorial Publish itself advances.
    service.register_shots([first])
    service.write_shot_editorial_snapshots([first], publish_v2)
    assert not (timing_root / "v002").exists()

    changed = EditorialEvent(
        "ep02", "s027", "c001", 278, 419,
        handle_head=12,
        handle_tail=12,
        source_in=20,
        source_out=161,
        event_id="E0001",
        clip="c001.mov",
    )
    service.write_shot_editorial_snapshots([changed], publish_v2)
    timing_v2 = json.loads(
        (timing_root / "v002" / "editorial_timing.json").read_text(encoding="utf-8")
    )
    shot_json = json.loads(
        (service.shots.shot_root(first.identity) / "shot.json").read_text(encoding="utf-8")
    )
    assert timing_v2["cut_out"] == 419
    assert timing_v2["work_range"] == [1001, 1166]
    assert shot_json["editorial_timing"]["version"] == "v002"


def test_existing_shot_gets_timing_when_folder_creation_is_disabled(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "existing_timing_config"
    _write_config(config_dir, project_root)
    service = EditorialIntakeService(ProjectConfig(config_dir))
    event = EditorialEvent("ep02", "s027", "c001", 278, 411)
    service.register_shots([event])
    csv_path = tmp_path / "events.csv"
    csv_path.write_text(
        "episode,sequence,shot,cut_in,cut_out,handle_head,handle_tail\n"
        "ep02,s027,c001,278,419,8,8\n",
        encoding="utf-8",
    )

    result = service.intake(
        EditorialIntakeRequest(
            csv_path=csv_path,
            work_dir=tmp_path / "editorial_work",
            publish_episode="ep02",
            publish_sequence="s027",
            register_shots=False,
        )
    )

    assert result.registered_shots == []
    assert len(result.editorial_timings) == 1
    timing = json.loads(result.editorial_timings[0].read_text(encoding="utf-8"))
    assert timing["cut_out"] == 419
    assert timing["source"]["kind"] == "smart_editorial_export"


def test_intake_extracts_versioned_audio_for_each_shot(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "audio_config"
    _write_config(config_dir, project_root)
    service = EditorialIntakeService(ProjectConfig(config_dir))
    events = [
        EditorialEvent("ep02", "s027", "c001", 1001, 1100),
        EditorialEvent("ep02", "s027", "c002", 1101, 1150),
    ]
    service.register_shots(events)
    source = tmp_path / "offline.mov"
    source.write_bytes(b"movie with audio")
    publish_dir = project_root / "editorial" / "publish" / "ep02" / "s027" / "v003"
    monkeypatch.setattr(service, "_source_has_audio", lambda _path: True)
    monkeypatch.setattr(service, "_ffmpeg_path", lambda: tmp_path / "ffmpeg.exe")
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"wav")
        return object()

    monkeypatch.setattr("smartlib.editorial.intake.subprocess.run", fake_run)

    written = service.write_shot_audio(events, source, publish_dir)

    assert [path.name for path in written] == ["c001.wav", "c002.wav"]
    assert commands[0][commands[0].index("-ss") + 1] == "0.000000"
    assert commands[1][commands[1].index("-ss") + 1] == "4.166667"
    latest = json.loads(
        (service.shots.shot_root(events[1].identity) / "data" / "audio" / "latest.json").read_text()
    )
    assert latest["version"] == "v003"
    assert latest["path"].endswith("/data/audio/v003/c002.wav")


def test_editorial_preflight_reports_audio_ready(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "audio_preview_config"
    _write_config(config_dir, project_root)
    csv_path = tmp_path / "events.csv"
    csv_path.write_text(
        "episode,sequence,shot,cut_in,cut_out\n"
        "ep02,s027,c001,1001,1100\n"
        "ep02,s027,c002,1101,1150\n",
        encoding="utf-8",
    )
    movie = tmp_path / "offline.mov"
    movie.write_bytes(b"movie")
    service = SmartEditorialIntakeService(ProjectConfig(config_dir))
    monkeypatch.setattr(service.intake_service, "_source_has_audio", lambda _path: True)
    monkeypatch.setattr(service.intake_service, "source_resolution", lambda _path: (1920, 1080))
    monkeypatch.setattr(
        service.intake_service,
        "detect_black_frames",
        lambda _path: [{"start": 1.0, "end": 1.125, "duration": 0.125}],
    )
    preview = service.inspect(
        "ep02",
        "s027",
        "Latest",
        csv_path=csv_path,
        mov_path=movie,
    )

    assert "audio - READY (2 shots)" in preview.report
    assert "audio - SKIP" not in preview.report
    assert "resolution - OK: 1920x1080" in preview.report
    assert "black frame - WARNING: 1 range(s): 1.000-1.125s" in preview.report


def test_intake_extracts_sequence_audio_for_sequence_recipe(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "sequence_audio_config"
    _write_config(config_dir, project_root)
    service = EditorialIntakeService(ProjectConfig(config_dir))
    events = [
        EditorialEvent("ep02", "s027", "c001", 1001, 1100),
        EditorialEvent("ep02", "s027", "c002", 1101, 1150),
    ]
    source = tmp_path / "offline.mov"
    source.write_bytes(b"movie")
    publish_dir = project_root / "editorial" / "publish" / "ep02" / "s027" / "v004"
    monkeypatch.setattr(service, "_source_has_audio", lambda _path: True)
    monkeypatch.setattr(service, "_ffmpeg_path", lambda: tmp_path / "ffmpeg.exe")

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"wav")
        return object()

    monkeypatch.setattr("smartlib.editorial.intake.subprocess.run", fake_run)

    output = service.write_sequence_audio(events, source, publish_dir)

    expected_root = service.shots.paths.sequence_workspace_root("ep02", "s027") / "data" / "audio"
    assert output == expected_root / "v004" / "ep02_s027.wav"
    latest = json.loads((expected_root / "latest.json").read_text())
    assert latest["version"] == "v004"
    assert latest["cut_in"] == 1001
    assert latest["cut_out"] == 1150


def test_marker_frame_remains_relative_when_offset_exceeds_timeline_start() -> None:
    from smartlib.dcc.resolve.export_timeline_csv import _absolute_marker_frame

    assert _absolute_marker_frame(0, 278) == 278
    assert _absolute_marker_frame(354, 278) == 632
