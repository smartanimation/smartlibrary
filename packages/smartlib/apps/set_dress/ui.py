from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.qt import parent_for_maya
from smartlib.dcc.maya import set_dress, set_dress_usd
from smartlib.setdress import SetDressPublishService


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
        live_context = set_dress.scene_context()
        self.package = set_dress.SetDressPackage(context=live_context)
        self.path = None
        self._recovery_error = ""
        try:
            recovered, embedded_path = set_dress.load_package_from_scene()
            if recovered is not None:
                self.package = recovered
                self.package.context.update(
                    {key: value for key, value in live_context.items() if value}
                )
                self.path = Path(embedded_path) if embedded_path else None
        except Exception as exc:
            self._recovery_error = str(exc)
        self.publish_service = SetDressPublishService(ProjectConfig(_default_config_dir()))
        try:
            self.identity = self.publish_service.identity_from_context(self.package.context)
        except ValueError:
            self.identity = None
        if self.identity and self.identity.shot:
            self.package.context["shot_root"] = str(
                self.publish_service.paths.shot_root(
                    self.identity.episode, self.identity.sequence, self.identity.shot
                )
            )
        self.recorded = None
        self.recording_layer_id = ""
        self._autosave_enabled = False
        self._scene_callback_id = None
        self._build_ui()
        self.scope.setCurrentText(
            str(self.package.context.get("scope") or "shot")
        )
        self.target.setCurrentText(
            str(self.package.context.get("target") or "maya").upper()
        )
        self.package_name.setText(
            str(self.package.context.get("package") or "main")
        )
        if self.identity is None:
            self.publish_btn.setEnabled(False)
            self.publish_btn.setToolTip("Open a shot scene whose episode and sequence can be resolved.")
        self._autosave_enabled = True
        if not self.package.layers:
            self.add_layer(prompt=False)
        else:
            self.refresh_layers()
            self.status.setText(
                f"RECOVERED {len(self.package.layers)} LAYERS FROM SCENE"
            )
        if self._recovery_error:
            self.status.setText(f"RECOVERY WARNING: {self._recovery_error}")
        self._install_scene_save_callback()

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
        self.target = QtWidgets.QComboBox()
        self.target.addItems(["Maya", "USD"])
        self.scope = QtWidgets.QComboBox()
        self.scope.addItems(["shot", "sequence"])
        self.package_name = QtWidgets.QLineEdit("main")
        self.package_name.setMaximumWidth(140)
        top.addWidget(QtWidgets.QLabel("Target"))
        top.addWidget(self.target)
        top.addWidget(QtWidgets.QLabel("Save scope"))
        top.addWidget(self.scope)
        top.addWidget(QtWidgets.QLabel("Package"))
        top.addWidget(self.package_name)
        right_layout.addLayout(top)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Node", "Attr", "Value", "State"])
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
        self.publish_btn = QtWidgets.QPushButton("Publish")
        self.load_btn = QtWidgets.QPushButton("Load")
        self.history_btn = QtWidgets.QPushButton("History")
        record_layout.addWidget(self.selection_only)
        record_layout.addStretch(1)
        record_layout.addWidget(self.record_btn)
        record_layout.addWidget(self.stop_btn)
        record_layout.addWidget(self.load_btn)
        record_layout.addWidget(self.history_btn)
        record_layout.addWidget(self.save_btn)
        record_layout.addWidget(self.publish_btn)
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
        self.publish_btn.clicked.connect(self.publish)
        self.load_btn.clicked.connect(self.load)
        self.history_btn.clicked.connect(self.restore_history)
        self.layer_list.currentItemChanged.connect(lambda *_: self.refresh_table())
        self.layer_list.itemChanged.connect(self._item_changed)
        self.layer_list.orderChanged.connect(self._sync_order)
        self.search.textChanged.connect(lambda *_: self.refresh_table())
        self.target.currentTextChanged.connect(lambda *_: self._autosave())
        self.scope.currentTextChanged.connect(lambda *_: self._autosave())
        self.package_name.editingFinished.connect(self._autosave)

    def add_layer(self, _checked=False, *, prompt=True):
        if prompt:
            name, ok = QtWidgets.QInputDialog.getText(
                self, "New Layer", "Layer name:", text=f"layer_{len(self.package.layers)+1:02d}"
            )
            if not ok or not name.strip():
                return
        else:
            name = "shot_fix"
        layer = set_dress.SetDressLayer(
            name=name.strip(),
            scope=self.scope.currentText() if hasattr(self, "scope") else "shot",
            target=self._target_name() if hasattr(self, "target") else "maya",
        )
        self.package.layers.insert(0, layer)
        self.refresh_layers(select_id=layer.id)
        self._autosave()

    def delete_layers(self):
        ids = {item.data(QtCore.Qt.UserRole) for item in self.layer_list.selectedItems()}
        self.package.layers = [layer for layer in self.package.layers if layer.id not in ids]
        self.refresh_layers()
        self._autosave()

    def refresh_layers(self, select_id=""):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for layer in self.package.layers:
            text = f"{'○' if layer.muted else '●'}  {layer.name}    {len(layer.changes)} changes"
            text = f"[{layer.target.upper()}] {text}"
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
            values = [change.node, change.attribute, _value(change.after), "Captured"]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))

    def current_layer(self):
        item = self.layer_list.currentItem()
        layer_id = item.data(QtCore.Qt.UserRole) if item else ""
        return next((layer for layer in self.package.layers if layer.id == layer_id), None)

    def _item_changed(self, item):
        layer = next((layer for layer in self.package.layers if layer.id == item.data(QtCore.Qt.UserRole)), None)
        if layer:
            text = item.text()
            prefix = f"[{layer.target.upper()}] "
            if text.startswith(prefix):
                text = text[len(prefix):]
            text = text.split("  ", 1)[-1]
            layer.name = text.rsplit("    ", 1)[0].strip() or layer.name
            self.refresh_layers(select_id=layer.id)
            self._autosave()

    def _sync_order(self):
        ids = [self.layer_list.item(i).data(QtCore.Qt.UserRole) for i in range(self.layer_list.count())]
        mapping = {layer.id: layer for layer in self.package.layers}
        self.package.layers = [mapping[item] for item in ids if item in mapping]
        self._apply_stack()
        self._autosave()

    def toggle_mute(self):
        ids = {item.data(QtCore.Qt.UserRole) for item in self.layer_list.selectedItems()}
        selected = [layer for layer in self.package.layers if layer.id in ids]
        mute = not all(layer.muted for layer in selected)
        for layer in selected:
            layer.muted = mute
        self.refresh_layers(select_id=selected[0].id if selected else "")
        self._apply_stack()
        self._autosave()

    def start_recording(self):
        layer = self.current_layer()
        if not layer:
            self.add_layer()
            layer = self.current_layer()
        try:
            backend = self._backend()
            if backend is set_dress_usd:
                backend.prepare_recording(self.selection_only.isChecked())
            self.recorded = backend.capture_scene(self.selection_only.isChecked())
            self.package.base = set_dress.remember_base(
                self.package.base, self.recorded
            )
        except Exception as exc:
            return self._error(str(exc))
        if not self.recorded:
            return self._error("No transform nodes found. Select a set hierarchy or disable Selected hierarchy.")
        layer.target = self._target_name()
        self._autosave()
        self.recording_layer_id = layer.id
        self.target.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setText(f"RECORDING {len(self.recorded)} NODES — edit the Maya viewport")

    def stop_capture(self):
        try:
            current = self._backend().capture_scene(self.selection_only.isChecked())
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
        self.target.setEnabled(True)
        self.refresh_layers(select_id=layer.id if layer else "")
        self._autosave()
        revision = self._create_revision()
        revision_text = f" — {revision.name}" if revision else ""
        self.status.setText(
            f"CAPTURED {len(changes)} CHANGES{revision_text}"
        )

    def restore(self):
        try:
            warnings = self._restore_all_backends()
            self.status.setText(f"BASE RESTORED{_warning_suffix(warnings)}")
        except Exception as exc:
            self._error(str(exc))

    def apply_selected(self):
        layer = self.current_layer()
        if not layer:
            return
        try:
            warnings = self._backend(layer.target).apply_changes(layer.changes)
            self.status.setText(f"APPLIED {layer.name}{_warning_suffix(warnings)}")
        except Exception as exc:
            self._error(str(exc))

    def _apply_stack(self):
        try:
            warnings = self._apply_all_backends()
            self.status.setText(f"STACK APPLIED{_warning_suffix(warnings)}")
        except Exception as exc:
            self._error(str(exc))

    def save(self):
        try:
            path = self._canonical_path(allow_dialog=True)
            if not path:
                return
            self._update_package_context()
            self.path = set_dress.save_package(self.package, path)
            set_dress.embed_package_in_scene(
                self.package, external_path=self.path, dirty=False
            )
            revision = self._create_revision()
            revision_text = f" — revision {revision.name}" if revision else ""
            self.status.setText(f"SAVED {self.path}{revision_text}")
        except Exception as exc:
            self._error(str(exc))

    def publish(self):
        if self.identity is None:
            return self._error("Could not resolve the current shot identity.")
        package_name = self.package_name.text().strip() or "main"
        scope = self.scope.currentText()
        try:
            self._autosave(force=True)
            if not self.path:
                raise RuntimeError("Set Dress working data could not be saved.")
        except Exception as exc:
            return self._error(str(exc))
        comment, accepted = QtWidgets.QInputDialog.getText(
            self, "Publish Set Dress", "Comment:"
        )
        if not accepted:
            return
        try:
            published = self.publish_service.publish(
                self.path,
                self.identity,
                package=package_name,
                scope=scope,
                comment=comment.strip(),
            )
            self.status.setText(f"PUBLISHED {published}")
            QtWidgets.QMessageBox.information(
                self, "Publish Set Dress", f"Published:\n{published}"
            )
        except Exception as exc:
            self._error(str(exc))

    def load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load Set Dress Layers", str(set_dress.suggested_path("shot", self.package.context).parent), "Set Dress (*.setdress.json);;JSON (*.json)")
        if not path:
            return
        try:
            self.package = set_dress.load_package(path)
            self.path = path
            self.package_name.setText(
                self.package.context.get("package") or Path(path).name.split(".setdress.", 1)[0]
            )
            self.refresh_layers()
            self._apply_stack()
            self._autosave(force=True)
        except Exception as exc:
            self._error(str(exc))

    def restore_history(self):
        working_path = self._canonical_path()
        if not working_path:
            return self._error("Save the Set Dress package before opening history.")
        revisions = set_dress.list_history_revisions(working_path)
        if not revisions:
            return self._error("No Set Dress working revisions were found.")
        labels = [path.name for path in revisions]
        selected, accepted = QtWidgets.QInputDialog.getItem(
            self,
            "Restore Set Dress History",
            "Revision:",
            labels,
            0,
            False,
        )
        if not accepted:
            return
        revision = revisions[labels.index(selected)]
        try:
            self._create_revision()
            restored = set_dress.load_package(revision)
            restored.context.update(self.package.context)
            self.package = restored
            self.refresh_layers()
            self._apply_stack()
            self._autosave(force=True)
            self.status.setText(f"RESTORED {revision.name}")
        except Exception as exc:
            self._error(str(exc))

    def _update_package_context(self):
        self.package.context.update({
            "scope": self.scope.currentText(),
            "package": self.package_name.text().strip() or "main",
            "target": self._target_name(),
        })
        if self.identity:
            self.package.context.update({
                "episode": self.identity.episode,
                "sequence": self.identity.sequence,
                "shot": self.identity.shot,
            })

    def _target_name(self):
        return self.target.currentText().strip().lower()

    def _backend(self, target=None):
        return set_dress_usd if (target or self._target_name()).lower() == "usd" else set_dress

    def _target_base(self, target):
        is_usd = target == "usd"
        return [state for state in self.package.base if ("," in state.node_id) == is_usd]

    def _target_layers(self, target):
        return [layer for layer in self.package.layers if layer.target == target]

    def _apply_all_backends(self):
        warnings = []
        for target in ("maya", "usd"):
            layers = self._target_layers(target)
            base = self._target_base(target)
            if layers or base:
                warnings.extend(self._backend(target).apply_stack(layers, base=base))
        return warnings

    def _restore_all_backends(self):
        warnings = []
        for target in ("maya", "usd"):
            layers = self._target_layers(target)
            base = self._target_base(target)
            if layers or base:
                warnings.extend(self._backend(target).restore_base(layers, base=base))
        return warnings

    def _canonical_path(self, *, allow_dialog=False):
        if self.identity:
            return self.publish_service.data_path(
                self.identity,
                self.package_name.text().strip() or "main",
                scope=self.scope.currentText(),
            )
        if not allow_dialog:
            return self.path
        default = self.path or set_dress.suggested_path(
            self.scope.currentText(), self.package.context
        )
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Set Dress Layers",
            str(default),
            "Set Dress (*.setdress.json);;JSON (*.json)",
        )
        return Path(path) if path else None

    def _autosave(self, *_args, force=False):
        if not self._autosave_enabled and not force:
            return
        try:
            self._update_package_context()
            path = self._canonical_path()
            set_dress.embed_package_in_scene(
                self.package, external_path=path or "", dirty=True
            )
            if path:
                self.path = set_dress.save_package(self.package, path)
                set_dress.embed_package_in_scene(
                    self.package, external_path=self.path, dirty=False
                )
            self.status.setText(
                f"AUTO-SAVED {self.path}" if self.path else "AUTO-SAVED IN SCENE"
            )
        except Exception as exc:
            self.status.setText(f"AUTO-SAVE WARNING: {exc}")

    def _create_revision(self):
        if not self.path:
            return None
        try:
            limit = int(os.environ.get("SMART_SET_DRESS_HISTORY_LIMIT", "30"))
        except ValueError:
            limit = 30
        try:
            return set_dress.create_history_revision(
                self.package, self.path, keep=max(0, limit)
            )
        except Exception as exc:
            self.status.setText(f"HISTORY WARNING: {exc}")
            return None

    def _install_scene_save_callback(self):
        try:
            import maya.OpenMaya as om

            self._scene_callback_id = om.MSceneMessage.addCallback(
                om.MSceneMessage.kBeforeSave,
                lambda *_args: self._autosave(force=True),
            )
        except Exception:
            self._scene_callback_id = None

    def closeEvent(self, event):
        self._autosave(force=True)
        if self._scene_callback_id is not None:
            try:
                import maya.OpenMaya as om

                om.MMessage.removeCallback(self._scene_callback_id)
            except Exception:
                pass
            self._scene_callback_id = None
        super().closeEvent(event)

    def _error(self, message):
        self.status.setText(message)
        QtWidgets.QMessageBox.warning(self, "Smart Set Dress", message)


def _value(value):
    return f"{float(value):.4g}" if isinstance(value, float) else str(value)


def _warning_suffix(warnings):
    if not warnings:
        return ""
    counts = Counter(item.split(":", 1)[0] for item in warnings)
    return f" — {len(warnings)} warnings ({', '.join(counts)})"


def _default_config_dir() -> Path:
    value = os.environ.get("PROJECT_CONFIG_DIR")
    if value:
        return Path(value)
    root = Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or Path(__file__).resolve().parents[4]
    )
    return root / "config" / "STKB"


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
