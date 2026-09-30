import json

import pytest

from smartlib.review.input_dependencies import associate_inputs, effective_components


def row(kind, name, enabled=True, **component):
    return dict(type=kind, cast_key=name, enabled=enabled, state='READY',
                component=dict(component_type=kind, name=name, enabled=enabled, **component))


def test_curve_grouped_under_exact_cast_and_parent_off_preserves_use():
    curve = row('animation_curve', 'JIN_main')
    rig = row('rig', 'JIN_main', False)
    rows = associate_inputs([curve, rig], reorder=True)
    assert rows == [rig, curve]
    assert curve['enabled'] is True
    assert curve['effective_enabled'] is False
    assert curve['state'] == 'PARENT OFF'
    rig['enabled'] = True
    associate_inputs(rows)
    assert curve['effective_enabled'] is True
    assert curve['state'] == 'READY'


def test_orphan_curve_blocks_execution_but_can_be_excluded():
    curve = row('animation_curve', 'JIN_main')
    associate_inputs([curve])
    assert curve['display_name'].startswith('[No target]')
    with pytest.raises(ValueError, match='JIN_main'):
        effective_components([curve['component']], {})
    curve['component']['enabled'] = False
    assert not effective_components([curve['component']], {})[0]['enabled']


def test_does_not_match_fuzzy_asset_name():
    rows = [row('rig', 'JIN_other'), row('animation_curve', 'JIN_main')]
    associate_inputs(rows)
    assert rows[1]['dependency_error']


def test_usd_is_not_an_animation_curve_target():
    with pytest.raises(ValueError):
        effective_components([row('usd', 'JIN_main')['component'],
                              row('animation_curve', 'JIN_main')['component']], {})


def test_setdress_shared_exact_namespace_and_changed_version(tmp_path):
    package = tmp_path / 'dress.json'
    package.write_text(json.dumps({'base': [{'node': '|root|BG:desk'}, {'node': '|PROP:chair'}]}))
    cast = {'room': {'namespace': 'BG'}, 'chair': {'namespace': 'PROP'}}
    rows = [row('rig', 'room'), row('rig', 'chair', False),
            row('set_dress', 'dress', path=str(package))]
    associate_inputs(rows, cast)
    assert rows[2]['parent_keys'] == ['chair', 'room']
    assert rows[2]['display_name'].startswith('[Shared:')
    assert not rows[2]['effective_enabled']
    package.write_text(json.dumps({'base': [{'node': '|root|BG:desk'}]}))
    associate_inputs(rows, cast)
    assert rows[2]['parent_keys'] == ['room']
    assert rows[2]['effective_enabled']


def test_ambiguous_namespace_not_guessed(tmp_path):
    path = tmp_path / 'dress.json'
    path.write_text(json.dumps({'base': [{'node': '|BG:desk'}]}))
    rows = [row('rig', 'a'), row('rig', 'b'), row('set_dress', 'desk', path=str(path))]
    associate_inputs(rows, {'a': {'namespace': 'BG'}, 'b': {'namespace': 'BG'}})
    assert rows[-1]['dependency_error']


def test_execution_does_not_mutate_requested_preferences():
    components = [row('rig', 'JIN_main', False)['component'], row('animation_curve', 'JIN_main')['component']]
    result = effective_components(components, {})
    assert not result[1]['enabled']
    assert components[1]['enabled']


def test_planned_parent_use_is_independent_and_grouping_keeps_editor_indices():
    from types import SimpleNamespace
    from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow as Window
    curve, rig = row('animation_curve', 'JIN_main'), row('rig', 'JIN_main', False)
    window = SimpleNamespace(
        current_build_content_rows=[curve, rig],
        _planned_snapshots={'shot': {'inputs': [dict(type='rig', name='JIN_main', enabled=True)]}},
        _planned_snapshot_key=lambda _: 'shot', _use_key=Window._use_key,
        _local_content_state=lambda data, enabled: 'READY' if enabled else 'EXCLUDED',
    )
    planned = Window._review_use_rows(window, None)
    assert [r['type'] for r in planned] == ['rig', 'animation_curve']
    assert [r['_content_index'] for r in planned] == [1, 0]
    assert planned[1]['effective_enabled']
    assert not rig['enabled']
    window._planned_snapshots['shot']['inputs'][0]['enabled'] = False
    planned = Window._review_use_rows(window, None)
    assert planned[1]['enabled']  # Keep preference, exclude only from execution.
    assert not planned[1]['effective_enabled']
