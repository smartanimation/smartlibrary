from copy import deepcopy
from types import SimpleNamespace

import pytest

from smartlib.dcc.maya import camera_publish as cp, shot_builder


def package():
    sample = dict(frame=1, world_matrix=[1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.],
                  attributes={attr: 1. for attr in cp.ATTRS}, frustum=[-1., 1., -1., 1.])
    row = dict(layer='CHA', width=100, height=100, start=1, end=1, camera='smartCam_CHA',
               camera_key='layer:CHA', enabled=True, version=1, take=1, mode='Custom')
    return dict(schema=cp.SCHEMA, units=dict(time='film', linear='cm', angle='deg'),
                reference_resolution=[100, 100], frame_range=[1, 1], rows=[row], cameras=[
                    dict(key='primary', role='primary', name='creativeCam', frame_range=[1, 1],
                         resolution=[100, 100], samples=[deepcopy(sample)]),
                    dict(key='layer:CHA', role='generated', primary_key='primary', name='smartCam_CHA',
                         frame_range=[1, 1], resolution=[100, 100], samples=[deepcopy(sample)],
                         spec=dict(layer='CHA', width=100, height=100, start=1, end=1,
                                   policy='horizontal', reference_resolution=[100, 100]))])


def test_valid_camera_package():
    cp.validate_package(package())


@pytest.mark.parametrize('mutate', [
    lambda p: p['cameras'][1].update(key='primary'),
    lambda p: p['cameras'][1].update(name='creativeCam'),
    lambda p: p['cameras'][1].update(name='|scene|smartCam_CHA'),
    lambda p: p['cameras'][1].update(primary_key='missing'),
    lambda p: p['cameras'][0].update(role='generated'),
    lambda p: p['cameras'][1]['samples'].clear(),
    lambda p: p['cameras'][0]['samples'][0]['attributes'].pop('focalLength'),
    lambda p: p['cameras'][0]['samples'][0]['attributes'].update(focalLength=float('nan')),
    lambda p: p['rows'][0].update(camera_key='missing'),
    lambda p: p['rows'][0].update(width=200),
    lambda p: p['cameras'][1]['spec'].update(policy='unknown'),
    lambda p: p['cameras'][1]['spec'].update(reference_resolution=[200, 100]),
])
def test_invalid_camera_packages_fail_preflight(mutate):
    data = package()
    mutate(data)
    with pytest.raises(ValueError):
        cp.validate_package(data)


def test_build_routes_package_without_legacy_fallback(tmp_path, monkeypatch):
    import json
    path = tmp_path / 'camera.json'
    path.write_text(json.dumps(package()), encoding='utf-8')
    def restore(*args, **kwargs):
        raise ValueError('package collision')
    monkeypatch.setattr(cp, 'restore_package', restore)
    cmds = SimpleNamespace()  # no delete, import, or legacy camera operations allowed
    with pytest.raises(ValueError, match='package collision'):
        shot_builder._apply_construct_cameras(cmds, tmp_path, {}, dict(components=[
            dict(component_type='camera', enabled=True, path=str(path), source=dict(kind='published_camera'))]), 0.)
    with pytest.raises(ValueError, match='package collision'):
        shot_builder._create_camera_from_json(cmds, path, {}, 0.)


def test_legacy_settings_cannot_replace_published_camera_rows(monkeypatch):
    from smartlib.dcc.maya import review_playblast
    owned = package()['rows']
    monkeypatch.setattr(review_playblast, 'load_scene_playblast_settings',
                        lambda cmds: dict(rows=owned, camera_package_source='publish-v003'))
    result = cp.merge_build_settings(dict(rows=[dict(layer='CHA', camera='wrong'),
                                               dict(layer='SPECIAL', camera='specialCam')]), None)
    assert result['rows'][0] == owned[0]
    assert result['rows'][1]['camera'] == 'specialCam'
    assert result['layer_order'] == ['CHA', 'SPECIAL']
