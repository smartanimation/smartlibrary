"""Headless Maya/Qt smoke test with a fake Shot service; no project data writes."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    # Maya standalone otherwise creates a QGuiApplication, which cannot own
    # QWidget instances. Create QApplication before initializing Maya.
    try:
        from PySide6 import QtWidgets, QtGui, QtTest
    except ImportError:
        from PySide2 import QtWidgets, QtGui, QtTest
    app = QtWidgets.QApplication([])
    QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setFont(QtGui.QFont("Segoe UI", 10))
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from smartlib.apps.smart_camera_playblast import ui

    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True)
    camera, _ = cmds.camera(name="shotCam_PRIMARY")
    service = SimpleNamespace(shot_departments=["anim"], list_shots=lambda: [], shot_identity_from_path=lambda path: None)
    errors = []
    temporary = tempfile.TemporaryDirectory(prefix='smart-camera-ui-native-')
    with patch("smartlib.apps.shot_manager.ShotManagerService", return_value=service), \
         patch("smartlib.dcc.maya.review_playblast.load_scene_playblast_settings", return_value={}), \
         patch.object(ui.QtWidgets.QMessageBox, "critical", side_effect=lambda *args: errors.append(args[-1])):
        window = ui.SmartCameraPlayblastWindow(config_dir=Path(__file__).resolve().parents[2] / "config" / "default")
        window.primary_combo.setCurrentIndex(window.primary_combo.findData(cmds.ls(camera, long=True)[0]))
        window.reference_width.setValue(1920)
        window.reference_height.setValue(1080)
        window.layer_combo.addItems(["CHA", "BGA"])
        for layer, width, height in [("CHA", 2048, 858), ("BGA", 1920, 1080)]:
            window._append_row(enabled=True, camera=camera, layer=layer, start=1001, end=1003,
                               width=width, height=height, version=1, take=1, mode="Custom")
        window.table.setCurrentCell(0, 1)
        window._load_properties()
        assert window.generate_cameras(), errors
        assert not errors, errors
        assert window._row(0)["camera"] == camera
        assert window._row(1)["camera"] == camera
        window.fit_combo.setCurrentIndex(window.fit_combo.findData("scale"))
        window.expansion_spin.setValue(1.1)
        assert window.generate_cameras(), errors
        assert window._row(0)['width'] == 2112 and window._row(0)['height'] == 1188
        window.table.setCurrentCell(1, 1)
        assert window.fit_combo.currentData() == "shared"
        window.table.setCurrentCell(0, 1)
        assert window.fit_combo.currentData() == "scale"
        assert window.playblast_button.text() == "Playblast Image Sequence"
        assert window.preset_combo.count() > 0
        window.show()
        app.processEvents()
        assert window.width() == 680, window.width()
        assert window.height() == 620, window.height()
        from smartlib.apps.smart_camera_playblast.layer_list import LayerCardDelegate
        assert isinstance(window.layer_list, ui.QtWidgets.QListView)
        assert window.layer_list.sizeHintForRow(0) == 58
        assert window.version_spin.width() <= 100
        assert window.width_spin.height() <= 26
        assert abs(
            window.width_spin.mapTo(window, ui.QtCore.QPoint()).y()
            - window.fit_combo.mapTo(window, ui.QtCore.QPoint()).y()
        ) < 90
        assert window.version_spin.mapTo(window, ui.QtCore.QPoint()).y() == window.take_spin.mapTo(window, ui.QtCore.QPoint()).y()
        window.version_spin.setValue(4)
        window.take_spin.setValue(3)
        assert window._row(0)["version"] == 4
        assert window._row(0)["take"] == 3
        assert not window.table.isVisible()
        assert not window.option_button.isVisible()
        assert not window.all_button.isVisible()
        first = window.layer_list.model().index(0, 0)
        assert LayerCardDelegate.is_checked(first)
        position = LayerCardDelegate.check_rect(window.layer_list.visualRect(first)).center()
        QtTest.QTest.mouseClick(window.layer_list.viewport(), ui.QtCore.Qt.LeftButton, pos=position)
        assert not window._row(0)["enabled"]
        QtTest.QTest.mouseClick(window.layer_list.viewport(), ui.QtCore.Qt.LeftButton, pos=position)
        assert window._row(0)["enabled"]
        window.layer_list.setCurrentIndex(window.layer_list.model().index(1, 0))
        assert window.table.currentRow() == 1
        assert window.layer_combo.currentText() == "BGA"
        window.layer_list.setCurrentIndex(first)
        before_rows = [window._row(i) for i in range(2)]
        viewport = window.layer_list.viewport()
        start = window.layer_list.visualRect(first).center()
        end = window.layer_list.visualRect(window.layer_list.model().index(1, 0)).center()
        QtTest.QTest.mousePress(viewport, ui.QtCore.Qt.LeftButton, pos=start)
        move = QtGui.QMouseEvent(ui.QtCore.QEvent.MouseMove, ui.QtCore.QPointF(end),
                                ui.QtCore.Qt.NoButton, ui.QtCore.Qt.LeftButton, ui.QtCore.Qt.NoModifier)
        app.sendEvent(viewport, move)
        QtTest.QTest.mouseRelease(viewport, ui.QtCore.Qt.LeftButton, pos=end)
        assert window._row(0) == before_rows[1]
        assert window._row(1) == before_rows[0]
        assert window.table.currentRow() == 1
        assert window.layer_list.currentIndex().row() == 1
        snapshots = window.table._snapshot_rows()
        window.table._restore_drag_snapshot(list(reversed(snapshots)), 0)
        app.processEvents()
        if len(sys.argv) > 1:
            window.resize(700, 930)
            app.processEvents()
            assert window.grab().save(sys.argv[1])
        window.resize(700, 930)
        app.processEvents()
        assert window.width() <= 700, window.minimumSizeHint().width()
        assert window.layer_list.width() >= 180
        assert window.generate_camera_button.isVisible()
        assert window.publish_camera_button.isVisible()
        assert window.publish_camera_button.objectName() == "publishCameraPackage"
        # Exercise the actual Publish button handler, but never write a project publish.
        from smartlib.apps.shot_manager import ShotIdentity
        from smartlib.dcc.maya import camera_publish, camera_native
        assert window.generate_cameras(), errors
        window.identity = ShotIdentity('ep001', 'sq010', 'sh0010')
        published_payloads = []
        def publish(identity, payload, **kwargs):
            assert kwargs['data_type'] == 'camera'
            assert kwargs['target'] == 'main' and kwargs['subset'] == 'main'
            published_payloads.append(payload)
            payload['files'] = kwargs['native_exporter'](Path(temporary.name))
            return Path(temporary.name) / 'camera.json'
        service.publish_shot_scene_snapshot = publish
        with patch.object(ui.QtWidgets.QDialog, 'exec_', return_value=ui.QtWidgets.QDialog.Accepted), \
             patch.object(ui.QtWidgets.QMessageBox, 'information'):
            window.publish_camera_package()
        assert len(published_payloads) == 1, errors
        assert published_payloads[0]['schema'] == camera_native.SCHEMA
        window._suppress_scene_save = True
        window.close()
        app.processEvents()
    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True)
    provenance = str(Path(temporary.name) / 'camera.json')
    camera_publish.restore_package(published_payloads[0], cmds=cmds, provenance=provenance)
    cmds.parent('smartCameraPublish', cmds.group(empty=True, name='new_build_hierarchy'))
    # No Review Spec or DisplayLayers here: explicit package rows still appear,
    # without creating any fake material membership.
    with patch('smartlib.apps.shot_manager.ShotManagerService', return_value=service):
        restored = ui.SmartCameraPlayblastWindow(config_dir=Path(__file__).resolve().parents[2] / 'config' / 'default')
        assert restored.table.rowCount() == 2, restored.table.rowCount()
        assert restored.primary_combo.currentData().startswith('|new_build_hierarchy|')
        assert restored.auto_update_cameras.isChecked()
        assert [restored._row(i)['camera'] for i in range(2)] == ['smartCam_CHA', 'smartPrimary:' + camera]
        assert restored._row(0)['version'] == 4
        assert restored._row(0)['take'] == 3
        assert restored._camera_prefs['layer_rules']['CHA'] == {'mode': 'scale', 'scale': 1.1}
        assert restored._scene_settings()['camera_package_source'] == provenance
        restored._suppress_scene_save = True
        restored.close()
    print("UI smoke passed: construction, existing fields, generation, camera assignment, per-layer policy, Qt rendering")
    temporary.cleanup()
    maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
