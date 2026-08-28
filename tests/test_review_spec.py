from __future__ import annotations

import json
from pathlib import Path

from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
from smartlib.apps.shot_manager.service import validate_cast_data
from smartlib.core.config_loader import ProjectConfig


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


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
    return (
        ShotManagerService(ProjectConfig(config_dir)),
        ShotIdentity("ep01", "sq01", "sh0010"),
    )


def test_review_layers_are_separated_from_cast(tmp_path: Path) -> None:
    service, identity = _service(tmp_path)
    shot_root = service.shot_root(identity)
    _write_json(
        shot_root / "cast.json",
        {
            "cast": {"hero": {"asset": "Hero", "role": "CHA"}},
            "review_layers": {"CHA": {"members": ["hero"], "order": 10}},
        },
    )

    spec_path = service.write_review_layers(
        identity,
        {"CHA": {"members": ["hero"], "order": 10}},
    )

    cast_data = json.loads((shot_root / "cast.json").read_text(encoding="utf-8"))
    review_spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert "review_layers" not in cast_data
    assert review_spec["schema"] == "smartpipeline.review_spec.v1"
    assert review_spec["layers"]["CHA"]["members"] == ["hero"]


def test_review_spec_has_no_default_layers(tmp_path: Path) -> None:
    service, identity = _service(tmp_path)

    assert service.review_layers(identity) == {}


