from pathlib import Path

import pytest

from test_usd_handoff import service, asset, plan
from smartlib.core.metadata import read_json, write_json


def test_camera_replaces_only_primary_and_keeps_base(service, tmp_path):
    from pxr import Usd, UsdGeom
    svc, identity = service
    bg, cam = asset(tmp_path), asset(tmp_path, 'camera')
    base_path = svc.publish(identity, plan(svc, identity, [
        dict(kind='assets', target='BG', source=str(bg)),
        dict(kind='camera', target='old_primary', source=str(cam))]))
    before = Path(base_path).read_bytes()
    result = svc.publish(identity, plan(svc, identity, [dict(kind='camera', target='primary', source=str(cam))]),
        base_composition=svc.pin(base_path))
    data = svc.load_handoff(result)
    assert data['included_targets'] == [['assets', 'BG'], ['camera', 'primary']]
    assert len(data['products']) == 2
    assert Path(base_path).read_bytes() == before
    stage = Usd.Stage.Open(data['entrypoint']['path'])
    assert stage.GetPrimAtPath('/Shot/Assets/BG/geo')
    assert len([p for p in stage.Traverse() if p.IsA(UsdGeom.Camera)]) == 1
    assert not stage.GetPrimAtPath('/Shot/Camera/old_primary')
    assert 'references' in Path(data['layers']['camera']['path']).read_text()
    with pytest.raises(ValueError, match='range/FPS/units'):
        changed = svc.plan(identity, [dict(kind='camera', target='primary', source=str(cam))], frame_range=[1, 3])
        svc.publish(identity, changed, base_composition=svc.pin(base_path))


@pytest.fixture
def panel(service, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import QtWidgets
    from smartlib.apps.shot_manager.camera_publish_panel import CameraPublishPanel
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc, identity = service
    monkeypatch.setattr(svc.shots, 'shot_frame_range', lambda _: (1, 2))
    parent = QtWidgets.QWidget()
    widget = CameraPublishPanel(svc.shots, parent, is_maya_session=True)
    widget.set_context(identity, '|cam')
    yield widget, identity
    parent.close()


def test_inline_selection_does_not_publish(panel, monkeypatch):
    widget, identity = panel
    calls = []
    monkeypatch.setattr(widget, 'start_camera_export', lambda bounds: calls.append(bounds))
    widget.set_context(identity, '|other')
    assert calls == []
    assert widget.source_label.text().startswith('|other')
    assert widget.step.value() == 1 and not widget.step.isEnabled()
    widget.publish()
    assert calls == [[1, 2]] and widget._running()
    widget.set_context(identity, '|next')
    assert widget.target == '|other'


def test_static_mode_preserves_shot_range(panel, monkeypatch):
    widget, _ = panel
    widget.range_mode.setCurrentText('Static')
    assert not widget.static_frame.isHidden()
    assert widget.static_frame.value() == 1
    widget.static_frame.setValue(2)
    assert not widget.start_frame.isEnabled()
    calls = []
    monkeypatch.setattr(widget, 'start_camera_export', lambda bounds: calls.append(bounds))
    widget.publish()
    assert calls == [[1, 2]]


def test_queue_completion_only_updates_panel(panel, tmp_path, monkeypatch):
    widget, identity = panel
    cam = asset(tmp_path, 'camera')
    snapshot = write_json(cam.with_name('camera.json'), {
        'portable_export': {'status': 'complete'}, 'files': {'usd': cam.name}})
    monkeypatch.setattr(widget, 'start_camera_export', lambda bounds: None)
    widget.publish()
    from types import SimpleNamespace
    widget._queue_job = 'test'
    widget._queue = SimpleNamespace(jobs={'test': dict(state='COMPLETE', task='Complete',
        message='composition/manifest.json', stderr='')})
    results = []
    widget.published.connect(results.append)
    widget._queue_changed('test')
    assert not widget._running()
    assert results == ['composition/manifest.json']
    assert widget.service.composition_versions(identity) == []
    widget.publish()
    widget._queue.jobs['test'].update(state='FAILED', message='failed')
    widget._queue_changed('test')
    assert results == ['composition/manifest.json']
    assert 'failed' in widget.status.toPlainText()


def test_panel_render(panel):
    from PySide6 import QtWidgets, QtGui
    widget, _ = panel
    font_id = QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
    if families:
        widget.setFont(QtGui.QFont(families[0], 9))
    widget.resize(620, 540)
    widget.parentWidget().resize(widget.size())
    widget.parentWidget().show()
    widget.show()
    QtWidgets.QApplication.processEvents()
    path = Path('.tmp/camera-publish-panel.png')
    path.parent.mkdir(parents=True, exist_ok=True)
    assert widget.grab().save(str(path))
    assert widget.minimumSizeHint().width() <= widget.width()
