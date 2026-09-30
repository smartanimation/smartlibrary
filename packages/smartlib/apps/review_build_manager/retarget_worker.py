"""Retarget Motion Test worker: build, reopen, and measure the saved scene."""
import argparse
import json
from pathlib import Path


def audit_saved_scene(cmds, profile, output):
    import math
    from maya.api import OpenMaya as om
    from smartlib.dcc.maya.shot_builder import _import_file
    cmds.file(str(output), open=True, force=True, prompt=False)
    cmds.currentUnit(linear="cm")
    targets = cmds.ls(type='transform', long=True) or []
    before = {cmds.ls(n, uuid=True)[0] for n in targets}
    _import_file(cmds, Path(profile['mocap_fbx']), 'audit_mcr')
    sources = [n for n in cmds.ls(type='transform', long=True) or [] if cmds.ls(n, uuid=True)[0] not in before]

    def resolve(nodes, name):
        matches = [n for n in nodes if n.rsplit('|', 1)[-1].rsplit(':', 1)[-1] == name]
        if len(matches) != 1:
            raise ValueError(f'Audit: expected one {name}, found {len(matches)}')
        return matches[0]

    rows = []
    for row in profile['mappings']:
        if not row.get('enabled', True):
            continue
        rows.append((row, resolve(sources, row['source']), resolve(targets, row['target'])))
    start, end = profile['frame_range']
    frames = [start + i for i in range(int(end - start) + 1)]
    keys = {}
    for row, _, dst in rows:
        attrs = ['rx','ry','rz'] if row['method']=='orient' else ['tx','ty','tz'] if row['method']=='point' else ['tx','ty','tz','rx','ry','rz']
        for attr in attrs:
            plug = dst+'.'+attr
            times = cmds.keyframe(plug, query=True, time=(start,end), timeChange=True) or []
            keys[row['target']+'.'+attr] = len(times)
            if any(not any(abs(t-f)<1e-5 for t in times) for f in frames):
                raise RuntimeError(f'Missing baked frame in saved result: {plug}')
    stats = {}
    for frame in frames:
        cmds.currentTime(frame)
        for row, src, dst in rows:
            matrices = [om.MTransformationMatrix(om.MMatrix(cmds.xform(n,query=True,worldSpace=True,matrix=True))) for n in (src,dst)]
            a,b = matrices
            qa,qb = [m.rotation(asQuaternion=True) for m in matrices]
            values = {'position_cm': (a.translation(om.MSpace.kWorld)-b.translation(om.MSpace.kWorld)).length(),
                      'rotation_deg': math.degrees(2*math.acos(min(1,abs(qa.x*qb.x+qa.y*qb.y+qa.z*qb.z+qa.w*qb.w))))}
            for metric,value in values.items():
                stat=stats.setdefault(row['target']+'/'+metric,{'max':0,'frame':frame})
                if value>stat['max']:stat.update(max=value,frame=frame)
    return {'status':'measured','frames':[start,end],'frame_count':len(frames),'key_counts':keys,'stats':stats,
            'comparison':'Received source versus saved ANIM mapped control, world space; no alignment. Offsets and rig proportions may produce intentional differences.',
            'max_position_cm':max((v['max'] for k,v in stats.items() if k.endswith('position_cm')),default=0),
            'max_rotation_deg':max((v['max'] for k,v in stats.items() if k.endswith('rotation_deg')),default=0)}


def main(argv=None):
    parser=argparse.ArgumentParser()
    for name in ('profile','output','report','audit'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args(argv)
    from smartlib.retarget.profile import load_retarget_profile
    p=load_retarget_profile(args.profile)
    # The existing worker supports both legacy and received-MCR routes.
    from tools.maya.bake_mocap_to_rig import load_plugins, sample_mcr, apply_to_animation_rig, cmds
    load_plugins(p)
    if p.get('input_mode')=='mcr_to_anim':
        from smartlib.dcc.maya.mcr_to_anim import bake_received_mcr
        report=bake_received_mcr(p,args.output,cmds=cmds)
        audit=audit_saved_scene(cmds,p,args.output)
    else:
        samples,report,solvers=sample_mcr(p)
        report.update(apply_to_animation_rig(p,samples,solvers,args.output))
        audit={'status':'not_supported','detail':'Numerical source/target comparison is available for received MCR mappings.'}
    Path(args.audit).write_text(json.dumps(audit,indent=2),encoding='utf-8')
    report['numeric_audit']={'path':args.audit,**{k:v for k,v in audit.items() if k in ('status','max_position_cm','max_rotation_deg')}}
    Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
