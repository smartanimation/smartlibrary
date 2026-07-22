from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType


bl_info = {
    "name": "Smart Modeling Support",
    "author": "SmartPipeline",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > SMART MODELING",
    "description": "Modeling helper tools for SmartPipeline.",
    "category": "Pipeline",
}


MODULE_NAME = "smartlib.dcc.blender.modeling_support"


def _root() -> Path:
    return Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or r"P:\dev\smartlibrary"
    )


def _ensure_paths() -> None:
    root = _root()
    for path in (root / "packages", root / "scripts", root):
        text = str(path).replace("\\", "/")
        if text not in sys.path:
            sys.path.insert(0, text)


def _modeling_module(*, reload: bool = False) -> ModuleType:
    _ensure_paths()
    module = sys.modules.get(MODULE_NAME)
    if module is None:
        module = importlib.import_module(MODULE_NAME)
    elif reload:
        module = importlib.reload(module)
    return module


def _unregister_loaded_modeling() -> None:
    module = sys.modules.get(MODULE_NAME)
    if module is None:
        return
    try:
        module.unregister()
    except Exception:
        # The panel may not be registered yet during Blender add-on reloads.
        pass


def register() -> None:
    _unregister_loaded_modeling()
    _modeling_module(reload=True).register()


def unregister() -> None:
    _modeling_module().unregister()
