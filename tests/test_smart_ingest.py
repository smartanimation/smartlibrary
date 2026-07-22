from __future__ import annotations

from pathlib import Path

from smartlib.apps.smart_ingest.service import IngestMetadata, SmartIngestService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json


def write_config(config_dir: Path, project_root: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "templates_base.yml").write_text(
        "\n".join(
            [
                "anchors:",
                "  project_name: TEST",
                f"  project_root: '{project_root.as_posix()}'",
                "asset_depts:",
                "- model",
                "- rig",
                "shot_depts:",
                "- layout",
                "- anim",
                "templates:",
                "  incoming_root: '{project_root}/incoming'",
                "  staging_root: '{project_root}/staging'",
            ]
        ),
        encoding="utf-8",
    )


def test_auto_plan_asset_copy(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "assets" / "20260605" / "kuma_model_render_v001.fbx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fbx")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    items = service.auto_plan()

    assert len(items) == 1
    item = items[0]
    assert item.status == "Ready"
    assert item.action == "copy"
    assert item.target_type == "Asset"
    assert item.metadata.asset == "KUMA"
    assert item.target_path is not None
    assert "assets/characters/main/KUMA/default/data/model/render/v001" in item.target_path.as_posix()

    result = service.ingest_selected(items)

    assert result.copied == [item.target_path]
    assert item.target_path.exists()
    processed_source = source.parent / "_processed" / source.name
    assert result.processed_sources == [processed_source]
    assert processed_source.exists()
    assert not source.exists()
    assert result.manifests[0].exists()
    manifest = read_json(result.manifests[0])
    assert manifest["state"] == "processed"
    assert manifest["processed_source_path"] == str(processed_source)
    assert manifest["metadata"]["asset"] == "KUMA"


def test_fbm_companion_is_hidden_and_ingested_with_parent_fbx(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    delivery = project_root / "incoming" / "assets" / "20260722"
    source = delivery / "kuma_model_render_v001.fbx"
    texture = delivery / "kuma_model_render_v001.fbm" / "colNml_u21_v1.png"
    texture.parent.mkdir(parents=True)
    source.write_bytes(b"fbx")
    texture.write_bytes(b"png-data")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    items = service.auto_plan()

    assert len(items) == 1
    assert items[0].source_path == source
    assert items[0].size == len(b"fbx") + len(b"png-data")

    result = service.ingest_selected(items)
    target = items[0].target_path
    assert target is not None
    assert (target.with_suffix(".fbm") / texture.name).read_bytes() == b"png-data"
    assert (delivery / "_processed" / source.name).exists()
    assert (delivery / "_processed" / texture.parent.name / texture.name).exists()
    manifest = read_json(result.manifests[0])
    assert manifest["companions"][0]["type"] == "fbm"
    assert manifest["companions"][0]["files"][0]["path"] == texture.name


def test_auto_plan_editorial_copy_uses_data_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "editorial" / "20260430" / "V_con_260106_1_24fps.mov"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"mov")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    item = service.plan_file(source)

    assert item.status == "Ready"
    assert item.action == "copy"
    assert item.target_type == "Editorial"
    assert item.target_path is not None
    assert "editorial/data/ep001/sq010/source/v001/ep001_sq010.mov" in item.target_path.as_posix()


