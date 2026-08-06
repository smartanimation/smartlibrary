"""Merge proven arm IK curves into the proven FK retarget scene."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402


IK_CHANNELS = {
    "CTL_L_arm": ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"),
    "CTL_L_armRoot": ("translateX", "translateY", "translateZ"),
    "CTL_L_elbow": ("rotateX", "rotateY", "rotateZ"),
    "CTL_R_arm": ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"),
    "CTL_R_armRoot": ("translateX", "translateY", "translateZ"),
    "CTL_R_elbow": ("rotateX", "rotateY", "rotateZ"),
}


def leaf(node: str) -> str:
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def node(name: str) -> str:
    matches = [item for item in cmds.ls(type="transform", long=True) or [] if leaf(item) == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {name}, found {matches}")
    return matches[0]


def sample_scene(path: str, start: int, end: int) -> dict[str, dict[str, list[float]]]:
    cmds.file(path, open=True, force=True, prompt=False)
    resolved = {name: node(name) for name in IK_CHANNELS}
    samples = {name: {attr: [] for attr in attrs} for name, attrs in IK_CHANNELS.items()}
    for frame in range(start, end + 1):
        cmds.currentTime(frame, edit=True)
        for name, attrs in IK_CHANNELS.items():
            for attr in attrs:
                samples[name][attr].append(float(cmds.getAttr(f"{resolved[name]}.{attr}")))
    return samples


def apply_scene(path: str, output: str, samples: dict[str, dict[str, list[float]]], start: int, end: int) -> dict:
    cmds.file(path, open=True, force=True, prompt=False)
    keyed = 0
    for name, values_by_attr in samples.items():
        target = node(name)
        for attr, values in values_by_attr.items():
            plug = f"{target}.{attr}"
            cmds.cutKey(plug, clear=True, time=(start, end))
            for offset, value in enumerate(values):
                cmds.setKeyframe(plug, time=start + offset, value=value)
            keyed += 1
    switches = {}
    for name in ("CTL_L_arm", "CTL_R_arm"):
        plug = f"{node(name)}.enable"
        cmds.cutKey(plug, clear=True)
        cmds.setAttr(plug, 0)
        switches[plug] = {"value": cmds.getAttr(plug), "keys": int(cmds.keyframe(plug, query=True, keyframeCount=True) or 0)}
    cmds.playbackOptions(min=start, max=end, animationStartTime=start, animationEndTime=end)
    cmds.currentTime(start, edit=True)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=output)
    cmds.file(save=True, force=True, type="mayaBinary")
    return {"output": output, "ik_channels": keyed, "switches": switches}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fk", required=True)
    parser.add_argument("--ik", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    plugin = "I:/ELCD/lib/tools/py3/dlas-maya-plug-ins/2024/clgIKNode.mll"
    cmds.loadPlugin(plugin, quiet=True)
    start, end = 1, 1012
    samples = sample_scene(args.ik, start, end)
    report = apply_scene(args.fk, args.output, samples, start, end)
    report.update({"fk_source": args.fk, "ik_source": args.ik, "frame_range": [start, end]})
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
