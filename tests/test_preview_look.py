from pathlib import Path
import pytest
from test_usd_handoff import service


def fixture_files(tmp_path):
    from pxr import Usd, UsdGeom, UsdShade, Sdf
    from smartlib.core.preview_look import mesh_fingerprint
    folder = tmp_path / 'v001'
    folder.mkdir()
    geo = Usd.Stage.CreateNew(str(folder / 'deform.usda'))
    root = UsdGeom.Xform.Define(geo, '/Geometry')
    geo.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageUpAxis(geo, 'Y')
    UsdGeom.SetStageMetersPerUnit(geo, .01)
    m = UsdGeom.Mesh.Define(geo, '/Geometry/body')
    m.CreatePointsAttr([(0,0,0),(1,0,0),(0,1,0)])
    m.CreateFaceVertexCountsAttr([3]); m.CreateFaceVertexIndicesAttr([0,1,2])
    geo.GetRootLayer().Save()
    look = Usd.Stage.CreateNew(str(folder / 'look.usda'))
    look.SetDefaultPrim(UsdGeom.Scope.Define(look,'/Look').GetPrim())
    UsdGeom.Scope.Define(look, '/Look/Looks')
    mat = UsdShade.Material.Define(look, '/Look/Looks/body')
    shader = UsdShade.Shader.Define(look, '/Look/Looks/body/surface')
    shader.CreateIdAttr('UsdPreviewSurface')
    shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set((.7,.1,.2))
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
    UsdShade.MaterialBindingAPI.Apply(look.OverridePrim('/Look/body')).Bind(mat)
    look.GetRootLayer().customLayerData = {'preview_meshes': {'/Look/body':mesh_fingerprint(m.GetPrim())}}
    look.GetRootLayer().Save()
    return geo, look


def test_sparse_apply_preserves_deform_and_remaps_connections(tmp_path):
    from pxr import Usd, UsdGeom, UsdShade
    from smartlib.core.preview_look import apply_preview_look
    geo, look = fixture_files(tmp_path)
    before = geo.GetRootLayer().ExportToString()
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim('/Shot/DLI').GetReferences().AddReference(geo.GetRootLayer().identifier, '/Geometry')
    apply_preview_look(stage, '/Shot/DLI', geo, look.GetRootLayer().identifier, {'/Look/body':'/Geometry/body'})
    mesh = stage.GetPrimAtPath('/Shot/DLI/body')
    material = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0]
    assert material.GetPath() == '/Shot/DLI/Looks/body'
    shader, _, _ = material.ComputeSurfaceSource()
    assert shader.GetIdAttr().Get() == 'UsdPreviewSurface'
    assert len(UsdGeom.Mesh(mesh).GetPointsAttr().Get()) == 3
    assert not stage.GetRootLayer().GetAttributeAtPath('/Shot/DLI/body.points')
    assert before == geo.GetRootLayer().ExportToString()
    UsdGeom.Mesh(geo.GetPrimAtPath('/Geometry/body')).GetFaceVertexIndicesAttr().Set([0,2,1])
    with pytest.raises(ValueError, match='topology/UV'):
        apply_preview_look(Usd.Stage.CreateInMemory(), '/Other', geo, look.GetRootLayer().identifier, {'/Look/body':'/Geometry/body'})


