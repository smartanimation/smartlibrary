###############################################################################
# Smart Viewport Gate Guides
#
# Maya 2024+ Python plug-in.
# Draws text and composition guides inside the active camera resolution gate.
###############################################################################

import datetime
import getpass
import json
import os

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.api.OpenMayaRender as omr
import maya.api.OpenMayaUI as omui
import maya.cmds as cmds


_SCENE_CALLBACK_IDS = []
_STATE_NODE = "smartGateGuideState"
_STATE_ATTR = "guideStateJson"
_SCENE_LABEL = "untitled"


def maya_useNewAPI():
    pass


class SmartGateGuideCmd(om.MPxCommand):
    COMMAND_NAME = "SmartGateGuide"
    DEFAULT_TRANSFORM_NAME = "SmartGateGuide#"

    def doIt(self, args):
        shape = cmds.createNode(SmartGateGuideLocator.NAME, name="SmartGateGuideShape#")
        parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        node = shape
        if parent:
            node = cmds.rename(parent[0], self.DEFAULT_TRANSFORM_NAME)
            shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
            if shapes:
                cmds.rename(shapes[0], "{0}Shape".format(node))
        cmds.select(node, replace=True)
        self.setResult(node)

    @staticmethod
    def creator():
        return SmartGateGuideCmd()


