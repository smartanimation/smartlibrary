from copy import deepcopy
from pathlib import Path

import pytest

from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
from smartlib.apps.shot_manager.animation_publish import AnimationCompositionService, BUILD_SCHEMA, file_hash
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from smartlib.core.pipeline_profile import profile_from_settings


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("SMARTPIPELINE_STUDIO_CONFIG", raising=False)
    monkeypatch.delenv("SMARTPIPELINE_STUDIO_CONFIG_DIR", raising=False)
    config = tmp_path / "config"
    config.mkdir()
    (config / "templates_base.yml").write_text(
        f"anchors:\n  project_name: TEST\n  project_root: '{tmp_path.as_posix()}/project'\n", encoding="utf-8"
    )
    shots = ShotManagerService(ProjectConfig(config))
    identity = ShotIdentity("ep01", "sq01", "sh001")
    write_json(shots.paths.artifact_file(shots.shot_root(identity), "shot.json"), {
        "editorial": {"cut_in": 1001, "cut_out": 1010}
    })
    write_json(shots.paths.artifact_file(shots.shot_root(identity), "cast.json"), {
        "cast": {"Hero_01": {"asset": "Hero", "variant": "default", "namespace": "Hero_01"}}
    })
    return shots, identity, AnimationCompositionService(shots)


def build(project, profile="alembic_cache"):
    shots, identity, service = project
    (shots.project_config.config_dir / "project_settings.yml").write_text(
        f"pipeline_profile: {profile}\npipeline_profile_version: 1\n", encoding="utf-8"
    )
    root = shots.paths.animation_build_dir(identity.episode, identity.sequence, identity.shot, "v001")
    root.mkdir(parents=True, exist_ok=True)
    source = shots.paths.artifact_file(root, "source.ma")
    source.write_text("//Maya ASCII 2024 scene\n", encoding="utf-8")
    deform = shots.paths.artifact_file(root, "deform.usda" if profile == "usd_animation" else "deform.abc")
    if profile == "usd_animation":
        from pxr import Usd, UsdGeom
        stage = Usd.Stage.CreateNew(str(deform))
        UsdGeom.Xform.Define(stage, "/Hero")
        mesh = UsdGeom.Mesh.Define(stage, "/Hero/body")
        mesh.GetPointsAttr().Set([(0, 0, 0), (1, 0, 0), (0, 1, 0)], 1001)
        mesh.GetPointsAttr().Set([(0, 0, 1), (1, 0, 1), (0, 1, 1)], 1010)
        mesh.GetFaceVertexCountsAttr().Set([3])
        mesh.GetFaceVertexIndicesAttr().Set([0, 1, 2])
        stage.GetRootLayer().Save()
    else:
        # Build receipt fixture: the Maya adapter owns ABC evaluation validation.
        deform.write_bytes(b"Ogawa fixture")
    digest = file_hash(deform)
    manifest = {
        "schema": BUILD_SCHEMA, "pipeline_profile": profile, "pipeline_profile_version": 1,
        "shot": {"episode": identity.episode, "sequence": identity.sequence, "shot": identity.shot},
        "frame_range": [1001, 1010], "fps": 24, "source_workfile": str(source), "source_sha256": file_hash(source),
        "members": [{"instance_id": "Hero_01", "asset": "Hero", "products": {
            "deform": {"source": str(deform), "sha256": digest, "evaluation": "final_deform",
                       "frame_range": [1001, 1010], "topology_signature": "mesh-topology-v1",
                       "validation": {"ok": True, "source_sha256": digest}}
        }}],
    }
    return write_json(shots.paths.artifact_file(root, "build_manifest.json"), manifest)


def test_profile_contract_rejects_unknown_and_preserves_legacy():
    assert profile_from_settings({}) is None
    assert profile_from_settings({"pipeline_profile": "usd_animation"}).products == ("deform",)
    for settings in ({"pipeline_profile": "bad"}, {"pipeline_profile": "usd_animation", "pipeline_profile_version": 2},
                     {"pipeline_profile": "usd_animation", "pipeline_profile_version": True}):
        with pytest.raises(ValueError):
            profile_from_settings(settings)


def test_publish_is_independent_of_workspace_and_adoption_is_fixed(project):
    _, identity, service = project
    source = build(project)
    original = read_json(source)
    first = service.publish_build(identity, source)
    lighting = service.adopt(identity, first, department="lighting")
    second = service.publish_build(identity, source)
    assert first != second
    Path(original["source_workfile"]).unlink()
    Path(original["members"][0]["products"]["deform"]["source"]).unlink()
    pinned = service.load(lighting)
    assert pinned["base"]["path"] == first.as_posix()
    assert pinned["members"][0]["products"]["deform"]["version"] == "v001"
    assert service.latest(identity) == second
    assert service.load(first)["source"]["path"] != original["source_workfile"]


