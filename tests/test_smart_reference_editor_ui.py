import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from types import SimpleNamespace
import pytest
pytest.importorskip('PySide6')
from PySide6 import QtCore, QtGui, QtWidgets
from smartlib.apps.shot_manager import ShotIdentity, SequenceIdentity
from smartlib.apps.smart_reference_editor.ui import ReferenceEditorWindow
from smartlib.apps.smart_reference_editor.service import Reference, Target
from test_smart_reference_editor import FakeMaya, item


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    font = Path('C:/Windows/Fonts/segoeui.ttf')
    if font.is_file():
        QtGui.QFontDatabase.addApplicationFont(str(font))
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'templates_base.yml').write_text('anchors:\n  project_name: TEST\n  project_root: "' + tmp_path.as_posix() + '"\n')
    old, new = tmp_path / 'old.ma', tmp_path / 'new.ma'
    old.write_text('// old')
    new.write_text('// new')
    seq, shot = SequenceIdentity('ep01', 'sq010'), ShotIdentity('ep01', 'sq010', 'sh020')
    service = SimpleNamespace(
        cast_target_choices=lambda row: {'Resolved': [{'version': 'Current', 'path': row.publish_path}]},
        scene_identity=lambda path: shot,
        casting=SimpleNamespace(sequences=lambda: [seq], shots_for_sequence=lambda *args: [shot]),
        preview=lambda *args: [item(new), item(new, 'prop')],
        targets=lambda *args: [Target('Hero / rig / v014', str(new))],
    )
    maya = FakeMaya([Reference('heroRN', 'hero', str(old))])
    errors = []
    monkeypatch.setattr(QtWidgets.QMessageBox, 'warning', lambda *args: errors.append(args[-1]))
    widget = ReferenceEditorWindow(str(config), service=service, cmds=maya)
    widget._test_errors = errors
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_cast_selection_and_context_invalidation(window):
    window.refresh_cast()
    assert not window._test_errors
    assert [d.status for d in window.differences] == ['Changed', 'Added']
    assert [op.namespace for op in window.operations()] == ['prop']
    window.cast_table.item(0, 0).setCheckState(QtCore.Qt.Checked)
    assert len(window.operations()) == 2
    window.select_added()
    assert len(window.operations()) == 1
    window.scope.setCurrentText('Sequence')
    assert window.scanned is None and not window.apply_button.isEnabled()
    assert not window.shot.isEnabled()


def test_relink_requires_mapping_and_shows_full_paths(window):
    window.tabs.setCurrentIndex(1)
    window.refresh_relink()
    assert not window._test_errors
    window.relink_table.item(0, 0).setCheckState(QtCore.Qt.Checked)
    with pytest.raises(RuntimeError, match='Choose a Production'):
        window.operations()
    window.relink_table.cellWidget(0, 3).setCurrentIndex(1)
    window.preview()
    assert 'new.ma' in window.detail.toPlainText()
    assert 'old.ma' in window.detail.toPlainText()
    window.show()
    QtWidgets.QApplication.processEvents()
    folder = Path('.tmp/reference-editor-ui')
    folder.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(folder / 'production-relink.png'))


def test_apply_selected_rechecks_casting(window, monkeypatch):
    window.refresh_cast()
    window.service.preview = lambda *args: []
    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec_', lambda self: QtWidgets.QMessageBox.Apply)
    window.apply_selected()
    assert window.cmds.calls == []
    assert any('Casting changed' in error for error in window._test_errors)


def test_apply_selected_uses_checked_rows(window, monkeypatch):
    window.refresh_cast()
    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec_', lambda self: QtWidgets.QMessageBox.Apply)
    window.apply_selected()
    assert not window._test_errors
    assert len(window.cmds.calls) == 1
    assert window.cmds.calls[0][1]['namespace'] == 'prop'
    assert window.scanned is not None
    assert next(d for d in window.differences if d.namespace == 'prop').status == 'In sync'
    assert 'add: prop' in window.detail.toPlainText()


def test_cast_assets_visible_by_default_and_cameras_hidden(window):
    path = next(iter(window.cmds.refs.values())).path
    window.service.preview = lambda *args: [item(path, 'hero', asset='AccidentCar', variant='default')]
    window.cmds.refs['cameraRN'] = Reference('cameraRN', 'c001', path)
    window.refresh_cast()
    assert not window._test_errors
    assert not window.changes_only.isChecked()
    assert window.cast_table.item(0, 1).text() == 'In sync'
    assert 'AccidentCar / default / hero' == window.cast_table.item(0, 2).text()
    assert not window.cast_table.isRowHidden(0)
    assert window.cast_table.isRowHidden(1)
    assert 'Casting assets: 1' in window.status.text()
    assert 'Not in casting' not in window.status.text()
    window.changes_only.setChecked(True)
    assert window.cast_table.isRowHidden(0)
    assert window.cast_table.isRowHidden(1)
    window.show_other_references.setChecked(True)
    assert not window.cast_table.isRowHidden(1)
    window.changes_only.setChecked(False)
    assert not window.cast_table.isRowHidden(0)


def test_current_sequence_scene_selects_sequence_casting(window):
    window.service.scene_identity = lambda path: SequenceIdentity('ep01', 'sq010')
    window.select_scene_context()
    assert window.scope.currentText() == 'Sequence'
    assert window.identity() == SequenceIdentity('ep01', 'sq010')
    assert window.department.currentText() == 'layout'
    assert not window.shot.isEnabled()
    assert window.scanned is not None
    assert window.cast_table.rowCount() == 2


