from __future__ import annotations

import json
from pathlib import Path

from smartlib.apps.smart_sequence_builder.service import SmartSequenceBuilderService
from smartlib.apps.review_build_manager.service import ReviewBuildManagerService
from smartlib.apps.shot_manager import SequenceIdentity
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

    plan = service.plan("ep02", "s027", "Mocap + Virtual Camera")

    assert not plan.can_build
    required = next(item for item in plan.validation if item.key == "required")
    assert required.state == "ERROR"
    assert "Motion Capture" in required.detail


def test_build_manager_sequence_plan_uses_recipe_validation(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config = _config(tmp_path / "config", project_root)
    service = ReviewBuildManagerService(config)
    sequence_root = project_root / "production" / "sequences" / "ep02" / "s027"
    _json(
        sequence_root / "sequence.json",
        {
            "episode": "ep02",
            "sequence": "s027",
            "shots": [{"shot": "sh010", "cut_in": 1001, "cut_out": 1010}],
        },
    )
    _json(sequence_root / "cast.json", {"cast": {"hero": {"namespace": "hero"}}})

    plan = service.sequence_build_plan(
        SequenceIdentity("ep02", "s027"),
        recipe="Mocap + Virtual Camera",
        overrides={"use_placements": False},
    )

    assert not plan.buildable
    assert any(row.code == "SEQUENCE_REQUIRED" for row in plan.validations)


def test_standard_sequence_is_default_and_does_not_require_mocap(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config = _config(tmp_path / "config", project_root)
    service = SmartSequenceBuilderService(config)
    sequence_root = project_root / "production" / "sequences" / "ep02" / "s027"
    _json(sequence_root / "sequence.json", {
        "episode": "ep02", "sequence": "s027",
        "shots": [{"shot": "sh010", "cut_in": 1001, "cut_out": 1010}],
    })
    _json(sequence_root / "cast.json", {"cast": {}})

    plan = service.plan("ep02", "s027")

    assert plan.recipe == "Standard Sequence"
    assert plan.can_build
    assert not next(item for item in plan.inputs if item.key == "mocap").enabled


def test_project_can_override_default_sequence_recipe(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config = _config(tmp_path / "config", project_root)
    config.config_dir.joinpath("sequence_builder.yml").write_text(
        "default_recipe: Mocap Only\n", encoding="utf-8"
    )

    service = SmartSequenceBuilderService(config)

    assert service.default_recipe() == "Mocap Only"


def test_build_manager_allocates_next_sequence_construct_version(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    service = ReviewBuildManagerService(_config(tmp_path / "config", project_root))
    identity = SequenceIdentity("ep02", "s027")
    output_root = (
        service.shots.sequence_workspace_root("ep02", "s027")
        / "output"
        / "scene_build"
        / "layout"
        / "main"
    )
    (output_root / "v001").mkdir(parents=True)
    (output_root / "v003").mkdir()

    assert service.next_sequence_construct_version(identity, "layout", "main") == "v004"


def test_recipe_inputs_define_default_enabled_components(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config = _config(tmp_path / "config", project_root)
    config.config_dir.joinpath("sequence_builder.yml").write_text(
        "recipes:\n  Editorial Only:\n    version: v002\n    inputs:\n      - editorial\n      - cast\n",
        encoding="utf-8",
    )
    sequence_root = project_root / "production" / "sequences" / "ep02" / "s027"
    _json(
        sequence_root / "sequence.json",
        {"episode": "ep02", "sequence": "s027", "shots": []},
    )
    _json(sequence_root / "cast.json", {"cast": {"hero": {"namespace": "hero"}}})

    plan = SmartSequenceBuilderService(config).plan(
        "ep02", "s027", "Editorial Only"
    )

    enabled = {item.key: item.enabled for item in plan.inputs}
    assert enabled["editorial"]
    assert enabled["cast"]
    assert not enabled["mocap"]
    assert plan.can_build
