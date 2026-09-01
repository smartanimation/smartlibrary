"""Verify an Animation ATOM by applying it to a newly referenced clean rig."""
from __future__ import annotations
import argparse,json,math,os,sys
from pathlib import Path

def leaf(node): return str(node).rsplit("|",1)[-1].split(":",1)[-1]
def main():
    p=argparse.ArgumentParser(); p.add_argument("source_scene"); p.add_argument("manifest")
    p.add_argument("--repo",required=True); p.add_argument("--report",required=True)
    p.add_argument("--source-namespace",default="DLI"); p.add_argument("--target-namespace",default="DLI_clean")
    p.add_argument("--start",type=int,required=True); p.add_argument("--end",type=int,required=True); a=p.parse_args()
    sys.path.insert(0,str(Path(a.repo)/"packages")); import maya.standalone; maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from smartlib.dcc.maya.animation_curves import apply_animation_atom_from_file
    try: cmds.file(a.source_scene,open=True,force=True,prompt=False)
    except RuntimeError:
        if not cmds.objExists(f"{a.source_namespace}:Root"): raise
    rig_path=cmds.referenceQuery(f"{a.source_namespace}:Root",filename=True,withoutCopyNumber=True)
    frames=[a.start+i*.25 for i in range((a.end-a.start)*4+1)]
    source_joints=sorted(str(n) for n in (cmds.ls(f"{a.source_namespace}:J_*",type="joint",long=True) or []))
    source_by_leaf={leaf(n):n for n in source_joints}; before={k:[] for k in source_by_leaf}
    for frame in frames:
        cmds.currentTime(frame,edit=True)
        for key,node in source_by_leaf.items(): before[key].append([float(v) for v in cmds.xform(node,q=True,ws=True,m=True)])
    cmds.file(new=True,force=True)
    cmds.file(rig_path,reference=True,namespace=a.target_namespace,mergeNamespacesOnClash=False,ignoreVersion=True,options="v=0;")
    apply_animation_atom_from_file(a.manifest,namespace=a.target_namespace,clear_existing=True)
    target_joints={leaf(n):str(n) for n in (cmds.ls(f"{a.target_namespace}:J_*",type="joint",long=True) or [])}
    missing=sorted(set(source_by_leaf)-set(target_joints)); diffs=[]; count=0; per_node={}
    for fi,frame in enumerate(frames):
        cmds.currentTime(frame,edit=True)
        for key,expected_values in before.items():
            node=target_joints.get(key)
            if not node: continue
            actual=[float(v) for v in cmds.xform(node,q=True,ws=True,m=True)]; expected=expected_values[fi]
            if any(not math.isclose(x,y,rel_tol=1e-9,abs_tol=1e-6) for x,y in zip(actual,expected)):
                count+=1; per_node[key]=per_node.get(key,0)+1
                if len(diffs)<1000: diffs.append({"joint":key,"frame":frame,"expected":expected,"actual":actual})
    report={"rig_path":rig_path,"source_joint_count":len(source_by_leaf),"target_joint_count":len(target_joints),
        "missing_joints":missing,"sample_count":len(source_by_leaf)*len(frames),"difference_count":count,
        "different_joint_count":len(per_node),"worst_joints":sorted(per_node.items(),key=lambda x:x[1],reverse=True)[:100],"differences":diffs}
    Path(a.report).write_text(json.dumps(report,indent=2),encoding="utf-8"); print(json.dumps({k:v for k,v in report.items() if k!="differences"},indent=2));sys.stdout.flush();os._exit(1 if count or missing else 0)
if __name__=="__main__": main()
