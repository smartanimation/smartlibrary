import json

from smartlib.apps.review_build_manager.worker import _collect_primary_review_overlay
from smartlib.review import overlay


class CameraCommands:
    def __init__(self):
        self.time = 7

    def listRelatives(self, camera, **kwargs):
        return [camera + "Shape"]

    def currentTime(self, value=None, **kwargs):
        if kwargs.get("query"):
            return self.time
        self.time = value

    def getAttr(self, plug, **kwargs):
        if kwargs.get("asString"):
            return "horizontal"
        if plug.endswith("focalLength"):
            return 50 + self.time
        if plug.endswith("horizontalFilmAperture"):
            return 1.417
        if plug.endswith("verticalFilmAperture"):
            return 0.945
        if plug.endswith("nearClipPlane"):
            return 0.1
        raise AssertionError(plug)

    def camera(self, shape, **kwargs):
        return 40 + self.time if kwargs.get("horizontalFieldOfView") else 25 + self.time


def test_primary_overlay_samples_camera_and_restores_time():
    cmds = CameraCommands()
    result = _collect_primary_review_overlay(
        cmds, camera="|smartPrimary:grp|smartPrimary:cam", start=10, end=11,
        width=1920, height=1080, fps=24, project="ELCD",
        shot="ep02/s027/c003", department="anim", task="preComp",
        source_file="C:/work/shot.ma", created_at="2026-09-06T10:00:00+09:00",
    )
    assert result["schema"] == overlay.SCHEMA
    assert result["camera"] == "cam"
    assert result["samples"][1]["frame"] == 11
    assert result["samples"][1]["focal_length_mm"] == 61
    assert cmds.time == 7


def test_ass_contains_static_and_per_frame_review_fields(tmp_path):
    data = {
        "schema": overlay.SCHEMA, "created_at": "2026-09-06T10:00:00+09:00",
        "project": "ELCD", "shot": "ep02/s027/c003", "department": "anim",
        "task": "preComp", "source_file": "shot.ma", "camera": "cam",
        "frame_range": [1001, 1002], "fps": 24, "resolution": [1920, 1080],
        "samples": [
            {"frame": 1001, "camera": "cam", "focal_length_mm": 50,
             "horizontal_fov_deg": 39.6, "vertical_fov_deg": 22.9},
            {"frame": 1002, "camera": "cam", "focal_length_mm": 51,
             "horizontal_fov_deg": 38.9, "vertical_fov_deg": 22.4},
        ],
    }
    path = overlay.write_review_overlay_ass(data, tmp_path / "review_overlay.ass")
    text = path.read_text(encoding="utf-8-sig")
    assert "ELCD  ep02/s027/c003" in text
    assert "anim / preComp" in text
    assert "anim / preComp\\N2026-09-06T10:00:00+09:00" in text
    assert ",,shot.ma" in text
    assert "FRAME 1001" in text and "FRAME 1002" in text
    assert "cam  51.00 mm  FOV 38.90 x 22.40 deg" in text
    # BorderStyle 3 uses OutlineColour for the text box itself.
    assert text.count("&H60383838") == 10


def test_movie_overlay_uses_ass_and_preserves_optional_audio(tmp_path, monkeypatch):
    clean = tmp_path / "clean.mov"
    clean.write_bytes(b"clean")
    data = {"schema": overlay.SCHEMA, "resolution": [1280, 720], "fps": 24,
            "frame_range": [1, 1], "samples": [{"frame": 1}]}
    calls = []

    class Result:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(overlay.subprocess, "run",
                        lambda command, **kwargs: calls.append(command) or Result())
    ok, _ = overlay.overlay_review_movie(
        clean_movie=clean, overlay=data, overlay_json=tmp_path / "overlay.json",
        mov_path=tmp_path / "review.mov", ffmpeg="ffmpeg",
    )
    assert ok
    command = calls[0]
    assert "subtitles=" in command[command.index("-vf") + 1]
    assert command[command.index("0:a?") - 1:command.index("0:a?") + 1] == ["-map", "0:a?"]
