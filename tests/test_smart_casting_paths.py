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
    write_json(
        asset_root / "default" / "publish" / "asset" / "work" / "v001" / "asset.json",
        {"category": "props", "group": "bp", "asset": "Chair"},
    )
    prop_root = project_root / "library" / "assets" / "prop" / "bp" / "Cup"
    write_json(prop_root / "asset.json", {"category": "prop", "group": "bp", "asset": "Cup"})
    write_json(prop_root / "default" / "variant.json", {"variant": "default"})

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


def test_smart_casting_asset_registration_defaults_namespace_to_asset_name(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)
    asset_root = project_root / "library" / "assets" / "CH" / "main" / "Chair"
    write_json(asset_root / "asset.json", {"category": "CH", "group": "main", "asset": "Chair"})
    write_json(asset_root / "default" / "variant.json", {"variant": "default"})

    service = SmartCastingService(ProjectConfig(config_dir))
    asset = service.list_assets()[0]

    _path, rows = service.add_assets_to_sequence_cast("ep001", "sq010", [asset])
    assert rows[0]["cast_key"] == "Chair_main"
    assert rows[0]["namespace"] == "Chair"
    saved = service.load_sequence_cast("ep001", "sq010")["cast"]
    assert saved["Chair_main"]["namespace"] == "Chair"

    _path, rows = service.add_assets_to_sequence_cast("ep001", "sq010", [asset])
    assert rows[0]["cast_key"] == "Chair_02"
    assert rows[0]["namespace"] == "Chair_02"


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


def test_sequence_cast_save_adds_missing_casts_to_shots_without_overwriting(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)
    chair_root = project_root / "library" / "assets" / "CH" / "main" / "Chair"
    table_root = project_root / "library" / "assets" / "BG" / "main" / "Table"
    write_json(chair_root / "asset.json", {"category": "CH", "group": "main", "asset": "Chair"})
    write_json(chair_root / "default" / "variant.json", {"variant": "default"})
    write_json(table_root / "asset.json", {"category": "BG", "group": "main", "asset": "Table"})
    write_json(table_root / "default" / "variant.json", {"variant": "default"})
    for shot in ("sh010", "sh020"):
        write_json(
            project_root
            / "production"
            / "shots"
            / "episodes"
            / "ep001"
            / "sequences"
            / "sq010"
            / "shots"
            / shot
            / "shot.json",
            {"episode": "ep001", "sequence": "sq010", "shot": shot},
        )

    service = SmartCastingService(ProjectConfig(config_dir))
    service.save_shot_cast(
        ShotIdentity("ep001", "sq010", "sh010"),
        [
            {
                "cast_key": "Chair_main",
                "asset": "Chair",
                "variant": "default",
                "role": "CHA",
                "namespace": "Chair_shot_override",
                "asset_publish": "v003",
                "required": False,
                "note": "shot override",
            }
        ],
    )

    service.save_sequence_cast(
        "ep001",
        "sq010",
        [
            {
                "cast_key": "Chair_main",
                "asset": "Chair",
                "variant": "default",
                "role": "CHA",
                "namespace": "Chair_main",
                "asset_publish": "approved",
                "required": True,
                "note": "sequence value",
            },
            {
                "cast_key": "Table_main",
                "asset": "Table",
                "variant": "default",
                "role": "BGA",
                "namespace": "Table_main",
                "asset_publish": "approved",
                "required": True,
                "note": "sequence table",
            },
        ],
    )

    sh010 = service.load_shot_cast(ShotIdentity("ep001", "sq010", "sh010"))["cast"]
    assert sh010["Chair_main"]["namespace"] == "Chair_shot_override"
    assert sh010["Chair_main"]["asset_publish"] == "v003"
    assert sh010["Chair_main"]["required"] is False
    assert sh010["Chair_main"]["note"] == "shot override"
    assert sh010["Table_main"]["namespace"] == "Table_main"

    sh020 = service.load_shot_cast(ShotIdentity("ep001", "sq010", "sh020"))["cast"]
    assert sorted(sh020) == ["Chair_main", "Table_main"]

    service.save_sequence_cast(
        "ep001",
        "sq010",
        [
            {
                "cast_key": "Chair_main",
                "asset": "Chair",
                "variant": "default",
                "role": "CHA",
                "namespace": "Chair_main",
                "asset_publish": "approved",
                "required": True,
                "note": "sequence value",
            }
        ],
    )
    assert "Table_main" in service.load_shot_cast(ShotIdentity("ep001", "sq010", "sh020"))["cast"]
