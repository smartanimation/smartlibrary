from smartlib.apps.retarget_setup.service import RetargetService
from smartlib.core.path_resolver import ProjectPaths, AssetIdentity
from smartlib.retarget.direct import validate_mappings


def test_direct_profile_needs_only_anim_rig_and_creates_ma_job(tmp_path):
    rig = tmp_path / "anim.ma"
    rig.write_text("rig")
    source = tmp_path / "mcr.fbx"
    source.write_bytes(b"fbx")
    service = RetargetService(ProjectPaths(tmp_path), AssetIdentity("character", "main", "DLI"))
    profile = dict(input_mode="mcr_to_anim", animation_rig_scene=str(rig), time_unit="film",
                   reference_frame=0, mappings=[dict(source="MC_LeftHand", target="A_L_wrist", method="orient")])
    assert service.validate(profile) == []
    assert service.fingerprint(profile)
    job = service.prepare_test(profile, source, 1, 12)
    assert job["result_file"] == "result.ma"


def test_invalid_or_duplicate_mapping_is_blocked():
    assert validate_mappings(dict(mappings=[]))
    profile = dict(reference_frame=0, mappings=[dict(source="MC", target="A", method="orient")]*2)
    assert "Duplicate ANIM target: A" in validate_mappings(profile)
