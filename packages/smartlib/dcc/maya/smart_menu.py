from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


MENU_NAME = "SmartPipelineMenu"
MENU_LABEL = "SmartMenu"
SMART_GATE_GUIDE_PLUGIN = "smart_viewport_gate_guides.py"


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
                    "label": "Review Layer Manager",
                    "command": "smartlib.dcc.maya.smart_menu.show_review_layer_manager",
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
                    "label": "Smart Render",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_render",
                    "enabled": True,
                },
            ],
            "Animation": [
                {
                    "label": "SmartGateGuide",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_gate_guide",
                    "enabled": True,
                },
                {
                    "label": "Smart CarSystem",
                    "command": "smartlib.dcc.maya.smart_menu.show_smart_car_system",
                    "enabled": True,
                },
            ],
        },
    }
}


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
    return data


def _resolve_command(path: str):
    if not path:
        return None
    module_name, _, attr_name = path.rpartition(".")
    if not module_name or not attr_name:
        return None
    module = importlib.import_module(module_name)
    return getattr(module, attr_name, None)


def _run_command(path: str) -> None:
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
    cmds.menuItem(
        label=label,
        parent=parent,
        enable=enabled,
        command=(lambda *_args, path=command_path: _run_command(path)) if enabled else "",
    )


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
    menu_data = data.get("maya_menu") or {}
    label = str(menu_data.get("label") or MENU_LABEL)
    menu = cmds.menu(MENU_NAME, label=label, parent=main_window, tearOff=True)
    categories = menu_data.get("categories") or {}
    if isinstance(categories, dict):
        for category_label, items in categories.items():
            normalized_items = _menu_items_from_config(items)
            if not normalized_items:
                continue
            submenu = cmds.menuItem(label=str(category_label), parent=menu, subMenu=True, tearOff=True)
            for item in normalized_items:
                _add_menu_item(cmds, submenu, item)
    elif isinstance(categories, list):
        for item in categories:
            _add_menu_item(cmds, menu, item)
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(label="Reload SmartMenu", parent=menu, command=lambda *_args: install())
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


def uninstall() -> None:
    try:
        import maya.cmds as cmds
    except ImportError:
        return
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME, menu=True)
