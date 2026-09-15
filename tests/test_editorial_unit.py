from dataclasses import replace

from smartlib.apps.smart_ingest.service import IngestMetadata, SmartIngestService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json
from smartlib.dcc.resolve.export_timeline_csv import write_work_manifest, ingested_editorial_files
from test_smart_ingest import write_config


def test_editorial_unit_overrides_legacy_sequence_without_creating_production(tmp_path):
    project = tmp_path / "project"
    write_config(tmp_path / "config", project)
    service = SmartIngestService(ProjectConfig(tmp_path / "config"))
    source = project / "incoming" / "editorial" / "20260915_01" / "received.xml"
    source.parent.mkdir(parents=True)
    source.write_text("<xmeml/>")
    metadata = IngestMetadata(target_type="Editorial", episode="ep02", sequence="legacy",
                              editorial_unit="full_edit", subset="edit_source")
    item = service.plan_file(source, metadata)
    assert item.target_path == service.paths.editorial_unit_data_dir("ep02", "full_edit", "edit_source") / "v001" / "ep02_full_edit.xml"
    assert service.plan_file(source, replace(metadata, editorial_unit="")).status == "Needs Metadata"
    result = service.ingest_selected([item])
    assert len(result.copied) == 1
    manifest = read_json(item.target_path.parent / "manifest.json")
    assert manifest["editorial_unit"] == "full_edit"
    assert "sequence" not in manifest
    assert not service.paths.sequence_workspace_root("ep02", "full_edit").exists()
    assert ingested_editorial_files(service.project_config, "ep02", "full_edit", "edit_source", extension="xml") == [item.target_path]
    work = service.paths.editorial_unit_work_dir("ep02", "full_edit") / "v001"
    path = write_work_manifest(work, episode="ep02", sequence="full_edit")
    assert read_json(path)["editorial_unit"] == "full_edit"
    assert "sequence" not in read_json(path)


def test_ingest_form_separates_editorial_unit_from_sequence(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from smartlib.apps.smart_ingest.main import SmartIngestWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    write_config(tmp_path / "config", tmp_path / "project")
    window = SmartIngestWindow(SmartIngestService(ProjectConfig(tmp_path / "config")))
    try:
        window._metadata_to_form(IngestMetadata(target_type="Editorial", episode="ep02", editorial_unit="full_edit"))
        assert not window.metadata_rows["editorial_unit"].isHidden()
        assert window.metadata_rows["sequence"].isHidden()
        metadata = window._metadata_from_form()
        assert metadata.editorial_unit == "full_edit"
        assert metadata.sequence == ""
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
