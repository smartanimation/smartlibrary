from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _smartpipeline_root() -> Path:
    root = os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT")
    return Path(root) if root else Path("P:/dev/smartlibrary")


def _current_comp(resolve_app):
    direct_comp = globals().get("comp")
    if direct_comp:
        return direct_comp
    for owner_name in ("fu", "fusion", "app"):
        owner = globals().get(owner_name)
        if not owner:
            continue
        candidate = getattr(owner, "CurrentComp", None)
        if candidate:
            return candidate
        getter = getattr(owner, "GetCurrentComp", None)
        if callable(getter):
            candidate = getter()
            if candidate:
                return candidate
    fusion_getter = getattr(resolve_app, "Fusion", None)
    if callable(fusion_getter):
        fusion = fusion_getter()
        getter = getattr(fusion, "GetCurrentComp", None)
        if callable(getter):
            return getter()
    return None


def main() -> None:
    packages = _smartpipeline_root() / "packages"
    if str(packages) not in sys.path:
        sys.path.insert(0, str(packages))
    resolve_app = app.GetResolve()  # type: ignore[name-defined]
    current_comp = _current_comp(resolve_app)
    if not current_comp:
        raise RuntimeError(
            "No current Fusion composition. Select a Fusion composition or open a clip on the Fusion page."
        )
    from smartlib.dcc.resolve import marker_text_plus
    importlib.reload(marker_text_plus)
    tool = marker_text_plus.create_marker_text_plus(resolve_app=resolve_app, comp=current_comp)
    print(f"Created marker Text+: {tool.Name}")


main()