class SmartGateGuideLocator(omui.MPxLocatorNode):
    NAME = "SmartViewportGateGuide"
    # Local development ID range. The former 0x0012B901 collided with
    # assumptions inside Maya 2024 XGen's guide baking callback.
    TYPE_ID = om.MTypeId(0x000FF7A1)
    DRAW_DB_CLASSIFICATION = "drawdb/geometry/smartViewportGateGuide"
    DRAW_REGISTRANT_ID = "SmartViewportGateGuideRegistrant"
    DEFAULT_CAMERA_NAMES = frozenset(
        ("persp", "top", "front", "side", "perspShape", "topShape", "frontShape", "sideShape")
    )

    TEXT_ATTRS = (
        ("topLeftText", "tlt", "{date}"),
        ("topCenterText", "tct", "{scene}"),
        ("topRightText", "trt", ""),
        ("bottomLeftText", "blt", "{camera} Lens:{focal_length} mm"),
        ("bottomCenterText", "bct", ""),
        ("bottomRightText", "brt", "{counter}{animTime}"),
    )

    def __init__(self):
        super(SmartGateGuideLocator, self).__init__()

    def postConstructor(self):
        node_fn = om.MFnDependencyNode(self.thisMObject())
        for plug_name in ("castsShadows", "receiveShadows", "motionBlur"):
            try:
                node_fn.findPlug(plug_name, False).setBool(False)
            except RuntimeError:
                pass

    def excludeAsLocator(self):
        return False

    @staticmethod
    def creator():
        return SmartGateGuideLocator()

    @classmethod
    def initialize(cls):
        numeric_attr = om.MFnNumericAttribute()
        typed_attr = om.MFnTypedAttribute()
        string_data = om.MFnStringData()

        camera_name = typed_attr.create(
            "camera", "cam", om.MFnData.kString, string_data.create("")
        )
        cls._set_attr_properties(typed_attr)
        cls.addAttribute(camera_name)

        for long_name, short_name, default in cls.TEXT_ATTRS:
            text_attr = typed_attr.create(
                long_name, short_name, om.MFnData.kString, string_data.create(default)
            )
            cls._set_attr_properties(typed_attr)
            cls.addAttribute(text_attr)

        cls._add_int_attr(numeric_attr, "textPadding", "tp", 12, 0, 100)
        cls._add_string_attr(typed_attr, string_data, "fontName", "fn", "Consolas")
        cls._add_color_attr(numeric_attr, "fontColor", "fc", (1.0, 1.0, 1.0))
        cls._add_float_attr(numeric_attr, "fontAlpha", "fa", 1.0, 0.0, 1.0)
        cls._add_float_attr(numeric_attr, "fontScale", "fs", 1.0, 0.25, 4.0)
        cls._add_int_attr(numeric_attr, "counterPadding", "cpd", 4, 1, 8)

        cls._add_bool_attr(numeric_attr, "showResolutionGate", "srg", True)
        cls._add_bool_attr(numeric_attr, "showCenterLine", "scl", True)
        cls._add_bool_attr(numeric_attr, "showRuleOfThirds", "srt", True)
        cls._add_bool_attr(numeric_attr, "showDiagonalCross", "sdc", False)

        cls._add_color_attr(numeric_attr, "gateColor", "gc", (1.0, 1.0, 1.0))
        cls._add_float_attr(numeric_attr, "gateAlpha", "ga", 0.55, 0.0, 1.0)
        cls._add_color_attr(numeric_attr, "centerColor", "cc", (1.0, 0.0, 0.0))
        cls._add_float_attr(numeric_attr, "centerAlpha", "ca", 0.85, 0.0, 1.0)
        cls._add_color_attr(numeric_attr, "thirdsColor", "tc", (1.0, 1.0, 0.0))
        cls._add_float_attr(numeric_attr, "thirdsAlpha", "ta", 0.85, 0.0, 1.0)
        cls._add_float_attr(numeric_attr, "lineWidth", "lw", 1.0, 0.5, 6.0)

    @classmethod
    def _set_attr_properties(cls, attr):
        attr.writable = True
        attr.storable = True
        if attr.type() == om.MFn.kNumericAttribute:
            attr.keyable = True

    @classmethod
    def _add_string_attr(cls, typed_attr, string_data, long_name, short_name, default):
        attr_obj = typed_attr.create(
            long_name, short_name, om.MFnData.kString, string_data.create(default)
        )
        cls._set_attr_properties(typed_attr)
        cls.addAttribute(attr_obj)

    @classmethod
    def _add_bool_attr(cls, numeric_attr, long_name, short_name, default):
        attr_obj = numeric_attr.create(
            long_name, short_name, om.MFnNumericData.kBoolean, default
        )
        cls._set_attr_properties(numeric_attr)
        cls.addAttribute(attr_obj)

    @classmethod
    def _add_int_attr(cls, numeric_attr, long_name, short_name, default, min_value, max_value):
        attr_obj = numeric_attr.create(
            long_name, short_name, om.MFnNumericData.kShort, default
        )
        cls._set_attr_properties(numeric_attr)
        numeric_attr.setMin(min_value)
        numeric_attr.setMax(max_value)
        cls.addAttribute(attr_obj)

    @classmethod
    def _add_float_attr(cls, numeric_attr, long_name, short_name, default, min_value, max_value):
        attr_obj = numeric_attr.create(
            long_name, short_name, om.MFnNumericData.kFloat, default
        )
        cls._set_attr_properties(numeric_attr)
        numeric_attr.setMin(min_value)
        numeric_attr.setMax(max_value)
        cls.addAttribute(attr_obj)

    @classmethod
    def _add_color_attr(cls, numeric_attr, long_name, short_name, default):
        attr_obj = numeric_attr.createColor(long_name, short_name)
        cls._set_attr_properties(numeric_attr)
        numeric_attr.default = default
        cls.addAttribute(attr_obj)


class SmartGateGuideData(om.MUserData):
    def __init__(self):
        super(SmartGateGuideData, self).__init__(False)
        self.text_values = []
        self.vp_width = 0
        self.vp_height = 0
        self.gate_width = 0.0
        self.gate_height = 0.0
        self.real_scale_value = 1.0


