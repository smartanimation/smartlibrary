from __future__ import annotations

import inspect

from smartlib.dcc.maya.animation_curves import (
    AnimationCurveApplyError,
    _anim_curve_destinations_from_members,
    _is_controller_node,
    _non_base_animation_layers,
    _collect_static_values,
    _contract_nodes,
    _apply_static_values,
    _apply_tangents,
    _authored_anim_curves,
    _remap_plug,
    apply_animation_curve_data,
    collect_animation_curves_for_cast,
    remap_animation_curve_destinations,
)


def test_animation_curve_publish_uses_all_rig_set_by_default() -> None:
    parameter = inspect.signature(collect_animation_curves_for_cast).parameters[
        "controller_root"
    ]

    assert parameter.default == "allRigSet"


class _ReferencedRigCmds:
    def ls(self, node, **_kwargs):
        return [node]

    def listRelatives(self, *_args, **_kwargs):
        return []

    def listAttr(self, _node, **kwargs):
        if kwargs.get("keyable"):
            return ["rotateX", "rollWeight"]
        return []

    def listConnections(self, plug, **_kwargs):
        if plug.endswith(".rollWeight"):
            if _kwargs.get("plugs"):
                return ["rollWeight_curve.output"]
            return ["rollWeight_curve"]
        if plug.endswith(".rotateX"):
            if _kwargs.get("plugs"):
                return ["DLIRN.phl[0]"]
            return []
        if plug == "DLIRN.phl[0]" and _kwargs.get("plugs"):
            return ["spine_rotateX_curve.output"]
        return []

    def listHistory(self, plug, **_kwargs):
        if plug.endswith(".rotateX"):
            return ["DLIRN", "spine_rotateX_curve"]
        return []

    def nodeType(self, node):
        return {
            "DLIRN": "reference",
            "spine_rotateX_curve": "animCurveTA",
            "rollWeight_curve": "animCurveTU",
        }.get(node, "transform")


def test_reference_proxy_curves_keep_controller_plug_as_destination() -> None:
    destinations = _anim_curve_destinations_from_members(
        _ReferencedRigCmds(),
        ["DLI:CTL_C_spineChest"],
    )

    assert destinations == {
        "spine_rotateX_curve": {"DLI:CTL_C_spineChest.rotateX"},
        "rollWeight_curve": {"DLI:CTL_C_spineChest.rollWeight"},
    }


class _HiddenReferenceProxyCmds(_ReferencedRigCmds):
    def listConnections(self, plug, **kwargs):
        if plug.rsplit("|", 1)[-1] == "DLI:CTL_C_spineChest" and kwargs.get("connections"):
            return [
                "DLI:CTL_C_spineChest.hiddenTwist",
                "DLIRN.phl[1]",
            ]
        if (
            plug.rsplit("|", 1)[-1] == "DLI:CTL_C_spineChest.hiddenTwist"
            and kwargs.get("plugs")
        ):
            return ["DLIRN.phl[1]"]
        if plug == "DLIRN.phl[1]" and kwargs.get("plugs"):
            return ["hidden_twist_curve.output"]
        return super().listConnections(plug, **kwargs)

    def nodeType(self, node):
        if node == "hidden_twist_curve":
            return "animCurveTU"
        return super().nodeType(node)


def test_non_channel_box_reference_proxy_curve_is_collected() -> None:
    destinations = _anim_curve_destinations_from_members(
        _HiddenReferenceProxyCmds(),
        ["DLI:CTL_C_spineChest"],
    )

    assert destinations["hidden_twist_curve"] == {
        "DLI:CTL_C_spineChest.hiddenTwist"
    }


def test_short_incoming_plug_is_normalized_to_long_controller_path() -> None:
    destinations = _anim_curve_destinations_from_members(
        _HiddenReferenceProxyCmds(),
        ["|DLI:rig|DLI:CTL_C_spineChest"],
    )

    assert destinations["hidden_twist_curve"] == {
        "|DLI:rig|DLI:CTL_C_spineChest.hiddenTwist"
    }


