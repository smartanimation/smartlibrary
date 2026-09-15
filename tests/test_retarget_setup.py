from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartlib.apps.retarget_setup.service import RetargetService, read, write
from smartlib.core.path_resolver import AssetIdentity, ProjectPaths


@pytest.fixture
def setup(tmp_path):
    paths = ProjectPaths(tmp_path)
    service = RetargetService(paths, AssetIdentity("CH", "main", "DLI", "costume"))
    for role in ("MCR", "ANM"):
        rig = paths.legacy_retarget_rig(service.identity, role)
        rig.parent.mkdir(parents=True, exist_ok=True)
        rig.write_bytes(role.encode())
    profile = {"asset": "DLI", "time_unit": "film", "source_skeleton": {"namespace_agnostic_prefix": "MC_"}, "transfer_nodes": {"controls": ["CTL_allLocal"]}, **service.resolve_rigs()}
    motion = tmp_path / "received.fbx"
    motion.write_bytes(b"fbx")
    return service, profile, motion


def completed(service, profile, motion):
    job = service.prepare_test(profile, motion, 1, 10)
    service.path("test", job["run_id"], "result.mb").write_bytes(b"maya")
    write(service.path("test", job["run_id"], "report.json"), {"keyed_plugs": 6, "skipped_plugs": []})
    return service.complete_test(job, 0)


def test_character_storage_ignores_clothing_and_respects_templates(tmp_path):
    paths = ProjectPaths(tmp_path, templates={"asset_data_root": "{project_root}/custom/{asset}/{variant}/data"})
    a = RetargetService(paths, AssetIdentity("CH", "main", "DLI", "coat"))
    b = RetargetService(paths, AssetIdentity("CH", "main", "DLI", "uniform"))
    assert a.path("data") == b.path("data") == tmp_path / "custom" / "DLI" / "data" / "retarget"
    with pytest.raises(ValueError):
        a.path("test", "../escape")


def test_test_input_is_not_saved_in_character_profile(setup):
    service, profile, motion = setup
    profile.update(variant="coat", mocap_fbx=str(motion), frame_range=[5, 20])
    saved = service.save_data(profile)
    data = read(saved)
    assert not {"variant", "mocap_fbx", "frame_range"} & data.keys()
    job = service.prepare_test(profile, motion, 10, 30)
    run = read(service.path("test", job["run_id"], "profile.json"))
    assert run["frame_range"] == [10, 30]
    assert run["mocap_fbx"] == motion.resolve().as_posix()


def test_immutable_versions_and_publish_provenance(setup):
    service, profile, motion = setup
    first = service.save_data(profile)
    original = first.read_bytes()
    profile["time_unit"] = "pal"
    second = service.save_data(profile)
    assert first != second and first.read_bytes() == original
    job = completed(service, profile, motion)
    with pytest.raises(ValueError):
        service.publish(second, job)
    job = service.review_test(job, True)
    published = service.publish(second, job, "approved motion")
    entry = service.resolve_published()
    assert entry["profile"] == published
    assert entry["manifest"]["source_data"]["version"] == "v002"
    assert entry["manifest"]["test_motion"]["reviewed"]


@pytest.mark.parametrize("change", ["rig", "settings", "motion"])
def test_changed_inputs_cannot_reuse_validation(setup, change):
    service, profile, motion = setup
    data = service.save_data(profile)
    job = service.review_test(completed(service, profile, motion), True)
    if change == "rig":
        Path(profile["mcr_scene"]).write_bytes(b"new rig content")
    elif change == "settings":
        profile["time_unit"] = "pal"
        data = service.save_data(profile)
    else:
        motion.write_bytes(b"updated motion")
    with pytest.raises(ValueError):
        service.publish(data, job)


@pytest.mark.parametrize("code,report,output", [(1, {"keyed_plugs": 5}, True), (0, {}, True), (0, {"keyed_plugs": 5, "skipped_plugs": ["locked.tx"]}, True), (0, {"keyed_plugs": 5}, False)])
def test_failed_or_incomplete_maya_run_never_passes(setup, code, report, output):
    service, profile, motion = setup
    job = service.prepare_test(profile, motion, 1, 2)
    if output:
        service.path("test", job["run_id"], "result.mb").write_bytes(b"maya")
    write(service.path("test", job["run_id"], "report.json"), report)
    job = service.complete_test(job, code)
    assert job["status"] == "failed"
    with pytest.raises(ValueError):
        service.review_test(job, True)


def test_legacy_data_loads_without_modification(setup):
    service, profile, motion = setup
    legacy = service.paths.legacy_retarget_roots(service.identity, "data")[-1]
    path = legacy / "v004" / "DLI_retarget.json"
    write(path, {**profile, "variant": "default"})
    write(path.parent / "data.json", {"profile": path.name})
    original = path.read_bytes()
    loaded = service.load_initial()
    assert loaded["asset"] == "DLI" and "variant" not in loaded
    service.save_draft(loaded)
    assert path.read_bytes() == original
    assert service.history("data")[0]["legacy"]


def test_import_materializes_template_and_rejects_wrong_character(setup, tmp_path):
    service, profile, _ = setup
    write(tmp_path / "template.json", profile)
    source = write(tmp_path / "input.json", {"asset": "DLI", "template": {"path": "template.json", "version": "v001"}})
    imported = service.import_profile(source)
    assert "path" not in imported["template"]
    assert imported["transfer_nodes"] == profile["transfer_nodes"]
    with pytest.raises(ValueError):
        service.clean({"asset": "OTHER"})


def test_bundled_template_is_available(setup):
    service, _, _ = setup
    profile = service.new_profile()
    assert profile["asset"] == "DLI"
    assert profile["transfer_nodes"] and profile["mcr_scene"]


def test_intentionally_locked_attributes_are_reported_separately(setup):
    service, profile, motion = setup
    job = service.prepare_test(profile, motion, 1, 2)
    service.path("test", job["run_id"], "result.mb").write_bytes(b"maya")
    write(service.path("test", job["run_id"], "report.json"), {
        "keyed_plugs": 5, "skipped_plugs": ["locked.tx"], "locked_plugs": ["locked.tx"],
        "missing_plugs": [], "failed_plugs": [],
    })
    assert service.complete_test(job, 0)["status"] == "passed"
