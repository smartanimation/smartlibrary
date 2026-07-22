from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


ROOT = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or r"P:\dev\smartlibrary")
MODULE_NAME = "smartlib.dcc.blender.smart_asset_panel"

for path in (ROOT / "packages", ROOT / "scripts", ROOT):
    text = str(path).replace("\\", "/")
    if text not in sys.path:
        sys.path.insert(0, text)

existing = sys.modules.get(MODULE_NAME)
if existing is not None:
    try:
        existing.unregister()
    except Exception:
        pass

from smartlib.dcc.blender import smart_asset_panel
importlib.reload(smart_asset_panel)
smart_asset_panel.register()
