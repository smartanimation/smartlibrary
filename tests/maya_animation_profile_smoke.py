"""Run from the repository root with mayapy; generated fixtures live under .tmp."""
from pathlib import Path
from datetime import datetime
import json
import sys
sys.path.insert(0, str(Path.cwd() / "packages"))
import maya.standalone
maya.standalone.initialize(name="python")
try:
    import maya.cmds as cmds
    from smartlib.apps.shot_manager import ShotManagerService, ShotIdentity
    from smartlib.apps.shot_manager.animation_publish import AnimationCompositionService
    from smartlib.core.config_loader import ProjectConfig
    from smartlib.core.path_resolver import AssetIdentity
    from smartlib.core.metadata import write_json, read_json
    from smartlib.dcc.maya.animation_build import build_manifest
    root = Path.cwd() / ".tmp" / ("animation-profile-maya-" + datetime.now().strftime("%Y%m%d%H%M%S"))
    config = root / "config"; config.mkdir(parents=True)
    (config / "templates_base.yml").write_text(
        f"anchors:\n  project_name: TEST\n  project_root: '{root.as_posix()}/project'\n", encoding="utf-8")
    shots = ShotManagerService(ProjectConfig(config))
    identity = ShotIdentity("ep01","sq01","sh001")
    aid = AssetIdentity("character", "main", "Hero")
    asset_root = shots.paths.asset_root(aid)
    write_json(asset_root / "asset.json", {"category":"character","group":"main","asset":"Hero"})
    rig_dir = shots.paths.asset_publish_dir(aid,"asset","rend") / "v001"
    rig_dir.mkdir(parents=True)
    rig = rig_dir / "rig.ma"
    cmds.file(new=True,force=True)
    mesh = cmds.polyCube(name="body")[0]
    joint = cmds.joint(name="root_JNT")
    cmds.skinCluster(joint,mesh,toSelectedBones=True)
    sculpt = cmds.duplicate(mesh,name="sculpt_target")[0]
    cmds.move(0,1,0,sculpt+".vtx[0]",relative=True)
    blend = cmds.blendShape(sculpt,mesh,name="shot_sculpt",after=True)[0]
    cmds.delete(sculpt)
    ctl = cmds.circle(name="root_CTL")[0]
    cmds.addAttr(ctl,longName="sculpt",attributeType="double",keyable=True)
    cmds.connectAttr(ctl+".sculpt",blend+".weight[0]")
    cmds.connectAttr(ctl+".translateX",joint+".translateX")
    cmds.sets(ctl,name="allRigSet")
    cmds.sets(mesh,name="cache_geo_set")
    cmds.file(rename=str(rig));cmds.file(save=True,type="mayaAscii",force=True)
    write_json(rig_dir.parent/"latest.json",{"version":"v001","path":"v001/rig.ma"})
    write_json(shots.shot_root(identity)/"shot.json",{"editorial":{"cut_in":1001,"cut_out":1010}})
    write_json(shots.shot_root(identity)/"cast.json",{"cast":{"Hero_01":{
        "asset":"Hero","variant":"default","namespace":"Hero_01","category":"character","asset_publish":"latest"
    }}})
    cmds.file(new=True,force=True)
    cmds.file(str(rig),reference=True,namespace="Hero_01")
    for attr, values in [("translateX",(0,5)),("sculpt",(0,1))]:
        for frame,value in zip((1001,1010),values):
            cmds.setKeyframe("Hero_01:root_CTL",attribute=attr,time=frame,value=value)
    source_dir=shots.paths.animation_build_dir("ep01","sq01","sh001","v001")
    source_dir.mkdir(parents=True)
    source=source_dir/"source.ma"
    cmds.file(rename=str(source));cmds.file(save=True,type="mayaAscii",force=True)
    expected={}
    for frame in (1001,1010):
        cmds.currentTime(frame)
        expected[frame]=cmds.xform("Hero_01:body.vtx[0]",query=True,worldSpace=True,translation=True)
    results={}
    for profile in ("usd_animation","alembic_cache","maya_rend_atom"):
        (config/"project_settings.yml").write_text(f"pipeline_profile: {profile}\npipeline_profile_version: 1\n",encoding="utf-8")
        service=AnimationCompositionService(shots)
        review_source=None
        if profile == "maya_rend_atom":
            from smartlib.apps.shot_manager.animation_publish import file_hash
            from smartlib.apps.review_build_manager.composition_sources import SOURCE_SCHEMA
            records={}
            for key in ("review_json", "source_manifest", "build_manifest", "validation"):
                receipt=write_json(root/("draft_"+key+".json"), {"test_receipt": key})
                records[key]={"path":str(receipt), "sha256":file_hash(receipt)}
            review_source={"schema":SOURCE_SCHEMA, "scene":str(source), "scene_sha256":file_hash(source),
                           "records":records,
                           "animation_draft":{"Hero_01":{"use":True, "rend":service._fixed_dependency(rig)}}}
        build=build_manifest(shots,identity,source,review_source=review_source)
        snapshot=service.publish_build(identity,build)
        data=service.load(snapshot)
        if profile=="usd_animation":
            from pxr import Usd,UsdGeom
            stage=Usd.Stage.Open(data["entrypoint"]["path"])
            prim=next(p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh))
            for frame in (1001,1010):
                pos=UsdGeom.Mesh(prim).GetPointsAttr().Get(frame)[0]
                world=UsdGeom.XformCache(frame).GetLocalToWorldTransform(prim).Transform(pos)
                assert all(abs(float(world[i])-expected[frame][i])<1e-4 for i in range(3)),(world,expected[frame])
        elif profile=="alembic_cache":
            cmds.file(new=True,force=True)
            cmds.loadPlugin("AbcImport",quiet=True)
            cmds.AbcImport(data["members"][0]["products"]["deform"]["path"],mode="import")
            shape=cmds.ls(type="mesh",long=True)[0]
            for frame in (1001,1010):
                cmds.currentTime(frame)
                pos=cmds.xform(shape+".vtx[0]",query=True,worldSpace=True,translation=True)
                assert all(abs(pos[i]-expected[frame][i])<1e-4 for i in range(3)),(pos,expected[frame])
        else:
            rend_scene=data["members"][0]["products"]["rend"]["path"]
            cmds.file(rend_scene,open=True,force=True,prompt=False)
            assert cmds.file(query=True,reference=True), "REND must retain Asset references"
            assert cmds.referenceQuery("Hero_01:root_CTL",isNodeReferenced=True)
            cmds.file(data["entrypoint"]["path"],open=True,force=True,prompt=False)
            assert cmds.playbackOptions(query=True,minTime=True)==1001
            assert cmds.playbackOptions(query=True,maxTime=True)==1010
            cmds.currentTime(1010)
            controls=cmds.ls("*:root_CTL",recursive=True) or []
            assert controls, "No referenced REND control"
            assert abs(cmds.getAttr(controls[0]+".translateX")-5)<1e-4
            assert abs(cmds.getAttr(controls[0]+".sculpt")-1)<1e-4
        results[profile]={"snapshot":str(snapshot),"ok":True}
    write_json(root/"report.json",{"expected":expected,"results":results})
    print("SMOKE_OK",root/"report.json")
finally:
    maya.standalone.uninitialize()
