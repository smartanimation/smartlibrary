from __future__ import annotations

import json
import hashlib
from pathlib import Path

from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.dcc.maya.animation_curves import (
    _normalize_skel_animation_layer,
    rebase_skel_animation_to_asset,
    validate_skel_animation_compatibility,
)
from smartlib.apps.shot_manager.service import _animation_binding_layer_text


def test_animation_binding_hides_skeleton_guide() -> None:
    text = _animation_binding_layer_text(
        [
            {
                "target_skeleton": "/DLI/Root/layout/IF_C_all",
                "animation_source": "/DLI/Root/layout/IF_C_all/Animation",
            }
        ]
    )

    assert 'rel skel:animationSource = </DLI/Root/layout/IF_C_all/Animation>' in text
    assert 'uniform token visibility = "invisible"' in text


def _service(tmp_path: Path) -> tuple[ShotManagerService, ShotIdentity]:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    config_dir.joinpath("templates_base.yml").write_text(
        "\n".join(
            [
                "anchors:",
                "  project_name: TEST",
                f"  project_root: '{project_root.as_posix()}'",
                "templates:",
                "  shots_root: '{project_root}/shots'",
            ]
        ),
        encoding="utf-8",
    )
    config_dir.joinpath("templates_shots.yml").write_text(
        "\n".join(
            [
                "templates:",
                "  shot_root: '{shots_root}/{episode}/{seq}/{shot}'",
            ]
        ),
        encoding="utf-8",
    )
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep01", "sq01", "sh0010")
    shot_root = service.shot_root(identity)
    shot_root.mkdir(parents=True, exist_ok=True)
    shot_root.joinpath("shot.json").write_text(
        json.dumps({"editorial": {"cut_in": 1001, "cut_out": 1010}}),
        encoding="utf-8",
    )
    shot_root.joinpath("cast.json").write_text(
        json.dumps(
            {
                "cast": {
                    "Hero_main": {
                        "asset": "Hero",
                        "variant": "default",
                        "namespace": "Hero_main",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return service, identity


def test_animation_atom_is_authoritative_data_payload(tmp_path: Path) -> None:
    service, identity = _service(tmp_path)
    plan = service.plan_animation_atom_export(identity, target="Hero_main")
    plan["atom_path"].write_text("atomVersion 1.0;", encoding="utf-8")

    manifest_path = service.finalize_animation_atom_export(
        identity,
        {
            "namespace": "Hero_main",
            "transfer_nodes": ["Hero_main:root_CTL", "Hero_main:A_L_IndexFinger1"],
            "payload_sha256": "test-checksum",
            "frame_range": [1001, 1010],
        },
        target="Hero_main",
        version=plan["version"],
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "smartpipeline.animation_atom.v3"
    assert manifest["format"] == "atom"
    assert manifest["payload"] == "animation.atom"
    assert manifest["payload_sha256"] == hashlib.sha256(b"atomVersion 1.0;").hexdigest()
    assert service.latest_animation_curve_path(identity, target="Hero_main") == manifest_path
    rows = service.list_animation_curve_versions(identity, target="Hero_main")
    assert rows[0].path == str(manifest_path)
    latest = json.loads(manifest_path.parents[1].joinpath("latest.json").read_text(encoding="utf-8"))
    assert latest["path"] == "v001/animation_manifest.json"


def test_cache_and_package_fix_curve_data_dependency(tmp_path: Path) -> None:
    service, identity = _service(tmp_path)
    curve_path = service.export_animation_curves_data(
        identity,
        {"curves": {"root.translateX": {"keys": []}}},
        target="Hero_main",
    )
    plan = service.plan_animation_cache_publish(identity, target="Hero_main")
    plan["version_dir"].mkdir(parents=True)
    plan["version_dir"].joinpath("animation.abc").write_bytes(b"abc")

    cache_path = service.finalize_animation_cache_publish(
        identity,
        {
            "files": {"abc": "animation.abc"},
            "frame_range": [1001, 1010],
            "source_set": "Hero_main:cache_geo_set",
            "source_nodes": ["Hero_main:geo"],
            "geometry": ["Hero_main:bodyShape"],
        },
        target="Hero_main",
        asset="Hero",
        namespace="Hero_main",
        curve_data_path=curve_path,
        version=plan["version"],
    )
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["curve_dependency"]["version"] == "v001"
    assert cache["curve_dependency"]["path"].endswith(
        "/data/animation/Hero_main/curves/v001/animation_curve.json"
    )
    assert curve_path.is_file()

    manifest_path = service.build_animation_package_snapshot(identity)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["casts"]["Hero_main"]["cache_version"] == "v001"
    assert manifest["casts"]["Hero_main"]["curve_dependency"]["version"] == "v001"

    publish = json.loads(
        manifest_path.with_name("publish.json").read_text(encoding="utf-8")
    )
    assert publish["curve_data_versions"] == {"Hero_main": "v001"}


def test_usd_skel_cache_composes_latest_asset_entry(tmp_path: Path) -> None:
    __import__("pytest").importorskip("pxr")
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel, Vt

    service, identity = _service(tmp_path)
    project_root = Path(service.project_config.project_root)
    asset_version_dir = (
        project_root
        / "production"
        / "assets"
        / "characters"
        / "main"
        / "Hero"
        / "default"
        / "publish"
        / "asset"
        / "work"
        / "v005"
    )
    asset_version_dir.mkdir(parents=True)
    asset_entry = asset_version_dir / "asset.usda"
    asset_stage = Usd.Stage.CreateNew(str(asset_entry))
    asset_root = UsdGeom.Xform.Define(asset_stage, "/Root")
    asset_stage.SetDefaultPrim(asset_root.GetPrim())
    skeleton = UsdSkel.Skeleton.Define(asset_stage, "/Root/layout/HeroRoot")
    skeleton.CreateJointsAttr(Vt.TokenArray(["root"]))
    mesh = UsdGeom.Mesh.Define(asset_stage, "/Root/geo/body")
    UsdSkel.BindingAPI.Apply(mesh.GetPrim()).CreateSkeletonRel().SetTargets(
        [Sdf.Path("/Root/layout/HeroRoot")]
    )
    asset_stage.GetRootLayer().Save()
    asset_base = asset_version_dir.parent
    asset_base.joinpath("latest.json").write_text(
        json.dumps(
            {
                "version": "v005",
                "path": "v005/asset.mb",
                "usd": "v005/asset.usda",
            }
        ),
        encoding="utf-8",
    )

    dependency = service.resolve_asset_rig_usd_dependency("Hero", "default", "anim")
    assert dependency["publish_type"] == "asset"
    assert dependency["subset"] == "work"
    assert dependency["version"] == "v005"
    assert dependency["path"].endswith("/publish/asset/work/v005/asset.usda")

    plan = service.plan_animation_cache_publish(identity, target="Hero_main")
    plan["version_dir"].mkdir(parents=True)
    animation_stage = Usd.Stage.CreateNew(str(plan["version_dir"] / "animation.usd"))
    animation = UsdSkel.Animation.Define(
        animation_stage, "/Root/layout/HeroRoot/Animation"
    )
    animation.CreateJointsAttr(Vt.TokenArray(["root"]))
    animation.CreateTranslationsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(0)]), 1001)
    animation.GetTranslationsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(1, 0, 0)]), 1010)
    animation_stage.GetRootLayer().Save()
    cache_path = service.finalize_animation_cache_publish(
        identity,
        {
            "files": {"usd": "animation.usd"},
            "frame_range": [1001, 1010],
            "source_set": "Hero_main:cache_geo_set",
            "source_skeleton_set": "Hero_main:skel_export_set",
            "skeleton_bindings": [
                {
                    "source_skeleton": "/assets_grp/Root/layout/HeroRoot",
                    "target_skeleton": "/Root/layout/HeroRoot",
                    "animation_source": "/Root/layout/HeroRoot/Animation",
                }
            ],
            "usd_kind": "usd_skel_animation",
        },
        target="Hero_main",
        asset="Hero",
        namespace="Hero_main",
        rig_dependency=dependency,
        version=plan["version"],
    )

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["asset_usd_dependency"]["version"] == "v005"
    assert cache["files"]["composed_usd"] == "animation_asset.usda"
    assert cache["files"]["timing_usd"] == "timing.usda"

    composed = cache_path.with_name("animation_asset.usda").read_text(encoding="utf-8")
    assert 'defaultPrim = "Root"' in composed
    assert "startTimeCode = 1001" in composed
    assert "endTimeCode = 1010" in composed
    assert "@animation.usd@" in composed
    assert "assets/characters/main/Hero/default/publish/asset/work/v005/asset.usda@" in composed
    assert composed.index("@animation.usd@") < composed.index("asset.usda@")
    composed_stage = Usd.Stage.Open(str(cache_path.with_name("animation_asset.usda")))
    assert composed_stage.GetDefaultPrim().GetPath() == Sdf.Path("/Root")


