import json
from types import SimpleNamespace

import pytest
from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow as Window
from smartlib.apps.shot_manager.service import ShotIdentity


@pytest.mark.parametrize('scope,expected', [
    (None, []),
    (('shot', 'ep01', 's001', 'c002'), ['c002']),
    (('sequence', 'ep01', 's001'), ['c001', 'c002']),
])
def test_only_selected_shots_are_resolved(scope, expected):
    calls = []
    def status(identity, **kwargs):
        calls.append(identity.shot)
        return SimpleNamespace(identity=identity)
    control = SimpleNamespace(currentText=lambda: 'anim')
    window = SimpleNamespace(
        _tree_scope=lambda: scope, _build_plan_cache={},
        _shot_identities=[ShotIdentity('ep01', 's001', 'c001'),
                          ShotIdentity('ep01', 's001', 'c002'),
                          ShotIdentity('ep02', 's099', 'other')],
        _identity_matches_scope=Window._identity_matches_scope,
        service=SimpleNamespace(shot_status=status),
        mode_combo=control, department_combo=control, task_combo=control,
        generate_review_check=SimpleNamespace(isChecked=lambda: False),
        _stage_input_overrides=lambda _: {}, _populate_filters=lambda: None,
        _apply_filters=lambda: None, _update_build_buttons=lambda: None,
        shot_table=SimpleNamespace(rowCount=lambda: 0),
        footer_label=SimpleNamespace(setText=lambda _: None))
    Window._scan_selected_scope(window)
    assert calls == expected


def test_submit_commits_only_selected_shot_not_other_drafts():
    stored = {'planned_snapshots': json.dumps({'other': {'version': 'old'}})}
    settings = SimpleNamespace(value=lambda k, default: stored.get(k, default),
                               setValue=lambda k, v: stored.update({k: v}))
    window = SimpleNamespace(_planned_snapshot_key=lambda i: i, _settings=lambda: settings,
                              _planned_snapshots={'other': {'version': 'unsaved'}})
    Window._commit_input_choices(window, 'selected', {'version': 'new'})
    assert json.loads(stored['planned_snapshots']) == {
        'other': {'version': 'old'}, 'selected': {'version': 'new'}}
