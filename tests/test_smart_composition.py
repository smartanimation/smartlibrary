from pathlib import Path

import pytest
from test_usd_handoff import service, asset, plan
from smartlib.apps.smart_composition.service import CompositionSession
from smartlib.core.metadata import read_json


def test_late_look_preview_save_reopen_and_disable(service, tmp_path):
    from pxr import UsdShade
    from smartlib.core.metadata import write_json
    from test_preview_look import fixture_files
    svc, identity = service
    geo, look = fixture_files(tmp_path)
    version, directory = svc._reserve(svc.paths.usd_handoff_dir(*svc._identity(identity), 'animation', 'DLI'))
    product = svc._file(directory, 'manifest.json')
    write_json(product, dict(schema='smartpipeline.usd_handoff_product.v1', status='published',
        kind='animation', target='DLI', version=version, shot=dict(episode='ep01',sequence='sq01',shot='sh001'),
        frame_range=[1,2], fps=24, usd=dict(meters_per_unit=.01,up_axis='Y'), inputs={}, dependencies=[],
        entrypoint=svc.pin(geo.GetRootLayer().identifier)))
    original = product.read_bytes()
    deform = Path(geo.GetRootLayer().identifier).read_bytes()
    composition = svc.compose_products(identity, [product])
    session = CompositionSession(svc.shots, composition)
    selected = list(session.initial.values())
    looks = {selected[0]: look.GetRootLayer().identifier}
    stage, _ = session.preview(selected, looks)
    assert UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/Shot/Animation/DLI/body')).ComputeBoundMaterial()[0]
    assert product.read_bytes() == original
    with pytest.raises(ValueError, match='Look selection'):
        session.save(selected, {})
    saved = session.save(selected, looks)
    reopened = CompositionSession(svc.shots, saved)
    new_products = list(reopened.initial.values())
    stage, _ = reopened.preview(new_products)
    assert UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/Shot/Animation/DLI/body')).ComputeBoundMaterial()[0]
    off = {new_products[0]: ''}
    stage, _ = reopened.preview(new_products, off)
    assert not UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/Shot/Animation/DLI/body')).ComputeBoundMaterial()[0]
    disabled = reopened.save(new_products, off)
    final = CompositionSession(svc.shots, disabled)
    stage, _ = final.preview(list(final.initial.values()))
    assert not UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/Shot/Animation/DLI/body')).ComputeBoundMaterial()[0]
    assert product.read_bytes() == original
    assert Path(geo.GetRootLayer().identifier).read_bytes() == deform


def test_preview_switch_save_and_source_immutable(service, tmp_path):
    from pxr import Usd, UsdGeom
    svc, identity = service
    first = asset(tmp_path, 'first')
    second = asset(tmp_path, 'second')
    stage = Usd.Stage.Open(str(second))
    UsdGeom.Cube(stage.GetPrimAtPath('/Root/geo')).GetSizeAttr().Set(8)
    stage.GetRootLayer().Save()
    snapshots = []
    for source in (first, second):
        snapshots.append(svc.publish(identity, plan(svc, identity,
            [dict(kind='assets', target='BG', source=str(source))])))
    session = CompositionSession(svc.shots, snapshots[0])
    versions = session.versions()[('assets', 'BG')]
    assert [v for v, _ in versions] == ['v002', 'v001']
    original = {p: p.read_bytes() for p in snapshots[0].parent.iterdir() if p.is_file()}
    root = svc.paths.composition_dir(*svc._identity(identity), 'usd')
    before = set(root.iterdir())
    preview, layers = session.preview([versions[0][1]])
    assert preview.GetRootLayer().anonymous
    assert all(s.GetRootLayer().anonymous for s in layers.values())
    assert UsdGeom.Cube(preview.GetPrimAtPath('/Shot/Assets/BG/geo')).GetSizeAttr().Get() == 8
    assert set(root.iterdir()) == before
    old, old_layers = session.preview([versions[1][1]])
    assert UsdGeom.Cube(old.GetPrimAtPath('/Shot/Assets/BG/geo')).GetSizeAttr().Get() == 2
    session.preview([versions[0][1]])
    saved = session.save([versions[0][1]])
    data = svc.load_handoff(saved)
    assert data['version'] == 'v003'
    assert data['products'][0]['path'] == versions[0][1]
    assert UsdGeom.Cube(Usd.Stage.Open(data['entrypoint']['path']).GetPrimAtPath('/Shot/Assets/BG/geo')).GetSizeAttr().Get() == 8
    assert all(p.read_bytes() == contents for p, contents in original.items())
    reopened = CompositionSession(svc.shots, data['entrypoint']['path'])
    assert reopened.initial[('assets', 'BG')] == versions[0][1]


def test_preview_rejects_empty_duplicate_and_changed_inputs(service, tmp_path):
    svc, identity = service
    source = asset(tmp_path)
    snapshot = svc.publish(identity, plan(svc, identity,
        [dict(kind='assets', target='BG', source=str(source))]))
    session = CompositionSession(svc.shots, snapshot)
    selected = list(session.initial.values())
    with pytest.raises(ValueError, match='Select published'):
        session.preview([])
    with pytest.raises(ValueError, match='one version'):
        session.preview(selected * 2)
    source.write_text(source.read_text() + '\n# changed\n')
    with pytest.raises(ValueError, match='changed'):
        session.preview(selected)


def test_disabled_product_is_absent(service, tmp_path):
    svc, identity = service
    bg, cam = asset(tmp_path), asset(tmp_path, 'camera')
    snapshot = svc.publish(identity, plan(svc, identity, [
        dict(kind='assets', target='BG', source=str(bg)),
        dict(kind='camera', target='primary', source=str(cam))]))
    session = CompositionSession(svc.shots, snapshot)
    stage, layers = session.preview([session.initial[('assets', 'BG')]])
    assert stage.GetPrimAtPath('/Shot/Assets/BG')
    assert not stage.GetPrimAtPath('/Shot/Camera/primary')


def test_save_requires_preview_and_pins_manifest(service, tmp_path):
    svc, identity = service
    source = asset(tmp_path)
    snapshot = svc.publish(identity, plan(svc, identity,
        [dict(kind='assets', target='BG', source=str(source))]))
    session = CompositionSession(svc.shots, snapshot)
    selected = list(session.initial.values())
    with pytest.raises(ValueError, match='Preview'):
        session.save(selected)
    session.preview(selected)
    manifest = Path(selected[0])
    manifest.write_text(manifest.read_text() + '\n')
    with pytest.raises(ValueError, match='changed'):
        session.save(selected)
