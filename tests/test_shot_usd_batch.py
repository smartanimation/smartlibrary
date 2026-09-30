from pathlib import Path

import pytest

from test_usd_handoff import service, asset, plan
from smartlib.core.metadata import write_json
from smartlib.dcc.maya.set_dress import SetDressPackage, SetDressLayer, Change


def camera_rows(svc, tmp_path):
    cam = asset(tmp_path, 'camera')
    snapshot = write_json(tmp_path / 'native' / 'v001' / 'camera.json', {'role': 'primary'})
    ref = svc.pin(snapshot)
    return [dict(kind='camera', target=name, source=str(cam), camera_role=role,
                 camera_snapshot=ref, primary_source=ref, primary_fingerprint='abc')
            for name, role in [('primary', 'primary'), ('smartCam_CHA', 'derived'), ('smartCam_BGA', 'derived')]]


def test_camera_batch_partial_update_and_primary_replacement(service, tmp_path):
    svc, identity = service
    rows = camera_rows(svc, tmp_path)
    first = svc.publish(identity, plan(svc, identity, rows))
    old = Path(first).read_bytes()
    second = svc.publish(identity, plan(svc, identity, [rows[1]]), base_composition=svc.pin(first))
    data = svc.load_handoff(second)
    products = {svc.load_handoff(p['path'])['target']: svc.load_handoff(p['path']) for p in data['products']}
    assert products['primary']['version'] == 'v001'
    assert products['smartCam_CHA']['version'] == 'v002'
    assert products['smartCam_BGA']['version'] == 'v001'
    assert Path(first).read_bytes() == old
    third = svc.publish(identity, plan(svc, identity, rows[:2]), base_composition=svc.pin(second))
    assert svc.load_handoff(third)['included_targets'] == [['camera', 'primary'], ['camera', 'smartCam_CHA']]


def test_camera_mismatched_primary_rejects_entire_batch(service, tmp_path):
    svc, identity = service
    rows = camera_rows(svc, tmp_path)
    first = svc.publish(identity, plan(svc, identity, rows))
    rows[1]['primary_fingerprint'] = 'changed'
    with pytest.raises(ValueError, match='does not match'):
        svc.publish(identity, plan(svc, identity, [rows[1]]), base_composition=svc.pin(first))
    assert len(svc.composition_versions(identity)) == 1
    with pytest.raises(ValueError, match='exactly one Primary'):
        svc.publish(identity, plan(svc, identity, [rows[1]]))


def placement_row(tmp_path, name='chair_place_loc', member='BG'):
    matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 12, 0, 0, 1]
    samples = dict(mode='STATIC', samples=[dict(frame=1., matrix=matrix)])
    source = write_json(tmp_path / name / 'v001' / 'placements.json',
        dict(schema='smartpipeline.placement_samples.v1', marker=samples,
             usd_placements=[dict(path='/Shot/Assets/' + member, data=samples)]))
    return dict(kind='layout', target='placement_' + name, source=str(source),
                layout_type='placement', layout_name=name)


def setdress_row(tmp_path, name, value, order=0):
    package = SetDressPackage(layers=[SetDressLayer(name=name, changes=[Change('node', 'geo', 'translateY', 0, value)])])
    source = write_json(tmp_path / name / 'v001' / 'layer.setdress.json', package.to_dict())
    return dict(kind='layout', target='setdress_' + name, source=str(source), layout_type='setdress',
                layout_name=name, layer_order=order, node_map={'node': '/Shot/Assets/BG/geo'})


