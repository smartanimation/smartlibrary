"""A Maya-native camera contact sheet with shared viewport controls."""
from __future__ import annotations

from functools import partial
import math

WINDOW = "SmartMultiViewWindow"
DEFAULT_CAMERAS = frozenset({"persp", "top", "front", "side"})
# Explicit flags preserve plugin geometry (for example MayaUSD proxy shapes).
# Do not use allObjects=False, which also disables plugin display filters.
GEOMETRY_ONLY_FLAGS = dict.fromkeys((
    "nurbsCurves", "locators", "joints", "ikHandles", "handles",
    "deformers", "dimensions", "pivots", "cameras", "lights",
    "controlVertices", "hulls", "manipulators", "motionTrails",
    "dynamics", "dynamicConstraints", "fluids", "hairSystems",
    "follicles", "nParticles", "nCloths", "nRigids", "strokes",
    "planes", "imagePlane",
), False)
GEOMETRY_ONLY_FLAGS.update(polymeshes=True, nurbsSurfaces=True, subdivSurfaces=True)
_instance = None


def scene_cameras(cmds):
    """Keep full DAG paths; referenced cameras and duplicate leaf names are valid."""
    cameras = set()
    for shape in cmds.ls(type="camera", long=True) or []:
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if not parents or cmds.getAttr(shape + ".intermediateObject"):
            continue
        camera = parents[0]
        # Namespaced cameras are user cameras, even when named namespace:persp.
        if camera.rsplit("|", 1)[-1] in DEFAULT_CAMERAS:
            continue
        if cmds.camera(shape, query=True, startupCamera=True):
            continue
        cameras.add(camera)
    return sorted(cameras, key=str.casefold)


def guide_nodes(cmds):
    from smartlib.dcc.maya.layout_panel import _smart_gate_guide_shapes
    return _smart_gate_guide_shapes(cmds)


def set_guides(cmds, enabled):
    """Explicit scene-wide visibility control, without changing guide contents."""
    shapes = guide_nodes(cmds)
    if enabled and not shapes:
        from smartlib.dcc.maya.smart_menu import ensure_smart_gate_guide_plugin
        ensure_smart_gate_guide_plugin(cmds)
        selection = cmds.ls(selection=True, long=True) or []
        try:
            cmds.SmartGateGuide()
        finally:
            cmds.select(selection, replace=True) if selection else cmds.select(clear=True)
        shapes = guide_nodes(cmds)
    failures = []
    for shape in shapes:
        nodes = [shape]
        if enabled:
            nodes += cmds.listRelatives(shape, parent=True, fullPath=True) or []
        for node in nodes:
            try:
                plug = node + ".visibility"
                if bool(cmds.getAttr(plug)) != bool(enabled):
                    cmds.setAttr(plug, bool(enabled))
            except RuntimeError as exc:
                failures.append(f"{node}: {exc}")
    if failures:
        raise RuntimeError("Some guides could not be changed: " + "; ".join(failures))


