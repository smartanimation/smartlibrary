from __future__ import annotations

from pathlib import Path

import pytest

from smartlib.core.config_loader import ProjectConfig
from smartlib.dcc.resolve.export_timeline_csv import (
    _import_editorial_timeline,
    _link_timeline_to_shot_media,
    ingested_editorial_files,
    latest_ingested_offline_movie,
    latest_ingested_shot_media,
    marker_event_rows,
)
from smartlib.editorial import EditorialIntakeRequest, EditorialIntakeService


def _write_config(config_dir: Path, project_root: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "templates_base.yml").write_text(
        "\n".join(
            [
                "anchors:",
                "  project_name: TEST",
                f"  project_root: '{project_root.as_posix()}'",
            ]
        ),
        encoding="utf-8",
    )


def test_ingested_editorial_latest_files_are_resolved(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    _write_config(config_dir, project_root)
    edit_root = project_root / "editorial" / "data" / "ep02" / "s027" / "edit_source"
    offline_root = project_root / "editorial" / "data" / "ep02" / "s027" / "offline"
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
    root = project_root / "editorial" / "data" / "ep02" / "s027" / "shot_media"
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