class _ConnectionInfoReferenceProxyCmds(_ReferencedRigCmds):
    def listConnections(self, plug, **kwargs):
        if plug.endswith(".rotateX"):
            return []
        return super().listConnections(plug, **kwargs)

    def connectionInfo(self, plug, **kwargs):
        if plug.endswith(".rotateX") and kwargs.get("sourceFromDestination"):
            return "DLIRN.phl[0]"
        return ""


def test_reference_edit_source_falls_back_to_connection_info() -> None:
    destinations = _anim_curve_destinations_from_members(
        _ConnectionInfoReferenceProxyCmds(),
        ["DLI:CTL_C_spineChest"],
    )

    assert destinations["spine_rotateX_curve"] == {
        "DLI:CTL_C_spineChest.rotateX"
    }


class _AuthoredCurveBoundaryCmds:
    def listConnections(self, plug, **kwargs):
        if plug == "DLI:A_L_forFingerB.rotateX" and kwargs.get("type") == "animCurve":
            return ["finger_curve"]
        if plug == "DLI:referenced_CTL.rotateX" and kwargs.get("plugs"):
            return ["DLIRN.phl[0]"]
        if plug == "DLIRN.phl[0]" and kwargs.get("type") == "animCurve":
            return ["referenced_curve"]
        if plug == "DLI:derivedJoint.rotateX" and kwargs.get("plugs"):
            return ["rigConstraint.outputX"]
        return []

    def connectionInfo(self, *_args, **_kwargs):
        return ""

    def nodeType(self, node):
        return {
            "finger_curve": "animCurveTA",
            "referenced_curve": "animCurveTA",
            "DLIRN": "reference",
            "rigConstraint": "orientConstraint",
        }.get(node, "transform")


def test_authored_curve_boundary_excludes_rig_evaluation_outputs() -> None:
    cmds = _AuthoredCurveBoundaryCmds()

    assert _authored_anim_curves(cmds, "DLI:A_L_forFingerB.rotateX") == ["finger_curve"]
    assert _authored_anim_curves(cmds, "DLI:referenced_CTL.rotateX") == ["referenced_curve"]
    assert _authored_anim_curves(cmds, "DLI:derivedJoint.rotateX") == []


class _SetMemberCmds(_ReferencedRigCmds):
    def listRelatives(self, node, **_kwargs):
        if node == "DLI:CTL_C_spineChest":
            return ["DLI:curveShapeA"]
        return []

    def listAttr(self, node, **kwargs):
        if node == "DLI:curveShapeA":
            raise ValueError(
                "No object matches name: DLI:curveShapeA.colorSetClamped"
            )
        return super().listAttr(node, **kwargs)


def test_explicit_members_do_not_walk_into_descendant_shapes_by_default() -> None:
    destinations = _anim_curve_destinations_from_members(
        _SetMemberCmds(),
        ["DLI:CTL_C_spineChest"],
    )

    assert destinations == {
        "spine_rotateX_curve": {"DLI:CTL_C_spineChest.rotateX"},
        "rollWeight_curve": {"DLI:CTL_C_spineChest.rollWeight"},
    }


def test_invalid_descendant_plug_does_not_abort_group_scan() -> None:
    destinations = _anim_curve_destinations_from_members(
        _SetMemberCmds(),
        ["DLI:CTL_C_spineChest"],
        traverse_descendants=True,
        namespace="DLI",
    )

    assert destinations == {
        "spine_rotateX_curve": {"DLI:CTL_C_spineChest.rotateX"},
        "rollWeight_curve": {"DLI:CTL_C_spineChest.rollWeight"},
    }


class _MixedNamespaceCmds(_ReferencedRigCmds):
    def listRelatives(self, _node, **_kwargs):
        return ["DLI:CTL_C_spineChest", "JIN:CTL_C_spineChest"]


def test_descendant_scan_uses_namespace_as_ownership_boundary() -> None:
    destinations = _anim_curve_destinations_from_members(
        _MixedNamespaceCmds(),
        ["DLI:controller_grp"],
        traverse_descendants=True,
        namespace="DLI",
    )

    assert destinations == {
        "spine_rotateX_curve": {
            "DLI:CTL_C_spineChest.rotateX",
            "DLI:controller_grp.rotateX",
        },
        "rollWeight_curve": {
            "DLI:CTL_C_spineChest.rollWeight",
            "DLI:controller_grp.rollWeight",
        },
    }


def test_apply_rejects_multiple_curves_for_one_destination(monkeypatch) -> None:
    monkeypatch.setattr(
        "smartlib.dcc.maya.animation_curves._maya_cmds",
        lambda: _ReferencedRigCmds(),
    )
    data = {
        "curves": [
            {"curve": "curve_a", "destinations": ["DLI:CTL.translateX"], "keys": []},
            {"curve": "curve_b", "destinations": ["DLI:CTL.translateX"], "keys": []},
        ]
    }

    try:
        apply_animation_curve_data(data)
    except AnimationCurveApplyError as exc:
        assert "ambiguous destinations" in str(exc)
        assert exc.report[0]["state"] == "AMBIGUOUS"
    else:
        raise AssertionError("Ambiguous animation curve data was applied")


class _AnimationLayerCmds:
    def ls(self, **kwargs):
        if kwargs.get("type") == "animLayer":
            return ["BaseAnimation", "BodyLayer", "FaceLayer"]
        return []

    def animLayer(self, **kwargs):
        if kwargs.get("query") and kwargs.get("root"):
            return "BaseAnimation"
        return ""


def test_non_base_animation_layers_are_publish_blockers() -> None:
    assert _non_base_animation_layers(_AnimationLayerCmds()) == [
        "BodyLayer",
        "FaceLayer",
    ]


class _ControllerRecognitionCmds:
    def listRelatives(self, node, **kwargs):
        if kwargs.get("shapes") and node == "DLI:handControl":
            return ["DLI:handControlShape"]
        return []

    def nodeType(self, node):
        return "nurbsCurve" if node.endswith("Shape") else "transform"


def test_controller_recognition_excludes_internal_rig_nodes() -> None:
    cmds = _ControllerRecognitionCmds()

    assert _is_controller_node(cmds, "DLI:CTL_C_spineChest")
    assert _is_controller_node(cmds, "DLI:ctlIK_L_hand")
    assert _is_controller_node(cmds, "DLI:handControl")
    assert not _is_controller_node(cmds, "DLI:A_C_spineChest")
    assert not _is_controller_node(cmds, "DLI:CTLDRV_C_spineChest")


class _ControllerContractCmds(_ControllerRecognitionCmds):
    def ls(self, *args, **_kwargs):
        return [args[0]] if args else []


def test_controller_contract_excludes_shaped_internal_finger_nodes() -> None:
    cmds = _ControllerContractCmds()

    nodes = _contract_nodes(
        cmds,
        ["DLI:A_L_forFingerB", "DLI:handControl"],
        traverse_descendants=False,
        namespace="DLI",
    )

    assert nodes == ["DLI:handControl"]


class _StaticValueCmds:
    def listAttr(self, _node, **kwargs):
        return ["ikBlend", "translateX"] if kwargs.get("keyable") else []

    def getAttr(self, plug, **kwargs):
        if kwargs.get("settable"):
            return True
        if kwargs.get("type"):
            return "double"
        return 1.0 if plug.endswith("ikBlend") else 0.0

    def attributeQuery(self, attribute, **_kwargs):
        return [0.0] if attribute in {"ikBlend", "translateX"} else []


