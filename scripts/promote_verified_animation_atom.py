"""Promote a zero-difference ATOM proof through ShotManagerService."""
from __future__ import annotations
import argparse, json, shutil, sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser(); p.add_argument("proof_dir"); p.add_argument("--repo",required=True)
    p.add_argument("--clean-rig-report", required=True)
    p.add_argument("--config",required=True); p.add_argument("--episode",required=True)
    p.add_argument("--sequence",required=True); p.add_argument("--shot",required=True)
    p.add_argument("--target",required=True); p.add_argument("--source-workfile",required=True)
    p.add_argument("--comment",default="Verified ATOM v2")
    a=p.parse_args(); sys.path.insert(0,str(Path(a.repo)/"packages"))
    from smartlib.apps.shot_manager.service import ShotIdentity,ShotManagerService
    from smartlib.core.config_loader import ProjectConfig
    proof=Path(a.proof_dir); report=json.loads((proof/"roundtrip_report.json").read_text(encoding="utf-8"))
    if int(report.get("difference_count",-1)) != 0: raise RuntimeError("Proof is not zero-difference")
    if int(report.get("joint_world_difference_count",-1)) != 0:
        raise RuntimeError("Joint world-matrix proof is missing or not zero-difference")
    clean_report = json.loads(Path(a.clean_rig_report).read_text(encoding="utf-8"))
    if int(clean_report.get("difference_count", -1)) != 0 or clean_report.get("missing_joints"):
        raise RuntimeError("Clean-rig namespace-remap proof is not zero-difference")
    manifest=json.loads((proof/"animation_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "smartpipeline.animation_atom.v3": raise RuntimeError("Proof is not ATOM v3")
    service=ShotManagerService(ProjectConfig(Path(a.config))); identity=ShotIdentity(a.episode,a.sequence,a.shot)
    plan=service.plan_animation_atom_export(identity,target=a.target,subset="curves")
    shutil.copy2(proof/"animation.atom",plan["atom_path"])
    result=service.finalize_animation_atom_export(identity,manifest,target=a.target,subset="curves",
        version=plan["version"],source_workfile=a.source_workfile,comment=a.comment)
    print(result)
if __name__=="__main__": main()
