from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow
from types import SimpleNamespace


def test_planned_snapshot_use_override_wins_after_construct_resolution() -> None:
    resolved = {
        "components": [
            {
                "component_type": "rig",
                "name": "JIN_main",
                "enabled": True,
                "version": "v002",
                "path": "latest/JIN.ma",
            },
            {"component_type": "rig", "name": "DLI_main", "enabled": True},
            {"component_type": "camera", "name": "virtual_cam", "enabled": True},
        ]
    }
    planned = {
        "inputs": [
            {
                "type": "rig",
                "name": "JIN_main",
                "enabled": False,
                "version": "v001",
                "path": "official/JIN.ma",
            },
            {"type": "rig", "name": "DLI_main", "enabled": True},
            {"type": "virtual_camera", "name": "virtual_cam", "enabled": False},
        ]
    }

    result = ReviewBuildManagerWindow._apply_planned_snapshot_to_construct(
        resolved, planned
    )

    enabled = {
        (row["component_type"], row["name"]): row["enabled"]
        for row in result["components"]
    }
    assert enabled[("rig", "JIN_main")] is False
    assert enabled[("rig", "DLI_main")] is True
    assert enabled[("camera", "virtual_cam")] is False
    jin = next(row for row in result["components"] if row["name"] == "JIN_main")
    assert jin["version"] == "v001"
    assert jin["path"] == "official/JIN.ma"
    assert resolved["components"][0]["enabled"] is True
    assert resolved["components"][0]["version"] == "v002"


def test_context_edit_clears_only_target_locks_and_preserves_use():
    snapshot = {"inputs": [
        {"type": "rig", "name": "JIN", "context": "LO", "enabled": False,
         "version": "v009", "path": "old.ma"},
        {"type": "rig", "name": "DLI", "version": "v001", "path": "other.ma"},
    ]}
    result = ReviewBuildManagerWindow._set_snapshot_context(snapshot, "rig", "JIN", "ANIM")
    row = result["inputs"][0]
    assert row["context"] == "ANIM"
    assert row["context_override"] is True
    assert row["enabled"] is False
    assert "version" not in row and "path" not in row
    assert result["inputs"][1] == snapshot["inputs"][1]
    assert snapshot["inputs"][0]["path"] == "old.ma"


def test_sequential_context_edits_preserve_prior_explicit_selection():
    Window = ReviewBuildManagerWindow
    saved = {'inputs': [
        dict(type='rig', name='MOT', context='ANIM', context_override=True, enabled=True),
        dict(type='rig', name='YOU', context='LO', context_override=False, enabled=False),
        dict(type='light', name='main', enabled=False, version='v002', path='light.json'),
    ]}
    # Re-resolved components do not carry the UI's manual-choice marker.
    rows = [dict(type='rig', cast_key=name, context=context, enabled=True,
                 component={'source': {}}, input_versions=[])
            for name, context in [('MOT', 'ANIM'), ('YOU', 'LO')]]
    window = SimpleNamespace(
        current_build_content_rows=rows, _planned_snapshots={'shot': saved},
        _planned_snapshot_key=lambda _: 'shot', _use_key=Window._use_key,
        _local_content_state=lambda row, enabled: 'READY' if enabled else 'EXCLUDED',
    )
    review_rows = Window._review_use_rows(window, None)
    payload = {'inputs': [dict(type=r['type'], name=r['cast_key'], context=r['context'],
                              context_override=r['context_override'], enabled=r['enabled'])
                          for r in review_rows] + [saved['inputs'][2]]}
    updated = Window._set_snapshot_context(payload, 'rig', 'YOU', 'ANIM')
    assert Window._snapshot_contexts({}, updated, 'WORK') == {'MOT': 'ANIM', 'YOU': 'ANIM'}
    assert updated['inputs'][1]['enabled'] is False
    assert updated['inputs'][2] == saved['inputs'][2]
    assert 'context_override' not in rows[0]  # Planned copies do not alter Build Use.


def test_old_context_version_cannot_restore_into_new_context(tmp_path):
    old = tmp_path / 'old.ma'
    old.write_text('old')
    row = dict(context='ANIM', component=dict(path='new.ma', version='v001'),
               input_versions=[dict(path=str(old), version='v009')])
    ReviewBuildManagerWindow._restore_input_version(row, dict(context='LO', path=str(old)))
    assert row['component'] == dict(path='new.ma', version='v001')