class SmartGateGuideDrawOverride(omr.MPxDrawOverride):
    def __init__(self, obj):
        super(SmartGateGuideDrawOverride, self).__init__(
            obj, SmartGateGuideDrawOverride.draw
        )

    def supportedDrawAPIs(self):
        return omr.MRenderer.kAllDevices

    def hasUIDrawables(self):
        return True

    def prepareForDraw(self, obj_path, camera_path, frame_context, old_data):
        data = old_data
        if not isinstance(data, SmartGateGuideData):
            data = SmartGateGuideData()

        if self._is_default_camera(camera_path):
            return None

        dag_fn = om.MFnDagNode(obj_path)

        camera_name = dag_fn.findPlug("camera", False).asString()
        if camera_name and self._camera_exists(camera_name):
            if not self._is_camera_match(camera_path, camera_name):
                return None

        vp_x, vp_y, data.vp_width, data.vp_height = frame_context.getViewportDimensions()
        if not (data.vp_width and data.vp_height):
            return None

        data.gate_width, data.gate_height = self._get_gate_size(
            camera_path, data.vp_width, data.vp_height
        )
        if not (data.gate_width and data.gate_height):
            return None

        data.text_padding = dag_fn.findPlug("textPadding", False).asInt()
        data.font_name = dag_fn.findPlug("fontName", False).asString()
        data.font_color = self._color_from_plugs(dag_fn, "fontColor", "fontAlpha")
        data.font_scale = dag_fn.findPlug("fontScale", False).asFloat()
        data.counter_padding = dag_fn.findPlug("counterPadding", False).asInt()

        data.show_gate = dag_fn.findPlug("showResolutionGate", False).asBool()
        data.show_center = dag_fn.findPlug("showCenterLine", False).asBool()
        data.show_thirds = dag_fn.findPlug("showRuleOfThirds", False).asBool()
        data.show_diagonal = dag_fn.findPlug("showDiagonalCross", False).asBool()
        data.gate_color = self._color_from_plugs(dag_fn, "gateColor", "gateAlpha")
        data.center_color = self._color_from_plugs(dag_fn, "centerColor", "centerAlpha")
        data.thirds_color = self._color_from_plugs(dag_fn, "thirdsColor", "thirdsAlpha")
        data.line_width = dag_fn.findPlug("lineWidth", False).asFloat()

        data.text_values = []
        for long_name, short_name, default in SmartGateGuideLocator.TEXT_ATTRS:
            text = dag_fn.findPlug(long_name, False).asString()
            data.text_values.append(self._parse_text(text, camera_path, data))

        # Draw overrides may run outside Maya's main UI thread. Calling
        # maya.cmds here can deadlock or crash during scene save callbacks.
        data.real_scale_value = 1.0

        return data

    def addUIDrawables(self, obj_path, draw_manager, frame_context, data):
        if not isinstance(data, SmartGateGuideData):
            return

        left, right, bottom, top = self._gate_rect(data)
        center_x = (left + right) * 0.5
        center_y = (bottom + top) * 0.5

        draw_manager.beginDrawable()

        if data.line_width:
            draw_manager.setLineWidth(data.line_width)

        if data.show_gate:
            draw_manager.setColor(data.gate_color)
            self._draw_line(draw_manager, left, top, right, top)
            self._draw_line(draw_manager, right, top, right, bottom)
            self._draw_line(draw_manager, right, bottom, left, bottom)
            self._draw_line(draw_manager, left, bottom, left, top)

        if data.show_thirds:
            draw_manager.setColor(data.thirds_color)
            third_w = data.gate_width / 3.0
            third_h = data.gate_height / 3.0
            self._draw_line(draw_manager, left + third_w, top, left + third_w, bottom)
            self._draw_line(draw_manager, left + third_w * 2.0, top, left + third_w * 2.0, bottom)
            self._draw_line(draw_manager, left, bottom + third_h, right, bottom + third_h)
            self._draw_line(draw_manager, left, bottom + third_h * 2.0, right, bottom + third_h * 2.0)

        if data.show_center:
            draw_manager.setColor(data.center_color)
            self._draw_line(draw_manager, center_x, top, center_x, bottom)
            self._draw_line(draw_manager, left, center_y, right, center_y)

        if data.show_diagonal:
            draw_manager.setColor(data.center_color)
            self._draw_line(draw_manager, left, top, right, bottom)
            self._draw_line(draw_manager, left, bottom, right, top)

        self._draw_text(draw_manager, data, left, right, bottom, top, center_x)

        draw_manager.endDrawable()

    def _draw_text(self, draw_manager, data, left, right, bottom, top, center_x):
        font_size = max(8, int(18 * data.font_scale / data.real_scale_value))
        padding = data.text_padding
        transparent = om.MColor((0.0, 0.0, 0.0, 0.0))

        draw_manager.setFontName(data.font_name)
        draw_manager.setFontSize(font_size)
        draw_manager.setColor(data.font_color)

        top_y = top - padding - font_size
        bottom_y = bottom + padding

        positions = (
            (om.MPoint(left + padding, top_y, 0.0), omr.MUIDrawManager.kLeft),
            (om.MPoint(center_x, top_y, 0.0), omr.MUIDrawManager.kCenter),
            (om.MPoint(right - padding, top_y, 0.0), omr.MUIDrawManager.kRight),
            (om.MPoint(left + padding, bottom_y, 0.0), omr.MUIDrawManager.kLeft),
            (om.MPoint(center_x, bottom_y, 0.0), omr.MUIDrawManager.kCenter),
            (om.MPoint(right - padding, bottom_y, 0.0), omr.MUIDrawManager.kRight),
        )

        for text, position_info in zip(data.text_values, positions):
            if not text:
                continue
            position, alignment = position_info
            draw_manager.text2d(
                position,
                text,
                alignment=alignment,
                backgroundColor=transparent,
            )

    @staticmethod
    def _draw_line(draw_manager, x1, y1, x2, y2):
        draw_manager.line2d(om.MPoint(x1, y1, 0.0), om.MPoint(x2, y2, 0.0))

    @staticmethod
    def _gate_rect(data):
        half_vp_width = data.vp_width * 0.5
        half_vp_height = data.vp_height * 0.5
        half_gate_width = data.gate_width * 0.5
        half_gate_height = data.gate_height * 0.5
        left = half_vp_width - half_gate_width
        right = half_vp_width + half_gate_width
        bottom = half_vp_height - half_gate_height
        top = half_vp_height + half_gate_height
        return left, right, bottom, top

    @staticmethod
    def _color_from_plugs(dag_fn, color_name, alpha_name):
        r = dag_fn.findPlug(color_name + "R", False).asFloat()
        g = dag_fn.findPlug(color_name + "G", False).asFloat()
        b = dag_fn.findPlug(color_name + "B", False).asFloat()
        a = dag_fn.findPlug(alpha_name, False).asFloat()
        return om.MColor((r, g, b, a))

    def _get_gate_size(self, camera_path, vp_width, vp_height):
        camera_fn = om.MFnCamera(camera_path)
        camera_aspect_ratio = camera_fn.aspectRatio()
        device_aspect_ratio = self._device_aspect_ratio(camera_path)
        vp_aspect_ratio = vp_width / float(vp_height)
        scale = 1.0

        if camera_fn.filmFit == om.MFnCamera.kHorizontalFilmFit:
            gate_width = vp_width / camera_fn.overscan
            gate_height = gate_width / device_aspect_ratio
        elif camera_fn.filmFit == om.MFnCamera.kVerticalFilmFit:
            gate_height = vp_height / camera_fn.overscan
            gate_width = gate_height * device_aspect_ratio
        elif camera_fn.filmFit == om.MFnCamera.kFillFilmFit:
            if vp_aspect_ratio < camera_aspect_ratio:
                if camera_aspect_ratio < device_aspect_ratio:
                    scale = camera_aspect_ratio / vp_aspect_ratio
                else:
                    scale = device_aspect_ratio / vp_aspect_ratio
            elif camera_aspect_ratio > device_aspect_ratio:
                scale = device_aspect_ratio / camera_aspect_ratio

            gate_width = vp_width / camera_fn.overscan * scale
            gate_height = gate_width / device_aspect_ratio
        elif camera_fn.filmFit == om.MFnCamera.kOverscanFilmFit:
            if vp_aspect_ratio < camera_aspect_ratio:
                if camera_aspect_ratio < device_aspect_ratio:
                    scale = camera_aspect_ratio / vp_aspect_ratio
                else:
                    scale = device_aspect_ratio / vp_aspect_ratio
            elif camera_aspect_ratio > device_aspect_ratio:
                scale = device_aspect_ratio / camera_aspect_ratio

            gate_height = vp_height / camera_fn.overscan / scale
            gate_width = gate_height * device_aspect_ratio
        else:
            om.MGlobal.displayError("[SmartViewportGateGuide] Unsupported camera film fit.")
            return None, None

        return gate_width, gate_height

    def _parse_text(self, text, camera_path, data):
        source_path = self._primary_camera_path(camera_path)
        current_time = oma.MAnimControl.currentTime().value
        current_frame = int(round(current_time))
        frame_start = int(round(oma.MAnimControl.minTime().value))
        frame_end = int(round(oma.MAnimControl.maxTime().value))

        replacements = {
            "{counter}": str(current_frame).zfill(data.counter_padding),
            "{animTime}": self._format_anim_time(current_frame),
            "{scene}": _SCENE_LABEL,
            "{camera}": self._camera_transform_name(source_path),
            "{camera_clean}": self._camera_transform_name(source_path, remove_namespace=True),
            "{focal_length}": str(int(round(om.MFnCamera(source_path).focalLength))),
            "{output_camera}": self._camera_transform_name(camera_path),
            "{output_focal_length}": str(int(round(om.MFnCamera(camera_path).focalLength))),
            "{frame_start}": str(frame_start),
            "{frame_end}": str(frame_end),
            "{total_frames}": str(frame_end - frame_start + 1),
            "{username}": getpass.getuser(),
            "{date}": datetime.date.today().strftime("%Y/%m/%d"),
        }

        parsed = text
        for token, value in replacements.items():
            parsed = parsed.replace(token, value)
        return parsed

    @staticmethod
    def _device_aspect_ratio(camera_path=None):
        # Managed output cameras have their own canvas; do not draw their
        # Burn-in against a different layer's global resolution gate.
        if camera_path is not None:
            try:
                transform_fn = om.MFnDependencyNode(camera_path.transform())
                if (transform_fn.hasAttribute("smartCameraOutputSchema")
                        and transform_fn.findPlug("smartCameraOutputSchema", False).asString() == "smartpipeline.camera_output.v1"):
                    spec = json.loads(transform_fn.findPlug("smartCameraOutputSpec", False).asString())
                    width, height = float(spec["width"]), float(spec["height"])
                    if width > 0 and height > 0:
                        return width / height
            except (RuntimeError, ValueError, KeyError, TypeError):
                pass
        try:
            selection = om.MSelectionList()
            selection.add("defaultResolution")
            node_fn = om.MFnDependencyNode(selection.getDependNode(0))
            value = node_fn.findPlug("deviceAspectRatio", False).asFloat()
            return value if value > 0.0 else 1.0
        except RuntimeError:
            return 1.0

    @staticmethod
    def _format_anim_time(frame):
        seconds = frame // 24
        remainder = frame % 24
        return "({0:02d} + {1:02d})".format(seconds, remainder)

    def _camera_exists(self, name):
        dg_iter = om.MItDependencyNodes(om.MFn.kCamera)
        while not dg_iter.isDone():
            if dg_iter.thisNode().hasFn(om.MFn.kDagNode):
                camera_path = om.MDagPath.getAPathTo(dg_iter.thisNode())
                if self._is_camera_match(camera_path, name):
                    return True
            dg_iter.next()
        return False

    def _is_camera_match(self, camera_path, name):
        source_path = self._primary_camera_path(camera_path)
        return (
            self._camera_transform_name(camera_path) == name
            or self._camera_shape_name(camera_path) == name
            or self._camera_transform_name(source_path) == name
            or self._camera_shape_name(source_path) == name
        )

    @staticmethod
    def _primary_camera_path(camera_path):
        """Only managed output cameras redirect creative Burn-in tokens."""
        try:
            transform_fn = om.MFnDependencyNode(camera_path.transform())
            if not transform_fn.hasAttribute("smartCameraOutputSchema"):
                return camera_path
            if transform_fn.findPlug("smartCameraOutputSchema", False).asString() != "smartpipeline.camera_output.v1":
                return camera_path
            sources = transform_fn.findPlug("smartPrimaryCamera", False).connectedTo(True, False)
            if len(sources) != 1:
                return camera_path
            source_path = om.MDagPath.getAPathTo(sources[0].node())
            source_path.extendToShape()
            if source_path.node().hasFn(om.MFn.kCamera):
                return source_path
        except RuntimeError:
            pass
        return camera_path

    def _is_default_camera(self, camera_path):
        return (
            self._camera_transform_name(camera_path)
            in SmartGateGuideLocator.DEFAULT_CAMERA_NAMES
            or self._camera_shape_name(camera_path)
            in SmartGateGuideLocator.DEFAULT_CAMERA_NAMES
        )

    @staticmethod
    def _camera_transform_name(camera_path, remove_namespace=False):
        camera_transform = camera_path.transform()
        if not camera_transform:
            return ""
        full_name = om.MFnTransform(camera_transform).name()
        if remove_namespace:
            return full_name.split(":")[-1]
        return full_name

    @staticmethod
    def _camera_shape_name(camera_path):
        camera_shape = camera_path.node()
        if not camera_shape:
            return ""
        return om.MFnCamera(camera_shape).name()

    @staticmethod
    def creator(obj):
        return SmartGateGuideDrawOverride(obj)

    @staticmethod
    def draw(context, data):
        return