def test_shot_identity_from_scene_path_uses_owning_shot(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    c001 = ShotIdentity("ep02", "s027", "c001")
    c002 = ShotIdentity("ep02", "s027", "c002")
    for identity in (c001, c002):
        _write_json(
            service.shot_root(identity) / "shot.json",
            {
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
            },
        )
    scene = (
        service.shot_root(c002)
        / "work"
        / "maya"
        / "anim"
        / "c002_anim_v001_01.ma"
    )

    assert service.shot_identity_from_path(scene) == c002


def test_latest_preview_render_outputs_reads_recorded_layer_take(
    tmp_path: Path,
) -> None:
    service, identity = _service(tmp_path)
    layer_root = (
        service.shot_root(identity)
        / "output"
        / "preview_render"
        / "anim"
        / "layers"
        / "CHA"
    )
    take_root = layer_root / "v002" / "t003"
    _write_json(
        take_root / "output.json",
        {
            "schema": "preview_render_group_output/v1",
            "group": "CHA",
            "version": "v002",
            "take": "t003",
            "pattern": "shot_anim_CHA_v002_t003_####.png",
        },
    )
    (take_root / "shot_anim_CHA_v002_t003_0278.png").write_bytes(b"png")
    _write_json(
        layer_root / "latest.json",
        {
            "version": "v002",
            "take": "t003",
            "path": "v002/t003/output.json",
        },
    )

    outputs = service.latest_preview_render_outputs(
        identity,
        department="anim",
    )

    assert outputs["CHA"]["version"] == "v002"
    assert outputs["CHA"]["take"] == "t003"
    assert outputs["CHA"]["output_dir"] == str(take_root)


def test_cast_role_does_not_require_review_layer() -> None:
    issues = validate_cast_data(
        {
            "cast": {
                "hero_main": {
                    "asset": "Hero",
                    "variant": "default",
                    "role": "CHA",
                    "namespace": "hero_main",
                    "asset_publish": "approved",
                }
            }
        }
    )

    assert issues == []


def test_legacy_empty_default_layers_are_removed(tmp_path: Path) -> None:
    service, identity = _service(tmp_path)
    _write_json(
        service.shot_root(identity) / "review_spec.json",
        {
            "schema": "smartpipeline.review_spec.v1",
            "layers": {
                "ENV": {"members": [], "order": -10},
                "CHA": {"members": ["hero"], "order": 20},
            },
        },
    )

    assert service.review_layers(identity) == {
        "CHA": {"members": ["hero"], "order": 20}
    }


def test_review_spec_migrates_latest_preview_render_settings(tmp_path: Path) -> None:
    service, identity = _service(tmp_path)
    shot_root = service.shot_root(identity)
    _write_json(
        shot_root / "cast.json",
        {
            "cast": {"hero": {"asset": "Hero", "role": "CHA"}},
            "review_layers": {"CHA": {"members": ["hero"], "order": 10}},
        },
    )
    packages = shot_root / "publish" / "preview_render" / "anim" / "packages"
    _write_json(
        packages / "latest.json",
        {"version": "v003", "path": "v003/render_manifest.json"},
    )
    _write_json(
        packages / "v003" / "render_manifest.json",
        {
            "groups": {
                "CHA": {
                    "order": 0,
                    "camera": "cam_CHA",
                    "frame_range": [1001, 1080],
                    "resolution": [1920, 1080],
                }
            }
        },
    )

    spec = service.load_review_spec(identity)

    layer = spec["layers"]["CHA"]
    assert layer["members"] == ["hero"]
    assert layer["camera"]["name"] == "cam_CHA"
    assert layer["frame_range"] == [1001, 1080]
    assert layer["resolution"]["width"] == 1920


def test_save_review_spec_creates_data_version(tmp_path: Path) -> None:
    service, identity = _service(tmp_path)
    path = service.write_review_layers(
        identity,
        {"CHA": {"members": ["hero"], "order": 10}},
    )

    assert path.name == "review_spec.json"
    assert path.parent.name == "v001"
    assert "/data/review_spec/anim/" in path.as_posix()
    latest = json.loads(
        (path.parent.parent / "latest.json").read_text(encoding="utf-8")
    )
    assert latest == {"version": "v001", "path": "v001/review_spec.json"}


def test_publish_preview_render_promotes_output_and_writes_manifest(
    tmp_path: Path,
) -> None:
    service, identity = _service(tmp_path)
    service.write_review_layers(
        identity,
        {"CHA": {"members": ["hero"], "order": 10}},
        department="anim",
    )
    output_dir = (
        service.shot_root(identity)
        / "output"
        / "preview_render"
        / "anim"
        / "layers"
        / "CHA"
        / "v001"
        / "t001"
    )
    output_dir.mkdir(parents=True)
    first = output_dir / "beauty_1001.png"
    last = output_dir / "beauty_1002.png"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    _write_json(
        output_dir / "output.json",
        {
            "schema": "preview_render_group_output/v1",
            "group": "CHA",
            "version": "v001",
            "take": "t001",
            "pattern": "beauty_####.png",
            "camera": "cam_CHA",
            "frame_range": [1001, 1002],
            "resolution": [1280, 720],
            "file_count": 2,
            "first_file": first.name,
            "last_file": last.name,
            "members": ["hero"],
        },
    )
    _write_json(
        output_dir.parent.parent / "latest.json",
        {
            "version": "v001",
            "take": "t001",
            "path": "v001/t001/output.json",
        },
    )

    manifest_path = service.publish_preview_render_outputs(
        identity,
        department="anim",
        comment="approved",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_path.as_posix().endswith(
        "/publish/preview_render/anim/packages/v001/render_manifest.json"
    )
    assert manifest["shot_root"] == service.shot_root(identity).as_posix()
    assert manifest["layers"]["CHA"]["version"] == "v001"
    assert manifest["review_spec"]["version"] == "v001"
    assert (
        service.shot_root(identity)
        / "publish"
        / "preview_render"
        / "anim"
        / "layers"
        / "CHA"
        / "v001"
        / "t001"
        / "beauty_1001.png"
    ).is_file()


def test_preview_render_record_stays_in_render_without_manifest(
    tmp_path: Path,
) -> None:
    service, identity = _service(tmp_path)
    output_dir = service.paths.shot_render_layer_version_dir(
        identity.episode,
        identity.sequence,
        identity.shot,
        "anim",
        "CHA",
        "v001",
    )
    output_dir.mkdir(parents=True)
    image = output_dir / "beauty_1001.png"
    image.write_bytes(b"frame")
    plan = {
        "department": "anim",
        "groups": [
            {
                "group": "CHA",
                "version": "v001",
                "take": "t001",
                "output_dir": str(output_dir),
                "output_record": "output_t001.json",
                "pattern": "beauty_####.png",
                "camera": "cam_CHA",
                "frame_range": [1001, 1001],
                "resolution": [1280, 720],
            }
        ],
    }

    service.record_preview_render_outputs(
        plan,
        {
            "CHA": {
                "file_count": 1,
                "first_file": str(image),
                "last_file": str(image),
                "members": ["hero"],
            }
        },
    )

    assert (output_dir / "output_t001.json").is_file()
    assert not list(service.shot_root(identity).glob("**/render_manifest.json"))