def test_layout_groups_marker_override_order_and_reuse(service, tmp_path):
    from pxr import Usd, UsdGeom
    svc, identity = service
    base = asset(tmp_path)
    rows = [dict(kind='assets', target='BG', source=str(base)), placement_row(tmp_path),
            setdress_row(tmp_path, 'chair', 5), setdress_row(tmp_path, 'desk', 2, 1)]
    first = svc.publish(identity, plan(svc, identity, rows))
    data = svc.load_handoff(first)
    stage = Usd.Stage.Open(data['entrypoint']['path'])
    assert UsdGeom.XformCache(1).GetLocalToWorldTransform(stage.GetPrimAtPath('/Shot/Assets/BG/geo')).ExtractTranslation() == (12, 5, 0)
    assert stage.GetPrimAtPath('/Shot/Placements/chair_place_loc')
    layout = Path(data['layers']['layout']['path'])
    text = layout.read_text()
    assert 'placement.usda' in text and 'setdress.usda' in text
    assert (layout.parent / 'placement' / 'chair_place_loc.usd').is_file()
    assert (layout.parent / 'setdress' / 'chair.usd').is_file()
    for member in (layout.parent / 'placement' / 'chair_place_loc.usd',
                   layout.parent / 'setdress' / 'chair.usd'):
        static_stage = Usd.Stage.Open(str(member))
        assert all(attr.GetNumTimeSamples() == 0 for prim in static_stage.TraverseAll()
                   for attr in prim.GetAttributes())
    assert data['frame_range'] == [1, 2]
    second = svc.compose_products(identity, [p['path'] for p in data['products']])
    assert svc.load_handoff(second)['sections']['layout'] == data['sections']['layout']
    # Updating one layer preserves placement and the other layer, with stack order intact.
    third = svc.publish(identity, plan(svc, identity, [setdress_row(tmp_path, 'top', 9, -1)]), base_composition=svc.pin(first))
    stage = Usd.Stage.Open(svc.load_handoff(third)['entrypoint']['path'])
    assert UsdGeom.XformCache(1).GetLocalToWorldTransform(stage.GetPrimAtPath('/Shot/Assets/BG/geo')).ExtractTranslation() == (12, 9, 0)


def test_layout_failure_does_not_commit_composition(service, tmp_path):
    svc, identity = service
    with pytest.raises(ValueError, match='Missing.*Placement'):
        svc.publish(identity, plan(svc, identity, [placement_row(tmp_path)]))
    assert svc.composition_versions(identity) == []


def test_layout_panel_construction(service, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import QtWidgets
    from smartlib.apps.shot_manager.layout_publish_panel import LayoutPublishPanel
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc, identity = service
    panel = LayoutPublishPanel(svc.shots)
    monkeypatch.setattr(svc.shots, 'shot_frame_range', lambda _: (1, 2))
    panel.set_sources(identity, 'placements', ['chair', 'desk'])
    assert panel.target == 'chair, desk'
    assert panel.mapping.isHidden()
    panel.close()


def test_layout_data_discovery_keeps_setdress_schema(service, tmp_path):
    from smartlib.dcc.maya.layout_publish import _publish_data
    svc, identity = service
    payload = SetDressPackage(layers=[SetDressLayer(name='chair')]).to_dict()
    row = _publish_data(svc, identity, 'chair', 'setdress', payload, {'scene_input': {}})
    data = svc.shots.list_set_dress_data(identity)
    assert len(data) == 1 and data[0].version == 'v001' and data[0].latest
    assert data[0].name == 'set_dress_data/chair'
    assert Path(data[0].path) == Path(row['source'])


@pytest.mark.parametrize('category,targets,label,animated,static', [
    ('placements', ['chair'], 'Static', False, True),
    ('placements', ['desk'], 'Shot Range', True, False),
    ('placements', ['chair', 'desk'], 'Mixed — per Marker', True, True),
    ('set_dress', ['chair', 'desk'], 'Static', False, False),
])
def test_layout_output_follows_source_motion(service, monkeypatch, category, targets, label, animated, static):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import QtWidgets
    from smartlib.apps.shot_manager.layout_publish_panel import LayoutPublishPanel
    from smartlib.dcc.maya import shot_publish_sources
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc, identity = service
    monkeypatch.setattr(svc.shots, 'shot_frame_range', lambda _: (412, 631))
    sources = [dict(target='chair', motion='STATIC'), dict(target='desk', motion='CURVE')]
    monkeypatch.setattr(shot_publish_sources, 'placement_sources', lambda: sources)
    monkeypatch.setattr(LayoutPublishPanel, '_populate_mapping', lambda *_: None)
    panel = LayoutPublishPanel(svc.shots, is_maya_session=True)
    panel.set_sources(identity, category, targets)
    assert panel.range_mode.currentText() == label
    assert panel.range_mode.count() == 1 and not panel.range_mode.isEnabled()
    assert panel.start_frame.isHidden() is not animated
    assert panel.static_frame.isHidden() is not static
    assert not panel.static_frame.isEnabled()
    assert panel.static_frame.value() == 412
    submitted = []
    monkeypatch.setattr(panel, 'start_camera_export', lambda bounds: submitted.append(bounds))
    panel.publish()
    assert submitted == [[412, 631]]
    panel._busy = False
    sources[0]['motion'] = 'CURVE'
    panel.set_context(identity, panel.target, force=True)
    assert panel.range_mode.currentText() == ('Static' if category == 'set_dress' else 'Shot Range')
    panel.close()
