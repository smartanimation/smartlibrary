from __future__ import annotations

import json

from smartlib.apps.asset_manager.retarget_publish import (
    generate_retarget_asset_profile,
    latest_retarget_data_profile,
    list_retarget_data_versions,
    list_retarget_versions,
    publish_retarget_profile,
    resolve_retarget_context_rigs,
    save_retarget_data_version,
    standard_test_motion,
    validate_retarget_profile,
)
from smartlib.retarget.profile import load_retarget_profile


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


def test_asset_profile_inherits_template_and_data_is_materialized(tmp_path):
    template = tmp_path / "templates" / "humanoid_v001.json"
    _write_json(template, {
        "source_skeleton": {"namespace_agnostic_prefix": "MC_"},
        "transfer_nodes": {"controls": ["CTL_L_arm"]},
    })
    profile = _profile(tmp_path)
    payload = json.loads(profile.read_text())
    payload["template"] = {"id": "humanoid", "version": "v001", "path": "templates/humanoid_v001.json"}
    payload.pop("source_skeleton")
    payload.pop("transfer_nodes")
    _write_json(profile, payload)

    resolved = load_retarget_profile(profile)
    assert resolved["source_skeleton"]["namespace_agnostic_prefix"] == "MC_"
    assert resolved["transfer_nodes"]["controls"] == ["CTL_L_arm"]

    saved = save_retarget_data_version(tmp_path / "assets" / "DLI", "default", profile)
    materialized = json.loads(saved["profile"].read_text())
    assert materialized["transfer_nodes"]["controls"] == ["CTL_L_arm"]
    assert materialized["template"] == {"id": "humanoid", "version": "v001"}
    assert load_retarget_profile(saved["profile"])["asset"] == "DLI"


def test_generate_profile_resolves_anm_and_mcr_context_packs(tmp_path):
    asset_root = tmp_path / "assets" / "CH" / "main" / "JIN"
    anim_dir = asset_root / "default" / "publish" / "asset" / "anim" / "v001"
    mcp_dir = asset_root / "default" / "publish" / "asset" / "mcp" / "v004"
    anim_dir.mkdir(parents=True)
    mcp_dir.mkdir(parents=True)
    (anim_dir / "JIN.mb").write_bytes(b"anm")
    (mcp_dir / "asset.mb").write_bytes(b"mcr")
    _write_json(anim_dir / "publish.json", {"files": {"mb": "JIN.mb"}})
    _write_json(mcp_dir / "publish.json", {"files": {"mb": "asset.mb"}})
    bundled = tmp_path / "bundled" / "elcd_humanoid_v001.json"
    _write_json(bundled, {
        "template_id": "elcd_humanoid",
        "template_version": "v001",
        "source_skeleton": {"namespace_agnostic_prefix": "MC_"},
        "transfer_nodes": {"controls": ["CTL_L_arm"]},
    })

    rigs = resolve_retarget_context_rigs(asset_root, "default")
    assert rigs["animation_rig_scene"] == anim_dir / "JIN.mb"
    assert rigs["mcr_scene"] == mcp_dir / "asset.mb"
    generated = generate_retarget_asset_profile(
        asset_root,
        "default",
        project_root=tmp_path,
        bundled_template=bundled,
    )
    profile = load_retarget_profile(generated["profile"])
    assert profile["asset"] == "JIN"
    assert profile["template"]["id"] == "elcd_humanoid"
    assert profile["animation_rig_scene"] == (anim_dir / "JIN.mb").resolve().as_posix()
    assert generated["template"] == tmp_path / "library" / "anim" / "retarget" / "templates" / bundled.name
