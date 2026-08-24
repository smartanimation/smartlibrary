from pathlib import Path

import pytest

from smartlib.delivery import (
    DeliveryEngine,
    DeliveryInput,
    DeliveryPlanner,
    DeliveryProfile,
    ShotContext,
)
from smartlib.delivery.validators import validate_constructed_package


PROFILE = Path(__file__).parent / "fixtures" / "dandelione_v003.yml"


def _context():
    return ShotContext("ep01", "s027", "c001", "preComp", 2)


def test_dandelione_profile_uses_shot_root_and_formats_version():
    profile = DeliveryProfile.load(PROFILE)

    path = profile.render("maya", _context().tokens())

    assert path.as_posix() == "shot/ep01/s027/c001/01_anim/03_animation/scenes/ep01_s027_c001_preComp_v002.ma"


def test_image_sequence_uses_review_layer_token():
    profile = DeliveryProfile.load(PROFILE)
    tokens = _context().tokens()
    tokens["review_layer"] = "CHB"

    path = profile.render("image_sequence", tokens)

    assert "/CHB/v002/" in path.as_posix()
    assert path.name == "ELCD_ep01_s027_c001_CHB_v002.####.png"


def test_profile_rejects_destination_outside_formal_root(tmp_path):
    profile_path = tmp_path / "invalid.yml"
    profile_path.write_text(
        "profile:\n  id: test\n  version: 1\nroot: shot\npaths:\n  maya: shots/{shot}.ma\n",
        encoding="utf-8",
    )
    profile = DeliveryProfile.load(profile_path)

    with pytest.raises(ValueError, match="formal 'shot' root"):
        profile.render("maya", _context().tokens())


def test_engine_constructs_manifest_and_zip(tmp_path):
    source = tmp_path / "source.ma"
    source.write_text("maya scene", encoding="utf-8")
    profile = DeliveryProfile.load(PROFILE)
    plan = DeliveryPlanner(profile).plan(
        _context(),
        [DeliveryInput("maya.scene.primary", "maya_scene", source, "maya")],
        tmp_path / "delivery",
        job_id="DLV-TEST",
    )

    result = DeliveryEngine().construct(plan, create_contact_sheet=False)

    assert not result.blocked
    assert result.manifest.is_file()
    assert result.archive and result.archive.is_file()
    assert (plan.package_root / plan.items[0].destination).read_text(encoding="utf-8") == "maya scene"


def test_engine_constructs_into_existing_client_root_without_archiving_unrelated_files(tmp_path):
    source = tmp_path / "source.ma"
    source.write_text("maya scene", encoding="utf-8")
    client_root = tmp_path / "ELCD"
    client_root.mkdir()
    (client_root / "unrelated.txt").write_text("keep", encoding="utf-8")
    profile = DeliveryProfile.load(PROFILE)
    plan = DeliveryPlanner(profile).plan(
        _context(),
        [DeliveryInput("maya.scene.primary", "maya_scene", source, "maya")],
        client_root,
        job_id="DLV-EXISTING",
    )

    result = DeliveryEngine().construct(plan, create_contact_sheet=False)

    assert not result.blocked
    assert (client_root / "shot" / "ep01").is_dir()
    assert result.archive is not None
    import zipfile
    with zipfile.ZipFile(result.archive) as archive:
        assert "unrelated.txt" not in archive.namelist()
        assert plan.items[0].destination.as_posix() in archive.namelist()


def test_validation_detects_case_mismatch(tmp_path):
    source = tmp_path / "source.ma"
    source.write_text("maya scene", encoding="utf-8")
    profile = DeliveryProfile.load(PROFILE)
    plan = DeliveryPlanner(profile).plan(
        _context(),
        [DeliveryInput("maya.scene.primary", "maya_scene", source, "maya")],
        tmp_path / "delivery",
    )
    wrong = plan.package_root / Path(str(plan.items[0].destination).replace("preComp", "precomp"))
    wrong.parent.mkdir(parents=True)
    wrong.write_text("maya scene", encoding="utf-8")

    results = validate_constructed_package(plan)

    # Windows resolves the differently-cased path as an existing file, but the
    # delivery contract must still reject its on-disk spelling.
    assert any(row.code == "PATH_CASE_MISMATCH" for row in results)
