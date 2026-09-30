"""Synthetic Maya STATIC/CURVE -> Assets USD test, confined to .tmp."""
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages'))


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        import maya.cmds as cmds
        from pxr import Usd, UsdGeom
        from smartlib.core.config_loader import ProjectConfig
        from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
        from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
        from smartlib.dcc.maya.placement import (PlacementLocator, _tag_placement_locator,
            _set_string_attr, MEMBER_ATTR, ATTACH_ROOT_ATTR, set_placement_motion)
        from smartlib.dcc.maya.placement_usd import capture_placements
        from smartlib.dcc.maya.proxy_usd import export_proxy_usd
        directory = ROOT / '.tmp' / ('prop-placement-' + uuid.uuid4().hex)
        config = directory / 'config'
        config.mkdir(parents=True)
        (config / 'templates_base.yml').write_text(
            "anchors:\n  project_name: TEST\n  project_root: '" + (directory / 'project').as_posix() + "'\n", encoding='utf8')
        service = UsdHandoffService(ShotManagerService(ProjectConfig(config)))
        identity = ShotIdentity('ep01', 'sq01', 'c001')
        cmds.file(new=True, force=True)
        cmds.currentUnit(linear='cm', time='film')
        cmds.upAxis(axis='y')
        root = cmds.group(empty=True, name='Root')
        cube = cmds.polyCube(name='geo')[0]
        cmds.parent(cube, root)
        cmds.sets(cube, name='cache_geo_set')
        source = directory / 'fixture' / 'v001' / 'Box.usd'
        source.parent.mkdir(parents=True)
        export_proxy_usd(source, {})
        locator = cmds.spaceLocator(name='Box_place')[0]
        _tag_placement_locator(cmds, locator)
        _set_string_attr(cmds, locator, MEMBER_ATTR, 'Box')
        _set_string_attr(cmds, locator, ATTACH_ROOT_ATTR, root)
        cmds.parentConstraint(locator, root, maintainOffset=False)
        cmds.setKeyframe(locator, attribute='translateX', time=1, value=4)
        cmds.setKeyframe(locator, attribute='translateX', time=3, value=12)
        for mode in ('STATIC', 'CURVE'):
            set_placement_motion(locator, mode)
            plan = service.plan(identity, [dict(kind='assets', target='Box', source=str(source),
                geometry_source='asset')], frame_range=[1, 3], replace_assets=True)
            plan['rows'][0]['placement'] = capture_placements(plan)['Box']
            result = service.load_handoff(service.publish(identity, plan))
            stage = Usd.Stage.Open(result['entrypoint']['path'], load=Usd.Stage.LoadNone)
            prim = stage.GetPrimAtPath('/Shot/Assets/Box')
            assert UsdGeom.XformCache(3).GetLocalToWorldTransform(prim).ExtractTranslation()[0] == (4 if mode == 'STATIC' else 12)
            stage.Load()
            meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
            assert len(meshes) == 1
            assert UsdGeom.XformCache(3).GetLocalToWorldTransform(meshes[0]).ExtractTranslation()[0] == (4 if mode == 'STATIC' else 12)
        print('PASS: real Maya cache_geo_set export / Smart Maker STATIC and CURVE / payload load and placement')
        # Exercise the saved-scene branch of the real detached worker as well.
        import hashlib
        import os
        import subprocess
        from smartlib.core.metadata import read_json, write_json
        from smartlib.dcc.maya.publish_scene_input import capture_saved_scene
        cmds.file(rename=str(directory / 'placement.mb'))
        cmds.file(save=True, type='mayaBinary')
        fixed_scene = capture_saved_scene(service, identity)
        plan = service.plan(identity, [dict(kind='assets', target='Box', source=str(source),
            geometry_source='asset'), dict(kind='assets', target='DeformingProp', source=None,
            geometry_source='animation')], frame_range=[1, 3], replace_assets=True)
        _, job_dir = service._reserve(service.paths.usd_handoff_build_dir(*service._identity(identity)))
        request_path = service.paths.artifact_file(job_dir, 'request.json')
        write_json(request_path, dict(schema='smartpipeline.publish_job.v1', kind='assets_usd',
            shot=dict(episode='ep01', sequence='sq01', shot='c001'), config=str(config),
            maya_config_name='software_test.yml', maya_config={}, base_composition=None,
            scene_input=fixed_scene, scene_options={}, frame_range=[1, 3], plan=plan))
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT / 'packages') + os.pathsep + env.get('PYTHONPATH', '')
        process = subprocess.run([sys.executable, '-m', 'smartlib.apps.review_build_manager.publish_worker',
            '--request', str(request_path), '--sha256', hashlib.sha256(request_path.read_bytes()).hexdigest()],
            env=env, capture_output=True, timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert process.returncode == 0, (process.stdout, process.stderr)
        receipt = read_json(service.paths.artifact_file(job_dir, 'result.json'), {})
        assert receipt['ok'], receipt
        snapshot = service.load_handoff(receipt['manifest'])
        stage = Usd.Stage.Open(snapshot['entrypoint']['path'])
        prim = stage.GetPrimAtPath('/Shot/Assets/Box')
        assert UsdGeom.XformCache(3).GetLocalToWorldTransform(prim).ExtractTranslation()[0] == 12
        assert not stage.GetPrimAtPath('/Shot/Assets/DeformingProp').HasPayload()
        print('PASS: saved-scene Worker capture / Assets composition / deforming Prop metadata only')
        print(directory)
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
