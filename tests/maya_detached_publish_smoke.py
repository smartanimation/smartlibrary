"""Submit a saved Camera, then terminate this Maya parent without an event loop."""
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages'))

import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
from smartlib.core.config_loader import ProjectConfig
from smartlib.apps.shot_manager import ShotManagerService, ShotIdentity
from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
from smartlib.apps.review_build_manager.detached_publish import DetachedPublishQueue
from smartlib.apps.review_build_manager.publish_queue import QtCore
from smartlib.dcc.maya.publish_scene_input import capture_saved_scene

directory = ROOT / '.tmp' / ('detached-camera-' + uuid.uuid4().hex)
config = directory / 'config'
config.mkdir(parents=True)
(config / 'templates_base.yml').write_text(
    f"anchors:\n  project_name: TEST\n  project_root: '{directory.as_posix()}/project'\n", encoding='utf8')
shots = ShotManagerService(ProjectConfig(config))
service = UsdHandoffService(shots)
identity = ShotIdentity('ep01', 'sq01', 'c001')
cmds.currentUnit(time='film', linear='cm')
camera, shape = cmds.camera(name='primary')
cmds.setKeyframe(camera, attribute='tx', time=1, value=0)
cmds.setKeyframe(camera, attribute='tx', time=2, value=5)
cmds.setKeyframe(shape, attribute='focalLength', time=1, value=35)
cmds.setKeyframe(shape, attribute='focalLength', time=2, value=70)
cmds.file(rename=str(directory / 'saved.ma'))
cmds.file(save=True, type='mayaAscii')
fixed = capture_saved_scene(service, identity)
os.environ['SMARTPIPELINE_MAYAPY'] = sys.executable
app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
queue = DetachedPublishQueue(shots, app)
key = queue.submit(identity, kind='primary_camera_usd', scene_input=fixed,
    scene_options=dict(target=camera, publish_target='primary',
        motion_mode='static' if '--static' in sys.argv else 'animated', sample_frame=2), frame_range=[1, 2])
print('DETACHED_STATUS=' + queue.jobs[key]['status_file'], flush=True)
# No event processing / worker wait: client lifetime ends here.
maya.standalone.uninitialize()