class MultiView:
    def __init__(self, cmds):
        self.cmds = cmds
        self.panels = []
        self.grid = None
        self.columns = 3
        self.geometry_only = False
        self.object_display_states = {}
        self.settings = dict(displayTextures=True, displayLights="default",
                             wireframeOnShaded=False, grid=False,
                             displayAppearance="smoothShaded")

    def show(self):
        c = self.cmds
        c.window(WINDOW, title="SmartMultiView", widthHeight=(1200, 800))
        self.root = c.formLayout(parent=WINDOW)
        toolbar = c.columnLayout(parent=self.root, adjustableColumn=True)
        row = c.rowLayout(parent=toolbar, numberOfColumns=6)
        for label, flag in (("Texture", "displayTextures"),
                            ("Use All Lights", "displayLights"),
                            ("Wireframe on Shaded", "wireframeOnShaded"),
                            ("Grid", "grid")):
            c.checkBox(parent=row, label=label,
                       value=self.settings[flag] is True,
                       changeCommand=partial(self.set_display, flag))
        self.guide_check = c.checkBox(
            parent=row, label="SmartGateGuide (Scene)",
            annotation="Changes guide visibility in all Maya viewports.",
            changeCommand=self.set_guide_visibility)
        c.button(parent=row, label="Refresh Cameras", command=self.refresh)
        row = c.rowLayout(parent=toolbar, numberOfColumns=3)
        c.intSliderGrp(parent=row, label="Columns", field=True, minValue=1,
                       maxValue=6, fieldMinValue=1, fieldMaxValue=6, value=3,
                       changeCommand=self.set_columns)
        c.checkBox(parent=row, label="Geometry Only", value=self.geometry_only,
                   annotation="Hide curves, locators and other helper objects in these views only.",
                   changeCommand=self.set_geometry_only)
        self.status = c.text(parent=row, label="")
        c.formLayout(self.root, edit=True, attachForm=[
            (toolbar, "top", 4), (toolbar, "left", 4), (toolbar, "right", 4)])
        self.toolbar = toolbar
        c.scriptJob(uiDeleted=[WINDOW, self.dispose], runOnce=True)
        for event in ("SceneOpened", "NewSceneOpened"):
            c.scriptJob(event=[event, self.refresh], parent=WINDOW)
        self.refresh()
        c.showWindow(WINDOW)
        return self

    def dispose(self, *_):
        for panel in self.panels:
            if self.cmds.modelPanel(panel, exists=True):
                self.cmds.deleteUI(panel, panel=True)
        self.panels.clear()
        self.object_display_states.clear()

    def set_columns(self, value):
        self.columns = max(1, min(6, int(value)))
        self.refresh()

    def set_display(self, flag, value):
        value = ("all" if value else "default") if flag == "displayLights" else bool(value)
        self.settings[flag] = value
        for panel in self.panels:
            if self.cmds.modelPanel(panel, exists=True):
                self.cmds.modelEditor(panel, edit=True, **{flag: value})

    def sync_guides(self):
        c = self.cmds
        shapes = guide_nodes(c)
        visible = any(c.getAttr(shape + ".visibility") and all(
            c.getAttr(parent + ".visibility")
            for parent in (c.listRelatives(shape, parent=True, fullPath=True) or [])
        ) for shape in shapes)
        c.checkBox(self.guide_check, edit=True, value=visible)

    def apply_geometry_only(self, panel):
        c = self.cmds
        if self.geometry_only:
            if panel not in self.object_display_states:
                self.object_display_states[panel] = {
                    flag: c.modelEditor(panel, query=True, **{flag: True})
                    for flag in GEOMETRY_ONLY_FLAGS
                }
            c.modelEditor(panel, edit=True, **GEOMETRY_ONLY_FLAGS)
        elif panel in self.object_display_states:
            c.modelEditor(panel, edit=True, **self.object_display_states[panel])
            del self.object_display_states[panel]

    def set_geometry_only(self, enabled):
        self.geometry_only = bool(enabled)
        for panel in self.panels:
            if self.cmds.modelPanel(panel, exists=True):
                self.apply_geometry_only(panel)

    def set_guide_visibility(self, value):
        try:
            set_guides(self.cmds, bool(value))
        except RuntimeError as exc:
            self.cmds.warning(str(exc))
        finally:
            self.sync_guides()

    def refresh(self, *_):
        c = self.cmds
        cameras = scene_cameras(c)
        self.dispose()
        if self.grid and c.layout(self.grid, exists=True):
            c.deleteUI(self.grid, layout=True)
        self.grid = c.formLayout(parent=self.root, numberOfDivisions=1000)
        c.formLayout(self.root, edit=True,
                     attachForm=[(self.grid, edge, 4) for edge in ("left", "right", "bottom")],
                     attachControl=[(self.grid, "top", 4, self.toolbar)])
        columns = min(self.columns, max(1, len(cameras)))
        rows = max(1, math.ceil(len(cameras) / columns))
        for index, camera in enumerate(cameras):
            cell = c.frameLayout(parent=self.grid, label=camera, collapsable=False)
            pane = c.paneLayout(parent=cell, configuration="single")
            panel = c.modelPanel(parent=pane, camera=camera, menuBarVisible=False)
            self.panels.append(panel)
            c.modelEditor(panel, edit=True, rendererName="vp2Renderer", **self.settings)
            self.apply_geometry_only(panel)
            row, col = divmod(index, columns)
            c.formLayout(self.grid, edit=True, attachPosition=[
                (cell, "left", 2, round(col * 1000 / columns)),
                (cell, "right", 2, round((col + 1) * 1000 / columns)),
                (cell, "top", 2, round(row * 1000 / rows)),
                (cell, "bottom", 2, round((row + 1) * 1000 / rows))])
        if not cameras:
            c.text(parent=self.grid, label="No additional cameras. Create a camera, then Refresh Cameras.")
        c.text(self.status, edit=True, label=f"{len(cameras)} cameras")
        self.sync_guides()


def show():
    import maya.cmds as cmds
    global _instance
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW, window=True)
    _instance = MultiView(cmds)
    return _instance.show()
