from __future__ import annotations

from typing import Any

from smartlib.dcc.resolve import marker_text_plus as base


MarkerCaption = base.MarkerCaption
build_styled_text_expression = base.build_styled_text_expression
format_frames = base.format_frames
marker_captions = base.marker_captions


def create_marker_text_plus(
    *, resolve_app: Any, comp: Any, tool_name: str = "MarkerTextPlus",
    size: float = 0.04, center: tuple[float, float] = (0.4, 0.25),
) -> Any:
    project = resolve_app.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("No current DaVinci Resolve project.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise RuntimeError("No current DaVinci Resolve timeline.")
    captions = base.marker_captions(timeline.GetMarkers() or {})
    if not captions:
        raise RuntimeError("No timeline markers found.")

    timeline_start = int(base._call(timeline, "GetStartFrame", 0))
    item = base._call(timeline, "GetCurrentVideoItem", None)
    item_start = int(base._call(item, "GetStart", timeline_start)) if item else timeline_start
    comp_start = int((comp.GetAttrs() or {}).get("COMPN_GlobalStart", 0))
    expression = base.build_styled_text_expression(
        captions,
        fps=base._timeline_fps(timeline, project),
        marker_origin=item_start - timeline_start,
        comp_origin=comp_start,
    )

    comp.Lock()
    try:
        tool = comp.FindTool(tool_name) or comp.AddTool("TextPlus")
        tool.SetAttrs({"TOOLS_Name": tool_name})
        tool.SetInput("Size", float(size))
        tool.SetInput("Center", {1: float(center[0]), 2: float(center[1])})
        _set_input_expression(tool, "StyledText", expression)
    finally:
        comp.Unlock()
    return tool


def _set_input_expression(tool: Any, input_name: str, expression: str) -> None:
    tool_setter = getattr(tool, "SetExpression", None)
    if callable(tool_setter):
        tool_setter(input_name, expression)
        return
    input_value = getattr(tool, input_name, None)
    if input_value is None:
        finder = getattr(tool, "FindInput", None)
        input_value = finder(input_name) if callable(finder) else None
    setter = getattr(input_value, "SetExpression", None)
    if not callable(setter):
        raise RuntimeError(f"Could not set an expression on Text+ input {input_name}.")
    setter(expression)
