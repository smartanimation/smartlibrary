"""Lightweight startup feedback shared by Maya and the desktop entry point."""
from contextlib import contextmanager

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.core.icons import tool_icon_path


class StartupSplash(QtWidgets.QSplashScreen):
    def __init__(self):
        canvas = QtGui.QPixmap(520, 240)
        canvas.fill(QtGui.QColor('#202428'))
        painter = QtGui.QPainter(canvas)
        try:
            painter.fillRect(0, 0, 520, 4, QtGui.QColor('#419cda'))
            path = tool_icon_path('build_manager', size=88)
            icon = QtGui.QPixmap(str(path)) if path else QtGui.QPixmap()
            if not icon.isNull():
                icon = icon.scaled(88, 88, QtCore.Qt.KeepAspectRatio,
                                   QtCore.Qt.SmoothTransformation)
                painter.drawPixmap((520 - icon.width()) // 2, 25, icon)
            painter.setPen(QtGui.QColor('#f0f3f5'))
            font = QtGui.QFont('Yu Gothic UI')
            font.setPointSize(19)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QtCore.QRect(12, 125, 496, 38), QtCore.Qt.AlignCenter,
                             'Review Build Manager')
            font.setPointSize(11)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QtGui.QColor('#b7c6d1'))
            painter.drawText(QtCore.QRect(12, 175, 496, 38), QtCore.Qt.AlignCenter,
                             '読み込み中… / Loading project and shots…')
        finally:
            painter.end()
        super().__init__(canvas, QtCore.Qt.WindowStaysOnTopHint)

    def mousePressEvent(self, event):
        # QSplashScreen normally disappears on click; keep feedback visible.
        event.accept()


@contextmanager
def startup_splash():
    splash = StartupSplash()
    try:
        splash.show()
        splash.repaint()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
        yield splash
    finally:
        splash.close()
        splash.deleteLater()
