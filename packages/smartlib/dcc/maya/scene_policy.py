from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig


FPS_TO_MAYA_TIME = {
    24: "film",
    25: "pal",
    30: "ntsc",
    48: "show",
    50: "palf",
    60: "ntscf",
}


def maya_scene_policy(config_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    cfg_dir = Path(config_dir or os.environ.get("PROJECT_CONFIG_DIR") or "")
    data = ProjectConfig(cfg_dir).load("tools.yml") if cfg_dir else {}
    policy = (((data.get("dcc") or {}).get("maya") or {}).get("scene_policy") or {})
    return {
        "unit": {
            "linear": ((policy.get("unit") or {}).get("linear") or "centimeter"),
            "angle": ((policy.get("unit") or {}).get("angle") or "degree"),
            "time_from_fps": bool((policy.get("unit") or {}).get("time_from_fps", True)),
        },
        "up_axis": str(policy.get("up_axis") or "y"),
        "playback": {
            "set_range_from_context": bool((policy.get("playback") or {}).get("set_range_from_context", True)),
        },
    }


def apply_scene_policy(
    cmds: Any,
    *,
    fps: int | float | str | None = None,
    frame_range: tuple[int | float, int | float] | list[int | float] | None = None,
    config_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    policy = maya_scene_policy(config_dir)
    applied: dict[str, Any] = {}
    unit = policy["unit"]
    try:
        kwargs = {}
        if unit.get("linear"):
            kwargs["linear"] = _maya_linear_unit(unit["linear"])
        if unit.get("angle"):
            kwargs["angle"] = _maya_angle_unit(unit["angle"])
        if unit.get("time_from_fps") and fps:
            kwargs["time"] = _maya_time_unit(fps)
        if kwargs:
            cmds.currentUnit(**kwargs)
            applied["unit"] = kwargs
    except Exception as exc:
        applied["unit_error"] = str(exc)

    try:
        up_axis = str(policy.get("up_axis") or "y").lower()
        current_up_axis = str(cmds.upAxis(query=True, axis=True) or "").lower()
        if current_up_axis != up_axis:
            cmds.upAxis(axis=up_axis, rotateView=False)
            applied["up_axis"] = up_axis
        else:
            applied["up_axis"] = current_up_axis
    except Exception as exc:
        applied["up_axis_error"] = str(exc)

    if policy["playback"].get("set_range_from_context") and frame_range:
        try:
            start, end = frame_range
            cmds.playbackOptions(minTime=float(start), animationStartTime=float(start))
            cmds.playbackOptions(maxTime=float(end), animationEndTime=float(end))
            applied["frame_range"] = [float(start), float(end)]
        except Exception as exc:
            applied["frame_range_error"] = str(exc)

    return applied


def _maya_linear_unit(value: str) -> str:
    mapping = {
        "centimeter": "cm",
        "centimeters": "cm",
        "cm": "cm",
        "meter": "m",
        "meters": "m",
        "m": "m",
        "millimeter": "mm",
        "millimeters": "mm",
        "mm": "mm",
    }
    return mapping.get(str(value).lower(), str(value))


def _maya_angle_unit(value: str) -> str:
    mapping = {"degree": "deg", "degrees": "deg", "deg": "deg", "radian": "rad", "radians": "rad", "rad": "rad"}
    return mapping.get(str(value).lower(), str(value))


def _maya_time_unit(fps: int | float | str) -> str:
    try:
        fps_int = int(float(fps))
    except (TypeError, ValueError):
        return str(fps)
    return FPS_TO_MAYA_TIME.get(fps_int, f"{fps_int}fps")
