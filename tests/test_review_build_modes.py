from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from smartlib.apps.review_build_manager.orchestrator import BUILD_MODES
from smartlib.apps.review_build_manager.service import ReviewBuildManagerService


def test_build_modes_match_stage_design() -> None:
    assert BUILD_MODES == ("WORK STAGE", "REND STAGE", "UPDATE")


def test_construct_diff_reports_updates_additions_and_omit(tmp_path: Path) -> None:
    asset_root = tmp_path / "Hero"
    asset_root.mkdir()
    asset_root.joinpath("asset.json").write_text(
        '{"status": "omit"}', encoding="utf-8"
    )
    manager = object.__new__(ReviewBuildManagerService)
    manager.shots = SimpleNamespace(
        find_asset_root=lambda asset: asset_root if asset == "Hero" else None,
    )
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")
    current = {
        "components": [
            {
                "component_type": "rig",
                "name": "Hero_main",
                "version": "v001",
                "path": "Hero_v001.ma",
                "enabled": True,
                "mode": "reference",
                "source": {"kind": "cast_entry", "asset": "Hero"},
            }
        ]
    }
    desired = {
        "components": [
            {
                "component_type": "rig",
                "name": "Hero_main",
                "version": "v002",
                "path": "Hero_v002.ma",
                "enabled": True,
                "mode": "reference",
                "source": {"kind": "cast_entry", "asset": "Hero"},
            },
            {
                "component_type": "camera",
                "name": "cam_main",
                "version": "v001",
                "path": "camera.ma",
                "enabled": True,
                "mode": "import",
                "source": {"kind": "published_camera"},
            },
        ]
    }

    changes = manager.construct_diff(identity, current=current, desired=desired)
    by_name = {(row["after"] or row["before"])["name"]: row for row in changes}

    assert by_name["Hero_main"]["change"] == "UPDATED"
    assert by_name["Hero_main"]["asset_status"] == "omit"
    assert by_name["Hero_main"]["severity"] == "ERROR"
    assert by_name["Hero_main"]["selected"] is False
    assert by_name["cam_main"]["change"] == "ADDED"
    assert by_name["cam_main"]["selected"] is True
