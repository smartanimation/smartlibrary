"""Synthetic Primary/derived updates and Placement/Set Dress batches under .tmp."""
import json
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
        from smartlib.core.metadata import write_json
        from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
        from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
        from smartlib.dcc.maya import camera_live, camera_output, camera_batch_publish, layout_publish
        from smartlib.dcc.maya.review_playblast import save_scene_playblast_settings
        from smartlib.dcc.maya.publish_scene_input import capture_saved_scene
        from smartlib.dcc.maya.placement import (_tag_placement_locator, _set_string_attr,
            MEMBER_ATTR, ATTACH_ROOT_ATTR, set_placement_motion)
        from smartlib.dcc.maya.set_dress import (SetDressPackage, SetDressLayer, Change, embed_package_in_scene)
        directory = ROOT / '.tmp' / ('shot-usd-batch-' + uuid.uuid4().hex)
        config = directory / 'config'
        config.mkdir(parents=True)
        (config / 'templates_base.yml').write_text(
            "anchors:\n  project_name: TEST\n  project_root: '" + (directory / 'project').as_posix() + "'\n", encoding='utf8')
        svc = UsdHandoffService(ShotManagerService(ProjectConfig(config)))
        identity = ShotIdentity('ep01', 'sq01', 'c001')
        cmds.file(new=True, force=True)
        cmds.currentUnit(time='film', linear='cm')
        cmds.upAxis(axis='y')
        cmds.undoInfo(state=True)
        camera, shape = cmds.camera(name='cam')
        cmds.setKeyframe(camera, attribute='translateX', time=1, value=1)
        cmds.setKeyframe(camera, attribute='translateX', time=3, value=3)
        rows = [dict(layer=name, start=1, end=3, camera_rule={'mode': 'scale', 'scale': 1.1}) for name in ('CHA', 'BGA')]
        def save_settings(scale):
            rows[0]['camera_rule']['scale'] = scale
            result = camera_live.configure(camera, rows, [1280, 720], cmds=cmds)
            prefs = dict(primary=camera, reference_resolution=[1280, 720],
                         layer_rules={r['layer']: r['camera_rule'] for r in rows})
            if not cmds.objExists(':smartCameraPlayblastInfo'):
                cmds.createNode('network', name=':smartCameraPlayblastInfo')
            camera_output._string_attr(cmds, ':smartCameraPlayblastInfo', 'settingsJson', json.dumps(prefs))
            save_scene_playblast_settings(dict(rows=[dict(r, enabled=True, start=1, end=3) for r in result]), cmds)
        save_settings(1.1)
        scene = directory / 'camera.ma'
        cmds.file(rename=str(scene))
        cmds.file(save=True, type='mayaAscii', force=True)
        def request(kind, targets, base=None):
            return dict(scene_options=dict(targets=targets, category=kind), frame_range=[1, 3],
                        base_composition=svc.pin(base) if base else None,
                        scene_input=capture_saved_scene(svc, identity, cmds))
        job = request('camera', ['primary'])
        p = camera_batch_publish.capture(svc, identity, job, cmds)
        base = svc.publish(identity, p)
        data = svc.load_handoff(base)
        stage = Usd.Stage.Open(data['entrypoint']['path'])
        cameras = [p for p in stage.Traverse() if p.IsA(UsdGeom.Camera)]
        assert len(cameras) == 3
        for prim in cameras:
            assert abs(UsdGeom.XformCache(3).GetLocalToWorldTransform(prim).ExtractTranslation()[0] - 3) < 1e-5
        cmds.file(str(scene), open=True, force=True)
        save_settings(1.2)
        cmds.file(save=True, force=True)
        job = request('camera', ['smartCam_CHA'], base)
        p = camera_batch_publish.capture(svc, identity, job, cmds)
        second = svc.publish(identity, p, base_composition=svc.pin(base))
        products = [svc.load_handoff(r['path']) for r in svc.load_handoff(second)['products']]
        versions = {p['target']: p['version'] for p in products}
        assert versions == dict(primary='v001', smartCam_CHA='v002', smartCam_BGA='v001'), versions
        cha = next(p for p in products if p['target'] == 'smartCam_CHA')
        assert cha['inputs']['camera_settings']['resolution'] == [1536, 864]
        cmds.file(str(scene), open=True, force=True)
        cmds.setAttr(shape + '.focalLength', 50)
        cmds.file(save=True, force=True)
        try:
            camera_batch_publish.capture(svc, identity, request('camera', ['smartCam_CHA'], second), cmds)
            raise AssertionError('Changed Primary accepted')
        except ValueError as exc:
            assert 'Primary has changed' in str(exc), str(exc)
        cmds.file(save=True, force=True)
        p = camera_batch_publish.capture(svc, identity, request('camera', ['primary'], second), cmds)
        third = svc.publish(identity, p, base_composition=svc.pin(second))
        versions = {svc.load_handoff(r['path'])['target']: svc.load_handoff(r['path'])['version']
                    for r in svc.load_handoff(third)['products']}
        assert versions == dict(primary='v002', smartCam_CHA='v003', smartCam_BGA='v002'), versions
        print('PASS: Primary batch, independent CHA resolution update, changed Primary rejection', flush=True)

        cmds.file(new=True, force=True)
        cmds.currentUnit(time='film', linear='cm')
        root = cmds.group(empty=True, name='Root')
        geo = cmds.polyCube(name='geo')[0]
        cmds.parent(geo, root)
        usd = directory / 'asset' / 'v001' / 'asset.usda'
        usd.parent.mkdir(parents=True)
        stage = Usd.Stage.CreateNew(str(usd))
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, '/Root').GetPrim())
        UsdGeom.Cube.Define(stage, '/Root/geo')
        UsdGeom.SetStageMetersPerUnit(stage, .01)
        UsdGeom.SetStageUpAxis(stage, 'Y')
        stage.GetRootLayer().Save()
        assets = svc.publish(identity, svc.plan(identity, [dict(kind='assets', target='Box', source=str(usd))], frame_range=[1, 3]))
        locator = cmds.spaceLocator(name='chair_place_loc')[0]
        _tag_placement_locator(cmds, locator)
        _set_string_attr(cmds, locator, MEMBER_ATTR, 'Box')
        _set_string_attr(cmds, locator, ATTACH_ROOT_ATTR, root)
        cmds.parentConstraint(locator, root, maintainOffset=False)
        cmds.setKeyframe(locator, attribute='translateX', time=1, value=4)
        cmds.setKeyframe(locator, attribute='translateX', time=3, value=12)
        set_placement_motion(locator, 'CURVE')
        package = SetDressPackage(layers=[SetDressLayer(name='chair', changes=[Change('geo', geo, 'translateY', 0, 5)]),
                                         SetDressLayer(name='desk', changes=[Change('geo', geo, 'translateZ', 0, 8)])])
        embed_package_in_scene(package, cmds=cmds)
        cmds.file(rename=str(directory / 'layout.ma'))
        cmds.file(save=True, type='mayaAscii', force=True)
        job = request('placements', ['chair_place_loc'], assets)
        result = svc.publish(identity, layout_publish.capture(svc, identity, job, cmds), base_composition=svc.pin(assets))
        cmds.file(save=True, force=True)
        job = request('set_dress', ['chair', 'desk'], result)
        job['scene_options']['node_map'] = {'geo': '/Shot/Assets/Box/geo'}
        final = svc.publish(identity, layout_publish.capture(svc, identity, job, cmds), base_composition=svc.pin(result))
        stage = Usd.Stage.Open(svc.load_handoff(final)['entrypoint']['path'])
        value = UsdGeom.XformCache(3).GetLocalToWorldTransform(stage.GetPrimAtPath('/Shot/Assets/Box/geo')).ExtractTranslation()
        assert tuple(value) == (12, 5, 8), value
        # Exercise the registered worker entry point with the same fixed scene.
        import hashlib
        import os
        import subprocess
        worker_request = dict(job, schema='smartpipeline.publish_job.v1', kind='layout_usd',
            config=str(config), shot=dict(episode='ep01', sequence='sq01', shot='c001'),
            maya_config_name='maya2024', maya_config={})
        _, job_dir = svc._reserve(svc.paths.usd_handoff_build_dir('ep01', 'sq01', 'c001'))
        request_path = svc._file(job_dir, 'request.json')
        write_json(request_path, worker_request)
        environment = dict(os.environ, PYTHONPATH=str(ROOT / 'packages'))
        process = subprocess.run([sys.executable, '-m', 'smartlib.apps.review_build_manager.publish_worker',
            '--request', str(request_path), '--sha256', hashlib.sha256(request_path.read_bytes()).hexdigest()],
            env=environment, capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert process.returncode == 0, (process.stdout, process.stderr)
        print('PASS: CURVE Placement Data and two Set Dress overrides -> Layout -> shot.usda', flush=True)
        print(directory, flush=True)
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
