from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or "P:/dev/smartlibrary")
    packages = root / "packages"
    if str(packages) not in sys.path:
        sys.path.insert(0, str(packages))
    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or "P:/dev/smartprojects/config/ELCD"
    resolve_app = app.GetResolve()  # type: ignore[name-defined]
    from smartlib.dcc.resolve import editorial_insert, editorial_insert_ui_tk
    importlib.reload(editorial_insert)
    importlib.reload(editorial_insert_ui_tk)
    editorial_insert_ui_tk.show(config_dir=config_dir, resolve_app=resolve_app)


main()
