from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _root() -> Path:
    value = os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT")
    return Path(value) if value else Path("P:/dev/smartlibrary")


def _current_item(resolve_app):
    project = resolve_app.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline() if project else None
    item = timeline.GetCurrentVideoItem() if timeline else None
    if not item:
        raise RuntimeError("No video clip at the current playhead position.")
    return item


def _comp_index_2(item):
    getter = getattr(item, "GetFusionCompByIndex", None)
    add_comp = getattr(item, "AddFusionComp", None)
    fusion_comp = getter(2) if callable(getter) else None
    for _attempt in range(2):
        if fusion_comp or not callable(add_comp):
            break
        add_comp()
        fusion_comp = getter(2)
    if not fusion_comp:
        raise RuntimeError("Could not get or create Fusion composition index 2.")
    return fusion_comp


def main() -> None:
    packages = _root() / "packages"
    if str(packages) not in sys.path:
        sys.path.insert(0, str(packages))
    resolve_app = app.GetResolve()  # type: ignore[name-defined]
    from smartlib.dcc.resolve import marker_text_plus_v3
    importlib.reload(marker_text_plus_v3)
    tool = marker_text_plus_v3.create_marker_text_plus(
        resolve_app=resolve_app, comp=_comp_index_2(_current_item(resolve_app)),
    )
    print(f"Created and merged marker Text+ in Fusion composition index 2: {tool.Name}")


main()
