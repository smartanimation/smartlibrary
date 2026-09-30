import sys
from types import ModuleType

import pytest

from smartlib.dcc.maya import shot_builder
from smartlib.dcc.maya.sequence_inputs import import_virtual_camera


@pytest.mark.parametrize("fail", [False, True])
def test_fbx_import_add_mode_is_scoped(tmp_path, monkeypatch, fail):
    maya = ModuleType("maya")
    mel = ModuleType("maya.mel")
    maya.mel = mel
    mode = ["merge"]
    def evaluate(command):
        if command == 'FBXImportMode -q;':
            return mode[0]
        mode[0] = command.split('"')[1]
    mel.eval = evaluate
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.mel", mel)
    class Cmds:
        def ls(self, **kwargs):
            return []
        def loadPlugin(self, *args, **kwargs):
            pass
        def file(self, path, **kwargs):
            assert mode[0] == "add"
            assert kwargs["namespace"] == "take31"
            if fail:
                raise RuntimeError("import failed")
    if fail:
        with pytest.raises(RuntimeError, match="import failed"):
            shot_builder._import_file(Cmds(), tmp_path / "camera.fbx", "take31")
    else:
        shot_builder._import_file(Cmds(), tmp_path / "camera.fbx", "take31")
    assert mode[0] == "merge"


@pytest.mark.parametrize("virtual", [True, False])
def test_sequence_stages_fbx_without_resolving_or_referencing_rig(tmp_path, monkeypatch, virtual):
    cmds = ModuleType("maya.cmds")
    def ls(*args, **kwargs):
        if kwargs.get("uuid"):
            return ["camera-uuid"]
        if args == ("camera-uuid",):
            return ["|shots_grp|resolvedCamera"]
        return []
    cmds.ls = ls
    cmds.currentTime = lambda *args, **kwargs: None
    maya = ModuleType("maya")
    maya.cmds = cmds
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.cmds", cmds)
    rows = [{"shot": f"c00{i}", "cut_in": i * 100, "cut_out": i * 100 + 99} for i in range(1, 4)]
    monkeypatch.setattr(shot_builder, "_sequence_shot_rows", lambda *args: rows)
    monkeypatch.setattr(shot_builder, "_latest_storyreel_root", lambda *args: tmp_path)
    monkeypatch.setattr(shot_builder, "_storyreel_first_frame", lambda *args: None)
    monkeypatch.setattr(shot_builder, "_ensure_group", lambda *args: "shots_grp")
    monkeypatch.setattr(shot_builder, "_parent_new_assemblies", lambda *args: None)
    references, imported, created = [], [], []
    def rig(*args):
        assert not virtual, "Virtual Camera must not resolve a rig"
        return tmp_path / "rig.ma"
    monkeypatch.setattr(shot_builder, "_resolve_camera_rig", rig)
    def reference(*args):
        assert not virtual, "Virtual Camera must not reference a rig"
        references.append(args[1])
        return "rig"
    monkeypatch.setattr(shot_builder, "_reference_file", reference)
    monkeypatch.setattr(shot_builder, "_first_new_camera", lambda *args: "rig:camera")
    def camera(cmds, path, shot):
        imported.append((shot, path))
        return shot + ":fbxCamera"
    monkeypatch.setattr("smartlib.dcc.maya.sequence_inputs.import_virtual_camera", camera)
    def create(cmds, row, camera, track):
        created.append((row["shot"], camera, row["cut_in"], row["cut_out"]))
        return row["shot"] + "_shot"
    monkeypatch.setattr(shot_builder, "_create_camera_sequencer_shot", create)
    mapping = {row["shot"]: str(tmp_path / (row["shot"] + ".fbx")) for row in rows}
    shot_builder.build_layout_sequence_all(
        {"episode": "ep02", "sequence": "s027"}, project_root=tmp_path,
        camera_inputs=mapping if virtual else None, shot_names=["c001", "c003"],
    )
    assert [row[0] for row in created] == ["c001", "c003"]
    if virtual:
        assert not references
        assert imported == [(shot, mapping[shot]) for shot in ("c001", "c003")]
        assert created[0] == ("c001", "|shots_grp|resolvedCamera", 100, 199)
    else:
        assert len(references) == 2
        assert not imported


@pytest.mark.parametrize("count", [0, 1, 2])
def test_camera_import_uses_filename_namespace_and_validates_camera(tmp_path, monkeypatch, count):
    path = tmp_path / "ep02s27_ep02s27c01_Take06.fbx"
    path.write_bytes(b"fbx")
    imported = []
    class Cmds:
        cameras = []
        def namespace(self, exists):
            return exists == path.stem  # Existing namespace must remain intact.
        def ls(self, **kwargs):
            return self.cameras[:]
        def listRelatives(self, *args, **kwargs):
            return ["imported:camera"]
    cmds = Cmds()
    def importer(cmds, source, namespace):
        imported.append((source, namespace))
        cmds.cameras = [f"camera{i}" for i in range(count)]
    monkeypatch.setattr(shot_builder, "_import_file", importer)
    if count == 1:
        assert import_virtual_camera(cmds, path, "c001") == "imported:camera"
    else:
        with pytest.raises(RuntimeError, match="expected one camera"):
            import_virtual_camera(cmds, path, "c001")
    assert imported == [(path, path.stem + "_1")]
