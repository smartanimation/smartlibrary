"""Standalone Maya test: native dependency publish and live expansion, no bake."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages'))


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    import maya.cmds as cmds
    from smartlib.dcc.maya import camera_live as live, camera_native as native, camera_output as co, shot_builder
    from smartlib.dcc.maya.camera_publish import _close
    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True)
    cam, shape = cmds.camera(name='primaryCam')
    cam = cmds.rename(cam, ':primaryCam', ignoreShape=True)
    root = cmds.group(cam, name='cameraRig')
    irrelevant = cmds.polyCube(name='unrelatedBackground')[0]
    cmds.parent(irrelevant, root)
    driver = cmds.createNode('transform', name='cameraDriver')
    constraint = cmds.parentConstraint(driver, cam)[0]
    cmds.setKeyframe(driver, attribute='tx', time=1, value=0)
    cmds.setKeyframe(driver, attribute='tx', time=20, value=10)
    cmds.setKeyframe(shape, attribute='focalLength', time=1, value=30)
    cmds.setKeyframe(shape, attribute='focalLength', time=20, value=60)
    cmds.setAttr(shape + '.horizontalFilmOffset', .1)
    cmds.setAttr(shape + '.verticalFilmOffset', .2)
    rows = [dict(layer='CHA', camera_rule={'mode': 'shared'}, start=1, end=20, enabled=True, version=1, take=1, mode='Custom'),
            dict(layer='CHB', camera_rule={'mode': 'shared'}, start=1, end=20, enabled=True, version=1, take=1, mode='Custom'),
            dict(layer='BGA', camera_rule={'mode': 'scale', 'scale': 1.1}, start=1, end=20, enabled=True, version=1, take=1, mode='Custom')]
    # Camera setup must not set time or write even a single keyframe.
    with patch.object(cmds, 'currentTime', side_effect=AssertionError('frame scan')), \
         patch.object(cmds, 'setKeyframe', side_effect=AssertionError('bake')):
        results = live.configure(cam, rows, [1920, 1080], cmds=cmds)
        nodes = cmds.ls(long=True)
        live.configure(cam, rows, [1920, 1080], cmds=cmds)
        assert cmds.ls(long=True) == nodes
    assert results[0]['camera'] == results[1]['camera'] == '|cameraRig|primaryCam'
    assert (results[2]['width'], results[2]['height']) == (2112, 1188)
    assert not cmds.ls('smartCam_CHA') and not cmds.ls('smartCam_CHB')
    # Rebuilding a live gate must not traverse and cut Primary's animation.
    source_keys = cmds.keyframe(shape, attribute='focalLength', query=True, valueChange=True)
    rows[2]['camera_rule'] = {'mode': 'resolution', 'width': 2200, 'height': 1400}
    with patch.object(cmds, 'setKeyframe', side_effect=AssertionError('update bake')):
        live.configure(cam, rows, [1920, 1080], cmds=cmds)
    assert cmds.keyframe(shape, attribute='focalLength', query=True, valueChange=True) == source_keys
    _, custom_shape = co.camera_nodes('smartCam_BGA', cmds)
    _close(co._camera_fn(custom_shape).getRenderingFrustum(2200 / 1400),
           co.output_frustum(co._camera_fn(shape).getRenderingFrustum(1920 / 1080),
                             [1920, 1080], [2200, 1400], 'pixel_scale'), 'custom material aspect')
    rows[2]['camera_rule'] = {'mode': 'scale', 'scale': 1.1}
    live.configure(cam, rows, [1920, 1080], cmds=cmds)
    dest, out_shape = co.camera_nodes('smartCam_BGA', cmds)
    for frame in [1, 4.5, 20]:
        cmds.currentTime(frame)
        for fit in range(4):
            for squeeze, scale in [(1, 1), (2, 1), (1, 2)]:
                cmds.setAttr(shape + '.filmFit', fit)
                cmds.setAttr(shape + '.lensSqueezeRatio', squeeze)
                cmds.setAttr(shape + '.cameraScale', scale)
                for offset in [0., .3]:
                    cmds.setAttr(shape + '.filmFitOffset', offset)
                    expected = co.output_frustum(co._camera_fn(shape).getRenderingFrustum(1920 / 1080),
                                                  [1920, 1080], [2112, 1188], 'pixel_scale')
                    actual = co._camera_fn(out_shape).getRenderingFrustum(2112 / 1188)
                    _close(actual, expected, f'fit={fit} squeeze={squeeze} scale={scale} offset={offset}')
                    _close(cmds.xform(dest, query=True, worldSpace=True, matrix=True),
                           cmds.xform(cam, query=True, worldSpace=True, matrix=True), 'world matrix')
    with tempfile.TemporaryDirectory(prefix='smart-camera-native-') as folder:
        with patch.object(cmds, 'currentTime', side_effect=AssertionError('publish frame scan')), \
             patch.object(cmds, 'setKeyframe', side_effect=AssertionError('publish bake')):
            payload = native.collect(cam, rows, [1920, 1080], cmds)
            assert not any('unrelatedBackground' in n for n in payload['dependency_nodes'])
            payload['files'] = native.export_native(payload, folder, cmds)
        scene = Path(folder) / 'primary_cam.ma'
        text = scene.read_text(encoding='utf-8')
        assert 'unrelatedBackground' not in text
        assert 'smartCam_BGA' not in text
        path = Path(folder) / 'camera.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        samples = {}
        for frame in [1, 8.25, 20]:
            cmds.currentTime(frame)
            samples[frame] = (cmds.xform(cam, query=True, worldSpace=True, matrix=True), cmds.getAttr(shape + '.focalLength'))
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        construct = dict(components=[dict(component_type='camera', name='Camera Package / main / main', enabled=True,
                                         path=str(path), source=dict(kind='published_camera'))])
        with patch.object(cmds, 'setKeyframe', side_effect=AssertionError('build bake')):
            shot_builder._apply_construct_cameras(cmds, Path(folder), {}, construct, 0.)
        assert cmds.ls(type='parentConstraint'), 'constraint was lost'
        primary = co.primary_camera('smartCam_BGA', cmds)
        primary, shape = co.camera_nodes(primary, cmds)
        assert co.primary_camera('smartCam_BGA', cmds) == primary
        for frame, (matrix, lens) in samples.items():
            cmds.currentTime(frame)
            _close(cmds.xform(primary, query=True, worldSpace=True, matrix=True), matrix, 'restored primary')
            _close([cmds.getAttr(shape + '.focalLength')], [lens], 'restored lens animation')
        # Edits after Build must immediately propagate, without reconfiguration.
        cmds.setAttr('smartPrimary:cameraDriver.ty', 8)
        assert abs(cmds.xform('smartCam_BGA', query=True, worldSpace=True, translation=True)[1] - 8) < 1e-5
        assert len(cmds.ls(type='animCurve')) == 2, cmds.ls(type='animCurve')
        # Publish a referenced rig without retaining an external reference file.
        cmds.file(new=True, force=True)
        cmds.file(str(scene), reference=True, namespace='sourceRig')
        ref_primary = 'sourceRig:primaryCam'
        ref_payload = native.collect(ref_primary, rows, [1920, 1080], cmds)
        ref_dir = Path(folder) / 'reference_test'
        ref_dir.mkdir()
        ref_payload['files'] = native.export_native(ref_payload, ref_dir, cmds)
        native_text = (ref_dir / 'primary_cam.ma').read_text(encoding='utf-8')
        assert 'file -r ' not in native_text and 'file -rdi' not in native_text
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        native.restore(ref_payload, cmds=cmds, provenance=str(ref_dir / 'camera.json'))
        ref_camera = co.primary_camera('smartCam_BGA', cmds)
        assert not cmds.referenceQuery(ref_camera, isNodeReferenced=True)
    print('PASS: shared Primary, 1.1 expansion, animated live optics, native dependencies, subframe roundtrip, no Bake')
    maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
