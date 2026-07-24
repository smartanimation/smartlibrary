from __future__ import annotations

import os
import sys
import importlib
from pathlib import Path


def _smartpipeline_root() -> Path:
    root = os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT")
    if root:
        return Path(root)
    return Path("P:/dev/smartlibrary")


def main() -> None:
    root = _smartpipeline_root()
    packages = root / "packages"
    if str(packages) not in sys.path:
        sys.path.insert(0, str(packages))

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(root / "config" / "STKB")

    resolve_app = None
    try:
        resolve_app = app.GetResolve()  # type: ignore[name-defined]
    except Exception:
        pass

    from smartlib.dcc.resolve import export_timeline_csv, export_timeline_ui

    importlib.reload(export_timeline_csv)
    importlib.reload(export_timeline_ui)

    export_timeline_ui.show(config_dir=config_dir, resolve_app=resolve_app)


main()
