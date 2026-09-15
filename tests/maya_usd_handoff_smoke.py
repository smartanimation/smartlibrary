"""Run with Maya 2024 mayapy; all generated files stay under .tmp/."""
from pathlib import Path
import hashlib
import json
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages'))


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        import maya.cmds as cmds
        from smartlib.dcc.maya.animation_curves import export_animation_atom_for_cast
        from smartlib.dcc.maya.usd_handoff import export_animation
        directory = ROOT / '.tmp' / ('usd-handoff-maya-' + uuid.uuid4().hex) / 'v001'
        directory.mkdir(parents=True)
        cmds.file(new=True, force=True)
        cmds.currentUnit(time='film', linear='cm')
        ctrl = cmds.circle(name='CTL_main', constructionHistory=False)[0]
        cube = cmds.polyCube(name='body')[0]
        cmds.select(clear=True)
        joint = cmds.joint(name='rigJoint')
        cmds.parent(joint, ctrl)
        cmds.parent(cube, joint)
        second = cmds.polyCube(name='second')[0]
        cmds.parent(second, joint)
        cmds.sets([ctrl], name='allRigSet')
        inner = cmds.sets([cube, second], name='cache')
        cmds.sets([inner], name='cache_geo_set')
        rig = directory / 'rig.ma'
        cmds.file(rename=str(rig))
        cmds.file(save=True, type='mayaAscii', force=True)
        cmds.file(new=True, force=True)
        cmds.file(str(rig), reference=True, namespace='Hero')
        driver = cmds.spaceLocator(name='driver')[0]
        for frame, value in [(1, 0), (2, 5), (3, 10)]:
            cmds.setKeyframe(driver, attribute='tx', time=frame, value=value)
        constraint = cmds.parentConstraint(driver, 'Hero:CTL_main', maintainOffset=False)[0]
        cmds.playbackOptions(minTime=1, maxTime=3)
        cmds.currentTime(2)
        before = cmds.getAttr('Hero:CTL_main.tx')
        # Exercise the exact shared action used by both Data and Publish tabs.
        from smartlib.core.config_loader import ProjectConfig
        from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
        from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
        from smartlib.dcc.maya.animation_data_publish import publish_current_animation_data
        from smartlib.core.metadata import read_json, write_json
        config = directory / 'config'
        config.mkdir()
        (config / 'templates_base.yml').write_text(
            f"anchors:\n  project_name: TEST\n  project_root: '{directory.as_posix()}/project'\n", encoding='utf8')
        service = UsdHandoffService(ShotManagerService(ProjectConfig(config)))
        identity = ShotIdentity('ep01', 'sq01', 'sh001')
        write_json(service.paths.artifact_file(service.shots.shot_root(identity), 'cast.json'),
            {'cast': {'Hero': {'asset': 'Hero', 'namespace': 'Hero'}}})
        captured = publish_current_animation_data(service.shots, identity, target='Hero', frame_range=(1, 3))
        captured_data = read_json(captured, {})
        assert captured_data['version'] == 'v001' and captured_data['payload_sha256']
        assert cmds.objExists(constraint), 'Data publication destroyed source constraint'
        captured_again = publish_current_animation_data(service.shots, identity, target='Hero', frame_range=(1, 3))
        assert read_json(captured_again, {})['version'] == 'v002'
        atom = directory / 'animation.atom'
        data = export_animation_atom_for_cast(atom, cast_key='Hero', namespace='Hero', frame_range=(1, 3))
        assert cmds.objExists(constraint), 'source constraint was destroyed'
        cmds.currentTime(2)
        assert abs(cmds.getAttr('Hero:CTL_main.tx') - before) < 1e-5, 'source motion changed'
        assert data['constraint_bake']['plugs'], 'constraint motion was not baked'
        manifest = directory / 'animation_manifest.json'
        manifest.write_text(json.dumps(data), encoding='utf8')
        row = {'target': 'Hero', 'source': {'path': str(manifest), 'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()},
               'rig': {'path': str(rig)}, 'sculpt': None}
        plan = {'frame_range': [1, 3], 'fps': 24, 'usd': {'up_axis': 'Y', 'meters_per_unit': .01}}
        output = directory / 'deform.usdc'
        receipt = export_animation(row, plan, output)
        assert receipt['ok'] and output.is_file()
        from pxr import Usd, UsdGeom
        stage = Usd.Stage.Open(str(output))
        assert stage.GetDefaultPrim().GetPath() == '/Geometry'
        assert len([p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]) == 2
        mesh = next(p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh))
        matrix = UsdGeom.XformCache(3).GetLocalToWorldTransform(mesh)
        assert abs(matrix.ExtractTranslation()[0] - 10) < 1e-4
        assert abs(UsdGeom.XformCache(1).GetLocalToWorldTransform(mesh).ExtractTranslation()[0]) < 1e-4
        selection = service.plan(identity, [dict(kind='animation', target='Hero',
            source=str(captured_again), rig=str(rig))], frame_range=[1, 3], fps=24)
        published = service.publish(identity, selection, animation_exporter=export_animation)
        assert service.load_handoff(published)['approval'] == 'not_reviewed'
        print('PASS: Constraint -> ATOM -> fixed Rig -> USD; source unchanged. ' + str(output))
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
