from __future__ import annotations

from typing import Any

from smartlib.dcc.resolve import marker_text_plus as expressions
from smartlib.dcc.resolve import marker_text_plus_batch as batch
from smartlib.dcc.resolve.marker_text_plus_batch_v2 import _set_black_left_aligned
from smartlib.dcc.resolve.marker_text_plus_batch_v3 import _set_gray_background
from smartlib.dcc.resolve.marker_text_plus_v2 import _set_input_expression
from smartlib.dcc.resolve.marker_text_plus_v3 import _merge_over_media


def apply_to_video_track(
    *, resolve_app: Any, track_index: int = 1, fusion_comp_index: int = 2,
    size: float = 0.04, center: tuple[float, float] = (0.9, 0.9),
) -> int:
    project = resolve_app.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline() if project else None
    if not timeline:
        raise RuntimeError("No current DaVinci Resolve timeline.")
    markers = expressions.marker_captions(timeline.GetMarkers() or {})
    if not markers:
        raise RuntimeError("No timeline markers found.")
    items = timeline.GetItemListInTrack("video", int(track_index)) or []
    timeline_start = int(expressions._call(timeline, "GetStartFrame", 0))
    fps = expressions._timeline_fps(timeline, project)
    processed = 0
    for item in items:
        relative_start = int(item.GetStart()) - timeline_start
        relative_end = int(item.GetEnd()) - timeline_start
        if not batch._overlaps_any_marker(relative_start, relative_end, markers):
            continue
        comp = batch._get_or_create_fusion_comp(item, fusion_comp_index)
        _apply_to_comp(
            comp=comp,
            marker_captions=markers,
            fps=fps,
            marker_origin=relative_start,
            filename=str(item.GetName() or ""),
            size=size,
            center=center,
        )
        processed += 1
    if not processed:
        raise RuntimeError(f"No clips on video track {track_index} overlap timeline markers.")
    return processed


def build_expression_with_filename(
    marker_captions: list[expressions.MarkerCaption], *, fps: float,
    marker_origin: int, comp_origin: int, filename: str,
) -> str:
    branches = []
    safe_filename = expressions._lua_string(filename)
    for marker in marker_captions:
        local_start = comp_origin + marker.start - marker_origin
        local_end = local_start + marker.duration
        shot = expressions._lua_string(marker.shot_name)
        duration = expressions.format_frames(marker.duration, fps)
        value = (
            f'"{shot}\\n" .. string.format("%04d", time - {local_start} + 1)'
            f' .. " ({duration})\\n{safe_filename}"'
        )
        branches.append(f"iif(time >= {local_start} and time < {local_end}, {value}, ")
    return "Text(" + "".join(branches) + '""' + ")" * (len(branches) + 1)


def _apply_to_comp(
    *, comp: Any, marker_captions: list[expressions.MarkerCaption], fps: float,
    marker_origin: int, filename: str, size: float, center: tuple[float, float],
) -> Any:
    comp_start = int((comp.GetAttrs() or {}).get("COMPN_GlobalStart", 0))
    expression = build_expression_with_filename(
        marker_captions,
        fps=fps,
        marker_origin=marker_origin,
        comp_origin=comp_start,
        filename=filename,
    )
    comp.Lock()
    try:
        text_tool = comp.FindTool("MarkerTextPlus") or comp.AddTool("TextPlus")
        text_tool.SetAttrs({"TOOLS_Name": "MarkerTextPlus"})
        text_tool.SetInput("Size", float(size))
        text_tool.SetInput("Center", {1: float(center[0]), 2: float(center[1])})
        _set_black_left_aligned(text_tool)
        _set_gray_background(text_tool)
        _set_input_expression(text_tool, "StyledText", expression)
        _merge_over_media(comp, text_tool)
    finally:
        comp.Unlock()
    return text_tool
