from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from smartlib.apps.review_build_manager.worker import _internal_review_slate_lines
from smartlib.review import playblast_package


def test_internal_review_slate_uses_planned_snapshot_without_manual_fields() -> None:
    lines = _internal_review_slate_lines(
        project="ELCD",
        episode="ep02",
        sequence="s027",
        shot="c001",
        department="anim",
        review_version="v002",
        planned_snapshot={
            "inputs": [
                {
                    "enabled": True,
                    "type": "rig",
                    "name": "DLI_main",
                    "context": "ANIM",
                    "version": "v001",
                },
                {
                    "enabled": False,
                    "type": "rig",
                    "name": "unused",
                    "version": "v003",
                },
            ]
        },
        frame_range=[278, 411],
        handles=[0, 0],
        fps=24,
        created_at=datetime(2026, 8, 31, 12, 0, 0),
    )

    text = "\n".join(lines)
    assert "PROJECT ELCD" in text
    assert "ep02 / s027 / c001" in text
    assert "ANIMATION REVIEW" in text
    assert "Review v002" in text
    assert "rig                DLI_main ANIM v001" in text
    assert "unused" not in text
    assert "Frames     278-411" in text
    assert "2026-08-31" in text


def test_internal_review_slate_renders_standalone_png(
    tmp_path: Path, monkeypatch
) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"")
    slate = tmp_path / "slate.png"
    captured = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"png")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(playblast_package.subprocess, "run", fake_run)
    ok, message = playblast_package.render_internal_review_slate_png(
        slate_path=slate,
        lines=["PROJECT ELCD", "Review v002"],
        width=1280,
        height=720,
        ffmpeg=str(ffmpeg),
    )

    assert ok, message
    assert slate.read_bytes() == b"png"
    filter_graph = captured["command"][
        captured["command"].index("-vf") + 1
    ]
    assert "drawtext=" in filter_graph
    assert "fontsize=h/40" in filter_graph
    assert "line_spacing=4" in filter_graph
    assert "line_spacing=h/" not in filter_graph
    assert "tpad=" not in filter_graph
    assert "color=c=0x202326:s=1280x720:r=1" in captured["command"]


def test_internal_review_slate_font_scales_with_snapshot_rows() -> None:
    assert playblast_package._review_slate_font_denominator(15) == 40
    assert playblast_package._review_slate_font_denominator(40) == 54
