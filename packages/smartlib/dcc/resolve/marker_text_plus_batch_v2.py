from __future__ import annotations

from typing import Any

from smartlib.dcc.resolve import marker_text_plus_batch as base


_original_apply_to_comp = base._apply_to_comp


def apply_to_video_track(**kwargs: Any) -> int:
    previous = base._apply_to_comp
    base._apply_to_comp = _apply_to_comp
    try:
        return base.apply_to_video_track(**kwargs)
    finally:
        base._apply_to_comp = previous


def _apply_to_comp(**kwargs: Any) -> Any:
    text_tool = _original_apply_to_comp(**kwargs)
    _set_black_left_aligned(text_tool)
    return text_tool


def _set_black_left_aligned(text_tool: Any) -> None:
    text_tool.SetInput("HorizontalJustification", -1.0)
    text_tool.SetInput("Red1", 0.0)
    text_tool.SetInput("Green1", 0.0)
    text_tool.SetInput("Blue1", 0.0)
