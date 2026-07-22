from __future__ import annotations

from pathlib import Path


def capture_viewport_thumbnail(
    path: str | Path,
    *,
    width: int = 320,
    height: int = 180,
    isolate_nodes: list[str] | None = None,
) -> Path:
    """Capture the current Maya viewport to a thumbnail image."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Thumbnail capture is available inside Maya.") from exc

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = cmds.currentTime(query=True)
    selection = cmds.ls(selection=True, long=True) or []
    panel = _active_model_panel(cmds)
    previous_isolate_state = None
    previous_camera = ""
    capture_camera = ""
    visibility_state: dict[str, bool] = {}
    isolate_nodes = [node for node in isolate_nodes or [] if node and cmds.objExists(node)]
    try:
        if panel:
            try:
                cmds.setFocus(panel)
            except Exception:
                pass
        if isolate_nodes:
            if panel:
                try:
                    previous_camera = cmds.modelEditor(panel, query=True, camera=True) or ""
                except Exception:
                    previous_camera = ""
                capture_camera = _create_capture_camera(cmds)
                if capture_camera:
                    try:
                        cmds.modelEditor(panel, edit=True, camera=capture_camera)
                    except Exception:
                        pass
            visibility_state = _show_nodes_for_capture(cmds, isolate_nodes)
            cmds.select(isolate_nodes, replace=True)
            if panel:
                previous_isolate_state = cmds.isolateSelect(panel, query=True, state=True)
                cmds.isolateSelect(panel, state=False)
                cmds.isolateSelect(panel, state=True)
                cmds.isolateSelect(panel, loadSelected=True)
                _view_fit_panel_camera(cmds, panel)
        if panel:
            try:
                cmds.setFocus(panel)
            except Exception:
                pass
        try:
            cmds.refresh(force=True)
        except Exception:
            pass
        cmds.playblast(
            completeFilename=str(output),
            forceOverwrite=True,
            format="image",
            compression="jpg",
            viewer=False,
            showOrnaments=False,
            offScreen=True,
            frame=[frame],
            widthHeight=[int(width), int(height)],
            percent=100,
        )
    finally:
        if panel and previous_isolate_state is not None:
            try:
                cmds.isolateSelect(panel, state=previous_isolate_state)
            except Exception:
                pass
        if panel and previous_camera:
            try:
                cmds.modelEditor(panel, edit=True, camera=previous_camera)
            except Exception:
                pass
        if capture_camera and cmds.objExists(capture_camera):
            try:
                cmds.delete(capture_camera)
            except Exception:
                pass
        _restore_visibility(cmds, visibility_state)
        if selection:
            existing = [node for node in selection if cmds.objExists(node)]
            if existing:
                cmds.select(existing, replace=True)
        else:
            try:
                cmds.select(clear=True)
            except Exception:
                pass
    return output


def _active_model_panel(cmds) -> str:
    panel = cmds.getPanel(withFocus=True)
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel" and cmds.modelPanel(panel, exists=True):
        return panel
    for panel in cmds.getPanel(visiblePanels=True) or []:
        if cmds.getPanel(typeOf=panel) == "modelPanel" and cmds.modelPanel(panel, exists=True):
            return panel
    for panel in cmds.getPanel(type="modelPanel") or []:
        if cmds.modelPanel(panel, exists=True):
            return panel
    return ""


def _view_fit_panel_camera(cmds, panel: str) -> None:
    try:
        cmds.setFocus(panel)
    except Exception:
        pass
    try:
        cmds.viewFit(fitFactor=1.08)
        return
    except Exception:
        pass
    try:
        cmds.viewFit(fitFactor=1.05)
    except Exception:
        pass


def _create_capture_camera(cmds) -> str:
    try:
        camera = cmds.camera(name="smartThumbnailCamera#")
    except Exception:
        return ""
    if not camera:
        return ""
    transform = camera[0]
    try:
        cmds.setAttr(f"{transform}.visibility", False)
    except Exception:
        pass
    return transform


def _show_nodes_for_capture(cmds, nodes: list[str]) -> dict[str, bool]:
    state: dict[str, bool] = {}
    for node in nodes:
        for item in _node_and_ancestors(cmds, node):
            attr = f"{item}.visibility"
            if attr in state or not cmds.objExists(attr):
                continue
            try:
                state[attr] = bool(cmds.getAttr(attr))
                cmds.setAttr(attr, True)
            except Exception:
                state.pop(attr, None)
    return state


def _restore_visibility(cmds, state: dict[str, bool]) -> None:
    for attr, value in reversed(list(state.items())):
        if not cmds.objExists(attr):
            continue
        try:
            cmds.setAttr(attr, value)
        except Exception:
            pass


def _node_and_ancestors(cmds, node: str) -> list[str]:
    nodes = []
    current = (cmds.ls(node, long=True) or [node])[0]
    while current and cmds.objExists(current):
        nodes.append(current)
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        current = parents[0] if parents else ""
    return nodes
