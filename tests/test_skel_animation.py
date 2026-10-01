from pathlib import Path
import pytest
from pxr import Usd, UsdSkel, UsdGeom, Gf
from test_skel_layers import source
from smartlib.core.skel_animation import compare_deformation, compose_animation


def moving_pair(tmp_path):
    stage = source(tmp_path)
    UsdGeom.Xform.Define(stage, '/Asset/Geometry')
    UsdSkel.BindingAPI(stage.GetPrimAtPath('/Asset/Geometry/body')).CreateGeomBindTransformAttr(Gf.Matrix4d(1))
    animation = UsdSkel.Animation.Define(stage, '/Asset/Skeleton/Animation')
    animation.CreateJointsAttr(['root'])
    for frame in (1, 2):
        animation.CreateTranslationsAttr().Set([(frame * 3, 0, 0)], frame)
        animation.CreateRotationsAttr().Set([Gf.Quatf(1)], frame)
        animation.CreateScalesAttr().Set([(1, 1, 1)], frame)
    UsdSkel.BindingAPI.Apply(stage.GetPrimAtPath('/Asset/Skeleton')).CreateAnimationSourceRel().SetTargets([animation.GetPath()])
    stage.GetRootLayer().Save()
    baked = Usd.Stage.Open(stage.Flatten())
    assert UsdSkel.BakeSkinning(Usd.PrimRange(baked.GetDefaultPrim()), Gf.Interval(1, 2))
    reference = tmp_path / 'deform.usdc'
    baked.GetRootLayer().Export(str(reference))
    return stage, reference


def test_comparison_all_frames_and_no_input_mutation(tmp_path):
    stage, reference = moving_pair(tmp_path)
    path = Path(stage.GetRootLayer().identifier)
    before = path.read_bytes(), reference.read_bytes()
    result = compare_deformation(path, reference, [1, 2])
    assert result['ok'] and result['frames_checked'] == 2
    assert result['max_error_cm'] == 0
    assert before == (path.read_bytes(), reference.read_bytes())
    stage.GetAttributeAtPath('/Asset/Skeleton/Animation.translations').Set([(99, 0, 0)], 2)
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError, match='frame 2'):
        compare_deformation(path, reference, [1, 2])


def test_missing_mesh_is_not_a_success(tmp_path):
    stage, reference = moving_pair(tmp_path)
    stage.RemovePrim('/Asset/Geometry/body')
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError, match='mesh sets'):
        compare_deformation(stage.GetRootLayer().identifier, reference, [1, 2])


def test_skeletal_bounds_follow_deformation_without_baking_published_points(tmp_path):
    from smartlib.core.skel_extents import author_skel_extents
    source_stage, reference = moving_pair(tmp_path)
    before = Path(source_stage.GetRootLayer().identifier).read_bytes()
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim('/Character').GetReferences().AddReference(source_stage.GetRootLayer().identifier, '/Asset')
    author_skel_extents(stage, [1, 2])
    mesh = stage.GetPrimAtPath('/Character/Geometry/body')
    for frame in (1, 2):
        extent = mesh.GetAttribute('extent').Get(frame)
        assert extent[0][0] == pytest.approx(frame * 3)
        assert extent[1][0] == pytest.approx(frame * 3 + 1)
        assert stage.GetPrimAtPath('/Character').GetAttribute('extent').Get(frame) == extent
    assert not stage.GetRootLayer().GetAttributeAtPath('/Character/Geometry/body.points')
    assert Path(source_stage.GetRootLayer().identifier).read_bytes() == before


def test_handoff_promotes_animation_and_resolves_static_layers(service, tmp_path, monkeypatch):
    from test_animation_batch import animation_plan, export
    from smartlib.core.metadata import write_json
    from smartlib.core.skel_layers import split_skel_layers
    svc, identity = service
    root = tmp_path / 'rig' / 'v001'
    root.mkdir(parents=True)
    stage = source(root)
    geo, rig, entry = [root / p for p in ('geo.usd', 'rig.usd', 'usdSkel.usd')]
    split_skel_layers(stage.GetRootLayer().identifier, geometry_path=geo, rig_path=rig, entry_path=entry)
    manifest = write_json(root/'publish.json', {'usd_skel': {'entry': entry.name}})
    monkeypatch.setattr(svc, 'skel_versions', lambda *a: [dict(path=str(manifest))])
    original = animation_plan(svc, identity, tmp_path, ['Hero'])['rows'][0]
    plan = svc.plan(identity, [dict(kind='animation', target='Hero', source=original['source']['path'],
                                   rig=original['rig']['path'], skel=str(manifest))], frame_range=[1, 2], fps=24)
    svc.validate_plan(plan, identity)
    def exporter(row, plan, output):
        export(row, plan, output)
        animation = svc.paths.artifact_file(output.parent, 'sample.animation.usd')
        st = Usd.Stage.CreateNew(str(animation))
        a = UsdSkel.Animation.Define(st, '/Asset/Skeleton/Animation')
        a.CreateJointsAttr(['root'])
        a.CreateTranslationsAttr().Set([(3, 0, 0)], 1)
        a.CreateRotationsAttr().Set([Gf.Quatf(1)], 1)
        a.CreateScalesAttr().Set([(1, 1, 1)], 1)
        st.GetRootLayer().Save()
        return dict(ok=True, representation='usd_skel_animation', animation=str(animation),
                    comparison=dict(ok=True, frames_checked=2),
                    bindings=[dict(target_skeleton='/Asset/Skeleton', animation_source='/Asset/Skeleton/Animation')])
    receipt = svc.load_handoff(svc.publish(identity, plan, animation_exporter=exporter))
    product = svc.load_handoff(receipt['products'][0]['path'])
    assert product['usd_kind'] == 'usd_skel_animation'
    assert Path(product['entrypoint']['path']).name == 'animation_asset.usda'
    result = Usd.Stage.Open(product['entrypoint']['path'])
    assert result.GetPrimAtPath('/Asset/Geometry/body').HasAPI(UsdSkel.BindingAPI)
    assert result.GetPrimAtPath('/Asset/Skeleton/Animation').IsA(UsdSkel.Animation)
    assert not any('.build' in layer.realPath for layer in result.GetUsedLayers() if not layer.anonymous)
    edited = Usd.Stage.Open(str(geo))
    edited.GetAttributeAtPath('/Asset/Geometry/body.points').Set([(0, 0, 0), (2, 0, 0), (0, 2, 0)])
    edited.GetRootLayer().Save()
    with pytest.raises(ValueError, match='changed'):
        svc.validate_plan(plan, identity)


from test_usd_handoff import service
