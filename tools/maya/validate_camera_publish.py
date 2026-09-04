"""Real Maya camera package roundtrip; only disposable scenes/temp files."""
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages'))


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    import maya.cmds as cmds
    from smartlib.dcc.maya import camera_output as co, camera_publish as cp, shot_builder
    from smartlib.dcc.maya.review_playblast import load_scene_playblast_settings

    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True)
    camera, shape = cmds.camera(name='creativeCam')
    rig = cmds.group(camera, name='originalRig')
    cmds.setKeyframe(rig, attribute='tx', time=10, value=2.)
    cmds.setKeyframe(rig, attribute='tx', time=14, value=20.)
    cmds.setKeyframe(shape, attribute='focalLength', time=10, value=28.)
    cmds.setKeyframe(shape, attribute='focalLength', time=14, value=52.)
    cmds.setKeyframe(shape, attribute='horizontalFilmOffset', time=10, value=.1)
    cmds.setKeyframe(shape, attribute='horizontalFilmOffset', time=14, value=.2)
    cmds.setAttr(shape + '.filmFit', 3)
    cmds.setAttr(shape + '.overscan', 1.3)
    rows = [dict(layer='CHA', width=2048, height=858, start=10, end=12, camera_fit='horizontal',
                 enabled=True, version=3, take=2, preset='layout_geometry', mode='Custom'),
            dict(layer='BGA', width=1280, height=720, start=11, end=14, camera_fit='pixel_scale',
                 enabled=True, version=2, take=5, preset='layout_geometry', mode='Custom')]
    results = co.generate_output_cameras(camera, rows, [1920, 1080], cmds=cmds)
    for row, result in zip(rows, results):
        row['camera'] = result['camera']
    cmds.currentTime(11)
    cmds.select(camera)
    original_sel = cmds.ls(selection=True, long=True)
    package = cp.collect_package(camera, rows, [1920, 1080], cmds=cmds)
    assert cmds.currentTime(query=True) == 11
    assert cmds.ls(selection=True, long=True) == original_sel
    stale = copy.deepcopy(rows)
    stale[0]['width'] = 1000
    try:
        cp.collect_package(camera, stale, [1920, 1080], cmds=cmds)
    except ValueError:
        pass
    else:
        raise AssertionError('stale resolution accepted')
    with tempfile.TemporaryDirectory(prefix='smart-camera-publish-') as temporary:
        path = Path(temporary) / 'camera.json'
        path.write_text(json.dumps(package), encoding='utf-8')
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        construct = dict(components=[dict(component_type='camera', enabled=True, name='main',
                                         path=str(path), source=dict(kind='published_camera'))])
        shot_builder._apply_construct_cameras(cmds, Path(temporary), {}, construct, 5.)
        prefs = json.loads(cmds.getAttr(cp.SETTINGS_NODE + '.settingsJson'))
        primary = cmds.ls(prefs['primary_uuid'], long=True)[0]
        assert primary.startswith('|camera_grp|smartCameraPublish|'), primary
        settings = load_scene_playblast_settings(cmds)
        assert [r['camera'] for r in settings['rows']] == ['smartCam_CHA', 'smartCam_BGA'], settings
        assert [(r['start'], r['end']) for r in settings['rows']] == [(15., 17.), (16., 19.)]
        assert not prefs['auto_update']
        for entry in package['cameras']:
            node, shape = co.camera_nodes(entry['name'], cmds)
            assert cmds.getAttr(node + '.' + cp.ROLE_ATTR) == entry['role']
            for sample in entry['samples']:
                cmds.currentTime(sample['frame'] + 5)
                cp._close(cmds.xform(node, query=True, worldSpace=True, matrix=True), sample['world_matrix'], 'matrix')
                for attr, value in sample['attributes'].items():
                    cp._close([cmds.getAttr(shape + '.' + attr)], [value], attr)
                cp._close(co._camera_fn(shape).getRenderingFrustum(entry['resolution'][0] / entry['resolution'][1]),
                          sample['frustum'], 'frustum')
            if entry['role'] == 'generated':
                assert co.primary_camera(node, cmds) == primary
        # Save/reopen preserves message connections and settings despite changed paths.
        scene = Path(temporary) / 'built.ma'
        cmds.file(rename=str(scene))
        cmds.file(save=True, type='mayaAscii', force=True)
        cmds.file(str(scene), open=True, force=True)
        assert co.primary_camera('smartCam_CHA', cmds) == primary
        restored_rows = load_scene_playblast_settings(cmds)['rows']
        ids = cmds.ls('smartCam_CHA', 'smartCam_BGA', uuid=True)
        co.generate_output_cameras(primary, restored_rows, [1920, 1080], cmds=cmds)
        assert cmds.ls('smartCam_CHA', 'smartCam_BGA', uuid=True) == ids
        legacy = dict(rows=[dict(layer='CHA', camera='wrong_camera', width=1)])
        merged = cp.merge_build_settings(legacy, cmds)
        assert merged['rows'][0]['camera'] == 'smartCam_CHA'
        before = set(cmds.ls(long=True))
        try:
            cp.restore_package(package, cmds=cmds)
        except ValueError:
            pass
        else:
            raise AssertionError('collision accepted')
        assert set(cmds.ls(long=True)) == before
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        broken = copy.deepcopy(package)
        broken['cameras'][1]['samples'][0]['frustum'][0] += 100
        before = set(cmds.ls(long=True))
        try:
            cp.restore_package(broken, cmds=cmds)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid projection accepted')
        assert set(cmds.ls(long=True)) == before, 'restore did not roll back'
    print('PASS: camera publish/build, optics/matrices, path changes, timing, settings, regenerate, collision, rollback')
    maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
