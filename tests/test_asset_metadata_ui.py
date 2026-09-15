from __future__ import annotations

import json
import os
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtGui, QtWidgets

from scripts.asset_manager import AssetManager
from smartlib.apps.asset_manager.metadata_ui import AssetMetadataPanel
from test_smart_casting_paths import write_config, write_json


@pytest.fixture
def setup(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    font_id = QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QtGui.QFont(families[0], 9))
    config = tmp_path / "config"
    write_config(config, tmp_path / "project")
    manager = AssetManager(config)
    assets = [manager.get_asset("character", "main", name) for name in ("JIN", "DLI")]
    for asset in assets:
        write_json(manager.asset_metadata_paths(asset)[0], {
            "asset": asset.name, "category": asset.category, "group": asset.group,
            "description": "Character", "status": "Wait",
            "metadata": {"enabled": True, "notes": "original", "settings": {"scale": 2}},
            "unrelated": {"keep": [1, 2]},
        })
        write_json(asset.root / "default" / "variant.json", {"variant": "default"})
    panel = AssetMetadataPanel(manager)
    panel.set_asset(assets[0])
    yield app, manager, assets, panel
    panel.deleteLater()
    app.processEvents()


def test_metadata_save_preserves_types_and_unrelated_fields(setup):
    _, manager, assets, panel = setup
    path = manager.asset_metadata_paths(assets[0])[0]
    original = path.read_bytes()
    panel.table.item(1, 1).setText("edited")
    panel.set_asset(assets[1])
    assert panel.table.item(1, 1).text() == "original"
    panel.set_asset(assets[0])
    assert panel.table.item(1, 1).text() == "edited"
    assert path.read_bytes() == original
    panel.save()
    data = json.loads(path.read_text())
    assert data["metadata"] == {"enabled": True, "notes": "edited", "settings": {"scale": 2}}
    assert data["unrelated"] == {"keep": [1, 2]}
    assert not panel.drafts


def test_invalid_metadata_keeps_draft_and_file(setup, monkeypatch):
    _, manager, assets, panel = setup
    path = manager.asset_metadata_paths(assets[0])[0]
    before = path.read_bytes()
    panel.table.item(1, 0).setText("enabled")
    error = Mock()
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", error)
    panel.save()
    error.assert_called_once()
    assert panel.drafts
    assert path.read_bytes() == before


def test_remove_metadata_and_failed_save_keep_changes(setup, monkeypatch):
    _, manager, assets, panel = setup
    panel.table.selectRow(1)
    panel._remove()
    failure = Mock(side_effect=OSError("read only"))
    with monkeypatch.context() as patch:
        patch.setattr(manager, "save_custom_metadata", failure)
        patch.setattr(QtWidgets.QMessageBox, "critical", Mock())
        panel.save()
    assert panel.drafts
    assert "notes" in manager.load_asset_metadata(assets[0])["metadata"]
    panel.save()
    assert "notes" not in manager.load_asset_metadata(assets[0])["metadata"]


def test_uninitialized_asset_cannot_be_edited(setup):
    _, manager, _, panel = setup
    asset = manager.get_asset("character", "main", "Uninitialized")
    panel.set_asset(asset)
    assert not panel.save_btn.isEnabled()
    assert not panel.table.isEnabled()
    with pytest.raises(ValueError, match="Initialize"):
        manager.save_custom_metadata(asset, {"note": "test"})
    assert not asset.root.exists()


def test_thumbnail_is_saved_only_on_save(setup, tmp_path, monkeypatch):
    _, manager, assets, panel = setup
    image = QtGui.QImage(32, 32, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor("green"))
    source = tmp_path / "source.png"
    image.save(str(source))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    panel._choose_thumbnail()
    assert "thumbnail" not in manager.load_asset_metadata(assets[0])
    panel.set_asset(assets[1])
    panel.set_asset(assets[0])
    panel.save()
    assert manager.find_asset_thumbnail(assets[0]).read_bytes() == source.read_bytes()


@pytest.mark.parametrize("saved_layout", ["new", "legacy", "collapsed"])
def test_asset_manager_browser_panel_and_casting_controls(setup, tmp_path, monkeypatch, saved_layout):
    app, manager, assets, _ = setup
    from scripts.asset_manager_ui import AssetManagerWindow
    from smartlib.apps.smart_casting.ui import SmartCastingWindow

    settings = QtCore.QSettings(str(tmp_path / "ui.ini"), QtCore.QSettings.IniFormat)
    if saved_layout != "new":
        old_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        for _ in range(2 if saved_layout == "legacy" else 3):
            old_splitter.addWidget(QtWidgets.QWidget())
        old_splitter.resize(1180, 650)
        old_splitter.setSizes([220, 960] if saved_layout == "legacy" else [220, 960, 0])
        settings.beginGroup(AssetManagerWindow.SETTINGS_STATE_GROUP)
        settings.setValue("asset_browser_splitter", old_splitter.saveState())
        settings.endGroup()
        old_splitter.deleteLater()
    monkeypatch.setattr(AssetManagerWindow, "_window_settings", lambda self: settings)
    manager_window = AssetManagerWindow(manager)
    casting_window = SmartCastingWindow(manager.config_dir)
    try:
        manager_window.resize(1280, 760)
        manager_window.show()
        app.processEvents()
        assert manager_window.metadata_panel.isVisible()
        assert manager_window.asset_browser_splitter.sizes()[2] >= 250
        assert manager_window.metadata_panel.asset is not None
        assert manager_window.metadata_panel.table.rowCount() == 3
        manager_window.grab().save(str(tmp_path / "asset-manager-metadata-card.png"))
        manager_window._set_asset_table_view()
        app.processEvents()
        assert manager_window.metadata_panel.asset is not None
        assert not hasattr(casting_window, "metadata_table")
        assert not hasattr(casting_window, "set_thumbnail_btn")
        assert hasattr(casting_window, "asset_cast_info_table")
        manager_window.grab().save(str(tmp_path / "asset-manager-metadata.png"))
    finally:
        manager_window.close()
        casting_window.close()
        manager_window.deleteLater()
        casting_window.deleteLater()
        app.processEvents()