def test_client_editorial_delivery_is_grouped_and_indexed(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    delivery = project_root / "incoming" / "client" / "20260722"
    delivery.mkdir(parents=True)
    sources = []
    for extension in (".aaf", ".edl", ".xml", ".mov"):
        source = delivery / f"ELCD_ep02_s027{extension}"
        source.write_bytes(extension.encode("ascii"))
        sources.append(source)

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    items = [service.plan_file(source) for source in sources]

    assert all(item.target_type == "Editorial" for item in items)
    assert all(item.metadata.episode == "ep02" for item in items)
    assert all(item.metadata.sequence == "s027" for item in items)
    assert all(item.metadata.subset == "source" for item in items)
    assert {item.target_path.name for item in items if item.target_path} == {
        "ep02_s027.aaf",
        "ep02_s027.edl",
        "ep02_s027.xml",
        "ep02_s027.mov",
    }

    result = service.ingest_selected(items)
    version_root = project_root / "editorial" / "data" / "ep02" / "s027" / "source"
    manifest = read_json(version_root / "v001" / "manifest.json")

    assert len(result.copied) == 4
    assert [item["format"] for item in manifest["files"]] == ["aaf", "edl", "mov", "xml"]
    assert read_json(version_root / "latest.json")["version"] == "v001"
    assert read_json(version_root / "versions.json") == [{"version": "v001", "status": "latest"}]
    assert read_json(version_root / "v001" / "validation.json")["status"] == "OK"


def test_editorial_cut_movies_keep_unique_suffixes(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    delivery = project_root / "incoming" / "client" / "20260722"
    delivery.mkdir(parents=True)
    sources = [delivery / f"ep02_s027_c00{index}.mov" for index in range(1, 4)]
    for source in sources:
        source.write_bytes(source.stem.encode("ascii"))

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    items = [service.plan_file(source) for source in sources]

    assert [item.target_path.name for item in items if item.target_path] == [
        "ep02_s027_c001.mov",
        "ep02_s027_c002.mov",
        "ep02_s027_c003.mov",
    ]


def test_processed_package_can_be_restored_without_deleting_history(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "assets" / "20260722" / "kuma_model_render_v001.fbx"
    texture = source.with_suffix(".fbm") / "normal.png"
    texture.parent.mkdir(parents=True)
    source.write_bytes(b"fbx")
    texture.write_bytes(b"png")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    result = service.ingest_selected(service.auto_plan())
    manifest_path = result.manifests[0]
    restored = service.restore_processed_manifest(manifest_path)

    assert source in restored
    assert source.read_bytes() == b"fbx"
    assert texture.read_bytes() == b"png"
    assert manifest_path.exists()
    assert read_json(manifest_path)["restores"][-1]["paths"][0] == str(source)


def test_unknown_file_is_rejected_with_sidecar(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "editorial" / "20260605" / "notes.tmp"
    source.parent.mkdir(parents=True)
    source.write_text("not an ingest asset", encoding="utf-8")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    item = service.plan_file(source)

    assert item.status == "Reject"
    assert item.action == "reject"
    assert item.target_path is not None
    assert item.target_path.parent.name == "20260605"

    result = service.ingest_selected([item])

    assert result.rejected == [item.target_path]
    sidecar = item.target_path.with_suffix(item.target_path.suffix + ".reject.json")
    assert sidecar.exists()
    data = read_json(sidecar)
    assert data["state"] == "rejected"
    assert data["reason"] == "unsupported file type"


def test_processed_files_are_not_scanned_again(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "assets" / "20260605" / "kuma_model_render_v001.fbx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fbx")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    service.ingest_selected(service.auto_plan())

    assert service.auto_plan() == []


def test_rejected_folder_is_scanned_only_when_requested(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    rejected = project_root / "incoming" / "_rejected" / "20260605" / "notes.tmp"
    rejected.parent.mkdir(parents=True)
    rejected.write_text("rejected", encoding="utf-8")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))

    assert service.auto_plan() == []
    items = service.auto_plan(include_rejected=True)
    assert len(items) == 1
    assert items[0].target_type == "Rejected"
    assert items[0].status == "Reject"


def test_manual_metadata_makes_unknown_ready(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "20260605" / "delivery.fbx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fbx")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    item = service.plan_file(source)
    assert item.status == "Needs Metadata"

    updated = service.replan(
        item,
        IngestMetadata(
            target_type="Shot",
            project="TEST",
            episode="ep001",
            sequence="sq010",
            shot="sh020",
            department="layout",
            subset="cache",
            format="fbx",
        ),
    )

    assert updated.status == "Ready"
    assert updated.action == "copy"
    assert updated.target_path is not None
    assert "shots/ep001/sq010/sh020/data/layout/fbx/cache/v001" in updated.target_path.as_posix()
