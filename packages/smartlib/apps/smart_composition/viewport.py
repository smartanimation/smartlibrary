"""Read-only camera information and mouse-transparent viewport HUD."""
from pxr import UsdGeom
from pxr.Usdviewq.qt import QtCore, QtWidgets


def camera_name(prim):
    path = str(prim.GetPath())
    prefix = '/Shot/Camera/'
    return path[len(prefix):] if path.startswith(prefix) else path


def camera_info(stage, prim, time):
    if not prim or not prim.IsA(UsdGeom.Camera):
        return 'Camera: Free camera\nFocal length: --'
    camera = UsdGeom.Camera(prim)
    name = camera_name(prim)
    if camera.GetProjectionAttr().Get(time) == UsdGeom.Tokens.orthographic:
        lens = 'Orthographic'
    else:
        focal = camera.GetFocalLengthAttr().Get(time)
        # USD optics are in tenths of a scene unit, not always millimeters.
        lens = f'{focal * UsdGeom.GetStageMetersPerUnit(stage) * 100:g} mm' if focal is not None else '--'
    horizontal = camera.GetHorizontalApertureAttr().Get(time)
    vertical = camera.GetVerticalApertureAttr().Get(time)
    aspect = f'\nCamera gate: {horizontal / vertical:.3f}:1' if horizontal and vertical else ''
    return f'Camera: {name}\nFocal length: {lens}{aspect}'


class ViewportHUD(QtWidgets.QWidget):
    def __init__(self, view):
        super().__init__(view)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet('ViewportHUD { background: transparent; } '
            'QLabel { background: rgba(0,0,0,135); color: white; padding: 7px; border-radius: 3px; }')
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.shot = QtWidgets.QLabel()
        self.camera = QtWidgets.QLabel()
        for label in (self.shot, self.camera):
            label.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(self.shot, 0, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        layout.addWidget(self.camera, 2, 1, QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom)
        layout.setRowStretch(1, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        view.installEventFilter(self)
        self.setGeometry(view.rect())
        self.hide()

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Resize:
            self.setGeometry(watched.rect())
        return False
