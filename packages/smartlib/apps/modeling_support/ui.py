from __future__ import annotations

from typing import Callable


def _qt_modules():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt_modules()


class ModelingSupportWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("Modeling Support")
        self.resize(280, 480)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._selection_mode_bar())
        layout.addWidget(self._modeling_tool_bar())

        options = QtWidgets.QGridLayout()
        options.setHorizontalSpacing(6)
        options.setVerticalSpacing(4)

        options.addWidget(QtWidgets.QLabel("Axis"), 0, 0)
        self.axis_combo = QtWidgets.QComboBox()
        self.axis_combo.addItems(["X", "Y", "Z"])
        options.addWidget(self.axis_combo, 0, 1)

        options.addWidget(QtWidgets.QLabel("Delete Side"), 1, 0)
        self.side_combo = QtWidgets.QComboBox()
        self.side_combo.addItem("- side", "negative")
        self.side_combo.addItem("+ side", "positive")
        options.addWidget(self.side_combo, 1, 1)

        options.addWidget(QtWidgets.QLabel("Plane"), 2, 0)
        self.plane_spin = QtWidgets.QDoubleSpinBox()
        self.plane_spin.setRange(-100000.0, 100000.0)
        self.plane_spin.setDecimals(3)
        self.plane_spin.setSingleStep(0.1)
        self.plane_spin.setValue(0.0)
        options.addWidget(self.plane_spin, 2, 1)

        options.addWidget(QtWidgets.QLabel("Merge Tolerance"), 3, 0)
        self.merge_tolerance_spin = QtWidgets.QDoubleSpinBox()
        self.merge_tolerance_spin.setRange(0.0, 1000.0)
        self.merge_tolerance_spin.setDecimals(4)
        self.merge_tolerance_spin.setSingleStep(0.001)
        self.merge_tolerance_spin.setValue(0.001)
        options.addWidget(self.merge_tolerance_spin, 3, 1)

        options.addWidget(QtWidgets.QLabel("X=0 Distance"), 4, 0)
        self.x_zero_distance_spin = QtWidgets.QDoubleSpinBox()
        self.x_zero_distance_spin.setRange(0.0, 1000.0)
        self.x_zero_distance_spin.setDecimals(4)
        self.x_zero_distance_spin.setSingleStep(0.001)
        self.x_zero_distance_spin.setValue(0.001)
        options.addWidget(self.x_zero_distance_spin, 4, 1)
        layout.addLayout(options)

        layout.addWidget(self._button("Delete Half Mesh", self.delete_half_mesh, "delete_half"))
        layout.addWidget(self._button("Mirror Copy + Combine", self.mirror_copy, "mirror"))
        layout.addWidget(self._button("Select X=0 Vertices", self.select_near_x_zero_vertices, "select_vertex"))
        layout.addWidget(self._button("Set Selected Vertices X=0", self.set_selected_vertices_x_zero, "move"))
        layout.addWidget(self._button("Extract Faces", self.extract_selected_faces, "extract"))
        layout.addWidget(self._button("Combine", self.combine_selected_to_last_name, "combine"))
        layout.addWidget(self._match_to_last_section())
        layout.addWidget(self._button("Create Locator at Selected", self.create_locators_at_selected_world, "locator"))
        layout.addWidget(self._button("Move BBox Bottom Center to Origin", self.move_bbox_bottom_center_to_origin, "center_pivot"))

        self.status_label = QtWidgets.QLabel("Select polygon mesh objects.")
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        self.setCentralWidget(central)
        self._apply_style()

    def _button(self, text: str, callback: Callable[[], None], icon_role: str = "") -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setMinimumHeight(28)
        if icon_role:
            button.setIcon(self._maya_icon(icon_role))
            button.setIconSize(QtCore.QSize(18, 18))
        button.clicked.connect(lambda _checked=False: callback())
        return button

    def _modeling_tool_bar(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._tool_button("Multi-Cut", self.activate_multi_cut_tool, "multi_cut"))
        layout.addWidget(self._tool_button("Quad Draw", self.activate_quad_draw_tool, "quad_draw"))
        return widget

    def _tool_button(self, text: str, callback: Callable[[], None], icon_role: str) -> QtWidgets.QPushButton:
        button = self._button(text, callback, icon_role)
        button.setMinimumHeight(30)
        button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return button

    def _match_to_last_section(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QFrame()
        widget.setObjectName("MatchPanel")
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(6)

        radio_layout = QtWidgets.QHBoxLayout()
        radio_layout.setSpacing(8)
        self.match_transform_radio = QtWidgets.QRadioButton("Transform")
        self.match_position_radio = QtWidgets.QRadioButton("Position")
        self.match_rotate_radio = QtWidgets.QRadioButton("Rotate")
        self.match_transform_radio.setChecked(True)
        for radio in (self.match_transform_radio, self.match_position_radio, self.match_rotate_radio):
            radio_layout.addWidget(radio)
        radio_layout.addStretch(1)
        layout.addLayout(radio_layout)
        layout.addWidget(self._button("Match to Last", self.match_to_last_selected, "match"))
        return widget

    def _selection_mode_bar(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        for mode, tooltip in (
            ("object", "Object Mode"),
            ("vertex", "Vertex Mode"),
            ("edge", "Edge Mode"),
            ("face", "Face Mode"),
            ("uv", "UV Mode"),
        ):
            layout.addWidget(self._mode_button(mode, tooltip))
        layout.addStretch(1)
        return widget

    def _mode_button(self, mode: str, tooltip: str) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton()
        button.setObjectName("ModeButton")
        button.setToolTip(tooltip)
        button.setIcon(_selection_mode_icon(mode))
        button.setIconSize(QtCore.QSize(28, 28))
        button.setFixedSize(36, 36)
        button.clicked.connect(lambda _checked=False, value=mode: self.set_selection_mode(value))
        return button

    def set_selection_mode(self, mode: str) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Selection Mode",
            lambda: modeling.set_selection_mode(mode),
        )

    def activate_multi_cut_tool(self) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Multi-Cut",
            modeling.activate_multi_cut_tool,
        )

    def activate_quad_draw_tool(self) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Quad Draw",
            modeling.activate_quad_draw_tool,
        )

    def delete_half_mesh(self) -> None:
        from smartlib.dcc.maya import modeling

        axis = self.axis_combo.currentText()
        side = str(self.side_combo.currentData())
        plane = self.plane_spin.value()
        self._run_status(
            "Delete Half Mesh",
            lambda: f"deleted {modeling.delete_half_mesh(axis=axis, side=side, plane=plane)} face(s)",
        )

    def mirror_copy(self) -> None:
        from smartlib.dcc.maya import modeling

        axis = self.axis_combo.currentText()
        plane = self.plane_spin.value()
        merge_tolerance = self.merge_tolerance_spin.value()
        self._run_status(
            "Mirror Copy + Combine",
            lambda: f"combined {len(modeling.mirror_copy(axis=axis, plane=plane, merge_tolerance=merge_tolerance))} object(s)",
        )

    def select_near_x_zero_vertices(self) -> None:
        from smartlib.dcc.maya import modeling

        tolerance = self.x_zero_distance_spin.value()
        self._run_status(
            "Select X=0 Vertices",
            lambda: f"selected {len(modeling.select_near_x_zero_vertices(tolerance=tolerance))} vertex/vertices",
        )

    def set_selected_vertices_x_zero(self) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Set Selected Vertices X=0",
            lambda: f"moved {modeling.set_selected_vertices_x_zero()} vertex/vertices",
        )

    def move_bbox_bottom_center_to_origin(self) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Move BBox Bottom Center to Origin",
            lambda: _offset_status(modeling.move_bbox_bottom_center_to_origin()),
        )

    def combine_selected_to_last_name(self) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Combine",
            lambda: f"created {modeling.combine_selected_to_last_name()}",
        )

    def extract_selected_faces(self) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Extract Faces",
            lambda: f"created {len(modeling.extract_selected_faces_delete_history_center_pivot())} object(s)",
        )

    def match_transform_to_last_selected(self) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Match Transform to Last",
            lambda: f"matched {modeling.match_transform_to_last_selected()} object(s)",
        )

    def match_position_to_last_selected(self) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Match Position to Last",
            lambda: f"matched {modeling.match_position_to_last_selected()} object(s)",
        )

    def match_rotate_to_last_selected(self) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Match Rotate to Last",
            lambda: f"matched {modeling.match_rotate_to_last_selected()} object(s)",
        )

    def match_to_last_selected(self) -> None:
        from smartlib.dcc.maya import modeling

        if self.match_position_radio.isChecked():
            title = "Match Position to Last"
            callback = modeling.match_position_to_last_selected
        elif self.match_rotate_radio.isChecked():
            title = "Match Rotate to Last"
            callback = modeling.match_rotate_to_last_selected
        else:
            title = "Match Transform to Last"
            callback = modeling.match_transform_to_last_selected

        self._run_status(
            title,
            lambda: f"matched {callback()} object(s)",
        )

    def create_locators_at_selected_world(self) -> None:
        from smartlib.dcc.maya import modeling

        self._run_status(
            "Create Locator at Selected",
            lambda: f"created {len(modeling.create_locators_at_selected_world())} locator(s)",
        )

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
            QFrame#MatchPanel {
                background: #454545;
                border: 1px solid #2d2d2d;
            }
            QToolButton#ModeButton {
                background: #505050;
                border: 1px solid #2c2c2c;
                padding: 3px;
            }
            QToolButton#ModeButton:hover {
                background: #5f7080;
            }
            QComboBox, QDoubleSpinBox {
                background: #333333;
                border: 1px solid #2b2b2b;
                color: #e8f2ff;
                padding: 3px;
            }
            """
        )

    def _maya_icon(self, role: str) -> QtGui.QIcon:
        candidates = {
            "delete_half": ("polyDelFacet.png", "deleteActive.png", "delete.png", "polyChipOff.png"),
            "mirror": ("polyMirrorGeometry.png", "polyMirrorCut.png", "mirror.png", "polyMirror.png"),
            "select_vertex": ("selectByComponent.png", "selectByVertex.png", "componentMode.png", "polyVertex.png"),
            "move": ("move_M.png", "moveTool.png", "move.png"),
            "extract": ("polyChipOff.png", "polySeparate.png", "polyExtrudeFacet.png"),
            "combine": ("polyUnite.png", "polyUnite3D.png", "polyMergeVertex.png"),
            "match": ("matchTransform.png", "parentConstraint.png", "orientConstraint.png"),
            "locator": ("locator.png", "locator.svg", "locatorCreate.png"),
            "center_pivot": ("centerPivot.png", "CenterPivot.png", "moveTool.png"),
            "multi_cut": ("polyCut.png", "polyCutContext.png", "multiCut.png", "polySplit.png"),
            "quad_draw": ("polyQuadDraw.png", "quadDraw.png", "polyCreateFacet.png", "polyAppendFacet.png"),
        }.get(role, ())
        for name in candidates:
            icon = QtGui.QIcon(f":/{name}")
            if not icon.isNull():
                return icon
        fallbacks = {
            "delete_half": QtWidgets.QStyle.SP_TrashIcon,
            "mirror": QtWidgets.QStyle.SP_BrowserReload,
            "select_vertex": QtWidgets.QStyle.SP_FileDialogDetailedView,
            "move": QtWidgets.QStyle.SP_ArrowUp,
            "extract": QtWidgets.QStyle.SP_DialogOpenButton,
            "combine": QtWidgets.QStyle.SP_DialogApplyButton,
            "match": QtWidgets.QStyle.SP_ArrowRight,
            "locator": QtWidgets.QStyle.SP_FileDialogNewFolder,
            "center_pivot": QtWidgets.QStyle.SP_ArrowDown,
            "multi_cut": QtWidgets.QStyle.SP_DialogResetButton,
            "quad_draw": QtWidgets.QStyle.SP_FileDialogListView,
        }
        return self.style().standardIcon(fallbacks.get(role, QtWidgets.QStyle.SP_ArrowRight))


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
    _WINDOW = ModelingSupportWindow(parent=window_parent)
    if window_parent is not None:
        _WINDOW.setWindowFlags(_WINDOW.windowFlags() | QtCore.Qt.Window)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW


def _offset_status(offset: tuple[float, float, float]) -> str:
    return f"offset ({offset[0]:.3f}, {offset[1]:.3f}, {offset[2]:.3f})"


def _selection_mode_icon(mode: str) -> QtGui.QIcon:
    size = 28
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

    line = QtGui.QColor("#202020")
    orange = QtGui.QColor("#d98a38")
    orange_dark = QtGui.QColor("#b66c25")
    grey = QtGui.QColor("#d7d8d5")

    if mode == "object":
        _draw_cube_icon(painter, line, orange, orange_dark)
    elif mode == "vertex":
        _draw_vertex_icon(painter, line, orange)
    elif mode == "edge":
        _draw_edge_icon(painter, line)
    elif mode == "face":
        _draw_face_icon(painter, line, orange, grey)
    else:
        _draw_uv_icon(painter, line, orange)

    painter.end()
    return QtGui.QIcon(pixmap)


def _draw_cube_icon(painter, line, orange, orange_dark) -> None:
    top = QtGui.QPolygonF([QtCore.QPointF(14, 3), QtCore.QPointF(25, 8), QtCore.QPointF(14, 14), QtCore.QPointF(3, 8)])
    left = QtGui.QPolygonF([QtCore.QPointF(3, 8), QtCore.QPointF(14, 14), QtCore.QPointF(14, 26), QtCore.QPointF(3, 20)])
    right = QtGui.QPolygonF([QtCore.QPointF(25, 8), QtCore.QPointF(14, 14), QtCore.QPointF(14, 26), QtCore.QPointF(25, 20)])
    painter.setPen(QtGui.QPen(line, 1))
    painter.setBrush(orange)
    painter.drawPolygon(top)
    painter.setBrush(orange_dark)
    painter.drawPolygon(left)
    painter.setBrush(orange)
    painter.drawPolygon(right)


def _draw_vertex_icon(painter, line, orange) -> None:
    painter.setPen(QtGui.QPen(line, 1))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawRect(QtCore.QRectF(7, 7, 14, 14))
    painter.setBrush(orange)
    for x, y in ((6, 6), (20, 6), (6, 20), (20, 20)):
        painter.drawRect(QtCore.QRectF(x - 2, y - 2, 4, 4))


def _draw_edge_icon(painter, line) -> None:
    painter.setPen(QtGui.QPen(line, 1.2))
    painter.setBrush(QtCore.Qt.NoBrush)
    points = [QtCore.QPointF(14, 3), QtCore.QPointF(25, 14), QtCore.QPointF(14, 25), QtCore.QPointF(3, 14)]
    painter.drawPolygon(QtGui.QPolygonF(points))


def _draw_face_icon(painter, line, orange, grey) -> None:
    top = QtGui.QPolygonF([QtCore.QPointF(14, 3), QtCore.QPointF(25, 8), QtCore.QPointF(14, 14), QtCore.QPointF(3, 8)])
    left = QtGui.QPolygonF([QtCore.QPointF(3, 8), QtCore.QPointF(14, 14), QtCore.QPointF(14, 26), QtCore.QPointF(3, 20)])
    right = QtGui.QPolygonF([QtCore.QPointF(25, 8), QtCore.QPointF(14, 14), QtCore.QPointF(14, 26), QtCore.QPointF(25, 20)])
    painter.setPen(QtGui.QPen(line, 1))
    painter.setBrush(grey)
    painter.drawPolygon(top)
    painter.drawPolygon(right)
    painter.setBrush(orange)
    painter.drawPolygon(left)


def _draw_uv_icon(painter, line, orange) -> None:
    painter.setPen(QtGui.QPen(line, 1))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawRect(QtCore.QRectF(7, 6, 15, 16))
    painter.drawLine(QtCore.QPointF(7, 14), QtCore.QPointF(22, 14))
    painter.drawLine(QtCore.QPointF(14, 6), QtCore.QPointF(14, 22))
    painter.setBrush(orange)
    for x, y in ((7, 6), (22, 6), (7, 22), (22, 22), (14, 14)):
        painter.drawRect(QtCore.QRectF(x - 2, y - 2, 4, 4))
