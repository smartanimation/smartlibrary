import json
import zipfile
from pathlib import Path

import pytest

from smartlib.core.asset_categories import ASSET_CATEGORIES, canonical_asset_category
from smartlib.delivery import PackageProfile, VendorPackageBuilder

from scripts import config_creator

PROFILE = Path(__file__).parents[1] / "config" / "delivery" / "package_profiles" / "client.json"


def test_internal_asset_categories_are_fixed() -> None:
    assert ASSET_CATEGORIES == ("character", "environment", "prop", "vehicle")
    assert canonical_asset_category("CH", strict=True) == "character"
    assert canonical_asset_category("BG", strict=True) == "environment"
    assert canonical_asset_category("BP", strict=True) == "prop"
    assert canonical_asset_category("VEH", strict=True) == "vehicle"
    with pytest.raises(ValueError, match="Unsupported asset category"):
        canonical_asset_category("misc", strict=True)


def test_client_profile_maps_categories_in_both_directions() -> None:
    profile = PackageProfile.load(PROFILE)
    assert profile.inbound_category("CH") == "character"
    assert profile.inbound_category("BG") == "environment"
    assert profile.inbound_category("BP") == "prop"
    assert profile.outbound_category("character") == "CH"
    assert profile.outbound_category("environment") == "BG"
    assert profile.outbound_category("prop") == "BP"


def test_client_package_keeps_external_category_but_internal_ingest_path(tmp_path: Path) -> None:
    scene = tmp_path / "Hero.ma"
    scene.write_text("// Maya ASCII\n", encoding="utf-8")
    output = tmp_path / "Hero.zip"

    result = VendorPackageBuilder(PackageProfile.load(PROFILE)).build_asset(
        scene=scene,
        texture_root=None,
        output=output,
        project="TEST",
        category="character",
        group="main",
        asset="Hero",
    )

    with zipfile.ZipFile(result.archive) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["target"]["category"] == "CH"
    assert manifest["ingest"]["internal_category"] == "character"
    assert "/assets/character/main/Hero/" in manifest["ingest"]["expected_target_root"]


def test_config_creator_category_mapping_round_trip() -> None:
    mapping = {
        "inbound": {"CH": "character", "CHAR": "character", "BG": "environment"},
        "outbound": {"character": "CH", "environment": "BG"},
    }
    rows = config_creator.category_mapping_editor_rows(mapping)
    assert rows["character"] == {"inbound": "CH, CHAR", "outbound": "CH"}
    assert rows["environment"] == {"inbound": "BG", "outbound": "BG"}
    assert config_creator.category_mapping_from_editor_rows(rows) == mapping


def test_config_creator_rejects_duplicate_inbound_alias() -> None:
    rows = {category: {"inbound": "", "outbound": ""} for category in ASSET_CATEGORIES}
    rows["character"]["inbound"] = "CH"
    rows["environment"]["inbound"] = "ch"
    with pytest.raises(ValueError, match="duplicated"):
        config_creator.category_mapping_from_editor_rows(rows)


def test_config_creator_rejects_unsafe_client_category_code() -> None:
    rows = {category: {"inbound": "", "outbound": ""} for category in ASSET_CATEGORIES}
    rows["character"]["outbound"] = "CH/HERO"
    with pytest.raises(ValueError, match="Invalid outbound category"):
        config_creator.category_mapping_from_editor_rows(rows)


def test_config_creator_saves_mapping_without_losing_profile_fields(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    source = {
        "schema": "smart_delivery.package_profile/v1",
        "profile": {"id": "client", "asset_subset": "client"},
        "layouts": {"asset": {"scene": "scene/{name}"}},
        "category_mapping": {"inbound": {"CH": "character"}, "outbound": {}},
    }
    config_creator.save_package_profile("client", source, root)
    loaded = config_creator.load_package_profile("client", root)
    loaded["category_mapping"] = {
        "inbound": {"BG": "environment"},
        "outbound": {"environment": "BG"},
    }
    config_creator.save_package_profile("client", loaded, root)
    result = config_creator.load_package_profile("client", root)
    assert result["profile"] == source["profile"]
    assert result["layouts"] == source["layouts"]
    assert result["category_mapping"]["outbound"] == {"environment": "BG"}
