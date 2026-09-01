"""Destructively round-trip animation curves in memory and report differences.

The opened Maya scene is never saved.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path


def _sample_times(keys):
    times = sorted(float(key["time"]) for key in keys if key.get("time") is not None)
    result = set(times)
    for left, right in zip(times, times[1:]):
        result.add((left + right) * 0.5)
        result.add(left + (right - left) * 0.25)
        result.add(left + (right - left) * 0.75)
    return sorted(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scene")
    parser.add_argument("curves")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(args.repo) / "packages"))
    import maya.standalone

    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from smartlib.dcc.maya.animation_curves import apply_animation_curve_data

    cmds.file(args.scene, open=True, force=True, prompt=False)
    with Path(args.curves).open("r", encoding="utf-8") as stream:
        data = json.load(stream)

    before = {}
    for curve in data.get("curves") or []:
        for plug in curve.get("destinations") or []:
            if not cmds.objExists(plug):
                continue
            before[plug] = {
                str(time): float(cmds.getAttr(plug, time=time))
                for time in _sample_times(curve.get("keys") or [])
            }
    static_before = {}
    for item in data.get("static_values") or []:
        plug = str(item.get("destination") or "")
        if plug and cmds.objExists(plug):
            try:
                static_before[plug] = cmds.getAttr(plug)
            except (RuntimeError, TypeError, ValueError):
                pass

    result = apply_animation_curve_data(data, clear_existing=True, strict_destinations=True)
    differences = []
    for plug, samples in before.items():
        for time_text, expected in samples.items():
            time = float(time_text)
            actual = float(cmds.getAttr(plug, time=time))
            if not math.isclose(actual, expected, rel_tol=1.0e-9, abs_tol=1.0e-7):
                differences.append(
                    {
                        "plug": plug,
                        "time": time,
                        "expected": expected,
                        "actual": actual,
                        "difference": actual - expected,
                    }
                )
    for plug, expected in static_before.items():
        actual = cmds.getAttr(plug)
        if actual != expected:
            differences.append(
                {
                    "plug": plug,
                    "time": None,
                    "expected": expected,
                    "actual": actual,
                    "difference": "static value changed",
                }
            )
    report = {
        "apply": result,
        "sample_count": sum(len(value) for value in before.values()) + len(static_before),
        "difference_count": len(differences),
        "differences": differences,
    }
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "applied_destinations": result["applied_destinations"],
                "skipped_destinations": result["skipped_destinations"],
                "sample_count": report["sample_count"],
                "difference_count": report["difference_count"],
            },
            indent=2,
        )
    )
    exit_code = 1 if differences else 0
    maya.standalone.uninitialize()
    sys.stdout.flush()
    os._exit(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
