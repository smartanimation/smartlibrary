from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _smartpipeline_root() -> Path:
    return Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or "P:/dev/smartlibrary"
    )


def _install_smart_menu() -> None:
    root = _smartpipeline_root()
    packages = str(root / "packages").replace("\\", "/")
    repo_root = str(root).replace("\\", "/")
    for path in (packages, repo_root):
        if path not in sys.path:
            sys.path.insert(0, path)

    from smartlib.dcc.maya import smart_menu

    importlib.reload(smart_menu)
    smart_menu.install()


def _deferred_install() -> None:
    try:
        import maya.utils

        maya.utils.executeDeferred(_install_smart_menu)
    except Exception:
        _install_smart_menu()


_deferred_install()

