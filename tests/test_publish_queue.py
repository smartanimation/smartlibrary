import hashlib
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from test_usd_handoff import service
from smartlib.core.metadata import read_json, write_json


@pytest.fixture
def queue(service, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import QtWidgets
    from smartlib.apps.review_build_manager.publish_queue import PublishQueue
    from smartlib.apps.review_build_manager.service import ReviewBuildManagerService
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc, identity = service
    monkeypatch.setattr(ReviewBuildManagerService, 'resolve_mayapy', lambda _: Path(sys.executable))
    monkeypatch.setattr(ReviewBuildManagerService, 'maya_process_environment', lambda _: ({}, {}))
    monkeypatch.setattr(ReviewBuildManagerService, 'maya_software_config_name', lambda _: 'software_test.yml')
    monkeypatch.setattr(ReviewBuildManagerService, 'maya_software_config', lambda _: {'plugin_profiles': {'core': {'required': ['testPlugin']}}})
    value = PublishQueue(svc.shots, app)
    yield value, identity, app
    value.timer.stop()
    if value.process:
        value.process.kill()
        value.process.waitForFinished(3000)
    value.pending.clear()
    value.deleteLater()
    app.processEvents()


def snapshot(tmp_path, version):
    directory = tmp_path / version
    directory.mkdir()
    (directory / 'primary.ma').write_text('// camera')
    return write_json(directory / 'camera.json', dict(schema='smartpipeline.primary_camera.v1',
        files={'ma': 'primary.ma'}, frame_range=[1, 2]))


def test_queue_is_fifo_and_survives_origin_widget(queue, tmp_path, monkeypatch):
    from PySide6 import QtWidgets
    q, identity, app = queue
    calls = []
    monkeypatch.setattr(q, '_launch', lambda job: calls.append(job['id']))
    origin = QtWidgets.QWidget()
    first = q.submit(identity, kind='primary_camera_usd', snapshot=snapshot(tmp_path, 'v001'), frame_range=[1, 2])
    second = q.submit(identity, kind='primary_camera_usd', snapshot=snapshot(tmp_path, 'v002'), frame_range=[1, 2])
    origin.deleteLater()
    app.processEvents()
    assert q.active == first and calls == [first]
    q.finish(False, 'failed one')
    app.processEvents()
    assert q.active == second and calls == [first, second]
    q.finish(True, 'manifest')
    assert q.jobs[first]['state'] == 'FAILED' and q.jobs[second]['state'] == 'COMPLETE'
    assert read_json(q.jobs[first]['status_file'], {})['message'] == 'failed one'


def test_request_is_frozen_and_edit_rejected_before_maya(queue, tmp_path, monkeypatch):
    from smartlib.apps.review_build_manager.publish_worker import run
    q, identity, app = queue
    lease = object()
    assert q.acquire(lease)
    key = q.submit(identity, kind='primary_camera_usd', snapshot=snapshot(tmp_path, 'v001'), frame_range=[1, 2])
    job = q.jobs[key]
    request = read_json(job['request_file'], {})
    assert request['maya_config_name'] == 'software_test.yml'
    assert request['native']['sha256']
    Path(job['request_file']).write_text('{}')
    assert run(job['request_file'], job['request_sha256']) == 1
    assert 'changed' in read_json(job['result_file'], {})['error']
    app.processEvents()
    assert q.active is None
    q.pending.clear()
    q.release(lease)


def test_failed_to_start_releases_queue(queue, tmp_path):
    q, identity, app = queue
    key = q.submit(identity, kind='primary_camera_usd', snapshot=snapshot(tmp_path, 'v001'), frame_range=[1, 2])
    q.jobs[key]['program'] = str(tmp_path / 'missing.exe')
    from PySide6 import QtCore
    timer = QtCore.QElapsedTimer()
    timer.start()
    while q.jobs[key]['state'] not in ('FAILED', 'COMPLETE') and timer.elapsed() < 3000:
        app.processEvents()
    assert q.jobs[key]['state'] == 'FAILED'
    assert q.active is None and q.lease is None


def test_assets_job_uses_shared_queue_with_frozen_registration(queue):
    q, identity, app = queue
    owner = object()
    q.acquire(owner)
    plan = q.service.plan(identity, [dict(kind='assets', target='Hero', source=None,
        geometry_source='animation')], frame_range=[1, 2], replace_assets=True)
    key = q.submit(identity, kind='assets_usd', plan=plan)
    plan['rows'].clear()
    request = read_json(q.jobs[key]['request_file'], {})
    assert request['plan']['rows'][0]['target'] == 'Hero'
    assert q.jobs[key]['task'] == 'Assets USD'
    q.pending.clear()
    q.release(owner)


def test_assets_placement_job_freezes_scene_and_plan(queue, tmp_path):
    from smartlib.dcc.maya.publish_scene_input import file_ref
    q, identity, app = queue
    owner = object()
    q.acquire(owner)
    scene = tmp_path / 'saved.ma'
    scene.write_text('// saved placement scene')
    scene_input = dict(scene=file_ref(scene), source=file_ref(scene), dependencies=[])
    plan = q.service.plan(identity, [dict(kind='assets', target='Cloth', source=None,
        geometry_source='animation')], frame_range=[1, 2], replace_assets=True)
    key = q.submit(identity, kind='assets_usd', plan=plan, scene_input=scene_input,
                   scene_options={}, frame_range=[1, 2])
    plan['rows'].clear()
    request = read_json(q.jobs[key]['request_file'], {})
    assert request['plan']['rows'][0]['target'] == 'Cloth'
    assert request['scene_input']['scene'] == file_ref(scene)
    q.pending.clear()
    q.release(owner)


@pytest.mark.parametrize('kind,label', [('primary_camera_usd', 'Primary Camera USD'),
                                      ('camera_batch_usd', 'Camera Batch USD'), ('layout_usd', 'Layout USD')])
def test_monitor_reopens_and_does_not_own_publish_process(queue, tmp_path, monkeypatch, kind, label):
    from PySide6 import QtCore
    from smartlib.apps.review_build_manager import publish_queue, window
    q, identity, app = queue
    monkeypatch.setattr(publish_queue, 'get_queue', lambda shots: q)
    monkeypatch.setattr(q, '_launch', lambda job: None)
    monkeypatch.setattr(window.ReviewBuildManagerWindow, 'scan_updates', lambda self: None)
    monkeypatch.setattr(window.ReviewBuildManagerWindow, '_settings', lambda self:
        QtCore.QSettings(str(tmp_path / 'settings.ini'), QtCore.QSettings.IniFormat))
    native = snapshot(tmp_path, 'v001')
    if kind == 'primary_camera_usd':
        key = q.submit(identity, kind=kind, snapshot=native, frame_range=[1, 2])
    else:
        from smartlib.dcc.maya.publish_scene_input import file_ref
        ref = file_ref(native.with_name('primary.ma'))
        key = q.submit(identity, kind=kind, scene_input=dict(scene=ref, source=ref),
                       scene_options=dict(targets=['primary']), frame_range=[1, 2])
    app.processEvents()
    monitor = window.ReviewBuildManagerWindow(config_dir=q.service.config.config_dir)
    assert len(monitor._publish_rows) == 1
    assert monitor.queue_jobs[0]['kind'] == 'publish_queue'
    monitor.close()
    assert q.active == key
    monkeypatch.setattr(window, '_WINDOW', monitor)
    assert window.show(q.service.config.config_dir) is monitor
    q.finish(True, 'test/manifest.json')
    assert monitor.queue_jobs[0]['state'] == 'COMPLETE'
    assert monitor.queue_table.item(0, 2).text().startswith(label)
    monitor.close()
    monitor.deleteLater()
