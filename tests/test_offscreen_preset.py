import pytest

from smartlib.dcc.maya.offscreen_preset import applied_hardware_display, hardware_display_settings


@pytest.mark.parametrize("display,expected", [
    ({"display_textures": True, "display_lights": "all"}, {"renderMode": 4, "lightingMode": 1}),
    ({"display_textures": False}, {"renderMode": 1}),
    ({"display_textures": True, "use_default_material": True}, {"renderMode": 3}),
    ({"display_lights": "default"}, {"lightingMode": 0}),
    ({"display_appearance": "wireframe"}, {"renderMode": 0}),
    ({}, {}),
])
def test_display_mapping(display, expected):
    assert hardware_display_settings(display) == expected


def test_selected_lights_cannot_silently_fall_back():
    with pytest.raises(ValueError, match="display_lights"):
        hardware_display_settings({"display_lights": "selected"})


class Commands:
    def __init__(self):
        self.values = {"hardwareRenderingGlobals.renderMode": 1, "hardwareRenderingGlobals.lightingMode": 0}

    def objExists(self, plug):
        return plug in self.values

    def getAttr(self, plug):
        return self.values[plug]

    def setAttr(self, plug, value):
        self.values[plug] = value


def test_globals_applied_without_panels_and_restored_on_render_failure():
    cmds = Commands()
    before = dict(cmds.values)
    with pytest.raises(RuntimeError, match="render failed"):
        with applied_hardware_display(cmds, {"display_textures": True, "display_lights": "all"}):
            assert cmds.values["hardwareRenderingGlobals.renderMode"] == 4
            assert cmds.values["hardwareRenderingGlobals.lightingMode"] == 1
            raise RuntimeError("render failed")
    assert cmds.values == before


def test_partial_application_restores_on_missing_attribute():
    cmds = Commands()
    del cmds.values["hardwareRenderingGlobals.renderMode"]
    before = dict(cmds.values)
    with pytest.raises(RuntimeError, match="unavailable"):
        with applied_hardware_display(cmds, {"display_textures": True, "display_lights": "all"}):
            pass
    assert cmds.values == before
