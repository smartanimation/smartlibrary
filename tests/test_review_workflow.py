from pathlib import Path
import os
import time

import pytest

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json
from smartlib.apps.shot_manager.service import (
    ShotIdentity,
    ShotManagerService,
    _normalized_playblast_settings,
)
from smartlib.review.workflow import (
    ReviewProfileService,
    ReviewWorkflowService,
    content_fingerprint,
)


def test_profiles_inherit_and_keep_work_png_rend_exr(tmp_path: Path) -> None:
    service = ReviewProfileService(ProjectConfig(tmp_path))
    fast = service.review_profile("fast_default")
    work = service.review_profile("work_default")
    rend = service.review_profile("rend_default")
    assert service.default_review_profile_id() == "fast_default"
    assert fast["stage"] == "FAST"
    assert fast["renderer"] == "maya_hardware2"
    assert work["image_format"] == "png"
    assert work["renderer"] == "maya_playblast"
    assert rend["image_format"] == "exr"
    assert work["resolution"] == [960, 540]
    assert rend["resolution"] == [960, 540]
    assert work["fingerprint"] != rend["fingerprint"]


def test_review_resolution_scale_follows_project_anchors(tmp_path: Path) -> None:
    (tmp_path / "templates_base.yml").write_text(
        "anchors:\n  resolution: [2048, 858]\n",
        encoding="utf-8",
    )
    (tmp_path / "review.yml").write_text(
        "review_profiles:\n  work_default:\n    resolution: [1280, 720]\n",
        encoding="utf-8",
    )

    profile = ReviewProfileService(ProjectConfig(tmp_path)).review_profile("work_default")

    assert profile["resolution_scale"] == 0.5
    assert profile["resolution"] == [1024, 429]


def test_default_review_profile_is_project_configurable(tmp_path: Path) -> None:
    (tmp_path / "review.yml").write_text(
        "default_review_profile: work_default\n",
        encoding="utf-8",
    )

    service = ReviewProfileService(ProjectConfig(tmp_path))

    assert service.default_review_profile_id() == "work_default"


def test_profile_cycle_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "review.yml").write_text(
        "review_profiles:\n  a:\n    extends: b\n  b:\n    extends: a\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cycle"):
        ReviewProfileService(ProjectConfig(tmp_path)).review_profile("a")


def test_assembly_and_dynamic_layers_are_versioned(tmp_path: Path) -> None:
    workflow = ReviewWorkflowService(tmp_path / "shot", tmp_path / "workspace")
    assembly_path = workflow.publish_assembly(
        {
            "members": [
                {
                    "uid": "bear",
                    "name": "Bear_main",
                    "asset": "Bear",
                    "variant": "winter",
                    "behavior": "CURVE",
                },
                {
                    "uid": "room",
                    "name": "Room_main",
                    "asset": "Room",
                    "behavior": "STATIC",
                },
            ]
        }
    )
    assert assembly_path.parent.name == "v001"
    assembly = read_json(assembly_path)
    assert assembly["members"][0]["animation_curve"]["required"] is True
    assert assembly["members"][1]["animation_curve"]["required"] is False

    layers_path = workflow.publish_layer_definition(
        {
            "layers": [
                {
                    "uid": "closeup",
                    "name": "Bear Closeup",
                    "members": ["bear"],
                    "camera": {"name": "cam_closeup"},
                },
                {
                    "uid": "background",
                    "name": "Background Only",
                    "members": ["room"],
                },
            ]
        }
    )
    layers = read_json(layers_path)["layers"]
    assert [layer["slug"] for layer in layers] == ["Bear_Closeup", "Background_Only"]
    assert layers[0]["display_layer"] == "Bear Closeup"
    assert "precomp_placeholder" not in layers[0]
    assert "camera" not in layers[0]


def test_playblast_settings_owns_review_output_contract() -> None:
    settings = _normalized_playblast_settings({
        "rows": [{
            "layer": "review_Bear Closeup",
            "display_layer": "Bear_Display",
            "camera": "cam_closeup",
            "width": 1280,
            "height": 720,
            "start": 1001,
            "end": 1100,
            "overscan": 1.05,
            "preset": "anim_review",
            "output_format": ".png",
            "ae_placeholder": "BEAR",
        }]
    })

    row = settings["rows"][0]
    assert settings["schema"] == "smartpipeline.playblast_settings.v2"
    assert settings["layer_order"] == ["Bear_Closeup"]
    assert row["review_layer_id"] == "Bear_Closeup"
    assert row["display_layer"] == "Bear_Display"
    assert row["camera"] == "cam_closeup"
    assert row["overscan"] == 1.05
    assert row["output_format"] == "png"
    assert row["ae_placeholder"] == "BEAR"