def _guide_shapes():
    return cmds.ls(type=SmartGateGuideLocator.NAME, long=True) or []


def _capture_guide_state():
    states = []
    string_attrs = ["camera"] + [item[0] for item in SmartGateGuideLocator.TEXT_ATTRS] + ["fontName"]
    color_attrs = ["fontColor", "gateColor", "centerColor", "thirdsColor"]
    scalar_attrs = [
        "textPadding",
        "fontAlpha",
        "fontScale",
        "counterPadding",
        "showResolutionGate",
        "showCenterLine",
        "showRuleOfThirds",
        "showDiagonalCross",
        "gateAlpha",
        "centerAlpha",
        "thirdsAlpha",
        "lineWidth",
    ]
    for shape in _guide_shapes():
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if not parents:
            continue
        transform = parents[0]
        values = {}
        for name in string_attrs + scalar_attrs:
            plug = f"{shape}.{name}"
            if cmds.objExists(plug):
                values[name] = cmds.getAttr(plug)
        for name in color_attrs:
            plug = f"{shape}.{name}"
            if cmds.objExists(plug):
                value = cmds.getAttr(plug)
                values[name] = list(value[0]) if value else [1.0, 1.0, 1.0]
        states.append(
            {
                "name": transform.rsplit("|", 1)[-1],
                "matrix": cmds.xform(transform, query=True, worldSpace=True, matrix=True),
                "values": values,
            }
        )
    return states


