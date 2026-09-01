from __future__ import annotations

from typing import Any

from smartlib.dcc.resolve import marker_text_plus_batch_v2 as base


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
    _set_gray_background(text_tool)
    return text_tool


def _set_gray_background(text_tool: Any) -> None:
    # Text+ Shading Element 2: filled text box behind the glyphs.
    text_tool.SetInput("Enabled2", 1.0)
    text_tool.SetInput("ElementShape2", 2.0)
    text_tool.SetInput("Level2", 1.0)
    text_tool.SetInput("Red2", 0.35)
    text_tool.SetInput("Green2", 0.35)
    text_tool.SetInput("Blue2", 0.35)
    text_tool.SetInput("Alpha2", 0.5)
    text_tool.SetInput("ExtendHorizontal2", 0.12)
    text_tool.SetInput("ExtendVertical2", 0.08)
