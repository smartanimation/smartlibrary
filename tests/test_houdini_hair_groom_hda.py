from __future__ import annotations

import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packages" / "smartlib" / "dcc" / "houdini" / "scripts" / "create_hair_groom_hda.py"
SMART_MENU = ROOT / "packages" / "smartlib" / "dcc" / "houdini" / "smart_menu.py"
SHELF = ROOT / "packages" / "smartlib" / "dcc" / "houdini" / "toolbar" / "SmartPipeline.shelf"


def test_hair_groom_hda_builder_is_valid_python(tmp_path):
    py_compile.compile(str(SCRIPT), cfile=str(tmp_path / "create_hair_groom_hda.pyc"), doraise=True)


def test_hair_groom_hda_builder_defines_expected_operator_and_outputs():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'HDA_NAME = "smart::hair_groom::1.0"' in text
    assert "smart_hair_groom.hda" in text
    assert "build_render_curves" in text
    assert "build_maya_proxy_tubes" in text
    assert "build_debug_id_curves" in text
    assert "hair_id" in text
    assert "guide_id" in text
    assert "clump_id" in text
    assert "material_name" in text


def test_hair_groom_hda_is_exposed_in_houdini_menu_and_shelf():
    menu_text = SMART_MENU.read_text(encoding="utf-8")
    shelf_text = SHELF.read_text(encoding="utf-8")

    assert "def create_hair_groom_hda()" in menu_text
    assert '"create_hair_groom_hda.py"' in menu_text
    assert 'memberTool name="smartpipeline_hair_groom_hda"' in shelf_text
    assert "smart_menu.create_hair_groom_hda()" in shelf_text
