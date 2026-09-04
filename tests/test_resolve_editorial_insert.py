from pathlib import Path

from smartlib.core.path_resolver import ProjectPaths
from smartlib.dcc.resolve.editorial_insert import (
    _hud_filter,
    _configure_prores_proxy,
    _ensure_marker_event_ids,
    _next_shot_media_version,
    event_storage_id,
    _fixed_artifact,
    _fixed_media_reference,
    _shot_revisions,
    build_insert_shots,
    frame_to_timecode,
)


def test_editorial_revision_paths_are_under_production_editorial(tmp_path: Path):
    paths = ProjectPaths(
        tmp_path,
        templates={
            "production_root": "{project_root}/production",
            "editorial_root": "{production_root}/editorial",
        },
    )
    assert paths.editorial_identity_registry_path("op") == (
        tmp_path / "production/editorial/publish/op/identity/shot_registry.json"
    )
    assert paths.editorial_revision_edit_dir("op", "v001") == (
        tmp_path / "production/editorial/publish/op/revisions/v001/media/edit"
    )
    assert paths.editorial_revision_clean_dir("op", "v001") == (
        tmp_path / "production/editorial/publish/op/revisions/v001/media/clean"
    )


def test_marker_range_includes_handles_and_source_tc_starts_at_head():
    registry = {"shots": {}}
    shots = build_insert_shots(
        {33: {"name": "c001", "duration": 36}},
        registry=registry,
        episode="op",
        production_sequence="op01",
        timeline_start=86400,
        timeline_end=90000,
        fps=24,
        head_handle=8,
        tail_handle=8,
    )
    shot = shots[0]
    assert shot.marker_start == 86433
    assert shot.mark_in == 86425
    assert shot.mark_out == 86476
    assert shot.source_tc == "01:00:01:01"
    assert registry["shots"]["op/op01/c001"]["cg_shot_id"] == shot.cg_shot_id


def test_hud_contains_identity_handles_regions_and_source_timecode():
    value = _hud_filter(
        font=Path("C:/Windows/Fonts/msgothic.ttc"),
        identity="op / op01 / c001",
        top_right="EDIT REF v001",
        cg_short="a84f921c",
        source_tc="01:00:01:01",
        fps=24,
        head=8,
        cut=36,
        tail_start=44,
        total=52,
    )
    assert "op / op01 / c001" in value
    assert "CGID\\:a84f921c  H\\:08 T\\:08" in value
    assert "HEAD -" in value and "CUT " in value and "TAIL +" in value
    assert "01\\:00\\:01\\:01" in value


def test_frame_to_timecode_uses_nominal_24fps():
    assert frame_to_timecode(86433, 24) == "01:00:01:09"

def test_existing_editorial_revision_can_be_fixed_without_rendering(tmp_path: Path):
    from smartlib.core.metadata import write_json

    paths = ProjectPaths(tmp_path)
    shot = build_insert_shots(
        {0: {"name": "c001", "duration": 36}}, registry={"shots": {}},
        episode="op", production_sequence="op01", timeline_start=0,
        timeline_end=100, fps=24, head_handle=0, tail_handle=0,
    )[0]
    mapping = {
        "shots": [{
            "shot": "c001", "cg_shot_id": shot.cg_shot_id,
            "editorial_event_uid": shot.editorial_event_uid,
            "clean": "media/clean/c001.mov",
            "editorial_primary": "media/edit/c001.mov",
            "export_action": "new", "media_version": "v001",
            "head_handle": 0, "tail_handle": 0,
        }]
    }
    write_json(paths.editorial_revision_mapping_path("op", "v001"), mapping)

    assert _shot_revisions(paths, "op", shot) == ["v001"]
    fixed = _fixed_artifact(paths, "op", "v001", shot)
    assert fixed["editorial_primary"] == "media/edit/c001.mov"
    assert _fixed_media_reference("v001", fixed["editorial_primary"]) == (
        "../v001/media/edit/c001.mov"
    )

def test_prores_proxy_uses_resolve_format_identifier_not_display_name():
    class Project:
        selected = None

        def GetRenderFormats(self):
            return {"QuickTime": "mov"}

        def GetRenderCodecs(self, format_id):
            return {"Apple ProRes 422 Proxy": "ProRes422Proxy"} if format_id == "mov" else {}

        def SetCurrentRenderFormatAndCodec(self, format_id, codec_id):
            self.selected = (format_id, codec_id)
            return True

        def SetCurrentRenderMode(self, _mode):
            return True

    project = Project()
    assert _configure_prores_proxy(project) == "Apple ProRes 422 Proxy"
    assert project.selected == ("mov", "ProRes422Proxy")

def test_fixed_revision_requires_exact_effective_handles(tmp_path: Path):
    from smartlib.core.metadata import write_json

    paths = ProjectPaths(tmp_path)
    shot = build_insert_shots(
        {8: {"name": "c001", "duration": 20}}, registry={"shots": {}},
        episode="op", production_sequence="op01", timeline_start=0,
        timeline_end=100, fps=24, head_handle=8, tail_handle=8,
    )[0]
    write_json(paths.editorial_revision_mapping_path("op", "v001"), {
        "shots": [{
            "cg_shot_id": shot.cg_shot_id, "editorial_event_uid": shot.editorial_event_uid,
            "export_action": "new", "head_handle": 0, "tail_handle": 0,
        }]
    })
    write_json(paths.editorial_revision_mapping_path("op", "v002"), {
        "shots": [{
            "cg_shot_id": shot.cg_shot_id, "editorial_event_uid": shot.editorial_event_uid,
            "export_action": "new", "head_handle": 8, "tail_handle": 8,
        }]
    })
    assert _shot_revisions(paths, "op", shot) == ["v002"]

def test_shot_media_paths_separate_timeline_metadata_from_event_versions(tmp_path: Path):
    paths = ProjectPaths(tmp_path)
    assert paths.editorial_revision_mapping_path("op", "v003") == (
        tmp_path / "production/editorial/publish/op/revisions/metadata/v003/editorial_mapping.json"
    )
    assert paths.editorial_event_media_edit_dir(
        "op", "CGID-d1a48b0f_EVID-72a41c9e", "v005"
    ) == (
        tmp_path / "production/editorial/publish/op/revisions/media"
        / "CGID-d1a48b0f_EVID-72a41c9e/v005/edit"
    )


def test_marker_custom_data_persists_stable_editorial_event_id():
    class Timeline:
        def UpdateMarkerCustomData(self, frame_id, value):
            markers[frame_id]["customData"] = value
            return True

    markers = {12.0: {"name": "c001", "duration": 10, "customData": ""}}
    _ensure_marker_event_ids(Timeline(), markers)
    first = markers[12.0]["_editorial_event_uid"]
    del markers[12.0]["_editorial_event_uid"]
    _ensure_marker_event_ids(Timeline(), markers)
    assert markers[12.0]["_editorial_event_uid"] == first


def test_event_identity_preserves_cgid_when_marker_shot_name_changes():
    registry = {"shots": {}, "events": {}}
    first = build_insert_shots(
        {0: {"name": "c001", "duration": 10, "_editorial_event_uid": "event-a"}},
        registry=registry, episode="op", production_sequence="op01",
        timeline_start=0, timeline_end=100, fps=24, head_handle=0, tail_handle=0,
    )[0]
    renamed = build_insert_shots(
        {0: {"name": "c010", "duration": 10, "_editorial_event_uid": "event-a"}},
        registry=registry, episode="op", production_sequence="op01",
        timeline_start=0, timeline_end=100, fps=24, head_handle=0, tail_handle=0,
    )[0]
    assert renamed.cg_shot_id == first.cg_shot_id
    assert event_storage_id(renamed).startswith("CGID-")
    assert "_EVID-eventa" in event_storage_id(renamed)
