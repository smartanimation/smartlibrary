"""Apply the display subset supported by Maya's headless OGS renderer.

Model-editor settings alone do not affect ogsRender without a model panel.
Do not alter UV links, materials or color-management policy as a fallback.
"""
from contextlib import contextmanager


def hardware_display_settings(display):
    values = {}
    if "display_lights" in display:
        modes = {"default": 0, "all": 1, "none": 2, "flat": 4}
        mode = display["display_lights"]
        if mode not in modes:
            raise ValueError(f"Offscreen review does not support display_lights={mode!r}")
        values["lightingMode"] = modes[mode]
    if any(k in display for k in ("display_appearance", "display_textures", "use_default_material")):
        appearance = display.get("display_appearance", "smoothShaded")
        if appearance == "wireframe":
            mode = 0
        elif appearance == "boundingBox":
            mode = 6
        elif appearance == "smoothShaded":
            mode = 3 if display.get("use_default_material") else (4 if display.get("display_textures", True) else 1)
        else:
            raise ValueError(f"Offscreen review does not support display_appearance={appearance!r}")
        values["renderMode"] = mode
    return values


@contextmanager
def applied_hardware_display(cmds, display):
    """Restore globals even after a failed frame; never save render overrides."""
    settings = hardware_display_settings(display)
    previous = {}
    try:
        for attr, value in settings.items():
            plug = "hardwareRenderingGlobals." + attr
            if not cmds.objExists(plug):
                raise RuntimeError(f"Required offscreen display setting is unavailable: {plug}")
            previous[plug] = cmds.getAttr(plug)
            cmds.setAttr(plug, value)
        yield
    finally:
        for plug, value in previous.items():
            cmds.setAttr(plug, value)


@contextmanager
def applied_offscreen_playblast_preset(project_config, preset_name):
    from .playblast_preset import _maya_cmds, _presets, applied_playblast_preset

    presets = _presets(project_config)
    if preset_name not in presets:
        raise ValueError(f"Unknown offscreen review preset: {preset_name}")
    display = presets[preset_name].get("display") or {}
    with applied_playblast_preset(project_config, preset_name):
        with applied_hardware_display(_maya_cmds(), display):
            yield
