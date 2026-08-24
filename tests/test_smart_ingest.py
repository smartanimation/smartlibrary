from __future__ import annotations

from dataclasses import replace
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
                "- assembly",
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
    source = project_root / "incoming" / "assets" / "20260605_01" / "kuma_model_render_v001.fbx"
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
    assert result.processed_sources == [source]
    assert source.exists()
    state_path = source.parent / "processed.json"
    assert state_path in result.manifests
    state = read_json(state_path)
    record = state["files"][source.name]
    assert record["status"] == "processed"
    assert record["metadata"]["asset"] == "KUMA"
    assert not (source.parent / ".ingest.lock").exists()


def test_asset_assembly_ingest_writes_asset_manager_indexes(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "assets" / "20260722_01" / "DLI.mb"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"maya-binary")
    existing = (
        project_root
        / "production"
        / "assets"
        / "CH"
        / "main"
        / "DLI"
        / "default"
        / "data"
        / "assembly"
        / "client"
        / "v002"
    )
    existing.mkdir(parents=True)

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    item = service.plan_file(
        source,
        IngestMetadata(
            target_type="Asset",
            project="TEST",
            asset="DLI",
            category="CH",
            group="main",
            variant="default",
            department="assembly",
            subset="client",
            format="mb",
            comment="client assembly ingest",
        ),
    )

    assert item.status == "Ready"
    assert item.target_path == existing.parent / "v003" / "DLI.mb"

    result = service.ingest_selected([item])

    assert result.copied == [item.target_path]
    assert item.target_path.exists()
    manifest = read_json(existing.parent / "v003" / "manifest.json")
    assert manifest["data_type"] == "assembly"
    assert manifest["subset"] == "client"
    assert manifest["files"] == {"assembly": "DLI.mb"}
    assert manifest["source_file"].endswith("/incoming/assets/20260722_01/DLI.mb")
    assert read_json(existing.parent / "latest.json") == {
        "version": "v003",
        "path": "v003/DLI.mb",
        "manifest": "v003/manifest.json",
    }
    assert read_json(existing.parent / "versions.json") == [
        {"version": "v003", "status": "latest", "comment": "client assembly ingest"}
    ]


def test_fbm_companion_is_hidden_and_ingested_with_parent_fbx(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    delivery = project_root / "incoming" / "assets" / "20260722_01"
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
    assert source.exists()
    assert texture.exists()
    state = read_json(delivery / "processed.json")
    record = state["files"][source.name]
    assert record["companions"][0]["type"] == "fbm"
    assert record["companions"][0]["files"][0]["path"] == texture.name


def test_auto_plan_editorial_copy_uses_data_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "editorial" / "20260430_01" / "V_con_260106_1_24fps.mov"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"mov")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    item = service.plan_file(source)

    assert item.status == "Ready"
    assert item.action == "copy"
    assert item.target_type == "Editorial"
    assert item.target_path is not None
    assert "editorial/data/ep001/sq010/offline/v001/ep001_sq010.mov" in item.target_path.as_posix()