def _write_guide_state(states):
    if not cmds.objExists(_STATE_NODE):
        cmds.createNode("network", name=_STATE_NODE)
    plug = f"{_STATE_NODE}.{_STATE_ATTR}"
    if not cmds.objExists(plug):
        cmds.addAttr(_STATE_NODE, longName=_STATE_ATTR, dataType="string")
    cmds.setAttr(plug, json.dumps(states, ensure_ascii=True), type="string")


def _read_guide_state():
    plug = f"{_STATE_NODE}.{_STATE_ATTR}"
    if not cmds.objExists(plug):
        return []
    try:
        value = cmds.getAttr(plug) or "[]"
        data = json.loads(value)
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def _remove_runtime_guides():
    transforms = []
    for shape in _guide_shapes():
        transforms.extend(cmds.listRelatives(shape, parent=True, fullPath=True) or [])
    if transforms:
        cmds.delete(sorted(set(transforms), key=len, reverse=True))


def _restore_runtime_guides():
    if _guide_shapes():
        return
    states = _read_guide_state()
    for state in states:
        shape = cmds.createNode(SmartGateGuideLocator.NAME, name="SmartGateGuideShape#")
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if not parents:
            continue
        transform = parents[0]
        requested_name = str(state.get("name") or "SmartGateGuide#")
        if not cmds.objExists(requested_name):
            transform = cmds.rename(transform, requested_name)
        matrix = state.get("matrix") or []
        if len(matrix) == 16:
            cmds.xform(transform, worldSpace=True, matrix=matrix)
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
        if not shapes:
            continue
        shape = shapes[0]
        for name, value in dict(state.get("values") or {}).items():
            plug = f"{shape}.{name}"
            if not cmds.objExists(plug):
                continue
            try:
                if isinstance(value, str):
                    cmds.setAttr(plug, value, type="string")
                elif isinstance(value, list) and len(value) == 3:
                    cmds.setAttr(plug, *value, type="double3")
                else:
                    cmds.setAttr(plug, value)
            except (RuntimeError, TypeError):
                continue


