from pathlib import Path
import pytest
from pxr import Usd, UsdGeom
from smartlib.apps.review_build_manager.houdini_build import write_stage, verify, SCHEMA
from smartlib.apps.asset_manager.environment_pack import digest


@pytest.fixture
def pinned(tmp_path):
    path=tmp_path/'asset.usda'
    stage=Usd.Stage.CreateNew(str(path))
    root=UsdGeom.Xform.Define(stage,'/Room').GetPrim();stage.SetDefaultPrim(root)
    variants=root.GetVariantSets().AddVariantSet('variant')
    for value in ('A','B'):
        variants.AddVariant(value);variants.SetVariantSelection(value)
        with variants.GetVariantEditContext():
            quality=root.GetVariantSets().AddVariantSet('quality')
            for q in ('proxy','render'):
                quality.AddVariant(q);quality.SetVariantSelection(q)
                with quality.GetVariantEditContext():
                    UsdGeom.Xform.Define(stage,'/Room/'+value+'_'+q)
    stage.GetRootLayer().Save()
    return dict(schema=SCHEMA, dependencies=[dict(path=str(path),sha256=digest(path))],
        usd=dict(meters_per_unit=.01,up_axis='Y'),fps=24,frame_range=[1001,1120],
        assets=[dict(name='room',prim_path='/World/room',entry_path=str(path),entry_prim='/Room',
          pack_version='v002',release_version='v003',variant_selections=dict(variant='A',quality='proxy'),
          transform=[1,0,0,0,0,1,0,0,0,0,1,0,20,0,0,1])])


def test_stage_pins_variants_and_timing(pinned,tmp_path):
    output=tmp_path/'shot.usda'
    write_stage(pinned,output)
    stage=Usd.Stage.Open(str(output))
    assert stage.GetPrimAtPath('/World/room/Asset/A_proxy')
    assert not stage.GetPrimAtPath('/World/room/Asset/B_render')
    assert stage.GetStartTimeCode()==1001 and stage.GetEndTimeCode()==1120
    assert stage.GetFramesPerSecond()==24
    assert UsdGeom.GetStageMetersPerUnit(stage)==.01
    assert UsdGeom.Xformable(stage.GetPrimAtPath('/World/room')).GetLocalTransformation()[3][0]==20


def test_changed_dependency_blocks_worker(pinned,tmp_path):
    Path(pinned['dependencies'][0]['path']).write_text('changed')
    with pytest.raises(ValueError,match='changed after queueing'):
        write_stage(pinned,tmp_path/'shot.usda')
    assert not (tmp_path/'shot.usda').exists()


def test_missing_variant_is_not_silently_defaulted(pinned,tmp_path):
    pinned['assets'][0]['variant_selections']['variant']='missing'
    with pytest.raises(ValueError,match='Missing USD selection'):
        write_stage(pinned,tmp_path/'shot.usda')


def test_unknown_snapshot_rejected():
    with pytest.raises(ValueError,match='Unsupported'):
        verify({})


def test_snapshot_rejects_unsupported_enabled_inputs():
    from types import SimpleNamespace
    from smartlib.apps.review_build_manager.houdini_build import snapshot
    service=SimpleNamespace(project_config=SimpleNamespace(load=lambda name:{}),
        shots=SimpleNamespace(paths=None,shot_frame_range=lambda i:(1001,1120),
                              load_shot=lambda i:{},project_fps=24))
    identity=SimpleNamespace(episode='e',sequence='s',shot='t')
    with pytest.raises(ValueError,match='does not yet support'):
        snapshot(service,identity,{'components':[{'component_type':'camera','name':'main'}]})
    with pytest.raises(ValueError,match='at least one'):
        snapshot(service,identity,{'components':[{'component_type':'camera','name':'main','enabled':False}]})