def test_asset_dependency_prefers_requested_context_latest(tmp_path: Path) -> None:
    service, _identity = _service(tmp_path)
    project_root = Path(service.project_config.project_root)
    asset_publish = (
        project_root
        / "production"
        / "assets"
        / "characters"
        / "main"
        / "Hero"
        / "default"
        / "publish"
        / "asset"
    )
    for context, version in (("anim", "v003"), ("work", "v007")):
        version_dir = asset_publish / context / version
        version_dir.mkdir(parents=True)
        version_dir.joinpath("asset.usda").write_text("#usda 1.0\n", encoding="utf-8")
        version_dir.parent.joinpath("latest.json").write_text(
            json.dumps({"version": version, "usd": f"{version}/asset.usda"}),
            encoding="utf-8",
        )

    dependency = service.resolve_asset_rig_usd_dependency(
        "Hero",
        "default",
        preferred_context="work",
    )

    assert dependency["subset"] == "work"
    assert dependency["version"] == "v007"
    assert dependency["path"].endswith("/publish/asset/work/v007/asset.usda")


def test_alembic_cache_versions_are_independent_from_animation_usd(tmp_path: Path) -> None:
    service, identity = _service(tmp_path)

    usd_plan = service.plan_animation_cache_publish(identity, target="Hero_main")
    usd_plan["version_dir"].mkdir(parents=True)
    usd_plan["version_dir"].joinpath("animation.usd").write_text(
        "#usda 1.0\ndef SkelAnimation \"Animation\" {}\n",
        encoding="utf-8",
    )
    service.finalize_animation_cache_publish(
        identity,
        {"files": {"usd": "animation.usd"}, "usd_kind": "usd_skel_animation"},
        target="Hero_main",
        version=usd_plan["version"],
    )

    abc_plan = service.plan_animation_cache_publish(
        identity,
        target="Hero_main",
        subset="alembic",
    )
    abc_plan["version_dir"].mkdir(parents=True)
    abc_plan["version_dir"].joinpath("animation.abc").write_bytes(b"abc")
    service.finalize_animation_cache_publish(
        identity,
        {"files": {"abc": "animation.abc"}, "usd_kind": "alembic_geometry_cache"},
        target="Hero_main",
        version=abc_plan["version"],
        subset="alembic",
    )

    shot_root = service.shot_root(identity)
    usd_latest = json.loads(
        (shot_root / "publish/animation/Hero_main/cache/latest.json").read_text(encoding="utf-8")
    )
    abc_latest = json.loads(
        (shot_root / "publish/animation/Hero_main/alembic/latest.json").read_text(encoding="utf-8")
    )
    assert usd_latest["version"] == "v001"
    assert abc_latest["version"] == "v001"
    assert service.plan_animation_cache_publish(identity, target="Hero_main")["version"] == "v002"
    assert service.plan_animation_cache_publish(
        identity, target="Hero_main", subset="alembic"
    )["version"] == "v002"


