from __future__ import annotations

import hashlib
import json
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
) -> dict[str, Any]:
    data = read_animation_curve_json(path)
    return apply_animation_curve_data(data, namespace=namespace, clear_existing=clear_existing)


def apply_animation_curve_data(
    data: dict[str, Any],
    *,
    namespace: str | None = None,
    clear_existing: bool = True,
) -> dict[str, Any]:
    cmds = _maya_cmds()
    report = remap_animation_curve_destinations(data, namespace=namespace)
    missing = [item for item in report if item["state"] != "FOUND"]
    if missing:
        raise AnimationCurveApplyError(
            f"Animation curve destination remap failed: {len(missing)} missing destinations.",
            report=report,
        )

    applied_destinations = 0
    applied_keys = 0
    report_by_source = {item["source"]: item["target"] for item in report}
    for curve in data.get("curves") or []:
        keys = curve.get("keys") or []
        if not keys:
            continue
        for source_plug in curve.get("destinations") or []:
            target_plug = report_by_source.get(str(source_plug))
            if not target_plug:
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
    root_node = _resolve_controller_root(cmds, namespace, controller_root)
    if not root_node:
        raise RuntimeError(f"Controller root was not found: {namespace}:{controller_root}")
    members = _controller_members(cmds, root_node)
    curves = _anim_curves_from_members(cmds, members)
    data = _collect_curves_from_nodes(cmds, curves, source_workfile=source_workfile)
    data.update(
        {
            "cast_key": cast_key,
            "asset": asset,
            "namespace": namespace,
            "controller_root": root_node,
            "controller_count": len(members),
        }
    )
    return data


def _collect_curves_from_nodes(cmds: Any, curve_nodes, *, source_workfile: str | Path = "") -> dict[str, Any]:
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
                "destinations": _curve_destinations(cmds, curve),
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
    curves = set()
    for member in _long_names(cmds, members or []):
        nodes = [member]
        nodes.extend(_safe_descendents(cmds, member))
        for node in nodes:
            connections = cmds.listConnections(
                node,
                source=True,
                destination=False,
                type="animCurve",
            ) or []
            curves.update(connections)
    return sorted(curves)


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
    leaf = parts[-1]
    if source_namespace and leaf.startswith(f"{source_namespace}:"):
        leaf = f"{target_namespace}:{leaf[len(source_namespace) + 1:]}"
    elif ":" in leaf:
        leaf = f"{target_namespace}:{leaf.split(':', 1)[1]}"
    else:
        leaf = f"{target_namespace}:{leaf}"
    parts[-1] = leaf
    return "|".join(parts)


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
) -> dict[str, Any]:
    """Export evaluated rig geometry as USD point cache and Alembic."""

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

    previous_selection = cmds.ls(selection=True, long=True) or []
    try:
        _load_cache_plugin(cmds, "mayaUsdPlugin")
        _load_cache_plugin(cmds, "AbcExport")
        cmds.select(roots, replace=True)
        usd_path = output / "animation.usd"
        abc_path = output / "animation.abc"
        _export_cache_usd(cmds, usd_path, (start, end))
        _export_cache_alembic(cmds, abc_path, roots, (start, end))
        geometry = _cache_geometry_metadata(cmds, roots)
        return {
            "files": {"usd": usd_path.name, "abc": abc_path.name},
            "source_set": f"{namespace}:cache_geo_set",
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