def test_render_manifest_export_requires_completed_material_outputs(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "templates_base.yml").write_text(
        "\n".join([
            "anchors:",
            "  project_name: TEST",
            f"  project_root: '{project_root.as_posix()}'",
            "shot_dept_partitions:",
            "  default: cg",
        ]),
        encoding="utf-8",
    )
    service = ShotManagerService(ProjectConfig(config_dir))
    identity = ShotIdentity("ep01", "sq01", "sh0010")

    payload = {
        "rows": [{
            "layer": "CHA", "display_layer": "CHA", "camera": "cam_CHA",
            "width": 1280, "height": 720, "start": 1001, "end": 1001,
            "version": 2, "take": 3, "enabled": True,
        }]
    }
    with pytest.raises(RuntimeError, match="not complete"):
        service.export_shot_scene_data(
            identity, "render_manifest", payload,
            target="anim", subset="main", filename="render_manifest.json",
        )

    plan = service.plan_preview_render_publish(identity, payload, department="anim")
    group = plan["groups"][0]
    assert group["take"] == "t03"
    assert group["output_dir"].endswith(
        "/workspace/cg/shots/ep01/sq01/sh0010/render/anim/layers/CHA/v002/t03"
    )
    output_dir = Path(group["output_dir"])
    output_dir.mkdir(parents=True)
    image = output_dir / group["pattern"].replace("####", "1001")
    image.write_bytes(b"png")
    service.record_preview_render_outputs(
        plan,
        {"CHA": {
            "file_count": 1,
            "first_file": image.as_posix(),
            "last_file": image.as_posix(),
        }},
    )

    path = service.export_shot_scene_data(
        identity,
        "render_manifest",
        payload,
        target="anim",
        subset="main",
        filename="render_manifest.json",
    )

    settings = read_json(path)
    row = settings["rows"][0]
    item = settings["items"][0]
    assert settings["status"] == "complete"
    assert settings["fingerprint"]
    assert row["output_dir"].endswith(
        "/workspace/cg/shots/ep01/sq01/sh0010/render/anim/layers/CHA/v002/t03"
    )
    assert row["receipt_path"].endswith("/v002/t03/output.json")
    assert item["first_frame_file"].endswith("_v002_t03_1001.png")

    receipt = read_json(row["receipt_path"])
    assert receipt["settings_fingerprint"] == settings["fingerprint"]
    assert receipt["settings_path"] == path.as_posix()


def test_review_construct_versions_are_independent_from_normal_builds(tmp_path: Path) -> None:
    workflow = ReviewWorkflowService(tmp_path / "shot", tmp_path / "workspace")
    assert workflow.next_construct_version("anim", "maya", "main") == "v001"
    (workflow.construct_root("anim", "maya", "main") / "v001").mkdir(parents=True)
    assert workflow.next_construct_version("anim", "maya", "main") == "v002"


def test_canonical_construct_can_be_reused_after_downstream_review_failure(tmp_path: Path) -> None:
    workflow = ReviewWorkflowService(tmp_path / "shot", tmp_path / "workspace")
    version_dir = workflow.construct_root("anim", "maya", "main") / "v001"
    scene = version_dir / "construct.ma"
    scene.parent.mkdir(parents=True)
    scene.write_text("maya", encoding="utf-8")
    from smartlib.core.metadata import write_json
    write_json(
        version_dir / "input_snapshot.json",
        {
            "canonical_fingerprint": "snapshot-hash",
            "scene": str(scene),
            "status": "validated",
        },
    )
    cached = workflow.find_canonical_construct(
        "anim", "maya", "main", "snapshot-hash"
    )
    assert cached and cached["version"] == "v001"


def test_layer_definition_rejects_unknown_assembly_member(tmp_path: Path) -> None:
    workflow = ReviewWorkflowService(tmp_path / "shot", tmp_path / "workspace")
    workflow.publish_assembly({"members": [{"uid": "bear", "name": "Bear"}]})
    with pytest.raises(ValueError, match="Unknown Shot Composition"):
        workflow.publish_layer_definition(
            {"layers": [{"name": "Character", "members": ["rabbit"]}]}
        )


