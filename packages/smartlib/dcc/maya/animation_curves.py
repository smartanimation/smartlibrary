from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any


ANIM_CURVE_TYPES = (
    "animCurveTA",
    "animCurveTL",
    "animCurveTT",
    "animCurveTU",
    "animCurveUA",
    "animCurveUL",
    "animCurveUT",
    "animCurveUU",
)


class AnimationCurveApplyError(RuntimeError):
    def __init__(self, message: str, report: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.report = report or []


def collect_animation_curves(*, source_workfile: str | Path = "") -> dict[str, Any]:
    cmds = _maya_cmds()
    return _collect_curves_from_nodes(cmds, cmds.ls(type=ANIM_CURVE_TYPES) or [], source_workfile=source_workfile)


def read_animation_curve_json(path: str | Path) -> dict[str, Any]:
    curve_path = Path(path)
    if not curve_path.exists():
        raise FileNotFoundError(f"Animation curve json was not found: {curve_path}")
    with curve_path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"Animation curve json must contain an object: {curve_path}")
    if data.get("publish_type") and data.get("publish_type") != "animation":
        raise ValueError(f"Not an animation publish json: {curve_path}")
    curves = data.get("curves")
    if curves is None:
        raise ValueError(f"Animation curve json has no 'curves' field: {curve_path}")
    if not isinstance(curves, list):
        raise ValueError(f"Animation curve json 'curves' field must be a list: {curve_path}")
    data["_path"] = str(curve_path).replace("\\", "/")
    return data


def summarize_animation_curve_data(data: dict[str, Any]) -> dict[str, Any]:
    curves = data.get("curves") or []
    key_count = 0
    destination_count = 0
    frame_min = None
    frame_max = None
    for curve in curves:
        keys = curve.get("keys") or []
        destinations = curve.get("destinations") or []
        key_count += len(keys)
        destination_count += len(destinations)
        for key in keys:
            time = key.get("time")
            if time is None:
                continue
            frame_min = float(time) if frame_min is None else min(frame_min, float(time))
            frame_max = float(time) if frame_max is None else max(frame_max, float(time))
    return {
        "path": data.get("_path", ""),
        "episode": data.get("episode", ""),
        "sequence": data.get("sequence", ""),
        "shot": data.get("shot", ""),
        "target": data.get("target") or data.get("cast_key", ""),
        "namespace": data.get("namespace", ""),
        "version": data.get("version", ""),
        "curve_count": len(curves),
        "key_count": key_count,
        "destination_count": destination_count,
        "static_value_count": len(data.get("static_values") or []),
        "controller_count": int(data.get("controller_count") or 0),
        "frame_range": [frame_min, frame_max] if frame_min is not None and frame_max is not None else [],
        "source_workfile": data.get("source_workfile", ""),
        "comment": data.get("comment", ""),
    }


def remap_animation_curve_destinations(
    data: dict[str, Any],
    *,
    namespace: str | None = None,
) -> list[dict[str, Any]]:
    cmds = _maya_cmds()
    source_namespace = str(data.get("namespace") or "")
    target_namespace = namespace if namespace is not None else source_namespace
    report = []
    for curve in data.get("curves") or []:
        for source_plug in curve.get("destinations") or []:
            target_plug = _remap_plug(str(source_plug), source_namespace, target_namespace)
            target_plug = _resolve_scene_plug(cmds, target_plug)
            state = "FOUND" if cmds.objExists(target_plug) else "MISSING"
            report.append(
                {
                    "curve": curve.get("curve", ""),
                    "source": str(source_plug),
                    "target": target_plug,
                    "state": state,
                }
            )
    return report


def apply_animation_curves_from_file(
    path: str | Path,
    *,
    namespace: str | None = None,
    clear_existing: bool = True,
    strict_destinations: bool = True,
) -> dict[str, Any]:
    data = read_animation_curve_json(path)
    return apply_animation_curve_data(
        data,
        namespace=namespace,
        clear_existing=clear_existing,
        strict_destinations=strict_destinations,
    )


def apply_animation_curve_data(
    data: dict[str, Any],
    *,
    namespace: str | None = None,
    clear_existing: bool = True,
    strict_destinations: bool = True,
) -> dict[str, Any]:
    cmds = _maya_cmds()
    duplicate_destinations = _duplicate_curve_destinations(data.get("curves") or [])
    if duplicate_destinations:
        details = ", ".join(
            f"{plug} ({len(curves)} curves)"
            for plug, curves in list(duplicate_destinations.items())[:5]
        )
        raise AnimationCurveApplyError(
            "Animation curve data has ambiguous destinations and cannot be applied: "
            f"{details}",
            report=[
                {
                    "target": plug,
                    "state": "AMBIGUOUS",
                    "curves": curves,
                }
                for plug, curves in duplicate_destinations.items()
            ],
        )
    static_report = _apply_static_values(
        cmds,
        data.get("static_values") or [],
        source_namespace=str(data.get("namespace") or ""),
        target_namespace=namespace,
        strict=strict_destinations,
    )
    report = remap_animation_curve_destinations(data, namespace=namespace)
    missing = [item for item in report if item["state"] != "FOUND"]
    if missing and strict_destinations:
        raise AnimationCurveApplyError(
            f"Animation curve destination remap failed: {len(missing)} missing destinations.",
            report=report,
        )

    applied_destinations = 0
    applied_keys = 0
    skipped_report: list[dict[str, Any]] = []
    report_by_source = {
        item["source"]: item["target"]
        for item in report
        if item["state"] == "FOUND"
    }
    for curve in data.get("curves") or []:
        keys = curve.get("keys") or []
        if not keys:
            continue
        for source_plug in curve.get("destinations") or []:
            target_plug = report_by_source.get(str(source_plug))
            if not target_plug:
                continue
            existing_sources = _non_animation_incoming_sources(cmds, target_plug)
            if existing_sources:
                skipped_report.append(
                    {
                        "source": str(source_plug),
                        "target": target_plug,
                        "state": "SKIPPED_EXISTING_CONNECTION",
                        "existing_sources": existing_sources,
                    }
                )
                continue
            if clear_existing:
                try:
                    cmds.cutKey(target_plug, clear=True)
                except RuntimeError:
                    pass
            for key in keys:
                if key.get("time") is None:
                    continue
                cmds.setKeyframe(target_plug, time=float(key["time"]), value=float(key.get("value", 0.0)))
                applied_keys += 1
            _apply_tangents(cmds, target_plug, keys, weighted_tangents=curve.get("weighted_tangents"))
            _apply_infinity(cmds, target_plug, curve.get("infinity") or {})
            applied_destinations += 1
    return {
        "applied_destinations": applied_destinations,
        "applied_keys": applied_keys,
        "applied_static_values": sum(
            item.get("state") == "APPLIED" for item in static_report
        ),
        "skipped_destinations": len(skipped_report),
        "missing_destinations": len(missing),
        "static_report": static_report,
        "skipped_report": skipped_report,
        "report": report,
    }


def collect_animation_curves_for_cast(
    *,
    cast_key: str,
    namespace: str,
    asset: str = "",
    controller_root: str = "allRigSet",
    source_workfile: str | Path = "",
) -> dict[str, Any]:
    cmds = _maya_cmds()
    animation_layers = _non_base_animation_layers(cmds)
    if animation_layers:
        raise RuntimeError(
            "Animation Layers are not allowed for Animation Curve publish. "
            "Merge them into BaseAnimation before publishing: "
            + ", ".join(animation_layers)
        )
    root_node = _resolve_controller_root(cmds, namespace, controller_root)
    if not root_node:
        raise RuntimeError(f"Controller root was not found: {namespace}:{controller_root}")
    members = _controller_members(cmds, root_node)
    controller_nodes = _contract_nodes(
        cmds,
        members,
        traverse_descendants=True,
        namespace=namespace,
    )
    curve_destinations = _anim_curve_destinations_from_members(
        cmds,
        controller_nodes,
        traverse_descendants=False,
        namespace=namespace,
        controller_nodes_only=True,
    )
    duplicate_destinations = _duplicate_destination_map(curve_destinations)
    if duplicate_destinations:
        details = ", ".join(
            f"{plug} ({len(curves)} curves)"
            for plug, curves in list(duplicate_destinations.items())[:5]
        )
        raise RuntimeError(
            "Animation curve export found ambiguous destination connections: "
            f"{details}"
        )
    static_values = _collect_static_values(
        cmds,
        controller_nodes,
        animated_destinations={
            destination
            for destinations in curve_destinations.values()
            for destination in destinations
        },
    )
    data = _collect_curves_from_nodes(
        cmds,
        curve_destinations,
        source_workfile=source_workfile,
        destination_overrides=curve_destinations,
    )
    data.update(
        {
            "cast_key": cast_key,
            "asset": asset,
            "namespace": namespace,
            "controller_root": root_node,
            "controller_count": len(
                {
                    destination.rsplit(".", 1)[0]
                    for destinations in curve_destinations.values()
                    for destination in destinations
                }
            ),
            "curve_contract": "direct_controller_curves/v1",
            "animation_layers": [],
            "controller_contract": controller_nodes,
            "static_values": static_values,
            "static_value_count": len(static_values),
        }
    )
    return data


