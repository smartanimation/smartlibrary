from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from smartlib.core.config_loader import ProjectConfig


def _qt_modules():
    try:
        from PySide6 import QtCore, QtWidgets

        return QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets

        return QtCore, QtWidgets


QtCore, QtWidgets = _qt_modules()


def _default_config_dir() -> Path:
    env_path = os.environ.get("PROJECT_CONFIG_DIR")
    if env_path:
        return Path(env_path)
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[4])
    return root / "config" / "STKB"


class CollapsibleSection(QtWidgets.QWidget):
    def __init__(self, title: str, content: QtWidgets.QWidget, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.toggle_button = QtWidgets.QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)

        self.content = content
        self.content.setVisible(expanded)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content)

        self.toggle_button.toggled.connect(self._set_expanded)

    def _set_expanded(self, expanded: bool) -> None:
        self.content.setVisible(expanded)
        self.toggle_button.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)


class MayaLayoutPanelWindow(QtWidgets.QMainWindow):
    CAMERA_MODES = ("all", "selected", "active", "pattern")
    FILM_FIT_VALUES = (("Fill", 0), ("Horizontal", 1), ("Vertical", 2), ("Overscan", 3))
    LENS_VALUES = (12, 24, 35, 40, 50, 85, 125, 180, 250)
    FSTOP_VALUES = (1.0, 1.4, 2.8, 5.6, 16, 22)

    def __init__(self, config_dir: str | os.PathLike[str] | None = None, parent=None):
        super().__init__(parent)
        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.camera_mode = "selected"
        self.active_camera_snapshot = ""
        self._maya_script_jobs = []
        self._active_camera_timer = None
        self._build_ui()
        self._install_active_camera_watchers()

    def _build_ui(self) -> None:
        self.setWindowTitle(f"MAYA Layout Panel - {self.project_config.project_name}")
        self.resize(270, 620)

        central = QtWidgets.QWidget()
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(5, 5, 5, 5)
        root_layout.setSpacing(4)

        title = QtWidgets.QLabel("MAYA Layout Panel")
        title.setObjectName("PanelTitle")
        root_layout.addWidget(title)

        root_layout.addWidget(self._mode_bar())

        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_content = QtWidgets.QWidget()
        self.section_layout = QtWidgets.QVBoxLayout(scroll_content)
        self.section_layout.setContentsMargins(0, 0, 0, 0)
        self.section_layout.setSpacing(5)
        scroll_area.setWidget(scroll_content)
        root_layout.addWidget(scroll_area, 1)

        self.status_label = QtWidgets.QLabel("Wildcard Select: Enter a wildcard pattern such as *_geo or cam_?.")
        self.status_label.setObjectName("StatusLabel")
        root_layout.addWidget(self.status_label)
        self.setCentralWidget(central)

        self._add_section("Viewport Settings", self._viewport_section())
        self._add_section("Camera Settings", self._camera_section())
        self._add_section("Smart Shot", self._smart_shot_section())
        self._add_section("Lens Settings", self._lens_section())
        self._add_section("F-stop", self._fstop_section())
        self.section_layout.addStretch(1)

        self._apply_style()

    def _add_section(self, title: str, content: QtWidgets.QWidget) -> None:
        self.section_layout.addWidget(CollapsibleSection(title, content))

    def _mode_bar(self) -> QtWidgets.QWidget:
        widget = self._content_widget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        mode_layout = QtWidgets.QHBoxLayout()
        mode_layout.setSpacing(8)
        self.mode_buttons = {}
        for label, mode in (("All Cameras", "all"), ("Selected", "selected"), ("Active", "active"), ("Pattern", "pattern")):
            button = QtWidgets.QRadioButton(label)
            button.setChecked(mode == self.camera_mode)
            button.toggled.connect(lambda checked, value=mode: self.set_camera_mode(value) if checked else None)
            self.mode_buttons[mode] = button
            mode_layout.addWidget(button)
        mode_layout.addStretch(1)
        layout.addLayout(mode_layout)

        pattern_layout = QtWidgets.QHBoxLayout()
        pattern_layout.setSpacing(4)
        pattern_layout.addWidget(QtWidgets.QLabel("Pattern"))
        self.pattern_edit = QtWidgets.QLineEdit("*_geo, cam_?")
        pattern_layout.addWidget(self.pattern_edit, 1)
        self.pattern_edit.returnPressed.connect(self.match_wildcard)
        layout.addLayout(pattern_layout)

        active_layout = QtWidgets.QHBoxLayout()
        active_layout.setSpacing(4)
        active_layout.addWidget(QtWidgets.QLabel("Active Camera"))
        self.active_camera_label = QtWidgets.QLabel("-")
        self.active_camera_label.setObjectName("FieldValue")
        active_layout.addWidget(self.active_camera_label, 1)
        active_layout.addWidget(self._button("Refresh", self.refresh_active_camera_label))
        layout.addLayout(active_layout)
        self.refresh_active_camera_label()
        return widget

    def _viewport_section(self) -> QtWidgets.QWidget:
        widget = self._content_widget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(4)
        button_layout.addWidget(self._toggle_button("Lights", self.toggle_lights))
        button_layout.addWidget(self._toggle_button("Texture", self.toggle_textures))
        button_layout.addWidget(self._toggle_button("DOF", self.toggle_dof))
        layout.addLayout(button_layout)
        return widget

    def _camera_section(self) -> QtWidgets.QWidget:
        widget = self._content_widget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(3)
        self._add_on_off_row(
            grid,
            0,
            "Film Resolution Gate",
            lambda enabled: self.apply_camera_bool("Resolution Gate", "set_resolution_gate", enabled),
        )
        self._add_on_off_row(
            grid,
            1,
            "Display Gate Mask",
            lambda enabled: self.apply_camera_bool("Gate Mask", "set_gate_mask", enabled),
        )
        self._add_film_fit_row(grid, 2)
        self.overscan_spin = self._add_spin_row(grid, 3, "Display Overscan", 1.0, 0.01, 10.0, 3, "set_display_overscan")
        self.near_clip_spin = self._add_spin_row(grid, 4, "Near Clip Plane", 0.1, 0.001, 100000.0, 3, "set_near_clip")
        self.far_clip_spin = self._add_spin_row(grid, 5, "Far Clip Plane", 10000.0, 1.0, 10000000.0, 3, "set_far_clip")
        layout.addLayout(grid)
        return widget

    def _smart_shot_section(self) -> QtWidgets.QWidget:
        widget = self._content_widget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        gate_layout = QtWidgets.QHBoxLayout()
        gate_layout.setSpacing(4)
        gate_layout.addWidget(self._button("Generate SmartGateGuide", self.generate_smart_gate_guide))
        gate_layout.addWidget(self._button("Select", self.select_smart_gate_guides))
        layout.addLayout(gate_layout)

        self.pip_btn = self._button("Picture in Picture", self.toggle_picture_in_picture, pass_checked=True)
        self.pip_btn.setCheckable(True)
        layout.addWidget(self.pip_btn)

        guide_grid = QtWidgets.QGridLayout()
        guide_grid.setHorizontalSpacing(4)
        guide_grid.setVerticalSpacing(2)
        self._add_on_off_row(
            guide_grid,
            0,
            "Show Resolution Gate",
            lambda enabled: self.set_guide_attr("showResolutionGate", enabled),
        )
        self._add_on_off_row(
            guide_grid,
            1,
            "Show Center Line",
            lambda enabled: self.set_guide_attr("showCenterLine", enabled),
        )
        self._add_on_off_row(
            guide_grid,
            2,
            "Show Rule Of Thirds",
            lambda enabled: self.set_guide_attr("showRuleOfThirds", enabled),
        )
        self._add_on_off_row(
            guide_grid,
            3,
            "Show Diagonal Cross",
            lambda enabled: self.set_guide_attr("showDiagonalCross", enabled),
            default_on=False,
        )
        layout.addLayout(guide_grid)

        alpha_layout = QtWidgets.QHBoxLayout()
        alpha_layout.addWidget(QtWidgets.QLabel("imagePlane alphaGain"))
        self.alpha_gain_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.alpha_gain_slider.setRange(0, 100)
        self.alpha_gain_slider.setValue(100)
        self.alpha_gain_spin = QtWidgets.QDoubleSpinBox()
        self.alpha_gain_spin.setRange(0.0, 1.0)
        self.alpha_gain_spin.setDecimals(2)
        self.alpha_gain_spin.setSingleStep(0.05)
        self.alpha_gain_spin.setValue(1.0)
        alpha_layout.addWidget(self.alpha_gain_slider, 1)
        alpha_layout.addWidget(self.alpha_gain_spin)
        layout.addLayout(alpha_layout)
        self.alpha_gain_slider.valueChanged.connect(self.set_image_plane_alpha_gain_from_slider)
        self.alpha_gain_spin.valueChanged.connect(self.set_image_plane_alpha_gain_from_spin)
        return widget

    def _lens_section(self) -> QtWidgets.QWidget:
        widget = self._content_widget()
        layout = QtWidgets.QGridLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        for index, value in enumerate(self.LENS_VALUES):
            layout.addWidget(self._button(f"{value} mm", lambda checked=False, lens=value: self.set_lens(lens)), index // 3, index % 3)
        return widget

    def _fstop_section(self) -> QtWidgets.QWidget:
        widget = self._content_widget()
        layout = QtWidgets.QGridLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        for index, value in enumerate(self.FSTOP_VALUES):
            label = f"f{value:g}"
            layout.addWidget(self._button(label, lambda checked=False, fstop=value: self.set_fstop(fstop)), index // 3, index % 3)
        return widget

    def _content_widget(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        widget.setObjectName("SectionContent")
        return widget

    def _button(self, text: str, callback: Callable, pass_checked: bool = False) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setMinimumHeight(24)
        if pass_checked:
            button.clicked.connect(callback)
        else:
            button.clicked.connect(lambda _checked=False: callback())
        return button

    def _toggle_button(self, text: str, callback: Callable[[bool], None]) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setCheckable(True)
        button.setMinimumHeight(24)
        button.clicked.connect(callback)
        return button

    def _add_on_off_row(
        self,
        grid: QtWidgets.QGridLayout,
        row: int,
        label: str,
        callback: Callable[[bool], None],
        default_on: bool = True,
    ) -> None:
        grid.addWidget(QtWidgets.QLabel(label), row, 0)
        control = QtWidgets.QWidget()
        control_layout = QtWidgets.QHBoxLayout(control)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(6)
        on_radio = QtWidgets.QRadioButton("ON")
        off_radio = QtWidgets.QRadioButton("OFF")
        on_radio.setChecked(default_on)
        off_radio.setChecked(not default_on)
        on_radio.toggled.connect(lambda checked: callback(True) if checked else None)
        off_radio.toggled.connect(lambda checked: callback(False) if checked else None)
        control_layout.addWidget(on_radio)
        control_layout.addWidget(off_radio)
        control_layout.addStretch(1)
        grid.addWidget(control, row, 1, 1, 2)

    def _add_film_fit_row(self, grid: QtWidgets.QGridLayout, row: int) -> None:
        grid.addWidget(QtWidgets.QLabel("Film Fit"), row, 0)
        self.film_fit_combo = QtWidgets.QComboBox()
        for label, value in self.FILM_FIT_VALUES:
            self.film_fit_combo.addItem(label, value)
        self.film_fit_combo.setCurrentText("Overscan")
        self.film_fit_combo.activated.connect(lambda _index=0: self.set_film_fit())
        grid.addWidget(self.film_fit_combo, row, 1, 1, 2)

    def _add_spin_row(
        self,
        grid: QtWidgets.QGridLayout,
        row: int,
        label: str,
        value: float,
        minimum: float,
        maximum: float,
        decimals: int,
        function_name: str,
    ) -> QtWidgets.QDoubleSpinBox:
        grid.addWidget(QtWidgets.QLabel(label), row, 0)
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(0.1 if decimals else 1.0)
        spin.setValue(value)
        grid.addWidget(spin, row, 1)
        grid.addWidget(self._button("Set", lambda: self.apply_camera_numeric(label, function_name, spin.value())), row, 2)
        return spin

    def set_camera_mode(self, mode: str) -> None:
        self.camera_mode = mode if mode in self.CAMERA_MODES else "selected"
        if self.camera_mode == "active":
            self.active_camera_snapshot = self._current_camera_shape()
        else:
            self.active_camera_snapshot = ""
        self.refresh_active_camera_label()

    def _camera_pattern(self) -> str:
        return self.pattern_edit.text() if self.camera_mode == "pattern" else ""

    def toggle_lights(self, checked: bool = False) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status("Lights", lambda: f"displayLights: {layout_panel.set_viewport_lights(checked)}")

    def toggle_textures(self, checked: bool = False) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status("Texture", lambda: _on_off(layout_panel.set_viewport_textures(checked)))

    def toggle_dof(self, checked: bool = False) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status("DOF", lambda: _on_off(layout_panel.set_active_camera_dof(checked)))

    def apply_camera_action(self, label: str, function_name: str) -> None:
        from smartlib.dcc.maya import layout_panel

        function = getattr(layout_panel, function_name)
        self._run_status(label, lambda: f"{function(self.camera_mode, self._camera_pattern())} camera(s)")

    def apply_camera_bool(self, label: str, function_name: str, enabled: bool) -> None:
        from smartlib.dcc.maya import layout_panel

        function = getattr(layout_panel, function_name)
        self._run_status(label, lambda: f"{_on_off(enabled)}: {function(self.camera_mode, enabled, self._camera_pattern())} camera(s)")

    def apply_camera_numeric(self, label: str, function_name: str, value: float) -> None:
        from smartlib.dcc.maya import layout_panel

        function = getattr(layout_panel, function_name)
        self._run_status(label, lambda: f"{value:g}: {function(self.camera_mode, value, self._camera_pattern())} camera(s)")

    def set_film_fit(self) -> None:
        from smartlib.dcc.maya import layout_panel

        label = self.film_fit_combo.currentText()
        value = int(self.film_fit_combo.currentData())
        self._run_status("Film Fit", lambda: f"{label}: {layout_panel.set_film_fit(self.camera_mode, value, self._camera_pattern())} camera(s)")

    def set_all_camera_defaults(self) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status("Set All", lambda: f"{layout_panel.apply_camera_display_defaults(self.camera_mode, self._camera_pattern())} camera(s)")

    def generate_smart_gate_guide(self) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status("SmartGateGuide", lambda: f"generated {layout_panel.create_smart_gate_guide()}")

    def select_smart_gate_guides(self) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status("SmartGateGuide", lambda: f"selected {len(layout_panel.select_smart_gate_guides())} guide(s)")

    def match_wildcard(self) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status("Wildcard Select", lambda: f"selected {len(layout_panel.wildcard_select(self.pattern_edit.text()))} object(s)")

    def clear_wildcard(self) -> None:
        from smartlib.dcc.maya import layout_panel

        self.pattern_edit.clear()
        self._run_status("Wildcard Select", lambda: (layout_panel.clear_selection() or "selection cleared"))

    def toggle_picture_in_picture(self, checked: bool = False) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status("Picture in Picture", lambda: f"{_on_off(checked)}: {layout_panel.set_picture_in_picture(checked)} imagePlane(s)")

    def toggle_guide_attr(self, attr: str) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status(attr, lambda: self._guide_attr_status(layout_panel.toggle_smart_gate_guide_attr(attr)))

    def set_guide_attr(self, attr: str, enabled: bool) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status(attr, lambda: f"{_on_off(enabled)}: {layout_panel.set_smart_gate_guide_attr(attr, enabled)} guide(s)")

    def set_image_plane_alpha_gain_from_slider(self, slider_value: int) -> None:
        alpha_gain = float(slider_value) / 100.0
        self.alpha_gain_spin.blockSignals(True)
        self.alpha_gain_spin.setValue(alpha_gain)
        self.alpha_gain_spin.blockSignals(False)
        self.apply_image_plane_alpha_gain(alpha_gain)

    def set_image_plane_alpha_gain_from_spin(self, value: float) -> None:
        slider_value = int(round(float(value) * 100.0))
        self.alpha_gain_slider.blockSignals(True)
        self.alpha_gain_slider.setValue(slider_value)
        self.alpha_gain_slider.blockSignals(False)
        self.apply_image_plane_alpha_gain(float(value))

    def apply_image_plane_alpha_gain(self, alpha_gain: float) -> None:
        from smartlib.dcc.maya import layout_panel

        self._run_status("imagePlane alphaGain", lambda: f"{alpha_gain:.2f}: {layout_panel.set_image_plane_alpha_gain(alpha_gain)} imagePlane(s)")

    def set_lens(self, value: float) -> None:
        from smartlib.dcc.maya import layout_panel

        note = self._active_camera_change_note()
        self._run_status("Lens", lambda: f"{note}{value:g} mm: {layout_panel.set_lens(value, self.camera_mode, self._camera_pattern())} camera(s)")

    def set_fstop(self, value: float) -> None:
        from smartlib.dcc.maya import layout_panel

        note = self._active_camera_change_note()
        self._run_status("F-stop", lambda: f"{note}f{value:g}: {layout_panel.set_fstop(value, self.camera_mode, self._camera_pattern())} camera(s)")

    def _current_camera_shape(self) -> str:
        try:
            from smartlib.dcc.maya import layout_panel

            return layout_panel.current_camera_shape()
        except Exception:
            return ""

    def refresh_active_camera_label(self) -> None:
        camera = self._current_camera_shape()
        self.active_camera_label.setText(_short_node_name(camera) if camera else "-")
        if self.camera_mode == "active" and camera:
            self.active_camera_snapshot = camera

    def closeEvent(self, event) -> None:
        self._remove_active_camera_watchers()
        super().closeEvent(event)

    def _install_active_camera_watchers(self) -> None:
        self._active_camera_timer = QtCore.QTimer(self)
        self._active_camera_timer.setInterval(400)
        self._active_camera_timer.timeout.connect(self.refresh_active_camera_label)
        self._active_camera_timer.start()

        try:
            import maya.cmds as cmds

            job = cmds.scriptJob(event=["timeChanged", self.refresh_active_camera_label], protected=True)
            self._maya_script_jobs.append(job)
        except Exception:
            pass

    def _remove_active_camera_watchers(self) -> None:
        if self._active_camera_timer is not None:
            self._active_camera_timer.stop()
            self._active_camera_timer = None
        try:
            import maya.cmds as cmds

            for job in self._maya_script_jobs:
                if cmds.scriptJob(exists=job):
                    cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass
        self._maya_script_jobs = []

    def _active_camera_change_note(self) -> str:
        if self.camera_mode != "active":
            return ""
        current = self._current_camera_shape()
        if current:
            self.active_camera_label.setText(_short_node_name(current))
        previous = self.active_camera_snapshot
        if not previous:
            self.active_camera_snapshot = current
            return ""
        if current == previous:
            return ""
        self.active_camera_snapshot = current
        return f"Active camera refreshed: {previous} -> {current}. "

    def _guide_attr_status(self, result: tuple[bool, int]) -> str:
        enabled, count = result
        return f"{_on_off(enabled)}: {count} guide(s)"

    def _run_status(self, title: str, callback: Callable[[], str]) -> None:
        try:
            detail = callback()
            self.status_label.setText(f"{title}: {detail}")
        except Exception as exc:
            self.status_label.setText(f"{title}: {exc}")
            QtWidgets.QMessageBox.warning(self, title, str(exc))

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #3f3f3f;
                color: #dddddd;
                font-size: 11px;
            }
            QLabel#PanelTitle {
                color: #d8e1e7;
                font-size: 18px;
                padding: 4px 2px;
            }
            QLabel#SubsectionLabel {
                color: #d8e1e7;
                font-weight: bold;
                padding-top: 5px;
            }
            QLabel#StatusLabel {
                color: #c7d5e0;
                padding: 3px;
            }
            QWidget#SectionContent {
                background: #454545;
                border: 1px solid #2d2d2d;
            }
            QToolButton {
                background: #3f3f3f;
                border: none;
                color: #f0f0f0;
                padding: 3px;
                text-align: left;
                font-weight: bold;
            }
            QPushButton {
                background: #5b5b5b;
                border: 1px solid #343434;
                color: #f1f1f1;
                min-height: 22px;
                padding: 2px 6px;
            }
            QPushButton:hover {
                background: #666666;
            }
            QPushButton:checked {
                background: #826824;
            }
            QLineEdit, QComboBox, QDoubleSpinBox, QLabel#FieldValue {
                background: #333333;
                border: 1px solid #2b2b2b;
                color: #e8f2ff;
                padding: 3px;
            }
            QRadioButton {
                spacing: 4px;
                min-height: 18px;
            }
            QSlider::groove:horizontal {
                background: #303030;
                height: 5px;
            }
            QSlider::handle:horizontal {
                background: #7d7d7d;
                border: 1px solid #242424;
                width: 12px;
                margin: -5px 0;
            }
            QScrollArea {
                border: none;
            }
            """
        )


def _on_off(enabled: bool) -> str:
    return "ON" if enabled else "OFF"


def _short_node_name(node: str) -> str:
    return str(node or "").split("|")[-1] or str(node or "")


_WINDOW = None


def show(config_dir: str | os.PathLike[str] | None = None, parent=None):
    global _WINDOW
    if _WINDOW is not None:
        try:
            _WINDOW.close()
        except Exception:
            pass
    from smartlib.core.qt import parent_for_maya

    window_parent = parent_for_maya(QtWidgets, parent)
    _WINDOW = MayaLayoutPanelWindow(config_dir=config_dir, parent=window_parent)
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW
