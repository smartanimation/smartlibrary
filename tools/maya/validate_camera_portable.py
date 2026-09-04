"""Maya standalone smoke test for world-baked Primary FBX/USD export."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages"))


def main():
    import maya.standalone

    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from smartlib.dcc.maya import camera_native, camera_portable

    cmds.file(new=True, force=True)
    rig = cmds.group(empty=True, name="cameraRig")
    camera, shape = cmds.camera(name="creativeCam")
    cmds.parent(camera, rig)
    cmds.setKeyframe(rig, attribute="translateX", time=10, value=2.0)
    cmds.setKeyframe(rig, attribute="translateX", time=12, value=12.0)
    cmds.setKeyframe(shape, attribute="focalLength", time=10, value=28.0)
    cmds.setKeyframe(shape, attribute="focalLength", time=12, value=50.0)
    rows = [{
        "layer": "CHA", "camera_rule": {"mode": "shared"},
        "camera": camera, "start": 10, "end": 12,
        "width": 1920, "height": 1080, "enabled": True,
        "version": 1, "take": 1, "mode": "Custom", "preset": "layout_lighting",
    }]
    payload = camera_native.collect(camera, rows, [1920, 1080], cmds)
    with tempfile.TemporaryDirectory(prefix="smart-camera-portable-") as temporary:
        files = camera_portable.export_portable(payload, temporary, cmds)
        assert files == {"fbx": "primary_cam.fbx", "usd": "primary_cam.usd"}
        baked_matches = cmds.ls("primary_cam", long=True) or []
        if not baked_matches:
            raise AssertionError("primary_cam missing after export; scene nodes: " + repr(cmds.ls(long=True)))
        baked = baked_matches[0]
        baked_shape = cmds.listRelatives(baked, shapes=True, fullPath=True)[0]
        assert not cmds.listRelatives(baked, parent=True)
        for frame in range(10, 13):
            cmds.currentTime(frame)
            source_matrix = cmds.xform(camera, query=True, worldSpace=True, matrix=True)
            baked_matrix = cmds.xform(baked, query=True, worldSpace=True, matrix=True)
            assert max(abs(a - b) for a, b in zip(source_matrix, baked_matrix)) < 1e-5
            assert abs(cmds.getAttr(shape + ".focalLength") - cmds.getAttr(baked_shape + ".focalLength")) < 1e-5
        assert cmds.getAttr(baked_shape + ".overscan") == 1.0
        assert camera_portable.validate_portable(files, temporary, cmds)
    print("PASS: primary_cam world bake and FBX/USD export")
    maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
