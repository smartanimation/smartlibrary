from __future__ import annotations


def maya_main_window(QtWidgets=None):
    """Return Maya's main window as a Qt widget when running inside Maya."""
    try:
        import maya.OpenMayaUI as omui
    except Exception:
        return None

    pointer = omui.MQtUtil.mainWindow()
    if not pointer:
        return None

    if QtWidgets is None:
        try:
            from PySide6 import QtWidgets as _QtWidgets
        except ImportError:
            from PySide2 import QtWidgets as _QtWidgets
        QtWidgets = _QtWidgets

    try:
        from shiboken6 import wrapInstance
    except ImportError:
        try:
            from shiboken2 import wrapInstance
        except ImportError:
            return None

    return wrapInstance(int(pointer), QtWidgets.QWidget)


def parent_for_maya(QtWidgets=None, parent=None):
    """Use the explicit parent, otherwise Maya's main window if available."""
    return parent if parent is not None else maya_main_window(QtWidgets)
