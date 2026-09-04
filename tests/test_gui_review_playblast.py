import json
from pathlib import Path
import subprocess

import pytest

from smartlib.core.metadata import sidecar_path
from smartlib.dcc.maya import gui_review_playblast as gui


@pytest.fixture(autouse=True)
def maya_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "_maya_executable", lambda: tmp_path / "maya" / "bin" / ("maya.exe" if gui.os.name == "nt" else "maya"))


def layer(tmp_path, name="CHA"):
    return {"name": name, "display_layer": name, "camera": "|camera_grp|cam",
            "frame_range": [630, 631], "resolution": [640, 360],
            "output_pattern": name + ".####.png", "output_dir": str(tmp_path / name)}


@pytest.mark.parametrize("change", [
    {"output_pattern": "../x.####.png"}, {"output_pattern": "x.png"},
    {"output_pattern": "x.####.exr"}, {"frame_range": [2, 1]},
    {"resolution": [0, 100]}, {"camera": ""},
])
def test_invalid_render_contract_rejected(tmp_path, change):
    with pytest.raises(ValueError):
        gui.validate_layers([{**layer(tmp_path), **change}])


class Commands:
    def __init__(self, root):
        self.root = root
        self.exports = []
        binary = root / "bin" / ("maya.exe" if gui.os.name == "nt" else "maya")
        binary.parent.mkdir(parents=True)
        binary.touch()

    def about(self, **kwargs):
        return str(self.root)

    def file(self, path=None, **kwargs):
        if kwargs.get("query"):
            return ["mayaAscii"]
        self.exports.append((path, kwargs))
        Path(path).write_bytes(b"snapshot")


class Process:
    pid = 99999
    returncode = None

    def __init__(self, exit_code=None, stuck=False):
        self.returncode = exit_code
        self.stuck = stuck
        self.terminated = 0

    def poll(self):
        return self.returncode

    def wait(self, **kwargs):
        if self.stuck and not self.terminated:
            raise subprocess.TimeoutExpired("maya", 10)
        return 0

    def terminate(self):
        self.terminated += 1


def test_one_gui_launch_for_multiple_layers_and_frozen_snapshot(tmp_path, monkeypatch):
    cmds = Commands(tmp_path / "maya")
    status = tmp_path / "job.json"
    captured = []
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    def start(command, **kwargs):
        request = json.loads(sidecar_path(status, ".playblast.request.json").read_text(encoding="utf-8"))
        captured.append((command, kwargs, request))
        gui._write(request["result"], {"state": "COMPLETE", "layers": {"CHA": {}, "BGA": {}}})
        return Process()

    monkeypatch.setattr(gui.subprocess, "Popen", start)
    result = gui.launch_playblast(cmds, scene=tmp_path / "built.ma",
                                 layers=[layer(tmp_path), layer(tmp_path, "BGA")],
                                 all_layers=["CHA", "BGA"], status_path=status)
    assert result["state"] == "COMPLETE"
    assert len(captured) == 1
    command, options, request = captured[0]
    assert "-batch" not in command
    assert "QT_QPA_PLATFORM" not in options["env"]
    assert options["env"]["MAYA_SKIP_USERSETUP_PY"] == "1"
    assert request["scene"] == str(sidecar_path(status, ".playblast.ma"))
    assert request["layers"][0]["camera"] == "|camera_grp|cam"
    assert cmds.exports[0][1] == {"exportAll": True, "type": "mayaAscii", "preserveReferences": True, "force": False}


def test_cache_only_job_does_not_launch_maya(tmp_path):
    assert gui.launch_playblast(None, scene="unused", layers=[], all_layers=[], status_path=tmp_path / "job.json") == {}


def test_gui_failure_keeps_unicode_error_and_log_paths(tmp_path, monkeypatch):
    cmds = Commands(tmp_path / "maya")
    status = tmp_path / "job.json"

    def start(*args, **kwargs):
        gui._write(sidecar_path(status, ".playblast.result.json"), {"state": "FAILED", "error": "描画失敗: camera missing"})
        return Process()

    monkeypatch.setattr(gui.subprocess, "Popen", start)
    with pytest.raises(RuntimeError, match="描画失敗") as error:
        gui.launch_playblast(cmds, scene="built.ma", layers=[layer(tmp_path)], all_layers=["CHA"], status_path=status)
    assert "playblast.log" in str(error.value)
    assert sidecar_path(status, ".playblast.request.json").exists()


def test_startup_timeout_terminates_only_owned_process(tmp_path, monkeypatch):
    cmds = Commands(tmp_path / "maya")
    process = Process(stuck=True)
    monkeypatch.setattr(gui.subprocess, "Popen", lambda *a, **k: process)
    clock = iter([0, 999])
    monkeypatch.setattr(gui.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="startup timed out"):
        gui.launch_playblast(cmds, scene="built.ma", layers=[layer(tmp_path)], all_layers=["CHA"], status_path=tmp_path / "job.json")
    assert process.terminated == 1


def test_early_exit_is_not_reported_as_success(tmp_path, monkeypatch):
    cmds = Commands(tmp_path / "maya")
    monkeypatch.setattr(gui.subprocess, "Popen", lambda *a, **k: Process(exit_code=7))
    with pytest.raises(RuntimeError, match="exit 7"):
        gui.launch_playblast(cmds, scene="built.ma", layers=[layer(tmp_path)], all_layers=["CHA"], status_path=tmp_path / "job.json")
