###############################################################################
# Smart Viewport Gate Guides
#
# Maya 2026 Python plug-in.
# Draws text and composition guides inside the active camera resolution gate.
###############################################################################

import datetime
import getpass
import os

import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr
import maya.api.OpenMayaUI as omui
import maya.cmds as cmds


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
    TYPE_ID = om.MTypeId(0x0012B901)
    DRAW_DB_CLASSIFICATION = "drawdb/geometry/smartViewportGateGuide"
    DRAW_REGISTRANT_ID = "SmartViewportGateGuideRegistrant"
    DEFAULT_CAMERA_NAMES = frozenset(
        ("persp", "top", "front", "side", "perspShape", "topShape", "frontShape", "sideShape")
    )

    TEXT_ATTRS = (
        ("topLeftText", "tlt", "topLeftText"),
        ("topCenterText", "tct", "topCenterText"),
        ("topRightText", "trt", "{date}"),
        ("bottomLeftText", "blt", "{camera} Lens:{focal_length} mm"),
        ("bottomCenterText", "bct", "bottomCenterText"),
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

        try:
            data.real_scale_value = cmds.mayaDpiSetting(query=True, rsv=True)
        except Exception:
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
        device_aspect_ratio = cmds.getAttr("defaultResolution.deviceAspectRatio")
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
        current_time = cmds.currentTime(query=True)
        current_frame = int(round(current_time))
        scene_name = cmds.file(query=True, sceneName=True, shortName=True)
        scene_name = os.path.splitext(scene_name)[0] if scene_name else "untitled"

        replacements = {
            "{counter}": str(current_frame).zfill(data.counter_padding),
            "{animTime}": self._format_anim_time(current_frame),
            "{scene}": scene_name,
            "{camera}": self._camera_transform_name(camera_path),
            "{camera_clean}": self._camera_transform_name(camera_path, remove_namespace=True),
            "{focal_length}": str(int(round(om.MFnCamera(camera_path).focalLength))),
            "{frame_start}": str(int(cmds.playbackOptions(query=True, minTime=True))),
            "{frame_end}": str(int(cmds.playbackOptions(query=True, maxTime=True))),
            "{total_frames}": str(
                int(cmds.playbackOptions(query=True, maxTime=True))
                - int(cmds.playbackOptions(query=True, minTime=True))
                + 1
            ),
            "{username}": getpass.getuser(),
            "{date}": datetime.date.today().strftime("%Y/%m/%d"),
        }

        parsed = text
        for token, value in replacements.items():
            parsed = parsed.replace(token, value)
        return parsed

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
        return (
            self._camera_transform_name(camera_path) == name
            or self._camera_shape_name(camera_path) == name
        )

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


def uninitializePlugin(obj):
    plugin_fn = om.MFnPlugin(obj)

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
