from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from smartlib.core.icons import tool_icon_path


MENU_NAME = "SmartPipelineMenu"
MENU_LABEL = "SmartMenu"
SMART_GATE_GUIDE_PLUGIN = "smart_viewport_gate_guides.py"

MENU_TOOL_ICONS = {
    "Asset Manager": "asset_manager",
    "Shot Manager": "shot_manager",
    "Build Manager": "build_manager",
    "Review Build Manager": "review_build_manager",
    "Smart Ingest": "smart_ingest",
    "Smart Casting": "smart_casting",
    "Smart AE Browser": "smart_ae_browser",
    "Smart Editorial": "smart_editorial",
    "Smart Delivery": "smart_delivery",
}


DEFAULT_MENU_CONFIG = {
    "maya_menu": {
        "label": MENU_LABEL,
        "categories": {
            "File": [
                {
                    "label": "Asset Manager",
                    "command": "smartlib.dcc.maya.smart_menu.show_asset_manager",
                    "enabled": True,
                },
                {
                    "label": "Shot Manager",
                    "command": "smartlib.dcc.maya.smart_menu.show_shot_manager",
                    "enabled": True,
                },
                {
                    "label": "Sequence Manager",
                    "command": "smartlib.dcc.maya.smart_menu.show_sequence_manager",
                    "enabled": True,
                },
                {
                    "label": "Smart Preflight",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_preflight",
                    "enabled": True,
                },
                {
                    "label": "Texture Path Repair",
                    "command": "smartlib.dcc.maya.smart_menu.show_texture_path_repair",
                    "enabled": True,
                },
            ],
            "Sets": [
                {
                    "label": "Asset Assembly",
                    "command": "smartlib.dcc.maya.smart_menu.show_asset_assembly",
                    "enabled": True,
                },
            ],
            "Layout": [
                {
                    "label": "Smart Sequence Builder",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_sequence_builder",
                    "enabled": True,
                },
                {
                    "label": "Smart Set Dress",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_set_dress",
                    "enabled": True,
                },
                {
                    "label": "MAYA Layout Panel",
                    "command": "smartlib.dcc.maya.smart_menu.show_maya_layout_panel",
                    "enabled": True,
                },
                {
                    "label": "Smart Maker",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_maker",
                    "enabled": True,
                },
                {
                    "label": "Smart Shot",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_shot",
                    "enabled": True,
                },
                {
                    "label": "SmartGateGuide",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_gate_guide",
                    "enabled": True,
                },
            ],
            "Modeling": [
                {
                    "label": "Modeling Support",
                    "command": "smartlib.dcc.maya.smart_menu.show_modeling_support",
                    "enabled": True,
                },
            ],
            "Render": [
                {
                    "label": "Review Layer Manager",
                    "command": "smartlib.dcc.maya.smart_menu.show_review_layer_manager",
                    "enabled": True,
                },
                {
                    "label": "Smart Playblast",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_playblast",
                    "enabled": True,
                },
                {
                    "label": "Smart Render",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_render",
                    "enabled": True,
                },
            ],
            "Animation": [
                {
                    "label": "Smart CarSystem",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_car_system",
                    "enabled": True,
                },
            ],
        },
    }
}


def allowed_maya_features(studio_config):
    """Return a Maya feature allowlist, or None when every feature is allowed."""
    data = studio_config if isinstance(studio_config, dict) else {}
    role = str((data.get("studio") or {}).get("role") or "internal").strip().lower()
    configured = (data.get("maya") or {}).get("allowed_features")
    if isinstance(configured, (list, tuple, set)):
        return {
            str(feature).strip().lower()
            for feature in configured
            if str(feature).strip()
        }
    if role == "vendor":
        return {"smart_preflight"}
    return None


def _studio_maya_features():
    try:
        from smartlib.core.config_loader import load_config, studio_config_path
        path = studio_config_path()
        data = load_config(path) if path else {}
    except Exception:
        data = {}
    return allowed_maya_features(data)


def _feature_id(command_path: str) -> str:
    name = str(command_path or "").strip().rsplit(".", 1)[-1].lower()
    return name[5:] if name.startswith("show_") else name


def _is_feature_allowed(command_path: str, allowed=None) -> bool:
    policy = _studio_maya_features() if allowed is None else allowed
    return policy is None or _feature_id(command_path) in policy


def _filter_allowed_items(items, allowed):
    filtered = []
    for item in _menu_items_from_config(items):
        candidate = dict(item)
        children = candidate.get("items")
        if isinstance(children, (list, dict)):
            child_items = _filter_allowed_items(children, allowed)
            if not child_items:
                continue
            candidate["items"] = child_items
        elif not _is_feature_allowed(candidate.get("command"), allowed):
            continue
        filtered.append(candidate)
    return filtered


