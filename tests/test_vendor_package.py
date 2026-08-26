from __future__ import annotations

import json
import zipfile
from pathlib import Path

from smartlib.apps.smart_ingest.service import SmartIngestService
from smartlib.core.config_loader import ProjectConfig
from smartlib.delivery.vendor_exporter import PackageProfile, VendorPackageBuilder


PROFILE = Path(__file__).parents[1] / "config" / "delivery" / "package_profiles" / "vendor.json"


def _config(root: Path) -> Path:
    config = root / "config"
    config.mkdir()
    (config / "templates_base.yml").write_text(
        f"anchors:\n  project_name: ELCD\n  project_root: '{root.as_posix()}'\n"
        "templates:\n  incoming_root: '{project_root}/incoming'\n  staging_root: '{project_root}/staging'\n",
        encoding="utf-8",
    )
    return config


def test_asset_package_supports_optional_texture_and_auto_plan(tmp_path: Path):
    scene = tmp_path / "source" / "YOU.mb"
    scene.parent.mkdir()
    scene.write_bytes(b"maya")
    archive = tmp_path / "incoming" / "vendors" / "sample" / "20260826_01" / "YOU.zip"
    result = VendorPackageBuilder(PackageProfile.load(PROFILE)).build_asset(
        scene=scene, texture_root=None, output=archive, project="ELCD", category="CH",
        group="main", asset="YOU", variant="default", comment="test",
    )

    with zipfile.ZipFile(result.archive) as package:
        assert package.namelist() == ["manifest.json", "scene/YOU.mb"]
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["ingest"]["auto_plan"] is True
        assert manifest["ingest"]["expected_target_root"].endswith("/assembly/vendor/v###")

    service = SmartIngestService(ProjectConfig(_config(tmp_path)))
    item = service.auto_plan()[0]
    assert item.action == "expand_package"
    assert item.target_path and item.target_path.as_posix().endswith("/assembly/vendor/v001")
    ingested = service.ingest_selected([item])
    assert (item.target_path / "scene" / "YOU.mb").read_bytes() == b"maya"
    assert (item.target_path / "manifest.json").is_file()
    assert ingested.processed_sources == [archive]


def test_asset_package_preserves_texture_relative_paths(tmp_path: Path):
    scene = tmp_path / "YOU.mb"; scene.write_bytes(b"maya")
    texture = tmp_path / "textures" / "body" / "diffuse.png"
    texture.parent.mkdir(parents=True); texture.write_bytes(b"png")
    archive = tmp_path / "YOU.zip"
    result = VendorPackageBuilder(PackageProfile.load(PROFILE)).build_asset(
        scene=scene, texture_root=texture.parents[1], output=archive, project="ELCD",
        category="CH", group="main", asset="YOU",
    )
    assert "sourceimages/body/diffuse.png" in result.files


def test_asset_assembly_manifest_preserves_reference_policy(tmp_path: Path):
    scene = tmp_path / "BG_set_assembly.ma"
    scene.write_text('file -r "Z:/vendor/assets/YOU.ma";', encoding="utf-8")
    archive = tmp_path / "assembly.zip"
    result = VendorPackageBuilder(PackageProfile.load(PROFILE)).build_asset(
        scene=scene, texture_root=None, output=archive, project="ELCD",
        category="BG", group="sets", asset="CITY", assembly=True,
    )

    assembly = result.manifest["assembly"]
    assert assembly["entity"] == "asset"
    assert assembly["scene"] == "scene/BG_set_assembly.ma"
    assert assembly["reference_policy"]["preserve_maya_references"] is True
    assert assembly["reference_policy"]["vendor_absolute_paths_are_production_truth"] is False
    assert assembly["placements"] == []


def test_shot_package_does_not_use_asset_assembly_contract(tmp_path: Path):
    cache = tmp_path / "ep02_s027_c001.abc"; cache.write_bytes(b"abc")
    result = VendorPackageBuilder(PackageProfile.load(PROFILE)).build_shot(
        sources=[cache], output=tmp_path / "shot.zip",
        target={"target_type": "Shot", "project": "ELCD", "episode": "ep02",
                "sequence": "s027", "shot": "c001", "department": "anim", "subset": "vendor"},
    )
    assert "assembly" not in result.manifest
