"""Real Maya worker/queue smoke test. All generated output stays under .tmp."""
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages'))


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        from pxr import Usd, UsdGeom
        from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
        from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
        from smartlib.apps.review_build_manager.publish_queue import PublishQueue, QtCore
        from smartlib.core.config_loader import ProjectConfig
        directory = ROOT / '.tmp' / ('assets-publish-' + uuid.uuid4().hex)
        config = directory / 'config'
        config.mkdir(parents=True)
        (config / 'templates_base.yml').write_text(
            f"anchors:\n  project_name: TEST\n  project_root: '{directory.as_posix()}/project'\n", encoding='utf8')
        shots = ShotManagerService(ProjectConfig(config))
        service = UsdHandoffService(shots)
        identity = ShotIdentity('ep01', 'sq01', 'c001')
        source = directory / 'fixtures' / 'v001' / 'room.usda'
        source.parent.mkdir(parents=True)
        stage = Usd.Stage.CreateNew(str(source))
        root = UsdGeom.Xform.Define(stage, '/Room')
        stage.SetDefaultPrim(root.GetPrim())
        UsdGeom.Cube.Define(stage, '/Room/geo')
        UsdGeom.SetStageUpAxis(stage, 'Y')
        UsdGeom.SetStageMetersPerUnit(stage, .01)
        stage.GetRootLayer().Save()
        plan = service.plan(identity, [
            dict(kind='assets', target='Room', source=str(source), geometry_source='asset'),
            dict(kind='assets', target='Hero', source=None, geometry_source='animation')],
            frame_range=[1, 2], replace_assets=True)
        os.environ['SMARTPIPELINE_MAYAPY'] = sys.executable
        app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
        queue = PublishQueue(shots, app)
        key = queue.submit(identity, kind='assets_usd', plan=plan)
        loop = QtCore.QEventLoop()
        queue.changed.connect(lambda changed: loop.quit() if changed == key and
            queue.jobs[key]['state'] in ('COMPLETE', 'FAILED') else None)
        timer = QtCore.QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(60000)
        loop.exec_()
        timer.stop()
        job = queue.jobs[key]
        assert job['state'] == 'COMPLETE', (job['message'], job['stderr'])
        snapshot = service.load_handoff(job['message'])
        result = Usd.Stage.Open(snapshot['entrypoint']['path'], load=Usd.Stage.LoadNone)
        assert result.GetPrimAtPath('/Shot/Assets/Room').HasPayload()
        assert not result.GetPrimAtPath('/Shot/Assets/Room/geo')
        result.Load()
        assert result.GetPrimAtPath('/Shot/Assets/Room/geo')
        assert not result.GetPrimAtPath('/Shot/Assets/Hero').HasPayload()
        assert not result.GetPrimAtPath('/Shot/Assets/Hero/geo')
        print('PASS: Assets queue / real Maya worker / Payload / metadata only')
        print(job['message'])
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
