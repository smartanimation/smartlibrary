"""Saved constrained scene -> detached Data capture / rebuild; parent exits."""
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

directory = ROOT / '.tmp' / ('detached-animation-' + uuid.uuid4().hex)
config = directory / 'config'
config.mkdir(parents=True)
(config / 'templates_base.yml').write_text(
    f"anchors:\n  project_name: TEST\n  project_root: '{directory.as_posix()}/project'\n", encoding='utf8')
service = UsdHandoffService(ShotManagerService(ProjectConfig(config)))
identity = ShotIdentity('ep01', 'sq01', 'c001')
cmds.currentUnit(time='film', linear='cm')
ctrl = cmds.circle(name='CTL_main', constructionHistory=False)[0]
cube = cmds.polyCube(name='body')[0]
cmds.parent(cube, ctrl)
cmds.sets([ctrl], name='allRigSet')
cmds.sets([cube], name='cache_geo_set')
rig = directory / 'v001' / 'rig.ma'
rig.parent.mkdir()
cmds.file(rename=str(rig))
cmds.file(save=True, type='mayaAscii')
cmds.file(new=True, force=True)
cmds.file(str(rig), reference=True, namespace='Hero')
driver = cmds.spaceLocator(name='driver')[0]
for frame, value in ((1, 0), (2, 5), (3, 10)):
    cmds.setKeyframe(driver, attribute='tx', time=frame, value=value)
cmds.parentConstraint(driver, 'Hero:CTL_main', maintainOffset=False)
cmds.file(str(rig), reference=True, namespace='Friend')
cmds.parentConstraint(driver, 'Friend:CTL_main', maintainOffset=False)
cmds.playbackOptions(minTime=1, maxTime=3)
cmds.file(rename=str(directory / 'saved.ma'))
cmds.file(save=True, type='mayaAscii')
fixed = capture_saved_scene(service, identity)
os.environ['SMARTPIPELINE_MAYAPY'] = sys.executable
app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
queue = DetachedPublishQueue(service.shots, app)
def options(target):
    return dict(target=target, rig=service.pin(rig), rig_context='', sculpt=None,
                cast=dict(asset=target, namespace=target))
for selection in (options('Hero'), options('Friend'), {'targets': [options('Hero'), options('Friend')]}):
    key = queue.submit(identity, kind='animation_usd', scene_input=fixed,
        scene_options=selection, frame_range=[1, 3], merge_animation=True)
    print('DETACHED_STATUS=' + queue.jobs[key]['status_file'], flush=True)
maya.standalone.uninitialize()
