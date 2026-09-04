"""Run with mayapy in a NEW standalone process, never in an artist's session."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from smartlib.dcc.maya import camera_output as output

    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True)
    rig = cmds.createNode("transform", name="cameraRig")
    camera, shape = cmds.camera(name="shotCam_PRIMARY")
    camera = cmds.parent(camera, rig)[0]
    camera, shape = output.camera_nodes(camera, cmds)
    for frame in (1, 4):
        cmds.setKeyframe(rig, attribute="translateX", time=frame, value=frame * 2)
        cmds.setKeyframe(camera, attribute="rotateY", time=frame, value=170 + frame * 10)
        cmds.setKeyframe(shape, attribute="focalLength", time=frame, value=40 + frame)
        cmds.setKeyframe(shape, attribute="horizontalFilmAperture", time=frame, value=1.4 + frame * 0.01)
    cmds.setAttr(f"{shape}.horizontalFilmOffset", 0.06)
    cmds.setAttr(f"{shape}.verticalFilmOffset", -0.04)
    cmds.currentTime(2)
    cmds.select(camera)
    rows = [
        dict(layer="CHA", width=2048, height=858, start=1, end=3, camera_fit="horizontal"),
        dict(layer="BGA", width=1920, height=1080, start=2, end=4, camera_fit="vertical"),
    ]
    for film_fit in range(4):
        cmds.setAttr(f"{shape}.filmFit", film_fit)
        result = output.generate_output_cameras(camera, rows, (1920, 1080), cmds=cmds)
        assert len(result) == 2
        assert cmds.currentTime(query=True) == 2
        assert cmds.ls(selection=True, long=True) == [camera]
        for entry, row in zip(result, rows):
            derived, derived_shape = output.camera_nodes(entry["camera"], cmds)
            assert output.primary_camera(derived, cmds) == camera
            assert cmds.getAttr(f"{derived_shape}.overscan") == 1
            assert cmds.keyframe(derived, attribute="tx", query=True, timeChange=True) == list(range(row["start"], row["end"] + 1))
            for frame in range(row["start"], row["end"] + 1):
                cmds.currentTime(frame)
                assert cmds.getAttr(f"{derived_shape}.focalLength") == cmds.getAttr(f"{shape}.focalLength")
        cmds.currentTime(2)
    assert len(cmds.ls(f"*.{output.OWNER_ATTR}", objectsOnly=True)) == 2
    assert result[0]["camera"] == "|smartCam_CHA"
    assert result[1]["camera"] == "|smartCam_BGA"
    camera_uuid = cmds.ls("smartCam_CHA", uuid=True)[0]
    legacy = cmds.rename("smartCam_CHA", "smartCam_CHA_legacy123", ignoreShape=True)
    cmds.select(legacy)
    output.generate_output_cameras(camera, rows, (1920, 1080), cmds=cmds)
    assert cmds.ls("smartCam_CHA", uuid=True)[0] == camera_uuid
    assert cmds.ls(selection=True, long=True) == ["|smartCam_CHA"]
    assert not cmds.objExists("smartCam_CHA_legacy123")
    occupied = cmds.createNode("transform", name="smartCam_COLLISION")
    try:
        output.generate_output_cameras(camera, [{**rows[0], "layer": "COLLISION"}], (1920, 1080), cmds=cmds)
        raise AssertionError("An unmanaged name collision must fail")
    except ValueError as exc:
        assert "already in use" in str(exc)
    assert cmds.objExists(occupied)
    cmds.select(camera)
    cmds.setAttr(f"{shape}.cameraScale", 1.3)
    cmds.setAttr(f"{shape}.lensSqueezeRatio", 1.2)
    output.generate_output_cameras(camera, rows, (1920, 1080), cmds=cmds)
    output.generate_output_cameras(camera, [{**rows[0], "camera_fit": "pixel_scale"}], (1920, 1080), cmds=cmds)
    # Gate guide tokens must resolve the source without changing output geometry.
    import importlib.util
    from types import SimpleNamespace
    plugin_path = Path(__file__).resolve().parent / "plug-ins" / "smart_viewport_gate_guides.py"
    module_spec = importlib.util.spec_from_file_location("smart_gate_test", plugin_path)
    plugin = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(plugin)
    cls = plugin.SmartGateGuideDrawOverride
    derived, derived_shape = output.camera_nodes(result[0]["camera"], cmds)
    path = output._camera_fn(derived_shape).dagPath()
    assert cls._primary_camera_path(path).fullPathName() == shape
    assert abs(cls._device_aspect_ratio(path) - 2048 / 858) < 1e-8
    helper = SimpleNamespace(**{name: getattr(cls, name) for name in (
        "_primary_camera_path", "_camera_transform_name", "_camera_shape_name", "_format_anim_time")})
    tokens = cls._parse_text(helper, "{camera}|{focal_length}|{output_camera}", path, SimpleNamespace(counter_padding=4))
    assert tokens.split("|")[0] == camera.rsplit("|", 1)[-1]
    assert tokens.split("|")[2] == derived.rsplit("|", 1)[-1]
    before = set(cmds.ls(type="camera", long=True))
    cmds.setAttr(f"{shape}.panZoomEnabled", True)
    try:
        output.generate_output_cameras(camera, [{**rows[0], "layer": "FAIL"}], (1920, 1080), cmds=cmds)
        raise AssertionError("Unsupported source should fail")
    except ValueError:
        pass
    assert set(cmds.ls(type="camera", long=True)) == before
    cmds.setAttr(f"{shape}.panZoomEnabled", False)
    try:
        output.generate_output_cameras(camera, [{**rows[0], "layer": "CANCEL"}], (1920, 1080), cmds=cmds,
                                       progress=lambda done, total: done < 1)
        raise AssertionError("Cancel should fail")
    except RuntimeError as exc:
        assert "cancelled" in str(exc)
    assert set(cmds.ls(type="camera", long=True)) == before
    assert cmds.currentTime(query=True) == 2
    print(json.dumps({"status": "passed", "checks": ["all filmFit modes", "animated rig and lens", "film offsets", "cameraScale and lens squeeze", "different resolutions/ranges", "pixel-scale canvas", "idempotent update", "primary Burn-in tokens", "rollback", "cancel", "time/selection restored"]}))
    maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
