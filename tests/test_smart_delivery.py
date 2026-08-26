from pathlib import Path

import json
import zipfile

from smartlib.apps.smart_delivery.service import SmartDeliveryService, expand_sequence


def test_expand_sequence_resolves_hash_pattern(tmp_path: Path):
    for frame in (1001, 1002, 1003):
        (tmp_path / f"shot_CHA.{frame}.png").write_text(str(frame), encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("x", encoding="utf-8")

    rows = expand_sequence(str(tmp_path / "shot_CHA.####.png"))

    assert [row.name for row in rows] == [
        "shot_CHA.1001.png",
        "shot_CHA.1002.png",
        "shot_CHA.1003.png",
    ]


def test_expand_sequence_resolves_printf_pattern(tmp_path: Path):
    for frame in (278, 279):
        (tmp_path / f"CHA.{frame:04d}.png").write_text(str(frame), encoding="utf-8")

    rows = expand_sequence(str(tmp_path / "CHA.%04d.png"))

    assert [row.name for row in rows] == ["CHA.0278.png", "CHA.0279.png"]


def test_asset_ingest_manifest_plans_client_asset_tree(tmp_path: Path):
    project = tmp_path / "project"
    config = tmp_path / "config"
    clients = config / "delivery" / "clients"
    clients.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "dandelione_v003.yml"
    clients.joinpath("dandelione_v003.yml").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    config.joinpath("templates_base.yml").write_text(
        f"anchors:\n  project_name: ELCD\n  project_root: '{project.as_posix()}'\n"
        "templates:\n  production_root: '{project_root}/production'\n"
        "  shots_root: '{production_root}/shots'\n  assets_root: '{production_root}/assets'\n"
        "  sequences_root: '{production_root}/sequences'\n  workspace_root: '{project_root}/workspace'\n"
        "  delivery_root: '{project_root}/delivery'\n  delivery_staging_root: '{workspace_root}/delivery'\n",
        encoding="utf-8",
    )
    package = tmp_path / "ingest" / "v003"
    package.joinpath("scene").mkdir(parents=True)
    package.joinpath("scene", "YOU.mb").write_bytes(b"maya")
    package.joinpath("sourceimages", "body").mkdir(parents=True)
    package.joinpath("sourceimages", "body", "diffuse.tx").write_bytes(b"texture")
    manifest = package / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": "smart_ingest.asset_package.v1", "package_type": "asset",
        "delivery": {"received_from": "ANM"},
        "target": {"category": "CH", "group": "main", "asset": "YOU", "variant": "default"},
        "files": [
            {"role": "scene", "path": "scene/YOU.mb", "required": True},
            {"role": "texture_root", "path": "sourceimages", "required": False},
        ],
    }), encoding="utf-8")

    service = SmartDeliveryService(config)
    plan = service.build_asset_package_plan(manifest, package_root=project, version=3)

    assert [item.destination.as_posix() for item in plan.items] == [
        "asset/CH/main/YOU/rig/ANM/YOU.mb",
        "asset/CH/main/YOU/texture/ANM/body/diffuse.tx",
    ]
    assert plan.metadata["entity_type"] == "asset"
    assert plan.metadata["source_ingest_manifest"] == manifest.as_posix()
    result = service.execute(plan)
    assert result.blocked is False
    assert result.archive and result.archive.is_file()
    assert project.joinpath("asset", "CH", "main", "YOU", "rig", "ANM", "YOU.mb").is_file()
    assert project.joinpath("asset", "CH", "main", "YOU", "texture", "ANM", "body", "diffuse.tx").is_file()


def test_smart_delivery_service_builds_asset_zip_with_selected_profile(tmp_path: Path):
    project = tmp_path / "project"
    config = tmp_path / "config"
    clients = config / "delivery" / "clients"
    clients.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "dandelione_v003.yml"
    clients.joinpath("dandelione_v003.yml").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    config.joinpath("templates_base.yml").write_text(
        f"anchors:\n  project_name: ELCD\n  project_root: '{project.as_posix()}'\n"
        "templates:\n  production_root: '{project_root}/production'\n"
        "  shots_root: '{production_root}/shots'\n  assets_root: '{production_root}/assets'\n"
        "  workspace_root: '{project_root}/workspace'\n  delivery_root: '{project_root}/delivery'\n"
        "  delivery_staging_root: '{workspace_root}/delivery'\n",
        encoding="utf-8",
    )
    scene = tmp_path / "YOU.mb"
    scene.write_bytes(b"maya")
    output = tmp_path / "YOU.zip"

    service = SmartDeliveryService(config)
    result = service.build_exchange_asset(
        profile="vendor", scene=scene, texture_root=None, output=output,
        category="CH", group="main", asset="YOU", variant="default",
    )

    assert result.archive == output
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["profile"] == "vendor"
    assert manifest["ingest"]["expected_target_root"].endswith("assembly/vendor/v###")


def test_build_manifest_autofills_delivery_asset_context(tmp_path: Path):
    project = tmp_path / "project"; config = tmp_path / "config"
    clients = config / "delivery" / "clients"; clients.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "dandelione_v003.yml"
    clients.joinpath("dandelione_v003.yml").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    config.joinpath("templates_base.yml").write_text(
        f"anchors:\n  project_name: ELCD\n  project_root: '{project.as_posix()}'\n"
        "templates:\n  production_root: '{project_root}/production'\n  shots_root: '{production_root}/shots'\n"
        "  assets_root: '{production_root}/assets'\n  workspace_root: '{project_root}/workspace'\n"
        "  delivery_root: '{project_root}/delivery'\n  delivery_staging_root: '{workspace_root}/delivery'\n",
        encoding="utf-8",
    )
    scene = tmp_path / "CITY.ma"; scene.write_text("maya", encoding="utf-8")
    context_manifest = tmp_path / "build_manifest.json"
    context_manifest.write_text(json.dumps({"asset": "CITY", "category": "BG", "group": "sets",
        "variant": "night", "source_scene": scene.as_posix()}), encoding="utf-8")

    defaults = SmartDeliveryService(config).manifest_delivery_defaults(context_manifest)

    assert defaults["delivery_type"] == "Asset"
    assert defaults["scene"] == scene.as_posix()
    assert [defaults[key] for key in ("category", "group", "asset", "variant")] == ["BG", "sets", "CITY", "night"]
