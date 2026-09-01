"""Prove the production Animation ATOM API against a Maya scene."""
from __future__ import annotations
import argparse, json, math, os, sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser(); p.add_argument("scene"); p.add_argument("--repo",required=True)
    p.add_argument("--output",required=True); p.add_argument("--namespace",default="DLI")
    p.add_argument("--start",type=int,required=True); p.add_argument("--end",type=int,required=True)
    a=p.parse_args(); sys.path.insert(0,str(Path(a.repo)/"packages"))
    import maya.standalone; maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from smartlib.dcc.maya.animation_curves import (apply_animation_atom_from_file,
        export_animation_atom_for_cast,_contract_nodes,_controller_members,_resolve_controller_root)
    try:
        cmds.file(a.scene,open=True,force=True,prompt=False)
    except RuntimeError:
        if not cmds.objExists(f"{a.namespace}:allRigSet"): raise
    root=_resolve_controller_root(cmds,a.namespace,"allRigSet")
    controls=_contract_nodes(cmds,_controller_members(cmds,root),traverse_descendants=True,namespace=a.namespace)
    fingers=[str(n) for n in cmds.ls(f"{a.namespace}:A_*Finger*",long=True) or [] if cmds.nodeType(n) in {"transform","joint"}]
    observed=sorted(set(controls+fingers)); attrs=[]
    for n in observed:
        for x in sorted(set((cmds.listAttr(n,keyable=True) or [])+(cmds.listAttr(n,channelBox=True) or []))):
            plug=f"{n}.{x}"
            try:
                if isinstance(cmds.getAttr(plug,time=a.start),(bool,int,float)): attrs.append(plug)
            except (RuntimeError,TypeError,ValueError): pass
    attrs=sorted(set(attrs)); frames=[a.start+i*.25 for i in range((a.end-a.start)*4+1)]
    before={x:[float(cmds.getAttr(x,time=f)) for f in frames] for x in attrs}
    joints=sorted(str(n) for n in (cmds.ls(f"{a.namespace}:J_*",type="joint",long=True) or []))
    before_world={n:[] for n in joints}
    for frame in frames:
        cmds.currentTime(frame,edit=True)
        for node in joints: before_world[node].append([float(v) for v in cmds.xform(node,q=True,ws=True,m=True)])
    out=Path(a.output); out.mkdir(parents=True,exist_ok=True)
    manifest=export_animation_atom_for_cast(out/"animation.atom",cast_key="DLI_main",asset="DLI",
        namespace=a.namespace,source_workfile=a.scene,frame_range=(a.start,a.end))
    mp=out/"animation_manifest.json"; mp.write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    transfer=manifest["transfer_nodes"]; cmds.cutKey(transfer,clear=True)
    for plug in attrs:
        node,attr=plug.rsplit(".",1)
        try:
            default=cmds.attributeQuery(attr,node=node,listDefault=True) or []
            if default and cmds.getAttr(plug,settable=True): cmds.setAttr(plug,default[0])
        except (RuntimeError,TypeError,ValueError): pass
    apply_animation_atom_from_file(mp,namespace=a.namespace,clear_existing=True)
    diffs=[]; count=0
    for plug,values in before.items():
        for frame,expected in zip(frames,values):
            actual=float(cmds.getAttr(plug,time=frame))
            if not math.isclose(actual,expected,rel_tol=1e-10,abs_tol=1e-7):
                count+=1
                if len(diffs)<1000: diffs.append({"plug":plug,"frame":frame,"expected":expected,"actual":actual})
    world_count=0; world_diffs=[]
    for frame_index,frame in enumerate(frames):
        cmds.currentTime(frame,edit=True)
        for node in joints:
            actual=[float(v) for v in cmds.xform(node,q=True,ws=True,m=True)]
            expected=before_world[node][frame_index]
            if any(not math.isclose(x,y,rel_tol=1e-10,abs_tol=1e-7) for x,y in zip(actual,expected)):
                world_count+=1
                if len(world_diffs)<1000: world_diffs.append({"node":node,"frame":frame,"expected":expected,"actual":actual})
    report={"controller_count":len(controls),"finger_node_count":len(fingers),
        "transfer_node_count":manifest["transfer_node_count"],"animated_node_count":manifest["animated_node_count"],
        "static_value_count":manifest["static_value_count"],"attribute_count":len(attrs),
        "sample_count":len(attrs)*len(frames),"difference_count":count,"differences":diffs,
        "joint_count":len(joints),"joint_world_sample_count":len(joints)*len(frames),
        "joint_world_difference_count":world_count,"joint_world_differences":world_diffs}
    (out/"roundtrip_report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in {"differences","joint_world_differences"}},indent=2)); sys.stdout.flush(); os._exit(1 if count or world_count else 0)
if __name__=="__main__": main()