def test_normalized_skel_animation_matches_asset_contract(tmp_path: Path) -> None:
    pxr = __import__("pytest").importorskip("pxr")
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel, Vt

    asset_path = tmp_path / "asset.usda"
    asset_stage = Usd.Stage.CreateNew(str(asset_path))
    skeleton = UsdSkel.Skeleton.Define(asset_stage, "/Root/layout/body")
    skeleton.CreateJointsAttr(Vt.TokenArray(["root", "root/spine"]))
    mesh = UsdGeom.Mesh.Define(asset_stage, "/Root/geo/body")
    UsdSkel.BindingAPI.Apply(mesh.GetPrim()).CreateSkeletonRel().SetTargets(
        [Sdf.Path("/Root/layout/body")]
    )
    asset_stage.GetRootLayer().Save()

    animation_path = tmp_path / "animation.usd"
    animation_stage = Usd.Stage.CreateNew(str(animation_path))
    animation = UsdSkel.Animation.Define(
        animation_stage, "/assets_grp/Root/layout/body/Animation"
    )
    animation.CreateJointsAttr(Vt.TokenArray(["root", "root/spine"]))
    animation.CreateTranslationsAttr().Set(
        Vt.Vec3fArray([Gf.Vec3f(0), Gf.Vec3f(0)]), 1001
    )
    animation.GetTranslationsAttr().Set(
        Vt.Vec3fArray([Gf.Vec3f(1, 0, 0), Gf.Vec3f(0)]), 1010
    )
    animation_stage.GetRootLayer().Save()
    del animation
    del animation_stage

    bindings = _normalize_skel_animation_layer(
        animation_path,
        [
            {
                "source_skeleton": "/assets_grp/Root/layout/body",
                "target_skeleton": "/Root/layout/body",
                "animation_source": "/assets_grp/Root/layout/body/Animation",
            }
        ],
    )
    result = validate_skel_animation_compatibility(asset_path, animation_path, bindings)

    assert result["ok"] is True, result
    assert result["bindings"][0]["sample_range"] == [1001.0, 1010.0]
    normalized_stage = Usd.Stage.Open(str(animation_path))
    assert normalized_stage.GetPrimAtPath("/Root/layout/body/Animation")
    assert not normalized_stage.GetPrimAtPath("/assets_grp")


