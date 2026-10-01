from dataclasses import asdict
from pathlib import Path

import pytest

from test_usd_handoff import service, asset, plan
from smartlib.apps.shot_manager.assets_publish import AssetsPublishService
from smartlib.apps.shot_manager.usd_handoff import compose_layers
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import AssetIdentity


@pytest.mark.parametrize('mode', ['STATIC', 'CURVE'])
def test_prop_placement_survives_payload_and_recomposition(service, tmp_path, mode):
    from pxr import Usd, UsdGeom
    from test_prop_usd import matrix
    svc, identity = service
    placement = dict(mode=mode, samples=[dict(frame=1, matrix=matrix(5))])
    if mode == 'CURVE':
        placement['samples'].append(dict(frame=2, matrix=matrix(10)))
    selected = svc.plan(identity, [dict(kind='assets', target='Box', source=str(asset(tmp_path)),
        geometry_source='asset', registration=asdict(AssetIdentity('prop', 'main', 'Box')),
        placement=placement)], frame_range=[1, 2], replace_assets=True)
    output = svc.publish(identity, selected)
    snapshot = svc.load_handoff(output)
    stage = Usd.Stage.Open(snapshot['entrypoint']['path'], load=Usd.Stage.LoadNone)
    prim = stage.GetPrimAtPath('/Shot/Assets/Box')
    assert prim.GetCustomDataByKey('smartpipeline:placement_motion') == mode
    assert UsdGeom.XformCache(2).GetLocalToWorldTransform(prim).ExtractTranslation()[0] == (5 if mode == 'STATIC' else 10)
    stage.Load()
    assert stage.GetPrimAtPath('/Shot/Assets/Box/geo')
    assert svc.compose_products(identity, [ref['path'] for ref in snapshot['products']])


def test_deforming_prop_cannot_receive_asset_placement(service):
    svc, identity = service
    with pytest.raises(ValueError, match='owned by Animation'):
        svc.plan(identity, [dict(kind='assets', target='Cloth', source=None, geometry_source='animation',
            placement={'mode': 'STATIC'})], frame_range=[1, 2])


def test_payload_and_metadata_survive_load_none_without_duplicate(service, tmp_path):
    from pxr import Usd
    svc, identity = service
    bg = asset(tmp_path)
    hero = asset(tmp_path, 'hero')
    info = asdict(AssetIdentity('BG', 'main', 'Room', 'default'))
    rows = [dict(kind='assets', target='Room_main', source=str(bg), geometry_source='asset', registration=info),
            dict(kind='assets', target='Hero_main', source=str(hero), geometry_source='animation',
                 registration=asdict(AssetIdentity('CH', 'main', 'Hero', 'default')))]
    selected = svc.plan(identity, rows, frame_range=[1, 2], fps=24, replace_assets=True)
    result = svc.publish(identity, selected)
    data = svc.load_handoff(result)
    stage = Usd.Stage.Open(data['entrypoint']['path'], load=Usd.Stage.LoadNone)
    room = stage.GetPrimAtPath('/Shot/Assets/Room_main')
    assert room.HasPayload() and not room.IsLoaded()
    assert room.GetCustomDataByKey('smartpipeline:name') == 'Room'
    assert not stage.GetPrimAtPath('/Shot/Assets/Room_main/geo')
    hero_prim = stage.GetPrimAtPath('/Shot/Assets/Hero_main')
    assert not hero_prim.HasPayload() and not hero_prim.HasAuthoredReferences()
    assert hero_prim.GetCustomDataByKey('smartpipeline:geometry_source') == 'animation'
    stage.Load()
    assert stage.GetPrimAtPath('/Shot/Assets/Room_main/geo')
    assert not stage.GetPrimAtPath('/Shot/Assets/Hero_main/geo')
    text = Path(data['layers']['assets']['path']).read_text()
    assert 'payload' in text and 'department' not in text and 'quality =' not in text
    assert svc.compose_products(identity, [ref['path'] for ref in data['products']])


def test_identity_only_does_not_require_character_usd(service):
    svc, identity = service
    selection = svc.plan(identity, [dict(kind='assets', target='Hero', source=None,
        geometry_source='animation', registration=asdict(AssetIdentity('CH', 'main', 'Hero')))],
        frame_range=[1, 2])
    result = svc.publish(identity, selection)
    assert svc.load_handoff(result)['included_targets'] == [['assets', 'Hero']]
    with pytest.raises(ValueError, match='requires'):
        svc.plan(identity, [dict(kind='assets', target='Hero', source=None, geometry_source='asset')],
                 frame_range=[1, 2])


def test_asset_snapshot_removal_keeps_camera_and_old_snapshot(service, tmp_path):
    from pxr import Usd
    svc, identity = service
    bg, cam = asset(tmp_path), asset(tmp_path, 'camera')
    base = svc.publish(identity, plan(svc, identity, [
        dict(kind='assets', target='Room', source=str(bg)),
        dict(kind='camera', target='primary', source=str(cam))]))
    original = Path(base).read_bytes()
    cleared = svc.plan(identity, [], frame_range=[1, 2], fps=24, replace_assets=True)
    result = svc.publish(identity, cleared, base_composition=svc.pin(base))
    data = svc.load_handoff(result)
    assert data['included_targets'] == [['camera', 'primary']]
    stage = Usd.Stage.Open(data['entrypoint']['path'])
    assert not stage.GetPrimAtPath('/Shot/Assets/Room')
    assert stage.GetPrimAtPath('/Shot/Camera/primary/cam')
    assert Path(base).read_bytes() == original


def test_duplicate_provider_rejected_by_all_composition_paths(service, tmp_path):
    svc, identity = service
    source = svc.pin(asset(tmp_path))
    products = [dict(kind='assets', target='Hero', entrypoint=source, inputs={}),
                dict(kind='animation', target='Hero', entrypoint=source)]
    settings = plan(svc, identity, [dict(kind='assets', target='Other', source=source['path'])])
    paths = dict.fromkeys(('shot', 'animation', 'camera', 'assets', 'layout'))
    with pytest.raises(ValueError, match='Duplicate geometry'):
        compose_layers(paths, products, settings, svc, in_memory=True)
    products[0]['inputs']['geometry_source'] = 'animation'
    stage, layers, _ = compose_layers(paths, products, settings, svc, in_memory=True)
    assert stage.GetPrimAtPath('/Shot/Animation/Hero/geo')
    assert not stage.GetPrimAtPath('/Shot/Assets/Hero/geo')


def test_registration_uses_existing_resolver_and_freezes_version(service, tmp_path, monkeypatch):
    old, identity = service
    svc = AssetsPublishService(old.shots)
    bg = asset(tmp_path)
    calls = []
    def resolve(asset_id, **kwargs):
        calls.append((asset_id, kwargs))
        return dict(path=str(bg))
    monkeypatch.setattr(svc.shots.asset_publish_resolver, 'resolve_usd_entry', resolve)
    monkeypatch.setattr(svc.shots, 'shot_frame_range', lambda _: [1, 2])
    aid = AssetIdentity('BG', 'main', 'Room')
    frozen = svc.registration_plan(identity, [dict(target='Room_main', asset=aid,
        version='v001', geometry_source='asset')])
    assert calls == [(aid, dict(version='v001', quality=None))]
    assert frozen['replace_assets'] and frozen['rows'][0]['source'] == svc.pin(bg)
    assert frozen['rows'][0]['registration']['usd_version'] == 'v001'


def test_asset_payload_changed_after_selection_is_rejected(service, tmp_path):
    svc, identity = service
    bg = asset(tmp_path)
    dep = asset(tmp_path, 'payload')
    frozen = svc.plan(identity, [dict(kind='assets', target='Room', source=str(bg),
        asset_dependencies=[svc.pin(dep)])], frame_range=[1, 2])
    dep.write_text(dep.read_text() + '\n# modified after enqueue\n')
    with pytest.raises(ValueError, match='changed'):
        svc.publish(identity, frozen)


def test_removed_asset_membership_restored_when_panel_reopens(service, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import QtWidgets, QtCore
    from smartlib.apps.shot_manager.assets_publish_panel import AssetsPublishPanel
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc, identity = service
    empty = svc.plan(identity, [], frame_range=[1, 2], replace_assets=True)
    snapshot = svc.publish(identity, empty)
    assert svc.load_handoff(snapshot)['assets_registration_complete']
    panel = AssetsPublishPanel(svc.shots)
    monkeypatch.setattr(panel.service, 'cast_entries', lambda _: [
        dict(target='Hero', asset=AssetIdentity('CH', 'main', 'Hero'), versions=[],
             error='', geometry_source='animation')])
    panel.set_context(identity)
    assert panel.table.item(0, 0).checkState() == QtCore.Qt.Unchecked
    panel.close()


def test_discovery_uses_cast_identity_and_defaults_character_to_metadata(service, monkeypatch):
    old, identity = service
    svc = AssetsPublishService(old.shots)
    monkeypatch.setattr(svc.shots, 'load_cast', lambda _: {'cast': {
        'Hero_main': dict(asset='Hero', category='character', group='main'),
        'Room_main': dict(asset='Room', category='BG', group='main')}})
    monkeypatch.setattr(svc.shots, 'find_asset_root', lambda _: None)
    monkeypatch.setattr(svc.shots.asset_publish_resolver, 'list_usd_versions', lambda _: ['v005', 'v002'])
    rows = svc.cast_entries(identity)
    assert rows[0]['geometry_source'] == 'animation'
    assert rows[1]['geometry_source'] == 'asset'
    assert rows[1]['versions'] == ['v005', 'v002']


def test_resolver_discovers_only_completed_proxy_members(service):
    svc, _ = service
    aid = AssetIdentity('BG', 'main', 'Room')
    for version, status, variant in [('v001', 'complete', 'default'), ('v009', 'building', 'default'),
                                     ('v010', 'complete', 'other'), ('v003', 'complete', 'default')]:
        directory = svc.paths.asset_usd_version_dir(aid, version)
        write_json(svc.paths.artifact_file(directory, 'manifest.json'),
                   dict(schema='smartpipeline.asset_usd_entry.v1', status=status,
                        members={variant: {'proxy': {'path': 'example'}}}))
    assert svc.shots.asset_publish_resolver.list_usd_versions(aid) == ['v003', 'v001']


def test_panel_no_load_policy_and_queues_snapshot(service, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import QtCore, QtWidgets
    from types import SimpleNamespace
    from smartlib.apps.shot_manager.assets_publish_panel import AssetsPublishPanel
    from smartlib.apps.review_build_manager import publish_queue
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc, identity = service
    panel = AssetsPublishPanel(svc.shots)
    monkeypatch.setattr(panel.service, 'cast_entries', lambda _: [
        dict(target='Hero', asset=AssetIdentity('CH', 'main', 'Hero'), versions=[],
             releases=[dict(label='v002', version='v003')], error='', geometry_source='asset')])
    monkeypatch.setattr(svc.shots, 'shot_frame_range', lambda _: [1, 2])
    panel.set_context(identity)
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 5).text() == 'Asset Release'
    assert panel.selection()[0]['quality'] == 'proxy'
    assert panel.selection()[0]['version'] == 'v003'
    monkeypatch.setattr(panel.service, 'registration_plan', lambda *a: {'rows': [{'source': 'pinned-release'}]})
    assert not any('Department' in label.text() for label in panel.findChildren(QtWidgets.QLabel))
    calls = []
    class Queue(QtCore.QObject):
        changed = QtCore.Signal(str)
        jobs = {}
        def submit(self, ident, **kwargs):
            calls.append((ident, kwargs))
            return 'PUB-test'
    q = Queue()
    monkeypatch.setattr(publish_queue, 'get_queue', lambda _: q)
    panel.publish()
    assert panel._busy and calls[0][1]['kind'] == 'assets_usd'
    assert calls[0][1]['plan']['rows'][0]['source'] == 'pinned-release'
    q.jobs['PUB-test'] = dict(state='COMPLETE', task='Complete', message='manifest', stderr='')
    q.changed.emit('PUB-test')
    assert not panel._busy
    panel.table.item(0, 0).setCheckState(QtCore.Qt.Unchecked)
    assert panel.selection() == []
    panel.close()
