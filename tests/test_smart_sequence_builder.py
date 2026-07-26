from __future__ import annotations

import json
from pathlib import Path

from smartlib.apps.smart_sequence_builder.service import SmartSequenceBuilderService
from smartlib.core.config_loader import ProjectConfig


def _json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _config(config_dir: Path, project_root: Path) -> ProjectConfig:
    config_dir.mkdir(parents=True)
    config_dir.joinpath("templates_base.yml").write_text(
        "\n".join(
            (
                "anchors:",
                "  project_name: TEST",
                f"  project_root: '{project_root.as_posix()}'",
                "  fps: 24",
                "templates:",
                "  shots_root: '{project_root}/production/shots'",
                "  sequences_root: '{project_root}/production/sequences'",
            )
        ),
        encoding="utf-8",
    )
    config_dir.joinpath("templates_shots.yml").write_text(
        "\n".join(
            (
                "templates:",
                "  shot_root: '{shots_root}/{episode}/{seq}/{shot}'",
            )
        ),
        encoding="utf-8",
    )
    return ProjectConfig(config_dir)


def test_plan_resolves_inputs_and_selects_latest_camera_take(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    service = SmartSequenceBuilderService(_config(tmp_path / "config", project_root))
    sequence_root = project_root / "production" / "sequences" / "ep02" / "s027"
    _json(
        sequence_root / "sequence.json",
        {
            "episode": "ep02",
            "sequence": "s027",
            "fps": 24,
            "shots": [
                {"shot": "sh010", "cut_in": 1001, "cut_out": 1100},
                {"shot": "sh020", "cut_in": 1101, "cut_out": 1200},
            ],
        },
    )
    _json(sequence_root / "cast.json", {"cast": {"hero": {"namespace": "hero"}}})
    (sequence_root / "data" / "mocap" / "DL1").mkdir(parents=True)
    (sequence_root / "data" / "virtual_camera" / "take06").mkdir(parents=True)
    (sequence_root / "data" / "virtual_camera" / "take30").mkdir(parents=True)

    plan = service.plan("ep02", "s027")

    assert plan.frame_start == 1001
    assert plan.frame_end == 1200
    assert plan.virtual_camera_take == "take30"
    assert plan.can_build
    camera = next(item for item in plan.inputs if item.key == "virtual_camera")
    assert [item.key for item in camera.children] == ["take06", "take30"]


def test_plan_blocks_missing_required_mocap(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    service = SmartSequenceBuilderService(_config(tmp_path / "config", project_root))
    sequence_root = project_root / "production" / "sequences" / "ep02" / "s027"
    _json(
        sequence_root / "sequence.json",
        {"episode": "ep02", "sequence": "s027", "shots": [{"shot": "sh010", "cut_in": 1001, "cut_out": 1010}]},
    )
    _json(sequence_root / "cast.json", {"cast": {}})

    plan = service.plan("ep02", "s027")

    assert not plan.can_build
    required = next(item for item in plan.validation if item.key == "required")
    assert required.state == "ERROR"
    assert "Motion Capture" in required.detail