def test_validation_rejects_animation_outside_asset_skeleton(tmp_path: Path) -> None:
    __import__("pytest").importorskip("pxr")
    from pxr import Gf, Sdf, Usd, UsdSkel, Vt

    asset_path = tmp_path / "asset.usda"
    asset_stage = Usd.Stage.CreateNew(str(asset_path))
    UsdSkel.Skeleton.Define(asset_stage, "/Root/layout/body").CreateJointsAttr(
        Vt.TokenArray(["root"])
    )
    asset_stage.GetRootLayer().Save()
    animation_path = tmp_path / "animation.usda"
    animation_stage = Usd.Stage.CreateNew(str(animation_path))
    animation = UsdSkel.Animation.Define(
        animation_stage, "/assets_grp/Root/layout/body/Animation"
    )
    animation.CreateJointsAttr(Vt.TokenArray(["root"]))
    animation.CreateTranslationsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(0)]), 1001)
    animation_stage.GetRootLayer().Save()

    result = validate_skel_animation_compatibility(
        asset_path,
        animation_path,
        [
            {
                "target_skeleton": "/Root/layout/body",
                "animation_source": "/assets_grp/Root/layout/body/Animation",
            }
        ],
    )
    assert result["ok"] is False
    assert "outside the canonical Asset Skeleton" in result["errors"][0]


def test_rebase_skel_animation_uses_composed_asset_skeleton_path(tmp_path: Path) -> None:
    __import__("pytest").importorskip("pxr")
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel, Vt

    asset_path = tmp_path / "asset.usda"
    asset_stage = Usd.Stage.CreateNew(str(asset_path))
    skeleton = UsdSkel.Skeleton.Define(asset_stage, "/DLI/IF_C_all")
    skeleton.CreateJointsAttr(Vt.TokenArray(["root", "root/spine"]))
    mesh = UsdGeom.Mesh.Define(asset_stage, "/DLI/geo/body")
    UsdSkel.BindingAPI.Apply(mesh.GetPrim()).CreateSkeletonRel().SetTargets(
        [Sdf.Path("/DLI/IF_C_all")]
    )
    asset_stage.GetRootLayer().Save()

    animation_path = tmp_path / "animation.usd"
    animation_stage = Usd.Stage.CreateNew(str(animation_path))
    animation = UsdSkel.Animation.Define(
        animation_stage, "/Root/layout/IF_C_all/Animation"
    )
    animation.CreateJointsAttr(Vt.TokenArray(["root", "root/spine"]))
    animation.CreateTranslationsAttr().Set(
        Vt.Vec3fArray([Gf.Vec3f(0), Gf.Vec3f(0)]), 278
    )
    animation.GetTranslationsAttr().Set(
        Vt.Vec3fArray([Gf.Vec3f(10, 0, 0), Gf.Vec3f(0)]), 411
    )
    animation_stage.GetRootLayer().Save()
    del animation
    del animation_stage

    bindings = rebase_skel_animation_to_asset(
        asset_path,
        animation_path,
        [
            {
                "source_skeleton": "/Root/layout/IF_C_all",
                "target_skeleton": "/Root/layout/IF_C_all",
                "animation_source": "/Root/layout/IF_C_all/Animation",
            }
        ],
    )
    result = validate_skel_animation_compatibility(asset_path, animation_path, bindings)

    assert bindings[0]["target_skeleton"] == "/DLI/IF_C_all"
    assert bindings[0]["animation_source"] == "/DLI/IF_C_all/Animation"
    assert result["ok"] is True, result
    assert result["bindings"][0]["sample_range"] == [278.0, 411.0]
    assert result["bindings"][0]["bound_mesh_count"] == 1