def test_context_override_and_stage_default_win_over_old_construct_choices():
    snapshot = {"inputs": [
        {"type": "rig", "name": "JIN", "context": "ANIM", "context_override": True},
        {"type": "rig", "name": "DLI", "context": "LO", "context_override": False},
    ]}
    assert ReviewBuildManagerWindow._snapshot_contexts(
        {"JIN": "LO", "DLI": "LO"}, snapshot, "REND"
    ) == {"JIN": "ANIM", "DLI": "REND"}
    reset = ReviewBuildManagerWindow._set_snapshot_context(snapshot, "rig", "JIN", "")
    assert not reset["inputs"][0]["context_override"]
    assert ReviewBuildManagerWindow._snapshot_contexts({}, reset, "WORK")["JIN"] == "WORK"


def test_re_resolved_rows_keep_each_saved_context_override_marker():
    rows = [
        {"type": "rig", "context": "ANIM", "component": {"source": {}}},
        {"type": "rig", "context": "PROXY", "component": {"source": {}}},
    ]
    saved = [
        {"context": "ANIM", "context_override": True},
        {"context": "PROXY", "context_override": True},
    ]

    for row, entry in zip(rows, saved):
        ReviewBuildManagerWindow._restore_snapshot_context_marker(row, entry)

    assert [row["context_override"] for row in rows] == [True, True]
    assert [row["component"]["source"]["context_override"] for row in rows] == [True, True]


def test_snapshot_cannot_restore_path_from_different_context():
    resolved = {"components": [{"component_type": "rig", "name": "JIN",
        "source": {"context": "ANIM"}, "path": "anim.ma", "version": "v002"}]}
    snapshot = {"inputs": [{"type": "rig", "name": "JIN", "context": "LO",
        "path": "lo.ma", "version": "v009", "enabled": False}]}
    row = ReviewBuildManagerWindow._apply_planned_snapshot_to_construct(resolved, snapshot)["components"][0]
    assert row["path"] == "anim.ma"
    assert row["version"] == "v002"
    assert not row["enabled"]


def test_snapshot_key_is_scoped_to_shot_department_and_task():
    window = SimpleNamespace(
        service=SimpleNamespace(project_name="ELCD"),
        department_combo=SimpleNamespace(currentText=lambda: "anim"),
        task_combo=SimpleNamespace(currentText=lambda: "preComp"),
    )
    identity = SimpleNamespace(episode="ep02", sequence="s027", shot="c001")
    key = ReviewBuildManagerWindow._planned_snapshot_key(window, identity)
    assert key == "ELCD/ep02/s027/c001/anim/preComp"
    identity.shot = "c002"
    assert ReviewBuildManagerWindow._planned_snapshot_key(window, identity) != key


def test_worker_overrides_respect_planned_context_even_in_rend_stage():
    identity = SimpleNamespace(episode="ep02", sequence="s027", shot="c001")
    snapshot = {"inputs": [{"type": "rig", "name": "JIN", "context": "ANIM", "context_override": True}]}
    window = SimpleNamespace(
        mode_combo=SimpleNamespace(currentText=lambda: "REND STAGE"),
        input_camera_edit=SimpleNamespace(text=lambda: ""),
        input_overlay_check=SimpleNamespace(isChecked=lambda: False),
        input_placements_check=SimpleNamespace(isChecked=lambda: False),
        input_representation_combo=SimpleNamespace(currentData=lambda: "project"),
        input_exclude_cast_edit=SimpleNamespace(text=lambda: ""),
        build_content_settings={}, _planned_snapshots={"scope": snapshot},
        _planned_snapshot_key=lambda _identity: "scope",
        _snapshot_contexts=ReviewBuildManagerWindow._snapshot_contexts,
        service=SimpleNamespace(
            shots=SimpleNamespace(load_construct=lambda _: {"components": []}),
            normalize_cast_contexts=lambda _, contexts, **kwargs: contexts,
        ),
    )
    result = ReviewBuildManagerWindow._stage_input_overrides(window, identity)
    assert result["cast_contexts"] == {"JIN": "ANIM"}
