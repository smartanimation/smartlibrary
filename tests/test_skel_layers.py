from pathlib import Path
import pytest
from pxr import Usd, UsdGeom, UsdSkel, Sdf, Gf
from smartlib.core.skel_layers import split_skel_layers


def source(tmp_path):
    s = Usd.Stage.CreateNew(str(tmp_path / 'source.usda'))
    root = UsdSkel.Root.Define(s, '/Asset').GetPrim()
    s.SetDefaultPrim(root)
    mesh = UsdGeom.Mesh.Define(s, '/Asset/Geometry/body')
    mesh.CreatePointsAttr([(0,0,0),(1,0,0),(0,1,0)])
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0,1,2])
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex').Set([(0,0),(1,0),(0,1)])
    skel = UsdSkel.Skeleton.Define(s, '/Asset/Skeleton')
    skel.CreateJointsAttr(['root'])
    skel.CreateBindTransformsAttr([Gf.Matrix4d(1)])
    skel.CreateRestTransformsAttr([Gf.Matrix4d(1)])
    binding = UsdSkel.BindingAPI.Apply(mesh.GetPrim())
    binding.CreateSkeletonRel().SetTargets([skel.GetPath()])
    binding.CreateJointIndicesPrimvar(False, 1).Set([0,0,0])
    binding.CreateJointWeightsPrimvar(False, 1).Set([1,1,1])
    shape = UsdSkel.BlendShape.Define(s, '/Asset/Shapes/smile')
    shape.CreateOffsetsAttr([(0,0,.1)])
    shape.CreatePointIndicesAttr([0])
    binding.CreateBlendShapesAttr(['smile'])
    binding.CreateBlendShapeTargetsRel().SetTargets([shape.GetPath()])
    s.GetRootLayer().Save()
    return s


def test_split_preserves_mesh_binding_and_blendshape_without_duplicate_geometry(tmp_path):
    s = source(tmp_path)
    before = s.GetRootLayer().ExportToString()
    geo, rig, entry = [tmp_path / n for n in ('geo.usd','rig.usd','usdSkel.usd')]
    split_skel_layers(s.GetRootLayer().identifier, geometry_path=geo, rig_path=rig, entry_path=entry)
    g, r, combined = [Usd.Stage.Open(str(p)) for p in (geo, rig, entry)]
    assert not any(p.IsA(UsdSkel.Skeleton) for p in g.Traverse())
    assert not r.GetRootLayer().GetAttributeAtPath('/Asset/Geometry/body.points')
    assert not r.GetRootLayer().GetAttributeAtPath('/Asset/Geometry/body.primvars:st')
    assert r.GetRootLayer().GetPrimAtPath('/Asset/Geometry/body').specifier == Sdf.SpecifierOver
    assert not g.GetRootLayer().GetAttributeAtPath('/Asset/Geometry/body.primvars:skel:jointWeights')
    assert combined.GetPrimAtPath('/Asset').IsA(UsdSkel.Root)
    assert UsdSkel.BindingAPI(combined.GetPrimAtPath('/Asset/Geometry/body')).GetSkeletonRel().GetTargets() == [Sdf.Path('/Asset/Skeleton')]
    for p in s.Traverse():
        for a in p.GetAttributes():
            assert combined.GetAttributeAtPath(a.GetPath()).Get() == a.Get()
    assert before == s.GetRootLayer().ExportToString()


def test_animated_source_rejected(tmp_path):
    s = source(tmp_path)
    s.GetAttributeAtPath('/Asset/Geometry/body.points').Set([(0,0,0)] * 3, 1)
    s.GetRootLayer().Save()
    with pytest.raises(ValueError, match='time samples'):
        split_skel_layers(s.GetRootLayer().identifier, geometry_path=tmp_path/'g.usd', rig_path=tmp_path/'r.usd', entry_path=tmp_path/'e.usd')


def test_publish_validation_rejects_mesh_without_points(tmp_path):
    from smartlib.dcc.maya.usd_skel import _validate_published_usd
    s = source(tmp_path)
    s.GetPrimAtPath('/Asset/Geometry/body').RemoveProperty('points')
    s.GetRootLayer().Save()
    result = _validate_published_usd(Path(s.GetRootLayer().identifier), expected_skinned_mesh_count=1)
    assert result['status'] == 'ERROR'
    assert any('points/topology' in issue for issue in result['issues'])