def test_rebase_skel_animation_skips_unbound_helper_skeleton(tmp_path: Path) -> None:
    __import__("pytest").importorskip("pxr")
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel, Vt

    asset_path = tmp_path / "asset.usda"
    asset_stage = Usd.Stage.CreateNew(str(asset_path))
    skeleton = UsdSkel.Skeleton.Define(asset_stage, "/DLI/IF_C_all")
    skeleton.CreateJointsAttr(Vt.TokenArray(["root", "root/spine"]))
    mesh = UsdGeom.Mesh.Define(asset_stage, "/DLI/geo/body")
    UsdSkel.BindingAPI.Apply(mesh.GetPrim()).CreateSkeletonRel().SetTargets(
        [Sdf.Path("/DLI/IF_C_all")]
    )
    asset_stage.GetRootLayer().Save()

    animation_path = tmp_path / "animation.usd"
    animation_stage = Usd.Stage.CreateNew(str(animation_path))
    helper = UsdSkel.Animation.Define(animation_stage, "/Root/layout/J_C_all/Animation")
    helper.CreateJointsAttr(Vt.TokenArray(["helper"]))
    helper.CreateTranslationsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(1, 0, 0)]), 278)
    animation = UsdSkel.Animation.Define(animation_stage, "/Root/layout/IF_C_all/Animation")
    animation.CreateJointsAttr(Vt.TokenArray(["root", "root/spine"]))
    animation.CreateTranslationsAttr().Set(
        Vt.Vec3fArray([Gf.Vec3f(0), Gf.Vec3f(0)]), 278
    )
    animation_stage.GetRootLayer().Save()

    bindings = rebase_skel_animation_to_asset(
        asset_path,
        animation_path,
        [
            {
                "target_skeleton": "/Root/layout/J_C_all",
                "animation_source": "/Root/layout/J_C_all/Animation",
            },
            {
                "target_skeleton": "/Root/layout/IF_C_all",
                "animation_source": "/Root/layout/IF_C_all/Animation",
            },
        ],
    )

    assert len(bindings) == 1
    assert bindings[0]["target_skeleton"] == "/DLI/IF_C_all"


def test_validation_rejects_asset_skeleton_without_bound_meshes(tmp_path: Path) -> None:
    __import__("pytest").importorskip("pxr")
    from pxr import Gf, Usd, UsdSkel, Vt

    asset_path = tmp_path / "asset.usda"
    asset_stage = Usd.Stage.CreateNew(str(asset_path))
    skeleton = UsdSkel.Skeleton.Define(asset_stage, "/DLI/IF_C_all")
    skeleton.CreateJointsAttr(Vt.TokenArray(["root"]))
    asset_stage.GetRootLayer().Save()

    animation_path = tmp_path / "animation.usda"
    animation_stage = Usd.Stage.CreateNew(str(animation_path))
    animation = UsdSkel.Animation.Define(animation_stage, "/DLI/IF_C_all/Animation")
    animation.CreateJointsAttr(Vt.TokenArray(["root"]))
    animation.CreateTranslationsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(1, 0, 0)]), 278)
    animation_stage.GetRootLayer().Save()

    result = validate_skel_animation_compatibility(
        asset_path,
        animation_path,
        [
            {
                "target_skeleton": "/DLI/IF_C_all",
                "animation_source": "/DLI/IF_C_all/Animation",
            }
        ],
    )

    assert result["ok"] is False
    assert any("no bound skin meshes" in error for error in result["errors"])
