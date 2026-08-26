from __future__ import annotations

import os
import sys
import ast
from copy import deepcopy
from pathlib import Path


def _qt_modules():
    try:
        from PySide6 import QtCore, QtWidgets

        return QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets

        return QtCore, QtWidgets


QtCore, QtWidgets = _qt_modules()


def _ensure_smartlib_on_path() -> None:
    root = (
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or str(Path(__file__).resolve().parents[1])
    )
    package_dir = str(Path(root) / "packages")
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)


def _default_config_dir() -> Path:
    env_path = os.environ.get("PROJECT_CONFIG_DIR")
    if env_path:
        return Path(env_path)
    root = Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or Path(__file__).resolve().parents[1]
    )
    return root / "config" / "STKB"


def _service(config_dir=None):
    _ensure_smartlib_on_path()
    from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
    from smartlib.core.config_loader import ProjectConfig

    return ShotManagerService(ProjectConfig(config_dir or _default_config_dir())), ShotIdentity


def _is_maya_session() -> bool:
    try:
        import maya.cmds  # noqa: F401

        return True
    except ImportError:
        return False


class ReviewLayerWindow(QtWidgets.QDialog):
    MEMBER_COLUMNS = ("Type", "Member", "Asset / Object", "Variant", "Role", "Namespace", "Status")
    CAST_COLUMNS = ("Member", "Asset", "Variant", "Role", "Namespace", "Assigned Layer")

    def __init__(self, identity=None, config_dir=None, department=None, parent=None):
        super().__init__(parent)
        self.service, self.identity_cls = _service(config_dir)
        self.is_maya_session = _is_maya_session()
        self.identity = identity
        self.fixed_identity = identity is not None
        if self.identity is None:
            self.identity = self._working_shot_identity()
        self.department = str(department or "anim").strip() or "anim"
        self._layers: dict[str, dict] = {}
        self._cast: dict[str, dict] = {}
        self.setWindowTitle(f"Review Layer Manager - {self.service.project_config.project_name}")
        self.resize(1120, 680)
        self._build_ui()
        if not self.fixed_identity:
            self._populate_shots()
        else:
            self._set_context_label()
        self.refresh()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        self.context_label = QtWidgets.QLabel("No shot selected")
        self.context_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.shot_combo = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        header.addWidget(self.context_label)
        header.addWidget(self.shot_combo, 1)
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        body = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        body.setChildrenCollapsible(False)
        body.addWidget(self._build_layers_panel())
        body.addWidget(self._build_members_panel())
        body.addWidget(self._build_cast_panel())
        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 3)
        body.setStretchFactor(2, 2)
        root.addWidget(body, 1)

        footer = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("")
        self.save_btn = QtWidgets.QPushButton("Publish Definitions")
        self.create_layers_btn = QtWidgets.QPushButton("Create / Sync Display Layers")
        self.close_btn = QtWidgets.QPushButton("Close")
        self.save_btn.setStyleSheet("background-color: #2868a8;")
        self.create_layers_btn.setStyleSheet("background-color: #3f7d32;")
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.save_btn)
        footer.addWidget(self.create_layers_btn)
        footer.addWidget(self.close_btn)
        root.addLayout(footer)

        if not self.is_maya_session:
            self.create_layers_btn.setEnabled(False)
            self.add_object_btn.setEnabled(False)
            self.select_maya_btn.setEnabled(False)

        self.shot_combo.currentIndexChanged.connect(self._shot_changed)
        self.refresh_btn.clicked.connect(self.refresh)
        self.layer_list.currentItemChanged.connect(lambda *_: self._refresh_member_views())
        self.add_layer_btn.clicked.connect(self.add_layer)
        self.duplicate_layer_btn.clicked.connect(self.duplicate_layer)
        self.delete_layer_btn.clicked.connect(self.delete_layer)
        self.add_cast_btn.clicked.connect(self.add_cast)
        self.add_object_btn.clicked.connect(self.add_selected_objects)
        self.remove_member_btn.clicked.connect(self.remove_members)
        self.select_maya_btn.clicked.connect(self.select_members_in_maya)
        self.cast_search.textChanged.connect(self._populate_available_cast)
        self.display_layer_combo.currentTextChanged.connect(self._display_layer_changed)
        self.save_btn.clicked.connect(self.save)
        self.create_layers_btn.clicked.connect(self.create_review_layers)
        self.close_btn.clicked.connect(self.close)

    def _build_layers_panel(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel("Review Layers"))
        self.layer_list = QtWidgets.QListWidget()
        self.layer_list.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        layout.addWidget(self.layer_list, 1)
        actions = QtWidgets.QHBoxLayout()
        self.add_layer_btn = QtWidgets.QPushButton("+")
        self.duplicate_layer_btn = QtWidgets.QPushButton("Duplicate")
        self.delete_layer_btn = QtWidgets.QPushButton("Delete")
        actions.addWidget(self.add_layer_btn)
        actions.addWidget(self.duplicate_layer_btn)
        actions.addWidget(self.delete_layer_btn)
        layout.addLayout(actions)
        return widget

    def _build_members_panel(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel("Layer Members"))
        mapping = QtWidgets.QHBoxLayout()
        mapping.addWidget(QtWidgets.QLabel("Display Layer"))
        self.display_layer_combo = QtWidgets.QComboBox()
        self.display_layer_combo.setEditable(True)
        mapping.addWidget(self.display_layer_combo, 1)
        layout.addLayout(mapping)
        actions = QtWidgets.QHBoxLayout()
        self.add_cast_btn = QtWidgets.QPushButton("Add Cast")
        self.add_object_btn = QtWidgets.QPushButton("Add Selected Objects")
        self.remove_member_btn = QtWidgets.QPushButton("Remove")
        self.select_maya_btn = QtWidgets.QPushButton("Select in Maya")
        for button in (
            self.add_cast_btn,
            self.add_object_btn,
            self.remove_member_btn,
            self.select_maya_btn,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.member_table = QtWidgets.QTableWidget(0, len(self.MEMBER_COLUMNS))
        self.member_table.setHorizontalHeaderLabels(self.MEMBER_COLUMNS)
        self.member_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.member_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.member_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.member_table.setShowGrid(False)
        self.member_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.member_table, 1)
        return widget

    def _build_cast_panel(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel("Available Cast"))
        self.cast_search = QtWidgets.QLineEdit()
        self.cast_search.setPlaceholderText("Search cast")
        layout.addWidget(self.cast_search)
        self.cast_table = QtWidgets.QTableWidget(0, len(self.CAST_COLUMNS))
        self.cast_table.setHorizontalHeaderLabels(self.CAST_COLUMNS)
        self.cast_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.cast_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.cast_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.cast_table.setShowGrid(False)
        self.cast_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.cast_table, 1)
        self.info_label = QtWidgets.QLabel("")
        self.info_label.setMinimumHeight(90)
        self.info_label.setAlignment(QtCore.Qt.AlignTop)
        layout.addWidget(self.info_label)
        return widget

    def _populate_shots(self) -> None:
        preferred = self.identity
        self.shot_combo.blockSignals(True)
        self.shot_combo.clear()
        preferred_index = -1
        for identity in self.service.list_shots():
            self.shot_combo.addItem(identity.code, identity)
            if identity == preferred:
                preferred_index = self.shot_combo.count() - 1
        if preferred_index >= 0:
            self.shot_combo.setCurrentIndex(preferred_index)
        self.shot_combo.blockSignals(False)
        if self.shot_combo.count():
            self.identity = self.shot_combo.currentData()
        self.shot_combo.setVisible(not self.fixed_identity)
        self._set_context_label()

    def _working_shot_identity(self):
        if self.is_maya_session:
            try:
                import maya.cmds as cmds

                scene_path = str(cmds.file(query=True, sceneName=True) or "")
                identity = self.service.shot_identity_from_path(scene_path)
                if identity is not None:
                    return identity
            except Exception:
                pass

        try:
            _ensure_smartlib_on_path()
            from smartlib.core.tokens import TokenContext

            tokens = TokenContext.from_environment()
            if tokens.episode and tokens.sequence and tokens.shot:
                candidate = self.identity_cls(
                    tokens.episode,
                    tokens.sequence,
                    tokens.shot,
                )
                if candidate in self.service.list_shots():
                    return candidate
        except Exception:
            pass
        return None

    def _shot_changed(self) -> None:
        identity = self.shot_combo.currentData()
        if identity:
            self.identity = identity
            self._set_context_label()
            self.refresh()

    def _set_context_label(self) -> None:
        if self.identity:
            self.context_label.setText(
                f"{self.identity.episode} / {self.identity.sequence} / {self.identity.shot}"
            )

    def refresh(self) -> None:
        if not self.identity:
            return
        self._cast = dict((self.service.load_cast(self.identity).get("cast") or {}))
        self._layers = deepcopy(
            self.service.review_layers(self.identity, self.department)
        )
        repaired = self._normalize_cast_members()
        self._populate_layers()
        self._refresh_member_views()
        self.status_label.setText(f"{len(self._layers)} Review Layers")

    def _populate_layers(self, selected="") -> None:
        current = selected or self.current_layer_name()
        self.layer_list.clear()
        for name, layer in sorted(
            self._layers.items(), key=lambda item: int((item[1] or {}).get("order", 0))
        ):
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, name)
            self.layer_list.addItem(item)
            self._set_layer_item_text(item)
            if name == current:
                self.layer_list.setCurrentItem(item)
        if self.layer_list.count() and self.layer_list.currentRow() < 0:
            self.layer_list.setCurrentRow(0)

    def _set_layer_item_text(self, item) -> None:
        name = str(item.data(QtCore.Qt.UserRole) or "")
        layer = self._layers.get(name) or {}
        count = len(layer.get("members") or []) + len(layer.get("objects") or [])
        item.setText(f"{name}    {count}")

    def current_layer_name(self) -> str:
        item = self.layer_list.currentItem()
        return str(item.data(QtCore.Qt.UserRole) or "") if item else ""

    def add_layer(self) -> None:
        name, accepted = QtWidgets.QInputDialog.getText(self, "Add Review Layer", "Layer name")
        name = str(name).strip().upper()
        if not accepted or not name:
            return
        if name in self._layers:
            QtWidgets.QMessageBox.warning(self, "Add Review Layer", f"Layer already exists: {name}")
            return
        self._layers[name] = {
            "members": [], "objects": [], "display_layer": name,
            "order": len(self._layers) * 10,
        }
        self._populate_layers(name)

    def duplicate_layer(self) -> None:
        source = self.current_layer_name()
        if not source:
            return
        name = f"{source}_COPY"
        index = 2
        while name in self._layers:
            name = f"{source}_COPY{index}"
            index += 1
        layer = deepcopy(self._layers[source])
        layer["members"] = []
        layer["objects"] = []
        layer["display_layer"] = name
        self._layers[name] = layer
        self._populate_layers(name)

    def delete_layer(self) -> None:
        name = self.current_layer_name()
        if name:
            self._layers.pop(name, None)
            self._populate_layers()
            self._refresh_member_views()

    def _cast_assignment(self, cast_key: str) -> str:
        for layer_name, layer in self._layers.items():
            if cast_key in (layer.get("members") or []):
                return layer_name
        return ""

    def add_cast(self) -> None:
        target = self.current_layer_name()
        if not target:
            return
        selected = sorted({index.row() for index in self.cast_table.selectionModel().selectedRows()})
        for row in selected:
            item = self.cast_table.item(row, 0)
            payload = item.data(QtCore.Qt.UserRole) if item else {}
            cast_key = (
                str(payload.get("cast_key") or "")
                if isinstance(payload, dict)
                else str(payload or "")
            )
            if not cast_key:
                continue
            assigned = self._cast_assignment(cast_key)
            if assigned and assigned != target:
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Duplicate Assignment",
                    f"{cast_key} is already assigned to {assigned}.\nMove to {target}?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                    QtWidgets.QMessageBox.Cancel,
                )
                if answer != QtWidgets.QMessageBox.Yes:
                    continue
                self._layers[assigned]["members"] = [
                    value for value in self._layers[assigned].get("members", []) if value != cast_key
                ]
            members = self._layers[target].setdefault("members", [])
            if cast_key not in members:
                members.append(cast_key)
        self._refresh_member_views()
        self._refresh_layer_counts()

    def _normalize_cast_members(self) -> bool:
        """Repair member payloads written by the first focused UI version."""
        changed = False
        for layer in self._layers.values():
            original = list(layer.get("members") or [])
            normalized = []
            for value in original:
                cast_key = ""
                if isinstance(value, dict):
                    cast_key = str(value.get("cast_key") or value.get("key") or "")
                elif isinstance(value, str):
                    cast_key = value
                    if value.startswith("{") and "cast_key" in value:
                        try:
                            payload = ast.literal_eval(value)
                        except (SyntaxError, ValueError):
                            payload = {}
                        if isinstance(payload, dict):
                            cast_key = str(payload.get("cast_key") or value)
                if cast_key and cast_key not in normalized:
                    normalized.append(cast_key)
            layer["members"] = normalized
            changed = changed or normalized != original
        return changed

    def add_selected_objects(self) -> None:
        if not self.is_maya_session:
            return
        import maya.cmds as cmds

        target = self.current_layer_name()
        selected = cmds.ls(selection=True, long=True) or []
        if not target or not selected:
            self.status_label.setText("Select Maya objects first")
            return
        objects = self._layers[target].setdefault("objects", [])
        existing = {
            str(item.get("maya_uuid") or item.get("dag_path") or "")
            for item in objects
            if isinstance(item, dict)
        }
        for node in selected:
            uuids = cmds.ls(node, uuid=True) or []
            uuid = str(uuids[0]) if uuids else ""
            key = uuid or str(node)
            if key in existing:
                continue
            objects.append(
                {
                    "name": str(node).rsplit("|", 1)[-1],
                    "maya_uuid": uuid,
                    "dag_path": str(node),
                }
            )
            existing.add(key)
        self._refresh_member_views()
        self._refresh_layer_counts()

    def remove_members(self) -> None:
        layer_name = self.current_layer_name()
        if not layer_name:
            return
        rows = sorted(
            {index.row() for index in self.member_table.selectionModel().selectedRows()},
            reverse=True,
        )
        for row in rows:
            item = self.member_table.item(row, 0)
            payload = dict(item.data(QtCore.Qt.UserRole) or {}) if item else {}
            if payload.get("type") == "cast":
                key = str(payload.get("key") or "")
                self._layers[layer_name]["members"] = [
                    value for value in self._layers[layer_name].get("members", []) if value != key
                ]
            else:
                key = str(payload.get("key") or "")
                self._layers[layer_name]["objects"] = [
                    value
                    for value in self._layers[layer_name].get("objects", [])
                    if str(value.get("maya_uuid") or value.get("dag_path") or "") != key
                ]
        self._refresh_member_views()
        self._refresh_layer_counts()

    def select_members_in_maya(self) -> None:
        if not self.is_maya_session:
            return
        import maya.cmds as cmds

        nodes = []
        for index in self.member_table.selectionModel().selectedRows():
            item = self.member_table.item(index.row(), 0)
            payload = dict(item.data(QtCore.Qt.UserRole) or {}) if item else {}
            if payload.get("type") == "object":
                uuid = str(payload.get("uuid") or "")
                matches = cmds.ls(uuid, long=True) if uuid else []
                path = str(payload.get("path") or "")
                nodes.extend(matches or ([path] if path and cmds.objExists(path) else []))
            else:
                entry = self._cast.get(str(payload.get("key") or "")) or {}
                namespace = str(entry.get("namespace") or "").strip(":")
                nodes.extend(cmds.ls(f"{namespace}:*", long=True) or [])
        if nodes:
            cmds.select(list(dict.fromkeys(nodes)), replace=True)

    def _refresh_member_views(self) -> None:
        self._populate_members()
        self._populate_available_cast()
        layer_name = self.current_layer_name()
        layer = self._layers.get(layer_name) or {}
        display_layer = str(layer.get("display_layer") or layer_name)
        choices = []
        if self.is_maya_session:
            try:
                import maya.cmds as cmds

                choices = [
                    str(value) for value in (cmds.ls(type="displayLayer") or [])
                    if str(value) != "defaultLayer"
                ]
            except Exception:
                choices = []
        self.display_layer_combo.blockSignals(True)
        self.display_layer_combo.clear()
        self.display_layer_combo.addItems(list(dict.fromkeys([display_layer, *choices])))
        self.display_layer_combo.setCurrentText(display_layer)
        self.display_layer_combo.setEnabled(bool(layer_name))
        self.display_layer_combo.blockSignals(False)
        count = len(layer.get("members") or []) + len(layer.get("objects") or [])
        self.info_label.setText(
            f"Selected Layer: {layer_name}\n"
            f"Display Layer: {display_layer}\n"
            f"Member Count: {count}\n"
            "Cast Unique: Yes"
        )

    def _display_layer_changed(self, value: str) -> None:
        layer_name = self.current_layer_name()
        if not layer_name:
            return
        self._layers.setdefault(layer_name, {})["display_layer"] = str(value).strip() or layer_name

    def _populate_members(self) -> None:
        self.member_table.setRowCount(0)
        layer = self._layers.get(self.current_layer_name()) or {}
        for cast_key in layer.get("members") or []:
            entry = self._cast.get(cast_key) or {}
            values = (
                "Cast",
                cast_key,
                entry.get("asset", ""),
                entry.get("variant", ""),
                entry.get("role", ""),
                entry.get("namespace", ""),
                "In Sync" if entry else "Missing",
            )
            self._append_table_row(
                self.member_table,
                values,
                {"type": "cast", "key": cast_key},
            )
        for obj in layer.get("objects") or []:
            path = str(obj.get("dag_path") or "")
            exists = False
            if self.is_maya_session:
                import maya.cmds as cmds

                exists = bool(cmds.objExists(path) or (obj.get("maya_uuid") and cmds.ls(obj["maya_uuid"])))
            values = (
                "Object",
                obj.get("name", ""),
                path,
                "-",
                "Scene Object",
                "-",
                "In Sync" if exists else "Missing",
            )
            self._append_table_row(
                self.member_table,
                values,
                {
                    "type": "object",
                    "key": str(obj.get("maya_uuid") or path),
                    "uuid": str(obj.get("maya_uuid") or ""),
                    "path": path,
                },
            )
        self.member_table.resizeColumnsToContents()

    def _populate_available_cast(self) -> None:
        query = self.cast_search.text().strip().lower()
        self.cast_table.setRowCount(0)
        for cast_key, entry in sorted(self._cast.items(), key=lambda item: item[0].lower()):
            assigned = self._cast_assignment(cast_key)
            if assigned:
                continue
            haystack = " ".join(
                str(value) for value in (cast_key, entry.get("asset"), entry.get("variant"), entry.get("role"))
            ).lower()
            if query and query not in haystack:
                continue
            values = (
                cast_key,
                entry.get("asset", ""),
                entry.get("variant", ""),
                entry.get("role", ""),
                entry.get("namespace", ""),
                assigned or "-",
            )
            row = self._append_table_row(
                self.cast_table,
                values,
                {"cast_key": cast_key},
            )
        self.cast_table.resizeColumnsToContents()

    @staticmethod
    def _append_table_row(table, values, payload) -> int:
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(str(value))
            if column == 0:
                item.setData(QtCore.Qt.UserRole, payload)
            table.setItem(row, column, item)
        return row

    def _refresh_layer_counts(self) -> None:
        for index in range(self.layer_list.count()):
            self._set_layer_item_text(self.layer_list.item(index))

    def save(self) -> None:
        if not self.identity:
            return
        try:
            ordered = {}
            for index in range(self.layer_list.count()):
                name = str(self.layer_list.item(index).data(QtCore.Qt.UserRole) or "")
                layer = deepcopy(self._layers.get(name) or {})
                layer["order"] = index * 10
                ordered[name] = layer
            self._layers = ordered
            composition_path, layers_path = self.service.publish_review_definitions(
                self.identity,
                self._layers,
                department=self.department,
                comment="Published from Review Layer Manager",
            )
            self.status_label.setText(
                "Published Definitions: "
                f"Composition {composition_path.parent.name} / "
                f"Layers {layers_path.parent.name}"
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Failed", str(exc))

    def create_review_layers(self) -> None:
        if not self.is_maya_session or not self.identity:
            return
        try:
            self.save()
            from smartlib.dcc.maya.shot_builder import create_review_display_layers

            contract = self.service.load_cast(self.identity)
            contract["review_layers"] = self.service.review_layers(
                self.identity,
                self.department,
            )
            result = create_review_display_layers(contract)
            summary = ", ".join(f"{name}: {count}" for name, count in sorted(result.items()))
            self.status_label.setText(f"Synced Maya Display Layers: {summary}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Sync Display Layers Failed", str(exc))


_WINDOW = None


def show(identity=None, config_dir=None, department=None, parent=None):
    global _WINDOW
    try:
        _WINDOW.close()
    except Exception:
        pass
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _ensure_smartlib_on_path()
    from smartlib.core.qt import parent_for_maya

    window_parent = parent_for_maya(QtWidgets, parent)
    _WINDOW = ReviewLayerWindow(
        identity=identity,
        config_dir=config_dir,
        department=department,
        parent=window_parent,
    )
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    show()
    sys.exit(app.exec())
