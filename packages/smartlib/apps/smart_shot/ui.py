from __future__ import annotations

import os
import subprocess
from pathlib import Path

from smartlib.core.config_loader import ProjectConfig


def _qt_modules():
    try:
        from PySide6 import QtCore, QtGui, QtUiTools, QtWidgets

        return QtCore, QtGui, QtUiTools, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtUiTools, QtWidgets

        return QtCore, QtGui, QtUiTools, QtWidgets


QtCore, QtGui, QtUiTools, QtWidgets = _qt_modules()


def _default_config_dir() -> Path:
    env_path = os.environ.get("PROJECT_CONFIG_DIR")
    if env_path:
        return Path(env_path)
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[4])
    return root / "config" / "STKB"


class SmartShotWindow(QtWidgets.QMainWindow):
    COLUMNS = ["Shot", "Camera", "Lens", "fStop", "Start Time", "End Time", "Duration"]

    def __init__(self, config_dir: str | os.PathLike[str] | None = None, parent=None):
        super().__init__(parent)
        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.shots = []
        self.validation_issues = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        ui_path = Path(__file__).resolve().parent / "ui" / "smart_shot.ui"
        ui_file = QtCore.QFile(str(ui_path))
        if not ui_file.open(QtCore.QFile.ReadOnly):
            raise RuntimeError(f"Could not open UI file: {ui_path}")
        try:
            loaded_ui = QtUiTools.QUiLoader().load(ui_file)
        finally:
            ui_file.close()
        if loaded_ui is None:
            raise RuntimeError(f"Could not load UI file: {ui_path}")

        self.loaded_ui = loaded_ui
        if isinstance(loaded_ui, QtWidgets.QMainWindow):
            central_widget = loaded_ui.centralWidget()
            if central_widget is None:
                raise RuntimeError(f"UI file has no central widget: {ui_path}")
            central_widget.setParent(None)
            self.ui = central_widget
            self.setCentralWidget(central_widget)
        else:
            self.ui = loaded_ui
            self.setCentralWidget(loaded_ui)
        self.setWindowTitle(f"Smart Shot - {self.project_config.project_name}")
        self.resize(380, 720)
        self.setMinimumWidth(320)

        self.validate_btn = self._ui_object("validate_btn")
        self.set_sequence_range_btn = self._ui_object("set_sequencerange_btn")
        self.set_selected_range_btn = self._ui_object("set_selectedshotrange_btn")
        self.scale_btn = self._ui_object("shotscale_btn")
        self.move_btn = self._ui_object("shotmove_btn")
        self.preview_btn = self._ui_object("preview_btn")
        self.preview_btn.setText("Export Preview Playblast")
        self.preview_btn.setToolTip("Export playblast image sequences for selected sequencer shots.")
        self.spin_box = self._ui_object("spinBox")
        self.shot_table = self._ui_object("shotlist")
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.setToolTip("Playblast display preset")
        self.sequence_start_btn = self._icon_button("Sequence Start", self._maya_icon("start"))
        self.sequence_end_btn = self._icon_button("Sequence End", self._maya_icon("end"))
        self.shot_start_btn = self._icon_button("Shot Start", self._maya_icon("previous"))
        self.shot_end_btn = self._icon_button("Shot End", self._maya_icon("next"))
        self.sequence_start_btn.setToolTip("Move the Maya time slider to the first sequencer shot frame.")
        self.sequence_end_btn.setToolTip("Move the Maya time slider to the last sequencer shot frame.")
        self.shot_start_btn.setToolTip("Move the Maya time slider to the selected shot start frame.")
        self.shot_end_btn.setToolTip("Move the Maya time slider to the selected shot end frame.")

        self.shot_table.setColumnCount(len(self.COLUMNS))
        self.shot_table.setHorizontalHeaderLabels(self.COLUMNS)
        self.shot_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.shot_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.shot_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.shot_table.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.shot_table.setEnabled(True)
        self.shot_table.verticalHeader().setVisible(False)
        self.shot_table.verticalHeader().setDefaultSectionSize(24)
        self.shot_table.horizontalHeader().setStretchLastSection(True)
        self.shot_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.shot_table.setSortingEnabled(False)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.publish_camera_btn = QtWidgets.QPushButton("Publish Camera")
        self.quick_open_rv_btn = QtWidgets.QPushButton("Quick Open Package in RV")
        self.publish_camera_btn.setStyleSheet(
            "QPushButton { background-color: #2f5f9f; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #3b73b8; }"
        )
        self.quick_open_rv_btn.setStyleSheet(
            "QPushButton { background-color: #2f6f4e; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #3d835f; }"
        )
        self._rebuild_control_layout()
        self.ui.layout().insertWidget(1, self.preset_combo)
        self.ui.layout().insertWidget(self.ui.layout().indexOf(self.preview_btn), self._transport_controls())
        self.ui.layout().addWidget(self.quick_open_rv_btn)
        self.ui.layout().addWidget(self.publish_camera_btn)
        self.ui.layout().addWidget(self.status_label)

        self.validate_btn.clicked.connect(self.validate)
        self.set_sequence_range_btn.clicked.connect(self.set_sequence_range)
        self.set_selected_range_btn.clicked.connect(self.set_selected_range)
        self.sequence_start_btn.clicked.connect(self.move_to_sequence_start)
        self.sequence_end_btn.clicked.connect(self.move_to_sequence_end)
        self.shot_start_btn.clicked.connect(self.move_to_shot_start)
        self.shot_end_btn.clicked.connect(self.move_to_shot_end)
        self.scale_btn.clicked.connect(self.scale_selected_shot)
        self.move_btn.clicked.connect(self.move_selected_shots)
        self.preview_btn.clicked.connect(self.export_preview)
        self.publish_camera_btn.clicked.connect(self.publish_camera)
        self.quick_open_rv_btn.clicked.connect(self.quick_open_latest_package_in_rv)
        self.shot_table.itemDoubleClicked.connect(self.edit_camera_value)
        self._populate_playblast_presets()

    def _ui_object(self, name: str):
        obj = self.ui.findChild(QtCore.QObject, name)
        if obj is None:
            raise RuntimeError(f"UI object was not found: {name}")
        return obj

    def _rebuild_control_layout(self) -> None:
        original_frame = self._ui_object("frame")
        original_frame.setVisible(False)

        control_frame = QtWidgets.QFrame()
        control_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        control_layout = QtWidgets.QVBoxLayout(control_frame)
        control_layout.setContentsMargins(4, 4, 4, 4)
        control_layout.setSpacing(4)

        range_row = QtWidgets.QHBoxLayout()
        range_row.setSpacing(4)
        range_row.addWidget(self.validate_btn)
        range_row.addWidget(self.set_sequence_range_btn)
        range_row.addWidget(self.set_selected_range_btn)
        control_layout.addLayout(range_row)

        edit_row = QtWidgets.QHBoxLayout()
        edit_row.setSpacing(4)
        edit_row.addWidget(self.scale_btn)
        edit_row.addWidget(self.move_btn)
        edit_row.addWidget(self.spin_box)
        control_layout.addLayout(edit_row)

        self.ui.layout().insertWidget(0, control_frame)

    def _transport_controls(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(6)
        layout.addStretch(1)
        for button in (self.sequence_start_btn, self.shot_start_btn, self.shot_end_btn, self.sequence_end_btn):
            layout.addWidget(button)
        layout.addStretch(1)
        return widget

    def _icon_button(self, tooltip: str, icon: QtGui.QIcon) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton()
        button.setToolTip(tooltip)
        button.setIcon(icon)
        button.setIconSize(QtCore.QSize(18, 18))
        button.setFixedSize(30, 26)
        button.setAutoRaise(False)
        return button

    def _maya_icon(self, role: str) -> QtGui.QIcon:
        candidates = {
            "start": ("timestart.png", "timeStart.png", "timefirst.png", "timeplayStart.png", "playbackStart.png", "goToStart.png"),
            "previous": ("timeprev.png", "timePrev.png", "timePrevious.png", "playbackPrevious.png"),
            "next": ("timenext.png", "timeNext.png", "timeForward.png", "playbackNext.png"),
            "end": ("timeend.png", "timeEnd.png", "timelast.png", "timeplayEnd.png", "playbackEnd.png", "goToEnd.png"),
        }.get(role, ())
        for name in candidates:
            icon = QtGui.QIcon(f":/{name}")
            if not icon.isNull():
                return icon
        fallbacks = {
            "start": QtWidgets.QStyle.SP_MediaSkipBackward,
            "previous": QtWidgets.QStyle.SP_MediaSeekBackward,
            "next": QtWidgets.QStyle.SP_MediaSeekForward,
            "end": QtWidgets.QStyle.SP_MediaSkipForward,
        }
        return self.style().standardIcon(fallbacks.get(role, QtWidgets.QStyle.SP_ArrowRight))

    def refresh(self) -> None:
        from smartlib.dcc.maya import smart_shot

        self.shots = smart_shot.list_sequencer_shots()
        self.shot_table.setRowCount(0)
        for row, shot in enumerate(self.shots):
            self.shot_table.insertRow(row)
            values = [
                shot.shot,
                shot.camera,
                "" if shot.lens is None else f"{shot.lens:.2f}",
                "" if shot.fstop is None else f"{shot.fstop:.2f}",
                str(shot.start),
                str(shot.end),
                self._duration_text(shot.duration),
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, shot.node)
                item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
                self.shot_table.setItem(row, column, item)
        self.shot_table.clearSelection()
        self._apply_validation_colors()
        self.status_label.setText(f"{len(self.shots)} sequencer shots. Validate manually if needed.")

    def validate(self) -> None:
        from smartlib.dcc.maya import smart_shot

        try:
            _official, issues = smart_shot.validate_against_editorial(self.project_config)
        except Exception as exc:
            self.validation_issues = {}
            self._apply_validation_colors()
            self.status_label.setText(f"Validate skipped: {exc}")
            return
        self.validation_issues = {issue.shot: issue for issue in issues}
        self._apply_validation_colors()
        if issues:
            self.status_label.setText(f"Validate: {len(issues)} differences found")
        else:
            self.status_label.setText("Validate: OK")

    def set_sequence_range(self) -> None:
        from smartlib.dcc.maya import smart_shot

        try:
            start, end = smart_shot.set_sequence_range()
            self.status_label.setText(f"Sequence range: {start}-{end}")
        except Exception as exc:
            self._warn("Set Sequence Range", str(exc))

    def set_selected_range(self) -> None:
        from smartlib.dcc.maya import smart_shot

        try:
            start, end = smart_shot.set_selected_range(self._selected_shot_nodes())
            self.status_label.setText(f"Selected range: {start}-{end}")
        except Exception as exc:
            self._warn("Set Selected Range", str(exc))

    def move_to_sequence_start(self) -> None:
        from smartlib.dcc.maya import smart_shot

        try:
            frame = smart_shot.move_time_to_sequence_start()
            self.status_label.setText(f"Time slider: sequence start {frame}")
        except Exception as exc:
            self._warn("Sequence Start", str(exc))

    def move_to_sequence_end(self) -> None:
        from smartlib.dcc.maya import smart_shot

        try:
            frame = smart_shot.move_time_to_sequence_end()
            self.status_label.setText(f"Time slider: sequence end {frame}")
        except Exception as exc:
            self._warn("Sequence End", str(exc))

    def move_to_shot_start(self) -> None:
        from smartlib.dcc.maya import smart_shot

        try:
            frame = smart_shot.move_time_to_selected_start(self._selected_shot_nodes())
            self.status_label.setText(f"Time slider: shot start {frame}")
        except Exception as exc:
            self._warn("Shot Start", str(exc))

    def move_to_shot_end(self) -> None:
        from smartlib.dcc.maya import smart_shot

        try:
            frame = smart_shot.move_time_to_selected_end(self._selected_shot_nodes())
            self.status_label.setText(f"Time slider: shot end {frame}")
        except Exception as exc:
            self._warn("Shot End", str(exc))

    def move_selected_shots(self) -> None:
        from smartlib.dcc.maya import smart_shot

        delta = int(self.spin_box.value())
        try:
            smart_shot.move_selected_shots(self._selected_shot_nodes(), delta)
            self.refresh()
            self.status_label.setText(f"Moved selected shots: {delta} frames")
        except Exception as exc:
            self._warn("Move", str(exc))

    def scale_selected_shot(self) -> None:
        from smartlib.dcc.maya import smart_shot

        nodes = self._selected_shot_nodes()
        if len(nodes) != 1:
            self._warn("Scale", "Select exactly one shot.")
            return
        duration = int(self.spin_box.value())
        try:
            smart_shot.scale_selected_shot_duration(nodes[0], duration)
            self.refresh()
            self.status_label.setText(f"Scaled selected shot to {duration} frames")
        except Exception as exc:
            self._warn("Scale", str(exc))

    def export_preview(self) -> None:
        from smartlib.dcc.maya import smart_shot

        nodes = self._selected_shot_nodes()
        if not nodes:
            self._warn("Export Preview Playblast", "Select one or more shot rows.")
            return
        try:
            path = smart_shot.export_selected_preview(
                self.project_config,
                nodes,
                playblast_preset=self._current_playblast_preset(),
            )
            self.status_label.setText(f"Preview playblast package: {path}")
        except Exception as exc:
            self._warn("Export Preview Playblast", str(exc))

    def _populate_playblast_presets(self) -> None:
        try:
            from smartlib.dcc.maya.playblast_preset import preset_label, preset_names

            self.preset_combo.clear()
            self.preset_combo.addItem("Preset: None", "")
            for name in preset_names(self.project_config):
                self.preset_combo.addItem(f"Preset: {preset_label(self.project_config, name)}", name)
        except Exception:
            self.preset_combo.clear()
            self.preset_combo.addItem("Preset: None", "")

    def _current_playblast_preset(self) -> str:
        return str(self.preset_combo.currentData() or "")

    def publish_camera(self) -> None:
        from smartlib.dcc.maya import smart_shot

        variant, ok = QtWidgets.QInputDialog.getText(self, "Publish Camera", "Camera option", text="main")
        if not ok:
            return
        try:
            path = smart_shot.publish_selected_cameras(
                self.project_config,
                self._selected_shot_nodes(),
                camera_variant=variant.strip() or "main",
            )
            self.status_label.setText(f"Camera published: {path}")
        except Exception as exc:
            self._warn("Publish Camera", str(exc))

    def quick_open_latest_package_in_rv(self) -> None:
        try:
            _open_rv(self.project_config)
            self.status_label.setText("Launched RV")
        except Exception as exc:
            self._warn("Quick Open Package in RV", str(exc))

    def edit_camera_value(self, item) -> None:
        from smartlib.dcc.maya import smart_shot

        if item.column() not in (2, 3):
            return
        shot_node = item.data(QtCore.Qt.UserRole)
        label = "Lens" if item.column() == 2 else "fStop"
        current = _float_or(item.text(), 35.0 if item.column() == 2 else 5.6)
        value, ok = QtWidgets.QInputDialog.getDouble(self, f"Set {label}", label, current, 0.001, 10000.0, 3)
        if not ok:
            return
        try:
            if item.column() == 2:
                smart_shot.set_camera_lens(shot_node, value)
            else:
                smart_shot.set_camera_fstop(shot_node, value)
            self.refresh()
        except Exception as exc:
            self._warn(f"Set {label}", str(exc))

    def _selected_shot_nodes(self) -> list[str]:
        nodes = []
        for row in self._selected_shot_rows():
            item = self.shot_table.item(row, 0)
            if item:
                nodes.append(item.data(QtCore.Qt.UserRole))
        return [node for node in nodes if node]

    def _selected_shot_names(self) -> list[str]:
        names = []
        for row in self._selected_shot_rows():
            item = self.shot_table.item(row, 0)
            if item and item.text():
                names.append(item.text())
        return names

    def _selected_shot_rows(self) -> list[int]:
        rows = sorted({index.row() for index in self.shot_table.selectedIndexes()})
        if not rows and self.shot_table.currentRow() >= 0:
            rows = [self.shot_table.currentRow()]
        return rows

    def _apply_validation_colors(self) -> None:
        warning = QtGui.QColor(200, 170, 70)
        locked = QtGui.QColor(55, 105, 70)
        clear = QtGui.QColor()
        for row in range(self.shot_table.rowCount()):
            shot_item = self.shot_table.item(row, 0)
            shot_name = shot_item.text() if shot_item else ""
            issue = self.validation_issues.get(shot_name)
            shot = self.shots[row] if row < len(self.shots) else None
            for column in range(self.shot_table.columnCount()):
                item = self.shot_table.item(row, column)
                if not item:
                    continue
                if shot and getattr(shot, "preview_locked", False):
                    item.setBackground(locked)
                    item.setToolTip("Camera has been published. Preview playblast will export a new review version.")
                elif issue:
                    item.setBackground(warning)
                    item.setToolTip(issue.message)
                else:
                    item.setBackground(clear)
                    item.setToolTip("")

    def _warn(self, title: str, message: str) -> None:
        self.status_label.setText(f"{title}: {message}")
        QtWidgets.QMessageBox.warning(self, title, message)

    @staticmethod
    def _duration_text(duration: int, fps: int = 24) -> str:
        q, mod = divmod(int(duration), int(fps))
        return f"{duration} ( {q:02} + {mod:02} )"


def _float_or(value: str, default: float) -> float:
    try:
        return float(str(value).split()[0])
    except Exception:
        return default


def _read_json(path: str | os.PathLike[str]) -> dict:
    import json

    json_path = Path(path)
    if not json_path.exists():
        return {}
    with json_path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    return data if isinstance(data, dict) else {}


def _open_rv(project_config: ProjectConfig) -> None:
    from smartlib.review.rv import find_rv_executable

    rv = find_rv_executable(project_config)
    if not rv:
        raise RuntimeError("OpenRV was not found. Set tools.openrv.path or OPENRV_PATH.")
    subprocess.Popen([str(rv)])


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
    _WINDOW = SmartShotWindow(config_dir=config_dir, parent=window_parent)
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW
