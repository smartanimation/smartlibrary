from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from smartlib.apps.shot_manager.service import ShotManagerService, ShotIdentity
from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService


@pytest.mark.parametrize('package', [False, True])
def test_camera_data_does_not_hide_review_cameras(tmp_path, package):
    shots = object.__new__(ShotManagerService)
    shots.paths = SimpleNamespace(project_root=tmp_path)
    shots.latest_editorial_timing_path = lambda _: None
    shots.shot_data_root = lambda _: tmp_path / 'data'
    shots.latest_anim_input = lambda _: None
    shots.selected_virtual_camera_dependencies = lambda _: []
    shots.list_set_dress_data = lambda _: []
    shots.list_set_dress_publish_versions = lambda _: []
    shots.load_cast = lambda _: {'cast': {}}
    shots.load_sequence_cast = lambda *a: {'cast': {}}
    shots.build_preview = lambda *a, **kw: []
    paths = []
    for name in (['review'] if package else ['primary', 'smartCam_CHA', 'smartCam_BGA']):
        path = tmp_path / 'publish' / name / 'main' / 'v002' / 'camera.json'
        path.parent.mkdir(parents=True)
        data = (dict(schema='smartpipeline.review_camera_rules.v1',
                     primary_camera={'camera': 'cam'},
                     rows=[{'camera': 'smartCam_CHA'}, {'camera': 'smartCam_BGA'}])
                if package else dict(schema='smartpipeline.primary_camera.v1',
                                     camera='cam' if name == 'primary' else name))
        path.write_text(json.dumps(data), encoding='utf-8')
        paths.append(str(path))
    shots._latest_review_camera_paths = lambda _: paths
    rows = []
    for name in ['cam', 'smartCam_CHA', 'VCCAM']:
        path = tmp_path / 'data' / name / 'v001'
        path.mkdir(parents=True)
        (path / 'camera.json').write_text('{}', encoding='utf-8')
        rows.append(SimpleNamespace(name=f'camera/{name}/main', latest=True,
                                    version='v001', path=str(path)))
    shots.list_shot_data_versions = lambda _: rows
    components = shots.construct_from_stage_inputs(ShotIdentity('ep', 'sq', 'sh'))['components']
    assert {c['path'] for c in components if c['source']['kind'] == 'published_camera'} == set(paths)
    assert [c['name'] for c in components if c['source']['kind'] == 'scene_data'] == ['VCCAM']
    assert all(c['enabled'] for c in components)
    if not package:
        assert {c['name'] for c in components} == {'cam', 'smartCam_CHA', 'smartCam_BGA', 'VCCAM'}
    shots.load_construct = lambda _: {'components': [dict(
        component_type='camera', name='cam', enabled=True, path='old-camera.json',
        source={'kind': 'scene_data'})]}
    resolved = shots.resolved_construct(ShotIdentity('ep', 'sq', 'sh'))['components']
    assert not any(c['path'] == 'old-camera.json' for c in resolved)


def test_completed_composition_camera_membership_wins_over_legacy(monkeypatch):
    shots = object.__new__(ShotManagerService)
    shots.list_camera_package_versions = lambda identity: [SimpleNamespace(path='legacy.json', latest=True)]
    monkeypatch.setattr(UsdHandoffService, '__init__', lambda self, shots: None)
    monkeypatch.setattr(UsdHandoffService, 'composition_versions', lambda *a: [{'path': 'composition'}])
    records = {
        'composition': {'products': [{'path': 'primary'}, {'path': 'derived'}, {'path': 'asset'}]},
        'primary': {'kind': 'camera', 'inputs': {'camera_snapshot': {'path': 'primary.json'}}},
        'derived': {'kind': 'camera', 'inputs': {'camera_snapshot': {'path': 'derived.json'}}},
        'asset': {'kind': 'assets', 'inputs': {}},
    }
    monkeypatch.setattr(UsdHandoffService, 'load_handoff', lambda self, p: records[str(p)])
    monkeypatch.setattr(UsdHandoffService, 'check', lambda self, ref: Path(ref['path']))
    assert shots._latest_review_camera_paths(None) == ['primary.json', 'derived.json']
    def fail(self, ref):
        raise ValueError('Input changed after selection')
    monkeypatch.setattr(UsdHandoffService, 'check', fail)
    with pytest.raises(ValueError, match='Input changed'):
        shots._latest_review_camera_paths(None)


def test_old_saved_camera_package_is_not_reintroduced_with_batch():
    shots = object.__new__(ShotManagerService)
    old = dict(component_type='camera', name='Camera Package / main / main',
               path='legacy.json', source=dict(kind='published_camera', camera_package=True))
    new = dict(component_type='camera', name='primary', path='primary.json',
               source=dict(kind='published_camera', camera_batch=True))
    shots.load_construct = lambda identity: dict(components=[old])
    shots.construct_from_stage_inputs = lambda *a, **kw: dict(components=[new])
    assert shots.resolved_construct(ShotIdentity('ep', 'sq', 'sh'))['components'] == [new]


def test_saved_camera_package_remains_when_no_batch_exists():
    shots = object.__new__(ShotManagerService)
    old = dict(component_type='camera', name='Camera Package / main / main',
               path='legacy.json', source=dict(kind='published_camera', camera_package=True))
    shots.load_construct = lambda identity: dict(components=[old])
    shots.construct_from_stage_inputs = lambda *a, **kw: dict(components=[])
    assert shots.resolved_construct(ShotIdentity('ep', 'sq', 'sh'))['components'] == [old]
