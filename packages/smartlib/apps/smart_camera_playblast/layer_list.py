"""Card presentation over the existing Playblast row model."""
from smartlib.apps.smart_playblast.ui import QtCore, QtWidgets
try:
    from PySide6 import QtGui
except ImportError:
    from PySide2 import QtGui


class LayerCardDelegate(QtWidgets.QStyledItemDelegate):
    @staticmethod
    def is_checked(index):
        value = index.data(QtCore.Qt.CheckStateRole)
        return value == QtCore.Qt.Checked or value == getattr(QtCore.Qt.Checked, "value", 2)

    def sizeHint(self, option, index):
        return QtCore.QSize(340, 58)

    @staticmethod
    def check_rect(rect):
        return QtCore.QRect(rect.left() + 14, rect.top() + 18, 20, 20)

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect.adjusted(4, 3, -4, -3)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        enabled = bool(index.flags() & QtCore.Qt.ItemIsEnabled)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QColor("#4984ad" if selected else "#3b3b3b"))
        gradient = QtGui.QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0, QtGui.QColor("#303941" if selected else "#303030"))
        gradient.setColorAt(1, QtGui.QColor("#252c32" if selected else "#252525"))
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 3, 3)
        check = self.check_rect(option.rect)
        painter.setPen(QtGui.QPen(QtGui.QColor("#919191" if enabled else "#555555"), 1))
        painter.setBrush(QtGui.QColor("#272727"))
        painter.drawRoundedRect(check, 2, 2)
        if self.is_checked(index):
            painter.setPen(QtGui.QPen(QtGui.QColor("#eeeeee" if enabled else "#777777"), 2))
            painter.drawLine(check.left() + 4, check.top() + 10, check.left() + 8, check.top() + 14)
            painter.drawLine(check.left() + 8, check.top() + 14, check.left() + 16, check.top() + 5)
        def value(column):
            return str(index.sibling(index.row(), column).data() or "")
        left = rect.left() + 60
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#4d9fe8" if enabled else "#646464"))
        painter.drawEllipse(QtCore.QPointF(rect.left() + 47, rect.top() + 10), 5, 5)
        def line(text, y, size, color, right_padding=16):
            font = QtGui.QFont(option.font)
            font.setPixelSize(size)
            painter.setFont(font)
            painter.setPen(QtGui.QColor(color if enabled else "#777777"))
            area = QtCore.QRect(left, rect.top() + y, rect.right() - left - right_padding, 17)
            text = QtGui.QFontMetrics(font).elidedText(text, QtCore.Qt.ElideRight, area.width())
            painter.drawText(area, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text)
        line(value(2), 0, 14, "#eeeeee", 100)
        line(f"{value(4)}    {value(3)}", 17, 11, "#c8c8c8")
        line(value(1), 33, 11, "#9ca5ad")
        font = QtGui.QFont(option.font)
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#c8c8c8"))
        painter.drawText(rect.adjusted(0, 2, -16, 0), QtCore.Qt.AlignRight | QtCore.Qt.AlignTop,
                         f"v{int(value(5) or 1):03d}  t{int(value(6) or 1):02d}")
        painter.restore()

    def helpEvent(self, event, view, option, index):
        camera = str(index.sibling(index.row(), 1).data() or "")
        QtWidgets.QToolTip.showText(event.globalPos(), f"Camera: {camera}", view)
        return True

    def editorEvent(self, event, model, option, index):
        if not index.flags() & QtCore.Qt.ItemIsEnabled:
            return False
        toggle = event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton and self.check_rect(option.rect).contains(event.pos())
        toggle |= event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Space
        if toggle:
            state = QtCore.Qt.Unchecked if self.is_checked(index) else QtCore.Qt.Checked
            return model.setData(index, getattr(state, "value", state), QtCore.Qt.CheckStateRole)
        return False


class LayerListView(QtWidgets.QListView):
    """Manual row movement preserves the production table's row payloads."""
    def __init__(self, table, parent=None):
        super().__init__(parent)
        self.table = table
        self.setModel(table.model())
        self.setModelColumn(0)
        self.setItemDelegate(LayerCardDelegate(self))
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._drag_row = -1
        self._dragging = False

    def mousePressEvent(self, event):
        index = self.indexAt(event.pos())
        self._drag_row = index.row() if index.isValid() else -1
        self._drag_start = event.pos()
        self._dragging = False
        if index.isValid() and LayerCardDelegate.check_rect(self.visualRect(index)).contains(event.pos()):
            self._drag_row = -1
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_row >= 0 and event.buttons() & QtCore.Qt.LeftButton and (event.pos() - self._drag_start).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
            self._dragging = True
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.viewport().unsetCursor()
        if self._dragging and event.button() == QtCore.Qt.LeftButton:
            target = self.indexAt(event.pos()).row()
            target = target if target >= 0 else self.model().rowCount() - 1
            rows = self.table._snapshot_rows()
            source = self._drag_row
            self._dragging = False
            self._drag_row = -1
            if 0 <= source < len(rows) and source != target:
                rows.insert(target, rows.pop(source))
                self.table._restore_drag_snapshot(rows, target)
            return
        self._drag_row = -1
        super().mouseReleaseEvent(event)
