from __future__ import annotations

from typing import Any

from smartlib.dcc.resolve import marker_text_plus_v2 as base


def create_marker_text_plus(*, resolve_app: Any, comp: Any, **kwargs: Any) -> Any:
    text_tool = base.create_marker_text_plus(resolve_app=resolve_app, comp=comp, **kwargs)
    _merge_over_media(comp, text_tool)
    return text_tool


def _merge_over_media(comp: Any, text_tool: Any) -> Any:
    media_out = comp.FindTool("MediaOut1") or _first_tool_by_id(comp, "MediaOut")
    if not media_out:
        raise RuntimeError("MediaOut node was not found in Fusion composition index 2.")

    media_out_input = _main_input(media_out)
    previous_output = _connected_output(media_out_input)
    previous_tool = _output_tool(previous_output)
    if previous_tool and _tool_id(previous_tool) == "Merge":
        _connect(previous_tool, "Foreground", _main_output(text_tool))
        return previous_tool
    if not previous_output:
        raise RuntimeError("MediaOut has no connected image to use as the Merge background.")

    merge = comp.FindTool("MarkerTextMerge") or comp.AddTool("Merge")
    merge.SetAttrs({"TOOLS_Name": "MarkerTextMerge"})
    _connect(merge, "Background", previous_output)
    _connect(merge, "Foreground", _main_output(text_tool))
    _connect(media_out, "Input", _main_output(merge))
    return merge


def _first_tool_by_id(comp: Any, tool_id: str) -> Any:
    tools = comp.GetToolList(False, tool_id) or {}
    return next(iter(tools.values()), None)


def _main_input(tool: Any) -> Any:
    finder = getattr(tool, "FindMainInput", None)
    return finder(1) if callable(finder) else getattr(tool, "Input", None)


def _main_output(tool: Any) -> Any:
    finder = getattr(tool, "FindMainOutput", None)
    return finder(1) if callable(finder) else getattr(tool, "Output", None)


def _connected_output(input_value: Any) -> Any:
    getter = getattr(input_value, "GetConnectedOutput", None)
    return getter() if callable(getter) else None


def _output_tool(output: Any) -> Any:
    getter = getattr(output, "GetTool", None)
    return getter() if callable(getter) else None


def _tool_id(tool: Any) -> str:
    attrs = tool.GetAttrs() or {}
    return str(attrs.get("TOOLS_RegID") or attrs.get("TOOLST_RegID") or "")


def _connect(tool: Any, input_name: str, output: Any) -> None:
    connector = getattr(tool, "ConnectInput", None)
    if not callable(connector) or not connector(input_name, output):
        raise RuntimeError(f"Could not connect {input_name} on {getattr(tool, 'Name', 'Fusion tool')}.")
