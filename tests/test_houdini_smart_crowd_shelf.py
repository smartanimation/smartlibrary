from __future__ import annotations

import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packages" / "smartlib" / "dcc" / "houdini" / "scripts" / "create_smart_crowd_seat_prototype.py"
SMART_MENU = ROOT / "packages" / "smartlib" / "dcc" / "houdini" / "smart_menu.py"
SHELF = ROOT / "packages" / "smartlib" / "dcc" / "houdini" / "toolbar" / "SmartPipeline.shelf"


def test_smart_crowd_seat_prototype_builder_is_valid_python(tmp_path):
    py_compile.compile(str(SCRIPT), cfile=str(tmp_path / "create_smart_crowd_seat_prototype.pyc"), doraise=True)


def test_smart_crowd_create_command_is_exposed_in_houdini_shelf():
    script_text = SCRIPT.read_text(encoding="utf-8")
    menu_text = SMART_MENU.read_text(encoding="utf-8")
    shelf_text = SHELF.read_text(encoding="utf-8")

    assert "def create_smart_crowd_seat_prototype(" in menu_text
    assert '"create_smart_crowd_seat_prototype.py"' in menu_text
    assert "create_single_agent_seat_prototype" in script_text
    assert 'memberTool name="smartpipeline_smart_crowd_seat_proto"' in shelf_text
    assert 'tool name="smartpipeline_smart_crowd_seat_proto"' in shelf_text
    assert "smart_menu.create_smart_crowd_seat_prototype()" in shelf_text
