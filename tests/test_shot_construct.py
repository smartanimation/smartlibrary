from __future__ import annotations

import json
from pathlib import Path

from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
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


def test_construct_from_cast_resolves_rig_publish(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep001", "sq010", "sh0010")

    shot_root = service.shot_root(identity)
    write_json(shot_root / "shot.json", {"episode": "ep001", "sequence": "sq010", "shot": "sh0010"})
    write_json(
        shot_root / "cast.json",
        {
            "cast": {
                "hero": {
                    "asset": "Hero",
                    "variant": "default",
                    "role": "CHA",
                    "namespace": "hero",
                    "asset_publish": "approved",
                    "required": True,
                }
            }
        },
    )

    asset_root = project_root / "library" / "assets" / "char" / "main" / "Hero"
    write_json(asset_root / "asset.json", {"category": "char", "group": "main", "asset": "Hero"})
    publish_root = asset_root / "default" / "publish" / "asset" / "work"
    scene_path = publish_root / "v001" / "hero.ma"
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    scene_path.write_text("// maya ascii", encoding="utf-8")
    write_json(publish_root / "latest.json", {"version": "v001", "path": "v001/hero.ma"})

    construct = service.construct_from_cast(identity)

    assert construct["components"][0]["component_type"] == "rig"
    assert construct["components"][0]["namespace"] == "hero"
    assert construct["components"][0]["path"] == str(scene_path)


def test_write_construct_normalizes_fx_cache(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep001", "sq010", "sh0010")

    path = service.write_construct(
        identity,
        {
            "components": [
                {
                    "component_type": "fx",
                    "name": "smoke",
                    "path": "cache/smoke.obj",
                    "mode": "reference_cache",
                }
            ]
        },
    )
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["components"][0]["component_type"] == "fx"
    assert data["components"][0]["mode"] == "reference_cache"
    assert "abc/usd/usda/usdc" in data["components"][0]["note"]


def test_ensure_stage_construct_registers_anim_input_components(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep001", "sq010", "sh0010")

    shot_root = service.shot_root(identity)
    write_json(shot_root / "shot.json", {"episode": "ep001", "sequence": "sq010", "shot": "sh0010"})
    write_json(shot_root / "cast.json", {"cast": {}})
    for path in (
        project_root / "shots" / "cast.json",
        project_root / "shots" / "placements.json",
        project_root / "sequences" / "ep001" / "sq010" / "publish" / "camera" / "sh0010" / "main" / "v002" / "camera.json",
        project_root / "shots" / "layout_overlay.usda",
    ):
        write_json(path, {"ok": True})

    anim_input_dir = shot_root / "publish" / "anim_input" / "main" / "v001"
    write_json(
        anim_input_dir / "anim_input.json",
        {
            "version": "v001",
            "cast": "shots/cast.json",
            "placements": "shots/placements.json",
            "camera": "sequences/ep001/sq010/publish/camera/sh0010/main/v002/camera.json",
            "layout_overlay": "shots/layout_overlay.usda",
        },
    )
    write_json(
        shot_root / "publish" / "anim_input" / "main" / "latest.json",
        {"version": "v001", "path": "v001/anim_input.json"},
    )

    construct_path = service.ensure_stage_construct(identity)
    data = json.loads(construct_path.read_text(encoding="utf-8"))
    component_types = {component["component_type"] for component in data["components"]}

    assert {"animation", "camera", "cast", "placement", "layout_overlay"}.issubset(component_types)
