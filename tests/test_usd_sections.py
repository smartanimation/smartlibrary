from pathlib import Path

import pytest
from test_usd_handoff import service, asset, plan
from smartlib.apps.shot_manager.usd_handoff import compose_layers
from smartlib.apps.smart_composition.service import CompositionSession
from smartlib.core.metadata import read_json, write_json


def test_camera_update_reuses_assets_and_only_writes_shot_in_composition(service, tmp_path):
    from pxr import Usd
    svc, identity = service
    bg, cam = asset(tmp_path), asset(tmp_path, 'camera')
    first = svc.publish(identity, plan(svc, identity, [
        dict(kind='assets', target='BG', source=str(bg)),
        dict(kind='camera', target='primary', source=str(cam))]))
    before = svc.load_handoff(first)
    original = {p: p.read_bytes() for p in first.parent.iterdir()}
    second = svc.publish(identity, plan(svc, identity, [
        dict(kind='camera', target='primary', source=str(cam))]), base_composition=svc.pin(first))
    after = svc.load_handoff(second)
    assert set(p.name for p in second.parent.iterdir()) == {'shot.usda', 'manifest.json'}
    assert set(after['sections']) == {'assets', 'camera'}
    assert after['sections']['assets'] == before['sections']['assets']
    assert after['sections']['camera'] != before['sections']['camera']
    assert svc.load_handoff(after['sections']['camera']['path'])['version'] == 'v002'
    assert not svc.paths.usd_section_dir(*svc._identity(identity), 'animation').exists()
    assert all(p.read_bytes() == value for p, value in original.items())
    stage = Usd.Stage.Open(after['entrypoint']['path'])
    assert stage.GetPrimAtPath('/Shot/Assets/BG/geo')
    assert all('/sections/' in path for path in stage.GetRootLayer().subLayerPaths)
    # Re-compose the same products: no new section version.
    again = svc.load_handoff(svc.compose_products(identity, [r['path'] for r in after['products']]))
    assert again['sections'] == after['sections']


def test_legacy_composition_remains_readable_and_migrates_on_new_save(service, tmp_path):
    from smartlib.apps.shot_manager.usd_handoff import COMPOSITION
    svc, identity = service
    bg = asset(tmp_path)
    current = svc.load_handoff(svc.publish(identity, plan(svc, identity,
        [dict(kind='assets', target='BG', source=str(bg))])))
    products = [svc.load_handoff(ref['path']) for ref in current['products']]
    version, directory = svc._reserve(svc.paths.composition_dir(*svc._identity(identity), 'usd'))
    paths = {k: svc._file(directory, k + '.usda') for k in ('shot', 'animation', 'assets', 'camera', 'layout')}
    deps = compose_layers(paths, products, current, svc)
    legacy = dict(current, version=version, entrypoint=svc.pin(paths['shot']),
                  layers={k: svc.pin(p) for k, p in paths.items()}, dependencies=deps)
    legacy.pop('sections')
    manifest = svc._file(directory, 'manifest.json')
    write_json(manifest, legacy)
    original = {p: p.read_bytes() for p in directory.iterdir()}
    session = CompositionSession(svc.shots, manifest, sections=True)
    assert not session.section_mode
    selected = list(session.initial.values())
    session.preview(selected)
    result = svc.load_handoff(session.save(selected))
    assert result['sections'] == current['sections']
    assert all(p.read_bytes() == value for p, value in original.items())


def test_smart_composition_selects_section_versions_and_reuses_them(service, tmp_path):
    from pxr import UsdGeom
    svc, identity = service
    a, b = asset(tmp_path, 'one'), asset(tmp_path, 'two')
    snapshots = [svc.publish(identity, plan(svc, identity,
        [dict(kind='assets', target='BG', source=str(source))])) for source in (a, b)]
    session = CompositionSession(svc.shots, snapshots[0], sections=True)
    assert session.section_mode
    choices = session.versions()[('assets', 'Section')]
    assert [v for v, _ in choices] == ['v002', 'v001']
    selected = [choices[0][1]]
    stage, layers = session.preview(selected)
    assert stage.GetPrimAtPath('/Shot/Assets/BG/geo')
    result = svc.load_handoff(session.save(selected))
    assert result['sections'] == svc.load_handoff(snapshots[1])['sections']
    with pytest.raises(ValueError, match='one version'):
        session.preview(selected * 2)


def test_changed_section_rejected(service, tmp_path):
    svc, identity = service
    bg = asset(tmp_path)
    snapshot = svc.load_handoff(svc.publish(identity, plan(svc, identity,
        [dict(kind='assets', target='BG', source=str(bg))])))
    section = svc.load_handoff(snapshot['sections']['assets']['path'])
    file = Path(section['entrypoint']['path'])
    file.write_text(file.read_text() + '\n# modified\n')
    with pytest.raises(ValueError, match='changed'):
        svc.load_handoff(svc.paths.artifact_file(Path(snapshot['entrypoint']['path']).parent, 'manifest.json'))


def test_failed_layout_does_not_publish_sections(service, tmp_path):
    from smartlib.dcc.maya.set_dress import SetDressPackage, SetDressLayer, Change
    svc, identity = service
    source = tmp_path / 'layout' / 'v001' / 'layout.json'
    write_json(source, SetDressPackage(layers=[SetDressLayer(changes=[
        Change('id', 'node', 'translateX', 0, 5)])]).to_dict())
    selected = plan(svc, identity, [dict(kind='layout', target='main', source=str(source),
                                       node_map={'id': '/Shot/Assets/Missing'})])
    with pytest.raises(ValueError, match='Missing Set Dress'):
        svc.publish(identity, selected)
    assert svc.section_versions(identity) == {}


def test_section_resolver_rejects_invalid_kind(service):
    svc, identity = service
    with pytest.raises(ValueError, match='Unsupported'):
        svc.paths.usd_section_dir(*svc._identity(identity), '../escape')
