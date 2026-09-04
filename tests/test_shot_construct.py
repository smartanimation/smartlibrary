from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from smartlib.apps.review_build_manager.service import (
    ReviewBuildManagerService,
    ReviewOutput,
    ReviewShotStatus,
)
from smartlib.apps.shot_manager import BuildPreviewItem, ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.dcc.maya import placement as placement_tools


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
                "  workspace_root: '{project_root}/workspace'",
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
                "  shot_build_root: '{workspace_root}/{episode}/{sequence}/{shot}/build'",
                "  shot_build: '{shot_build_root}/{department}/{dcc}/{task}/{version}'",
                "  sequence_build_root: '{workspace_root}/{episode}/{sequence}/build'",
                "  sequence_build: '{sequence_build_root}/{department}/{dcc}/{task}/{version}'",
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
                    "category": "character",
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
    assert construct["components"][0]["version"] == "v001"
    assert construct["components"][0]["source"]["asset_publish"] == "approved"


def test_editorial_timing_is_versioned_and_overlays_shot_json(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep001", "sq010", "sh0010")
    write_json(
        service.shot_root(identity) / "shot.json",
        {
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "editorial": {"fps": 24, "cut_in": 278, "cut_out": 411},
        },
    )

    v001 = service.publish_editorial_timing(
        identity,
        {"fps": 24, "cut_in": 278, "cut_out": 411, "handles": {"head": 8, "tail": 8}},
        source={"kind": "editorial_import", "edit": "edit_v010"},
    )
    v002 = service.publish_editorial_timing(
        identity,
        {"fps": 24, "cut_in": 278, "cut_out": 419, "handles": {"head": 8, "tail": 8}},
        source={"kind": "editorial_import", "edit": "edit_v011"},
    )

    shot = service.load_shot(identity)
    assert v001.parent.name == "v001"
    assert v002.parent.name == "v002"
    assert shot["editorial_timing"]["version"] == "v002"
    assert shot["editorial"]["cut_out"] == 419
    assert shot["editorial"]["work_range"] == [1001, 1158]
    assert service.shot_frame_range(identity) == (278, 419)


def test_editorial_timing_is_a_construct_input_and_diff_code(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep001", "sq010", "sh0010")
    write_json(service.shot_root(identity) / "cast.json", {"cast": {}})
    timing_v1 = service.publish_editorial_timing(
        identity, {"fps": 24, "cut_in": 100, "cut_out": 120}
    )
    current = service.construct_from_stage_inputs(identity)
    assert current["components"][0]["component_type"] == "editorial_timing"
    assert current["components"][0]["path"] == str(timing_v1)

    service.publish_editorial_timing(
        identity, {"fps": 24, "cut_in": 100, "cut_out": 124}
    )
    manager = ReviewBuildManagerService(ProjectConfig(config_dir))
    diff = manager.construct_diff(identity, current=current)
    timing_change = next(row for row in diff if row["key"][0] == "editorial_timing")
    assert timing_change["change"] == "UPDATED"
    assert timing_change["code"] == "TIMING_CHANGED"
    assert timing_change["selected"] is True


def test_shot_audio_is_a_construct_input(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    write_config(config_dir, project_root)
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep001", "sq010", "sh0010")
    audio = service.shot_data_root(identity) / "audio" / "v002" / "sh0010.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"wav")
    write_json(
        service.shot_data_root(identity) / "audio" / "latest.json",
        {
            "version": "v002",
            "path": audio.relative_to(project_root).as_posix(),
        },
    )

    construct = service.construct_from_stage_inputs(identity)
    component = next(row for row in construct["components"] if row["component_type"] == "audio")

    assert component["name"] == "main"
    assert component["version"] == "v002"
    assert component["mode"] == "apply"
    assert component["enabled"] is True
    assert component["path"] == str(audio)


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


def test_ensure_stage_construct_keeps_only_selectable_anim_input_components(tmp_path: Path) -> None:
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

    assert component_types == {"placement", "layout_overlay"}


def test_resolved_construct_overlays_saved_choices_and_context() -> None:
    service = object.__new__(ShotManagerService)
    captured = {}
    service.load_construct = lambda _identity: {
        "components": [
            {
                "component_type": "rig",
                "name": "hero",
                "enabled": False,
                "mode": "reference",
                "source": {"context": "FAST", "context_override": True},
            },
            {
                "component_type": "fx",
                "name": "smoke",
                "enabled": True,
                "path": "smoke.abc",
                "source": {"kind": "custom"},
            },
        ]
    }

    def generated(
        _identity, *, cast_contexts=None, exclude_cast=None, representation="project"
    ):
        captured["contexts"] = dict(cast_contexts or {})
        captured["representation"] = representation
        return {
            "components": [
                {
                    "component_type": "rig",
                    "name": "hero",
                    "enabled": True,
                    "mode": "reference",
                    "path": "hero.ma",
                    "source": {"asset": "Hero"},
                }
            ]
        }

    service.construct_from_stage_inputs = generated
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")

    result = service.resolved_construct(identity)

    assert captured["contexts"] == {"hero": "FAST"}
    assert result["components"][0]["enabled"] is False
    assert result["components"][0]["source"]["context"] == "FAST"
    assert result["components"][1]["name"] == "smoke"


def test_resolved_construct_prunes_cast_removed_from_current_definition() -> None:
    service = object.__new__(ShotManagerService)
    service.load_construct = lambda _identity: {
        "components": [
            {
                "component_type": "rig",
                "name": "removed_character",
                "source": {"kind": "cast_entry", "asset": "Removed"},
            },
            {
                "component_type": "fx",
                "name": "custom_smoke",
                "source": {"kind": "custom"},
            },
        ]
    }
    service.construct_from_stage_inputs = lambda *_args, **_kwargs: {
        "components": []
    }
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")

    result = service.resolved_construct(identity)

    assert [component["name"] for component in result["components"]] == [
        "custom_smoke"
    ]


def test_saved_context_without_explicit_override_follows_stage_profile() -> None:
    service = object.__new__(ShotManagerService)
    captured = {}
    service.load_construct = lambda _identity: {
        "components": [{
            "component_type": "rig",
            "name": "hero",
            "source": {"kind": "cast_entry", "context": "ANIM"},
        }]
    }

    def generated(_identity, *, cast_contexts=None, **_kwargs):
        captured["contexts"] = dict(cast_contexts or {})
        return {"components": [{
            "component_type": "rig",
            "name": "hero",
            "source": {"kind": "cast_entry", "context": "LO"},
        }]}

    service.construct_from_stage_inputs = generated
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")

    result = service.resolved_construct(identity)

    assert captured["contexts"] == {}
    assert result["components"][0]["source"]["context"] == "LO"


def test_missing_background_asset_usd_is_visible_but_not_required(tmp_path: Path) -> None:
    service = object.__new__(ShotManagerService)
    variant_root = tmp_path / "assets" / "env" / "set" / "Room" / "default"
    variant_root.mkdir(parents=True)
    background = BuildPreviewItem(
        cast_key="Room_main",
        asset="Room",
        variant="default",
        namespace="Room_main",
        category="environment",
        review_layer="",
        asset_publish="approved",
        required=True,
        status="resolved",
        variant_root=str(variant_root),
        publish_path=str(variant_root / "publish" / "asset" / "work" / "v001" / "Room.ma"),
    )
    service.latest_anim_input = lambda _identity: None
    service.list_shot_data_versions = lambda _identity: []
    service.load_dependencies = lambda _identity: {"dependencies": []}
    service._latest_review_camera_paths = lambda _identity: []
    service.list_set_dress_publish_versions = lambda _identity: []
    service.list_preview_render_versions = lambda _identity: []
    service.latest_animation_package_path = lambda _identity: None
    service.load_cast = lambda _identity: {"cast": {}}
    service.load_sequence_cast = lambda *_args: {"cast": {}}
    service.build_preview = lambda *_args, **_kwargs: [background]
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")

    component = service.construct_from_stage_inputs(
        identity, representation="usd"
    )["components"][0]

    assert component["component_type"] == "usd"
    assert component["path"] == ""
    assert component["required"] is False
    assert component["enabled"] is True
    assert component["note"] == "MISSING: Compose/Pack Asset USD required"

    maya_component = service.construct_from_stage_inputs(identity)["components"][0]
    assert maya_component["component_type"] == "rig"
    assert maya_component["mode"] == "reference"
    assert maya_component["path"] == background.publish_path
    assert maya_component["namespace"] == "Room_main"


def test_background_resolves_formal_asset_usda_after_pack(tmp_path: Path) -> None:
    service = object.__new__(ShotManagerService)
    variant_root = tmp_path / "assets" / "env" / "set" / "Room" / "default"
    # A component model USD is not a valid shot entry by itself.
    model_usd = variant_root / "publish" / "model" / "render" / "v003" / "model.usd"
    model_usd.parent.mkdir(parents=True)
    model_usd.write_text("#usda 1.0", encoding="utf-8")
    item = BuildPreviewItem(
        cast_key="Room_main",
        asset="Room",
        variant="default",
        namespace="Room_main",
        category="environment",
        review_layer="",
        asset_publish="approved",
        required=True,
        status="resolved",
        variant_root=str(variant_root),
    )
    assert service._asset_usd_for_preview(item, profile="WORK") is None

    asset_usd = variant_root / "publish" / "asset" / "final" / "v004" / "asset.usda"
    asset_usd.parent.mkdir(parents=True)
    asset_usd.write_text("#usda 1.0", encoding="utf-8")
    write_json(
        variant_root / "publish" / "asset" / "final" / "latest.json",
        {"version": "v004", "path": "v004/asset.usda"},
    )

    assert service._asset_usd_for_preview(item, profile="WORK") == asset_usd


def test_resolved_construct_keeps_missing_background_optional() -> None:
    service = object.__new__(ShotManagerService)
    service.load_construct = lambda _identity: {
        "components": [
            {
                "component_type": "usd",
                "name": "Room_main",
                "required": True,
                "note": "old blocking background",
                "source": {"kind": "cast_entry", "category": "environment", "context": "WORK"},
            }
        ]
    }
    service.construct_from_stage_inputs = lambda *_args, **_kwargs: {
        "components": [
            {
                "component_type": "usd",
                "name": "Room_main",
                "path": "",
                "required": False,
                "enabled": True,
                "note": "MISSING: Compose/Pack Asset USD required",
                "source": {"kind": "cast_entry", "category": "environment", "context": "WORK"},
            }
        ]
    }
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")

    component = service.resolved_construct(identity)["components"][0]

    assert component["required"] is False
    assert component["note"] == "MISSING: Compose/Pack Asset USD required"


def test_build_manager_contents_use_construct_components() -> None:
    manager = object.__new__(ReviewBuildManagerService)
    manager.shots = SimpleNamespace(
        resolved_construct=lambda *_args, **_kwargs: {
            "components": [
                {
                    "component_type": "camera",
                    "name": "shot_camera",
                    "version": "v003",
                    "path": "",
                    "enabled": True,
                    "required": True,
                    "source": {"field": "camera"},
                },
                {
                    "component_type": "camera",
                    "name": "take30",
                    "version": "v001",
                    "path": "",
                    "enabled": True,
                    "required": False,
                    "source": {
                        "kind": "shot_dependency",
                        "dependency_type": "virtual_camera",
                        "dependency_id": "vcam_take30",
                        "representation": "fbx",
                    },
                },
                {
                    "component_type": "rig",
                    "name": "hero",
                    "version": "v012",
                    "path": "",
                    "enabled": False,
                    "required": True,
                    "source": {"asset": "Hero", "category": "character"},
                },
            ]
        },
        find_asset_root=lambda _asset: None,
    )
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")

    rows = manager.build_contents(identity)

    assert [row["type"] for row in rows] == ["camera", "virtual_camera", "rig"]
    assert rows[0]["component"]["source"]["field"] == "camera"
    assert rows[0]["context"] == ""
    assert rows[1]["note"] == "from dependencies.json: vcam_take30"
    assert rows[2]["context"] == "WORK"
    assert rows[2]["state"] == "EXCLUDED"


def test_build_manager_discards_context_removed_from_asset_profiles() -> None:
    manager = object.__new__(ReviewBuildManagerService)
    manager.shots = SimpleNamespace(
        load_cast=lambda _identity: {
            "cast": {
                "hero": {"asset": "Hero", "variant": "default"},
            }
        },
        find_asset_root=lambda _asset: Path("assets/character/Hero"),
    )
    manager.asset_context_profiles_for_root = lambda *_args: ["LO", "ANIM", "REND"]
    manager.default_asset_context = lambda *_args: "ANIM"
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")

    contexts = manager.normalize_cast_contexts(
        identity,
        {"hero": "CHAR_ANIM"},
        default_context="WORK",
    )

    assert contexts == {"hero": "ANIM"}


def test_build_manager_contents_show_existing_shot_data_version_as_latest(
    tmp_path: Path,
) -> None:
    data_file = tmp_path / "v009" / "animation_curve.json"
    data_file.parent.mkdir()
    data_file.write_text("{}", encoding="utf-8")
    manager = object.__new__(ReviewBuildManagerService)
    manager.shots = SimpleNamespace(
        resolved_construct=lambda *_args, **_kwargs: {
            "components": [
                {
                    "component_type": "animation_curve",
                    "name": "DLI_main",
                    "version": "v009",
                    "path": str(data_file),
                    "enabled": True,
                    "required": True,
                    "source": {"kind": "animation_curve_data"},
                }
            ]
        },
        load_construct=lambda _identity: {"components": []},
        load_cast=lambda _identity: {"cast": {}},
        find_asset_root=lambda _asset: None,
    )
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")

    row = manager.build_contents(identity)[0]

    assert row["latest"] == "v009"
    assert row["official"] == "v009"
    assert row["state"] == "READY"


def test_work_stage_status_uses_construct_curves_without_animation_package(
    tmp_path: Path,
) -> None:
    manager = object.__new__(ReviewBuildManagerService)
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")
    build_dir = tmp_path / "v003"
    build_dir.mkdir()
    write_json(
        build_dir / "build_manifest.json",
        {
            "construct": {"components": []},
            "review_requested": False,
        },
    )
    curve = tmp_path / "curves" / "v002" / "animation_curve.json"
    write_json(curve, {})
    rig = tmp_path / "hero.ma"
    rig.write_text("// Maya ASCII", encoding="utf-8")
    desired = {
        "components": [
            {
                "component_type": "rig",
                "name": "hero",
                "path": str(rig),
                "required": True,
                "enabled": True,
                "source": {"category": "character"},
            },
            {
                "component_type": "animation_curve",
                "name": "hero",
                "version": "v002",
                "path": str(curve),
                "required": True,
                "enabled": True,
            },
        ]
    }
    manager.shots = SimpleNamespace(
        resolved_construct=lambda *_args, **_kwargs: desired,
        load_cast=lambda _identity: {
            "cast": {"hero": {"required": True, "category": "character"}}
        },
        load_sequence_cast=lambda *_args: {"cast": {}},
        load_shot=lambda _identity: {"status": "wip"},
        shot_root=lambda _identity: tmp_path,
    )
    manager.list_constructs = lambda *_args: [
        {
            "version": "v003",
            "directory": str(build_dir),
            "scene": str(build_dir / "shot.ma"),
            "updated": "2026-08-12 10:00",
        }
    ]
    manager.construct_diff = lambda *_args, **_kwargs: []

    status = manager.shot_status(identity, mode="WORK STAGE")

    assert status.state == "UP TO DATE"
    assert status.source_version == "v002"
    assert "Animation Package" not in status.message


def test_work_stage_status_reports_missing_required_animation_curve(
    tmp_path: Path,
) -> None:
    manager = object.__new__(ReviewBuildManagerService)
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")
    rig = tmp_path / "hero.ma"
    rig.write_text("// Maya ASCII", encoding="utf-8")
    manager.shots = SimpleNamespace(
        resolved_construct=lambda *_args, **_kwargs: {
            "components": [{
                "component_type": "rig",
                "name": "hero",
                "path": str(rig),
                "required": True,
                "enabled": True,
                "source": {"category": "character"},
            }]
        },
        load_cast=lambda _identity: {
            "cast": {"hero": {"required": True, "category": "character"}}
        },
        load_sequence_cast=lambda *_args: {"cast": {}},
        load_shot=lambda _identity: {},
        shot_root=lambda _identity: tmp_path,
    )
    manager.list_constructs = lambda *_args: []
    manager.construct_diff = lambda *_args, **_kwargs: []

    status = manager.shot_status(identity, mode="WORK STAGE")

    assert status.state == "MISSING"
    assert "Animation Curves: hero" in status.message


def test_work_stage_static_placement_does_not_require_animation_curve(
    tmp_path: Path,
) -> None:
    manager = object.__new__(ReviewBuildManagerService)
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")
    rig = tmp_path / "chair.ma"
    rig.write_text("// Maya ASCII", encoding="utf-8")
    placement_dir = tmp_path / "placements" / "v001"
    placements_path = placement_dir / "placements.json"
    write_json(placements_path, {"placements": []})
    write_json(
        placement_dir / "placement_members.json",
        {"placements": [{"locator": "chair_place_loc", "member": "chair", "motion": "STATIC"}]},
    )
    manager.shots = SimpleNamespace(
        resolved_construct=lambda *_args, **_kwargs: {
            "components": [
                {"component_type": "rig", "name": "chair", "path": str(rig), "required": True, "enabled": True},
                {"component_type": "placement", "name": "placements", "path": str(placements_path), "required": True, "enabled": True},
            ]
        },
        load_cast=lambda _identity: {"cast": {"chair": {"required": True, "category": "prop"}}},
        load_sequence_cast=lambda *_args: {"cast": {}},
        load_shot=lambda _identity: {},
        shot_root=lambda _identity: tmp_path,
    )
    manager.list_constructs = lambda *_args: []
    manager.construct_diff = lambda *_args, **_kwargs: []

    status = manager.shot_status(identity, mode="WORK STAGE")

    assert status.state == "READY"
    assert "Animation Curves: chair" not in status.message


def test_work_stage_curve_placement_requires_animation_curve(tmp_path: Path) -> None:
    manager = object.__new__(ReviewBuildManagerService)
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")
    rig = tmp_path / "chair.ma"
    rig.write_text("// Maya ASCII", encoding="utf-8")
    placement_dir = tmp_path / "placements" / "v001"
    placements_path = placement_dir / "placements.json"
    write_json(placements_path, {"placements": []})
    write_json(
        placement_dir / "placement_members.json",
        {"placements": [{"locator": "chair_place_loc", "member": "chair", "motion": "CURVE"}]},
    )
    manager.shots = SimpleNamespace(
        resolved_construct=lambda *_args, **_kwargs: {
            "components": [
                {"component_type": "rig", "name": "chair", "path": str(rig), "required": True, "enabled": True},
                {"component_type": "placement", "name": "placements", "path": str(placements_path), "required": True, "enabled": True},
            ]
        },
        load_cast=lambda _identity: {"cast": {"chair": {"required": True, "category": "prop"}}},
        load_sequence_cast=lambda *_args: {"cast": {}},
        load_shot=lambda _identity: {},
        shot_root=lambda _identity: tmp_path,
    )
    manager.list_constructs = lambda *_args: []
    manager.construct_diff = lambda *_args, **_kwargs: []

    status = manager.shot_status(identity, mode="WORK STAGE")

    assert status.state == "MISSING"
    assert "Animation Curves: chair" in status.message


def test_smart_maker_export_writes_shot_data_placement_type(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(placement_tools, "_context_root", lambda _config: tmp_path)
    monkeypatch.setattr(
        placement_tools,
        "_collect_placement_metadata",
        lambda: (
            {"placements": [{"locator": "hero_place_loc"}]},
            {"placements": [{"locator": "hero_place_loc", "member": "hero"}]},
        ),
    )

    placements_path, members_path = placement_tools.export_metadata(SimpleNamespace())

    assert placements_path == tmp_path / "data/placement/hero_place_loc/main/v001/placements.json"
    assert members_path == tmp_path / "data/placement/hero_place_loc/main/v001/placement_members.json"
    assert json.loads((placements_path.parent / "data.json").read_text(encoding="utf-8"))["data_type"] == "placement"
    assert json.loads((placements_path.parents[1] / "latest.json").read_text(encoding="utf-8")) == {
        "version": "v001",
        "path": "v001/placements.json",
    }

def test_work_stage_input_resolves_shot_data_camera_and_placements(tmp_path: Path) -> None:
    service = object.__new__(ShotManagerService)
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")
    camera_dir = tmp_path / "data" / "camera" / "cam" / "main" / "v003"
    camera = camera_dir / "camera.json"
    write_json(camera, {"data_type": "camera"})
    placements = tmp_path / "publish" / "layout" / "placements" / "v002" / "placements.json"
    write_json(placements, {"placements": []})
    service.list_shot_data_versions = lambda _identity: [
        SimpleNamespace(name="camera/cam/main", version="v003", path=str(camera_dir), latest=True)
    ]
    service.list_placement_publish_versions = lambda _identity: [
        SimpleNamespace(version="v002", path=str(placements), latest=True)
    ]
    service._latest_shot_camera_publish = lambda _identity: None

    assert service._latest_work_stage_camera(identity) == camera
    assert service._latest_work_stage_placements(identity) == placements


def test_work_stage_input_prefers_exported_placement_data(tmp_path: Path) -> None:
    service = object.__new__(ShotManagerService)
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")
    placement_dir = tmp_path / "data" / "placement" / "chair_place_loc" / "main" / "v004"
    placements = placement_dir / "placements.json"
    write_json(placements, {"placements": []})
    published = tmp_path / "publish" / "layout" / "placements" / "v002" / "placements.json"
    write_json(published, {"placements": []})
    service.list_shot_data_versions = lambda _identity: [
        SimpleNamespace(name="placement/chair_place_loc/main", version="v004", path=str(placement_dir), latest=True)
    ]
    service.list_placement_publish_versions = lambda _identity: [
        SimpleNamespace(version="v002", path=str(published), latest=True)
    ]
    service.shot_data_root = lambda _identity: tmp_path / "data"

    assert service._latest_work_stage_placements(identity) == placements


def test_construct_from_stage_inputs_includes_placement_data(tmp_path: Path) -> None:
    service = object.__new__(ShotManagerService)
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")
    placement_dir = tmp_path / "data" / "placement" / "chair_place_loc" / "main" / "v004"
    placements = placement_dir / "placements.json"
    write_json(placements, {"placements": []})
    service.paths = SimpleNamespace(project_root=tmp_path)
    service.latest_editorial_timing_path = lambda _identity: None
    service.shot_data_root = lambda _identity: tmp_path / "data"
    service.latest_anim_input = lambda _identity: None
    service.list_shot_data_versions = lambda _identity: [
        SimpleNamespace(name="placement/chair_place_loc/main", version="v004", path=str(placement_dir), latest=True)
    ]
    service.selected_virtual_camera_dependencies = lambda _identity: []
    service._latest_review_camera_paths = lambda _identity: []
    service.list_set_dress_data = lambda _identity: []
    service.list_set_dress_publish_versions = lambda _identity: []
    service.load_cast = lambda _identity: {"cast": {}}
    service.load_sequence_cast = lambda *_args: {"cast": {}}
    service.latest_animation_curve_path = lambda *_args, **_kwargs: None
    service.build_preview = lambda *_args, **_kwargs: []

    construct = service.construct_from_stage_inputs(identity)
    placement_component = next(
        row for row in construct["components"] if row["component_type"] == "placement"
    )

    assert placement_component["name"] == "chair_place_loc"
    assert placement_component["version"] == "v004"
    assert placement_component["path"] == str(placements)
    assert placement_component["source"]["data_type"] == "placement"

def test_construct_output_uses_workspace_and_reads_legacy_history(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    project_root = tmp_path / "project"
    write_config(config_dir, project_root)
    manager = ReviewBuildManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep01", "sq01", "sh010")
    new_root = manager.shots.shot_build_dir(identity, "anim", "maya", "main", "v003")
    legacy_root = manager.shots.legacy_shot_build_root(identity) / "anim" / "main" / "v002"
    for root in (new_root, legacy_root):
        root.mkdir(parents=True)
        scene = root / f"sh010_anim_main_{root.name}.ma"
        scene.write_text("// Maya ASCII", encoding="utf-8")
        write_json(root / "build_manifest.json", {"scene": str(scene), "components": []})
        write_json(root / "validation.json", {"state": "COMPLETE"})

    assert manager.shots.shot_build_root(identity) == (
        project_root / "workspace" / "ep01" / "sq01" / "sh010" / "build"
    )
    assert manager.shots.shot_build_dir(identity, "layout", "houdini", "main", "v001") == (
        project_root / "workspace" / "ep01" / "sq01" / "sh010"
        / "build" / "layout" / "houdini" / "main" / "v001"
    )
    assert manager.next_construct_version(identity, "anim", "main") == "v004"
    assert [row["version"] for row in manager.list_constructs(identity, "anim", "main")] == [
        "v003", "v002"
    ]


def test_generate_review_toggle_uses_cached_construct_and_output(tmp_path: Path) -> None:
    movie = tmp_path / "review.mov"
    base = ReviewShotStatus(
        identity=SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010"),
        state="UP TO DATE",
        output_version="v003",
        output_label="v003",
        last_review="-",
        thumbnail="",
        comment="",
        source_version="v001",
        message="Construct and Animation Curves are current.",
        outputs=(),
    )

    required = ReviewBuildManagerService.apply_generate_review_requirement(base, True)
    assert required.state == "READY"
    assert "MOV is not available" in required.message

    optional = ReviewBuildManagerService.apply_generate_review_requirement(required, False)
    assert optional.state == "UP TO DATE"

    movie.write_bytes(b"mov")
    with_movie = replace(
        base,
        outputs=(
            ReviewOutput(
                version="v003",
                directory=str(tmp_path),
                movie=str(movie),
                state="COMPLETE",
            ),
        ),
    )
    unchanged = ReviewBuildManagerService.apply_generate_review_requirement(
        with_movie, True
    )
    assert unchanged.state == "UP TO DATE"


def test_construct_discovers_publishes_without_anim_input(tmp_path: Path) -> None:
    service = object.__new__(ShotManagerService)
    camera = tmp_path / "publish" / "camera" / "cam_main" / "main" / "v003" / "camera.json"
    set_dress = tmp_path / "publish" / "setdress" / "main" / "v002" / "main.setdress.json"
    set_dress_work = tmp_path / "data" / "setdress" / "main.setdress.json"
    preview = tmp_path / "publish" / "preview_render" / "anim" / "packages" / "v004" / "render_manifest.json"
    animation = tmp_path / "publish" / "animation" / "package" / "main" / "v005" / "animation_manifest.json"
    for path in (camera, set_dress, set_dress_work, preview, animation):
        write_json(path, {"ok": True})
    service.latest_anim_input = lambda _identity: None
    service.list_shot_data_versions = lambda _identity: []
    service.load_dependencies = lambda _identity: {"dependencies": []}
    service._latest_review_camera_paths = lambda _identity: [str(camera)]
    service.list_set_dress_publish_versions = lambda _identity: [
        SimpleNamespace(name="set_dress/main", version="v002", path=str(set_dress), latest=True)
    ]
    service.list_set_dress_data = lambda _identity: [
        SimpleNamespace(
            name="set_dress_data/main",
            version="WORK",
            path=str(set_dress_work),
            latest=True,
        )
    ]
    service.list_preview_render_versions = lambda _identity: [
        SimpleNamespace(name="preview_render/anim", version="v004", path=str(preview), latest=True)
    ]
    service.latest_animation_package_path = lambda _identity: animation
    service.load_cast = lambda _identity: {"cast": {}}
    service.load_sequence_cast = lambda *_args: {"cast": {}}
    service.build_preview = lambda *_args, **_kwargs: []
    identity = SimpleNamespace(episode="ep01", sequence="sq01", shot="sh010")

    result = service.construct_from_stage_inputs(identity)

    assert {row["component_type"] for row in result["components"]} == {
        "camera", "set_dress"
    }
    set_dress_component = next(
        row for row in result["components"] if row["component_type"] == "set_dress"
    )
    assert set_dress_component["version"] == "WORK"
    assert set_dress_component["path"] == str(set_dress_work)
    assert set_dress_component["source"]["kind"] == "set_dress_work_data"


def test_latest_shot_camera_falls_back_to_direct_publish(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    project_root = tmp_path / "project"
    write_config(config_dir, project_root)
    service = ShotManagerService(ProjectConfig(config_dir))


def test_normalized_maya_timeline_starts_at_configured_frame(tmp_path: Path) -> None:
    config_dir = tmp_path / "normalized_config"
    project_root = tmp_path / "project"
    write_config(config_dir, project_root)
    service = ShotManagerService(ProjectConfig(config_dir))

    assert service._anim_work_range(278, 411, [8, 8]) == [1001, 1150]


def test_editorial_maya_timeline_preserves_cut_frames(tmp_path: Path) -> None:
    config_dir = tmp_path / "editorial_config"
    project_root = tmp_path / "project"
    write_config(config_dir, project_root)
    templates = config_dir / "templates_base.yml"
    templates.write_text(
        templates.read_text(encoding="utf-8")
        + "\nmaya_timeline:\n  mode: editorial\n  normalized_start: 1001\n",
        encoding="utf-8",
    )
    service = ShotManagerService(ProjectConfig(config_dir))
    work_range = service._anim_work_range(278, 411, [8, 8])

    assert work_range == [270, 419]
    assert service._anim_cut_range_in_work(
        work_range, 278, 411, [8, 8]
    ) == [278, 411]
    identity = ShotIdentity("ep001", "sq010", "sh0010")
    camera_root = (
        service.shot_root(identity)
        / "publish"
        / "camera"
        / "cam_CHA_baked"
        / "main"
    )
    camera_path = camera_root / "v003" / "camera.json"
    write_json(camera_path, {})
    write_json(
        camera_root / "latest.json",
        {"version": "v003", "path": "v003/camera.json"},
    )

    assert service._latest_shot_camera_publish(identity) == camera_path
    camera_status = next(
        item for item in service.shot_anim_input_status(identity)
        if item.name == "camera"
    )
    assert camera_status.state == "READY"


def test_camera_native_publish_files_are_registered(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    project_root = tmp_path / "project"
    write_config(config_dir, project_root)
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep001", "sq010", "sh0010")
    snapshot = service.publish_shot_scene_snapshot(
        identity,
        {"schema": "maya_camera/v1", "camera": "cam_main"},
        data_type="camera",
        target="cam_main",
    )

    service.register_shot_scene_publish_files(
        snapshot,
        {"ma": "cam_main.ma", "fbx": "cam_main.fbx"},
    )

    camera_data = json.loads(snapshot.read_text(encoding="utf-8"))
    publish_data = json.loads(
        snapshot.with_name("publish.json").read_text(encoding="utf-8")
    )
    assert camera_data["files"] == {
        "ma": "cam_main.ma",
        "fbx": "cam_main.fbx",
    }
    assert publish_data["files"]["camera"] == "camera.json"
    assert publish_data["files"]["ma"] == "cam_main.ma"


def test_camera_package_uses_existing_publish_versions(tmp_path: Path) -> None:
    from smartlib.dcc.maya.camera_publish import SCHEMA
    config_dir = tmp_path / "config"
    write_config(config_dir, tmp_path / "project")
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep001", "sq010", "sh0010")
    payload = {"schema": SCHEMA, "camera": "primaryCam", "rows": [{"camera_key": "layer:CHA"}],
               "cameras": [{"key": "primary", "role": "primary"}]}
    first = service.publish_shot_scene_snapshot(identity, payload, data_type="camera")
    second = service.publish_shot_scene_snapshot(identity, payload, data_type="camera")
    assert json.loads(first.read_text(encoding="utf-8"))["version"] == "v001"
    restored = json.loads(second.read_text(encoding="utf-8"))
    assert restored["schema"] == SCHEMA
    assert restored["cameras"] == payload["cameras"]
    assert restored["rows"] == payload["rows"]
    assert service._latest_shot_camera_publish(identity) == second
    assert {row.version for row in service.list_shot_scene_publish_versions(identity, "camera")} == {"v001", "v002"}


def test_camera_package_data_and_build_version_selection(tmp_path: Path) -> None:
    from smartlib.core.camera_package import SCHEMA, camera_package_info
    config_dir = tmp_path / "config"
    write_config(config_dir, tmp_path / "project")
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep001", "sq010", "sh0010")
    payload = dict(schema=SCHEMA, reference_resolution=[1920, 1080],
                   cameras=[dict(role='primary', name='creativeCam')],
                   rows=[dict(layer='CHA', camera='smartCam_CHA', width=2048, height=858,
                              start=1001, end=1100, version=2, take=3)])
    first = service.publish_shot_scene_snapshot(identity, payload, data_type='camera')
    second = service.publish_shot_scene_snapshot(identity, payload, data_type='camera')
    assert len(service.list_camera_package_versions(identity)) == 2
    assert 'smartCam_CHA' in camera_package_info(first)['summary']
    manager = ReviewBuildManagerService(ProjectConfig(config_dir))
    rows = manager.build_contents(identity)
    index = next(i for i, row in enumerate(rows) if row.get('camera_versions'))
    assert rows[index]['component']['path'] == str(second)
    assert len(rows[index]['camera_versions']) == 2
    manager.select_camera_package_version(identity, rows, index, str(first))
    # A newly published version must not change the selected input.
    service.publish_shot_scene_snapshot(identity, payload, data_type='camera')
    rows = manager.build_contents(identity)
    selected = next(row for row in rows if row.get('camera_versions'))
    assert selected['official'] == 'v001'
    assert selected['latest'] == 'v003'
    assert selected['component']['path'] == str(first)
    assert selected['component']['source']['camera_package_version_locked']
    # Same target, different subset remains an independently selectable package.
    other = service.publish_shot_scene_snapshot(identity, payload, data_type='camera', subset='alternate')
    rows = manager.build_contents(identity)
    assert sum(bool(row.get('camera_versions')) for row in rows) == 2
    index = next(i for i, row in enumerate(rows) if row['component']['path'] == str(other))
    manager.select_camera_package_version(identity, rows, index, str(other))
    enabled = [row for row in manager.build_contents(identity) if row.get('camera_versions') and row['enabled']]
    assert len(enabled) == 1 and enabled[0]['component']['path'] == str(other)


def test_native_camera_publish_commits_only_after_dependency_export(tmp_path: Path) -> None:
    import pytest
    config_dir = tmp_path / 'config'
    write_config(config_dir, tmp_path / 'project')
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity('ep001', 'sq010', 'sh0010')
    payload = dict(schema='smartpipeline.camera_package.v2', cameras=[dict(role='primary', name='cam')], rows=[])
    with pytest.raises(ValueError, match='requires'):
        service.publish_shot_scene_snapshot(identity, payload, data_type='camera')
    def export(directory):
        (directory / 'primary.ma').write_text('// test native payload', encoding='utf-8')
        return {'ma': 'primary.ma'}
    published = service.publish_shot_scene_snapshot(identity, payload, data_type='camera', native_exporter=export)
    assert json.loads(published.read_text(encoding='utf-8'))['files'] == {'ma': 'primary.ma'}
    assert json.loads(published.with_name('publish.json').read_text(encoding='utf-8'))['files']['ma'] == 'primary.ma'
    def fail(directory):
        raise RuntimeError('dependency export failed')
    with pytest.raises(RuntimeError):
        service.publish_shot_scene_snapshot(identity, payload, data_type='camera', native_exporter=fail)
    assert service._latest_shot_camera_publish(identity) == published
    assert len(service.list_camera_package_versions(identity)) == 1
