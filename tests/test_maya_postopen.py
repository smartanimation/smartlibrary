import os
import sys
from types import SimpleNamespace

import pytest
import yaml

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.path_resolver import ProjectPaths, configured_project_paths
from smartlib.dcc.maya import postopen


@pytest.fixture
def project(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "templates_base.yml").write_text(yaml.safe_dump({
        "anchors": {"project_root": str(tmp_path / "project"), "project_name": "TEST"},
        "templates": {"production_root": "{project_root}/custom_production"},
    }))
    config = ProjectConfig(config_dir)
    folder = configured_project_paths(config.project_root, config).software_postopen_dir("maya")
    folder.mkdir(parents=True)
    return config, folder


def register(config, scripts):
    (config.config_dir / "software_maya2024_test.yml").write_text(yaml.safe_dump({
        "source_software": "maya2024", "postopen_scripts": scripts,
    }))


def test_only_registered_scripts_run_in_order_with_fresh_context(project, tmp_path):
    config, folder = project
    log = tmp_path / "result.txt"
    (folder / "first.py").write_text(
        f"from pathlib import Path\nPath({str(log)!r}).write_text('first\\n')\n"
        "assert context['event'] == 'postopen'\ncontext['project_id'] = 'changed'\n"
    )
    (folder / "second.py").write_text(
        "def run(context):\n"
        "    assert context['project_id'] == 'TEST'\n"
        "    assert context['software_id'] == 'maya2024_test'\n"
        "    assert context['file_path'] == 'D:/scene.ma'\n"
        f"    with open({str(log)!r}, 'a') as stream: stream.write('second\\n')\n"
    )
    (folder / "unregistered.py").write_text("raise AssertionError('must not run')")
    register(config, ["first.py", "second.py"])
    postopen.execute(config, "maya2024_test", "D:/scene.ma")
    assert log.read_text() == "first\nsecond\n"
    assert folder.parents[3].name == "custom_production"
    # Disk edits are picked up on the next open (no imported-module cache).
    (folder / "first.py").write_text(f"open({str(log)!r}, 'w').write('updated\\n')")
    postopen.execute(config, "maya2024_test", "D:/scene.ma")
    assert log.read_text() == "updated\nsecond\n"


def test_failure_stops_remaining_scripts(project):
    config, folder = project
    (folder / "bad.py").write_text("raise ValueError('broken')")
    (folder / "later.py").write_text("raise AssertionError('must not reach')")
    register(config, ["bad.py", "later.py"])
    with pytest.raises(RuntimeError, match="bad.py.*broken"):
        postopen.execute(config, "maya2024_test", "D:/scene.ma")


def test_missing_file_validated_before_any_execution(project):
    config, folder = project
    (folder / "first.py").write_text("raise AssertionError('must not run')")
    register(config, ["first.py", "missing.py"])
    with pytest.raises(FileNotFoundError, match="missing.py"):
        postopen.execute(config, "maya2024_test", "D:/scene.ma")


@pytest.mark.parametrize("reference", ["../outside.py", "C:/outside.py", "bad.txt", ""])
def test_resolver_rejects_invalid_script_paths(tmp_path, reference):
    with pytest.raises(ValueError):
        ProjectPaths(tmp_path).software_postopen_script("maya", reference)


def test_callback_reentrancy_warning_and_install(monkeypatch, project):
    config, _ = project
    jobs, warnings, runs = [], [], []
    def job(**kwargs):
        if "exists" in kwargs:
            return kwargs["exists"] == 42
        jobs.append(kwargs)
        return 42
    cmds = SimpleNamespace(about=lambda **k: False, scriptJob=job,
                           file=lambda **k: "D:/scene.ma", warning=warnings.append)
    monkeypatch.setitem(sys.modules, "maya", SimpleNamespace(cmds=cmds))
    monkeypatch.setitem(sys.modules, "maya.cmds", cmds)
    monkeypatch.setattr(postopen, "_job", None)
    monkeypatch.setattr(postopen, "current_project_config", lambda: config)
    monkeypatch.setenv("SMART_SOFTWARE_ID", "maya2024_test")
    def execute(*args):
        runs.append(args)
        postopen.on_scene_opened()
        raise ValueError("script error")
    monkeypatch.setattr(postopen, "execute", execute)
    assert postopen.install() == postopen.install() == 42
    assert len(jobs) == 1
    assert jobs[0]["event"][0] == "SceneOpened"
    jobs[0]["event"][1]()
    assert len(runs) == 1
    assert warnings == ["script error"]
    assert not postopen._running


def test_batch_does_not_install_callback(monkeypatch):
    cmds = SimpleNamespace(about=lambda **k: True)
    monkeypatch.setitem(sys.modules, "maya", SimpleNamespace(cmds=cmds))
    monkeypatch.setitem(sys.modules, "maya.cmds", cmds)
    assert postopen.install() is None


def test_config_creator_order_roundtrip_and_clear(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from scripts import config_creator
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = config_creator.ConfigCreatorApp.__new__(config_creator.ConfigCreatorApp)
    QtWidgets.QMainWindow.__init__(window)
    window.software_configs = {}
    window.setCentralWidget(window.setup_software_tab())
    try:
        window._populate_tree({"postopen_scripts": ["a.py", "b.py"], "name": "Maya"})
        window.postopen_list.setCurrentRow(1)
        window.move_postopen_file(-1)
        window._save_tree_to_memory("maya2024")
        config_creator.save_yml(tmp_path / "software.yml", window.software_configs["maya2024"])
        stored = config_creator.load_yml(tmp_path / "software.yml")
        assert stored["postopen_scripts"] == ["b.py", "a.py"]
        assert stored["name"] == "Maya"
        window._populate_tree(stored)
        assert window.postopen_list.item(0).text() == "b.py"
        window._populate_tree({})
        assert window.postopen_list.count() == 0
    finally:
        window.close()
        window.deleteLater()
