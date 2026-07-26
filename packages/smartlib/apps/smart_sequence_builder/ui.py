from __future__ import annotations

import os
from pathlib import Path

from smartlib.apps.smart_sequence_builder.service import SequenceBuildPlan, SmartSequenceBuilderService
from smartlib.core.config_loader import ProjectConfig


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt()


def _default_config_dir() -> Path:
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path.cwd())
    return Path(os.environ.get("PROJECT_CONFIG_DIR") or root / "config" / "STKB")


class SmartSequenceBuilderWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir=None, parent=None):
        super().__init__(parent)
        self.project_config = ProjectConfig(config_dir or _default_config_dir())
        self.service = SmartSequenceBuilderService(self.project_config)
        self.plan: SequenceBuildPlan | None = None
        self.setWindowTitle("Smart Sequence Builder")
        self.resize(1120, 720)
        self._build_ui()
        self._populate_context()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)

        context = QtWidgets.QHBoxLayout()
        self.project_label = QtWidgets.QLabel(self.project_config.project_name)
        self.episode_combo = QtWidgets.QComboBox()
        self.sequence_combo = QtWidgets.QComboBox()
        self.recipe_combo = QtWidgets.QComboBox()
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        for label, widget in (
            ("Project", self.project_label),
            ("Episode", self.episode_combo),
            ("Sequence", self.sequence_combo),
            ("Recipe", self.recipe_combo),
        ):
            context.addWidget(QtWidgets.QLabel(label))
            context.addWidget(widget)
        context.addWidget(self.refresh_button)
        outer.addLayout(context)

        splitter = QtWidgets.QSplitter()
        outer.addWidget(splitter, 1)
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        self.input_tree = QtWidgets.QTreeWidget()
        self.input_tree.setHeaderLabels(["Use", "Input", "Required", "State", "Version", "Source", "Adapter"])
        self.input_tree.setColumnWidth(1, 150)
        self.input_tree.setColumnWidth(5, 270)
        left_layout.addWidget(QtWidgets.QLabel("Resolved Inputs"))
        left_layout.addWidget(self.input_tree, 2)
        left_layout.addWidget(QtWidgets.QLabel("Validation"))
        self.validation_table = QtWidgets.QTableWidget(0, 3)
        self.validation_table.setHorizontalHeaderLabels(["Check", "State", "Detail"])
        self.validation_table.horizontalHeader().setStretchLastSection(True)
        left_layout.addWidget(self.validation_table, 1)
        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.addWidget(QtWidgets.QLabel("Build Summary"))
        self.summary = QtWidgets.QTableWidget(0, 2)
        self.summary.setHorizontalHeaderLabels(["Item", "Value"])
        self.summary.horizontalHeader().setStretchLastSection(True)
        right_layout.addWidget(self.summary, 2)
        right_layout.addWidget(QtWidgets.QLabel("Scene Output"))
        self.output_path = QtWidgets.QLineEdit()
        self.output_path.setReadOnly(True)
        right_layout.addWidget(self.output_path)
        right_layout.addWidget(QtWidgets.QLabel("Manifest"))
        self.manifest_path = QtWidgets.QLineEdit()
        self.manifest_path.setReadOnly(True)
        right_layout.addWidget(self.manifest_path)
        right_layout.addStretch(1)
        splitter.addWidget(right)
        splitter.setSizes([770, 350])

        actions = QtWidgets.QHBoxLayout()
        self.build_button = QtWidgets.QPushButton("Stage Sequence")
        self.build_button.setMinimumHeight(38)
        self.status_label = QtWidgets.QLabel("")
        actions.addWidget(self.build_button)
        actions.addWidget(self.status_label, 1)
        outer.addLayout(actions)

        self.refresh_button.clicked.connect(self.refresh)
        self.episode_combo.currentTextChanged.connect(self._episode_changed)
        self.sequence_combo.currentTextChanged.connect(lambda _text: self.refresh())
        self.recipe_combo.currentTextChanged.connect(lambda _text: self.refresh())
        self.input_tree.itemChanged.connect(self._input_changed)
        self.input_tree.itemSelectionChanged.connect(self._take_selected)
        self.build_button.clicked.connect(self.build)

    def _populate_context(self):
        sequences = self.service.sequences()
        self._sequences = sequences
        self.episode_combo.blockSignals(True)
        self.episode_combo.clear()
        self.episode_combo.addItems(sorted({item.episode for item in sequences}))
        self.episode_combo.blockSignals(False)
        self.recipe_combo.addItems(self.service.recipes())
        self._episode_changed(self.episode_combo.currentText())

    def _episode_changed(self, episode):
        current = self.sequence_combo.currentText()
        self.sequence_combo.blockSignals(True)
        self.sequence_combo.clear()
        self.sequence_combo.addItems([item.sequence for item in self._sequences if item.episode == episode])
        index = self.sequence_combo.findText(current)
        if index >= 0:
            self.sequence_combo.setCurrentIndex(index)
        self.sequence_combo.blockSignals(False)
        self.refresh()

    def refresh(self):
        episode, sequence = self.episode_combo.currentText(), self.sequence_combo.currentText()
        if not episode or not sequence:
            self.plan = None
            self.build_button.setEnabled(False)
            return
        enabled = {}
        selected_take = ""
        for index in range(self.input_tree.topLevelItemCount()):
            item = self.input_tree.topLevelItem(index)
            enabled[item.data(0, QtCore.Qt.UserRole)] = item.checkState(0) == QtCore.Qt.Checked
            for child_index in range(item.childCount()):
                child = item.child(child_index)
                if child.data(0, QtCore.Qt.UserRole + 1):
                    selected_take = child.data(0, QtCore.Qt.UserRole)
        self.plan = self.service.plan(
            episode, sequence, self.recipe_combo.currentText(),
            virtual_camera_take=selected_take, enabled=enabled,
        )
        self._render()

    def _render(self):
        if not self.plan:
            return
        self.input_tree.blockSignals(True)
        self.input_tree.clear()
        for row in self.plan.inputs:
            item = self._tree_item(row)
            self.input_tree.addTopLevelItem(item)
            item.setExpanded(True)
            for child in row.children:
                child_item = self._tree_item(child, checkable=False)
                child_item.setData(0, QtCore.Qt.UserRole + 1, child.key == self.plan.virtual_camera_take)
                if child.key == self.plan.virtual_camera_take:
                    child_item.setSelected(True)
                item.addChild(child_item)
        self.input_tree.blockSignals(False)

        self.validation_table.setRowCount(len(self.plan.validation))
        for row_index, result in enumerate(self.plan.validation):
            for column, value in enumerate((result.label, result.state, result.detail)):
                cell = QtWidgets.QTableWidgetItem(value)
                if column == 1:
                    cell.setForeground(self._state_color(result.state))
                self.validation_table.setItem(row_index, column, cell)

        enabled_count = len([item for item in self.plan.inputs if item.enabled])
        summary = (
            ("Project", self.plan.project), ("Episode", self.plan.episode),
            ("Sequence", self.plan.sequence), ("Recipe", self.plan.recipe),
            ("Target", "Maya"), ("FPS", str(self.plan.fps)),
            ("Range", f"{self.plan.frame_start} - {self.plan.frame_end}"),
            ("Inputs", str(enabled_count)), ("Virtual Camera", self.plan.virtual_camera_take or "--"),
            ("Status", "READY" if self.plan.can_build else "BLOCKED"),
        )
        self.summary.setRowCount(len(summary))
        for row_index, values in enumerate(summary):
            self.summary.setItem(row_index, 0, QtWidgets.QTableWidgetItem(values[0]))
            self.summary.setItem(row_index, 1, QtWidgets.QTableWidgetItem(values[1]))
        self.output_path.setText(self.plan.output_scene)
        self.manifest_path.setText(self.plan.manifest_path)
        self.build_button.setEnabled(self.plan.can_build)
        self.status_label.setText("Ready to build" if self.plan.can_build else "Resolve validation errors")

    def _tree_item(self, row, *, checkable=True):
        item = QtWidgets.QTreeWidgetItem([
            "", row.label, "Required" if row.required else "Optional", row.state,
            row.version or "--", row.path or "--", row.adapter or "--",
        ])
        item.setData(0, QtCore.Qt.UserRole, row.key)
        if checkable:
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.Checked if row.enabled else QtCore.Qt.Unchecked)
        item.setForeground(3, self._state_color(row.state))
        return item

    def _input_changed(self, _item, _column):
        self.refresh()

    def _take_selected(self):
        selected = self.input_tree.selectedItems()
        if not selected or not selected[0].parent():
            return
        parent_key = selected[0].parent().data(0, QtCore.Qt.UserRole)
        if parent_key != "virtual_camera":
            return
        for index in range(selected[0].parent().childCount()):
            selected[0].parent().child(index).setData(0, QtCore.Qt.UserRole + 1, False)
        selected[0].setData(0, QtCore.Qt.UserRole + 1, True)
        self.refresh()

    def build(self):
        if not self.plan:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Stage Sequence",
            "The current Maya scene will be replaced. Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            result = self.service.build(self.plan)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Sequence Build Failed", str(exc))
            return
        self.status_label.setText(f"Built: {result.scene_path}")
        QtWidgets.QMessageBox.information(self, "Sequence Build Complete", result.scene_path)

    @staticmethod
    def _state_color(state):
        return QtGui.QColor({
            "READY": "#70d060", "ACTIVE": "#4aa3df", "AVAILABLE": "#e8b632",
            "WARNING": "#e8b632", "ERROR": "#ef5b5b", "MISSING": "#ef5b5b",
        }.get(state, "#b7bdc4"))


_WINDOW = None


def show(config_dir=None, parent=None):
    global _WINDOW
    from smartlib.core.qt import parent_for_maya
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window_parent = parent_for_maya(QtWidgets, parent)
    if _WINDOW is None:
        _WINDOW = SmartSequenceBuilderWindow(config_dir=config_dir, parent=window_parent)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW
