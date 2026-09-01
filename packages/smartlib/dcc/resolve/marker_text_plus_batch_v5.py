from __future__ import annotations

from typing import Any

from smartlib.dcc.resolve import marker_text_plus_batch_v4 as base


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
    _set_h_anchor_left(text_tool)
    return text_tool


def _set_h_anchor_left(text_tool: Any) -> list[str]:
    applied = []
    for input_id in ("HorizontalJustification", "HorizontalJustificationNew"):
        try:
            result = text_tool.SetInput(input_id, -1.0)
            if result is not False:
                applied.append(input_id)
        except Exception:
            pass

    getter = getattr(text_tool, "GetInputList", None)
    input_list = getter() if callable(getter) else {}
    for key, input_value in (input_list or {}).items():
        attrs_getter = getattr(input_value, "GetAttrs", None)
        attrs = attrs_getter() if callable(attrs_getter) else {}
        input_id = str(attrs.get("INPS_ID") or key)
        description = " ".join(
            str(attrs.get(name) or "")
            for name in ("INPS_ID", "INPS_Name", "INPS_IC_Name")
        ).lower()
        is_anchor = "horizontal" in description and (
            "anchor" in description or "justification" in description
        )
        if not is_anchor and "h anchor" not in description:
            continue
        try:
            text_tool.SetInput(input_id, -1.0)
            applied.append(input_id)
        except Exception:
            try:
                input_value[0] = -1.0
                applied.append(input_id)
            except Exception:
                pass
    if not applied:
        raise RuntimeError("Could not find the Text+ H Anchor input.")
    return sorted(set(applied))
