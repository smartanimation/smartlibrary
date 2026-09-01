from __future__ import annotations

from typing import Any

from smartlib.dcc.resolve import marker_text_plus as expressions
from smartlib.dcc.resolve.marker_text_plus_v2 import _set_input_expression
from smartlib.dcc.resolve.marker_text_plus_v3 import _merge_over_media


def apply_to_video_track(
    *, resolve_app: Any, track_index: int = 1, fusion_comp_index: int = 2,
    size: float = 0.04, center: tuple[float, float] = (0.4, 0.25),
) -> int:
    project = resolve_app.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("No current DaVinci Resolve project.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise RuntimeError("No current DaVinci Resolve timeline.")
    markers = expressions.marker_captions(timeline.GetMarkers() or {})
    if not markers:
        raise RuntimeError("No timeline markers found.")
    items = timeline.GetItemListInTrack("video", int(track_index)) or []
    if not items:
        raise RuntimeError(f"No video clips found on track {track_index}.")

    timeline_start = int(expressions._call(timeline, "GetStartFrame", 0))
    fps = expressions._timeline_fps(timeline, project)
    processed = 0
    for item in items:
        item_start = int(item.GetStart())
        item_end = int(item.GetEnd())
        relative_start = item_start - timeline_start
        relative_end = item_end - timeline_start
        if not _overlaps_any_marker(relative_start, relative_end, markers):
            continue
        comp = _get_or_create_fusion_comp(item, fusion_comp_index)
        _apply_to_comp(
            comp=comp,
            marker_captions=markers,
            fps=fps,
            marker_origin=relative_start,
            size=size,
            center=center,
        )
        processed += 1
    if not processed:
        raise RuntimeError(f"No clips on video track {track_index} overlap timeline markers.")
    return processed


def _apply_to_comp(
    *, comp: Any, marker_captions: list[expressions.MarkerCaption], fps: float,
    marker_origin: int, size: float, center: tuple[float, float],
) -> Any:
    comp_start = int((comp.GetAttrs() or {}).get("COMPN_GlobalStart", 0))
    expression = expressions.build_styled_text_expression(
        marker_captions, fps=fps, marker_origin=marker_origin, comp_origin=comp_start,
    )
    comp.Lock()
    try:
        text_tool = comp.FindTool("MarkerTextPlus") or comp.AddTool("TextPlus")
        text_tool.SetAttrs({"TOOLS_Name": "MarkerTextPlus"})
        text_tool.SetInput("Size", float(size))
        text_tool.SetInput("Center", {1: float(center[0]), 2: float(center[1])})
        _set_input_expression(text_tool, "StyledText", expression)
        _merge_over_media(comp, text_tool)
    finally:
        comp.Unlock()
    return text_tool


def _get_or_create_fusion_comp(item: Any, comp_index: int) -> Any:
    getter = getattr(item, "GetFusionCompByIndex", None)
    add_comp = getattr(item, "AddFusionComp", None)
    comp = getter(comp_index) if callable(getter) else None
    for _attempt in range(comp_index):
        if comp or not callable(add_comp):
            break
        add_comp()
        comp = getter(comp_index)
    if not comp:
        raise RuntimeError(f"Could not get or create Fusion composition index {comp_index} for {item.GetName()}.")
    return comp


def _overlaps_any_marker(start: int, end: int, markers: list[expressions.MarkerCaption]) -> bool:
    return any(marker.start < end and marker.start + marker.duration > start for marker in markers)
