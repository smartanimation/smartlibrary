import json
import subprocess
from pathlib import Path

import pytest

from smartlib.core.config_loader import ProjectConfig
from smartlib.editorial.intake import EditorialEvent, EditorialIntakeService, EditorialIntakeRequest
from smartlib.editorial.cut_assignment import compile_assignments, default_assignments
from smartlib.editorial.assigned_media import validate_offline_ranges, probe_movie
from smartlib.editorial.storyreel import StoryreelBuilder
from test_resolve_editorial_stage import _write_config


@pytest.fixture
def setup(tmp_path):
    _write_config(tmp_path / "config", tmp_path / "project")
    service = EditorialIntakeService(ProjectConfig(tmp_path / "config"))
    events = [EditorialEvent("op", "op01", "c001", 120, 143, 0, 0),
              EditorialEvent("op", "op01", "c002", 156, 179, 0, 0)]
    rows = default_assignments(events, service.shots)
    for row, start in zip(rows, [278, 326]):
        row.update(work_shot="sh001", maya_in=start, maya_out=start + 23)
    plan = {"schema": "smartpipeline.cut_assignment.v1", "rows": rows,
            "offline_origin": 0, "alignment_confirmed": True}
    return service, events, plan


def test_grouping_preserves_record_and_maya_domains(setup):
    _, events, plan = setup
    selected, production = compile_assignments(events, plan)
    assert selected == events
    assert len(production) == 1
    assert production[0].maya_range == (278, 349)
    assert production[0].editorial_segments[0]["offline_in"] == 120
    assert production[0].editorial_segments[1]["record_in"] == 156
    plan["rows"][0]["enabled"] = False
    selected, production = compile_assignments(events, plan)
    assert len(selected) == 1
    assert production[0].maya_range == (326, 349)


@pytest.mark.parametrize("mutation,message", [
    (lambda p: p.update(alignment_confirmed=False), "Confirm"),
    (lambda p: p["rows"][0].update(signature="old"), "Markers changed"),
    (lambda p: p["rows"][1].update(maya_in=278, maya_out=301), "overlapping"),
    (lambda p: p["rows"][0].update(maya_out=302), "24 frames"),
    (lambda p: p["rows"][0].update(work_shot="../bad"), "invalid"),
])
def test_invalid_assignment_fails_before_publish(setup, mutation, message):
    service, events, plan = setup
    mutation(plan)
    with pytest.raises(ValueError, match=message):
        compile_assignments(events, plan)
    assert not service.project_root.exists()


