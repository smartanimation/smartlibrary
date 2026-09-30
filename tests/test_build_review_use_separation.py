from types import SimpleNamespace

import pytest

from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow as Window


@pytest.mark.parametrize('kind', ['rig', 'usd', 'set_dress', 'camera', 'light',
                                  'placement', 'animation_curve', 'review_layers'])
@pytest.mark.parametrize('build_use,review_use', [(True, False), (False, True)])
def test_review_use_is_independent_for_all_inputs(kind, build_use, review_use):
    row = dict(type=kind, cast_key='main', enabled=build_use,
               component=dict(enabled=build_use))
    window = SimpleNamespace(
        current_build_content_rows=[row],
        _planned_snapshots={'shot': {'inputs': [dict(type=kind, name='main', enabled=review_use)]}},
        _planned_snapshot_key=lambda _: 'shot',
        _use_key=Window._use_key,
        _local_content_state=lambda data, enabled: 'READY' if enabled else 'EXCLUDED',
    )
    result = Window._review_use_rows(window, None)
    assert result[0]['enabled'] is review_use
    assert result[0]['component']['enabled'] is review_use
    assert row['enabled'] is build_use
    assert row['component']['enabled'] is build_use


def test_planned_camera_data_cannot_replace_pinned_batch_snapshot():
    component = dict(component_type='camera', name='cam', enabled=True,
                     path='published/primary/v002/camera.json', version='v002',
                     source={'kind': 'published_camera', 'camera_batch': True})
    snapshot = {'inputs': [dict(type='camera', name='cam', enabled=False,
                               path='data/camera/cam/v001/camera.json', version='v001')]}
    result = Window._apply_planned_snapshot_to_construct({'components': [component]}, snapshot)
    camera = result['components'][0]
    assert camera['path'] == component['path']
    assert camera['version'] == 'v002'
    assert camera['enabled'] is False


def test_excluded_cast_remains_in_build_and_review_candidates():
    from smartlib.apps.review_build_manager.service import ReviewBuildManagerService
    manager = object.__new__(ReviewBuildManagerService)
    captured = {}
    def resolve(*args, **kwargs):
        captured.update(kwargs)
        return {'components': [dict(component_type='rig', name='DLI_main', enabled=True,
            path='', version='latest', source={'kind': 'cast_entry', 'asset': 'DLI'})]}
    manager.shots = SimpleNamespace(
        load_construct=lambda _: {'components': []}, load_cast=lambda _: {'cast': {}},
        resolved_construct=resolve, find_asset_root=lambda _: None, paths=object())
    manager.normalize_cast_contexts = lambda *args, **kwargs: {}
    manager.review_workflow = lambda _: SimpleNamespace(latest_layer_definition=lambda: ({}, None))
    rows = manager.build_contents(SimpleNamespace(episode='ep', sequence='sq', shot='sh'),
                                  excluded_cast=['DLI_main'])
    rig = next(row for row in rows if row['type'] == 'rig')
    assert captured['exclude_cast'] == []
    assert rig['cast_key'] == 'DLI_main'
    assert rig['enabled'] is False
