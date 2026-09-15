from copy import deepcopy
import pytest
from test_animation_composition import project, build
from smartlib.core.metadata import read_json, write_json


def test_revision_keeps_original_and_pins_selected_bundle(project):
    shots, identity, service = project
    first = service.publish_build(identity, build(project))
    before = first.read_bytes()
    second = service.publish_build(identity, build(project))
    revision = service.revise_members(identity, first, {"Hero_01": str(second)})
    assert first.read_bytes() == before
    assert revision not in {first, second}
    data = service.load(revision)
    assert data["members"] == service.load(second)["members"]
    assert data["previous"]["path"] == first.as_posix()


def test_empty_and_incompatible_revision_rejected(project):
    shots, identity, service = project
    first = service.publish_build(identity, build(project))
    with pytest.raises(ValueError, match="at least one"):
        service.revise_members(identity, first, {"Hero_01": None})
    with pytest.raises(ValueError, match="Draft members"):
        service.revise_members(identity, first, {})
    different = service.load(first)
    different["fps"] = 30
    other = service._commit(identity, different)
    with pytest.raises(ValueError, match="timing/profile"):
        service.revise_members(identity, first, {"Hero_01": str(other)})


def test_explicit_exclusion_is_recorded_without_changing_cast(project):
    shots, identity, service = project
    cast_path = shots.paths.artifact_file(shots.shot_root(identity), "cast.json")
    cast = read_json(cast_path)
    cast["cast"]["Hero_02"] = deepcopy(cast["cast"]["Hero_01"])
    write_json(cast_path, cast)
    manifest = build(project)
    data = read_json(manifest)
    with pytest.raises(ValueError, match="cast mismatch"):
        service.publish_build(identity, manifest)
    data["excluded_members"] = ["Hero_02"]
    write_json(manifest, data)
    published = service.publish_build(identity, manifest)
    assert service.load(published)["excluded_members"] == ["Hero_02"]
    assert read_json(cast_path) == cast


def test_snapshot_version_combo_keeps_draft_on_refresh(project):
    from types import SimpleNamespace
    from PySide6 import QtWidgets
    from smartlib.apps.review_build_manager import composition_draft_ui as editor
    shots, identity, service = project
    first = service.publish_build(identity, build(project))
    second = service.publish_build(identity, build(project))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    versions = QtWidgets.QComboBox()
    versions.addItem("v001", str(first))
    window = SimpleNamespace(
        composition_table=QtWidgets.QTableWidget(0, 7), composition_versions=versions,
        composition_open_btn=QtWidgets.QPushButton(), composition_save_btn=QtWidgets.QPushButton(),
        composition_build_btn=QtWidgets.QPushButton(), composition_state=QtWidgets.QLabel(),
        _composition_identity=lambda: identity, _composition_service=lambda: service,
        _selected_review_source=lambda: None)
    editor.render(window)
    assert not window.composition_save_btn.isEnabled()
    combo = window.composition_table.cellWidget(0, 5)
    combo.setCurrentIndex(combo.findData(str(second)))
    assert window.composition_save_btn.isEnabled()
    assert window.composition_table.item(0, 6).text() == "CHANGED"
    editor.render(window)
    assert window.composition_table.cellWidget(0, 5).currentData() == str(second)
    assert window.composition_save_btn.isEnabled()