def test_client_editorial_delivery_is_split_by_role_and_indexed(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    delivery = project_root / "incoming" / "client" / "20260722_01"
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
    assert [item.metadata.subset for item in items] == [
        "edit_source",
        "edit_source",
        "edit_source",
        "offline",
    ]
    assert {item.target_path.name for item in items if item.target_path} == {
        "ep02_s027.aaf",
        "ep02_s027.edl",
        "ep02_s027.xml",
        "ep02_s027.mov",
    }

    result = service.ingest_selected(items)
    editorial_root = project_root / "editorial" / "data" / "ep02" / "s027"
    edit_root = editorial_root / "edit_source"
    offline_root = editorial_root / "offline"
    edit_manifest = read_json(edit_root / "v001" / "manifest.json")

    assert len(result.copied) == 4
    assert [item["format"] for item in edit_manifest["files"]] == ["aaf", "edl", "xml"]
    assert read_json(edit_root / "latest.json")["version"] == "v001"
    assert read_json(offline_root / "latest.json")["version"] == "v001"
    assert read_json(edit_root / "versions.json") == [{"version": "v001", "status": "latest"}]
    delivery_manifest = read_json(editorial_root / "deliveries" / "20260722_01" / "manifest.json")
    assert {entry["role"] for entry in delivery_manifest["entries"]} == {
        "edit_source",
        "offline",
    }


def test_audio_is_ingested_as_shot_data(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "client" / "20260722_01" / "ep02_s027_sh020_dialog.wav"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"wav")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    item = service.plan_file(source)

    assert item.status == "Ready"
    assert item.action == "copy"
    assert item.target_type == "Shot"
    assert item.metadata.department == "audio"
    assert item.metadata.subset == "dialog"
    assert item.metadata.episode == "ep02"
    assert item.metadata.sequence == "s027"
    assert item.metadata.shot == "sh020"
    assert item.target_path is not None
    assert "shots/ep02/s027/sh020/data/audio/dialog/v001/ep02_s027_sh020_dialog.wav" in item.target_path.as_posix()


def test_audio_without_shot_waits_for_metadata(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "client" / "20260722_01" / "ep02_s027_dialog.wav"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"wav")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    item = service.plan_file(source)

    assert item.status == "Needs Metadata"
    assert item.target_type == "Shot"
    assert item.metadata.department == "audio"
    assert item.metadata.subset == "dialog"
    assert item.reason == "shot and department are required"


def test_editorial_cut_movies_keep_unique_suffixes(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    delivery = project_root / "incoming" / "client" / "20260722_01"
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
    assert [item.metadata.subset for item in items] == [
        "shot_media/c001",
        "shot_media/c002",
        "shot_media/c003",
    ]
    assert [item.target_path.parent.parent.name for item in items if item.target_path] == [
        "c001",
        "c002",
        "c003",
    ]


def test_processed_package_can_be_marked_for_retry(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "assets" / "20260722_01" / "kuma_model_render_v001.fbx"
    texture = source.with_suffix(".fbm") / "normal.png"
    texture.parent.mkdir(parents=True)
    source.write_bytes(b"fbx")
    texture.write_bytes(b"png")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    result = service.ingest_selected(service.auto_plan())
    state_path = source.parent / "processed.json"
    retried = service.retry_processed_record(state_path, source.name)

    assert retried == source
    assert source.read_bytes() == b"fbx"
    assert texture.read_bytes() == b"png"
    assert read_json(state_path)["files"][source.name]["status"] == "pending"
    assert service.auto_plan()[0].source_path == source


def test_unknown_file_is_rejected_with_sidecar(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "editorial" / "20260605_01" / "notes.tmp"
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
    source = project_root / "incoming" / "assets" / "20260605_01" / "kuma_model_render_v001.fbx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fbx")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    service.ingest_selected(service.auto_plan())

    assert service.auto_plan() == []


def test_non_cg_file_can_be_ignored_and_retried(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "client" / "20260722_01" / "readme.txt"
    source.parent.mkdir(parents=True)
    source.write_text("notes for client delivery", encoding="utf-8")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    item = service.plan_file(source)

    assert item.status == "Reject"
    assert item.target_type == "Rejected"

    result = service.ignore_items([replace(item, selected=True)])
    state_path = source.parent / "processed.json"

    assert result.processed_sources == [source]
    assert state_path in result.manifests
    record = read_json(state_path)["files"][source.name]
    assert record["status"] == "ignored"
    assert record["target_type"] == "Ignored"
    assert source.exists()
    assert service.auto_plan() == []

    retried = service.retry_processed_record(state_path, source.name)

    assert retried == source
    assert service.auto_plan()[0].source_path == source


def test_delivery_folder_requires_numbered_receipt_name(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "assets" / "20260605" / "kuma_model_render_v001.fbx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fbx")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    item = service.plan_file(source)

    assert item.status == "Needs Metadata"
    assert item.reason == "delivery folder must match YYYYMMDD_##"


def test_delivery_lock_blocks_parallel_ingest(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    delivery = project_root / "incoming" / "assets" / "20260605_01"
    source = delivery / "kuma_model_render_v001.fbx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fbx")

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    items = service.auto_plan()
    lock_path = delivery / ".ingest.lock"
    lock_path.mkdir()
    (lock_path / "owner.json").write_text(
        '{"user":"other","machine":"workstation","acquired_at":"2026-07-23T12:00:00"}',
        encoding="utf-8",
    )

    try:
        service.ingest_selected(items)
    except RuntimeError as exc:
        assert "locked by another ingest process" in str(exc)
        assert "other / workstation" in str(exc)
    else:
        raise AssertionError("Parallel ingest should have been blocked")

    assert source.exists()
    assert items[0].target_path is not None
    assert not items[0].target_path.exists()


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
    source = project_root / "incoming" / "20260605_01" / "delivery.fbx"
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


def test_sequence_data_types_are_loaded_from_default_config(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))

    assert service.sequence_data_types() == ["virtual_camera", "mocap"]


def test_manual_mocap_sequence_metadata_becomes_ready(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "client" / "20260722_01" / "ELCD_ep02_s027_DLI.fbx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fbx")
    service = SmartIngestService(ProjectConfig(tmp_path / "config"))

    item = service.plan_file(
        source,
        IngestMetadata(
            target_type="Sequence",
            project="TEST",
            department="mocap",
            subset="DLI",
            format="fbx",
            episode="ep02",
            sequence="s027",
        ),
    )

    assert item.status == "Ready"
    assert item.action == "copy"
    assert item.target_path is not None
    assert "sequences/ep02/s027/data/mocap/fbx/DLI/v001/ELCD_ep02_s027_DLI.fbx" in item.target_path.as_posix()


def test_virtual_camera_fbx_and_mov_share_take_version_package(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    delivery = project_root / "incoming" / "client" / "20260722_01"
    delivery.mkdir(parents=True)
    sources = [
        delivery / "ep02s27_ep02s27c01_Take06.fbx",
        delivery / "ep02s27_ep02s27c01_Take06.mov",
    ]
    for source in sources:
        source.write_bytes(source.suffix.encode("ascii"))
    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    metadata = IngestMetadata(
        target_type="Sequence",
        project="TEST",
        department="virtual_camera",
        subset="take06",
        episode="ep02",
        sequence="s027",
    )

    items = [service.plan_file(source, metadata) for source in sources]

    expected_root = (
        project_root
        / "sequences"
        / "ep02"
        / "s027"
        / "data"
        / "virtual_camera"
        / "take06"
    )
    assert [item.target_path for item in items] == [
        expected_root / "v001" / sources[0].name,
        expected_root / "v001" / sources[1].name,
    ]

    service.ingest_selected(items)

    manifest = read_json(expected_root / "v001" / "manifest.json")
    assert manifest["data_type"] == "virtual_camera"
    assert manifest["take"] == "take06"
    assert set(manifest["files"]) == {"fbx", "mov"}
    assert read_json(expected_root / "latest.json")["version"] == "v001"
    assert read_json(expected_root / "versions.json") == [{"version": "v001", "status": "latest"}]


def test_virtual_camera_metadata_is_inferred_from_compact_filename(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    delivery = project_root / "incoming" / "client" / "20260722_01"
    delivery.mkdir(parents=True)
    sources = [
        delivery / "ep02s27_ep02s27c01_Take06.fbx",
        delivery / "ep02s27_ep02s27c01_Take06.mov",
    ]
    for source in sources:
        source.write_bytes(source.suffix.encode("ascii"))

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    items = service.auto_plan()

    assert all(item.status == "Ready" for item in items)
    assert all(item.target_type == "Sequence" for item in items)
    assert all(item.metadata.department == "virtual_camera" for item in items)
    assert all(item.metadata.episode == "ep02" for item in items)
    assert all(item.metadata.sequence == "s027" for item in items)
    assert all(item.metadata.subset == "take06" for item in items)
    assert all("/data/virtual_camera/take06/v001/" in item.target_path.as_posix() for item in items)


def test_editorial_data_roles_are_loaded_from_naming_config(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)

    service = SmartIngestService(ProjectConfig(tmp_path / "config"))

    assert service.editorial_data_roles() == ["edit_source", "offline", "shot_media"]


def test_manual_editorial_shot_media_requires_shot(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    write_config(tmp_path / "config", project_root)
    source = project_root / "incoming" / "client" / "20260722_01" / "preview.mov"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"mov")
    service = SmartIngestService(ProjectConfig(tmp_path / "config"))

    item = service.plan_file(
        source,
        IngestMetadata(
            target_type="Editorial",
            project="TEST",
            episode="ep02",
            sequence="s027",
            subset="shot_media",
            format="mov",
        ),
    )

    assert item.status == "Needs Metadata"
    assert item.reason == "shot is required for editorial shot_media"
