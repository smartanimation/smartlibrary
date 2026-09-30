import json
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from smartlib.core.path_resolver import ProjectPaths
from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow as Window, QtWidgets
from smartlib.apps.review_build_manager.service import ReviewBuildManagerService


@pytest.mark.parametrize('kind', ['animation_curve', 'camera', 'light', 'audio',
                                 'editorial_timing', 'placement', 'set_dress',
                                 'review_layers', 'layout_overlay', 'virtual_camera', 'rig', 'usd'])
@pytest.mark.parametrize('planned', [True, False])
def test_input_selection_is_independent_and_reaches_construct(tmp_path, kind, planned):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    paths = []
    for version in ['v001', 'v007']:
        path = tmp_path / version / 'input.json'
        path.parent.mkdir()
        path.write_text('{}')
        paths.append(str(path))
    row = dict(type=kind, cast_key='main', enabled=False,
               component=dict(component_type=kind, name='main', enabled=False,
                              path=paths[0], version='v001'),
               input_versions=[dict(version='v001', path=paths[0]), dict(version='v007', path=paths[1])])
    saved_settings = {}
    class Harness(QtWidgets.QWidget):
        _use_key = staticmethod(Window._use_key)
        _input_version_changed = Window._input_version_changed
        _restore_input_version = staticmethod(Window._restore_input_version)
        _commit_input_choices = Window._commit_input_choices
        def _selected_status(self): return SimpleNamespace(identity='shot')
        def _planned_snapshot_key(self, identity): return 'shot'
        def _settings(self): return SimpleNamespace(setValue=lambda k, v: saved_settings.update({k: v}),
                                                   value=lambda k, default: saved_settings.get(k, default))
        def _planned_snapshot_payload(self, identity):
            return deepcopy(self._planned_snapshots['shot'])
        def _populate_build_contents(self, status): pass
        def _populate_planned_snapshot(self, status): pass
    w = Harness()
    w.current_build_content_rows = [row]
    w._build_versions = {}
    w._build_plan_cache = {}
    w._planned_snapshots = {'shot': {'inputs': [dict(type=kind, name='main', enabled=False,
                                                   path=paths[0], version='v001')]}}
    table = QtWidgets.QTableWidget(1, 1, w)
    Window._input_version_combo(w, table, 0, 0, 0, row, planned)
    combo = table.cellWidget(0, 0)
    combo.setCurrentIndex(combo.findData(paths[1]))
    combo.activated.emit(combo.currentIndex())
    assert saved_settings == {}
    if planned:
        assert row['component']['path'] == paths[0]
        assert not w._build_versions
        w._commit_input_choices('shot', w._planned_snapshots['shot'])
        payload = json.loads(saved_settings['planned_snapshots'])['shot']
    else:
        assert w._planned_snapshots['shot']['inputs'][0]['path'] == paths[0]
        w._commit_input_choices('shot')
        payload = {'inputs': list(json.loads(saved_settings['build_versions'])['shot'].values())}
    result = Window._apply_planned_snapshot_to_construct(
        {'components': [dict(row['component'], component_type='camera' if kind == 'virtual_camera' else kind)]}, payload)
    assert result['components'][0]['path'] == paths[1]
    assert result['components'][0]['version'] == 'v007'
    assert result['components'][0]['enabled'] is False
    w.close()


def test_version_resolver_does_not_cross_families_and_handles_filename_tokens(tmp_path):
    for version in ['v001', 'v010', 'v002']:
        path = tmp_path / 'hero' / version / f'hero_{version}.fbx'
        path.parent.mkdir(parents=True)
        path.touch()
    other = tmp_path / 'other' / 'v099' / 'hero_v099.fbx'
    other.parent.mkdir(parents=True)
    other.touch()
    versions = ProjectPaths.artifact_version_files(tmp_path / 'hero' / 'v001' / 'hero_v001.fbx')
    assert [v for v, p in versions] == ['v010', 'v002', 'v001']
    assert ProjectPaths.artifact_version_files(tmp_path / 'work.json') == []


def test_explicit_camera_selection_stays_in_published_family(tmp_path):
    for version in ['v001', 'v002']:
        path = tmp_path / 'publish' / version / 'camera.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}')
    old, current = [str(tmp_path / 'publish' / v / 'camera.json') for v in ['v001', 'v002']]
    component = dict(component_type='camera', name='cam', path=current, version='v002',
                     source={'camera_batch': True})
    selection = dict(type='camera', name='cam', path=old, version='v001', version_selected=True)
    result = Window._apply_planned_snapshot_to_construct({'components': [component]}, {'inputs': [selection]})
    assert result['components'][0]['path'] == old
    selection['path'] = str(tmp_path / 'data' / 'v001' / 'camera.json')
    result = Window._apply_planned_snapshot_to_construct({'components': [component]}, {'inputs': [selection]})
    assert result['components'][0]['path'] == current