@pytest.mark.parametrize("change,match", [
    ("source", "changed"), ("range", "timing"), ("cast", "cast mismatch"),
    ("skeleton", "Final deformation"), ("profile", "differs"), ("receipt", "validated Build"),
])
def test_invalid_build_does_not_publish_snapshot(project, change, match):
    _, identity, service = project
    manifest = build(project)
    previous = service.publish_build(identity, manifest)
    data = read_json(manifest)
    if change == "source":
        Path(data["members"][0]["products"]["deform"]["source"]).write_bytes(b"modified")
    elif change == "range":
        data["frame_range"] = [1002, 1010]
    elif change == "cast":
        data["members"][0]["instance_id"] = "Other"
    elif change == "skeleton":
        data["members"][0]["products"]["deform"]["evaluation"] = "skeleton"
    elif change == "profile":
        data["pipeline_profile"] = "usd_animation"
    elif change == "receipt":
        data["members"][0]["products"]["deform"]["validation"]["ok"] = False
    write_json(manifest, data)
    with pytest.raises(ValueError, match=match):
        service.publish_build(identity, manifest)
    assert service.latest(identity) == previous
    assert len(service.list_snapshots(identity)) == 1


def test_commit_failure_leaves_previous_snapshot_active(project, monkeypatch):
    _, identity, service = project
    manifest = build(project)
    previous = service.publish_build(identity, manifest)
    def fail(*args):
        raise RuntimeError("Shot build failed")
    monkeypatch.setattr(service, "_write_entrypoint", fail)
    with pytest.raises(RuntimeError, match="Shot build failed"):
        service.publish_build(identity, manifest)
    assert service.latest(identity) == previous


