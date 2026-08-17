"""Bake namespace-agnostic mocap animation through an MCR rig onto an ANM rig."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "packages"))
from smartlib.retarget.profile import load_retarget_profile  # noqa: E402

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402
from maya.api import OpenMaya as om  # noqa: E402


def leaf(node: str) -> str:
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def unique_by_leaf(name: str, *, namespace_contains: str | None = None) -> str:
    matches = []
    for node in cmds.ls(long=True) or []:
        short = node.rsplit("|", 1)[-1]
        if leaf(node) != name:
            continue
        if namespace_contains and namespace_contains not in short:
            continue
        matches.append(node)
    if len(matches) != 1:
        raise RuntimeError(f"Expected one node named {name!r}, found {matches}")
    return matches[0]


def load_plugins(profile: dict[str, Any]) -> None:
    for plugin in profile.get("required_plugins", []):
        if not os.path.isfile(plugin):
            raise RuntimeError(f"Required plug-in does not exist: {plugin}")
        cmds.loadPlugin(plugin, quiet=True)
    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya", quiet=True)


def transfer_names(profile: dict[str, Any]) -> list[str]:
    result = []
    excluded = set(profile.get("excluded_transfer_nodes", []))
    for names in profile["transfer_nodes"].values():
        result.extend(name for name in names if name not in excluded)
    return result


def import_source_fbx(path: str) -> None:
    cmds.file(
        path,
        i=True,
        type="FBX",
        ignoreVersion=True,
        mergeNamespacesOnClash=False,
        namespace="MOCAP",
        options="fbx",
        preserveReferences=True,
    )


def skeleton_pairs(prefix: str) -> list[tuple[str, str]]:
    pairs = []
    for source in cmds.ls(type="joint", long=True) or []:
        if ":" not in source.rsplit("|", 1)[-1]:
            continue
        name = leaf(source)
        if not name.startswith(prefix):
            continue
        targets = [
            node for node in cmds.ls(type="joint", long=True) or []
            if leaf(node) == name and ":" not in node.rsplit("|", 1)[-1]
        ]
        if len(targets) == 1:
            pairs.append((source, targets[0]))
    if not pairs:
        raise RuntimeError("No matching mocap/MCR skeleton joints were found")
    return pairs


def writable(plug: str) -> bool:
    return bool(cmds.objExists(plug) and cmds.getAttr(plug, settable=True))


def set_compound(node: str, attribute: str, value: tuple[float, float, float]) -> None:
    plug = f"{node}.{attribute}"
    if writable(plug):
        cmds.setAttr(plug, *value, type="double3")


def world_position(node: str) -> om.MVector:
    return om.MVector(cmds.xform(node, query=True, worldSpace=True, translation=True))


def pole_geometry(start: str, middle: str, end: str, distance_ratio: float) -> tuple[om.MVector, om.MVector, om.MVector]:
    start_pos = world_position(start)
    middle_pos = world_position(middle)
    end_pos = world_position(end)
    chain = end_pos - start_pos
    chain_length = chain.length()
    if chain_length < 1.0e-8:
        raise RuntimeError(f"Zero-length pole-vector chain: {start}, {middle}, {end}")
    chain_direction = chain.normal()
    projected = start_pos + chain_direction * ((middle_pos - start_pos) * chain_direction)
    bend = middle_pos - projected
    if bend.length() < 1.0e-6:
        bend = om.MVector(0.0, 0.0, 1.0)
        if abs(bend * chain_direction) > 0.95:
            bend = om.MVector(1.0, 0.0, 0.0)
        bend -= chain_direction * (bend * chain_direction)
    pole_direction = bend.normal()
    pole_position = middle_pos + pole_direction * chain_length * float(distance_ratio)
    return pole_position, pole_direction, chain_direction


def matrix_axes(node: str) -> list[om.MVector]:
    matrix = cmds.xform(node, query=True, worldSpace=True, matrix=True)
    return [om.MVector(matrix[0:3]).normal(), om.MVector(matrix[4:7]).normal(), om.MVector(matrix[8:11]).normal()]


def closest_axis(axes: list[om.MVector], direction: om.MVector, excluded: set[int] | None = None) -> tuple[int, float]:
    excluded = excluded or set()
    candidates = [(index, axis * direction) for index, axis in enumerate(axes) if index not in excluded]
    index, dot = max(candidates, key=lambda item: abs(item[1]))
    return index, 1.0 if dot >= 0.0 else -1.0


def oriented_matrix(
    position: om.MVector,
    pole_direction: om.MVector,
    chain_direction: om.MVector,
    pole_axis: tuple[int, float],
    chain_axis: tuple[int, float],
) -> list[float]:
    rows: list[om.MVector | None] = [None, None, None]
    rows[pole_axis[0]] = pole_direction * pole_axis[1]
    rows[chain_axis[0]] = chain_direction * chain_axis[1]
    missing = ({0, 1, 2} - {pole_axis[0], chain_axis[0]}).pop()
    if missing == 0:
        rows[0] = rows[1] ^ rows[2]
    elif missing == 1:
        rows[1] = rows[2] ^ rows[0]
    else:
        rows[2] = rows[0] ^ rows[1]
    resolved = [row.normal() for row in rows]
    return [
        resolved[0].x, resolved[0].y, resolved[0].z, 0.0,
        resolved[1].x, resolved[1].y, resolved[1].z, 0.0,
        resolved[2].x, resolved[2].y, resolved[2].z, 0.0,
        position.x, position.y, position.z, 1.0,
    ]


def sample_pole_vectors(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for name, definition in profile.get("pole_vectors", {}).items():
        if not definition.get("enabled", True):
            continue
        chain = [unique_by_leaf(node) for node in definition["chain"]]
        control = unique_by_leaf(definition["control"])
        position, pole_direction, chain_direction = pole_geometry(*chain, definition.get("distance_ratio", 1.0))
        axes = matrix_axes(control)
        pole_axis = closest_axis(axes, pole_direction)
        chain_axis = closest_axis(axes, chain_direction, {pole_axis[0]})
        result[name] = {
            "definition": definition,
            "chain": chain,
            "control": control,
            "pole_axis": pole_axis,
            "chain_axis": chain_axis,
            "matrices": [oriented_matrix(position, pole_direction, chain_direction, pole_axis, chain_axis)],
        }
    return result


def world_matrix(node: str) -> om.MMatrix:
    return om.MMatrix(cmds.xform(node, query=True, worldSpace=True, matrix=True))


def prepare_wrist_solvers(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for name, definition in profile.get("wrists", {}).items():
        source = unique_by_leaf(definition["source"])
        target = unique_by_leaf(definition["target"])
        source_rest = world_matrix(source)
        target_rest = world_matrix(target)
        result[name] = {
            "definition": definition,
            "source": source,
            "offset": source_rest.inverse() * target_rest,
            "matrices": [],
        }
    return result


def match_dual_arms(profile: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    matched = {}
    for name, definition in profile.get("dual_arm_match", {}).items():
        switch_node, switch_attr = definition["switch"].rsplit(".", 1)
        switch = f"{unique_by_leaf(switch_node)}.{switch_attr}"
        control = unique_by_leaf(definition["ik_control"])
        result_wrist = unique_by_leaf(definition["result_wrist"])
        iterations = int(definition.get("iterations", 2))
        for frame in range(int(start), int(end) + 1):
            cmds.currentTime(frame, edit=True)
            cmds.setAttr(switch, 0)
            target = world_matrix(result_wrist)
            cmds.setAttr(switch, 1)
            for _ in range(iterations):
                current = world_matrix(result_wrist)
                control_matrix = world_matrix(control)
                correction = current.inverse() * target
                cmds.xform(control, worldSpace=True, matrix=list(control_matrix * correction))
            cmds.setKeyframe(
                control,
                attribute=["translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"],
                time=frame,
            )
        cmds.setAttr(switch, 0)
        matched[name] = {"frames": int(end) - int(start) + 1, "iterations": iterations}
    return matched


def sample_mcr(profile: dict[str, Any]) -> tuple[dict[str, dict[str, list[float]]], dict[str, Any], dict[str, dict[str, Any]]]:
    cmds.file(profile["mcr_scene"], open=True, force=True, prompt=False)
    load_plugins(profile)
    wrist_samples = prepare_wrist_solvers(profile)
    import_source_fbx(profile["mocap_fbx"])
    pairs = skeleton_pairs(profile["source_skeleton"]["namespace_agnostic_prefix"])
    nodes = {name: unique_by_leaf(name) for name in transfer_names(profile)}
    start, end = profile["frame_range"]
    attributes = profile["bake_attributes"]
    samples = {name: {attr: [] for attr in attributes} for name in nodes}
    pole_samples = None
    for frame in range(int(start), int(end) + 1):
        cmds.currentTime(frame, edit=True)
        for source, target in pairs:
            for compound in profile["source_skeleton"]["copy_attributes"]:
                value = cmds.getAttr(f"{source}.{compound}")[0]
                set_compound(target, compound, value)
        cmds.dgdirty(allPlugs=True)
        for item in wrist_samples.values():
            item["matrices"].append(list(world_matrix(item["source"]) * item["offset"]))
        if pole_samples is None:
            pole_samples = sample_pole_vectors(profile)
        else:
            for item in pole_samples.values():
                position, pole_direction, chain_direction = pole_geometry(
                    *item["chain"], item["definition"].get("distance_ratio", 1.0)
                )
                item["matrices"].append(oriented_matrix(
                    position, pole_direction, chain_direction, item["pole_axis"], item["chain_axis"]
                ))
        for name, node in nodes.items():
            for attr in attributes:
                samples[name][attr].append(float(cmds.getAttr(f"{node}.{attr}")))
    report = {
        "skeleton_pairs": len(pairs),
        "transfer_nodes": len(nodes),
        "pole_vectors": len(pole_samples or {}),
        "wrist_solvers": len(wrist_samples),
    }
    return samples, report, {"poles": pole_samples or {}, "wrists": wrist_samples}


def apply_to_animation_rig(
    profile: dict[str, Any], samples: dict[str, dict[str, list[float]]], solver_samples: dict[str, dict[str, Any]], output: str
) -> dict[str, Any]:
    cmds.file(profile["animation_rig_scene"], open=True, force=True, prompt=False)
    load_plugins(profile)
    cmds.currentUnit(time=profile["time_unit"])
    start, end = profile["frame_range"]
    keyed_plugs = 0
    skipped = []
    applied_settings = {}
    for plug_name, setting in profile.get("rig_settings", {}).items():
        if isinstance(setting, dict):
            value = setting["value"]
            key_setting = bool(setting.get("key", False))
        else:
            value = setting
            key_setting = True
        node_name, attribute = plug_name.rsplit(".", 1)
        node = unique_by_leaf(node_name)
        plug = f"{node}.{attribute}"
        if not cmds.objExists(plug):
            raise RuntimeError(f"Rig setting does not exist: {plug_name}")
        cmds.setAttr(plug, value)
        if key_setting:
            cmds.setKeyframe(plug, time=start, value=value)
            cmds.setKeyframe(plug, time=end, value=value)
        applied_settings[plug_name] = cmds.getAttr(plug)
    for name, values_by_attr in samples.items():
        node = unique_by_leaf(name)
        for attr, values in values_by_attr.items():
            plug = f"{node}.{attr}"
            if not cmds.objExists(plug) or cmds.getAttr(plug, lock=True):
                skipped.append(plug)
                continue
            try:
                cmds.cutKey(plug, clear=True, time=(start, end))
                for offset, value in enumerate(values):
                    cmds.setKeyframe(plug, time=start + offset, value=value)
                keyed_plugs += 1
            except RuntimeError:
                skipped.append(plug)
    for item in solver_samples["poles"].values():
        control = unique_by_leaf(item["definition"]["control"])
        for offset, matrix in enumerate(item["matrices"]):
            frame = start + offset
            cmds.currentTime(frame, edit=True)
            cmds.xform(control, worldSpace=True, matrix=matrix)
            cmds.setKeyframe(control, attribute=["rotateX", "rotateY", "rotateZ"], time=frame)
    for item in solver_samples["wrists"].values():
        target = unique_by_leaf(item["definition"]["target"])
        for offset, matrix_values in enumerate(item["matrices"]):
            frame = start + offset
            cmds.currentTime(frame, edit=True)
            matrix = om.MMatrix(matrix_values)
            rotation = om.MTransformationMatrix(matrix).rotation()
            degrees = [math.degrees(rotation.x), math.degrees(rotation.y), math.degrees(rotation.z)]
            cmds.xform(target, worldSpace=True, rotation=degrees)
            cmds.setKeyframe(target, attribute=["rotateX", "rotateY", "rotateZ"], time=frame)
    dual_match = match_dual_arms(profile, start, end)
    curves = cmds.listConnections(
        [unique_by_leaf(name) for name in samples], source=True, destination=False, type="animCurve"
    ) or []
    if curves:
        cmds.filterCurve(sorted(set(curves)))
    cmds.playbackOptions(min=start, max=end, animationStartTime=start, animationEndTime=end)
    cmds.currentTime(start, edit=True)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=str(output_path))
    cmds.file(save=True, force=True, type="mayaBinary")
    return {
        "keyed_plugs": keyed_plugs,
        "skipped_plugs": skipped,
        "rig_settings": applied_settings,
        "dual_arm_match": dual_match,
        "output": str(output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    parser.add_argument("output")
    parser.add_argument("--report")
    args = parser.parse_args()
    profile = load_retarget_profile(args.profile)
    load_plugins(profile)
    samples, report, solver_samples = sample_mcr(profile)
    report.update(apply_to_animation_rig(profile, samples, solver_samples, args.output))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
