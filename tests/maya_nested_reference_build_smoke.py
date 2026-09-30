"""Regression for Shot Build of a background containing prop references."""
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
        from smartlib.dcc.maya.shot_builder import _reference_file
        directory = ROOT / '.tmp' / 'nested-reference-build' / uuid.uuid4().hex
        directory.mkdir(parents=True)
        chair = directory / 'chair.mb'
        room = directory / 'room.mb'
        cmds.file(new=True, force=True)
        cmds.polyCube(name='chair_geo')
        cmds.file(rename=str(chair))
        cmds.file(save=True, type='mayaBinary')
        cmds.file(new=True, force=True)
        cmds.createNode('transform', name='Root')
        cmds.file(str(chair), reference=True, namespace='DeleinChair')
        cmds.file(rename=str(room))
        cmds.file(save=True, type='mayaBinary')
        cmds.file(new=True, force=True)
        assert _reference_file(cmds, room, 'DeleinRoomB') == 'DeleinRoomB'
        assert _reference_file(cmds, room, 'DeleinRoomB_copy') == 'DeleinRoomB_copy'
        for namespace in ['DeleinRoomB', 'DeleinRoomB_copy']:
            assert cmds.objExists(namespace + ':DeleinChair:chair_geo')
            assert cmds.objExists(namespace + ':Root')
        shot = directory / 'shot.mb'
        cmds.file(rename=str(shot))
        cmds.file(save=True, type='mayaBinary')
        cmds.file(str(shot), open=True, force=True)
        assert cmds.objExists('DeleinRoomB:DeleinChair:chair_geo')
        assert cmds.objExists('DeleinRoomB_copy:DeleinChair:chair_geo')
        print('PASS: nested background build, repeated file references, namespace preservation, save/reopen')
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
