from __future__ import annotations

import json
from pathlib import Path

from smartlib.apps.smart_casting import SmartCastingService
from smartlib.apps.shot_manager import ShotIdentity
from smartlib.core.config_loader import ProjectConfig


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_config(config_dir: Path, project_root: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.joinpath("templates_base.yml").write_text(
        "\n".join(
            [
                "anchors:",
                "  project_name: MOVE",
                f"  project_root: '{project_root.as_posix()}'",
                "templates:",
                "  assets_root: '{project_root}/library/assets'",
                "  shots_root: '{project_root}/production/shots'",
                "  sequences_root: '{project_root}/production/sequences'",
            ]
        ),
        encoding="utf-8",
    )
    config_dir.joinpath("templates_assets.yml").write_text(
        "\n".join(
            [
                "templates:",
                "  asset_root: '{assets_root}/{category}/{group}/{asset_name}'",
            ]
        ),
        encoding="utf-8",
    )
    config_dir.joinpath("templates_shots.yml").write_text(
        "\n".join(
            [
                "templates:",
                "  shot_root: '{shots_root}/episodes/{episode}/sequences/{seq}/shots/{shot}'",
            ]
        ),
        encoding="utf-8",
    )


def test_smart_casting_lists_assets_and_shots_from_configured_roots(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)

    asset_root = project_root / "library" / "assets" / "props" / "bp" / "Chair"
    write_json(asset_root / "asset.json", {"category": "props", "group": "bp", "asset": "Chair"})
    write_json(asset_root / "default" / "variant.json", {"variant": "default", "status": "approved"})

    shot_root = (
        project_root
        / "production"
        / "shots"
        / "episodes"
        / "ep001"
        / "sequences"
        / "sq010"
        / "shots"
        / "sh020"
    )
    write_json(shot_root / "shot.json", {"episode": "ep001", "sequence": "sq010", "shot": "sh020"})

    service = SmartCastingService(ProjectConfig(config_dir))

    assets = service.list_assets()
    assert [(row.category, row.group, row.asset, row.variant) for row in assets] == [
        ("props", "bp", "Chair", "default")
    ]
    assert service.asset_root(assets[0]) == asset_root

    assert [(seq.episode, seq.sequence) for seq in service.sequences()] == [("ep001", "sq010")]
    assert [(shot.episode, shot.sequence, shot.shot) for shot in service.shots_for_sequence("ep001", "sq010")] == [
        ("ep001", "sq010", "sh020")
    ]


def test_smart_casting_saves_edited_namespace(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)
    service = SmartCastingService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep001", "sq010", "sh020")

    service.save_shot_cast(
        identity,
        [
            {
                "cast_key": "Chair_A",
                "asset": "Chair",
                "variant": "default",
                "role": "BGA",
                "namespace": "setChair_custom",
                "asset_publish": "approved",
                "required": True,
            }
        ],
    )

    saved = service.load_shot_cast(identity)
    assert saved["cast"]["Chair_A"]["namespace"] == "setChair_custom"


def test_smart_casting_sequence_cast_rows_include_context_statuses(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)
    asset_root = project_root / "library" / "assets" / "props" / "bp" / "Chair"
    write_json(asset_root / "asset.json", {"category": "props", "group": "bp", "asset": "Chair"})
    write_json(asset_root / "default" / "variant.json", {"variant": "default"})
    write_json(asset_root / "default" / "publish" / "asset" / "work" / "versions.json", [{"version": "v001", "status": "approved"}])
    work_scene = asset_root / "default" / "publish" / "asset" / "work" / "v001" / "Chair.ma"
    work_scene.parent.mkdir(parents=True, exist_ok=True)
    work_scene.write_text("// maya", encoding="utf-8")
    write_json(asset_root / "default" / "publish" / "asset" / "final" / "latest.json", {"version": "v002"})
    final_scene = asset_root / "default" / "publish" / "asset" / "final" / "v002" / "Chair.ma"
    final_scene.parent.mkdir(parents=True, exist_ok=True)
    final_scene.write_text("// maya", encoding="utf-8")

    service = SmartCastingService(ProjectConfig(config_dir))
    service.save_sequence_cast(
        "ep001",
        "sq010",
        [
            {
                "cast_key": "Chair_main",
                "asset": "Chair",
                "variant": "default",
                "role": "BGA",
                "namespace": "Chair_main",
                "asset_publish": "approved",
                "required": True,
                "note": "set",
            }
        ],
    )

    rows = service.sequence_cast_rows("ep001", "sq010")
    assert rows[0]["contexts"] == {"FAST": "Missing", "WORK": "Ready", "FINAL": "WIP"}
