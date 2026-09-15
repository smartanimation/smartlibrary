from types import SimpleNamespace
from pathlib import Path

import pytest

from test_animation_composition import project, build
from smartlib.apps.review_build_manager.service import ReviewBuildManagerService
from smartlib.apps.review_build_manager.composition_sources import validate_review_source
from smartlib.apps.shot_manager.animation_publish import file_hash
from smartlib.core.metadata import read_json, write_json


@pytest.fixture
def submitted(project):
    shots, identity, _ = project
    manager = ReviewBuildManagerService(shots.project_config)
    paths = shots.paths
    directory = paths.shot_build_dir(identity.episode, identity.sequence, identity.shot, "anim", "maya", "preComp", "v001")
    directory.mkdir(parents=True, exist_ok=True)
    scene = paths.artifact_file(directory, "review_construct.ma")
    scene.write_text("// reviewed scene", encoding="utf-8")
    write_json(paths.artifact_file(directory, "build_manifest.json"), {
        "format": "smartpipeline.scene_build_manifest", "episode": identity.episode,
        "sequence": identity.sequence, "shot": identity.shot, "department": "anim",
        "task": "preComp", "scene": str(scene), "status": "validated", "review_requested": False,
    })
    write_json(paths.artifact_file(directory, "validation.json"), {"status": "passed", "results": []})
    profile = manager.delivery_profile_ids()[0]
    root = manager.review_workflow(identity).review_destination_root("anim", profile, manager.review_profiles.delivery_profile(profile))
    # Test fixture version directory; application discovery uses the resolved root.
    receipt = root / "v001"
    write_json(paths.artifact_file(receipt, "review.json"), {
        "schema": "smartpipeline.formal_review.v1", "episode": identity.episode,
        "sequence": identity.sequence, "shot": identity.shot, "department": "anim",
        "state": "SUBMITTED", "profile": profile, "version": "v001",
    })
    source = write_json(paths.artifact_file(receipt, "source_manifest.json"), {
        "schema": "smartpipeline.review_source_manifest.v1", "construct": str(scene),
        "construct_sha256": file_hash(scene),
    })
    return manager, identity, source, scene


def test_lists_exact_submitted_reused_construct(submitted):
    manager, identity, source, scene = submitted
    (scene.parent / "unsubmitted.ma").write_text("unused", encoding="utf-8")
    rows = manager.list_review_animation_sources(identity, task="preComp")
    assert len(rows) == 1
    assert rows[0]["scene"] == str(scene)
    assert rows[0]["state"] == "READY"
    assert manager.list_review_animation_sources(identity, task="animation") == []
    assert manager.list_review_animation_sources(identity, department="lighting") == []
    selected = manager.resolve_review_animation_source(identity, source, task="preComp")
    validate_review_source(selected, scene)


def test_modified_reviewed_scene_is_rejected(submitted):
    manager, identity, source, scene = submitted
    scene.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since Submit"):
        manager.resolve_review_animation_source(identity, source)


def test_selected_receipts_cannot_change(submitted):
    manager, identity, source, scene = submitted
    selected = manager.resolve_review_animation_source(identity, source)
    write_json(scene.parent / "validation.json", {"status": "failed"})
    with pytest.raises(ValueError, match="record changed"):
        validate_review_source(selected, scene)
    assert manager.list_review_animation_sources(identity)[0]["state"] == "BLOCKED"
    with pytest.raises(ValueError, match="validation"):
        manager.resolve_review_animation_source(identity, source)


def test_unsubmitted_and_missing_sources(submitted):
    manager, identity, source, scene = submitted
    scene.unlink()
    assert manager.list_review_animation_sources(identity)[0]["state"] == "BLOCKED"
    review = read_json(source.parent / "review.json")
    review["state"] = "FAILED"
    write_json(source.parent / "review.json", review)
    assert manager.list_review_animation_sources(identity) == []


def test_legacy_receipt_pins_current_source(submitted):
    manager, identity, source, scene = submitted
    data = read_json(source)
    del data["construct_sha256"]
    write_json(source, data)
    selected = manager.resolve_review_animation_source(identity, source)
    assert not selected["checksum_recorded"]
    validate_review_source(selected, scene)
    scene.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Construct changed"):
        validate_review_source(selected, scene)