def _root() -> Path:
    return Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or Path(__file__).resolve().parents[4]
    )


def ensure_runtime_paths() -> None:
    root = _root()
    for path in (root / "packages", root):
        text = str(path).replace("\\", "/")
        if text not in sys.path:
            sys.path.insert(0, text)


def smart_gate_guide_plugin_path() -> Path:
    return _root() / "tools" / "maya" / "plug-ins" / SMART_GATE_GUIDE_PLUGIN


def ensure_smart_gate_guide_plugin(cmds=None, *, required: bool = True) -> bool:
    if cmds is None:
        try:
            import maya.cmds as cmds
        except ImportError as exc:
            if required:
                raise RuntimeError("SmartGateGuide plugin can only be loaded inside Maya.") from exc
            return False
    plugin_path = smart_gate_guide_plugin_path()
    plugin_text = str(plugin_path).replace("\\", "/")
    candidates = (plugin_path.name, plugin_path.stem, plugin_text)
    for candidate in candidates:
        try:
            if cmds.pluginInfo(candidate, query=True, loaded=True):
                return True
        except Exception:
            pass
    if not plugin_path.exists():
        message = f"SmartGateGuide plugin was not found: {plugin_text}"
        if required:
            raise RuntimeError(message)
        try:
            cmds.warning(message)
        except Exception:
            pass
        return False
    try:
        cmds.loadPlugin(plugin_text, quiet=True)
        return True
    except Exception as exc:
        message = f"SmartGateGuide plugin load failed: {exc}"
        if required:
            raise RuntimeError(message) from exc
        try:
            cmds.warning(message)
        except Exception:
            pass
        return False


def _config_dir() -> Path:
    return Path(os.environ.get("PROJECT_CONFIG_DIR") or _root() / "config" / "STKB")


def _reload(*names: str) -> None:
    for name in names:
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def _unload_module_tree(*prefixes: str) -> None:
    normalized = tuple(prefix.strip() for prefix in prefixes if prefix.strip())
    if not normalized:
        return
    for name in list(sys.modules):
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in normalized):
            sys.modules.pop(name, None)


def _load_menu_config() -> dict:
    ensure_runtime_paths()
    try:
        from smartlib.core.config_loader import ProjectConfig

        data = ProjectConfig(_config_dir()).load("maya_menu.yml")
    except Exception:
        return DEFAULT_MENU_CONFIG
    if not isinstance(data, dict) or not isinstance(data.get("maya_menu"), dict):
        return DEFAULT_MENU_CONFIG
    _ensure_sequence_manager_entry(data)
    return data


def _ensure_sequence_manager_entry(data: dict) -> None:
    """Add the built-in Sequence Manager entry to older project menu configs."""
    menu_data = data.get("maya_menu") or {}
    categories = menu_data.get("categories")
    if not isinstance(categories, dict):
        return
    file_items = categories.setdefault("File", [])
    normalized = _menu_items_from_config(file_items)
    command = "smartlib.dcc.maya.smart_menu.show_sequence_manager"
    if any(str(item.get("command") or "").strip() == command for item in normalized):
        return
    entry = {"label": "Sequence Manager", "command": command, "enabled": True}
    if isinstance(file_items, list):
        file_items.append(entry)
    elif isinstance(file_items, dict):
        file_items[entry["label"]] = {
            "command": entry["command"],
            "enabled": entry["enabled"],
        }


def _resolve_command(path: str):
    if not path:
        return None
    module_name, _, attr_name = path.rpartition(".")
    if not module_name or not attr_name:
        return None
    module = importlib.import_module(module_name)
    return getattr(module, attr_name, None)


def _run_command(path: str) -> None:
    if not _is_feature_allowed(path):
        try:
            import maya.cmds as cmds
            cmds.warning(f"SmartMenu command is disabled by the studio policy: {path}")
        except ImportError:
            pass
        return
    command = _resolve_command(path)
    if not callable(command):
        try:
            import maya.cmds as cmds

            cmds.warning(f"SmartMenu command was not found: {path}")
        except ImportError:
            pass
        return
    command()


def _is_visible(item: dict) -> bool:
    return str(item.get("visible", True)).strip().lower() not in {"0", "false", "no", "off"}


def _is_enabled(item: dict) -> bool:
    return str(item.get("enabled", True)).strip().lower() not in {"0", "false", "no", "off"}


