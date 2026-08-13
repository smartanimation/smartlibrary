from __future__ import annotations

import json

from smartlib.apps.asset_manager.retarget_publish import (
    latest_retarget_data_profile,
    list_retarget_data_versions,
    list_retarget_versions,
    publish_retarget_profile,
    save_retarget_data_version,
    standard_test_motion,
    validate_retarget_profile,
)


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _profile(tmp_path):
    anm = tmp_path / "ANM.mb"
    mcr = tmp_path / "MCR.mb"
    anm.write_bytes(b"anm")
    mcr.write_bytes(b"mcr")
    profile = tmp_path / "DLI.json"
    _write_json(profile, {
        "asset": "DLI",
        "mcr_scene": str(mcr),
        "animation_rig_scene": str(anm),
        "source_skeleton": {"namespace_agnostic_prefix": "MC_"},
        "transfer_nodes": {"controls": ["CTL_L_arm"]},
    })
    return profile


def test_standard_test_motion_uses_manifest(tmp_path):
    root = tmp_path / "library" / "anim" / "retarget" / "test_motion"
    fbx = root / "custom_v003.fbx"
    fbx.parent.mkdir(parents=True)
    fbx.write_bytes(b"fbx")
    _write_json(root / "manifest.json", {"file": fbx.name})
    assert standard_test_motion(tmp_path) == fbx


def test_publish_retarget_profile_versions_and_records_test_motion(tmp_path):
    profile = _profile(tmp_path)
    test_root = tmp_path / "library" / "anim" / "retarget" / "test_motion"
    test_fbx = test_root / "humanoid_retarget_test_v001.fbx"
    test_fbx.parent.mkdir(parents=True)
    test_fbx.write_bytes(b"fbx")
    _write_json(test_root / "manifest.json", {"file": test_fbx.name})
    _write_json(test_root / "validation_inventory.json", [{"joints": list(range(80)), "animated": list(range(20))}])
    asset_root = tmp_path / "assets" / "characters" / "hero" / "DLI"

    first = publish_retarget_profile(
        asset_root, "default", profile, project_root=tmp_path, comment="first", run_test_motion=True
    )
    second = publish_retarget_profile(
        asset_root, "default", profile, project_root=tmp_path, comment="second", run_test_motion=True
    )

    assert first["version"] == "v001"
    assert second["version"] == "v002"
    assert first["validation"]["test_motion"]["status"] == "passed"
    assert [item["version"] for item in list_retarget_versions(asset_root, "default")] == ["v002", "v001"]
    latest = json.loads((asset_root / "default" / "publish" / "retarget" / "latest.json").read_text())
    assert latest["version"] == "v002"


def test_save_retarget_data_versions_are_immutable_and_publish_tracks_source(tmp_path):
    profile = _profile(tmp_path)
    asset_root = tmp_path / "assets" / "characters" / "hero" / "DLI"
    first = save_retarget_data_version(asset_root, "default", profile, comment="draft one")
    second = save_retarget_data_version(asset_root, "default", profile, comment="draft two")
    assert first["version"] == "v001"
    assert second["version"] == "v002"
    assert latest_retarget_data_profile(asset_root, "default") == second["profile"]
    assert [item["version"] for item in list_retarget_data_versions(asset_root, "default")] == ["v002", "v001"]
    published = publish_retarget_profile(
        asset_root, "default", second["profile"], project_root=tmp_path, run_test_motion=False
    )
    manifest = json.loads((published["directory"] / "publish.json").read_text())
    assert manifest["source_data"]["version"] == "v002"


def test_validation_blocks_missing_profile_dependencies(tmp_path):
    profile = tmp_path / "bad.json"
    _write_json(profile, {"asset": "DLI"})
    validation = validate_retarget_profile(profile, project_root=tmp_path, test_motion=False)
    assert validation["status"] == "failed"
    assert any("mcr_scene" in error for error in validation["errors"])