def _before_save_check(_client_data):
    _update_scene_label()
    states = _capture_guide_state()
    _write_guide_state(states)
    _remove_runtime_guides()
    return True


def _after_save(_client_data):
    _update_scene_label()
    _restore_runtime_guides()
    cmds.file(modified=False)


def _after_open(_client_data):
    _update_scene_label()
    cmds.evalDeferred(_restore_runtime_guides)


def _update_scene_label():
    global _SCENE_LABEL
    scene_path = cmds.file(query=True, sceneName=True) or ""
    scene_name = os.path.basename(scene_path)
    _SCENE_LABEL = os.path.splitext(scene_name)[0] if scene_name else "untitled"


def _register_scene_callbacks():
    _update_scene_label()
    cmds.evalDeferred(_restore_runtime_guides)
    _SCENE_CALLBACK_IDS.append(
        om.MSceneMessage.addCheckCallback(om.MSceneMessage.kBeforeSaveCheck, _before_save_check)
    )
    _SCENE_CALLBACK_IDS.append(
        om.MSceneMessage.addCallback(om.MSceneMessage.kAfterSave, _after_save)
    )
    _SCENE_CALLBACK_IDS.append(
        om.MSceneMessage.addCallback(om.MSceneMessage.kAfterOpen, _after_open)
    )


