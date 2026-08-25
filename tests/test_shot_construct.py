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
        role="BGA",
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
        role="BGA",
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
                "source": {"kind": "cast_entry", "role": "BGA", "context": "WORK"},
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
                "source": {"kind": "cast_entry", "role": "BGA", "context": "WORK"},
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
                    "source": {"asset": "Hero", "role": "CHA"},
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
                "source": {"role": "CHA"},
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
            "cast": {"hero": {"required": True, "role": "CHA"}}
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
                "source": {"role": "CHA"},
            }]
        },
        load_cast=lambda _identity: {
            "cast": {"hero": {"required": True, "role": "CHA"}}
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
        load_cast=lambda _identity: {"cast": {"chair": {"required": True, "role": "CHA"}}},
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
        load_cast=lambda _identity: {"cast": {"chair": {"required": True, "role": "CHA"}}},
        load_sequence_cast=lambda *_args: {"cast": {}},
        load_shot=lambda _identity: {},
        shot_root=lambda _identity: tmp_path,
    )
    manager.list_constructs = lambda *_args: []
    manager.construct_diff = lambda *_args, **_kwargs: []

    status = manager.shot_status(identity, mode="WORK STAGE")

    assert status.state == "MISSING"
    assert "Animation Curves: chair" in status.message


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
    preview = tmp_path / "publish" / "preview_render" / "anim" / "packages" / "v004" / "render_manifest.json"
    animation = tmp_path / "publish" / "animation" / "package" / "main" / "v005" / "animation_manifest.json"
    for path in (camera, set_dress, preview, animation):
        write_json(path, {"ok": True})
    service.latest_anim_input = lambda _identity: None
    service.list_shot_data_versions = lambda _identity: []
    service.load_dependencies = lambda _identity: {"dependencies": []}
    service._latest_review_camera_paths = lambda _identity: [str(camera)]
    service.list_set_dress_publish_versions = lambda _identity: [
        SimpleNamespace(name="set_dress/main", version="v002", path=str(set_dress), latest=True)
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


def test_latest_shot_camera_falls_back_to_direct_publish(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    project_root = tmp_path / "project"
    write_config(config_dir, project_root)
    service = ShotManagerService(ProjectConfig(config_dir))
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