def _collect_curves_from_nodes(
    cmds: Any,
    curve_nodes,
    *,
    source_workfile: str | Path = "",
    destination_overrides: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    curves = []
    for curve in sorted(set(curve_nodes or [])):
        if not cmds.objExists(curve) or cmds.nodeType(curve) not in ANIM_CURVE_TYPES:
            continue
        keys = _curve_keys(cmds, curve)
        if not keys:
            continue
        curves.append(
            {
                "curve": curve,
                "type": cmds.nodeType(curve),
                "destinations": sorted(
                    (destination_overrides or {}).get(curve)
                    or _curve_destinations(cmds, curve)
                ),
                "infinity": _curve_infinity(cmds, curve),
                "weighted_tangents": _curve_weighted_tangents(cmds, curve),
                "keys": keys,
            }
        )
    return {
        "publish_type": "animation",
        "subset": "curves",
        "source_workfile": str(source_workfile).replace("\\", "/") if source_workfile else "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "curve_count": len(curves),
        "curves": curves,
    }


def _resolve_controller_root(cmds: Any, namespace: str, controller_root: str) -> str:
    candidates = []
    if namespace:
        candidates.extend(
            [
                f"{namespace}:{controller_root}",
                f"{namespace}:*:{controller_root}",
            ]
        )
    candidates.append(controller_root)
    for pattern in candidates:
        matches = cmds.ls(pattern, long=True) or []
        if matches:
            return matches[0]
    return ""


def _controller_members(cmds: Any, root_node: str) -> list[str]:
    if cmds.nodeType(root_node) == "objectSet":
        return _long_names(cmds, cmds.sets(root_node, query=True) or [])
    members = [root_node]
    members.extend(cmds.listRelatives(root_node, allDescendents=True, fullPath=True) or [])
    return members


def _anim_curves_from_members(cmds: Any, members) -> list[str]:
    return sorted(_anim_curve_destinations_from_members(cmds, members))


def _anim_curve_destinations_from_members(
    cmds: Any,
    members,
    *,
    traverse_descendants: bool = False,
    namespace: str = "",
    controller_nodes_only: bool = False,
    animatable_dag_only: bool = False,
) -> dict[str, set[str]]:
    """Resolve controller curves through Maya's reference/edit connection graph.

    A referenced rig can serialize an animation connection as
    ``animCurve.output -> referenceNode.phl[index]``.  Asking only for direct
    animCurve connections on the controller therefore omits most channels.
    Querying history per controller plug preserves the real destination while
    still limiting export to members of the controller set.
    """

    destinations: dict[str, set[str]] = {}
    for member in _long_names(cmds, members or []):
        nodes = [member]
        if traverse_descendants:
            nodes.extend(_safe_descendents(cmds, member))
        for node in nodes:
            if namespace and not _node_belongs_to_namespace(node, namespace):
                continue
            if controller_nodes_only and not _is_controller_node(cmds, node):
                continue
            if animatable_dag_only and not _is_animatable_dag_node(cmds, node):
                continue
            for plug in _animated_candidate_plugs(cmds, node):
                curves = _upstream_anim_curves(cmds, plug)
                for curve in curves:
                    destinations.setdefault(curve, set()).add(plug)
    return destinations


def _non_base_animation_layers(cmds: Any) -> list[str]:
    try:
        layers = [str(layer) for layer in (cmds.ls(type="animLayer") or [])]
    except (RuntimeError, TypeError, ValueError):
        return []
    try:
        root = str(cmds.animLayer(query=True, root=True) or "")
    except (RuntimeError, TypeError, ValueError):
        root = ""
    base_layers = {name for name in (root, "BaseAnimation") if name}
    return sorted(layer for layer in layers if layer and layer not in base_layers)


def _is_controller_node(cmds: Any, node: str) -> bool:
    """Recognize animator-facing controls while excluding internal rig DAG nodes."""

    if _has_controller_name(node):
        return True
    leaf = str(node or "").rsplit("|", 1)[-1].rsplit(":", 1)[-1]
    if leaf.startswith(
        (
            "A_",
            "Adrv_",
            "C_",
            "J_",
            "N_",
            "Null_",
            "ObjectSpace_",
            "PMX_",
            "offset_",
            "rotationSpace_",
        )
    ):
        return False
    return _has_controller_shape(cmds, node)


def _has_controller_name(node: str) -> bool:
    leaf = str(node or "").rsplit("|", 1)[-1].rsplit(":", 1)[-1]
    return bool(re.match(r"^(?:CTL(?!DRV|NULL)|ctl)(?:_|[A-Z0-9])", leaf))


def _has_controller_shape(cmds: Any, node: str) -> bool:
    try:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    except (RuntimeError, TypeError, ValueError):
        shapes = []
    for shape in shapes:
        try:
            if cmds.nodeType(shape) == "nurbsCurve":
                return True
        except (RuntimeError, TypeError, ValueError):
            continue
    return False


def _is_animatable_dag_node(cmds: Any, node: str) -> bool:
    try:
        return cmds.nodeType(node) in {"transform", "joint"}
    except (RuntimeError, TypeError, ValueError):
        return False


def _contract_nodes(
    cmds: Any,
    members,
    *,
    traverse_descendants: bool,
    namespace: str,
) -> list[str]:
    nodes: set[str] = set()
    for member in _long_names(cmds, members or []):
        candidates = [(member, True)]
        if traverse_descendants:
            candidates.extend((node, False) for node in _safe_descendents(cmds, member))
        for node, is_member in candidates:
            if namespace and not _node_belongs_to_namespace(node, namespace):
                continue
            recognized = (
                _is_controller_node(cmds, node)
                if is_member
                else _has_controller_shape(cmds, node)
            )
            if _is_animatable_dag_node(cmds, node) and recognized:
                nodes.add(str(node))
    # Some production rigs omit valid CTL nodes from allRigSet entirely.
    # Namespace-scoped name discovery fills those holes without admitting
    # internal LocalSpace/Guide transforms merely because they have a curve
    # shape.
    try:
        namespace_transforms = cmds.ls(type="transform", long=True) or []
    except (RuntimeError, TypeError, ValueError):
        namespace_transforms = []
    for node in namespace_transforms:
        if (
            _node_belongs_to_namespace(str(node), namespace)
            and _has_controller_name(str(node))
            and _has_controller_shape(cmds, str(node))
        ):
            nodes.add(str(node))
    return sorted(nodes)


def _collect_static_values(
    cmds: Any,
    nodes: list[str],
    *,
    animated_destinations: set[str],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for node in nodes:
        for plug in _static_candidate_plugs(cmds, node):
            if plug in animated_destinations:
                continue
            try:
                if not cmds.getAttr(plug, settable=True):
                    continue
                value = cmds.getAttr(plug)
            except (RuntimeError, TypeError, ValueError):
                continue
            normalized = _json_scalar(value)
            if normalized is None:
                continue
            attribute = plug.rsplit(".", 1)[-1]
            try:
                defaults = cmds.attributeQuery(attribute, node=node, listDefault=True) or []
            except (RuntimeError, TypeError, ValueError):
                defaults = []
            if defaults and _static_values_equal(normalized, _json_scalar(defaults[0])):
                continue
            try:
                attribute_type = str(cmds.getAttr(plug, type=True) or "")
            except (RuntimeError, TypeError, ValueError):
                attribute_type = ""
            values.append(
                {
                    "destination": plug,
                    "value": normalized,
                    "type": attribute_type,
                }
            )
    return values


def _json_scalar(value: Any) -> bool | int | float | str | None:
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return _json_scalar(value[0])
    return None


def _static_values_equal(left: Any, right: Any, tolerance: float = 1.0e-8) -> bool:
    if isinstance(left, (int, float, bool)) and isinstance(right, (int, float, bool)):
        return abs(float(left) - float(right)) <= tolerance
    return left == right


def _apply_static_values(
    cmds: Any,
    static_values: list[dict[str, Any]],
    *,
    source_namespace: str,
    target_namespace: str | None,
    strict: bool,
) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    missing: list[str] = []
    for item in static_values:
        source = str(item.get("destination") or "")
        target = _remap_plug(
            source,
            source_namespace,
            target_namespace if target_namespace is not None else source_namespace,
        )
        target = _resolve_scene_plug(cmds, target)
        if not target or not cmds.objExists(target):
            report.append({"source": source, "target": target, "state": "MISSING"})
            missing.append(target or source)
            continue
        try:
            if item.get("type") == "string":
                cmds.setAttr(target, str(item.get("value") or ""), type="string")
            else:
                cmds.setAttr(target, item.get("value"))
            report.append({"source": source, "target": target, "state": "APPLIED"})
        except (RuntimeError, TypeError, ValueError) as exc:
            report.append(
                {
                    "source": source,
                    "target": target,
                    "state": "FAILED",
                    "error": str(exc),
                }
            )
            missing.append(target)
    if missing and strict:
        raise AnimationCurveApplyError(
            f"Static controller state apply failed: {len(missing)} destinations.",
            report=report,
        )
    return report


def _node_belongs_to_namespace(node: str, namespace: str) -> bool:
    """Return whether the DAG leaf belongs to namespace or one nested below it."""

    expected = str(namespace or "").strip(":")
    if not expected:
        return True
    leaf = str(node or "").rsplit("|", 1)[-1]
    if ":" not in leaf:
        return False
    node_namespace = leaf.rsplit(":", 1)[0].strip(":")
    return node_namespace == expected or node_namespace.startswith(f"{expected}:")


def _animated_candidate_plugs(cmds: Any, node: str) -> list[str]:
    attributes: set[str] = set()
    for kwargs in (
        {"keyable": True},
        {"channelBox": True},
        {"connectable": True, "scalar": True},
    ):
        try:
            attributes.update(cmds.listAttr(node, **kwargs) or [])
        except (RuntimeError, TypeError, ValueError):
            continue
    plugs = {f"{node}.{attribute}" for attribute in attributes}
    plugs.update(_incoming_destination_plugs(cmds, node))
    return sorted(plugs)


def _static_candidate_plugs(cmds: Any, node: str) -> list[str]:
    attributes: set[str] = set()
    for kwargs in ({"keyable": True}, {"channelBox": True}):
        try:
            attributes.update(cmds.listAttr(node, **kwargs) or [])
        except (RuntimeError, TypeError, ValueError):
            continue
    plugs = {f"{node}.{attribute}" for attribute in attributes}
    plugs.update(_incoming_destination_plugs(cmds, node))
    return sorted(plugs)


def _incoming_destination_plugs(cmds: Any, node: str) -> list[str]:
    """Return exact plugs on *node* that have incoming connections."""

    try:
        connections = cmds.listConnections(
            node,
            source=True,
            destination=False,
            plugs=True,
            connections=True,
            skipConversionNodes=False,
        ) or []
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return []
    node_leaf = str(node).rsplit("|", 1)[-1]
    destinations: set[str] = set()
    for value in connections:
        plug = str(value)
        plug_node, separator, attribute = plug.partition(".")
        if not separator:
            continue
        # Maya can return a short plug even when the queried controller is a
        # long DAG path. Normalize it back to the contract node so it is not
        # discarded and later remaps consistently during Apply.
        if plug_node == node or plug_node.rsplit("|", 1)[-1] == node_leaf:
            destinations.add(f"{node}.{attribute}")
    return sorted(destinations)


def _upstream_anim_curves(cmds: Any, plug: str) -> list[str]:
    """Resolve anim curves through exact incoming plug connections.

    ``listHistory(plug)`` is deliberately not used here. Maya promotes that
    query to node history for several referenced rigs, which makes every curve
    on a controller appear to drive every attribute on that controller.
    """

    curves: set[str] = set()
    try:
        direct = cmds.listConnections(
            plug,
            source=True,
            destination=False,
            type="animCurve",
            skipConversionNodes=True,
        ) or []
        curves.update(direct)
    except (RuntimeError, TypeError, ValueError):
        pass

    pending = [plug]
    visited: set[str] = set()
    while pending:
        current_plug = pending.pop()
        if current_plug in visited:
            continue
        visited.add(current_plug)
        for source_plug in _incoming_source_plugs(cmds, current_plug):
            source_node = source_plug.rsplit(".", 1)[0]
            try:
                source_type = cmds.nodeType(source_node)
            except (RuntimeError, TypeError, ValueError):
                continue
            if source_type in ANIM_CURVE_TYPES:
                curves.add(source_node)
            else:
                # Reference proxy attributes (for example ``refRN.phl[3]``)
                # have another exact incoming connection. Following that plug
                # preserves the destination channel without widening to all
                # history on the reference node.
                pending.append(source_plug)
    return sorted(curves)


def _incoming_source_plugs(cmds: Any, plug: str) -> list[str]:
    try:
        sources = cmds.listConnections(
            plug,
            source=True,
            destination=False,
            plugs=True,
            skipConversionNodes=False,
        ) or []
    except (RuntimeError, TypeError, ValueError):
        sources = []
    # Reference edits are not always reported by listConnections from the
    # referenced destination. connectionInfo resolves the exact source (often
    # a reference-node placeHolderList plug) without broadening to node history.
    try:
        source = cmds.connectionInfo(plug, sourceFromDestination=True)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        source = ""
    if source:
        sources.append(source)
    return sorted({str(source) for source in sources if "." in str(source)})


def _non_animation_incoming_sources(cmds: Any, plug: str) -> list[str]:
    try:
        sources = cmds.listConnections(
            plug,
            source=True,
            destination=False,
            plugs=True,
            skipConversionNodes=False,
        ) or []
    except (RuntimeError, TypeError, ValueError):
        return []
    blockers = []
    for source in sources:
        source_plug = str(source)
        source_node = source_plug.rsplit(".", 1)[0]
        try:
            source_type = cmds.nodeType(source_node)
        except (RuntimeError, TypeError, ValueError):
            source_type = ""
        if source_type not in ANIM_CURVE_TYPES:
            blockers.append(source_plug)
    return sorted(set(blockers))


def _duplicate_destination_map(
    curve_destinations: dict[str, set[str]],
) -> dict[str, list[str]]:
    destination_curves: dict[str, list[str]] = {}
    for curve, destinations in curve_destinations.items():
        for destination in destinations:
            destination_curves.setdefault(str(destination), []).append(str(curve))
    return {
        destination: sorted(set(curves))
        for destination, curves in destination_curves.items()
        if len(set(curves)) > 1
    }


def _duplicate_curve_destinations(
    curves: list[dict[str, Any]],
) -> dict[str, list[str]]:
    return _duplicate_destination_map(
        {
            str(curve.get("curve") or f"curve_{index}"): {
                str(destination)
                for destination in curve.get("destinations") or []
            }
            for index, curve in enumerate(curves)
        }
    )


def _remap_plug(source_plug: str, source_namespace: str, target_namespace: str | None) -> str:
    node, separator, attribute = source_plug.partition(".")
    if not separator:
        return source_plug
    if not target_namespace:
        return source_plug
    remapped_node = _remap_node_namespace(node, source_namespace, target_namespace)
    return f"{remapped_node}.{attribute}"


def _remap_node_namespace(node: str, source_namespace: str, target_namespace: str) -> str:
    parts = node.split("|")
    for index, part in enumerate(parts):
        if not part:
            continue
        if source_namespace and part.startswith(f"{source_namespace}:"):
            parts[index] = f"{target_namespace}:{part[len(source_namespace) + 1:]}"
        elif index == len(parts) - 1:
            if ":" in part:
                parts[index] = f"{target_namespace}:{part.split(':', 1)[1]}"
            else:
                parts[index] = f"{target_namespace}:{part}"
    return "|".join(parts)


def _resolve_scene_plug(cmds: Any, plug: str) -> str:
    """Resolve a published plug after a Build template adds parent groups.

    Animation Curve publishes retain the source scene's full DAG path. During
    construction a referenced Rig root may be parented below a template group
    such as ``assets_grp``. The namespace-relative DAG suffix remains stable,
    so accept it only when it identifies exactly one scene node.
    """

    if not plug or cmds.objExists(plug):
        return plug
    node, separator, attribute = plug.partition(".")
    if not separator or not node.startswith("|"):
        return plug
    leaf = node.rsplit("|", 1)[-1]
    try:
        matches = cmds.ls(leaf, long=True, objectsOnly=True) or []
    except (RuntimeError, TypeError, ValueError):
        return plug
    suffix_matches = []
    for match in matches:
        candidate_node = str(match)
        candidate_plug = f"{candidate_node}.{attribute}"
        if candidate_node.endswith(node) and cmds.objExists(candidate_plug):
            suffix_matches.append(candidate_plug)
    unique = sorted(set(suffix_matches))
    return unique[0] if len(unique) == 1 else plug


def _apply_tangents(cmds: Any, plug: str, keys: list[dict[str, Any]], *, weighted_tangents: Any = None) -> None:
    if weighted_tangents is not None:
        try:
            cmds.keyTangent(plug, edit=True, weightedTangents=bool(weighted_tangents))
        except RuntimeError:
            pass
    for key in keys:
        if key.get("time") is None:
            continue
        kwargs = {}
        if key.get("in_tangent"):
            kwargs["inTangentType"] = key["in_tangent"]
        if key.get("out_tangent"):
            kwargs["outTangentType"] = key["out_tangent"]
        for source_key, maya_key in (
            ("in_angle", "inAngle"),
            ("out_angle", "outAngle"),
            ("in_weight", "inWeight"),
            ("out_weight", "outWeight"),
        ):
            if key.get(source_key) is not None:
                kwargs[maya_key] = float(key[source_key])
        if key.get("tangent_lock") is not None:
            kwargs["lock"] = bool(key["tangent_lock"])
        if key.get("weight_lock") is not None:
            kwargs["weightLock"] = bool(key["weight_lock"])
        if not kwargs:
            continue
        try:
            cmds.keyTangent(plug, edit=True, time=(float(key["time"]), float(key["time"])), **kwargs)
        except RuntimeError:
            pass


def _apply_infinity(cmds: Any, plug: str, infinity: dict[str, Any]) -> None:
    curves = cmds.listConnections(plug, source=True, destination=False, type="animCurve") or []
    if not curves:
        return
    for curve in curves:
        _set_infinity_attr(cmds, curve, "preInfinity", infinity.get("pre"))
        _set_infinity_attr(cmds, curve, "postInfinity", infinity.get("post"))


def _long_names(cmds: Any, nodes) -> list[str]:
    result = []
    for node in nodes or []:
        matches = cmds.ls(node, long=True, objectsOnly=True) or []
        if matches:
            result.extend(matches)
        else:
            result.append(node)
    return sorted(set(result))


def _safe_descendents(cmds: Any, node: str) -> list[str]:
    try:
        return cmds.listRelatives(node, allDescendents=True, fullPath=True) or []
    except RuntimeError:
        return []


def _curve_destinations(cmds: Any, curve: str) -> list[str]:
    destinations = cmds.listConnections(
        curve,
        source=False,
        destination=True,
        plugs=True,
    ) or []
    return sorted(str(destination) for destination in destinations)


def _curve_infinity(cmds: Any, curve: str) -> dict[str, str]:
    result = {}
    pre = _query_infinity_attr(cmds, curve, "preInfinity")
    post = _query_infinity_attr(cmds, curve, "postInfinity")
    if pre:
        result["pre"] = pre
    if post:
        result["post"] = post
    return result


def _curve_weighted_tangents(cmds: Any, curve: str) -> bool:
    try:
        value = cmds.keyTangent(curve, query=True, weightedTangents=True)
    except RuntimeError:
        return False
    if isinstance(value, (list, tuple)):
        return bool(value[0]) if value else False
    return bool(value)


def _query_infinity_attr(cmds: Any, curve: str, attr: str) -> str:
    try:
        value = cmds.getAttr(f"{curve}.{attr}")
        enums = cmds.attributeQuery(attr, node=curve, listEnum=True) or []
    except RuntimeError:
        return ""
    names = enums[0].split(":") if enums else []
    index = int(value)
    if 0 <= index < len(names):
        return names[index]
    return str(index)


def _set_infinity_attr(cmds: Any, curve: str, attr: str, value: Any) -> None:
    if value in (None, ""):
        return
    try:
        enums = cmds.attributeQuery(attr, node=curve, listEnum=True) or []
        names = enums[0].split(":") if enums else []
        if isinstance(value, str) and not value.isdigit():
            if value not in names:
                return
            enum_value = names.index(value)
        else:
            enum_value = int(value)
        cmds.setAttr(f"{curve}.{attr}", enum_value)
    except (RuntimeError, ValueError):
        pass


def _curve_keys(cmds: Any, curve: str) -> list[dict[str, Any]]:
    times = cmds.keyframe(curve, query=True, timeChange=True) or []
    values = cmds.keyframe(curve, query=True, valueChange=True) or []
    in_tangents = cmds.keyTangent(curve, query=True, inTangentType=True) or []
    out_tangents = cmds.keyTangent(curve, query=True, outTangentType=True) or []
    in_angles = _query_key_tangent_array(cmds, curve, "inAngle")
    out_angles = _query_key_tangent_array(cmds, curve, "outAngle")
    in_weights = _query_key_tangent_array(cmds, curve, "inWeight")
    out_weights = _query_key_tangent_array(cmds, curve, "outWeight")
    tangent_locks = _query_key_tangent_array(cmds, curve, "lock")
    weight_locks = _query_key_tangent_array(cmds, curve, "weightLock")
    keys = []
    for index, time in enumerate(times):
        keys.append(
            {
                "time": float(time),
                "value": float(values[index]) if index < len(values) else 0.0,
                "in_tangent": str(in_tangents[index]) if index < len(in_tangents) else "",
                "out_tangent": str(out_tangents[index]) if index < len(out_tangents) else "",
                "in_angle": _indexed_float(in_angles, index),
                "out_angle": _indexed_float(out_angles, index),
                "in_weight": _indexed_float(in_weights, index),
                "out_weight": _indexed_float(out_weights, index),
                "tangent_lock": _indexed_bool(tangent_locks, index),
                "weight_lock": _indexed_bool(weight_locks, index),
            }
        )
    return keys


def _query_key_tangent_array(cmds: Any, curve: str, flag_name: str) -> list[Any]:
    try:
        return cmds.keyTangent(curve, query=True, **{flag_name: True}) or []
    except RuntimeError:
        return []


def _indexed_float(values: list[Any], index: int) -> float | None:
    if index >= len(values):
        return None
    try:
        return float(values[index])
    except (TypeError, ValueError):
        return None


def _indexed_bool(values: list[Any], index: int) -> bool | None:
    if index >= len(values):
        return None
    return bool(values[index])


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Animation curve export is available inside Maya.") from exc
    return cmds


def export_animation_geometry_cache(
    *,
    namespace: str,
    output_dir: str | Path,
    frame_range: tuple[int, int],
    skeleton_set: str = "skel_export_set",
    formats: tuple[str, ...] = ("usd",),
) -> dict[str, Any]:
    """Export independently versioned USD Skel animation or Alembic geometry."""

    cmds = _maya_cmds()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    roots = _cache_export_roots(cmds, namespace)
    if not roots:
        raise RuntimeError(
            f"No exportable geometry was found in {namespace}:cache_geo_set. "
            "The rig publish must provide this objectSet."
        )

    start, end = (int(frame_range[0]), int(frame_range[1]))
    if end < start:
        raise ValueError(f"Invalid animation cache frame range: {start}-{end}")

    requested = {str(value).strip().lower() for value in formats}
    unsupported = requested.difference({"usd", "abc"})
    if not requested or unsupported:
        raise ValueError(f"Unsupported animation cache formats: {sorted(unsupported or requested)}")

    previous_selection = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(roots, replace=True)
        configured_skeletons = _skeleton_export_members(cmds, namespace, skeleton_set)
        bound_skeletons = _skin_skeleton_roots(cmds, roots, namespace)
        # The explicit export set is the skeleton contract. Skin history may
        # also expose an unbound driver hierarchy (for example J_C_all), but
        # that hierarchy is only a motion source and must not become a second
        # USD Skeleton.
        skeleton_members = sorted(
            set(configured_skeletons or bound_skeletons),
            key=lambda value: (value.count("|"), value),
        )
        files: dict[str, str] = {}
        usd_kind = ""
        source_skeleton_set = ""
        skeleton_bindings: list[dict[str, str]] = []
        if "usd" in requested:
            _load_cache_plugin(cmds, "mayaUsdPlugin")
            usd_path = output / "animation.usd"
            if not skeleton_members:
                raise RuntimeError(
                    f"No skeleton roots were found in {namespace}:{skeleton_set}. "
                    "Animation USD publish requires a USD Skel skeleton contract."
                )
            _export_skel_animation_usd(cmds, usd_path, skeleton_members, (start, end))
            driver_motion_joints = _merge_driver_skeleton_motion(
                cmds,
                usd_path,
                skeleton_members,
                configured_skeletons,
                (start, end),
            )
            skeleton_bindings = _usd_skel_animation_bindings(usd_path)
            skeleton_bindings = _normalize_skel_animation_layer(
                usd_path,
                skeleton_bindings,
            )
            files["usd"] = usd_path.name
            usd_kind = "usd_skel_animation"
            source_skeleton_set = f"{namespace}:{skeleton_set}"
        else:
            driver_motion_joints = []
        if "abc" in requested:
            _load_cache_plugin(cmds, "AbcExport")
            abc_path = output / "animation.abc"
            _export_cache_alembic(cmds, abc_path, roots, (start, end))
            files["abc"] = abc_path.name
        geometry = _cache_geometry_metadata(cmds, roots)
        return {
            "files": files,
            "formats": sorted(requested),
            "usd_kind": usd_kind or "alembic_geometry_cache",
            "source_set": f"{namespace}:cache_geo_set",
            "source_skeleton_set": source_skeleton_set,
            "driver_motion_joints": driver_motion_joints,
            "source_skeleton_roots": skeleton_members,
            "skeleton_bindings": skeleton_bindings,
            "source_nodes": roots,
            "frame_range": [start, end],
            "geometry": geometry,
            "topology_signature": _cache_topology_signature(geometry),
        }
    finally:
        try:
            cmds.select(previous_selection, replace=True) if previous_selection else cmds.select(clear=True)
        except Exception:
            pass


def _load_cache_plugin(cmds: Any, name: str) -> None:
    if not cmds.pluginInfo(name, query=True, loaded=True):
        cmds.loadPlugin(name, quiet=True)


def _cache_export_roots(cmds: Any, namespace: str) -> list[str]:
    set_name = f"{str(namespace or '').strip()}:cache_geo_set"
    if not cmds.objExists(set_name):
        return []
    roots: list[str] = []
    for member in cmds.sets(set_name, query=True) or []:
        if not cmds.objExists(member):
            continue
        node_type = cmds.nodeType(member)
        if node_type == "mesh":
            roots.extend(cmds.listRelatives(member, parent=True, fullPath=True) or [])
        elif node_type == "transform" and _cache_mesh_shapes(cmds, member):
            roots.extend(cmds.ls(member, long=True) or [member])
    ordered = sorted(set(roots), key=lambda value: value.count("|"))
    return [
        node
        for index, node in enumerate(ordered)
        if not any(node.startswith(f"{parent}|") for parent in ordered[:index])
    ]


def _skeleton_export_members(cmds: Any, namespace: str, configured_name: str) -> list[str]:
    namespace = str(namespace or "").strip().rstrip(":")
    set_name = f"{namespace}:{configured_name}" if namespace else configured_name
    if not cmds.objExists(set_name) or cmds.nodeType(set_name) != "objectSet":
        return []
    result: list[str] = []
    pending = list(cmds.sets(set_name, query=True) or [])
    while pending:
        member = pending.pop(0)
        if not cmds.objExists(member):
            continue
        if cmds.nodeType(member) == "objectSet":
            pending.extend(cmds.sets(member, query=True) or [])
            continue
        if cmds.nodeType(member) == "joint":
            result.extend(cmds.ls(member, long=True) or [member])
            continue
        result.extend(cmds.listRelatives(member, allDescendents=True, type="joint", fullPath=True) or [])
    ordered = sorted(set(result), key=lambda value: (value.count("|"), value))
    selected = set(ordered)
    roots: list[str] = []
    for joint in ordered:
        parents = cmds.listRelatives(joint, parent=True, type="joint", fullPath=True) or []
        if parents and parents[0] in selected:
            continue
        roots.append(joint)
    return roots


def _skin_skeleton_roots(cmds: Any, geometry_roots: list[str], namespace: str) -> list[str]:
    """Find independent skeleton roots from the skinClusters driving cache geometry."""

    namespace = str(namespace or "").strip().rstrip(":")
    prefix = f"{namespace}:" if namespace else ""
    result: set[str] = set()
    for geometry_root in geometry_roots:
        for shape in _cache_mesh_shapes(cmds, geometry_root):
            history = cmds.listHistory(shape) or []
            for skin_cluster in cmds.ls(history, type="skinCluster") or []:
                influences = cmds.skinCluster(skin_cluster, query=True, influence=True) or []
                for influence in influences:
                    if not cmds.objExists(influence) or cmds.nodeType(influence) != "joint":
                        continue
                    skin_root = _top_joint_ancestor(cmds, influence, prefix)
                    if skin_root:
                        result.add(skin_root)
    return sorted(result, key=lambda value: (value.count("|"), value))


def _top_joint_ancestor(cmds: Any, node: str, namespace_prefix: str) -> str:
    """Walk through transform groups and return the highest namespaced joint ancestor."""

    current = (cmds.ls(node, long=True) or [node])[0]
    highest = current if cmds.nodeType(current) == "joint" else ""
    while True:
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        if not parents:
            break
        current = parents[0]
        leaf = current.rsplit("|", 1)[-1]
        if cmds.nodeType(current) == "joint" and (
            not namespace_prefix or leaf.startswith(namespace_prefix)
        ):
            highest = current
    return highest


def _export_skel_animation_usd(
    cmds: Any,
    path: Path,
    skeleton_members: list[str],
    frame_range: tuple[int, int],
) -> None:
    start, end = frame_range
    previous = cmds.ls(selection=True, long=True) or []
    try:
        exported_layers: list[Path] = []
        for skeleton_root in skeleton_members:
            if len(skeleton_members) == 1:
                export_path = path
            else:
                root_name = skeleton_root.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
                safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", root_name).strip("_") or "skeleton"
                export_path = path.with_name(f"{path.stem}.{safe_name}{path.suffix}")
            cmds.select(skeleton_root, replace=True, noExpand=True)
            cmds.mayaUSDExport(
                file=str(export_path).replace("\\", "/"),
                selection=True,
                frameRange=(start, end),
                frameStride=1.0,
                exportSkels="auto",
                exportSkin="none",
                exportBlendShapes=False,
                exportInstances=True,
                mergeTransformAndShape=True,
                stripNamespaces=True,
            )
            if not export_path.exists():
                raise RuntimeError(f"Maya USD Skel export did not create a file: {export_path}")
            exported_layers.append(export_path)

        if len(exported_layers) > 1:
            layer_entries = "\n".join(f"        @{layer.name}@," for layer in exported_layers)
            path.write_text(
                "\n".join(
                    [
                        "#usda 1.0",
                        "(",
                        f"    startTimeCode = {start}",
                        f"    endTimeCode = {end}",
                        "    subLayers = [",
                        layer_entries.rstrip(","),
                        "    ]",
                        ")",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            try:
                from pxr import Usd

                stage = Usd.Stage.Open(str(path))
                if stage is None:
                    raise RuntimeError(f"Could not open temporary animation layer stack: {path}")
                flattened = stage.Flatten()
                if not flattened.Export(str(path)):
                    raise RuntimeError(f"Could not flatten animation USD: {path}")
            finally:
                for layer in exported_layers:
                    if layer != path and layer.exists():
                        layer.unlink()
    except (TypeError, RuntimeError) as exc:
        raise RuntimeError(f"USD Skel animation export failed for {path.name}: {exc}") from exc
    finally:
        cmds.select(previous, replace=True) if previous else cmds.select(clear=True)
    if not path.exists():
        raise RuntimeError(f"Maya USD Skel export did not create a file: {path}")


def _merge_driver_skeleton_motion(
    cmds: Any,
    path: Path,
    bound_roots: list[str],
    configured_roots: list[str],
    frame_range: tuple[int, int],
) -> list[str]:
    """Fill static bound joints from an animated, unbound driver skeleton.

    Some production rigs keep global and pelvis motion on a control skeleton
    (for example ``J_C_all``), while the skinCluster uses another hierarchy
    (``IF_C_all``). Maya USD does not sample the unbound hierarchy, so copy its
    evaluated local transforms only into channels that are static in the bound
    SkelAnimation.
    """

    # A configured export set may contain both the bound IF hierarchy and its
    # unbound J driver hierarchy. The latter can still appear in the broad
    # Maya USD export, so membership in ``bound_roots`` alone is not enough to
    # identify it.
    driver_roots = [
        root
        for root in configured_roots
        if root.rsplit("|", 1)[-1].rsplit(":", 1)[-1].startswith("J_")
    ]
    if not driver_roots:
        driver_roots = [root for root in configured_roots if root not in set(bound_roots)]
    if not driver_roots:
        # The production export set intentionally contains only the bound IF
        # hierarchy.  Its evaluated motion can be driven by a sibling J
        # hierarchy, so resolve that sibling without requiring it in the set.
        for bound_root in bound_roots:
            long_root = (cmds.ls(bound_root, long=True) or [bound_root])[0]
            leaf = long_root.rsplit("|", 1)[-1]
            short_leaf = leaf.rsplit(":", 1)[-1]
            if not short_leaf.startswith("IF_"):
                continue
            namespace = leaf[: -len(short_leaf)]
            driver_leaf = f"{namespace}J_{short_leaf[3:]}"
            parent = long_root.rsplit("|", 1)[0]
            candidate = f"{parent}|{driver_leaf}"
            matches = cmds.ls(candidate, long=True, type="joint") or []
            if len(matches) == 1:
                driver_roots.append(matches[0])
    if not driver_roots:
        return []
    try:
        from maya.api import OpenMaya as om
        from pxr import Gf, Usd, UsdSkel, Vt
    except ImportError:
        return []

    stage = Usd.Stage.Open(str(path))
    if stage is None:
        return []
    start, end = frame_range
    frames = list(range(int(start), int(end) + 1))
    changed: list[str] = []

    driver_nodes: dict[str, str] = {}
    for root in driver_roots:
        for node in [root] + (cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []):
            relative = _joint_relative_path(node, root)
            driver_nodes.setdefault(_canonical_joint_path(relative), node)

    # Animation-only override layers contain a SkelAnimation below an ``over``
    # hierarchy but no Skeleton prim or animationSource relationship.  Work
    # from the animation prim itself so both full exports and override layers
    # receive the root-motion correction.
    animation_prims = [prim for prim in stage.TraverseAll() if prim.IsA(UsdSkel.Animation)]
    for animation_prim in animation_prims:
        parent_leaf = animation_prim.GetParent().GetName()
        if driver_roots and not parent_leaf.startswith("IF_"):
            continue
        animation = UsdSkel.Animation(animation_prim)
        joints = [str(value) for value in (animation.GetJointsAttr().Get() or [])]
        translations_attr = animation.GetTranslationsAttr()
        rotations_attr = animation.GetRotationsAttr()
        scales_attr = animation.GetScalesAttr()
        sample_times = sorted(
            set(translations_attr.GetTimeSamples())
            | set(rotations_attr.GetTimeSamples())
            | set(scales_attr.GetTimeSamples())
        )
        if not sample_times:
            continue

        translation_samples = {frame: list(translations_attr.Get(frame) or []) for frame in frames}
        rotation_samples = {frame: list(rotations_attr.Get(frame) or []) for frame in frames}
        scale_samples = {frame: list(scales_attr.Get(frame) or []) for frame in frames}
        replaced_branches: list[str] = []
        for index, joint in enumerate(joints):
            canonical_joint = _canonical_joint_path(joint)
            if any(
                canonical_joint == branch or canonical_joint.startswith(f"{branch}/")
                for branch in replaced_branches
            ):
                continue
            driver = driver_nodes.get(canonical_joint)
            if not driver:
                continue
            target_values = [
                translation_samples[frame][index]
                for frame in frames
                if index < len(translation_samples[frame])
            ]
            target_rotations = [
                rotation_samples[frame][index]
                for frame in frames
                if index < len(rotation_samples[frame])
            ]
            if not target_values or not target_rotations:
                continue
            target_static = all(_values_close(target_values[0], value) for value in target_values[1:]) and all(
                _values_close(target_rotations[0], value) for value in target_rotations[1:]
            )
            # C_top is the production root-motion channel.  Its bound IF
            # counterpart can contain harmless evaluation noise, so the J
            # driver remains authoritative even when it is not exactly static.
            is_root_motion = canonical_joint.rsplit("/", 1)[-1] == "C_top"
            # Other IF joints already contain the exported skin animation.
            # Sampling every static helper from the J hierarchy can encounter
            # singular rig matrices and is unnecessary for the root-motion
            # correction this pass owns.
            if not is_root_motion:
                continue
            driver_values = [_maya_channel_trs(cmds, om, driver, frame) for frame in frames]
            if all(
                _values_close(driver_values[0][0], value[0])
                and _values_close(driver_values[0][1], value[1])
                for value in driver_values[1:]
            ):
                continue
            for frame, (translation, rotation, scale) in zip(frames, driver_values):
                translation_samples[frame][index] = Gf.Vec3f(*translation)
                rotation_samples[frame][index] = Gf.Quatf(rotation[3], Gf.Vec3f(*rotation[:3]))
                scale_samples[frame][index] = Gf.Vec3h(*scale)
            changed.append(joint)
            replaced_branches.append(canonical_joint)

        for frame in frames:
            translations_attr.Set(Vt.Vec3fArray(translation_samples[frame]), frame)
            rotations_attr.Set(Vt.QuatfArray(rotation_samples[frame]), frame)
            scales_attr.Set(Vt.Vec3hArray(scale_samples[frame]), frame)

    stage.GetRootLayer().Save()
    return changed


def _joint_relative_path(node: str, root: str) -> str:
    node_parts = [part for part in node.split("|") if part]
    root_leaf = root.rsplit("|", 1)[-1]
    try:
        index = node_parts.index(root_leaf)
    except ValueError:
        index = max(0, len(node_parts) - 1)
    return "/".join(part.rsplit(":", 1)[-1] for part in node_parts[index:])


def _canonical_joint_path(value: str) -> str:
    result: list[str] = []
    for part in str(value).split("/"):
        leaf = part.rsplit(":", 1)[-1]
        if leaf.startswith("IFN_"):
            leaf = "N_" + leaf[4:]
        elif leaf.startswith(("IF_", "J_")):
            leaf = leaf.split("_", 1)[1]
        result.append(leaf)
    return "/".join(result)


def _maya_local_trs(cmds: Any, om: Any, node: str, frame: int) -> tuple[tuple[float, ...], ...]:
    raw = cmds.getAttr(f"{node}.matrix", time=frame)
    values = list(raw[0] if raw and isinstance(raw[0], (list, tuple)) else raw)
    transform = om.MTransformationMatrix(om.MMatrix(values))
    translation = transform.translation(om.MSpace.kTransform)
    rotation = transform.rotation(asQuaternion=True)
    scale = transform.scale(om.MSpace.kTransform)
    return (
        (translation.x, translation.y, translation.z),
        (rotation.x, rotation.y, rotation.z, rotation.w),
        tuple(scale),
    )


def _maya_channel_trs(
    cmds: Any,
    om: Any,
    node: str,
    frame: int,
) -> tuple[tuple[float, ...], ...]:
    """Read animated local channels without decomposing a singular rig matrix."""

    translation = cmds.getAttr(f"{node}.translate", time=frame)[0]
    rotation = cmds.getAttr(f"{node}.rotate", time=frame)[0]
    scale = cmds.getAttr(f"{node}.scale", time=frame)[0]
    order_index = int(cmds.getAttr(f"{node}.rotateOrder"))
    orders = (
        om.MEulerRotation.kXYZ,
        om.MEulerRotation.kYZX,
        om.MEulerRotation.kZXY,
        om.MEulerRotation.kXZY,
        om.MEulerRotation.kYXZ,
        om.MEulerRotation.kZYX,
    )
    euler = om.MEulerRotation(
        *(math.radians(float(value)) for value in rotation),
        orders[order_index],
    )
    quaternion = euler.asQuaternion()
    return (
        tuple(float(value) for value in translation),
        (quaternion.x, quaternion.y, quaternion.z, quaternion.w),
        tuple(float(value) for value in scale),
    )


def _maya_relative_trs(
    cmds: Any,
    om: Any,
    driver: str,
    skeleton_prim: Any,
    joint: str,
    frame: int,
) -> tuple[tuple[float, ...], ...]:
    """Evaluate driver world motion relative to the bound joint's Maya parent."""

    driver_matrix = om.MMatrix(_maya_matrix_values(cmds.getAttr(f"{driver}.worldMatrix[0]", time=frame)))
    joint_leaf = str(joint).split("/")[-1]
    driver_leaf = driver.rsplit("|", 1)[-1]
    namespace = driver_leaf.rsplit(":", 1)[0] if ":" in driver_leaf else ""
    target_leaf = f"IF_{joint_leaf.split('_', 1)[1]}" if joint_leaf.startswith("J_") else joint_leaf
    target_name = f"{namespace}:{target_leaf}" if namespace else target_leaf
    parents = cmds.listRelatives(target_name, parent=True, fullPath=True) or []
    if parents:
        parent_matrix = om.MMatrix(
            _maya_matrix_values(cmds.getAttr(f"{parents[0]}.worldMatrix[0]", time=frame))
        )
        matrix = driver_matrix * parent_matrix.inverse()
    else:
        matrix = driver_matrix
    transform = om.MTransformationMatrix(matrix)
    translation = transform.translation(om.MSpace.kTransform)
    rotation = transform.rotation(asQuaternion=True)
    scale = transform.scale(om.MSpace.kTransform)
    return (
        (translation.x, translation.y, translation.z),
        (rotation.x, rotation.y, rotation.z, rotation.w),
        tuple(scale),
    )


def _maya_matrix_values(raw: Any) -> list[float]:
    if raw and isinstance(raw[0], (list, tuple)):
        return list(raw[0])
    return list(raw)


def _values_close(left: Any, right: Any, tolerance: float = 1.0e-5) -> bool:
    try:
        return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right))
    except TypeError:
        return left == right


def _usd_skel_animation_bindings(path: Path) -> list[dict[str, str]]:
    """Map exported shot skeletons to the canonical asset skeleton paths."""

    try:
        from pxr import Sdf, Usd, UsdGeom, UsdSkel
    except ImportError as exc:
        raise RuntimeError("Maya USD Python bindings are required for USD Skel export.") from exc

    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise RuntimeError(f"Could not inspect USD Skel animation export: {path}")

    bindings: list[dict[str, str]] = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdSkel.Skeleton):
            continue
        relation = UsdSkel.BindingAPI(prim).GetAnimationSourceRel()
        targets = relation.GetTargets() if relation else []
        if not targets:
            continue
        source_skeleton = str(prim.GetPath())
        root_index = source_skeleton.find("/Root/")
        target_skeleton = source_skeleton[root_index:] if root_index >= 0 else source_skeleton
        bindings.append(
            {
                "source_skeleton": source_skeleton,
                "target_skeleton": target_skeleton,
                "animation_source": str(targets[0]),
            }
        )
    if not bindings:
        raise RuntimeError(f"USD Skel export contains no animation bindings: {path}")
    return bindings


def _normalize_skel_animation_layer(
    path: Path,
    bindings: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Write a pure animation layer at the asset's canonical skeleton paths."""

    try:
        from pxr import Sdf, Usd
    except ImportError as exc:
        raise RuntimeError("Maya USD Python bindings are required for USD Skel export.") from exc

    source_stage = Usd.Stage.Open(str(path))
    if source_stage is None:
        raise RuntimeError(f"Could not open USD Skel animation export: {path}")
    source_layer = source_stage.Flatten()
    normalized_path = path.with_name(f"{path.stem}.normalized.usda")
    if normalized_path.exists():
        normalized_path.unlink()
    output_layer = Sdf.Layer.CreateNew(str(normalized_path))
    output_layer.startTimeCode = source_stage.GetStartTimeCode()
    output_layer.endTimeCode = source_stage.GetEndTimeCode()
    output_layer.framesPerSecond = source_stage.GetFramesPerSecond()
    output_layer.timeCodesPerSecond = source_stage.GetTimeCodesPerSecond()

    normalized: list[dict[str, str]] = []
    for binding in bindings:
        source_animation = Sdf.Path(str(binding["animation_source"]))
        target_skeleton = Sdf.Path(str(binding["target_skeleton"]))
        target_animation = target_skeleton.AppendChild(source_animation.name)
        parent = target_animation.GetParentPath()
        ancestors: list[Sdf.Path] = []
        while parent != Sdf.Path.absoluteRootPath:
            ancestors.append(parent)
            parent = parent.GetParentPath()
        for ancestor in reversed(ancestors):
            Sdf.CreatePrimInLayer(output_layer, ancestor)
        if not Sdf.CopySpec(source_layer, source_animation, output_layer, target_animation):
            raise RuntimeError(
                f"Could not copy SkelAnimation {source_animation} to {target_animation}."
            )
        normalized.append(
            {
                "source_skeleton": str(binding["source_skeleton"]),
                "target_skeleton": str(target_skeleton),
                "animation_source": str(target_animation),
            }
        )

    output_layer.Save()
    del output_layer
    del source_layer
    del source_stage
    normalized_path.replace(path)
    cached_layer = Sdf.Layer.FindOrOpen(str(path))
    if cached_layer is not None:
        cached_layer.Reload()
    return normalized


def validate_skel_animation_compatibility(
    asset_path: str | Path,
    animation_path: str | Path,
    bindings: list[dict[str, str]],
) -> dict[str, Any]:
    """Validate that a pure animation layer can drive the published asset skeletons."""

    try:
        from pxr import Sdf, Usd, UsdGeom, UsdSkel
    except ImportError as exc:
        raise RuntimeError("USD Python bindings are required for USD Skel validation.") from exc

    asset_stage = Usd.Stage.Open(str(asset_path))
    if asset_stage is not None:
        asset_stage.Reload()
    animation_layer = Sdf.Layer.FindOrOpen(str(animation_path))
    if animation_layer is not None:
        animation_layer.Reload()
    composition_layer = Sdf.Layer.CreateAnonymous("skel_animation_validation.usda")
    composition_layer.subLayerPaths = [
        Path(animation_path).resolve().as_posix(),
        Path(asset_path).resolve().as_posix(),
    ]
    animation_stage = Usd.Stage.Open(composition_layer)
    if asset_stage is None or animation_stage is None:
        raise RuntimeError("Could not open the Asset USD or animation USD for validation.")

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    bound_meshes: dict[str, list[str]] = {}
    for prim in asset_stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        binding_api = UsdSkel.BindingAPI(prim)
        targets = list(binding_api.GetSkeletonRel().GetTargets())
        if not targets:
            inherited_skeleton = binding_api.GetInheritedSkeleton()
            if inherited_skeleton:
                targets = [inherited_skeleton.GetPrim().GetPath()]
        for target in targets:
            bound_meshes.setdefault(str(target), []).append(str(prim.GetPath()))
    for binding in bindings:
        skeleton_path = str(binding.get("target_skeleton") or "")
        animation_source = str(binding.get("animation_source") or "")
        expected_animation_source = f"{skeleton_path}/Animation"
        if animation_source != expected_animation_source:
            errors.append(
                "SkelAnimation is outside the canonical Asset Skeleton: "
                f"{animation_source} (expected {expected_animation_source})"
            )
            continue
        skeleton_prim = asset_stage.GetPrimAtPath(skeleton_path)
        animation_prim = animation_stage.GetPrimAtPath(animation_source)
        if not skeleton_prim or not skeleton_prim.IsA(UsdSkel.Skeleton):
            errors.append(f"Asset Skeleton was not found: {skeleton_path}")
            continue
        if not animation_prim or not animation_prim.IsA(UsdSkel.Animation):
            errors.append(f"Shot SkelAnimation was not found: {animation_source}")
            continue

        skeleton_meshes = bound_meshes.get(skeleton_path, [])
        if not skeleton_meshes:
            errors.append(
                f"Asset Skeleton has no bound skin meshes: {skeleton_path}. "
                "Repack the Asset Context with geometry, skeleton, and skin binding in one USD stage."
            )

        skeleton_joints = [
            str(value) for value in (UsdSkel.Skeleton(skeleton_prim).GetJointsAttr().Get() or [])
        ]
        animation = UsdSkel.Animation(animation_prim)
        animation_joints = [str(value) for value in (animation.GetJointsAttr().Get() or [])]
        sample_times: set[float] = set()
        for attribute in (
            animation.GetTranslationsAttr(),
            animation.GetRotationsAttr(),
            animation.GetScalesAttr(),
        ):
            sample_times.update(float(value) for value in attribute.GetTimeSamples())

        if skeleton_joints != animation_joints:
            errors.append(
                f"Joint order mismatch at {skeleton_path}: "
                f"asset={len(skeleton_joints)}, animation={len(animation_joints)}"
            )
        if not sample_times:
            errors.append(f"No animation time samples were found: {animation_source}")
        results.append(
            {
                "skeleton": skeleton_path,
                "animation_source": animation_source,
                "joint_count": len(skeleton_joints),
                "bound_mesh_count": len(skeleton_meshes),
                "sample_range": [min(sample_times), max(sample_times)] if sample_times else [],
            }
        )

    if not bindings:
        errors.append("No USD Skel bindings were supplied.")
    return {"ok": not errors, "errors": errors, "bindings": results}


def rebase_skel_animation_to_asset(
    asset_path: str | Path,
    animation_path: str | Path,
    bindings: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Move exported SkelAnimation prims onto matching Asset Skeleton paths."""

    try:
        from pxr import Sdf, Usd, UsdSkel
    except ImportError as exc:
        raise RuntimeError("USD Python bindings are required for USD Skel rebasing.") from exc

    asset_stage = Usd.Stage.Open(str(asset_path))
    animation_stage = Usd.Stage.Open(str(animation_path))
    if asset_stage is None or animation_stage is None:
        raise RuntimeError("Could not open the Asset USD or animation USD for rebasing.")
    asset_skeletons = [prim for prim in asset_stage.Traverse() if prim.IsA(UsdSkel.Skeleton)]
    if not asset_skeletons:
        raise RuntimeError(f"Asset USD contains no Skeleton prims: {asset_path}")

    source_layer = animation_stage.Flatten()
    output_path = Path(animation_path).with_name(f"{Path(animation_path).stem}.rebased.usda")
    if output_path.exists():
        output_path.unlink()
    output_layer = Sdf.Layer.CreateNew(str(output_path))
    output_layer.startTimeCode = animation_stage.GetStartTimeCode()
    output_layer.endTimeCode = animation_stage.GetEndTimeCode()
    output_layer.framesPerSecond = animation_stage.GetFramesPerSecond()
    output_layer.timeCodesPerSecond = animation_stage.GetTimeCodesPerSecond()

    rebased: list[dict[str, str]] = []
    used_targets: set[str] = set()
    unmatched_sources: list[str] = []
    for binding in bindings:
        source_animation = Sdf.Path(str(binding.get("animation_source") or ""))
        source_skeleton_path = str(binding.get("target_skeleton") or "")
        source_skeleton = animation_stage.GetPrimAtPath(source_skeleton_path)
        source_joints = (
            tuple(UsdSkel.Skeleton(source_skeleton).GetJointsAttr().Get() or [])
            if source_skeleton
            else ()
        )
        source_leaf = Sdf.Path(source_skeleton_path).name
        candidates = [prim for prim in asset_skeletons if prim.GetName() == source_leaf]
        if source_joints:
            joint_matches = [
                prim
                for prim in asset_skeletons
                if tuple(UsdSkel.Skeleton(prim).GetJointsAttr().Get() or []) == source_joints
            ]
            if joint_matches:
                candidates = joint_matches
        candidates = [prim for prim in candidates if str(prim.GetPath()) not in used_targets]
        if not candidates:
            # A Maya rig may contain helper or facial skeleton roots that do not
            # deform any geometry in the canonical Asset USD. They are not part
            # of the USD Skel contract and must not block the bound skeletons.
            unmatched_sources.append(source_skeleton_path)
            continue
        if len(candidates) != 1:
            raise RuntimeError(
                f"Could not uniquely match shot Skeleton {source_skeleton_path} "
                f"to the Asset USD (matches: {len(candidates)})."
            )

        target_skeleton = candidates[0].GetPath()
        target_animation = target_skeleton.AppendChild(source_animation.name or "Animation")
        parent = target_animation.GetParentPath()
        ancestors: list[Sdf.Path] = []
        while parent != Sdf.Path.absoluteRootPath:
            ancestors.append(parent)
            parent = parent.GetParentPath()
        for ancestor in reversed(ancestors):
            Sdf.CreatePrimInLayer(output_layer, ancestor)
        if not Sdf.CopySpec(source_layer, source_animation, output_layer, target_animation):
            raise RuntimeError(f"Could not rebase SkelAnimation {source_animation} to {target_animation}.")
        used_targets.add(str(target_skeleton))
        rebased.append(
            {
                "source_skeleton": str(binding.get("source_skeleton") or source_skeleton_path),
                "target_skeleton": str(target_skeleton),
                "animation_source": str(target_animation),
            }
        )

    if not rebased:
        detail = ", ".join(unmatched_sources) or "none"
        raise RuntimeError(
            "No shot Skeleton could be matched to a bound Skeleton in the Asset USD. "
            f"Unmatched shot Skeletons: {detail}"
        )

    output_layer.Save()
    del output_layer
    del source_layer
    del animation_stage
    output_path.replace(animation_path)
    cached_layer = Sdf.Layer.FindOrOpen(str(animation_path))
    if cached_layer is not None:
        cached_layer.Reload()
    return rebased


def _cache_mesh_shapes(cmds: Any, root: str) -> list[str]:
    candidates = cmds.listRelatives(root, shapes=True, fullPath=True, noIntermediate=True) or []
    candidates += cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
    result = []
    for node in candidates:
        if not cmds.objExists(node) or cmds.nodeType(node) != "mesh":
            continue
        try:
            if cmds.getAttr(f"{node}.intermediateObject"):
                continue
        except Exception:
            pass
        result.append(node)
    return sorted(set(result))


def _export_cache_usd(cmds: Any, path: Path, frame_range: tuple[int, int]) -> None:
    cmds.mayaUSDExport(
        file=str(path),
        selection=True,
        frameRange=frame_range,
        frameStride=1.0,
        mergeTransformAndShape=True,
        stripNamespaces=False,
        exportSkels="none",
        exportSkin="none",
        exportBlendShapes=False,
        shadingMode="none",
    )
    if not path.exists():
        raise RuntimeError(f"Maya USD export did not create a file: {path}")


def _export_cache_alembic(
    cmds: Any,
    path: Path,
    roots: list[str],
    frame_range: tuple[int, int],
) -> None:
    root_flags = " ".join(f'-root "{root}"' for root in roots)
    job = (
        f"-frameRange {frame_range[0]} {frame_range[1]} "
        "-uvWrite -writeColorSets -writeFaceSets -worldSpace "
        f'-writeVisibility -dataFormat ogawa {root_flags} -file "{path.as_posix()}"'
    )
    cmds.AbcExport(jobArg=job)
    if not path.exists():
        raise RuntimeError(f"Alembic export did not create a file: {path}")


def _cache_geometry_metadata(cmds: Any, roots: list[str]) -> list[dict[str, Any]]:
    rows = []
    for root in roots:
        for shape in _cache_mesh_shapes(cmds, root):
            rows.append(
                {
                    "name": shape.rsplit("|", 1)[-1],
                    "path": shape,
                    "vertex_count": int(cmds.polyEvaluate(shape, vertex=True) or 0),
                    "face_count": int(cmds.polyEvaluate(shape, face=True) or 0),
                }
            )
    return rows


def _cache_topology_signature(geometry: list[dict[str, Any]]) -> str:
    source = "\n".join(
        f"{row['name']}:{row['vertex_count']}:{row['face_count']}"
        for row in sorted(geometry, key=lambda item: item["path"])
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
