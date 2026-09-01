from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class MarkerCaption:
    shot_name: str
    start: int
    duration: int


def format_frames(frame_count: int, fps: float) -> str:
    nominal_fps = max(1, int(round(float(fps))))
    seconds, remainder = divmod(max(0, int(frame_count)), nominal_fps)
    return f"{seconds:02d} + {remainder:02d}"


def marker_captions(markers: dict[Any, Any]) -> list[MarkerCaption]:
    captions = []
    for frame_key in sorted(markers, key=lambda value: int(float(value))):
        marker = markers[frame_key] or {}
        captions.append(MarkerCaption(
            shot_name=str(marker.get("name") or ""),
            start=int(float(frame_key)),
            duration=max(1, int(float(marker.get("duration") or 1))),
        ))
    return captions


def build_styled_text_expression(
    captions: Iterable[MarkerCaption], *, fps: float, marker_origin: int = 0, comp_origin: int = 0,
) -> str:
    branches = []
    for caption in captions:
        local_start = comp_origin + caption.start - marker_origin
        local_end = local_start + caption.duration
        shot = _lua_string(caption.shot_name)
        total = format_frames(caption.duration, fps)
        value = (
            f'"{shot}\\n" .. string.format("%04d", time - {local_start} + 1)'
            f' .. " ({total})"'
        )
        branches.append(f"iif(time >= {local_start} and time < {local_end}, {value}, ")
    if not branches:
        raise RuntimeError("No timeline markers found.")
    return "Text(" + "".join(branches) + '""' + ")" * (len(branches) + 1)


def create_marker_text_plus(
    *, resolve_app: Any, comp: Any, tool_name: str = "index2", size: float = 0.04,
    center: tuple[float, float] = (0.4, 0.25),
) -> Any:
    project = resolve_app.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("No current DaVinci Resolve project.")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise RuntimeError("No current DaVinci Resolve timeline.")
    captions = marker_captions(timeline.GetMarkers() or {})
    if not captions:
        raise RuntimeError("No timeline markers found.")

    fps = _timeline_fps(timeline, project)
    timeline_start = int(_call(timeline, "GetStartFrame", 0))
    item = _call(timeline, "GetCurrentVideoItem", None)
    item_start = int(_call(item, "GetStart", timeline_start)) if item else timeline_start
    comp_start = int((comp.GetAttrs() or {}).get("COMPN_GlobalStart", 0))
    expression = build_styled_text_expression(
        captions, fps=fps, marker_origin=item_start - timeline_start, comp_origin=comp_start,
    )
    comp.Lock()
    try:
        tool = comp.FindTool(tool_name) or comp.AddTool("TextPlus")
        tool.SetAttrs({"TOOLS_Name": tool_name})
        tool.SetInput("Size", float(size))
        tool.SetInput("Center", {1: float(center[0]), 2: float(center[1])})
        tool.SetExpression("StyledText", expression)
    finally:
        comp.Unlock()
    return tool


def _timeline_fps(timeline: Any, project: Any) -> float:
    for owner, key in ((timeline, "timelineFrameRate"), (project, "timelineFrameRate"), (project, "timelinePlaybackFrameRate")):
        value = _call(owner, "GetSetting", None, key)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return 24.0


def _call(owner: Any, method_name: str, default: Any, *args: Any) -> Any:
    method = getattr(owner, method_name, None)
    if not callable(method):
        return default
    try:
        value = method(*args)
    except Exception:
        return default
    return default if value is None else value


def _lua_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
