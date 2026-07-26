from __future__ import annotations

from collections import Counter

from smartlib.core.qt import parent_for_maya
from smartlib.dcc.maya import set_dress


def _qt_modules():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt_modules()
_WINDOW = None


class LayerList(QtWidgets.QListWidget):
    orderChanged = QtCore.Signal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.orderChanged.emit()


class SmartSetDressWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent_for_maya(QtWidgets, parent))
        self.package = set_dress.SetDressPackage(context=set_dress.scene_context())
        self.recorded = None
        self.recording_layer_id = ""
        self.path = None
        self._build_ui()
        self.add_layer(prompt=False)

    def _build_ui(self):
        self.setWindowTitle("Smart Set Dress Manager")
        self.resize(980, 620)
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        self.setCentralWidget(central)

        splitter = QtWidgets.QSplitter()
        layout.addWidget(splitter, 1)
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.addWidget(QtWidgets.QLabel("Set Dress Layers"))
        action_row = QtWidgets.QHBoxLayout()
        self.new_btn = QtWidgets.QPushButton("+ New Layer")
        self.delete_btn = QtWidgets.QPushButton("Delete")
        action_row.addWidget(self.new_btn)
        action_row.addWidget(self.delete_btn)
        left_layout.addLayout(action_row)
        self.layer_list = LayerList()
        self.layer_list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.layer_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        left_layout.addWidget(self.layer_list, 1)
        layer_actions = QtWidgets.QHBoxLayout()
        self.mute_btn = QtWidgets.QPushButton("Mute Selected")
        self.restore_btn = QtWidgets.QPushButton("Restore Base")
        self.apply_btn = QtWidgets.QPushButton("Apply Layer")
        layer_actions.addWidget(self.mute_btn)
        layer_actions.addWidget(self.restore_btn)
        layer_actions.addWidget(self.apply_btn)
        left_layout.addLayout(layer_actions)
        left_layout.addWidget(QtWidgets.QLabel("Top layers have priority"))

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Recorded Changes"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search nodes...")
        top.addWidget(self.search, 1)
        self.scope = QtWidgets.QComboBox()
        self.scope.addItems(["shot", "sequence"])
        top.addWidget(QtWidgets.QLabel("Save scope"))
        top.addWidget(self.scope)
        right_layout.addLayout(top)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Node", "Attr", "Before", "After", "Delta", "State"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        right_layout.addWidget(self.table, 1)

        record_box = QtWidgets.QGroupBox("Capture")
        record_layout = QtWidgets.QHBoxLayout(record_box)
        self.selection_only = QtWidgets.QCheckBox("Selected hierarchy")
        self.selection_only.setChecked(True)
        self.record_btn = QtWidgets.QPushButton("●  Record")
        self.stop_btn = QtWidgets.QPushButton("■  Stop && Capture")
        self.stop_btn.setEnabled(False)
        self.save_btn = QtWidgets.QPushButton("Save Layers")
        self.load_btn = QtWidgets.QPushButton("Load")
        record_layout.addWidget(self.selection_only)
        record_layout.addStretch(1)
        record_layout.addWidget(self.record_btn)
        record_layout.addWidget(self.stop_btn)
        record_layout.addWidget(self.load_btn)
        record_layout.addWidget(self.save_btn)
        right_layout.addWidget(record_box)
        self.status = QtWidgets.QLabel("READY TO RECORD")
        layout.addWidget(self.status)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([280, 700])

        self.new_btn.clicked.connect(self.add_layer)
        self.delete_btn.clicked.connect(self.delete_layers)
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.restore_btn.clicked.connect(self.restore)
        self.apply_btn.clicked.connect(self.apply_selected)
        self.record_btn.clicked.connect(self.start_recording)
        self.stop_btn.clicked.connect(self.stop_capture)
        self.save_btn.clicked.connect(self.save)
        self.load_btn.clicked.connect(self.load)
        self.layer_list.currentItemChanged.connect(lambda *_: self.refresh_table())
        self.layer_list.itemChanged.connect(self._item_changed)
        self.layer_list.orderChanged.connect(self._sync_order)
        self.search.textChanged.connect(lambda *_: self.refresh_table())

    def add_layer(self, _checked=False, *, prompt=True):
        if prompt:
            name, ok = QtWidgets.QInputDialog.getText(
                self, "New Layer", "Layer name:", text=f"layer_{len(self.package.layers)+1:02d}"
            )
            if not ok or not name.strip():
                return
        else:
            name = "shot_fix"
        layer = set_dress.SetDressLayer(name=name.strip(), scope=self.scope.currentText() if hasattr(self, "scope") else "shot")
        self.package.layers.insert(0, layer)
        self.refresh_layers(select_id=layer.id)

    def delete_layers(self):
        ids = {item.data(QtCore.Qt.UserRole) for item in self.layer_list.selectedItems()}
        self.package.layers = [layer for layer in self.package.layers if layer.id not in ids]
        self.refresh_layers()

    def refresh_layers(self, select_id=""):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for layer in self.package.layers:
            text = f"{'○' if layer.muted else '●'}  {layer.name}    {len(layer.changes)} changes"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, layer.id)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsDragEnabled)
            if layer.muted:
                item.setForeground(QtGui.QColor("#777777"))
            self.layer_list.addItem(item)
            if layer.id == select_id:
                self.layer_list.setCurrentItem(item)
        self.layer_list.blockSignals(False)
        if not self.layer_list.currentItem() and self.layer_list.count():
            self.layer_list.setCurrentRow(0)
        self.refresh_table()

    def refresh_table(self):
        layer = self.current_layer()
        query = self.search.text().lower().strip()
        changes = [c for c in (layer.changes if layer else []) if not query or query in c.node.lower() or query in c.attribute.lower()]
        self.table.setRowCount(len(changes))
        for row, change in enumerate(changes):
            delta = _delta(change.before, change.after)
            values = [change.node, change.attribute, _value(change.before), _value(change.after), delta, "Captured"]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))

    def current_layer(self):
        item = self.layer_list.currentItem()
        layer_id = item.data(QtCore.Qt.UserRole) if item else ""
        return next((layer for layer in self.package.layers if layer.id == layer_id), None)

    def _item_changed(self, item):
        layer = next((layer for layer in self.package.layers if layer.id == item.data(QtCore.Qt.UserRole)), None)
        if layer:
            text = item.text().split("  ", 1)[-1]
            layer.name = text.rsplit("    ", 1)[0].strip() or layer.name
            self.refresh_layers(select_id=layer.id)

    def _sync_order(self):
        ids = [self.layer_list.item(i).data(QtCore.Qt.UserRole) for i in range(self.layer_list.count())]
        mapping = {layer.id: layer for layer in self.package.layers}
        self.package.layers = [mapping[item] for item in ids if item in mapping]
        self._apply_stack()

    def toggle_mute(self):
        ids = {item.data(QtCore.Qt.UserRole) for item in self.layer_list.selectedItems()}
        selected = [layer for layer in self.package.layers if layer.id in ids]
        mute = not all(layer.muted for layer in selected)
        for layer in selected:
            layer.muted = mute
        self.refresh_layers(select_id=selected[0].id if selected else "")
        self._apply_stack()

    def start_recording(self):
        layer = self.current_layer()
        if not layer:
            self.add_layer()
            layer = self.current_layer()
        try:
            self.recorded = set_dress.capture_scene(self.selection_only.isChecked())
        except Exception as exc:
            return self._error(str(exc))
        if not self.recorded:
            return self._error("No transform nodes found. Select a set hierarchy or disable Selected hierarchy.")
        self.recording_layer_id = layer.id
        self.record_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setText(f"RECORDING {len(self.recorded)} NODES — edit the Maya viewport")

    def stop_capture(self):
        try:
            current = set_dress.capture_scene(self.selection_only.isChecked())
            changes = set_dress.diff_states(self.recorded or [], current)
        except Exception as exc:
            return self._error(str(exc))
        layer = next((item for item in self.package.layers if item.id == self.recording_layer_id), None)
        if layer:
            layer.changes = changes
            layer.scope = self.scope.currentText()
        self.recorded = None
        self.recording_layer_id = ""
        self.record_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.refresh_layers(select_id=layer.id if layer else "")
        self.status.setText(f"CAPTURED {len(changes)} CHANGES")

    def restore(self):
        try:
            warnings = set_dress.restore_base(self.package.layers)
            self.status.setText(f"BASE RESTORED{_warning_suffix(warnings)}")
        except Exception as exc:
            self._error(str(exc))

    def apply_selected(self):
        layer = self.current_layer()
        if not layer:
            return
        try:
            warnings = set_dress.apply_changes(layer.changes)
            self.status.setText(f"APPLIED {layer.name}{_warning_suffix(warnings)}")
        except Exception as exc:
            self._error(str(exc))

    def _apply_stack(self):
        try:
            warnings = set_dress.apply_stack(self.package.layers)
            self.status.setText(f"STACK APPLIED{_warning_suffix(warnings)}")
        except Exception as exc:
            self._error(str(exc))

    def save(self):
        scope = self.scope.currentText()
        default = self.path or set_dress.suggested_path(scope, self.package.context)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Set Dress Layers", str(default), "Set Dress (*.setdress.json);;JSON (*.json)")
        if not path:
            return
        try:
            self.path = set_dress.save_package(self.package, path)
            self.status.setText(f"SAVED {self.path}")
        except Exception as exc:
            self._error(str(exc))

    def load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load Set Dress Layers", str(set_dress.suggested_path("shot", self.package.context).parent), "Set Dress (*.setdress.json);;JSON (*.json)")
        if not path:
            return
        try:
            self.package = set_dress.load_package(path)
            self.path = path
            self.refresh_layers()
            self._apply_stack()
        except Exception as exc:
            self._error(str(exc))

    def _error(self, message):
        self.status.setText(message)
        QtWidgets.QMessageBox.warning(self, "Smart Set Dress", message)


def _value(value):
    return f"{float(value):.4g}" if isinstance(value, float) else str(value)


def _delta(before, after):
    if isinstance(before, bool) or isinstance(after, bool):
        return "changed"
    try:
        return f"{float(after) - float(before):+.4g}"
    except (TypeError, ValueError):
        return "changed"


def _warning_suffix(warnings):
    if not warnings:
        return ""
    counts = Counter(item.split(":", 1)[0] for item in warnings)
    return f" — {len(warnings)} warnings ({', '.join(counts)})"


def show():
    global _WINDOW
    if _WINDOW is not None:
        try:
            _WINDOW.close()
            _WINDOW.deleteLater()
        except RuntimeError:
            pass
    _WINDOW = SmartSetDressWindow()
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW
