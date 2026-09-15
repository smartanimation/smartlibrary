import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets
from scripts import config_creator


@pytest.fixture
def editor(monkeypatch, tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(config_creator, "DEFAULT_DIR", str(tmp_path / "defaults"))
    monkeypatch.setattr(config_creator, "PROJECTS_ROOT", str(tmp_path))
    window = config_creator.ConfigCreatorApp.__new__(config_creator.ConfigCreatorApp)
    QtWidgets.QMainWindow.__init__(window)
    window.setCentralWidget(window.setup_pipeline_profile_tab())
    window.context_configs = {}
    window.context_active_versions = {"asset": "v001"}
    window.asset_subset_catalog = {}
    window.workspace_representation_combo = QtWidgets.QComboBox()
    window.workspace_representation_combo.addItem("Maya", "maya")
    yield window
    window.close()
    window.deleteLater()


def test_usd_settings_save_reload_and_preserve_other_settings(editor, tmp_path):
    project = tmp_path / "USD_TEST"
    config_creator.save_yml(project / "project_settings.yml", {
        "unrelated": {"keep": True},
        "usd": {"meters_per_unit": 0.01, "up_axis": "Y", "other_option": "keep"},
    })
    editor.usd_meters_per_unit_edit.setText("1")
    editor.usd_up_axis_combo.setCurrentText("Z")
    editor._save_context_configs(str(project))
    stored = config_creator.load_yml(project / "project_settings.yml")
    assert stored["usd"] == {"meters_per_unit": 1.0, "up_axis": "Z", "other_option": "keep"}
    assert stored["unrelated"] == {"keep": True}
    editor.usd_meters_per_unit_edit.setText("0.001")
    editor._load_pipeline_profile_editor("USD_TEST")
    assert editor._usd_config_from_ui() == {"meters_per_unit": 1.0, "up_axis": "Z"}


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "abc", ""])
def test_invalid_units_do_not_write_files(editor, tmp_path, value):
    editor.usd_meters_per_unit_edit.setText(value)
    target = tmp_path / "invalid"
    with pytest.raises(ValueError, match="positive finite"):
        editor._save_context_configs(str(target))
    assert not target.exists()


def test_missing_usd_settings_use_cm_y_and_project_switch_resets(editor, tmp_path):
    config_creator.save_yml(tmp_path / "OLD" / "project_settings.yml", {})
    editor.usd_meters_per_unit_edit.setText("1")
    editor.usd_up_axis_combo.setCurrentText("Z")
    editor._load_pipeline_profile_editor("OLD")
    assert editor._usd_config_from_ui() == {"meters_per_unit": 0.01, "up_axis": "Y"}


def test_invalid_loaded_axis_is_not_silently_replaced(editor, tmp_path):
    config_creator.save_yml(tmp_path / "BAD" / "project_settings.yml", {
        "usd": {"meters_per_unit": 0.01, "up_axis": "X"},
    })
    editor._load_pipeline_profile_editor("BAD")
    with pytest.raises(ValueError, match="Up Axis"):
        editor._usd_config_from_ui()
