"""Verify a tests/maya_detached_publish_smoke.py --static fixture with mayapy."""
import json
from pathlib import Path
import sys

import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
cmds.loadPlugin('mayaUsdPlugin', quiet=True)
from pxr import Usd, UsdGeom

root = Path(sys.argv[1])
motion = dict(mode='static', animation_required=False, sample_frame=2)
try:
    snapshot = next(root.rglob('camera.json'))
    data = json.loads(snapshot.read_text(encoding='utf8'))
    assert data['camera_motion'] == motion
    assert json.loads(snapshot.with_name('publish.json').read_text())['camera_motion'] == motion
    native = snapshot.parent / data['files']['ma']
    cmds.file(str(native), open=True, force=True, executeScriptNodes=False)
    shape = (cmds.listRelatives(data['primary_path'], shapes=True, fullPath=True) or [])[0]
    for frame in (1, 2, 20):
        cmds.currentTime(frame)
        assert abs(cmds.xform(data['primary_path'], q=True, ws=True, translation=True)[0] - 5) < 1e-5
        assert cmds.getAttr(shape + '.focalLength') == 70
    usd = Usd.Stage.Open(str(snapshot.parent / data['files']['usd']))
    assert usd.GetRootLayer().customLayerData['camera_motion'] == motion
    composition = next(root.rglob('shot.usda'))
    stage = Usd.Stage.Open(str(composition))
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (1, 2)
    cameras = [p for p in stage.Traverse() if p.IsA(UsdGeom.Camera)]
    assert len(cameras) == 1
    camera = cameras[0]
    assert camera.GetCustomDataByKey('camera_motion') == motion
    for prim in stage.Traverse():
        assert not any(attr.GetNumTimeSamples() for attr in prim.GetAttributes())
    for frame in (1, 2, 20):
        assert UsdGeom.Camera(camera).GetFocalLengthAttr().Get(frame) == 70
        assert abs(UsdGeom.XformCache(frame).GetLocalToWorldTransform(camera).ExtractTranslation()[0] - 5) < 1e-5
    manifests = [json.loads(p.read_text(encoding='utf8')) for p in root.rglob('manifest.json')]
    products = [p for p in manifests if p.get('schema') == 'smartpipeline.usd_handoff_product.v1']
    assert len(products) == 1 and products[0]['inputs']['camera_motion'] == motion
    sections = [p for p in manifests if p.get('schema') == 'smartpipeline.usd_handoff_section.v1']
    assert len(sections) == 1 and 'animation_required' in json.dumps(sections[0])
    print('STATIC_CAMERA_OK: native, USD, composition, product and section metadata')
finally:
    maya.standalone.uninitialize()
