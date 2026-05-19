from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


MENU_NAME = "SmartPipelineMenu"
MENU_LABEL = "SmartMenu"


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


def _reload(*names: str) -> None:
    for name in names:
        if name in sys.modules:
            importlib.reload(sys.modules[name])


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


def install() -> str:
    try:
        import maya.cmds as cmds
        import maya.mel as mel
    except ImportError as exc:
        raise RuntimeError("SmartMenu can only be installed inside Maya.") from exc

    ensure_runtime_paths()
    main_window = mel.eval("$tmp=$gMainWindow")
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME, menu=True)
    menu = cmds.menu(MENU_NAME, label=MENU_LABEL, parent=main_window, tearOff=True)
    cmds.menuItem(label="Asset Manager", parent=menu, command=lambda *_args: show_asset_manager())
    cmds.menuItem(label="Shot Manager", parent=menu, command=lambda *_args: show_shot_manager())
    cmds.menuItem(label="Review Layer Manager", parent=menu, command=lambda *_args: show_review_layer_manager())
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(label="Reload SmartMenu", parent=menu, command=lambda *_args: install())
    return menu


def uninstall() -> None:
    try:
        import maya.cmds as cmds
    except ImportError:
        return
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME, menu=True)