def _add_menu_item(cmds, parent: str, item: dict) -> None:
    if not isinstance(item, dict) or not _is_visible(item):
        return
    if item.get("divider"):
        cmds.menuItem(divider=True, parent=parent)
        return
    label = str(item.get("label") or "").strip()
    if not label:
        return
    children = item.get("items")
    if isinstance(children, list):
        submenu = cmds.menuItem(label=label, parent=parent, subMenu=True, tearOff=True, enable=_is_enabled(item))
        for child in children:
            _add_menu_item(cmds, submenu, child)
        return
    command_path = str(item.get("command") or "").strip()
    enabled = _is_enabled(item) and bool(command_path)
    icon_id = str(item.get("icon") or MENU_TOOL_ICONS.get(label) or "").strip()
    icon_path = tool_icon_path(icon_id, 16) if icon_id else None
    kwargs = {
        "label": label,
        "parent": parent,
        "enable": enabled,
        "command": (lambda *_args, path=command_path: _run_command(path)) if enabled else "",
    }
    if icon_path:
        kwargs["image"] = str(icon_path)
    cmds.menuItem(**kwargs)


def _menu_items_from_config(items) -> list[dict]:
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    if not isinstance(items, dict):
        return []
    normalized = []
    for label, value in items.items():
        if isinstance(value, str):
            normalized.append({"label": str(label), "command": value, "enabled": True})
        elif isinstance(value, dict):
            item = dict(value)
            item.setdefault("label", str(label))
            normalized.append(item)
    return normalized


def _build_configured_menu(cmds, main_window: str) -> str:
    data = _load_menu_config()
    allowed = _studio_maya_features()
    menu_data = data.get("maya_menu") or {}
    label = str(menu_data.get("label") or MENU_LABEL)
    menu = cmds.menu(MENU_NAME, label=label, parent=main_window, tearOff=True)
    categories = menu_data.get("categories") or {}
    if isinstance(categories, dict):
        for category_label, items in categories.items():
            normalized_items = _filter_allowed_items(items, allowed)
            if not normalized_items:
                continue
            submenu = cmds.menuItem(label=str(category_label), parent=menu, subMenu=True, tearOff=True)
            for item in normalized_items:
                _add_menu_item(cmds, submenu, item)
    elif isinstance(categories, list):
        for item in _filter_allowed_items(categories, allowed):
            _add_menu_item(cmds, menu, item)
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(label="Reload SmartMenu", parent=menu, command=lambda *_args: reload_smart_menu())
    return menu


def show_asset_manager() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.scene_info",
        "scripts.asset_manager",
        "scripts.asset_manager_ui",
    )
    from scripts import asset_manager_ui

    asset_manager_ui.show()


def show_shot_manager() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.shot_builder",
        "smartlib.apps.shot_manager",
        "smartlib.apps.shot_manager.service",
        "scripts.shot_manager_ui",
    )
    from scripts import shot_manager_ui

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    shot_manager_ui.show(config_dir)


def show_sequence_manager() -> None:
    ensure_runtime_paths()
    existing_module = sys.modules.get("smartlib.apps.sequence_manager.__main__")
    existing_window = getattr(existing_module, "_WINDOW", None) if existing_module else None
    if existing_window is not None:
        try:
            existing_window.close()
            existing_window.deleteLater()
        except RuntimeError:
            pass
    _reload(
        "smartlib.apps.sequence_manager.service",
        "smartlib.apps.sequence_manager.__main__",
        "smartlib.apps.sequence_manager",
    )
    from smartlib.apps import sequence_manager

    sequence_manager.show(config_dir=str(_config_dir()))


def show_smart_preflight() -> None:
    ensure_runtime_paths()
    _unload_module_tree(
        "smartlib.preflight",
        "smartlib.apps.smart_preflight",
        "smartlib.dcc.maya.preflight",
    )
    from smartlib.dcc.maya.preflight import show_smart_preflight as show

    show()


def show_texture_path_repair() -> None:
    ensure_runtime_paths()
    _reload("smartlib.core.texture_reconnect", "smartlib.dcc.maya.texture_reconnect", "smartlib.apps.texture_path_repair.ui", "smartlib.apps.texture_path_repair")
    from smartlib.apps import texture_path_repair
    texture_path_repair.show(config_dir=str(_config_dir()))


def show_review_layer_manager() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.shot_builder",
        "smartlib.apps.shot_manager",
        "smartlib.apps.shot_manager.service",
        "scripts.review_layer_ui",
    )
    from scripts import review_layer_ui

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    review_layer_ui.show(config_dir=config_dir)


def show_smart_playblast() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.review_playblast",
        "smartlib.review.rv",
    )
    ui_module = importlib.import_module("smartlib.apps.smart_playblast.ui")
    # Always reload the UI. Maya keeps Python modules resident after the
    # window closes, so a version comparison can accidentally relaunch stale
    # drag/drop code when only the tool window is restarted.
    ui_module = importlib.reload(ui_module)
    ui_module.show(config_dir=str(_config_dir()))


