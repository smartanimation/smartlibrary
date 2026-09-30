"""Read-only real-project Camera Batch restore check in an isolated mayapy."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages'))
from smartlib.core.config_loader import ProjectConfig
from smartlib.apps.shot_manager import ShotManagerService, ShotIdentity

shots = ShotManagerService(ProjectConfig(Path(sys.argv[1])))
identity = ShotIdentity(*sys.argv[2:5])
paths = shots._latest_review_camera_paths(identity)
assert len(paths) > 1, paths
resolved = shots.resolved_construct(identity)
components = [c for c in resolved['components'] if c['component_type'] == 'camera' and c['enabled']]
assert {str(Path(c['path'])) for c in components} == {str(Path(p)) for p in paths}, components

import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
from smartlib.dcc.maya.primary_camera import restore

def sample(node, frames):
    shape = cmds.listRelatives(node, shapes=True, fullPath=True, type='camera')[0]
    values = []
    for frame in frames:
        cmds.currentTime(frame)
        values.append(list(cmds.xform(node, query=True, worldSpace=True, matrix=True)) +
                      [cmds.getAttr(shape + '.' + attr) for attr in
                       ('focalLength', 'horizontalFilmAperture', 'verticalFilmAperture',
                        'horizontalFilmOffset', 'verticalFilmOffset')])
    return values

try:
    expected = {}
    for path in paths:
        data = json.loads(Path(path).read_text(encoding='utf8'))
        start, end = data['frame_range']
        frames = [start, (start + end) // 2, end]
        cmds.file(str(shots.paths.manifest_source(path, data['files']['ma'])), open=True,
                  force=True, executeScriptNodes=False)
        expected[path] = (data, frames, sample(data['primary_path'], frames))
    cmds.file(new=True, force=True)
    for unit, value in next(iter(expected.values()))[0]['units'].items():
        cmds.currentUnit(**{unit: value})
    for path in paths:
        data, frames, original = expected[path]
        node = restore(data, cmds=cmds, provenance=path)
        if data['role'] == 'primary':
            node = next(n for n in cmds.ls(type='transform', long=True)
                        if cmds.objExists(n + '.smartCameraRole') and cmds.getAttr(n + '.smartCameraRole') == 'primary')
        actual = sample(node, frames)
        assert all(abs(a-b) < 1e-5 for left, right in zip(original, actual) for a,b in zip(left,right)), path
    shapes = [s for s in cmds.ls(type='camera', long=True)
              if s.rsplit('|', 1)[-1] not in ('perspShape','topShape','frontShape','sideShape')]
    assert len(shapes) == len(paths), shapes
    from smartlib.apps.review_build_manager.worker import _camera_for_layer
    cameras = [cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in shapes]
    for name in ('CHA','CHB','BGA'):
        assert _camera_for_layer(cameras, name, 'smartCam_' + name), cameras
    print('REVIEW_CAMERA_BATCH_OK: ' + ', '.join(cameras), flush=True)
finally:
    maya.standalone.uninitialize()