def test_composition_tab_requires_explicit_review_row(submitted, monkeypatch):
    from PySide6 import QtWidgets
    from smartlib.apps.review_build_manager.composition_ui import CompositionSnapshotMixin
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    manager, identity, source, scene = submitted
    (manager.project_config.config_dir / "project_settings.yml").write_text("pipeline_profile: alembic_cache", encoding="utf-8")
    class Window(CompositionSnapshotMixin, QtWidgets.QWidget):
        def _selected_status(self):
            return SimpleNamespace(identity=identity)
    window = Window()
    window.service = manager
    window.scope_combo = QtWidgets.QComboBox()
    window.scope_combo.addItem("Shot")
    window.department_combo = QtWidgets.QComboBox()
    window.department_combo.addItem("anim")
    window.task_combo = QtWidgets.QComboBox()
    window.task_combo.addItem("preComp")
    window.workflow_tabs = QtWidgets.QTabWidget()
    receipt = scene.parent / "build_manifest.json"
    data = read_json(receipt)
    data["construct"] = {"components": [{"component_type": "rig", "name": "Hero_01",
        "version": "v003", "path": "fixed/rig.mb", "enabled": True,
        "source": {"category": "character", "context": "ANIM"}}]}
    write_json(receipt, data)
    window._setup_composition_tab()
    assert window.composition_details_tabs.count() == 1
    assert window.composition_details_tabs.tabText(0) == "Published Snapshot"
    assert window.review_sources_table.horizontalHeaderItem(0).text() == "Build"
    assert window.review_sources_table.rowCount() == 1
    assert not window.composition_build_btn.isEnabled()
    window.review_sources_table.selectRow(0)
    assert window.composition_build_btn.isEnabled()
    assert window.composition_table.item(0, 2).text() == "Hero_01"
    assert window.composition_table.cellWidget(0, 5).count() == 1
    from PySide6 import QtCore
    window.composition_table.item(0, 0).setCheckState(QtCore.Qt.Unchecked)
    assert not window.composition_build_btn.isEnabled()
    window._refresh_compositions()
    assert window.composition_table.item(0, 0).checkState() == QtCore.Qt.Unchecked
    window.composition_table.item(0, 0).setCheckState(QtCore.Qt.Checked)
    assert window.composition_build_btn.isEnabled()
    assert scene.name in window.review_sources_table.item(0, 1).text()
    from PySide6 import QtGui
    font_id = QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
    if families:
        window.setFont(QtGui.QFont(families[0], 9))
    page_layout = QtWidgets.QVBoxLayout(window)
    page_layout.addWidget(window.workflow_tabs)
    window.resize(1300, 680)
    window.show()
    app.processEvents()
    assert window.review_sources_table.width() <= 480
    assert window.composition_details_tabs.width() > window.review_sources_table.width()
    window.grab().save(str(manager.project_config.config_dir / "construct-list.png"))
    window._refresh_compositions()
    assert window._selected_review_source()["source_manifest"] == str(source)
    window.composition_department.setCurrentText("lighting")
    assert not window.composition_build_btn.isEnabled()
    window.close()


def test_review_window_hosts_composition_snapshot(project, monkeypatch):
    from PySide6 import QtCore, QtWidgets
    from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow
    shots, identity, _ = project
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(ReviewBuildManagerWindow, "_settings", lambda self: QtCore.QSettings(
        str(shots.project_config.config_dir / "test-ui.ini"), QtCore.QSettings.IniFormat))
    def fail_dialog(*args):
        raise AssertionError(str(args[1:]))
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", fail_dialog)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", fail_dialog)
    write_json(shots.paths.artifact_file(shots.shot_root(identity), "cast.json"), {"cast": {}})
    window = ReviewBuildManagerWindow(config_dir=shots.project_config.config_dir)
    assert window.workflow_tabs.indexOf(window.composition_tab) >= 0
    window.close()
    window.deleteLater()
    app.processEvents()


def test_publish_records_review_provenance_and_survives_workspace_removal(project, submitted):
    manager, identity, source, scene = submitted
    manifest_path = build(project)
    data = read_json(manifest_path)
    data["source_workfile"] = str(scene)
    data["source_sha256"] = file_hash(scene)
    data["review_source"] = manager.resolve_review_animation_source(identity, source)
    write_json(manifest_path, data)
    service = project[2]
    published = service.publish_build(identity, manifest_path)
    source.unlink()
    scene.unlink()
    snapshot = service.load(published)
    assert snapshot["review_source"]["review_version"] == "v001"
    assert snapshot["review_source"]["scene_sha256"] == data["source_sha256"]
    with pytest.raises((ValueError, FileNotFoundError)):
        service.publish_build(identity, manifest_path)


def test_snapshot_verification_is_not_an_animation_source(submitted):
    manager, identity, source, scene = submitted
    data = read_json(source)
    data["composition_snapshot"] = {"path": "published/manifest.json", "version": "v001"}
    write_json(source, data)
    assert manager.list_review_animation_sources(identity) == []
