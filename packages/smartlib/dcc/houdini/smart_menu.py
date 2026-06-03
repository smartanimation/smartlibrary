from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


SHELF_NAME = "SmartMenu"


def _root() -> Path:
    return Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or Path(__file__).resolve().parents[4]
    )


def ensure_runtime_paths() -> None:
    root = _root()
    for path in (root / "packages", root / "scripts", root):
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
        "scripts.asset_manager",
        "scripts.asset_manager_ui",
        "asset_manager",
        "asset_manager_ui",
    )
    try:
        from scripts import asset_manager_ui
    except ImportError:
        import asset_manager_ui

    asset_manager_ui.show()


def show_shot_manager() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.apps.shot_manager",
        "smartlib.apps.shot_manager.service",
        "scripts.shot_manager_ui",
    )
    from scripts import shot_manager_ui

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    shot_manager_ui.show(config_dir)


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

