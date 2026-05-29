from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig, load_config


def preset_names(project_config: ProjectConfig) -> list[str]:
    return list(_presets(project_config).keys())


def preset_label(project_config: ProjectConfig, name: str) -> str:
    preset = _presets(project_config).get(name) or {}
    return str(preset.get("label") or name)


@contextmanager
def applied_playblast_preset(project_config: ProjectConfig, preset_name: str | None):
    cmds = _maya_cmds()
    preset = (_presets(project_config).get(preset_name or "") or {}).get("display") or {}
    if not preset:
        yield
        return

    panel = _active_model_panel(cmds)
    panels = _model_panels(cmds)
    panel_state = {panel: _capture_panel_state(cmds, panel) for panel in panels}
    image_plane_state = _capture_image_planes(cmds)
    try:
        for panel in panels:
            _apply_panel_preset(cmds, panel, preset)
        _apply_image_plane_preset(cmds, preset)
        _focus_panel(cmds, panel)
        try:
            cmds.refresh(force=True)
        except Exception:
            pass
        yield
    finally:
        for panel, state in panel_state.items():
            _restore_panel_state(cmds, panel, state)
        _restore_image_planes(cmds, image_plane_state)
        try:
            cmds.refresh(force=True)
        except Exception:
            pass


def _presets(project_config: ProjectConfig) -> dict[str, Any]:
    default_path = _pipeline_root(project_config) / "config" / "default" / "playblast_presets.yml"
    data = load_config(default_path)
    project_path = project_config.config_dir / "playblast_presets.yml"
    project_data = load_config(project_path)
    merged = dict((data.get("presets") or {}) if isinstance(data, dict) else {})
    merged.update((project_data.get("presets") or {}) if isinstance(project_data, dict) else {})
    return merged


def _pipeline_root(project_config: ProjectConfig) -> Path:
    return project_config.config_dir.parent.parent


def _active_model_panel(cmds: Any) -> str:
    panel = cmds.getPanel(withFocus=True)
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
        return panel
    panels = cmds.getPanel(type="modelPanel") or []
    return panels[0] if panels else ""


def _model_panels(cmds: Any) -> list[str]:
    panels = cmds.getPanel(type="modelPanel") or []
    active = _active_model_panel(cmds)
    ordered = []
    for panel in [active, *panels]:
        if panel and panel not in ordered:
            ordered.append(panel)
    return ordered


def _focus_panel(cmds: Any, panel: str) -> None:
    if not panel:
        return
    try:
        cmds.setFocus(panel)
    except Exception:
        pass


def _capture_panel_state(cmds: Any, panel: str) -> dict[str, Any]:
    state = {}
    for flag in ("displayAppearance", "displayTextures", "displayLights", "shadows", "grid", "useDefaultMaterial"):
        try:
            state[flag] = cmds.modelEditor(panel, query=True, **{flag: True})
        except Exception:
            pass
    return state


def _restore_panel_state(cmds: Any, panel: str, state: dict[str, Any]) -> None:
    for flag, value in state.items():
        try:
            cmds.modelEditor(panel, edit=True, **{flag: value})
        except Exception:
            pass


def _apply_panel_preset(cmds: Any, panel: str, preset: dict[str, Any]) -> None:
    mapping = {
        "display_appearance": "displayAppearance",
        "display_textures": "displayTextures",
        "display_lights": "displayLights",
        "shadows": "shadows",
        "grid": "grid",
        "use_default_material": "useDefaultMaterial",
    }
    for key, flag in mapping.items():
        if key not in preset:
            continue
        try:
            cmds.modelEditor(panel, edit=True, **{flag: preset[key]})
        except Exception:
            pass
    if preset.get("use_default_material") is True:
        try:
            cmds.modelEditor(panel, edit=True, displayTextures=False)
        except Exception:
            pass
    if str(preset.get("display_lights", "")).lower() in {"default", "none"}:
        try:
            cmds.modelEditor(panel, edit=True, shadows=False)
        except Exception:
            pass
    # Keep the viewport in a geometry-centric state; cameras/lights are still available
    # for looking through/rendering, but image planes are handled explicitly below.
    for flag, value in (
        ("polymeshes", True),
        ("nurbsSurfaces", True),
        ("subdivSurfaces", True),
        ("cameras", True),
        ("lights", True),
        ("joints", False),
        ("ikHandles", False),
        ("deformers", False),
        ("imagePlane", bool(preset.get("image_planes", True))),
    ):
        try:
            cmds.modelEditor(panel, edit=True, **{flag: value})
        except Exception:
            pass


def _capture_image_planes(cmds: Any) -> dict[str, dict[str, Any]]:
    state = {}
    for image_plane in cmds.ls(type="imagePlane") or []:
        row = {}
        for attr in ("visibility", "displayMode"):
            full = f"{image_plane}.{attr}"
            if not cmds.objExists(full):
                continue
            try:
                row[attr] = cmds.getAttr(full)
            except Exception:
                pass
        state[image_plane] = row
    return state


def _restore_image_planes(cmds: Any, state: dict[str, dict[str, Any]]) -> None:
    for image_plane, attrs in state.items():
        for attr, value in attrs.items():
            full = f"{image_plane}.{attr}"
            if not cmds.objExists(full):
                continue
            try:
                cmds.setAttr(full, value)
            except Exception:
                pass


def _apply_image_plane_preset(cmds: Any, preset: dict[str, Any]) -> None:
    if "image_planes" not in preset:
        return
    visible = bool(preset.get("image_planes"))
    for image_plane in cmds.ls(type="imagePlane") or []:
        if cmds.objExists(f"{image_plane}.visibility"):
            try:
                cmds.setAttr(f"{image_plane}.visibility", visible)
            except Exception:
                pass
        if cmds.objExists(f"{image_plane}.displayMode"):
            try:
                cmds.setAttr(f"{image_plane}.displayMode", 3 if visible else 0)
            except Exception:
                pass


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Playblast presets are available inside Maya.") from exc
    return cmds
