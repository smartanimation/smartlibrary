from __future__ import annotations

from typing import Iterable

from smartlib.dcc.maya.set_dress import Change, NodeState


ATTRIBUTES = (
    "translateX", "translateY", "translateZ",
    "rotateX", "rotateY", "rotateZ",
    "scaleX", "scaleY", "scaleZ", "visibility",
)


def capture_scene(selection_only: bool = True, cmds=None) -> list[NodeState]:
    """Capture MayaUSD prim transforms using stable UFE paths as node IDs."""
    cmds = cmds or _maya_cmds()
    stages = _selected_prims(cmds) if selection_only else _all_prims(cmds)
    states = []
    for ufe_path, stage, prim in stages:
        values = _prim_values(prim)
        if values:
            states.append(NodeState(ufe_path, ufe_path, values))
    return states


def prepare_recording(selection_only: bool = True, cmds=None) -> None:
    """Route viewport edits to non-destructive session layers."""
    cmds = cmds or _maya_cmds()
    rows = _selected_prims(cmds) if selection_only else _all_prims(cmds)
    seen = set()
    for _ufe_path, stage, _prim in rows:
        key = id(stage)
        if key not in seen:
            stage.SetEditTarget(stage.GetSessionLayer())
            seen.add(key)


def apply_changes(
    changes: Iterable[Change], *, use_after: bool = True, cmds=None
) -> list[str]:
    cmds = cmds or _maya_cmds()
    warnings = []
    for change in changes:
        try:
            stage, prim = _resolve_prim(change.node_id or change.node, cmds)
            if not prim or not prim.IsValid():
                warnings.append(f"Missing: {change.node}.{change.attribute}")
                continue
            with _edit_context(stage):
                _set_prim_value(
                    prim,
                    change.attribute,
                    change.after if use_after else change.before,
                )
        except Exception as exc:
            warnings.append(f"{change.node}.{change.attribute}: {exc}")
    return warnings


def apply_stack(layers, *, base=None, cmds=None) -> list[str]:
    from smartlib.dcc.maya.set_dress import composed_values

    warnings = restore_base(layers, base=base, cmds=cmds)
    warnings.extend(apply_changes(composed_values(layers).values(), cmds=cmds))
    return warnings


def restore_base(layers, *, base=None, cmds=None) -> list[str]:
    from smartlib.dcc.maya.set_dress import base_values

    if base:
        changes = (
            Change(state.node_id, state.node, attribute, value, value)
            for state in base
            for attribute, value in state.values.items()
        )
        return apply_changes(changes, cmds=cmds)
    return apply_changes(base_values(layers).values(), use_after=False, cmds=cmds)


def _prim_values(prim) -> dict[str, float | bool]:
    Usd, UsdGeom, _Gf = _pxr_modules()
    if not prim.IsA(UsdGeom.Xformable):
        return {}
    translate, rotate, scale, _pivot, _order = UsdGeom.XformCommonAPI(
        prim
    ).GetXformVectors(Usd.TimeCode.Default())
    visibility = UsdGeom.Imageable(prim).ComputeVisibility() != UsdGeom.Tokens.invisible
    return {
        "translateX": float(translate[0]),
        "translateY": float(translate[1]),
        "translateZ": float(translate[2]),
        "rotateX": float(rotate[0]),
        "rotateY": float(rotate[1]),
        "rotateZ": float(rotate[2]),
        "scaleX": float(scale[0]),
        "scaleY": float(scale[1]),
        "scaleZ": float(scale[2]),
        "visibility": visibility,
    }


def _set_prim_value(prim, attribute: str, value) -> None:
    Usd, UsdGeom, Gf = _pxr_modules()
    if attribute == "visibility":
        token = UsdGeom.Tokens.inherited if bool(value) else UsdGeom.Tokens.invisible
        UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(token)
        return
    common = UsdGeom.XformCommonAPI(prim)
    translate, rotate, scale, pivot, order = common.GetXformVectors(
        Usd.TimeCode.Default()
    )
    groups = {
        "translate": list(translate),
        "rotate": list(rotate),
        "scale": list(scale),
    }
    prefix = next((name for name in groups if attribute.startswith(name)), "")
    if not prefix or attribute[-1] not in "XYZ":
        raise ValueError(f"Unsupported USD attribute: {attribute}")
    groups[prefix]["XYZ".index(attribute[-1])] = float(value)
    authored = common.SetXformVectors(
        Gf.Vec3d(*groups["translate"]),
        Gf.Vec3f(*groups["rotate"]),
        Gf.Vec3f(*groups["scale"]),
        pivot,
        order,
        Usd.TimeCode.Default(),
    )
    if not authored:
        raise RuntimeError(f"Could not author a common xform stack on {prim.GetPath()}")


def _selected_prims(cmds):
    rows = []
    try:
        import ufe

        selected = [str(item.path()) for item in ufe.GlobalSelection.get()]
    except ImportError as exc:
        raise RuntimeError("MayaUSD UFE Python bindings are required.") from exc
    for ufe_path in selected:
        if "," not in ufe_path:
            continue
        stage, prim = _resolve_prim(ufe_path, cmds)
        for child in _prim_range(prim):
            rows.append((_join_ufe_path(ufe_path.split(",", 1)[0], child.GetPath()), stage, child))
    return _unique_rows(rows)


def _all_prims(cmds):
    rows = []
    for proxy in cmds.ls(type="mayaUsdProxyShape", long=True) or []:
        stage = _stage(str(proxy))
        if not stage:
            continue
        for prim in stage.Traverse():
            rows.append((_join_ufe_path(str(proxy), prim.GetPath()), stage, prim))
    return _unique_rows(rows)


def _resolve_prim(ufe_path: str, cmds):
    if "," not in ufe_path:
        raise ValueError(f"USD prim path must be a UFE path: {ufe_path}")
    proxy, prim_path = ufe_path.split(",", 1)
    stage = _stage(proxy)
    if not stage:
        raise RuntimeError(f"MayaUSD stage was not found: {proxy}")
    return stage, stage.GetPrimAtPath(prim_path)


def _stage(proxy_path: str):
    try:
        import mayaUsd.ufe
    except ImportError as exc:
        raise RuntimeError("mayaUsdPlugin and MayaUSD Python bindings are required.") from exc
    return mayaUsd.ufe.getStage(proxy_path)


def _edit_context(stage):
    Usd, _UsdGeom, _Gf = _pxr_modules()
    return Usd.EditContext(stage, stage.GetSessionLayer())


def _prim_range(prim):
    Usd, _UsdGeom, _Gf = _pxr_modules()
    return Usd.PrimRange(prim) if prim and prim.IsValid() else []


def _join_ufe_path(proxy: str, prim_path) -> str:
    return f"{proxy},{prim_path}"


def _unique_rows(rows):
    return list({path: (path, stage, prim) for path, stage, prim in rows}.values())


def _pxr_modules():
    try:
        from pxr import Gf, Usd, UsdGeom
    except ImportError as exc:
        raise RuntimeError("USD Python bindings are required.") from exc
    return Usd, UsdGeom, Gf


def _maya_cmds():
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("USD Set Dress is available inside Maya.") from exc
    if not cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
        cmds.loadPlugin("mayaUsdPlugin")
    return cmds
