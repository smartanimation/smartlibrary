from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


DEFAULT_ATTRIBUTES = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
    "visibility",
    "relative",
    "stretch",
)


def _controller(cmds, namespace: str, controller: str) -> str:
    matches = cmds.ls(f"{namespace}:{controller}", long=True) or []
    if len(matches) != 1:
        raise RuntimeError(
            f"Controller must resolve uniquely: {namespace}:{controller} "
            f"(matches={matches})"
        )
    return str(matches[0])


def _sample(cmds, node: str, attributes: tuple[str, ...], frames: tuple[float, ...]):
    return {
        attribute: [cmds.getAttr(f"{node}.{attribute}", time=frame) for frame in frames]
        for attribute in attributes
        if cmds.objExists(f"{node}.{attribute}")
    }


def _connection_diagnostics(cmds, node: str, attributes: tuple[str, ...]):
    rows = {}
    for attribute in attributes:
        plug = f"{node}.{attribute}"
        if not cmds.objExists(plug):
            continue
        try:
            connections = cmds.listConnections(
                plug,
                source=True,
                destination=False,
                plugs=True,
                connections=True,
                skipConversionNodes=False,
            ) or []
        except Exception as exc:
            connections = [f"ERROR: {exc}"]
        try:
            source = cmds.connectionInfo(plug, sourceFromDestination=True)
        except Exception as exc:
            source = f"ERROR: {exc}"
        rows[attribute] = {
            "keyable": cmds.getAttr(plug, keyable=True),
            "channel_box": cmds.getAttr(plug, channelBox=True),
            "list_connections": connections,
            "connection_info": source,
        }
    reference_node = cmds.referenceQuery(node, referenceNode=True)
    try:
        edits = cmds.referenceQuery(
            reference_node,
            editStrings=True,
            successfulEdits=True,
        ) or []
    except Exception as exc:
        edits = [f"ERROR: {exc}"]
    return {
        "node": node,
        "reference_node": reference_node,
        "attributes": rows,
        "spine_edits": [str(edit) for edit in edits if "spineChest" in str(edit)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Animation Curve collection and Apply in a clean Maya scene."
    )
    parser.add_argument("--source-scene", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--controller-root", default="allRigSet")
    parser.add_argument("--controller", required=True)
    parser.add_argument("--attribute", action="append")
    parser.add_argument("--report")
    parser.add_argument("--output-scene")
    args = parser.parse_args(argv)

    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds

        from smartlib.dcc.maya.animation_curves import (
            apply_animation_curve_data,
            collect_animation_curves_for_cast,
        )

        source_scene = Path(args.source_scene)
        if not source_scene.is_file():
            raise FileNotFoundError(source_scene)
        attributes = tuple(args.attribute or DEFAULT_ATTRIBUTES)
        cmds.file(str(source_scene), open=True, force=True, ignoreVersion=True)
        source_controller = _controller(cmds, args.namespace, args.controller)
        reference_path = Path(
            cmds.referenceQuery(source_controller, filename=True, withoutCopyNumber=True)
        )
        if not reference_path.is_file():
            raise FileNotFoundError(reference_path)
        frame_min = float(cmds.playbackOptions(query=True, animationStartTime=True))
        frame_max = float(cmds.playbackOptions(query=True, animationEndTime=True))
        frames = (frame_min, (frame_min + frame_max) / 2.0, frame_max)
        source_samples = _sample(cmds, source_controller, attributes, frames)
        source_key_counts = {
            attribute: int(
                cmds.keyframe(
                    f"{source_controller}.{attribute}",
                    query=True,
                    keyframeCount=True,
                )
                or 0
            )
            for attribute in attributes
        }

        data = collect_animation_curves_for_cast(
            cast_key=f"{args.namespace}_roundtrip",
            namespace=args.namespace,
            controller_root=args.controller_root,
            source_workfile=source_scene,
        )
        expected_suffixes = {
            f"{args.namespace}:{args.controller}.{attribute}"
            for attribute in attributes
        }
        collected = {
            destination.rsplit("|", 1)[-1]
            for curve in data.get("curves") or []
            for destination in curve.get("destinations") or []
            if destination.rsplit("|", 1)[-1] in expected_suffixes
        }
        missing = sorted(expected_suffixes - collected)
        if missing:
            from smartlib.dcc.maya import animation_curves as animation_curve_module

            root_node = animation_curve_module._resolve_controller_root(
                cmds, args.namespace, args.controller_root
            )
            members = animation_curve_module._controller_members(cmds, root_node)
            candidates = animation_curve_module._animated_candidate_plugs(
                cmds, source_controller
            )
            diagnostic = {
                "missing": missing,
                "controller_in_contract": source_controller
                in set(data.get("controller_contract") or []),
                "controller_root": root_node,
                "controller_is_member": source_controller in set(members),
                "matching_members": [
                    member for member in members if "spineChest" in member
                ],
                "candidate_plugs": [
                    plug for plug in candidates if "spineChest" in plug
                ],
                "upstream": {
                    plug: animation_curve_module._upstream_anim_curves(cmds, plug)
                    for plug in candidates
                    if plug.rsplit(".", 1)[-1] in attributes
                },
                "connections": _connection_diagnostics(
                    cmds, source_controller, attributes
                ),
            }
            if args.report:
                report = Path(args.report)
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    json.dumps(diagnostic, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
            raise RuntimeError(
                "Controller animation destinations were not collected: "
                + ", ".join(missing)
            )

        cmds.file(new=True, force=True)
        cmds.file(
            str(reference_path),
            reference=True,
            namespace=args.namespace,
            mergeNamespacesOnClash=False,
        )
        target_controller = _controller(cmds, args.namespace, args.controller)
        validation_data = dict(data)
        validation_data["curves"] = [
            curve
            for curve in data.get("curves") or []
            if any(
                destination.rsplit("|", 1)[-1] in expected_suffixes
                for destination in curve.get("destinations") or []
            )
        ]
        validation_data["static_values"] = [
            item
            for item in data.get("static_values") or []
            if str(item.get("destination") or "").rsplit("|", 1)[-1].split(".", 1)[0]
            == f"{args.namespace}:{args.controller}"
        ]
        apply_report = apply_animation_curve_data(
            validation_data,
            namespace=args.namespace,
            clear_existing=True,
            strict_destinations=True,
        )
        target_samples = _sample(cmds, target_controller, attributes, frames)
        target_key_counts = {
            attribute: int(
                cmds.keyframe(
                    f"{target_controller}.{attribute}",
                    query=True,
                    keyframeCount=True,
                )
                or 0
            )
            for attribute in attributes
        }
        skipped_attributes = {
            str(item.get("target") or "").rsplit(".", 1)[-1]
            for item in apply_report.get("skipped_report") or []
        }
        missing_keys = sorted(
            attribute
            for attribute, count in source_key_counts.items()
            if count
            and attribute not in skipped_attributes
            and not target_key_counts.get(attribute)
        )
        if missing_keys:
            raise RuntimeError(
                "Applied controller attributes have no animation keys: "
                + ", ".join(missing_keys)
            )
        mismatches = []
        for attribute, expected_values in source_samples.items():
            actual_values = target_samples.get(attribute) or []
            if len(expected_values) != len(actual_values):
                mismatches.append(attribute)
                continue
            for expected, actual in zip(expected_values, actual_values):
                if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                    if not math.isclose(float(expected), float(actual), abs_tol=1.0e-6):
                        mismatches.append(attribute)
                        break
                elif expected != actual:
                    mismatches.append(attribute)
                    break
        if mismatches:
            raise RuntimeError(
                "Applied controller samples differ from source: "
                + ", ".join(sorted(set(mismatches)))
            )
        output_scene = ""
        if args.output_scene:
            scene_path = Path(args.output_scene)
            scene_path.parent.mkdir(parents=True, exist_ok=True)
            cmds.file(rename=str(scene_path))
            file_type = "mayaBinary" if scene_path.suffix.lower() == ".mb" else "mayaAscii"
            cmds.file(save=True, type=file_type, force=True)
            output_scene = str(scene_path)

        result = {
            "ok": True,
            "source_scene": str(source_scene),
            "reference": str(reference_path),
            "controller": target_controller,
            "frames": frames,
            "collected_destinations": sorted(collected),
            "source_samples": source_samples,
            "target_samples": target_samples,
            "source_key_counts": source_key_counts,
            "target_key_counts": target_key_counts,
            "skipped_attributes": sorted(skipped_attributes),
            "output_scene": output_scene,
            "curve_count": len(data.get("curves") or []),
            "static_value_count": len(data.get("static_values") or []),
            "apply": apply_report,
        }
        output = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if args.report:
            report = Path(args.report)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(output, encoding="utf-8")
        print(output)
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