def test_real_media_offset_grouped_audio_and_timing(setup, tmp_path, monkeypatch):
    service, events, plan = setup
    ffmpeg = Path("P:/dev/smarttools/ffmpeg/ffmpeg.exe")
    if not ffmpeg.is_file():
        pytest.skip("FFmpeg is required for media integration")
    monkeypatch.setattr(service, "_ffmpeg_path", lambda: ffmpeg)
    movie = tmp_path / "offline.mov"
    subprocess.run([str(ffmpeg), "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=24:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
        "-c:v", "libx264", "-c:a", "pcm_s16le", str(movie)], check=True, capture_output=True)
    validate_offline_ranges(service, movie, events, plan)
    invalid = dict(plan, offline_origin=-24)
    with pytest.raises(ValueError, match="outside"):
        validate_offline_ranges(service, movie, events, invalid)
    work = tmp_path / "work"
    work.mkdir()
    csv = work / "events.csv"
    csv.write_text("episode,sequence,shot,cut_in,cut_out,handle_head,handle_tail\n"
        "op,op01,c001,120,143,0,0\nop,op01,c002,156,179,0,0\n")
    result = service.intake(EditorialIntakeRequest(csv_path=csv, offline_mov=movie,
        work_dir=work, cut_assignment=plan))
    assert [identity.shot for identity in result.registered_shots] == ["sh001"]
    timing = json.loads(result.editorial_timings[0].read_text())
    assert timing["cut_range"] == [278, 349]
    assert timing["work_range"] == [278, 349]
    assert timing["source"]["segments"][0]["record_in"] == 120
    metadata = json.loads(result.editorial_json.read_text())
    assert len(metadata["cut_media"]) == 2
    for path in metadata["cut_media"].values():
        assert int(probe_movie(service, path)[1]["nb_frames"]) == 24
    reference = metadata["work_shots"][0]["reference_movie"]
    assert int(probe_movie(service, reference)[1]["nb_frames"]) == 72
    assert int(probe_movie(service, result.offline_mov)[1]["nb_frames"]) == 60
    # The middle 24 frames are silence, not unrelated sequence audio.
    import wave
    with wave.open(str(result.shot_audio[0]), "rb") as sound:
        sound.setpos(48000)
        assert not any(sound.readframes(48000))
    builder = StoryreelBuilder(service.project_config)
    monkeypatch.setattr(builder, "_ffmpeg_path", lambda: ffmpeg)
    reel = builder.build_from_publish(result.publish_dir, execute=False)
    assert reel.results[0].frame_count == 72
    assert "0278" in reel.results[0].first_file.name
    assert reference in reel.results[0].command
    loaded = service.shots.load_shot(result.registered_shots[0])
    assert loaded["editorial"]["cut_range"] == [278, 349]
    anim_path = service.shots.publish_shot_anim_input(result.registered_shots[0],
        cast_publish=service.project_root / "cast.json", placements_publish=None,
        shot_data=loaded, sequence_data={}, overrides={"representation": "maya"})
    anim = json.loads(anim_path.read_text())
    assert anim["work_range"] == [278, 349]
    assert anim["cut_range"] == [278, 349]
    from unittest.mock import MagicMock
    from smartlib.dcc.maya.shot_builder import _apply_shot_timing, _load_shot_audio
    cmds = MagicMock()
    _apply_shot_timing(cmds, loaded)
    cmds.playbackOptions.assert_any_call(minTime=278.0, animationStartTime=278.0)
    cmds.playbackOptions.assert_any_call(maxTime=349.0, animationEndTime=349.0)
    _load_shot_audio(cmds, service.project_root, loaded)
    assert cmds.sound.call_args.kwargs["offset"] == 278


def test_dialog_restores_assignments_and_requires_new_confirmation(setup):
    import tkinter as tk
    from smartlib.dcc.resolve.cut_assignment_ui import CutAssignmentDialog
    service, events, plan = setup
    root = tk.Tk()
    root.withdraw()
    try:
        dialog = CutAssignmentDialog(root, events, service.shots, saved=plan)
        dialog.window.withdraw()
        assert dialog.rows[0]["work_shot"] == "sh001"
        assert "production_sequence" in dialog.columns
        assert not dialog.confirmed.get()
        assert dialog.tree.item("0", "values")[4] == "120"
        dialog.confirmed.set(True)
        dialog.origin.set("1")
        assert not dialog.confirmed.get()
        dialog.origin.set("0")
        dialog.confirmed.set(True)
        dialog.accept()
        assert dialog.result["rows"][1]["maya_in"] == 326
    finally:
        root.destroy()


def test_same_shot_name_in_different_sequences_is_independent(setup):
    _, events, plan = setup
    for row, sequence in zip(plan["rows"], ["s001", "s002"]):
        row.update(sequence=sequence, maya_in=278, maya_out=301)
    selected, production = compile_assignments(events, plan)
    assert [(e.sequence, e.shot) for e in production] == [("s001", "sh001"), ("s002", "sh001")]
    assert [e.sequence for e in selected] == ["s001", "s002"]
    assert [e.cut_in for e in selected] == [120, 156]
    plan["rows"][0]["sequence"] = "../bad"
    with pytest.raises(ValueError, match="invalid sequence"):
        compile_assignments(events, plan)


def test_legacy_assignments_default_to_received_sequence(setup):
    _, events, plan = setup
    for row in plan["rows"]:
        row.pop("sequence")
    selected, production = compile_assignments(events, plan)
    assert [e.sequence for e in selected] == ["op01", "op01"]
    assert len(production) == 1