def test_layer_cache_uses_json_fingerprint_and_human_versions(tmp_path: Path) -> None:
    workflow = ReviewWorkflowService(tmp_path / "shot", tmp_path / "workspace")
    profile = ReviewProfileService(ProjectConfig(tmp_path)).review_profile("work_default")
    layer = {"uid": "character", "name": "Character", "slug": "character", "members": ["bear"]}
    fingerprint, dependencies = workflow.layer_fingerprint(
        layer,
        member_snapshots={"bear": {"version": "v018", "hash": "aaa"}},
        camera_snapshot={"version": "v006", "hash": "cam"},
        light_snapshot={},
        frame_range=[278, 411],
        review_profile=profile,
    )
    miss = workflow.find_layer_cache("anim", "character", fingerprint)
    assert miss.state == "MISS"
    assert miss.version == "v001"
    assert miss.directory.name == "v001"
    assert fingerprint not in miss.directory.as_posix()
    manifest = workflow.write_layer_cache_manifest(
        miss,
        layer=layer,
        dependencies=dependencies,
        frame_pattern="frames/character.####.png",
        frame_count=134,
    )
    assert read_json(manifest)["fingerprint"] == fingerprint
    hit = workflow.find_layer_cache("anim", "character", fingerprint)
    assert hit.state == "HIT"
    assert hit.version == "v001"

    changed, _ = workflow.layer_fingerprint(
        layer,
        member_snapshots={"bear": {"version": "v019", "hash": "bbb"}},
        camera_snapshot={"version": "v006", "hash": "cam"},
        light_snapshot={},
        frame_range=[278, 411],
        review_profile=profile,
    )
    assert workflow.find_layer_cache("anim", "character", changed).version == "v002"


def test_layer_cache_reservation_is_exclusive_and_recoverable(tmp_path: Path) -> None:
    workflow = ReviewWorkflowService(tmp_path / "shot", tmp_path / "workspace")
    miss = workflow.find_layer_cache("anim", "character", "abc")
    reserved, lock = workflow.reserve_layer_cache(miss, timeout_seconds=0)
    assert reserved.state == "MISS"
    assert lock and lock.is_file()
    workflow.release_layer_cache(lock)
    assert not lock.exists()


def test_shot_wide_precomp_publish_has_no_department_axis(tmp_path: Path) -> None:
    workflow = ReviewWorkflowService(tmp_path / "shot", tmp_path / "workspace")
    source = tmp_path / "artist.aep"
    source.write_bytes(b"aep")
    published = workflow.publish_precomp(
        source,
        input_schema={"inputs": {"Character": {"placeholder": "INPUT_CHARACTER"}}},
        composition={
            "comp": "final", "fps": 24, "resolution": [1280, 720],
            "duration": 134,
        },
        validation={"status": "passed", "results": []},
        author="artist",
    )
    assert published == tmp_path / "shot" / "publish" / "precomp" / "v001"
    assert workflow.latest_precomp() == published / "aftereffects" / "precomp.aep"


def test_formal_review_contains_only_final_artifacts(tmp_path: Path) -> None:
    workflow = ReviewWorkflowService(tmp_path / "shot", tmp_path / "workspace")
    movie = tmp_path / "job" / "review.mov"
    thumbnail = tmp_path / "job" / "thumbnail.jpg"
    movie.parent.mkdir()
    movie.write_bytes(b"movie")
    thumbnail.write_bytes(b"\xff\xd8jpeg\xff\xd9")
    destination = workflow.submit_review(
        department="anim",
        delivery_profile="internal",
        movie=movie,
        thumbnail=thumbnail,
        review_data={"shot": "c001"},
        source_manifest={"inputs": {"Character": {"version": "v019"}}},
    )
    assert destination == tmp_path / "workspace" / "output" / "review" / "internal" / "v001"
    assert {path.name for path in destination.iterdir()} == {
        "review.mov", "thumbnail.jpg", "review.json", "source_manifest.json"
    }
    assert read_json(destination / "review.json")["state"] == "SUBMITTED"


def test_delivery_profile_controls_shot_destination(tmp_path: Path) -> None:
    workflow = ReviewWorkflowService(tmp_path / "shot", tmp_path / "workspace")
    root = workflow.review_destination_root(
        "anim",
        "client_a",
        {
            "target_template": "{shot_root}/client_review/{profile}/{department}/{version}"
        },
    )
    assert root == tmp_path / "shot" / "client_review" / "client_a" / "anim"


def test_job_cleanup_removes_runtime_but_keeps_manifest(tmp_path: Path) -> None:
    workflow = ReviewWorkflowService(tmp_path / "shot", tmp_path / "workspace")
    _job_id, job_dir = workflow.create_job({})
    (job_dir / "runtime.aep").write_bytes(b"aep")
    (job_dir / "output").mkdir()
    (job_dir / "output" / "temp.mov").write_bytes(b"movie")
    job = read_json(job_dir / "job.json")
    job["state"] = "SUBMITTED"
    from smartlib.core.metadata import write_json
    write_json(job_dir / "job.json", job)
    old = time.time() - 5 * 86400
    os.utime(job_dir, (old, old))
    workflow.cleanup_jobs(
        {"retain_success_days": 3, "retain_failed_days": 30, "retain_logs_days": 90},
        now=time.time(),
    )
    assert (job_dir / "job.json").is_file()
    assert not (job_dir / "runtime.aep").exists()
    assert not (job_dir / "output").exists()


def test_canonical_json_fingerprint_ignores_mapping_order() -> None:
    assert content_fingerprint({"a": 1, "b": 2}) == content_fingerprint({"b": 2, "a": 1})
