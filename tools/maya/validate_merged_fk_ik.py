"""Compare merged FK and IK evaluations against their proven source scenes."""

from __future__ import annotations

import json
import math
import sys

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402


FRAMES = (1, 273, 500, 750, 1012)


def leaf(node: str) -> str:
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def node(name: str) -> str:
    matches = [item for item in cmds.ls(long=True) or [] if leaf(item) == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {name}, found {matches}")
    return matches[0]


def capture(path: str, enable: int) -> dict[str, dict[str, list[float]]]:
    cmds.file(path, open=True, force=True, prompt=False)
    for side in ("L", "R"):
        cmds.setAttr(f"{node(f'CTL_{side}_arm')}.enable", enable)
    result = {}
    for frame in FRAMES:
        cmds.currentTime(frame, edit=True)
        result[str(frame)] = {
            side: cmds.xform(node(f"J_{side}_wrist"), query=True, worldSpace=True, matrix=True)
            for side in ("L", "R")
        }
    return result


def compare(expected: dict, actual: dict) -> dict:
    errors = {side: [] for side in ("L", "R")}
    for frame in map(str, FRAMES):
        for side in errors:
            errors[side].append(math.sqrt(sum(
                (a - b) ** 2 for a, b in zip(expected[frame][side], actual[frame][side])
            )))
    return {side: {"errors": values, "max_matrix_error": max(values)} for side, values in errors.items()}


def main() -> None:
    fk_source, ik_source, merged, output = sys.argv[1:5]
    cmds.loadPlugin("I:/ELCD/lib/tools/py3/dlas-maya-plug-ins/2024/clgIKNode.mll", quiet=True)
    expected_fk = capture(fk_source, 0)
    actual_fk = capture(merged, 0)
    expected_ik = capture(ik_source, 1)
    actual_ik = capture(merged, 1)
    report = {
        "frames": list(FRAMES),
        "fk_against_fk_source": compare(expected_fk, actual_fk),
        "ik_against_ik_source": compare(expected_ik, actual_ik),
    }
    with open(output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