def _remove_scene_callbacks():
    while _SCENE_CALLBACK_IDS:
        callback_id = _SCENE_CALLBACK_IDS.pop()
        try:
            om.MMessage.removeCallback(callback_id)
        except RuntimeError:
            pass


def initializePlugin(obj):
    plugin_fn = om.MFnPlugin(obj, "SmartLibrary", "1.0.0", "2026")

    try:
        plugin_fn.registerCommand(
            SmartGateGuideCmd.COMMAND_NAME,
            SmartGateGuideCmd.creator,
        )
    except Exception:
        om.MGlobal.displayError(
            "Failed to register command: {0}".format(SmartGateGuideCmd.COMMAND_NAME)
        )
        raise

    try:
        plugin_fn.registerNode(
            SmartGateGuideLocator.NAME,
            SmartGateGuideLocator.TYPE_ID,
            SmartGateGuideLocator.creator,
            SmartGateGuideLocator.initialize,
            om.MPxNode.kLocatorNode,
            SmartGateGuideLocator.DRAW_DB_CLASSIFICATION,
        )
    except Exception:
        om.MGlobal.displayError(
            "Failed to register node: {0}".format(SmartGateGuideLocator.NAME)
        )
        raise

    try:
        omr.MDrawRegistry.registerDrawOverrideCreator(
            SmartGateGuideLocator.DRAW_DB_CLASSIFICATION,
            SmartGateGuideLocator.DRAW_REGISTRANT_ID,
            SmartGateGuideDrawOverride.creator,
        )
    except Exception:
        om.MGlobal.displayError("Failed to register SmartViewportGateGuide draw override.")
        raise

    _register_scene_callbacks()


def uninitializePlugin(obj):
    plugin_fn = om.MFnPlugin(obj)
    _remove_scene_callbacks()

    try:
        omr.MDrawRegistry.deregisterDrawOverrideCreator(
            SmartGateGuideLocator.DRAW_DB_CLASSIFICATION,
            SmartGateGuideLocator.DRAW_REGISTRANT_ID,
        )
    except Exception:
        om.MGlobal.displayError("Failed to deregister SmartViewportGateGuide draw override.")

    try:
        plugin_fn.deregisterNode(SmartGateGuideLocator.TYPE_ID)
    except Exception:
        om.MGlobal.displayError(
            "Failed to deregister node: {0}".format(SmartGateGuideLocator.NAME)
        )

    try:
        plugin_fn.deregisterCommand(SmartGateGuideCmd.COMMAND_NAME)
    except Exception:
        om.MGlobal.displayError(
            "Failed to deregister command: {0}".format(SmartGateGuideCmd.COMMAND_NAME)
        )
