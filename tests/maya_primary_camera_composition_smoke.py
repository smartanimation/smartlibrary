"""Synthetic Maya camera -> portable worker -> composition; .tmp output only."""
import sys
import subprocess
import uuid
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages'))


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        import maya.cmds as cmds
        from smartlib.core.metadata import read_json
        from smartlib.core.config_loader import ProjectConfig
        from smartlib.apps.shot_manager import ShotManagerService, ShotIdentity
        from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
        from smartlib.dcc.maya import primary_camera
        from pxr import Usd, UsdGeom
        directory = ROOT / '.tmp' / ('camera-composition-' + uuid.uuid4().hex)
        config_dir = directory / 'config'
        config_dir.mkdir(parents=True)
        (config_dir / 'templates_base.yml').write_text(
            f"anchors:\n  project_name: TEST\n  project_root: '{directory.as_posix()}/project'\n", encoding='utf8')
        shots = ShotManagerService(ProjectConfig(config_dir))
        identity = ShotIdentity('ep01', 'sq01', 'c001')
        cmds.currentUnit(time='film', linear='cm')
        camera, shape = cmds.camera(name='testPrimary')
        for frame, value in ((1, 0), (2, 5), (3, 10)):
            cmds.setKeyframe(camera, attribute='tx', time=frame, value=value)
        payload = primary_camera.collect(camera, [1, 3], cmds)
        snapshot = shots.publish_shot_scene_snapshot(identity, payload, data_type='camera', target='testPrimary',
            native_exporter=lambda dest: primary_camera.export_native(payload, dest, cmds))
        # The queue owns the worker even after the submitting QObject is gone.
        from smartlib.apps.review_build_manager.publish_queue import PublishQueue, QtCore
        os.environ['SMARTPIPELINE_MAYAPY'] = sys.executable
        app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
        queue = PublishQueue(shots, app)
        origin = QtCore.QObject()
        job_id = queue.submit(identity, kind='primary_camera_usd', snapshot=snapshot, frame_range=[1, 3])
        origin.deleteLater()
        loop = QtCore.QEventLoop()
        queue.changed.connect(lambda key: loop.quit() if key == job_id and
            queue.jobs[key]['state'] in ('COMPLETE', 'FAILED') else None)
        timeout = QtCore.QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timeout.start(60000)
        loop.exec_()
        timeout.stop()
        job = queue.jobs[job_id]
        assert job['state'] == 'COMPLETE', (job['message'], job['stderr'])
        data = read_json(snapshot, {})
        assert data['portable_export']['status'] == 'complete'
        svc = UsdHandoffService(shots)
        result = job['message']
        composition = svc.load_handoff(result)
        stage = Usd.Stage.Open(composition['entrypoint']['path'])
        cameras = [p for p in stage.Traverse() if p.IsA(UsdGeom.Camera)]
        assert len(cameras) == 1
        assert abs(UsdGeom.XformCache(3).GetLocalToWorldTransform(cameras[0]).ExtractTranslation()[0] - 10) < 1e-5
        assert cmds.objExists(camera)
        print('PASS: Primary USD referenced by camera.usda and shot.usda: ' + str(result))
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
