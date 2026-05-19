from __future__ import annotations

import os
import sys
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
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[1])
    return root / "config" / "STKB"


def _service(config_dir: str | os.PathLike[str] | None = None):
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
    COLUMNS = [
        "Layer",
        "Members",
        "Camera",
        "Camera Version",
        "Width",
        "Height",
        "Order",
        "threeDLayer",
        "FrameRange",
        "Take",
        "AE Slot",
    ]

    def __init__(
        self,
        identity=None,
        config_dir: str | os.PathLike[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.service, self.identity_cls = _service(config_dir)
        self.identity = identity
        self.fixed_identity = identity is not None
        self.is_maya_session = _is_maya_session()
        self.setWindowTitle("Review Layer Manager")
        self.resize(780, 560)
        self._build_ui()
        if self.identity is None:
            self._populate_shot_combo()
        else:
            self._set_identity_fields(self.identity)
        self.refresh()

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(4)

        shot_layout = QtWidgets.QHBoxLayout()
        shot_layout.setSpacing(4)
        self.shot_combo = QtWidgets.QComboBox()
        self.episode_edit = QtWidgets.QLineEdit()
        self.sequence_edit = QtWidgets.QLineEdit()
        self.shot_edit = QtWidgets.QLineEdit()
        for widget in (self.episode_edit, self.sequence_edit, self.shot_edit):
            widget.setReadOnly(True)
        shot_layout.addWidget(QtWidgets.QLabel("PROJ"))
        shot_layout.addWidget(QtWidgets.QLabel(self.service.project_config.project_name))
        shot_layout.addWidget(QtWidgets.QLabel("EP"))
        shot_layout.addWidget(self.episode_edit)
        shot_layout.addWidget(QtWidgets.QLabel("SEQ"))
        shot_layout.addWidget(self.sequence_edit)
        shot_layout.addWidget(QtWidgets.QLabel("SHOT"))
        shot_layout.addWidget(self.shot_edit)
        shot_layout.addWidget(self.shot_combo, 1)
        root_layout.addLayout(shot_layout)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(4)
        self.add_btn = QtWidgets.QPushButton("Add")
        self.duplicate_btn = QtWidgets.QPushButton("Duplicate")
        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        button_layout.addWidget(self.add_btn)
        button_layout.addWidget(self.duplicate_btn)
        button_layout.addWidget(self.delete_btn)
        button_layout.addStretch(1)
        button_layout.addWidget(self.refresh_btn)
        root_layout.addLayout(button_layout)

        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        root_layout.addWidget(self.table, 1)

        form_group = QtWidgets.QGroupBox("Properties")
        form = QtWidgets.QFormLayout(form_group)
        form.setContentsMargins(6, 6, 6, 6)
        form.setSpacing(4)
        self.layer_edit = QtWidgets.QLineEdit()
        self.members_edit = QtWidgets.QLineEdit()
        self.camera_edit = QtWidgets.QLineEdit()
        self.camera_version_edit = QtWidgets.QLineEdit("latest")
        self.width_spin = QtWidgets.QSpinBox()
        self.width_spin.setRange(1, 16384)
        self.width_spin.setValue(960)
        self.height_spin = QtWidgets.QSpinBox()
        self.height_spin.setRange(1, 16384)
        self.height_spin.setValue(540)
        self.order_spin = QtWidgets.QSpinBox()
        self.order_spin.setRange(-999, 999)
        self.three_d_check = QtWidgets.QCheckBox("ON")
        self.frame_range_combo = QtWidgets.QComboBox()
        self.frame_range_combo.addItems(["Animation", "Editorial", "Custom"])
        self.take_spin = QtWidgets.QSpinBox()
        self.take_spin.setRange(1, 999)
        self.take_spin.setValue(1)
        self.ae_slot_edit = QtWidgets.QLineEdit()
        form.addRow("Layer", self.layer_edit)
        form.addRow("Members", self.members_edit)
        form.addRow("Camera", self.camera_edit)
        form.addRow("Camera Version", self.camera_version_edit)
        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("Order", self.order_spin)
        form.addRow("threeD Layer", self.three_d_check)
        form.addRow("Frame Range", self.frame_range_combo)
        form.addRow("Take", self.take_spin)
        form.addRow("AE Slot", self.ae_slot_edit)
        root_layout.addWidget(form_group)

        action_layout = QtWidgets.QHBoxLayout()
        self.save_btn = QtWidgets.QPushButton("Save Contract")
        self.create_layers_btn = QtWidgets.QPushButton("Create Review Layers")
        if not self.is_maya_session:
            self.create_layers_btn.setEnabled(False)
            self.create_layers_btn.setToolTip("Available inside Maya.")
        action_layout.addWidget(self.save_btn)
        action_layout.addWidget(self.create_layers_btn)
        root_layout.addLayout(action_layout)

        self.status_label = QtWidgets.QLabel("")
        root_layout.addWidget(self.status_label)

        self.shot_combo.currentIndexChanged.connect(self._on_shot_combo_changed)
        self.add_btn.clicked.connect(self.add_layer)
        self.duplicate_btn.clicked.connect(self.duplicate_layer)
        self.delete_btn.clicked.connect(self.delete_layer)
        self.refresh_btn.clicked.connect(self.refresh)
        self.save_btn.clicked.connect(self.save)
        self.create_layers_btn.clicked.connect(self.create_review_layers)
        self.table.currentCellChanged.connect(lambda *_args: self._load_selected_row_to_form())
        for widget in (
            self.layer_edit,
            self.members_edit,
            self.camera_edit,
            self.camera_version_edit,
            self.ae_slot_edit,
        ):
            widget.editingFinished.connect(self._apply_form_to_selected_row)
        for widget in (self.width_spin, self.height_spin, self.order_spin, self.take_spin):
            widget.valueChanged.connect(lambda _value: self._apply_form_to_selected_row())
        self.three_d_check.stateChanged.connect(lambda _state: self._apply_form_to_selected_row())
        self.frame_range_combo.currentTextChanged.connect(lambda _text: self._apply_form_to_selected_row())

    def _populate_shot_combo(self) -> None:
        self.shot_combo.blockSignals(True)
        self.shot_combo.clear()
        for identity in self.service.list_shots():
            self.shot_combo.addItem(identity.code, identity)
        self.shot_combo.blockSignals(False)
        if self.shot_combo.count():
            self.identity = self.shot_combo.itemData(0)
            self._set_identity_fields(self.identity)

    def _on_shot_combo_changed(self) -> None:
        identity = self.shot_combo.currentData()
        if identity:
            self.identity = identity
            self._set_identity_fields(identity)
            self.refresh()

    def _set_identity_fields(self, identity) -> None:
        self.episode_edit.setText(identity.episode)
        self.sequence_edit.setText(identity.sequence)
        self.shot_edit.setText(identity.shot)
        self.shot_combo.setVisible(not self.fixed_identity)

    def refresh(self) -> None:
        if not self.identity:
            self.table.setRowCount(0)
            return
        self.table.setRowCount(0)
        for row_data in self.service.review_layer_rows(self.identity):
            self._append_row(row_data)
        if self.table.rowCount():
            self.table.setCurrentCell(0, 0)
        self.table.resizeColumnsToContents()
        self.status_label.setText(f"{self.table.rowCount()} review layers")

    def add_layer(self) -> None:
        name = self._unique_layer_name("NEW")
        self._append_row(
            {
                "layer": name,
                "members": "",
                "camera": "",
                "camera_publish": "latest",
                "width": 960,
                "height": 540,
                "order": 0,
                "three_d_layer": False,
                "frame_range": "Animation",
                "take": 1,
                "ae_slot": name,
            }
        )
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)

    def duplicate_layer(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        data = self._row_data(row)
        data["layer"] = self._unique_layer_name(f"{data['layer']}_COPY")
        self._append_row(data)
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)

    def delete_layer(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def save(self) -> None:
        if not self.identity:
            return
        try:
            self.service.write_review_layers(self.identity, self._review_layers_from_table())
            self.status_label.setText("Saved review layer contract")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Contract Failed", str(exc))

    def create_review_layers(self) -> None:
        if not self.identity:
            return
        if not self.is_maya_session:
            QtWidgets.QMessageBox.information(self, "Create Review Layers", "Create Review Layers is available inside Maya.")
            return
        try:
            self.save()
            _ensure_smartlib_on_path()
            from smartlib.dcc.maya.shot_builder import create_review_display_layers

            result = create_review_display_layers(self.service.load_cast(self.identity))
            summary = ", ".join(f"{name}: {count}" for name, count in sorted(result.items()))
            self.status_label.setText(f"Created review layers: {summary}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Create Review Layers Failed", str(exc))

    def _append_row(self, data: dict) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [
            data.get("layer", ""),
            data.get("members", ""),
            data.get("camera", ""),
            data.get("camera_publish", ""),
            data.get("width", ""),
            data.get("height", ""),
            data.get("order", ""),
            "true" if data.get("three_d_layer") else "false",
            data.get("frame_range", "Animation"),
            data.get("take", 1),
            data.get("ae_slot", ""),
        ]
        for column, value in enumerate(values):
            self.table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))

    def _load_selected_row_to_form(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        data = self._row_data(row)
        self.layer_edit.setText(data["layer"])
        self.members_edit.setText(data["members"])
        self.camera_edit.setText(data["camera"])
        self.camera_version_edit.setText(data["camera_publish"])
        self.width_spin.setValue(_int_or(data["width"], 960))
        self.height_spin.setValue(_int_or(data["height"], 540))
        self.order_spin.setValue(_int_or(data["order"], 0))
        self.three_d_check.setChecked(_bool_text(data["three_d_layer"]))
        index = self.frame_range_combo.findText(data["frame_range"])
        self.frame_range_combo.setCurrentIndex(index if index >= 0 else 0)
        self.take_spin.setValue(_int_or(data["take"], 1))
        self.ae_slot_edit.setText(data["ae_slot"])

    def _apply_form_to_selected_row(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        values = [
            self.layer_edit.text().strip(),
            self.members_edit.text().strip(),
            self.camera_edit.text().strip(),
            self.camera_version_edit.text().strip() or "latest",
            str(self.width_spin.value()),
            str(self.height_spin.value()),
            str(self.order_spin.value()),
            "true" if self.three_d_check.isChecked() else "false",
            self.frame_range_combo.currentText(),
            str(self.take_spin.value()),
            self.ae_slot_edit.text().strip(),
        ]
        for column, value in enumerate(values):
            item = self.table.item(row, column)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.table.setItem(row, column, item)
            item.setText(value)

    def _review_layers_from_table(self) -> dict:
        review_layers = {}
        for row in range(self.table.rowCount()):
            data = self._row_data(row)
            layer = data["layer"].strip().upper()
            if not layer:
                continue
            members = [item.strip() for item in data["members"].split(",") if item.strip()]
            review_layers[layer] = {
                "members": members,
                "order": _int_or(data["order"], 0),
                "three_d_layer": _bool_text(data["three_d_layer"]),
                "frame_range": data["frame_range"] or "Animation",
                "take": _int_or(data["take"], 1),
                "camera": {
                    "publish_type": "camera",
                    "version": data["camera_publish"] or "latest",
                    "name": data["camera"],
                },
                "resolution": {
                    "width": _int_or(data["width"], 960),
                    "height": _int_or(data["height"], 540),
                    "scale": 1.0,
                },
                "ae": {
                    "comp_name": data["ae_slot"] or layer,
                    "template_slot": data["ae_slot"] or layer,
                    "blend_mode": "normal",
                },
            }
        return review_layers

    def _row_data(self, row: int) -> dict:
        return {
            "layer": self._table_text(row, 0),
            "members": self._table_text(row, 1),
            "camera": self._table_text(row, 2),
            "camera_publish": self._table_text(row, 3),
            "width": self._table_text(row, 4),
            "height": self._table_text(row, 5),
            "order": self._table_text(row, 6),
            "three_d_layer": self._table_text(row, 7),
            "frame_range": self._table_text(row, 8),
            "take": self._table_text(row, 9),
            "ae_slot": self._table_text(row, 10),
        }

    def _table_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text().strip() if item else ""

    def _unique_layer_name(self, base: str) -> str:
        existing = {self._table_text(row, 0).upper() for row in range(self.table.rowCount())}
        candidate = base.upper()
        if candidate not in existing:
            return candidate
        index = 1
        while f"{candidate}_{index:02d}" in existing:
            index += 1
        return f"{candidate}_{index:02d}"


def _int_or(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_text(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


_WINDOW = None


def show(identity=None, config_dir: str | os.PathLike[str] | None = None, parent=None):
    global _WINDOW
    try:
        _WINDOW.close()
    except Exception:
        pass
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _WINDOW = ReviewLayerWindow(identity=identity, config_dir=config_dir, parent=parent)
    _WINDOW.show()
    return _WINDOW


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    show()
    sys.exit(app.exec())
