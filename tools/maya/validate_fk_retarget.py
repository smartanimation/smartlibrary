"""Validate the DLI arm FK state in a baked Maya scene."""

from __future__ import annotations

import json
import math
import sys

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402


def leaf(node: str) -> str:
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def node(name: str) -> str:
    matches = [item for item in cmds.ls(long=True) or [] if leaf(item) == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {name}, found {matches}")
    return matches[0]


def key_count(name: str) -> int:
    return int(cmds.keyframe(node(name), query=True, keyframeCount=True) or 0)


def main() -> None:
    scene, output = sys.argv[1:3]
    cmds.loadPlugin("I:/ELCD/lib/tools/py3/dlas-maya-plug-ins/2024/clgIKNode.mll", quiet=True)
    cmds.file(scene, open=True, force=True, prompt=False)
    switch_match = {}
    for side in ("L", "R"):
        switch = f"{node(f'CTL_{side}_arm')}.enable"
        result_wrist = node(f"J_{side}_wrist")
        errors = []
        for frame in (1, 273, 500, 750, 1012):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(switch, 0)
            fk_matrix = cmds.xform(result_wrist, query=True, worldSpace=True, matrix=True)
            cmds.setAttr(switch, 1)
            ik_matrix = cmds.xform(result_wrist, query=True, worldSpace=True, matrix=True)
            errors.append(math.sqrt(sum((a - b) ** 2 for a, b in zip(fk_matrix, ik_matrix))))
        cmds.setAttr(switch, 0)
        switch_match[side] = {"frames": [1, 273, 500, 750, 1012], "max_matrix_error": max(errors), "errors": errors}
    report = {
        "scene": scene,
        "range": [cmds.playbackOptions(query=True, min=True), cmds.playbackOptions(query=True, max=True)],
        "arm_enable": {
            "left": cmds.getAttr(f"{node('CTL_L_arm')}.enable"),
            "right": cmds.getAttr(f"{node('CTL_R_arm')}.enable"),
        },
        "arm_enable_key_counts": {
            "left": int(cmds.keyframe(f"{node('CTL_L_arm')}.enable", query=True, keyframeCount=True) or 0),
            "right": int(cmds.keyframe(f"{node('CTL_R_arm')}.enable", query=True, keyframeCount=True) or 0),
        },
        "fk_key_counts": {name: key_count(name) for name in (
            "A_L_upArm", "A_L_lowArm", "A_L_wrist",
            "A_R_upArm", "A_R_lowArm", "A_R_wrist",
        )},
        "excluded_ik_key_counts": {name: key_count(name) for name in (
            "CTL_L_arm", "CTL_L_armRoot", "CTL_L_elbow",
            "CTL_R_arm", "CTL_R_armRoot", "CTL_R_elbow",
        )},
        "fk_ik_result_wrist_match": switch_match,
    }
    with open(output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