def show_viewer() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.apps.viewer",
        "smartlib.apps.viewer.service",
        "scripts.viewer_ui",
    )
    from scripts import viewer_ui

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    viewer_ui.show(config_dir=config_dir)


def show_smart_shot() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.smart_shot",
        "smartlib.apps.smart_shot.ui",
        "smartlib.apps.smart_shot",
    )
    from smartlib.apps import smart_shot

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    smart_shot.show(config_dir=config_dir)


def show_smart_sequence_builder() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.shot_builder",
        "smartlib.apps.review_build_manager.orchestrator",
        "smartlib.apps.review_build_manager.service",
        "smartlib.apps.review_build_manager.worker",
        "smartlib.apps.review_build_manager.window",
        "smartlib.apps.review_build_manager",
    )
    from smartlib.apps import review_build_manager

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    review_build_manager.show(config_dir=config_dir, initial_scope="Sequence")


def show_smart_set_dress() -> None:
    ensure_runtime_paths()
    existing_ui = sys.modules.get("smartlib.apps.set_dress.ui")
    existing_window = getattr(existing_ui, "_WINDOW", None) if existing_ui else None
    if existing_window is not None:
        try:
            existing_window.close()
            existing_window.deleteLater()
        except RuntimeError:
            pass
    _reload(
        "smartlib.setdress.service",
        "smartlib.setdress",
        "smartlib.dcc.maya.set_dress",
        "smartlib.apps.set_dress.ui",
        "smartlib.apps.set_dress",
    )
    from smartlib.apps import set_dress

    set_dress.show()


def show_maya_layout_panel() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.layout_panel",
        "smartlib.apps.maya_layout_panel.ui",
        "smartlib.apps.maya_layout_panel",
    )
    from smartlib.apps import maya_layout_panel

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    maya_layout_panel.show(config_dir=config_dir)


def show_smart_maker() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.placement",
        "smartlib.apps.placement_manager.ui",
        "smartlib.apps.placement_manager",
    )
    from smartlib.apps import placement_manager

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    placement_manager.show(config_dir=config_dir)


def show_placement_manager() -> None:
    show_smart_maker()


def show_modeling_support() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.modeling",
        "smartlib.apps.modeling_support.ui",
        "smartlib.apps.modeling_support",
    )
    from smartlib.apps import modeling_support

    modeling_support.show()


def show_asset_assembly() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.asset_assembly",
        "smartlib.apps.asset_assembly.ui",
        "smartlib.apps.asset_assembly",
    )
    from smartlib.apps import asset_assembly

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    asset_assembly.show(config_dir=config_dir)


def show_smart_render() -> None:
    ensure_runtime_paths()
    _unload_module_tree("NodeGraphQt")
    _reload(
        "smartlib.review.ae",
        "smartlib.dcc.maya.render_graph",
        "smartlib.apps.smart_render.ui",
        "smartlib.apps.smart_render",
    )
    from smartlib.apps import smart_render

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    smart_render.show(config_dir=config_dir)


def show_smart_gate_guide() -> None:
    ensure_runtime_paths()
    try:
        import maya.cmds as cmds

        ensure_smart_gate_guide_plugin(cmds, required=True)
        cmds.SmartGateGuide()
    except ImportError:
        raise RuntimeError("SmartGateGuide can only be executed inside Maya.")


def show_smart_car_system() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.dcc.maya.car_system",
        "smartlib.apps.smart_car_system.ui",
        "smartlib.apps.smart_car_system",
    )
    from smartlib.apps import smart_car_system

    smart_car_system.show()


def install() -> str:
    try:
        import maya.cmds as cmds
        import maya.mel as mel
    except ImportError as exc:
        raise RuntimeError("SmartMenu can only be installed inside Maya.") from exc

    ensure_runtime_paths()
    ensure_smart_gate_guide_plugin(cmds, required=False)
    main_window = mel.eval("$tmp=$gMainWindow")
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME, menu=True)
    return _build_configured_menu(cmds, main_window)


def reload_smart_menu() -> str:
    """Reload this module from disk, then rebuild Maya's SmartMenu.

    The menu callback can belong to an older module object, so the newly
    reloaded module is used explicitly for installation.
    """
    ensure_runtime_paths()
    importlib.invalidate_caches()
    module = importlib.reload(sys.modules[__name__])
    return module.install()


def uninstall() -> None:
    try:
        import maya.cmds as cmds
    except ImportError:
        return
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME, menu=True)