def test_mutated_publish_and_cross_shot_adoption_rejected(project):
    _, identity, service = project
    manifest = service.publish_build(identity, build(project))
    with pytest.raises(ValueError, match="different shot"):
        service.adopt(ShotIdentity("ep01", "sq01", "sh002"), manifest, department="lighting")
    data = read_json(manifest)
    Path(data["members"][0]["products"]["deform"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        service.load(manifest)


def test_path_resolver_honors_custom_roots_and_rejects_traversal(project, tmp_path):
    shots, identity, _ = project
    from smartlib.core.path_resolver import ProjectPaths
    paths = ProjectPaths(tmp_path, templates={"shot_publish_root": "{project_root}/custom/{episode}/{seq}/{shot}/publish"})
    result = paths.animation_artifact_dir(identity.episode, identity.sequence, identity.shot, "Hero_01", "deform", "v012")
    assert result == tmp_path / "custom/ep01/sq01/sh001/publish/animation/Hero_01/deform/v012"
    for target in ("../escape", "Hero/other", "C:/outside", ""):
        with pytest.raises(ValueError):
            paths.animation_artifact_dir("ep01", "sq01", "sh001", target, "deform", "v001")
    with pytest.raises(ValueError):
        paths.composition_dir("ep01", "sq01", "sh001", version="latest")


def test_real_usd_final_deformation_and_look_revision(project):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom, UsdShade, Sdf
    shots, identity, service = project
    manifest = service.publish_build(identity, build(project, "usd_animation"))
    data = service.load(manifest)
    shot = Usd.Stage.Open(data["entrypoint"]["path"])
    points = UsdGeom.Mesh(shot.GetPrimAtPath("/Shot/Hero_01/Hero/body")).GetPointsAttr()
    assert points.Get(1010)[0][2] == 1
    lighting = service.adopt(identity, manifest, department="lighting")
    look_dir = shots.paths.production_root() / "assets/Hero/look/v001"
    look_dir.mkdir(parents=True)
    look_file = look_dir / "look.usda"
    stage = Usd.Stage.CreateNew(str(look_file))
    root = stage.OverridePrim("/Look")
    material = UsdShade.Material.Define(stage, "/Look/Materials/red")
    mesh = stage.OverridePrim("/Look/Hero/body")
    UsdShade.MaterialBindingAPI.Apply(mesh).Bind(material)
    stage.GetRootLayer().Save()
    selection = {"Hero_01": {"path": str(look_file), "prim_path": "/Look",
                             "topology_signature": "mesh-topology-v1", "variants": {}}}
    revision = service.revise_looks(identity, lighting, selection)
    final = Usd.Stage.Open(service.load(revision)["entrypoint"]["path"])
    mesh = final.GetPrimAtPath("/Shot/Hero_01/Hero/body")
    assert UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0]
    assert UsdGeom.Mesh(mesh).GetPointsAttr().Get(1010)[0][2] == 1
    selection["Hero_01"]["topology_signature"] = "wrong-topology"
    with pytest.raises(ValueError, match="topology mismatch"):
        service.revise_looks(identity, lighting, selection)


def test_look_cannot_override_geometry_even_in_unselected_variant(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    from smartlib.dcc.maya.animation_build import validate_look_usd
    path = tmp_path / "look.usda"
    stage = Usd.Stage.CreateNew(str(path))
    root = stage.OverridePrim("/Look")
    variants = root.GetVariantSets().AddVariantSet("look")
    variants.AddVariant("bad")
    variants.SetVariantSelection("bad")
    with variants.GetVariantEditContext():
        UsdGeom.Mesh.Define(stage, "/Look/body").GetPointsAttr().Set([(0, 0, 0)])
    variants.AddVariant("good")
    variants.SetVariantSelection("good")
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError, match="geometry"):
        validate_look_usd(path, "/Look", {"look": "good"})


def test_config_creator_profile_roundtrip(project, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    import scripts.config_creator as creator
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    shots, _, _ = project
    monkeypatch.setattr(creator, "PROJECTS_ROOT", str(shots.project_config.config_dir.parent))
    window = creator.ConfigCreatorApp()
    try:
        for name in ("usd_animation", "alembic_cache", "maya_rend_atom"):
            window.pipeline_profile_combo.setCurrentIndex(window.pipeline_profile_combo.findData(name))
            window._save_context_configs(str(shots.project_config.config_dir))
            window._load_pipeline_profile_editor(shots.project_config.config_dir.name)
            assert shots.project_config.pipeline_profile.name == name
            assert window.pipeline_profile_combo.currentData() == name
    finally:
        window.close()


def test_atom_profile_requires_matching_transfer_and_applied_rend(project):
    shots, identity, service = project
    manifest = build(project)
    (shots.project_config.config_dir / "project_settings.yml").write_text(
        "pipeline_profile: maya_rend_atom\npipeline_profile_version: 1\n", encoding="utf-8"
    )
    data = read_json(manifest)
    data["pipeline_profile"] = "maya_rend_atom"
    root = manifest.parent
    atom = root / "animation.atom"
    atom.write_text("atomVersion 1.0;\n", encoding="utf-8")
    scene = root / "rend_animation.ma"
    scene.write_text('//Maya ASCII scene\nrequires maya "2024";\n', encoding="utf-8")
    products = {}
    for role, path in (("transfer", atom), ("rend", scene)):
        digest = file_hash(path)
        products[role] = {
            "source": str(path), "sha256": digest, "frame_range": [1001, 1010],
            "validation": {"ok": True, "source_sha256": digest,
                           "atom_applied": role == "rend", "atom_sha256": file_hash(atom)},
        }
    products["transfer"]["transfer_manifest"] = {
        "schema": "smartpipeline.animation_atom.v3",
        "payload_sha256": file_hash(atom), "payload": atom.name,
        "namespace": "Hero_01", "transfer_nodes": ["Hero_01:root_CTL"],
    }
    data["members"][0]["products"] = products
    write_json(manifest, data)
    snapshot = service.publish_build(identity, manifest)
    published = service.load(snapshot)
    transfer = published["members"][0]["products"]["transfer"]
    assert Path(transfer["transfer_manifest"]["path"]).is_file()
    assert read_json(transfer["transfer_manifest"]["path"])["transfer_nodes"] == ["Hero_01:root_CTL"]
    assert "rend_animation.ma" in Path(published["entrypoint"]["path"]).read_text(encoding="utf-8")
    products["rend"]["validation"]["atom_sha256"] = "other-payload"
    write_json(manifest, data)
    with pytest.raises(ValueError, match="different ATOM"):
        service.publish_build(identity, manifest)
    assert service.latest(identity) == snapshot


def test_profile_change_does_not_reinterpret_existing_snapshots(project):
    shots, identity, service = project
    snapshot = service.publish_build(identity, build(project))
    (shots.project_config.config_dir / "project_settings.yml").write_text(
        "pipeline_profile: usd_animation\npipeline_profile_version: 1\n", encoding="utf-8"
    )
    lighting = service.adopt(identity, snapshot, department="lighting")
    assert service.load(lighting)["profile"]["name"] == "alembic_cache"


def test_reject_workspace_dependencies_and_fps_change(project):
    shots, identity, service = project
    manifest = build(project)
    data = read_json(manifest)
    data["dependencies"] = {"camera": data["source_workfile"]}
    write_json(manifest, data)
    with pytest.raises(ValueError, match="Production"):
        service.publish_build(identity, manifest)
    data.pop("dependencies")
    data["fps"] = 30
    write_json(manifest, data)
    with pytest.raises(ValueError, match="FPS"):
        service.publish_build(identity, manifest)
    assert service.latest(identity) is None