def test_editorial_unit_dialog_requires_explicit_production_sequence(setup):
    import tkinter as tk
    from smartlib.dcc.resolve.cut_assignment_ui import CutAssignmentDialog
    service, events, _ = setup
    root = tk.Tk()
    root.withdraw()
    try:
        dialog = CutAssignmentDialog(root, events, service.shots, editorial_unit="op_edit")
        dialog.window.withdraw()
        assert all(row["production_sequence"] == "" for row in dialog.rows)
        plan = {"schema": "smartpipeline.cut_assignment.v1", "editorial_unit": "op_edit",
                "rows": dialog.rows, "offline_origin": 0, "alignment_confirmed": True}
        with pytest.raises(ValueError, match="invalid sequence"):
            compile_assignments(events, plan)
        for row in dialog.rows:
            row["production_sequence"] = "s001"
        dialog.confirmed.set(True)
        dialog.accept()
        assert dialog.result["editorial_unit"] == "op_edit"
        assert all("sequence" not in row for row in dialog.result["rows"])
    finally:
        root.destroy()


def test_new_unit_work_cannot_publish_without_mapping(setup, tmp_path):
    service, _, _ = setup
    work = tmp_path / "received_work"
    work.mkdir()
    (work / "manifest.json").write_text(json.dumps({"editorial_unit": "full_edit"}))
    csv = work / "events.csv"
    csv.write_text("episode,sequence,shot,cut_in,cut_out\nop,full_edit,c001,120,143\n")
    with pytest.raises(ValueError, match="Production Sequence mapping"):
        service.intake(EditorialIntakeRequest(csv_path=csv))
    assert not service.project_root.exists()


def test_multi_sequence_publish_from_one_received_movie(setup, tmp_path, monkeypatch):
    from smartlib.apps.editorial_intake.service import SmartEditorialIntakeService
    service, _, plan = setup
    ffmpeg = Path("P:/dev/smarttools/ffmpeg/ffmpeg.exe")
    if not ffmpeg.is_file():
        pytest.skip("FFmpeg is required for media integration")
    movie = tmp_path / "received.mov"
    subprocess.run([str(ffmpeg), "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=24:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
        "-c:v", "libx264", "-c:a", "pcm_s16le", str(movie)], check=True, capture_output=True)
    work = tmp_path / "received_work"
    work.mkdir()
    csv = work / "events.csv"
    csv.write_text("episode,sequence,shot,cut_in,cut_out,handle_head,handle_tail\n"
        "op,op01,c001,120,143,0,0\nop,op01,c002,156,179,0,0\n")
    plan["editorial_unit"] = "op01"
    (work / "manifest.json").write_text(json.dumps({"editorial_unit": "op01", "cut_assignment": plan}))
    for row, target in zip(plan["rows"], ["s001", "s002"]):
        row.pop("sequence", None)
        row.update(production_sequence=target, maya_in=278, maya_out=301)
    app = SmartEditorialIntakeService(service.project_config)
    monkeypatch.setattr(app.intake_service, "_ffmpeg_path", lambda: ffmpeg)
    preview = app.run("op", "op01", "v001", csv_path=csv, mov_path=movie,
        cut_assignment=plan, dry_run=True, generate_storyreel=False)
    assert any("s001, s002" in line for line in preview.report)
    assert not service.project_root.exists()
    result = app.run("op", "op01", "v001", csv_path=csv, mov_path=movie,
        cut_assignment=plan, generate_storyreel=False)
    assert len(result.sequence_results) == 2
    for part, target in zip(result.sequence_results, ["s001", "s002"]):
        intake = part.intake
        assert intake.publish_dir.parent.name == target
        assert len(intake.registered_shots) == 1
        identity = intake.registered_shots[0]
        assert (identity.sequence, identity.shot) == (target, "sh001")
        assert intake.sequence_audio.parent.parent.parent.parent.name == target
        assert int(probe_movie(app.intake_service, intake.offline_mov)[1]["nb_frames"]) == 24
        timing = json.loads(intake.editorial_timings[0].read_text())
        assert timing["sequence"] == target
        assert timing["source"]["editorial_unit"] == "op01"
        assert timing["source"]["production_sequence"] == target
        assert timing["cut_range"] == [278, 301]
        metadata = json.loads(intake.editorial_json.read_text())
        assert metadata["work_shots"][0]["sequence"] == target
        sequence_path = app.paths.sequence_workspace_root("op", target) / "sequence.json"
        assert json.loads(sequence_path.read_text())["shots"][0]["shot"] == "sh001"
    assert not app.paths.shot_root("op", "op01", "sh001").exists()