class _HiddenStaticValueCmds(_StaticValueCmds):
    def listConnections(self, node, **kwargs):
        if node == "DLI:CTL_L_foot" and kwargs.get("connections"):
            return ["DLI:CTL_L_foot.hiddenState", "rigDriver.output"]
        return []

    def getAttr(self, plug, **kwargs):
        if plug.endswith("hiddenState"):
            if kwargs.get("settable"):
                return True
            if kwargs.get("type"):
                return "double"
            return 1.0
        return super().getAttr(plug, **kwargs)

    def attributeQuery(self, attribute, **kwargs):
        if attribute == "hiddenState":
            return [0.0]
        return super().attributeQuery(attribute, **kwargs)


def test_static_values_collect_only_non_default_unanimated_channels() -> None:
    values = _collect_static_values(
        _StaticValueCmds(),
        ["DLI:CTL_L_foot"],
        animated_destinations={"DLI:CTL_L_foot.translateX"},
    )

    assert values == [
        {
            "destination": "DLI:CTL_L_foot.ikBlend",
            "value": 1.0,
            "type": "double",
        }
    ]


def test_static_values_include_non_channel_box_incoming_plugs() -> None:
    values = _collect_static_values(
        _HiddenStaticValueCmds(),
        ["DLI:CTL_L_foot"],
        animated_destinations=set(),
    )

    assert {
        "destination": "DLI:CTL_L_foot.hiddenState",
        "value": 1.0,
        "type": "double",
    } in values


class _StaticApplyCmds:
    def __init__(self):
        self.values = {}

    def objExists(self, _plug):
        return True

    def setAttr(self, plug, value, **_kwargs):
        self.values[plug] = value

    def listRelatives(self, *_args, **_kwargs):
        return ["shape"]

    def nodeType(self, node):
        return "nurbsCurve" if node == "shape" else "transform"


def test_static_values_are_namespace_remapped_and_applied() -> None:
    cmds = _StaticApplyCmds()
    report = _apply_static_values(
        cmds,
        [{"destination": "DLI:CTL_L_foot.ikBlend", "value": 1.0, "type": "double"}],
        source_namespace="DLI",
        target_namespace="hero",
        strict=True,
    )

    assert cmds.values == {"hero:CTL_L_foot.ikBlend": 1.0}
    assert report[0]["state"] == "APPLIED"


class _InternalStaticApplyCmds(_StaticApplyCmds):
    def listRelatives(self, *_args, **_kwargs):
        return []


def test_static_values_do_not_overwrite_internal_rig_nodes() -> None:
    cmds = _InternalStaticApplyCmds()
    report = _apply_static_values(
        cmds,
        [{"destination": "DLI:A_L_forFingerB.rotateX", "value": 12.0, "type": "double"}],
        source_namespace="DLI",
        target_namespace="DLI",
        strict=True,
    )

    assert cmds.values == {}
    assert report[0]["state"] == "SKIPPED_NON_CONTROLLER"


def test_long_dag_destination_remaps_every_namespace_segment() -> None:
    source = (
        "|DLI:Root|DLI:ctlGrp|DLI:CTLNULL_C_spineChest|"
        "DLI:CTL_C_spineChest.rotateX"
    )

    assert _remap_plug(source, "DLI", "hero") == (
        "|hero:Root|hero:ctlGrp|hero:CTLNULL_C_spineChest|"
        "hero:CTL_C_spineChest.rotateX"
    )


class _ParentedRigCmds:
    target = (
        "|assets_grp|DLI:Root|DLI:ctlGrp|DLI:CTLNULL_C_spineChest|"
        "DLI:CTL_C_spineChest.rotateX"
    )

    def objExists(self, plug):
        return plug == self.target

    def ls(self, node, **_kwargs):
        if node == "DLI:CTL_C_spineChest":
            return [self.target.rsplit(".", 1)[0]]
        return []


