import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets
from scripts import config_creator
from smartlib.dcc.maya import smart_menu


@pytest.fixture
def editor(monkeypatch, tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(config_creator, "DEFAULT_DIR", str(tmp_path / "defaults"))
    monkeypatch.setattr(config_creator, "PROJECTS_ROOT", str(tmp_path))
    window = config_creator.ConfigCreatorApp.__new__(config_creator.ConfigCreatorApp)
    QtWidgets.QMainWindow.__init__(window)
    window.setCentralWidget(window.setup_maya_custom_tab())
    window._load_maya_custom_editor()
    yield window
    window.close()
    window.deleteLater()


def test_editor_roundtrip_and_remove(editor, tmp_path):
    command = "import studiolibrary\nstudiolibrary.main()"
    editor._add_maya_custom_row({"name": "studiolibrary", "type": "python", "command": command})
    data = editor._maya_custom_config_from_ui()
    project = tmp_path / "TEST"
    project.mkdir()
    config_creator.save_yml(project / "maya_menu.yml", data)
    editor._load_maya_custom_editor("TEST")
    assert editor.maya_custom_table.cellWidget(0, 2).toPlainText() == command
    assert editor._maya_custom_config_from_ui() == data
    editor._remove_maya_custom_row()
    result = editor._maya_custom_config_from_ui()
    assert result["maya_menu"]["categories"]["Custom"] == []
    assert result["maya_menu"]["categories"]["File"] == data["maya_menu"]["categories"]["File"]


def test_editor_rejects_invalid_input(editor):
    editor._add_maya_custom_row()
    with pytest.raises(ValueError, match="required"):
        editor._maya_custom_config_from_ui()
    editor.maya_custom_table.item(0, 0).setText("broken")
    editor.maya_custom_table.cellWidget(0, 2).setPlainText("if:")
    with pytest.raises(ValueError, match="broken"):
        editor._maya_custom_config_from_ui()


def test_menu_build_defers_python_until_click(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "studiolibrary", SimpleNamespace(main=lambda: calls.append("opened")))
    entries = [
        {"name": "studiolibrary", "type": "python", "command": "import studiolibrary\nstudiolibrary.main()"},
        {"name": "second", "type": "python", "command": "import studiolibrary\nstudiolibrary.main()"},
    ]
    monkeypatch.setattr(smart_menu, "_load_menu_config", lambda: {"maya_menu": {"categories": {"Custom": entries}}})
    monkeypatch.setattr(smart_menu, "_studio_maya_features", lambda: None)
    items = []
    def menu_item(**kwargs):
        items.append(kwargs)
        return kwargs.get("label", "divider")
    cmds = SimpleNamespace(menu=lambda *args, **kwargs: "SmartMenu", menuItem=menu_item)
    smart_menu._build_configured_menu(cmds, "main")
    assert not calls
    custom = next(item for item in items if item.get("label") == "Custom")
    assert custom["parent"] == "SmartMenu"
    item = next(item for item in items if item.get("label") == "studiolibrary")
    assert item["parent"] == "Custom"
    item["command"]()
    assert calls == ["opened"]
    monkeypatch.setattr(smart_menu, "_studio_maya_features", lambda: {"smart_preflight"})
    assert smart_menu._filter_allowed_items(entries, {"smart_preflight"}) == []
    item["command"]()
    assert calls == ["opened"]


def test_mel_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(smart_menu, "_studio_maya_features", lambda: None)
    mel = SimpleNamespace(eval=calls.append)
    monkeypatch.setitem(sys.modules, "maya", SimpleNamespace(mel=mel))
    monkeypatch.setitem(sys.modules, "maya.mel", mel)
    smart_menu._run_custom_command('print("hello");', "mel")
    assert calls == ['print("hello");']
