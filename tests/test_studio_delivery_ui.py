import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
from PySide6 import QtWidgets, QtTest
from test_asset_studio_delivery import delivery, passed_job
from smartlib.apps.asset_manager import studio_delivery_ui as ui
from dataclasses import replace


@pytest.fixture
def tab(delivery):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    service, identity = delivery
    widget = ui.StudioDeliveryTab(service.config.config_dir)
    widget.set_identity(identity)
    widget.show()
    app.processEvents()
    yield widget, app, service, identity
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_ui_finalize_history_and_selection_reset(tab):
    widget, app, service, identity = tab
    passed_job(service, identity)
    widget.refresh()
    assert widget.generate.isEnabled() and widget.finalize_button.isEnabled()
    widget.finalize()
    assert widget.releases.topLevelItem(0).text(0) == "v001"
    assert not widget.finalize_button.isEnabled()
    widget.releases.setCurrentItem(widget.releases.topLevelItem(0))
    widget.copy_path()
    assert QtWidgets.QApplication.clipboard().text().endswith("Alice_v001.fbx")
    widget.set_identity(replace(identity, name="Other"))
    assert not widget.generate.isEnabled()
    assert widget.releases.topLevelItemCount() == 0
    assert not widget.sent_button.isEnabled()


def test_ui_failed_worker_launch_does_not_release(tab, monkeypatch, tmp_path):
    widget, app, service, identity = tab
    monkeypatch.setattr(ui, "resolve_mayapy", lambda config: tmp_path / "missing.exe")
    monkeypatch.setattr(ui, "process_environment", lambda config: ({}, {}))
    widget.generate_fbx()
    for _ in range(100):
        app.processEvents()
        if widget.process is None:
            break
        QtTest.QTest.qWait(10)
    assert widget.process is None
    assert widget.generate.isEnabled() and not widget.finalize_button.isEnabled()
    assert not service.releases(identity)


def test_asset_manager_contains_delivery_tab(delivery):
    from scripts.asset_manager import AssetManager
    from scripts.asset_manager_ui import AssetManagerWindow
    service, identity = delivery
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = AssetManagerWindow(AssetManager(service.config.config_dir))
    assert "Studio Delivery" in [window.detail_tabs.tabText(i) for i in range(window.detail_tabs.count())]
    window.close()
    window.deleteLater()
    app.processEvents()
