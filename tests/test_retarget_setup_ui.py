from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtWidgets

from smartlib.apps.retarget_setup import window as ui
from smartlib.apps.retarget_setup.service import RetargetService
from smartlib.core.path_resolver import AssetIdentity, ProjectPaths


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    paths = ProjectPaths(tmp_path)
    assets = []
    for name in ("DLI", "SECOND"):
        identity = AssetIdentity("CH", "main", name)
        asset = SimpleNamespace(category="CH", group="main", name=name, root=paths.asset_root(identity))
        assets.append(asset)
        service = RetargetService(paths, identity)
        service.save_draft({"asset": name, "time_unit": "film", "source_skeleton": {"namespace_agnostic_prefix": "MC_"}, "transfer_nodes": {"controls": ["CTL_allLocal"]}, "pole_vectors": {"left_arm": {"enabled": True, "distance_ratio": 1.0}}})
    manager = SimpleNamespace(paths=paths, config_dir=tmp_path, list_assets=lambda: assets)
    monkeypatch.setattr(ui, "ProjectConfig", lambda path: None)
    widget = ui.RetargetWindow(manager)
    widget.show()
    app.processEvents()
    yield widget, app
    widget.dirty = False
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_basic_setting_changes_profile_and_invalidates_test(window):
    widget, app = window
    widget.data_path = "saved.json"
    widget.job = {"status": "passed", "reviewed": True}
    widget.update_publish()
    assert widget.publish_button.isEnabled()
    widget.poles["left_arm"][1].setValue(1.25)
    assert widget.current_profile()["pole_vectors"]["left_arm"]["distance_ratio"] == 1.25
    assert widget.dirty and widget.data_path is None and widget.job is None
    assert not widget.publish_button.isEnabled()


def test_character_switch_clears_test_and_data(window):
    widget, app = window
    widget.job = {"status": "passed", "reviewed": True}
    widget.data_path = "previous.json"
    widget.characters.setCurrentIndex(1)
    assert widget.current_profile()["asset"] == "SECOND"
    assert widget.data_path is None and widget.job is None
    assert not widget.publish_button.isEnabled()


def test_running_disables_all_test_input_changes(window):
    widget, app = window
    widget.set_running(True)
    assert not widget.characters.isEnabled()
    assert not widget.motion.isEnabled()
    assert not widget.motion_browse_button.isEnabled()
    assert not widget.setup_page.isEnabled()
    assert widget.cancel_button.isEnabled()
    widget.set_running(False)


def test_draft_save_does_not_publish_and_survives_reload(window):
    widget, app = window
    widget.poles["left_arm"][1].setValue(1.5)
    widget.save_draft()
    assert not widget.dirty and not widget.publish_button.isEnabled()
    widget.characters.setCurrentIndex(1)
    widget.characters.setCurrentIndex(0)
    assert widget.current_profile()["pole_vectors"]["left_arm"]["distance_ratio"] == 1.5


def test_advanced_settings_retain_basic_controls(window):
    widget, app = window
    widget.editor.setPlainText('{"pole_vectors":{"left_arm":{"enabled":false,"distance_ratio":2.0}}}')
    assert not widget.poles["left_arm"][0].isChecked()
    assert widget.poles["left_arm"][1].value() == 2.0


def test_maya_failed_start_restores_controls(window, monkeypatch, tmp_path):
    from PySide6 import QtTest
    widget, app = window
    for key, edit in widget.rigs.items():
        path = tmp_path / (key + ".mb")
        path.write_bytes(b"rig")
        edit.setText(str(path))
    motion = tmp_path / "motion.fbx"
    motion.write_bytes(b"motion")
    widget.motion.setText(str(motion))
    monkeypatch.setattr(ui, "resolve_mayapy", lambda cfg: tmp_path / "missing-mayapy.exe")
    monkeypatch.setattr(ui, "process_environment", lambda cfg: ({}, {}))
    widget.run_test()
    for _ in range(100):
        app.processEvents()
        if widget.process is None:
            break
        QtTest.QTest.qWait(10)
    assert widget.process is None
    assert widget.job["status"] == "failed"
    assert widget.run_button.isEnabled() and widget.characters.isEnabled()
    assert not widget.review.isEnabled() and not widget.publish_button.isEnabled()


def test_transfer_node_exclusion_updates_profile(window):
    widget, app = window
    from PySide6 import QtCore
    group = widget.nodes.topLevelItem(0)
    node = group.child(0)
    node.setCheckState(1, QtCore.Qt.Unchecked)
    assert widget.current_profile()["excluded_transfer_nodes"] == ["CTL_allLocal"]
    assert widget.dirty and not widget.publish_button.isEnabled()
