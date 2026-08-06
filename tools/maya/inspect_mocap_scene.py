"""Dump a compact Maya scene inventory for mocap retarget investigation."""

from __future__ import annotations

import json
import os
import sys

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402


def _animated_plugs(node: str) -> list[str]:
    curves = cmds.listConnections(node, source=True, destination=False, type="animCurve") or []
    result = []
    for curve in curves:
        result.extend(cmds.listConnections(curve, source=False, destination=True, plugs=True) or [])
    return sorted(set(result))


def inspect(path: str) -> dict:
    cmds.file(new=True, force=True)
    if path.lower().endswith(".fbx") and not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya", quiet=True)
    cmds.file(path, open=True, force=True, prompt=False, loadReferenceDepth="all")
    joints = cmds.ls(type="joint", long=True) or []
    transforms = cmds.ls(type="transform", long=True) or []
    animated = []
    for node in transforms:
        plugs = _animated_plugs(node)
        if plugs:
            animated.append({"node": node, "plugs": plugs})
    controls = []
    for node in transforms:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        if any(cmds.nodeType(shape) == "nurbsCurve" for shape in shapes):
            controls.append(node)
    return {
        "path": path,
        "maya_version": cmds.about(version=True),
        "time_unit": cmds.currentUnit(query=True, time=True),
        "linear_unit": cmds.currentUnit(query=True, linear=True),
        "playback": [cmds.playbackOptions(query=True, min=True), cmds.playbackOptions(query=True, max=True)],
        "references": cmds.file(query=True, reference=True) or [],
        "namespaces": cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or [],
        "joints": joints,
        "controls": controls,
        "animated": animated,
    }


def main() -> None:
    output = sys.argv[-1]
    paths = sys.argv[1:-1]
    payload = []
    for path in paths:
        try:
            payload.append(inspect(path))
        except Exception as exc:
            payload.append({"path": path, "error": repr(exc)})
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
