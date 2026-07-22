from __future__ import annotations

import os
from pathlib import Path

from smartlib.dcc.maya.car_system import ImportOptions


def _qt_modules():
    try:
        from PySide6 import QtCore, QtWidgets

        return QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets

        return QtCore, QtWidgets


QtCore, QtWidgets = _qt_modules()


class SmartCarSystemWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("Smart CarSystem")
        self.resize(470, 455)

        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self.setCentralWidget(central)

        self.tabs = QtWidgets.QTabWidget()
        self.import_tab = QtWidgets.QWidget()
        self.spec_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.import_tab, "Animation JSON")
        self.tabs.addTab(self.spec_tab, "Vehicle Spec")
        root.addWidget(self.tabs, 1)

        self._build_import_tab()
        self._build_spec_tab()

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self._apply_style()

    def _build_import_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.import_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        file_row = QtWidgets.QHBoxLayout()
        self.anim_json_path = QtWidgets.QLineEdit()
        self.anim_json_path.setPlaceholderText("car_locator_anim.json")
        self.anim_browse_btn = QtWidgets.QPushButton("Browse")
        file_row.addWidget(self.anim_json_path, 1)
        file_row.addWidget(self.anim_browse_btn)
        layout.addLayout(file_row)

        options = QtWidgets.QGridLayout()
        options.setHorizontalSpacing(6)
        options.setVerticalSpacing(4)

        self.use_json_settings_check = QtWidgets.QCheckBox("Use JSON Maya Export Settings")
        self.use_json_settings_check.setChecked(True)
        options.addWidget(self.use_json_settings_check, 0, 0, 1, 2)

        self.create_validation_check = QtWidgets.QCheckBox("Create Validation Locators")
        self.create_validation_check.setChecked(True)
        options.addWidget(self.create_validation_check, 1, 0, 1, 2)

        self.parent_validation_check = QtWidgets.QCheckBox("Parent Validation Locators")
        self.parent_validation_check.setChecked(True)
        options.addWidget(self.parent_validation_check, 2, 0, 1, 2)

        self.key_controllers_check = QtWidgets.QCheckBox("Key Controllers")
        self.key_controllers_check.setChecked(False)
        options.addWidget(self.key_controllers_check, 3, 0, 1, 2)

        options.addWidget(QtWidgets.QLabel("Prefix"), 4, 0)
        self.prefix_edit = QtWidgets.QLineEdit("hda_")
        options.addWidget(self.prefix_edit, 4, 1)

        options.addWidget(QtWidgets.QLabel("Translate Scale"), 5, 0)
        self.translate_scale_spin = QtWidgets.QDoubleSpinBox()
        self.translate_scale_spin.setRange(0.000001, 1000000.0)
        self.translate_scale_spin.setDecimals(6)
        self.translate_scale_spin.setValue(100.0)
        self.translate_scale_spin.setSingleStep(1.0)
        options.addWidget(self.translate_scale_spin, 5, 1)
        layout.addLayout(options)

        self.import_btn = QtWidgets.QPushButton("Import Animation JSON")
        layout.addWidget(self.import_btn)
        layout.addStretch(1)

        self.anim_browse_btn.clicked.connect(self.browse_anim_json)
        self.import_btn.clicked.connect(self.import_animation_json)
        self.anim_json_path.editingFinished.connect(self.load_json_settings)
        self.use_json_settings_check.toggled.connect(self._sync_option_enabled)
        self._sync_option_enabled()

    def _build_spec_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.spec_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        file_row = QtWidgets.QHBoxLayout()
        self.spec_json_path = QtWidgets.QLineEdit()
        self.spec_json_path.setPlaceholderText("vehicle_spec.json")
        self.spec_browse_btn = QtWidgets.QPushButton("Browse")
        file_row.addWidget(self.spec_json_path, 1)
        file_row.addWidget(self.spec_browse_btn)
        layout.addLayout(file_row)

        controller_group = QtWidgets.QGroupBox("Controller Mapping")
        controller_layout = QtWidgets.QGridLayout(controller_group)
        controller_layout.setHorizontalSpacing(6)
        controller_layout.setVerticalSpacing(4)
        self.controller_edits = {}
        self.roll_attr_edits = {}
        self.steer_attr_edits = {}
        from smartlib.dcc.maya.car_system import (
            LOCATOR_NAMES,
            default_controller_nodes,
            default_controller_roll_attrs,
            default_controller_steer_attrs,
        )

        defaults = default_controller_nodes()
        roll_defaults = default_controller_roll_attrs()
        steer_defaults = default_controller_steer_attrs()
        controller_layout.addWidget(QtWidgets.QLabel("Locator"), 0, 0)
        controller_layout.addWidget(QtWidgets.QLabel("Controller"), 0, 1)
        controller_layout.addWidget(QtWidgets.QLabel("Roll Attr"), 0, 2)
        controller_layout.addWidget(QtWidgets.QLabel("Steer Attr"), 0, 3)
        for row, name in enumerate(LOCATOR_NAMES):
            ui_row = row + 1
            controller_layout.addWidget(QtWidgets.QLabel(name), ui_row, 0)
            edit = QtWidgets.QLineEdit(defaults.get(name, ""))
            self.controller_edits[name] = edit
            controller_layout.addWidget(edit, ui_row, 1)
            roll_edit = QtWidgets.QLineEdit(roll_defaults.get(name, ""))
            roll_edit.setPlaceholderText("rotateX / -rotateX")
            roll_edit.setEnabled(name in roll_defaults)
            self.roll_attr_edits[name] = roll_edit
            controller_layout.addWidget(roll_edit, ui_row, 2)
            steer_edit = QtWidgets.QLineEdit(steer_defaults.get(name, ""))
            steer_edit.setPlaceholderText("rotateY / -rotateY")
            steer_edit.setEnabled(name in steer_defaults)
            self.steer_attr_edits[name] = steer_edit
            controller_layout.addWidget(steer_edit, ui_row, 3)
        layout.addWidget(controller_group)

        options = QtWidgets.QGridLayout()
        self.spec_key_controllers_check = QtWidgets.QCheckBox("Key Controllers From Houdini JSON")
        self.spec_key_controllers_check.setChecked(True)
        options.addWidget(self.spec_key_controllers_check, 0, 0, 1, 2)
        options.addWidget(QtWidgets.QLabel("Maya Translate Scale"), 1, 0)
        self.spec_translate_scale_spin = QtWidgets.QDoubleSpinBox()
        self.spec_translate_scale_spin.setRange(0.000001, 1000000.0)
        self.spec_translate_scale_spin.setDecimals(6)
        self.spec_translate_scale_spin.setValue(100.0)
        self.spec_translate_scale_spin.setSingleStep(1.0)
        options.addWidget(self.spec_translate_scale_spin, 1, 1)
        options.addWidget(QtWidgets.QLabel("Wheel Roll Multiplier"), 2, 0)
        self.spec_wheel_roll_multiplier_spin = QtWidgets.QDoubleSpinBox()
        self.spec_wheel_roll_multiplier_spin.setRange(-1000000.0, 1000000.0)
        self.spec_wheel_roll_multiplier_spin.setDecimals(8)
        self.spec_wheel_roll_multiplier_spin.setValue(0.00277778)
        self.spec_wheel_roll_multiplier_spin.setSingleStep(0.0001)
        options.addWidget(self.spec_wheel_roll_multiplier_spin, 2, 1)
        layout.addLayout(options)

        self.export_spec_btn = QtWidgets.QPushButton("Export Vehicle Spec JSON")
        layout.addWidget(self.export_spec_btn)
        layout.addStretch(1)

        self.spec_browse_btn.clicked.connect(self.browse_spec_json)
        self.export_spec_btn.clicked.connect(self.export_vehicle_spec)

    def browse_anim_json(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import Car Locator Animation JSON",
            self._default_dir(),
            "JSON Files (*.json)",
        )
        if path:
            self.anim_json_path.setText(path)
            self.load_json_settings()

    def browse_spec_json(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Vehicle Spec JSON",
            self._default_dir(),
            "JSON Files (*.json)",
        )
        if path:
            self.spec_json_path.setText(path)

    def load_json_settings(self) -> None:
        path = self.anim_json_path.text().strip()
        if not path or not Path(path).exists():
            return
        try:
            from smartlib.dcc.maya.car_system import json_maya_export_settings

            settings = json_maya_export_settings(path)
            self.create_validation_check.setChecked(bool(settings.get("create_validation_locators", True)))
            self.parent_validation_check.setChecked(bool(settings.get("parent_validation_locators", True)))
            self.key_controllers_check.setChecked(bool(settings.get("key_controllers", False)))
            self.prefix_edit.setText(str(settings.get("validation_locator_prefix", "hda_")))
            self.translate_scale_spin.setValue(float(settings.get("translate_scale", 100.0)))
            self.status_label.setText("Loaded JSON settings.")
        except Exception as exc:
            self.status_label.setText(str(exc))

    def import_animation_json(self) -> None:
        path = self.anim_json_path.text().strip()
        if not path:
            QtWidgets.QMessageBox.warning(self, "Import Animation JSON", "Choose a JSON file.")
            return
        try:
            from smartlib.dcc.maya import car_system

            use_json_settings = self.use_json_settings_check.isChecked()
            options = ImportOptions(
                use_json_settings=use_json_settings,
                create_validation_locators=None if use_json_settings else self.create_validation_check.isChecked(),
                validation_locator_prefix=None if use_json_settings else self.prefix_edit.text(),
                translate_scale=None if use_json_settings else self.translate_scale_spin.value(),
                parent_validation_locators=None if use_json_settings else self.parent_validation_check.isChecked(),
                key_controllers=None if use_json_settings else self.key_controllers_check.isChecked(),
            )
            result = car_system.import_car_locator_anim_json(path, options)
            self.status_label.setText(
                "Imported {frame_count} frame(s), scale {translate_scale:g}, roll keys {keyed_roll_attr_count}.".format(**result)
            )
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.warning(self, "Import Animation JSON", str(exc))

    def export_vehicle_spec(self) -> None:
        path = self.spec_json_path.text().strip()
        if not path:
            QtWidgets.QMessageBox.warning(self, "Export Vehicle Spec JSON", "Choose an output path.")
            return
        try:
            from smartlib.dcc.maya import car_system

            controller_nodes = {name: edit.text().strip() for name, edit in self.controller_edits.items()}
            roll_attrs = {name: edit.text().strip() for name, edit in self.roll_attr_edits.items()}
            steer_attrs = {name: edit.text().strip() for name, edit in self.steer_attr_edits.items()}
            result = car_system.export_vehicle_spec_json(
                path,
                controller_nodes,
                roll_attrs,
                steer_attrs,
                key_controllers=self.spec_key_controllers_check.isChecked(),
                translate_scale=self.spec_translate_scale_spin.value(),
                wheel_roll_multiplier=self.spec_wheel_roll_multiplier_spin.value(),
            )
            self.status_label.setText(
                "Exported {locator_count} controller spec point(s), unit {linear_unit}: {path}".format(**result)
            )
        except Exception as exc:
            self.status_label.setText(str(exc))
            QtWidgets.QMessageBox.warning(self, "Export Vehicle Spec JSON", str(exc))

    def _sync_option_enabled(self) -> None:
        enabled = not self.use_json_settings_check.isChecked()
        for widget in (
            self.create_validation_check,
            self.parent_validation_check,
            self.key_controllers_check,
            self.prefix_edit,
            self.translate_scale_spin,
        ):
            widget.setEnabled(enabled)

    def _default_dir(self) -> str:
        workspace = os.environ.get("WORKSPACE") or os.environ.get("MAYA_PROJECT")
        if workspace:
            return workspace
        return str(Path.home() / "Desktop")

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #3f3f3f;
                color: #dddddd;
                font-size: 11px;
            }
            QLabel#StatusLabel {
                color: #c7d5e0;
                padding-top: 2px;
            }
            QPushButton {
                background: #5b5b5b;
                border: 1px solid #343434;
                color: #f1f1f1;
                min-height: 24px;
                padding: 3px 8px;
            }
            QPushButton:hover {
                background: #666666;
            }
            QLineEdit, QDoubleSpinBox {
                background: #333333;
                border: 1px solid #2b2b2b;
                color: #e8f2ff;
                padding: 3px;
            }
            QTabWidget::pane {
                border: 1px solid #303030;
            }
            QTabBar::tab {
                background: #4a4a4a;
                border: 1px solid #303030;
                padding: 5px 9px;
            }
            QTabBar::tab:selected {
                background: #5a5a5a;
            }
            """
        )


_WINDOW = None


def show(parent=None):
    global _WINDOW
    if _WINDOW is not None:
        try:
            _WINDOW.close()
        except Exception:
            pass

    from smartlib.core.qt import parent_for_maya

    window_parent = parent_for_maya(QtWidgets, parent)
    _WINDOW = SmartCarSystemWindow(parent=window_parent)
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW
