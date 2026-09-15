import json
from types import SimpleNamespace

import pytest

from smartlib.core.config_loader import ProjectConfig
from smartlib.dcc.resolve import export_timeline_csv as exporter
from test_resolve_editorial_stage import _write_config


XML = '''<xmeml><sequence><rate><timebase>24</timebase></rate><media>
<video><track><clipitem><name>graphic</name><start>120</start><end>144</end><in>0</in><out>24</out></clipitem></track></video>
<audio><track><clipitem><start>0</start><end>300</end></clipitem></track></audio>
</media></sequence></xmeml>'''
EDL = '''TITLE: EDIT
FCM: NON-DROP FRAME
001 AX V C 00:00:00:00 00:00:01:00 01:00:00:00 01:00:01:00
002 AX A C 00:00:00:00 00:00:05:00 01:00:00:00 01:00:05:00
'''


@pytest.mark.parametrize("kind,text,origin,marker_start", [("xml", XML, 0, 120), ("edl", EDL, 86400, 0)])
def test_import_failure_builds_offline_and_picture_markers(tmp_path, monkeypatch, kind, text, origin, marker_start):
    _write_config(tmp_path / "config", tmp_path / "project")
    config = ProjectConfig(tmp_path / "config")
    reference = tmp_path / ("edit." + kind)
    reference.write_text(text)
    created = []
    timeline = SimpleNamespace(start=86400)
    timeline.GetStartFrame = lambda: timeline.start
    timeline.SetStartTimecode = lambda tc: setattr(timeline, "start", exporter._timecode_to_frames(tc)) or True
    timeline.AddMarker = lambda *args: created.append(args) or True
    timeline.GetMarkers = lambda: {args[0]: {"name": args[2]} for args in created}
    monkeypatch.setattr(exporter, "_import_media", lambda *_: ["offline"])
    def fail(*args, **kwargs):
        raise RuntimeError("ImportTimelineFromFile returned None")
    monkeypatch.setattr(exporter, "_import_editorial_timeline", fail)
    monkeypatch.setattr(exporter, "_create_offline_timeline", lambda *a, **k: timeline)
    monkeypatch.setattr(exporter, "_current_timeline", lambda *_: timeline)
    monkeypatch.setattr(exporter, "resolve_project_manifest_data", lambda *_: {})
    work = exporter.stage_editorial_source(project_config=config, episode="op", sequence="op_edit",
        movie_path=tmp_path / "offline.mov", reference_path=reference, reference_type=kind,
        work_dir=tmp_path / "work")
    manifest = json.loads((work / "manifest.json").read_text())
    assert manifest["timeline_import_mode"] == kind + "_markers_on_offline"
    assert "returned None" in manifest["timeline_import_error"]
    assert manifest["timeline_start_frame"] == origin
    assert len(created) == 1
    assert created[0][0] == marker_start
    assert created[0][4] == 24
    assert json.loads(created[0][5])["source_out"] == 23


def test_edl_uses_project_fps_and_rejects_drop_frame(tmp_path):
    path = tmp_path / "edit.edl"
    path.write_text(EDL)
    markers = exporter._parse_edl_markers(path, fps=25)
    assert markers[0]["start"] == 90000
    assert markers[0]["duration"] == 25
    path.write_text(EDL.replace("NON-DROP", "DROP"))
    with pytest.raises(RuntimeError, match="Drop-frame"):
        exporter._parse_edl_markers(path, fps=30)


def test_xml_rejects_fps_mismatch_and_keeps_relative_positions(tmp_path):
    path = tmp_path / "edit.xml"
    path.write_text(XML)
    markers = exporter._parse_xml_markers(path, fps=24)
    assert len(markers) == 1
    assert markers[0]["frame_space"] == "relative"
    with pytest.raises(RuntimeError, match="frame rate"):
        exporter._parse_xml_markers(path, fps=25)


def test_partial_marker_creation_is_not_reported_as_success(monkeypatch):
    timeline = SimpleNamespace(GetStartFrame=lambda: 0, AddMarker=lambda *args: args[0] == 0)
    timeline.GetMarkers = lambda: {0: {"name": "c001"}}
    monkeypatch.setattr(exporter, "_current_timeline", lambda *_: timeline)
    with pytest.raises(RuntimeError, match="1/2"):
        exporter._add_reference_markers(None, [
            {"start": 0, "duration": 10, "frame_space": "relative"},
            {"start": 10, "duration": 10, "frame_space": "relative"},
        ], "edit", "c", 1, 1, 3)
