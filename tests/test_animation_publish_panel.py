from pathlib import Path
from types import SimpleNamespace

import pytest

from test_usd_handoff import service
from smartlib.core.metadata import write_json


@pytest.fixture
def panel(service, tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    pytest.importorskip('PySide6')
    from PySide6 import QtWidgets
    from smartlib.apps.shot_manager.animation_publish_panel import AnimationPublishPanel
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc, identity = service
    versions = []
    for version in ('v012', 'v011'):
        directory = tmp_path / version
        directory.mkdir()
        rig = directory / 'rig.ma'
        rig.write_text('// Rig')
        source = write_json(directory / 'animation_manifest.json', {
            'schema': 'smartpipeline.animation_atom.v3', 'rig_context': 'ANIM',
            'rig_dependencies': [{'path': str(rig)}]})
        versions.append(SimpleNamespace(name='data/animation/Hero/curves', version=version, path=str(source)))
    from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
    monkeypatch.setattr(UsdHandoffService, 'animation_rig_versions', lambda self, identity, target:
        {'ANIM': [{'version': v.version, 'path': str(Path(v.path).with_name('rig.ma'))} for v in versions],
         'REND': []} if target == 'Hero' else {})
    monkeypatch.setattr(svc.shots, 'shot_frame_range', lambda _: (278, 411))
    parent = QtWidgets.QWidget()
    widget = AnimationPublishPanel(svc.shots, parent, is_maya_session=True)
    widget.set_context(identity, 'Hero')
    yield widget, identity, versions
    widget.close()
    parent.close()


def test_inline_layout_and_real_versions(panel):
    from PySide6 import QtWidgets
    widget, _, versions = panel
    assert not widget.isWindow()
    assert not isinstance(widget, QtWidgets.QDialog)
    assert widget.source_group.title() == 'Source Versions'
    assert widget.output_group.title() == 'Output Settings'
    assert not hasattr(widget, 'curve_version')
    assert not hasattr(widget, 'curve_check')
    assert not hasattr(widget, 'open_btn')
    assert widget.rig_version.currentData() == str(Path(versions[0].path).with_name('rig.ma'))
    assert widget.rig_context.currentText() == 'ANIM'
    assert widget.sculpt_version.currentData() is None
    assert widget.publish_btn.isEnabled()
    assert (widget.start_frame.value(), widget.end_frame.value(), widget.step.value()) == (278, 411, 1)
    assert widget.usd_check.isChecked()
    assert not widget.abc_check.isEnabled() and not widget.strip_namespaces.isEnabled()


def test_target_change_clears_inputs_and_restores_draft(panel):
    widget, identity, versions = panel
    widget.rig_version.setCurrentIndex(1)
    widget.range_mode.setCurrentIndex(1)
    widget.start_frame.setValue(300)
    widget.end_frame.setValue(400)
    widget.set_context(identity, 'Missing')
    assert widget.rig_version.count() == 0
    assert not widget.publish_btn.isEnabled()
    widget.set_context(identity, 'Hero')
    assert widget.rig_version.currentData() == str(Path(versions[1].path).with_name('rig.ma'))
    assert (widget.start_frame.value(), widget.end_frame.value()) == (300, 400)


def test_publish_exports_current_data_before_plan(panel, monkeypatch):
    widget, identity, versions = panel
    widget.rig_version.setCurrentIndex(1)
    widget.range_mode.setCurrentIndex(1)
    widget.start_frame.setValue(300)
    calls = []
    captured = []
    monkeypatch.setattr(widget, 'export_current_data', lambda bounds: captured.append(bounds) or versions[0].path)
    widget.data_published.connect(lambda path: captured.append(path))
    def plan(ident, rows, **kwargs):
        calls.append((ident, rows, kwargs))
        return {'frozen': True}
    monkeypatch.setattr(widget.service, 'plan', plan)
    monkeypatch.setattr(widget, 'start_worker', lambda value: calls.append(value))
    widget.publish()
    assert calls[0][0] == identity
    assert captured == [[300, 411], versions[0].path]
    assert calls[0][1][0]['source'] == versions[0].path
    assert calls[0][1][0]['rig_context'] == 'ANIM'
    assert calls[0][1][0]['rig'] == str(Path(versions[1].path).with_name('rig.ma'))
    assert calls[0][1][0]['sculpt'] is None
    assert calls[0][2]['frame_range'] == [300, 411]
    assert calls[1] == {'frozen': True}


def test_same_selection_does_not_rescan(panel, monkeypatch):
    widget, identity, _ = panel
    def fail(*args, **kwargs):
        raise AssertionError('Unexpected source scan')
    monkeypatch.setattr(widget.service, 'animation_rig_versions', fail)
    widget.set_context(identity, 'Hero')
    assert widget.publish_btn.isEnabled()


def test_no_export_on_selection_or_outside_maya(panel, monkeypatch):
    widget, identity, _ = panel
    def fail(*args):
        raise AssertionError('Should not export')
    monkeypatch.setattr(widget, 'export_current_data', fail)
    widget.rig_context.setCurrentText('REND')
    assert not widget.publish_btn.isEnabled()
    widget.rig_context.setCurrentText('ANIM')
    widget.is_maya_session = False
    widget._update_ready()
    assert not widget.publish_btn.isEnabled()
    widget.publish()


def test_data_retained_and_reported_after_usd_failure(panel, monkeypatch):
    widget, _, versions = panel
    monkeypatch.setattr(widget, 'export_current_data', lambda bounds: versions[0].path)
    def fail(*a, **k):
        raise ValueError('Incompatible Rig')
    monkeypatch.setattr(widget.service, 'plan', fail)
    widget.publish()
    assert 'Incompatible Rig' in widget.status.toPlainText()
    assert versions[0].path in widget.status.toPlainText()
    assert Path(versions[0].path).is_file()


def test_shot_manager_has_no_launcher_button():
    source = Path('scripts/shot_manager_ui.py').read_text(encoding='utf8')
    assert 'QPushButton("USD Publish — From Data' not in source
    assert 'self.publish_details_stack.addWidget(self.animation_publish_panel)' in source


@pytest.mark.parametrize('width', [520, 740])
def test_panel_render(panel, width):
    from PySide6 import QtWidgets, QtGui
    widget, _, _ = panel
    font_id = QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
    if families:
        widget.setFont(QtGui.QFont(families[0], 9))
    widget.setStyleSheet('QWidget { background: #292d30; color: #e1e5e8; } '
        'QGroupBox { border: 1px solid #48545b; border-radius: 4px; margin-top: 12px; padding-top: 12px; } '
        'QGroupBox::title { subcontrol-origin: margin; left: 10px; } '
        'QComboBox, QSpinBox, QPushButton { padding: 4px; } '
        'QWidget:disabled { color: #81878b; }')
    widget.resize(width, 580)
    widget.parentWidget().resize(widget.size())
    widget.parentWidget().show()
    widget.show()
    QtWidgets.QApplication.processEvents()
    path = Path('.tmp/animation-publish-panel.png')
    path.parent.mkdir(parents=True, exist_ok=True)
    assert widget.grab().save(str(path))
    assert widget.minimumSizeHint().width() <= widget.width()
