"""Run with mayapy; artifacts stay under .tmp/studio-delivery-smoke."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
import maya.standalone
maya.standalone.initialize(name="python")
try:
    from maya import cmds
    from smartlib.apps.asset_manager.studio_delivery import StudioDeliveryService
    from smartlib.core.path_resolver import AssetIdentity
    from smartlib.core.config_loader import ProjectConfig
    from smartlib.dcc.maya.studio_delivery import export_job
    import uuid
    root = ROOT / ".tmp" / "studio-delivery-smoke" / uuid.uuid4().hex
    config = root / "config"
    (config / "contexts/studio_delivery").mkdir(parents=True)
    (config / "templates_base.yml").write_text(f'anchors:\n  project_root: "{root.as_posix()}"\n  project_name: smoke\n')
    (config / "contexts/studio_delivery/v001.yml").write_text('enabled: true\n')
    service = StudioDeliveryService(ProjectConfig(config))
    for category, context, units, axis, referenced in (("character", "mcp", "cm", "y", False), ("environment", "rend", "cm", "y", False), ("character", "mcp", "m", "z", True)):
        cmds.file(new=True, force=True)
        mesh = cmds.polyCube(name="body", subdivisionsY=3)[0]
        cmds.sets(mesh, name="cache_geo_set")
        if category == "character":
            cmds.select(clear=True)
            joint = cmds.joint(name="root_jnt", position=(0, -1, 0))
            tip = cmds.joint(name="tip_jnt", position=(0, 1, 0))
            cmds.skinCluster(joint, tip, mesh, toSelectedBones=True)
            cmds.sets(joint, name="skel_export_set")
        (config / "contexts/studio_delivery/v001.yml").write_text(f'enabled: true\nfbx:\n  units: {units}\n  up_axis: {axis}\n')
        service = StudioDeliveryService(ProjectConfig(config))
        identity = AssetIdentity(category, "test", category + ("_reference" if referenced else ""))
        if referenced:
            rig_dir = service.paths.asset_data_version_dir(identity, "rig", "mocap", "v001")
            rig_dir.mkdir(parents=True)
            rig_file = service.paths.artifact_file(rig_dir, "rig.ma")
            cmds.file(rename=str(rig_file))
            cmds.file(save=True, type="mayaAscii")
            cmds.file(new=True, force=True)
            cmds.file(str(rig_file), reference=True, namespace="character")
        directory = service.paths.asset_publish_version_dir(identity, "asset", context, "v012")
        directory.mkdir(parents=True)
        source = service.paths.artifact_file(directory, "source.ma")
        cmds.file(rename=str(source))
        cmds.file(save=True, type="mayaAscii")
        job_path = service.prepare(identity, "v012")
        report = export_job(job_path)
        release = service.finalize(identity, report["run_id"])
        assert release["version"] == "v001"
        print("STUDIO_DELIVERY_SMOKE", category, report, flush=True)
finally:
    maya.standalone.uninitialize()