def test_destination_resolves_when_build_template_parents_rig_root(monkeypatch) -> None:
    cmds = _ParentedRigCmds()
    monkeypatch.setattr(
        "smartlib.dcc.maya.animation_curves._maya_cmds",
        lambda: cmds,
    )
    source = (
        "|DLI:Root|DLI:ctlGrp|DLI:CTLNULL_C_spineChest|"
        "DLI:CTL_C_spineChest.rotateX"
    )

    report = remap_animation_curve_destinations(
        {
            "namespace": "DLI",
            "curves": [{"curve": "spine", "destinations": [source]}],
        },
        namespace="DLI",
    )

    assert report == [
        {
            "curve": "spine",
            "source": source,
            "target": cmds.target,
            "state": "FOUND",
        }
    ]


class _ExistingRigConnectionCmds:
    def __init__(self):
        self.keyed = []

    def objExists(self, _plug):
        return True

    def listConnections(self, plug, **kwargs):
        if plug.endswith(".visibility") and kwargs.get("plugs"):
            return ["DLI:Root.rigVisible"]
        return []

    def nodeType(self, node):
        return "transform" if node == "DLI:Root" else "animCurveTU"

    def setKeyframe(self, plug, **_kwargs):
        self.keyed.append(plug)


def test_apply_skips_destination_with_existing_rig_connection(monkeypatch) -> None:
    cmds = _ExistingRigConnectionCmds()
    monkeypatch.setattr(
        "smartlib.dcc.maya.animation_curves._maya_cmds",
        lambda: cmds,
    )
    data = {
        "namespace": "DLI",
        "curves": [
            {
                "curve": "visibility_curve",
                "destinations": ["DLI:CTL.visibility"],
                "keys": [{"time": 1.0, "value": 0.0}],
            }
        ],
    }

    result = apply_animation_curve_data(data)

    assert cmds.keyed == []
    assert result["skipped_destinations"] == 1
    assert result["skipped_report"][0]["state"] == "SKIPPED_EXISTING_CONNECTION"


class _TangentApplyCmds:
    def __init__(self):
        self.calls = []

    def keyTangent(self, plug, **kwargs):
        self.calls.append((plug, kwargs))


def test_computed_tangents_do_not_apply_derived_angles_or_weights() -> None:
    cmds = _TangentApplyCmds()

    _apply_tangents(
        cmds,
        "DLI:CTL.translateX",
        [
            {
                "time": 201.0,
                "in_tangent": "auto",
                "out_tangent": "auto",
                "in_angle": 12.0,
                "out_angle": 13.0,
                "in_weight": 2.0,
                "out_weight": 3.0,
                "tangent_lock": True,
                "weight_lock": False,
            }
        ],
        weighted_tangents=False,
    )

    all_kwargs = [kwargs for _plug, kwargs in cmds.calls]
    assert not any("inAngle" in kwargs or "outAngle" in kwargs for kwargs in all_kwargs)
    assert not any("inWeight" in kwargs or "outWeight" in kwargs for kwargs in all_kwargs)
    assert not any("weightLock" in kwargs for kwargs in all_kwargs)
    assert all_kwargs[-1]["lock"] is True


def test_fixed_tangent_values_are_applied_unlocked_then_relocked() -> None:
    cmds = _TangentApplyCmds()

    _apply_tangents(
        cmds,
        "DLI:CTL.translateX",
        [
            {
                "time": 201.0,
                "in_tangent": "fixed",
                "out_tangent": "fixed",
                "in_angle": 12.0,
                "out_angle": 13.0,
                "in_weight": 2.0,
                "out_weight": 3.0,
                "tangent_lock": True,
                "weight_lock": True,
            }
        ],
        weighted_tangents=True,
    )

    all_kwargs = [kwargs for _plug, kwargs in cmds.calls]
    assert {"edit": True, "time": (201.0, 201.0), "lock": False, "weightLock": False} in all_kwargs
    assert {
        "edit": True,
        "time": (201.0, 201.0),
        "inAngle": 12.0,
        "inWeight": 2.0,
        "outAngle": 13.0,
        "outWeight": 3.0,
    } in all_kwargs
    assert all_kwargs[-1]["lock"] is True
    assert all_kwargs[-1]["weightLock"] is True
