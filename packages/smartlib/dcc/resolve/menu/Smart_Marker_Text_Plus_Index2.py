from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _smartpipeline_root() -> Path:
    root = os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT")
    return Path(root) if root else Path("P:/dev/smartlibrary")


def _timeline_and_item(resolve_app):
    project = resolve_app.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("No current DaVinci Resolve project.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise RuntimeError("No current DaVinci Resolve timeline.")
    item = timeline.GetCurrentVideoItem()
    if not item:
        raise RuntimeError("No video clip at the current playhead position.")
    return timeline, item


def _fusion_comp_at_index(item, comp_index=2):
    getter = getattr(item, "GetFusionCompByIndex", None)
    if not callable(getter):
        raise RuntimeError("This Resolve version does not expose Fusion compositions for the current clip.")

    fusion_comp = getter(comp_index)
    add_comp = getattr(item, "AddFusionComp", None)
    while not fusion_comp and callable(add_comp):
        count_getter = getattr(item, "GetFusionCompCount", None)
        before = int(count_getter() or 0) if callable(count_getter) else 0
        created = add_comp()
        fusion_comp = getter(comp_index)
        after = int(count_getter() or 0) if callable(count_getter) else before + int(bool(created))
        if after <= before:
            break
    if not fusion_comp:
        raise RuntimeError(f"Could not get or create Fusion composition index {comp_index}.")
    return fusion_comp


def main() -> None:
    packages = _smartpipeline_root() / "packages"
    if str(packages) not in sys.path:
        sys.path.insert(0, str(packages))

    resolve_app = app.GetResolve()  # type: ignore[name-defined]
    _timeline, item = _timeline_and_item(resolve_app)
    fusion_comp = _fusion_comp_at_index(item, 2)

    from smartlib.dcc.resolve import marker_text_plus

    importlib.reload(marker_text_plus)
    tool = marker_text_plus.create_marker_text_plus(
        resolve_app=resolve_app,
        comp=fusion_comp,
        tool_name="MarkerTextPlus",
    )
    print(f"Created marker Text+ in Fusion composition index 2: {tool.Name}")


main()
