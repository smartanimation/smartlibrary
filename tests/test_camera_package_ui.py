"""Actual Qt tree/combo interaction against a disposable project, no Maya."""
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow, QtWidgets, QtCore
from smartlib.apps.review_build_manager.service import ReviewBuildManagerService
from smartlib.apps.shot_manager import ShotIdentity
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.camera_package import SCHEMA
from test_shot_construct import write_config


def test_data_tree_and_build_version_combo(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    config = tmp_path / 'config'
    write_config(config, tmp_path / 'project')
    manager = ReviewBuildManagerService(ProjectConfig(config))
    identity = ShotIdentity('ep001', 'sq010', 'sh0010')
    payload = dict(schema=SCHEMA, reference_resolution=[1920, 1080],
                   cameras=[dict(role='primary', name='creativeCam')],
                   rows=[dict(layer='CHA', camera='smartCam_CHA', width=2048, height=858,
                              start=1001, end=1100, version=2, take=3)])
    first = manager.shots.publish_shot_scene_snapshot(identity, payload, data_type='camera')
    second = manager.shots.publish_shot_scene_snapshot(identity, payload, data_type='camera')
    # Extract the real Data-tab methods without starting Shot Manager's unrelated
    # browser/services, matching the repository's existing UI harness pattern.
    path = Path(__file__).parents[1] / 'scripts' / 'shot_manager_ui.py'
    cls = next(n for n in ast.parse(path.read_text(encoding='utf-8-sig')).body
               if isinstance(n, ast.ClassDef) and n.name == 'ShotManagerWindow')
    scope = dict(Path=Path, json=json, QtWidgets=QtWidgets, QtCore=QtCore)
    names = {'populate_data_tree', '_data_version_labels', '_data_version_is_published_animation',
             '_show_camera_package_details'}
    exec(compile(ast.Module(body=[n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names],
                            type_ignores=[]), str(path), 'exec'), scope)
    class DataHarness(QtWidgets.QWidget):
        populate_data_tree = scope['populate_data_tree']
        _data_version_labels = scope['_data_version_labels']
        _data_version_is_published_animation = scope['_data_version_is_published_animation']
        _show_camera_package_details = scope['_show_camera_package_details']
    data = DataHarness()
    data.service = manager.shots
    data.active_shot_identity = identity
    data.active_sequence_identity = None
    data.current_identity = lambda: identity
    data.current_sequence_identity = lambda: None
    data.shot_data_tree = QtWidgets.QTreeWidget(data)
    data.shot_data_tree.setColumnCount(4)
    data.populate_data_tree()
    camera = data.shot_data_tree.topLevelItem(1)
    group = camera.child(0)
    assert group.childCount() == 2
    version = group.child(0)
    assert 'Camera Package' in version.text(1)
    assert 'smartCam_CHA' in version.toolTip(0)
    assert version.data(0, QtCore.Qt.UserRole) == str(second)
    dialogs = []
    def inspect_dialog():
        dialog = next(w for w in app.topLevelWidgets() if isinstance(w, QtWidgets.QDialog) and w.isVisible())
        dialogs.append(dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText())
        dialog.reject()
    QtCore.QTimer.singleShot(0, inspect_dialog)
    data._show_camera_package_details(version)
    assert 'Primary: creativeCam' in dialogs[0] and str(second) in dialogs[0]

    class BuildHarness(ReviewBuildManagerWindow):
        def __init__(self):
            QtWidgets.QMainWindow.__init__(self)
            self.service = manager
            self.status_row = SimpleNamespace(identity=identity)
            self.build_content_settings = {}
            self._planned_snapshots = {}
            self.build_contents_table = QtWidgets.QTableWidget(0, 10, self)
            self.build_contents_group = QtWidgets.QGroupBox(self)
            self.contents_summary_label = QtWidgets.QLabel(self)
            self.mode_combo = QtWidgets.QComboBox(self)
            self.mode_combo.addItem('WORK STAGE')
            self.input_context_combo = QtWidgets.QComboBox(self)
            self.input_context_combo.addItem('WORK')
            self.input_representation_combo = QtWidgets.QComboBox(self)
            self.input_representation_combo.addItem('project', 'project')
        def _selected_status(self):
            return self.status_row
        def closeEvent(self, event):
            event.accept()  # Never write desktop QSettings from this harness.
        def _planned_snapshot_key(self, identity):
            return identity.shot
        def _planned_controls_changed(self):
            self._planned_snapshots[identity.shot] = dict(inputs=[dict(type=row['type'], name=row['cast_key'],
                enabled=row['enabled'], version=row.get('build_version') or row['official'],
                path=row['component']['path'])
                for row in self.current_build_content_rows])
        def _populate_planned_snapshot(self, status):
            pass
    build = BuildHarness()
    build._populate_build_contents(build.status_row)
    index = next(i for i, row in enumerate(build.current_build_content_rows) if row.get('camera_versions'))
    combo = build.build_contents_table.cellWidget(index, 6)
    assert combo.currentData() == str(second)
    old_index = combo.findData(str(first))
    combo.setCurrentIndex(old_index)
    combo.activated.emit(old_index)
    assert build.build_contents_table.cellWidget(index, 6).currentData() == str(first)
    snapshot = build._planned_snapshots[identity.shot]
    construct = build._apply_planned_snapshot_to_construct(manager.shots.resolved_construct(identity), snapshot)
    selected = next(c for c in construct['components'] if (c.get('source') or {}).get('camera_package'))
    assert selected['path'] == str(first) and selected['version'] == 'v001'
    # A fresh plan defaults to Latest; the submitted Review remains separate.
    reopened = BuildHarness()
    reopened._populate_build_contents(reopened.status_row)
    assert reopened.build_contents_table.cellWidget(index, 6).currentData() == str(second)
    stale = BuildHarness()
    stale._planned_snapshots[identity.shot] = {"inputs": [{
        "type": "camera",
        "name": snapshot["inputs"][index]["name"],
        "enabled": True,
        "version": "v999",
        "path": str(second.parent.parent / "v999" / "camera.json"),
    }]}
    stale._populate_build_contents(stale.status_row)
    stale_camera = stale.current_build_content_rows[index]
    assert stale_camera["build_version"] == second.parent.name
    assert stale_camera["component"]["path"] == str(second)
    assert "using Latest" in stale_camera["note"]
    for widget in (data, build, reopened, stale):
        widget.close()
