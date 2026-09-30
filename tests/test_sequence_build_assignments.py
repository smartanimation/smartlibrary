from dataclasses import replace

import pytest

from smartlib.apps.shot_manager import SequenceIdentity, ShotIdentity
from smartlib.apps.smart_sequence_builder.service import SmartSequenceBuilderService
from smartlib.dcc.maya.sequence_inputs import apply_sequence_inputs
from test_smart_sequence_builder import _config, _json


def assigned_plan(tmp_path):
    service = SmartSequenceBuilderService(_config(tmp_path / "config", tmp_path / "project"))
    identity = SequenceIdentity("ep02", "s027")
    root = service.shots.sequence_workspace_root("ep02", "s027")
    _json(root / "sequence.json", {"episode": "ep02", "sequence": "s027", "fps": 24,
          "shots": [{"shot": f"c00{i}", "cut_in": i * 100, "cut_out": i * 100 + 99} for i in range(1, 4)]})
    _json(root / "cast.json", {"cast": {name: {"asset": name, "category": "character", "namespace": name} for name in ("DLI", "JIN")}})

    def entry(kind, target):
        path = service.shots.paths.sequence_data_version_dir("ep02", "s027", kind, "fbx", target, "v002") / "input.fbx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fbx")
        return dict(id=target, target=target, type=kind, role="body_motion" if kind == "mocap" else "import_fbx",
                    status="selected", source=str(path), version="v002", name=target, representation="fbx")

    service.shots.write_dependencies(identity, {"dependencies": [entry("mocap", name) for name in ("DLI", "JIN")]})
    for i in range(1, 4):
        service.shots.write_dependencies(ShotIdentity("ep02", "s027", f"c00{i}"), {"dependencies": [entry("virtual_camera", f"take{i}")]})
    return service


def test_sequence_and_shot_assignments_resolve_exact_files(tmp_path):
    service = assigned_plan(tmp_path)
    plan = service.plan("ep02", "s027", "Mocap + Virtual Camera", virtual_camera_take="stale_take")
    assert plan.can_build
    motion, camera = [next(row for row in plan.inputs if row.key == key) for key in ("mocap", "virtual_camera")]
    assert [row.key for row in motion.children] == ["DLI", "JIN"]
    assert [row.key for row in camera.children] == ["c001", "c002", "c003"]
    assert all(row.state == "READY" and row.version == "v002" for row in (*motion.children, *camera.children))
    assert not plan.virtual_camera_take
    from pathlib import Path
    Path(camera.children[1].path).unlink()
    plan = service.plan("ep02", "s027", "Mocap + Virtual Camera")
    assert not plan.can_build
    assert next(row for row in plan.inputs if row.key == "virtual_camera").children[1].state == "MISSING"


def test_maya_imports_sequence_motion_and_connects_selected_shot_camera(tmp_path, monkeypatch):
    service = assigned_plan(tmp_path)
    plan = service.plan("ep02", "s027", "Mocap + Virtual Camera")
    imports, connected = [], []

    class Cmds:
        cameras = []
        def ls(self, **kwargs):
            return list(self.cameras)
        def listRelatives(self, camera, **kwargs):
            return [camera + "Transform"]
        def objExists(self, name):
            return name == "c002_shot"
        def shot(self, name, **kwargs):
            connected.append((name, kwargs["currentCamera"]))

    cmds = Cmds()
    def importer(cmds, path, namespace):
        imports.append((str(path), namespace))
        if "virtual_camera" in namespace:
            cmds.cameras.append(namespace + ":cameraShape")
    monkeypatch.setattr("smartlib.dcc.maya.shot_builder._import_file", importer)
    paths = apply_sequence_inputs(plan.inputs, shot_names=["c002"], cmds=cmds)
    assert len(paths) == 3
    assert [namespace for _, namespace in imports] == ["input_mocap_DLI", "input_mocap_JIN", "input_virtual_camera_c002"]
    assert connected == [("c002_shot", "input_virtual_camera_c002:cameraShapeTransform")]
    disabled = tuple(replace(row, enabled=False) for row in plan.inputs)
    assert apply_sequence_inputs(disabled, cmds=cmds) == []


def test_ambiguous_camera_fails_build(tmp_path, monkeypatch):
    service = assigned_plan(tmp_path)
    plan = service.plan("ep02", "s027", "Mocap + Virtual Camera")
    class Cmds:
        cameras = []
        def ls(self, **kwargs):
            return list(self.cameras)
    cmds = Cmds()
    def importer(cmds, path, namespace):
        if "virtual_camera" in namespace:
            cmds.cameras.extend([namespace + ":cam1", namespace + ":cam2"])
    monkeypatch.setattr("smartlib.dcc.maya.shot_builder._import_file", importer)
    with pytest.raises(RuntimeError, match="expected one camera"):
        apply_sequence_inputs(plan.inputs, cmds=cmds)
