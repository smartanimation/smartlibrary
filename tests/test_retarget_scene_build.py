from dataclasses import replace
import pytest
from smartlib.apps.retarget_setup.service import RetargetService, write
from smartlib.core.path_resolver import AssetIdentity
from smartlib.apps.shot_manager import SequenceIdentity
from smartlib.retarget.scene_build import attach_retarget_inputs
from smartlib.dcc.maya.sequence_inputs import apply_sequence_inputs
from test_sequence_build_assignments import assigned_plan


def published(service, tmp_path, name, group='main'):
    r = RetargetService(service.shots.paths, AssetIdentity('character', group, name, ''))
    rig = tmp_path / (name+'.ma'); rig.write_text('rig')
    profile = dict(asset=name,input_mode='mcr_to_anim',animation_rig_scene=str(rig),time_unit='film',reference_frame=0,
                   mappings=[dict(source='MC',target='A',method='orient',maintain_offset=False)])
    data = r.save_data(profile)
    motion = tmp_path / (name+'.fbx'); motion.write_bytes(b'fbx')
    job=r.prepare_test(profile,motion,1,10)
    r.path('test',job['run_id'],'result.ma').write_text('test')
    write(r.path('test',job['run_id'],'report.json'),{'keyed_plugs':3})
    job=r.review_test(r.complete_test(job,0),True)
    r.publish(data,job)
    return rig


def test_cast_without_group_resolves_registered_sub_character(tmp_path):
    s = assigned_plan(tmp_path)
    for name in ['DLI', 'JIN']:
        identity = AssetIdentity('character', 'sub', name, '')
        write(s.shots.paths.artifact_file(s.shots.paths.asset_root(identity), 'asset.json'),
              dict(asset=name, category='character', group='sub'))
        published(s, tmp_path, name, group='sub')
    plan = attach_retarget_inputs(s.plan('ep02', 's027', 'Mocap Only'),
                                  s.shots, SequenceIdentity('ep02', 's027'))
    assert {row.key for row in plan.inputs} >= {'editorial', 'mocap', 'cast', 'storyreel', 'audio'}
    children = next(row for row in plan.inputs if row.key == 'mocap').children
    assert all(row.retarget['version'] == 'v001' for row in children)
    assert all('/character/sub/' in row.retarget['profile_path'].replace('\\', '/') for row in children)
    assert not any(v.key.startswith('retarget_') and v.state == 'ERROR' for v in plan.validation)


def test_requires_each_character_publish_and_detects_rig_change(tmp_path):
    s=assigned_plan(tmp_path);identity=SequenceIdentity('ep02','s027')
    plan=s.plan('ep02','s027','Mocap + Virtual Camera')
    checked=attach_retarget_inputs(plan,s.shots,identity)
    assert not checked.can_build
    rig=published(s,tmp_path,'DLI')
    checked=attach_retarget_inputs(plan,s.shots,identity)
    assert not checked.can_build
    assert any(v.key=='retarget_JIN' and v.state=='ERROR' for v in checked.validation)
    published(s,tmp_path,'JIN')
    checked=attach_retarget_inputs(plan,s.shots,identity)
    assert checked.can_build
    row=next(g for g in checked.inputs if g.key=='mocap').children[0]
    assert row.retarget['version']=='v001' and row.retarget['rig']==str(rig)
    rig.write_text('changed rig dependency')
    assert not attach_retarget_inputs(plan,s.shots,identity).can_build


def test_build_bakes_in_cast_namespace_and_blocks_raw_fallback(tmp_path,monkeypatch):
    s=assigned_plan(tmp_path);identity=SequenceIdentity('ep02','s027')
    plan=s.plan('ep02','s027','Mocap + Virtual Camera')
    with pytest.raises(RuntimeError,match='verified Retarget Publish'):
        apply_sequence_inputs(plan.inputs,cmds=object(),include_camera=False,require_retarget=True)
    for name in ['DLI','JIN']:published(s,tmp_path,name)
    plan=attach_retarget_inputs(plan,s.shots,identity)
    calls=[]
    monkeypatch.setattr('smartlib.dcc.maya.mcr_to_anim.bake_received_mcr',lambda profile,**kw:calls.append((profile,kw)))
    paths=apply_sequence_inputs(plan.inputs,cmds=object(),include_camera=False,frame_range=(278,822),require_retarget=True)
    assert len(calls)==2 and len(paths)==6
    assert [kw['target_namespace'] for _,kw in calls]==['DLI','JIN']
    assert all(p['frame_range']==[278,822] for p,_ in calls)


def test_cast_preview_uses_published_anim_not_another_representation(tmp_path):
    from smartlib.apps.shot_manager.service import BuildPreviewItem
    from smartlib.retarget.scene_build import retarget_cast_preview
    s=assigned_plan(tmp_path)
    for name in ['DLI','JIN']:published(s,tmp_path,name)
    plan=attach_retarget_inputs(s.plan('ep02','s027','Mocap + Virtual Camera'),s.shots,SequenceIdentity('ep02','s027'))
    preview=[BuildPreviewItem(name,name,'default',name,'character','CHA','approved',True,'missing',publish_path='wrong.mb') for name in ['DLI','JIN']]
    result=retarget_cast_preview(preview,plan.inputs)
    assert all(r.status=='resolved' for r in result)
    assert [r.publish_path for r in result]==[str(tmp_path/'DLI.ma'),str(tmp_path/'JIN.ma')]
    with pytest.raises(ValueError,match='absent from build preview'):
        retarget_cast_preview(preview[:1],plan.inputs)
