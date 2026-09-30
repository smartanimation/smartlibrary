from pathlib import Path
from types import SimpleNamespace
import pytest
from test_usd_handoff import service, asset, plan
from test_animation_publish_panel import panel
from smartlib.core.metadata import write_json, read_json


def animation_plan(svc, identity, tmp_path, targets):
    directory = tmp_path / 'data' / 'v001'
    directory.mkdir(parents=True, exist_ok=True)
    rig, atom = directory / 'rig.ma', directory / 'animation.atom'
    rig.write_text('rig')
    atom.write_text('atom')
    data = write_json(directory / 'animation_manifest.json', dict(schema='smartpipeline.animation_atom.v3',
        payload=atom.name, payload_sha256=svc.pin(atom)['sha256'],
        rig_dependencies=[svc.pin(rig)], frame_range=[1, 2]))
    return plan(svc, identity, [dict(kind='animation', target=t, source=str(data), rig=str(rig)) for t in targets])


def export(row, plan, output):
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.CreateNew(str(output))
    root = UsdGeom.Xform.Define(stage, '/Geometry')
    stage.SetDefaultPrim(root.GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, '/Geometry/body')
    mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
    UsdGeom.SetStageUpAxis(stage, 'Y')
    UsdGeom.SetStageMetersPerUnit(stage, .01)
    stage.GetRootLayer().Save()
    return {'ok': True}


def test_sequential_and_batch_keep_unselected_products(service, tmp_path):
    from pxr import Usd
    svc, identity = service
    bg, cam = asset(tmp_path), asset(tmp_path, 'camera')
    base = svc.publish(identity, plan(svc, identity, [
        dict(kind='assets', target='BG', source=str(bg)), dict(kind='camera', target='primary', source=str(cam))]))
    for targets in (['Hero'], ['Friend'], ['Hero', 'Third']):
        base = svc.publish(identity, animation_plan(svc, identity, tmp_path, targets),
                           animation_exporter=export, base_composition=svc.pin(base))
    result = svc.load_handoff(base)
    assert set(map(tuple, result['included_targets'])) == {
        ('assets', 'BG'), ('camera', 'primary'), ('animation', 'Hero'),
        ('animation', 'Friend'), ('animation', 'Third')}
    stage = Usd.Stage.Open(result['entrypoint']['path'])
    for target in ('Hero', 'Friend', 'Third'):
        assert stage.GetPrimAtPath('/Shot/Animation/' + target + '/body')


def test_failed_second_target_keeps_composition_and_sections(service, tmp_path):
    svc, identity = service
    first = svc.publish(identity, animation_plan(svc, identity, tmp_path, ['Hero']), animation_exporter=export)
    before = svc.composition_versions(identity)
    sections = svc.section_versions(identity)
    def fail_second(row, plan, output):
        if row['target'] == 'Friend':
            raise ValueError('Second target failed')
        return export(row, plan, output)
    with pytest.raises(ValueError, match='Second'):
        svc.publish(identity, animation_plan(svc, identity, tmp_path, ['Hero', 'Friend']),
                    animation_exporter=fail_second, base_composition=svc.pin(first))
    assert svc.composition_versions(identity) == before
    assert svc.section_versions(identity) == sections


def test_batch_ui_independent_rigs_and_one_scene_capture(panel, monkeypatch):
    widget, identity, versions = panel
    rigs = [{'version': v.version, 'path': str(Path(v.path).with_name('rig.ma'))} for v in versions]
    monkeypatch.setattr(widget.service, 'animation_rig_versions', lambda *a: {'ANIM': rigs})
    monkeypatch.setattr(widget.service.shots, 'load_cast', lambda _: {
        'cast': {t: {'asset': t, 'namespace': t} for t in ('Hero', 'Friend')}})
    widget.rig_version.setCurrentIndex(1)
    widget.set_targets(identity, ['Hero', 'Friend'])
    assert widget.batch_settings.selections()[0]['rig'] == rigs[1]['path']
    assert widget.batch_settings.selections()[1]['rig'] == rigs[0]['path']
    from smartlib.dcc.maya import publish_scene_input
    from smartlib.apps.review_build_manager import publish_queue
    captures, calls = [], []
    monkeypatch.setattr(publish_scene_input, 'capture_saved_scene',
                        lambda *a: captures.append(True) or {'fixed': True})
    class Queue:
        changed = SimpleNamespace(connect=lambda f: None)
        jobs = {'job': {'state': 'QUEUED'}}
        def submit(self, ident, **kwargs):
            calls.append(kwargs)
            return 'job'
    monkeypatch.setattr(publish_queue, 'get_queue', lambda _: Queue())
    widget.publish()
    assert captures == [True]
    assert calls[0]['merge_animation']
    assert [row['target'] for row in calls[0]['scene_options']['targets']] == ['Hero', 'Friend']
    assert calls[0]['frame_range'] == [278, 411]