def test_unknown_scene_does_not_default_to_first_shot(window):
    window.service.scene_identity = lambda path: None
    window.select_scene_context()
    with pytest.raises(RuntimeError, match='Select an existing'):
        window.identity()


def test_current_scene_identity_survives_module_reload(window):
    from dataclasses import make_dataclass
    Identity = make_dataclass('SequenceIdentity', [('episode', str), ('sequence', str)],
                              namespace={'code': property(lambda self: self.episode + '_' + self.sequence)})
    window.service.scene_identity = lambda path: Identity('ep01', 'sq010')
    window.select_scene_context()
    assert window.scope.currentText() == 'Sequence'
    assert window.identity().sequence == 'sq010'
    assert not window.shot.isEnabled()


def test_current_scene_and_apply_refresh_relink(window, monkeypatch):
    window.tabs.setCurrentIndex(1)
    window.select_scene_context()
    assert window.scanned is not None
    assert window.relink_table.rowCount() == 1
    window.relink_table.item(0, 0).setCheckState(QtCore.Qt.Checked)
    window.relink_table.cellWidget(0, 3).setCurrentIndex(1)
    target = window.relink_table.cellWidget(0, 3).currentData().path
    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec_', lambda self: QtWidgets.QMessageBox.Apply)
    window.apply_selected()
    assert not window._test_errors
    assert window.references[0].path == target
    assert window.relink_table.item(0, 0).checkState() == QtCore.Qt.Unchecked
    assert 'replace: hero' in window.detail.toPlainText()


def test_unreadable_reference_does_not_block_ui(window):
    window.cmds.refs['sotaiMRN2'] = Reference('sotaiMRN2', 'sotaiM', '')
    original = window.cmds.referenceQuery
    def query(node, **kwargs):
        if node == 'sotaiMRN2':
            raise RuntimeError('Not associated with a reference file')
        return original(node, **kwargs)
    window.cmds.referenceQuery = query
    window.refresh_cast()
    assert not window._test_errors
    assert 'sotaiMRN2' in window.scan_warning.toPlainText()
    assert window.info_tabs.tabText(1) == 'Warnings (1)'
    assert window.info_tabs.currentWidget() is window.detail
    window.info_tabs.setCurrentIndex(1)
    assert window.info_tabs.currentWidget() is window.scan_warning
    assert window.cast_table.rowCount() == 3
    window.tabs.setCurrentIndex(1)
    window.refresh_relink()
    row = next(i for i, ref in enumerate(window.references) if ref.node == 'sotaiMRN2')
    assert not window.relink_table.cellWidget(row, 3).isEnabled()
    assert window.relink_table.item(row, 4).text() == 'Unreadable — kept'


@pytest.fixture
def version_window(window, tmp_path):
    from dataclasses import replace
    paths = {}
    for context, version in [('anim', 'v003'), ('anim', 'v001'), ('proxy', 'v002')]:
        path = tmp_path / context / version / 'Hero.ma'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('// Maya scene')
        paths[(context, version)] = str(path)
    choices = {context: [{'version': ver, 'path': path} for (ctx, ver), path in paths.items() if ctx == context]
               for context in ['anim', 'proxy']}
    window.service.preview = lambda *args: [item(paths['anim', 'v003'], asset='Hero', variant='default')]
    window.service.cast_target_choices = lambda row: choices
    window.cmds.refs['heroRN'] = replace(window.cmds.refs['heroRN'], path=paths['anim', 'v003'])
    window.refresh_cast()
    return window, paths, choices


def test_context_and_version_selection_apply_and_refresh(version_window, monkeypatch):
    window, paths, _ = version_window
    context = window.cast_table.cellWidget(0, 3)
    version = window.cast_table.cellWidget(0, 4)
    assert context.currentText() == 'anim' and version.currentText() == 'v003'
    assert window.differences[0].status == 'In sync'
    context.setCurrentText('proxy')
    assert version.count() == 1 and version.currentText() == 'v002'
    assert window.operations()[0].target == paths['proxy', 'v002']
    context.setCurrentText('anim')
    version.setCurrentText('v001')
    assert window.operations()[0].target == paths['anim', 'v001']
    window.preview()
    assert paths['anim', 'v001'] in window.detail.toPlainText()
    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec_', lambda self: QtWidgets.QMessageBox.Apply)
    window.apply_selected()
    assert not window._test_errors
    assert window.cmds.calls[0][0] == paths['anim', 'v001']
    assert window.differences[0].status == 'In sync'
    assert window.cast_table.cellWidget(0, 4).currentText() == 'v001'


def test_removed_context_version_stops_apply(version_window, monkeypatch):
    window, paths, choices = version_window
    window.cast_table.cellWidget(0, 3).setCurrentText('proxy')
    choices.pop('proxy')
    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec_', lambda self: QtWidgets.QMessageBox.Apply)
    window.apply_selected()
    assert window.cmds.calls == []
    assert any('no longer available' in error for error in window._test_errors)


def test_context_selection_cleared_on_scope_change(version_window):
    window, paths, _ = version_window
    window.cast_table.cellWidget(0, 3).setCurrentText('proxy')
    assert window.cast_overrides
    window.scope.setCurrentText('Sequence')
    assert not window.cast_overrides
    window.refresh_cast()
    assert window.differences[0].target == paths['anim', 'v003']
