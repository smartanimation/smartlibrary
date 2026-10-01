from pathlib import Path
import pytest
from pxr import Usd, UsdGeom, UsdSkel, Gf, UsdShade
from test_usd_handoff import service
from test_preview_release import inputs, rig_input
from test_animation_batch import animation_plan, export
from smartlib.apps.asset_manager.preview_release import release_preview
from smartlib.apps.shot_manager.assets_publish import AssetsPublishService
from smartlib.core.metadata import read_json


@pytest.mark.parametrize('skeletal', [True, False])
def test_cast_release_animation_single_geometry_provider(service, tmp_path, monkeypatch, skeletal):
    svc, shot = service
    monkeypatch.setattr(svc.shots, 'shot_frame_range', lambda _: [1,2])
    publisher, identity, geo, look = inputs(service, tmp_path)
    rig = rig_input(publisher, identity, tmp_path)
    release = release_preview(publisher, identity, geo, look, rig_manifest=rig)
    assets = AssetsPublishService(svc.shots)
    cast_plan = assets.registration_plan(shot, [dict(target='Hero', asset=identity,
        version='v001', quality='proxy', geometry_source='asset')])
    cast_plan['frame_range'] = [1,2]
    base = svc.publish(shot, cast_plan)
    cast_ref = svc.load_handoff(base)['products'][0]
    original = animation_plan(svc, shot, tmp_path, ['Hero'])['rows'][0]
    monkeypatch.setattr(svc, 'skel_versions', lambda *a: [dict(path=str(rig))])
    plan = svc.plan(shot, [dict(kind='animation', target='Hero', source=original['source']['path'],
        rig=original['rig']['path'], cast_asset=cast_ref)], frame_range=[1,2])
    assert plan['rows'][0]['skel_entry']['path'] == read_json(release,{})['absolute_files']['usd'].replace('\\','/')
    def exporter(row, plan, output):
        export(row, plan, output)
        if not skeletal:
            return dict(ok=True, representation='deform', fallback_reason='unsupported deformation')
        path = output.with_name('anim.usd')
        stage = Usd.Stage.CreateNew(str(path))
        anim = UsdSkel.Animation.Define(stage, '/Asset/Skeleton/Animation')
        anim.CreateJointsAttr(['root'])
        for frame in [1,2]:
            anim.CreateTranslationsAttr().Set([(frame,0,0)],frame)
            anim.CreateRotationsAttr().Set([Gf.Quatf(1)],frame)
            anim.CreateScalesAttr().Set([(1,1,1)],frame)
        stage.GetRootLayer().Save()
        return dict(ok=True, representation='usd_skel_animation', animation=str(path),
            comparison=dict(ok=True,frames_checked=2), bindings=[dict(target_skeleton='/Asset/Skeleton', animation_source='/Asset/Skeleton/Animation')])
    result = svc.publish(shot, plan, animation_exporter=exporter, base_composition=svc.pin(base))
    record = svc.load_handoff(result)
    stage = Usd.Stage.Open(record['entrypoint']['path'])
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    assert len(meshes) == 1
    assert str(meshes[0].GetPath()).startswith('/Shot/Assets/Hero/')
    assert UsdShade.MaterialBindingAPI(meshes[0]).ComputeBoundMaterial()[0]
    assert not stage.GetPrimAtPath('/Shot/Animation/Hero')
    if skeletal:
        animation_only = Usd.Stage.Open(record['layers']['animation']['path'])
        assert not any(p.IsA(UsdGeom.Mesh) for p in animation_only.Traverse())
        baked = Usd.Stage.Open(stage.Flatten())
        root = baked.GetPrimAtPath('/Shot/Assets/Hero')
        assert UsdSkel.BakeSkinning(Usd.PrimRange(root), Gf.Interval(1,2))
        body = next(p for p in baked.Traverse() if p.IsA(UsdGeom.Mesh))
        assert body.GetAttribute('points').Get(2)[0][0] == pytest.approx(2)
    products = [svc.load_handoff(ref['path']) for ref in record['products']]
    product = next(p for p in products if p['kind']=='animation')
    assert product['inputs']['asset_release'] == svc.pin(release)
    from smartlib.apps.smart_composition.service import CompositionSession
    session = CompositionSession(svc.shots, result)
    manifests = [r['path'] for r in record['products']]
    animation_manifest = next(r['path'] for r, p in zip(record['products'], products) if p['kind'] == 'animation')
    preview, layers = session.preview(manifests, {animation_manifest: ''})
    mesh = next(p for p in preview.Traverse() if p.IsA(UsdGeom.Mesh))
    assert not UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0]
    saved = session.save(manifests, {animation_manifest: ''})
    saved_stage = Usd.Stage.Open(svc.load_handoff(saved)['entrypoint']['path'])
    saved_mesh = next(p for p in saved_stage.Traverse() if p.IsA(UsdGeom.Mesh))
    assert not UsdShade.MaterialBindingAPI(saved_mesh).ComputeBoundMaterial()[0]
    if skeletal:
        assert saved_mesh.HasAPI(UsdSkel.BindingAPI)
    from smartlib.apps.shot_manager.usd_handoff import compose_layers
    changed = next(p for p in products if p['kind']=='assets')
    changed['inputs']['asset_release'] = dict(path='other',sha256='other')
    with pytest.raises(ValueError, match='Cast Release differs'):
        compose_layers(dict.fromkeys(('shot','animation','assets','layout','camera')),products,plan,svc,in_memory=True)
