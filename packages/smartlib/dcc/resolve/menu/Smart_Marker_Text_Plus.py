from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _smartpipeline_root() -> Path:
    root = os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT")
    return Path(root) if root else Path("P:/dev/smartlibrary")


def main() -> None:
    packages = _smartpipeline_root() / "packages"
    if str(packages) not in sys.path:
        sys.path.insert(0, str(packages))
    resolve_app = app.GetResolve()  # type: ignore[name-defined]
    comp = fu.GetCurrentComp()  # type: ignore[name-defined]
    if not comp:
        raise RuntimeError("No current Fusion composition. Open a clip on the Fusion page first.")
    from smartlib.dcc.resolve import marker_text_plus
    importlib.reload(marker_text_plus)
    tool = marker_text_plus.create_marker_text_plus(resolve_app=resolve_app, comp=comp)
    print(f"Created marker Text+: {tool.Name}")


main()