def test_look_preserves_referenced_skinning_and_evaluated_motion(tmp_path):
    from pxr import Usd, UsdGeom, UsdSkel, UsdShade, Gf
    from smartlib.core.preview_look import apply_preview_look
    geo, look = fixture_files(tmp_path)
    UsdSkel.Root.Define(geo, '/Geometry')
    skel = UsdSkel.Skeleton.Define(geo, '/Geometry/Skeleton')
    skel.CreateJointsAttr(['root'])
    skel.CreateBindTransformsAttr([Gf.Matrix4d(1)])
    skel.CreateRestTransformsAttr([Gf.Matrix4d(1)])
    animation = UsdSkel.Animation.Define(geo, '/Geometry/Animation')
    animation.CreateJointsAttr(['root'])
    animation.CreateTranslationsAttr().Set([(4, 0, 0)], 1)
    animation.CreateRotationsAttr().Set([Gf.Quatf(1)], 1)
    animation.CreateScalesAttr().Set([(1, 1, 1)], 1)
    UsdSkel.BindingAPI.Apply(skel.GetPrim()).CreateAnimationSourceRel().SetTargets([animation.GetPath()])
    binding = UsdSkel.BindingAPI.Apply(geo.GetPrimAtPath('/Geometry/body'))
    binding.CreateSkeletonRel().SetTargets([skel.GetPath()])
    binding.CreateGeomBindTransformAttr(Gf.Matrix4d(1))
    binding.CreateJointIndicesPrimvar(False, 1).Set([0, 0, 0])
    binding.CreateJointWeightsPrimvar(False, 1).Set([1, 1, 1])
    geo.GetRootLayer().Save()
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim('/Shot/DLI').GetReferences().AddReference(geo.GetRootLayer().identifier, '/Geometry')
    apply_preview_look(stage, '/Shot/DLI', geo, look.GetRootLayer().identifier, {'/Look/body': '/Geometry/body'})
    mesh = stage.GetPrimAtPath('/Shot/DLI/body')
    assert mesh.HasAPI(UsdSkel.BindingAPI)
    assert UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0]
    assert UsdSkel.BakeSkinning(Usd.PrimRange(stage.GetPrimAtPath('/Shot/DLI')), Gf.Interval(1, 1))
    assert UsdGeom.Mesh(mesh).GetPointsAttr().Get(1)[0] == Gf.Vec3f(4, 0, 0)


def test_adopt_preview_save_reopen_and_changed_look_rejected(service, tmp_path):
    from pxr import UsdShade
    from smartlib.core.metadata import write_json
    from smartlib.apps.smart_composition.service import CompositionSession
    svc, identity = service
    geo, look = fixture_files(tmp_path)
    version, directory = svc._reserve(svc.paths.usd_handoff_dir(*svc._identity(identity),'animation','DLI'))
    manifest = svc._file(directory,'manifest.json')
    write_json(manifest, dict(schema='smartpipeline.usd_handoff_product.v1',status='published',
        kind='animation',target='DLI',version=version,shot=dict(episode='ep01',sequence='sq01',shot='sh001'),
        frame_range=[1,2],fps=24,usd=dict(meters_per_unit=.01,up_axis='Y'),inputs={},dependencies=[],
        entrypoint=svc.pin(geo.GetRootLayer().identifier)))
    original = manifest.read_bytes()
    adopted = svc.adopt_preview_look(identity,manifest,look.GetRootLayer().identifier,{'/Look/body':'/Geometry/body'})
    composition = svc.compose_products(identity,[adopted])
    session = CompositionSession(svc.shots,composition)
    stage, layers = session.preview([adopted.as_posix()])
    assert UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/Shot/Animation/DLI/body')).ComputeBoundMaterial()[0]
    saved = session.save([adopted.as_posix()])
    assert CompositionSession(svc.shots,saved).initial[('animation','DLI')] == adopted.as_posix()
    assert original == manifest.read_bytes()
    Path(look.GetRootLayer().identifier).write_text('# modified')
    with pytest.raises(ValueError,match='changed'):
        session.preview([str(adopted)])


def test_relative_texture_is_resolved_before_copying(tmp_path):
    from pxr import Sdf, Usd, UsdShade
    from smartlib.core.preview_look import apply_preview_look
    geo, look = fixture_files(tmp_path)
    texture = Path(look.GetRootLayer().identifier).parent / 'color.png'
    texture.write_bytes(b'texture-dependency')
    node = UsdShade.Shader.Define(look,'/Look/Looks/body/texture')
    node.CreateIdAttr('UsdUVTexture')
    node.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('color.png'))
    look.GetRootLayer().Save()
    stage = Usd.Stage.CreateInMemory()
    deps = apply_preview_look(stage,'/DLI',geo,look.GetRootLayer().identifier,{'/Look/body':'/Geometry/body'})
    value = stage.GetPrimAtPath('/DLI/Looks/body/texture').GetAttribute('inputs:file').Get()
    assert Path(value.path).resolve() == texture.resolve()
    assert {Path(p).resolve() for p in deps} == {texture.resolve()}
