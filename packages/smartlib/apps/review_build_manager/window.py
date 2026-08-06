from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.apps.review_build_manager.service import (
    ReviewBuildManagerService,
    ReviewShotStatus,
)
from smartlib.apps.review_build_manager.orchestrator import BUILD_MODES
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.tokens import TokenContext


STATE_COLORS = {
    "DIRTY": "#f2ae30",
    "READY": "#79bd69",
    "BUILDING": "#4a98e8",
    "FAILED": "#ef665d",
    "MISSING": "#ef665d",
    "UP TO DATE": "#80bd72",
}
BUILDABLE_STATES = {"READY", "DIRTY", "UP TO DATE"}


class ReviewBuildManagerWindow(QtWidgets.QMainWindow):
    SETTINGS_ORGANIZATION = "SmartPipeline"
    SETTINGS_APPLICATION = "ReviewBuildManager"

    def __init__(self, parent=None, *, config_dir: str | os.PathLike[str]):
        super().__init__(parent)
        self.service = ReviewBuildManagerService(ProjectConfig(config_dir))
        self.rows: list[ReviewShotStatus] = []
        self.current_filter = "ALL"
        self.pending_jobs: list[dict] = []
        self.active_job: dict | None = None
        self.worker_process: QtCore.QProcess | None = None
        self.job_counter = 0
        self.build_content_settings: dict[tuple[str, str, str], dict] = {}
        self.sequence_input_settings: dict[tuple[str, str], dict] = {}
        self.current_build_content_rows: list[dict] = []
        self._startup_context_applied = False
        self.job_timer = QtCore.QTimer(self)
        self.job_timer.setInterval(500)
        self.job_timer.timeout.connect(self._poll_active_job)
        self.setWindowTitle(f"Review Build Manager - {self.service.project_name}")
        self.resize(1500, 860)
        self.setMinimumSize(1050, 620)
        self._build_ui()
        self._connect_signals()
        self._restore_settings()
        self.scan_updates()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(6)

        toolbar = QtWidgets.QHBoxLayout()
        project_label = QtWidgets.QLabel("Project")
        self.project_combo = QtWidgets.QComboBox()
        self.project_combo.addItem(self.service.project_name)
        self.project_combo.setFixedWidth(150)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(BUILD_MODES)
        self.mode_combo.setToolTip("AUTO resolves Stage, Update, Rebuild, or Review Only per shot.")
        self.scope_combo = QtWidgets.QComboBox()
        self.scope_combo.addItems(["Shot", "Sequence"])
        self.department_combo = QtWidgets.QComboBox()
        self.department_combo.addItems(self.service.shots.shot_departments)
        anim_index = self.department_combo.findText("anim")
        self.department_combo.setCurrentIndex(max(0, anim_index))
        self.task_combo = QtWidgets.QComboBox()
        self._populate_tasks()
        self.dry_run_btn = QtWidgets.QPushButton("Dry Run")
        self.scan_btn = QtWidgets.QPushButton("Refresh")
        self.build_selected_btn = QtWidgets.QPushButton("Build Selected")
        self.build_all_changes_btn = QtWidgets.QPushButton("Build All Changes")
        self.build_all_changes_btn.setProperty("primary", True)
        self.build_selected_btn.setEnabled(False)
        self.build_all_changes_btn.setEnabled(False)
        toolbar.addWidget(project_label)
        toolbar.addWidget(self.project_combo)
        toolbar.addStretch(1)
        toolbar.addWidget(QtWidgets.QLabel("Mode"))
        toolbar.addWidget(self.mode_combo)
        toolbar.addWidget(QtWidgets.QLabel("Scope"))
        toolbar.addWidget(self.scope_combo)
        toolbar.addWidget(QtWidgets.QLabel("Dept"))
        toolbar.addWidget(self.department_combo)
        toolbar.addWidget(QtWidgets.QLabel("Task"))
        toolbar.addWidget(self.task_combo)
        toolbar.addWidget(self.dry_run_btn)
        toolbar.addWidget(self.scan_btn)
        toolbar.addWidget(self.build_selected_btn)
        toolbar.addWidget(self.build_all_changes_btn)
        root.addLayout(toolbar)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        root.addWidget(splitter, 1)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([220, 820, 390])
        splitter.setChildrenCollapsible(False)
        self.main_splitter = splitter

        self.footer_label = QtWidgets.QLabel("Ready")
        self.footer_label.setObjectName("footerLabel")
        root.addWidget(self.footer_label)
        self._apply_style()

    def _build_stage_inputs_panel(self) -> QtWidgets.QWidget:
        self.stage_inputs_panel = QtWidgets.QGroupBox("Stage Inputs")
        layout = QtWidgets.QGridLayout(self.stage_inputs_panel)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(5)
        self.input_policy_combo = QtWidgets.QComboBox()
        self.input_policy_combo.addItems(
            ["Generate Missing", "Regenerate Selected", "Use Existing"]
        )
        self.input_context_combo = QtWidgets.QComboBox()
        self.input_context_combo.addItems(self.service.asset_context_profiles())
        self.input_camera_edit = QtWidgets.QLineEdit()
        self.input_camera_edit.setPlaceholderText("latest camera (optional override)")
        self.input_overlay_check = QtWidgets.QCheckBox("Use Layout Overlay")
        self.input_overlay_check.setChecked(True)
        self.input_placements_check = QtWidgets.QCheckBox("Use Placements")
        self.input_placements_check.setChecked(True)
        self.input_exclude_cast_edit = QtWidgets.QLineEdit()
        self.input_exclude_cast_edit.setPlaceholderText(
            "Exclude cast keys, comma separated"
        )
        self.input_comment_edit = QtWidgets.QLineEdit()
        self.input_comment_edit.setPlaceholderText("Batch input comment")
        self.generate_inputs_btn = QtWidgets.QPushButton("Generate Inputs")
        layout.addWidget(QtWidgets.QLabel("Policy"), 0, 0)
        layout.addWidget(self.input_policy_combo, 0, 1)
        layout.addWidget(QtWidgets.QLabel("Context"), 1, 0)
        layout.addWidget(self.input_context_combo, 1, 1)
        layout.addWidget(QtWidgets.QLabel("Camera"), 2, 0)
        layout.addWidget(self.input_camera_edit, 2, 1)
        option_row = QtWidgets.QHBoxLayout()
        option_row.addWidget(self.input_placements_check)
        option_row.addWidget(self.input_overlay_check)
        layout.addLayout(option_row, 3, 0, 1, 2)
        layout.addWidget(QtWidgets.QLabel("Exclude Cast"), 4, 0)
        layout.addWidget(self.input_exclude_cast_edit, 4, 1)
        layout.addWidget(QtWidgets.QLabel("Comment"), 5, 0)
        layout.addWidget(self.input_comment_edit, 5, 1)
        layout.addWidget(self.generate_inputs_btn, 6, 0, 1, 2)
        layout.setColumnStretch(1, 1)
        return self.stage_inputs_panel

    def _build_sequence_inputs_panel(self) -> QtWidgets.QWidget:
        self.sequence_inputs_panel = QtWidgets.QGroupBox("Sequence Recipe Inputs")
        layout = QtWidgets.QVBoxLayout(self.sequence_inputs_panel)
        tools = QtWidgets.QHBoxLayout()
        self.sequence_recipe_combo = QtWidgets.QComboBox()
        self.sequence_recipe_combo.addItems(self.service.sequence_recipes())
        self.sequence_validation_label = QtWidgets.QLabel("Select a sequence")
        tools.addWidget(QtWidgets.QLabel("Recipe"))
        tools.addWidget(self.sequence_recipe_combo)
        tools.addStretch(1)
        tools.addWidget(self.sequence_validation_label)
        layout.addLayout(tools)
        self.sequence_inputs_tree = QtWidgets.QTreeWidget()
        self.sequence_inputs_tree.setHeaderLabels(
            ["Use", "Input", "Required", "State", "Version", "Source", "Adapter"]
        )
        self.sequence_inputs_tree.header().setStretchLastSection(True)
        layout.addWidget(self.sequence_inputs_tree)
        return self.sequence_inputs_panel

    def _build_left_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setMinimumWidth(185)
        panel.setMaximumWidth(270)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._section_label("Build Filter"))
        self.filter_list = QtWidgets.QListWidget()
        self.filter_list.setFixedHeight(180)
        layout.addWidget(self.filter_list)
        layout.addWidget(self._section_label("Shots"))
        self.shot_tree = QtWidgets.QTreeWidget()
        self.shot_tree.setHeaderHidden(True)
        self.shot_tree.setIndentation(12)
        self.shot_tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        layout.addWidget(self.shot_tree, 1)
        return panel

    def _build_center_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        table_tools = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search shot")
        self.search_edit.setClearButtonEnabled(True)
        self.select_all_btn = QtWidgets.QPushButton("Select All")
        self.clear_selection_btn = QtWidgets.QPushButton("Clear Selection")
        self.invert_selection_btn = QtWidgets.QPushButton("Invert Selection")
        self.select_all_btn.setToolTip("Check all visible buildable shots.")
        self.clear_selection_btn.setToolTip("Clear all checked shots.")
        self.invert_selection_btn.setToolTip(
            "Invert checks for visible buildable shots."
        )
        table_tools.addWidget(self.search_edit, 1)
        table_tools.addWidget(self.select_all_btn)
        table_tools.addWidget(self.clear_selection_btn)
        table_tools.addWidget(self.invert_selection_btn)
        layout.addLayout(table_tools)
        self.shot_table = QtWidgets.QTableWidget(0, 9)
        self.shot_table.setHorizontalHeaderLabels(
            [
                "Build",
                "Thumbnail",
                "Shot",
                "Mode",
                "Validation",
                "State",
                "Output Version",
                "Last Review",
                "Comment",
            ]
        )
        self.shot_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.shot_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.shot_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.shot_table.setShowGrid(False)
        self.shot_table.verticalHeader().setVisible(False)
        header = self.shot_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Fixed)
        self.shot_table.setColumnWidth(1, 112)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        for column in range(3, 8):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QtWidgets.QHeaderView.Stretch)
        self.shot_table.setMinimumHeight(190)
        layout.addWidget(self.shot_table, 1)

        self.build_contents_group = QtWidgets.QGroupBox("Build Contents")
        contents_layout = QtWidgets.QVBoxLayout(self.build_contents_group)
        contents_layout.setContentsMargins(6, 6, 6, 6)
        contents_layout.setSpacing(5)
        contents_tools = QtWidgets.QHBoxLayout()
        self.contents_select_all_btn = QtWidgets.QPushButton("Select All")
        self.contents_clear_btn = QtWidgets.QPushButton("Clear")
        self.contents_invert_btn = QtWidgets.QPushButton("Invert")
        self.contents_context_combo = QtWidgets.QComboBox()
        self.contents_context_combo.addItems(self.service.asset_context_profiles())
        self.contents_apply_context_btn = QtWidgets.QPushButton("Review Changes")
        self.contents_apply_context_btn.setToolTip(
            "Review the selected cast context changes before applying them."
        )
        self.contents_summary_label = QtWidgets.QLabel("Select a shot")
        contents_tools.addWidget(self.contents_select_all_btn)
        contents_tools.addWidget(self.contents_clear_btn)
        contents_tools.addWidget(self.contents_invert_btn)
        contents_tools.addStretch(1)
        contents_tools.addWidget(QtWidgets.QLabel("Context to Selected"))
        contents_tools.addWidget(self.contents_context_combo)
        contents_tools.addWidget(self.contents_apply_context_btn)
        contents_tools.addStretch(1)
        contents_tools.addWidget(self.contents_summary_label)
        contents_layout.addLayout(contents_tools)
        self.build_contents_table = QtWidgets.QTableWidget(0, 10)
        self.build_contents_table.setHorizontalHeaderLabels(
            ["Use", "Type", "Name", "Role", "Variant", "Context", "Official", "Latest", "State", "Note"]
        )
        self.build_contents_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.build_contents_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.build_contents_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.build_contents_table.setShowGrid(False)
        self.build_contents_table.verticalHeader().setVisible(False)
        contents_header = self.build_contents_table.horizontalHeader()
        for column in range(9):
            contents_header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        contents_header.setSectionResizeMode(9, QtWidgets.QHeaderView.Stretch)
        self.build_contents_table.setMinimumHeight(205)
        contents_layout.addWidget(self.build_contents_table)
        layout.addWidget(self.build_contents_group, 1)
        layout.addWidget(self._build_sequence_inputs_panel(), 1)

        layout.addWidget(self._section_label("Job Queue"))
        self.queue_table = QtWidgets.QTableWidget(0, 6)
        self.queue_table.setHorizontalHeaderLabels(
            ["Job", "Shot", "Task", "Status", "Progress", "Elapsed"]
        )
        self.queue_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.queue_table.setShowGrid(False)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.horizontalHeader().setStretchLastSection(True)
        self.queue_table.setFixedHeight(155)
        layout.addWidget(self.queue_table)
        return panel

    def _build_right_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setMinimumWidth(330)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.detail_title = self._section_label("Output History")
        layout.addWidget(self.detail_title)
        layout.addWidget(self._build_stage_inputs_panel())
        self.detail_summary = QtWidgets.QLabel("Select a shot.")
        self.detail_summary.setWordWrap(True)
        self.detail_summary.setObjectName("detailSummary")
        layout.addWidget(self.detail_summary)
        self.output_table = QtWidgets.QTableWidget(0, 4)
        self.output_table.setHorizontalHeaderLabels(["Version", "State", "Updated", "Movie"])
        self.output_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.output_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.output_table.setShowGrid(False)
        self.output_table.verticalHeader().setVisible(False)
        self.output_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.output_table, 1)
        self.construct_title = self._section_label("Construct Scene Files")
        layout.addWidget(self.construct_title)
        self.construct_list = QtWidgets.QTreeWidget()
        self.construct_list.setHeaderLabels(["Version", "State", "Updated", "Scene"])
        self.construct_list.setRootIsDecorated(False)
        self.construct_list.setIndentation(10)
        self.construct_list.setMinimumHeight(150)
        self.construct_list.header().setStretchLastSection(True)
        layout.addWidget(self.construct_list, 1)
        self.open_output_btn = QtWidgets.QPushButton("Open Output Folder")
        self.open_output_btn.setEnabled(False)
        action_row = QtWidgets.QHBoxLayout()
        action_row.addWidget(self.open_output_btn)
        layout.addLayout(action_row)
        return panel

    def _connect_signals(self) -> None:
        self.scan_btn.clicked.connect(self.scan_updates)
        self.build_selected_btn.clicked.connect(self.build_selected)
        self.build_all_changes_btn.clicked.connect(self.build_all_changes)
        self.select_all_btn.clicked.connect(self.select_all_shots)
        self.clear_selection_btn.clicked.connect(self.clear_shot_selection)
        self.invert_selection_btn.clicked.connect(self.invert_shot_selection)
        self.dry_run_btn.clicked.connect(self.dry_run)
        self.department_combo.currentTextChanged.connect(self._department_changed)
        self.task_combo.currentTextChanged.connect(self._refresh_plan_columns)
        self.mode_combo.currentTextChanged.connect(self._refresh_plan_columns)
        self.mode_combo.currentTextChanged.connect(self._update_stage_inputs_visibility)
        self.scope_combo.currentTextChanged.connect(self._scope_changed)
        self.input_policy_combo.currentTextChanged.connect(self._refresh_plan_columns)
        self.input_context_combo.currentTextChanged.connect(self._refresh_plan_columns)
        self.input_camera_edit.textChanged.connect(self._refresh_plan_columns)
        self.input_placements_check.toggled.connect(self._refresh_plan_columns)
        self.input_overlay_check.toggled.connect(self._refresh_plan_columns)
        self.input_exclude_cast_edit.textChanged.connect(self._refresh_plan_columns)
        self.generate_inputs_btn.clicked.connect(self.generate_stage_inputs)
        self.search_edit.textChanged.connect(self._apply_filters)
        self.filter_list.currentItemChanged.connect(self._filter_changed)
        self.shot_tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self.shot_table.itemSelectionChanged.connect(self._table_selection_changed)
        self.shot_table.itemChanged.connect(self._shot_item_changed)
        self.open_output_btn.clicked.connect(self._open_output_folder)
        self.contents_select_all_btn.clicked.connect(lambda: self._set_content_checks("select"))
        self.contents_clear_btn.clicked.connect(lambda: self._set_content_checks("clear"))
        self.contents_invert_btn.clicked.connect(lambda: self._set_content_checks("invert"))
        self.contents_apply_context_btn.clicked.connect(self._apply_context_to_contents)
        self.build_contents_table.itemChanged.connect(self._content_item_changed)
        self.sequence_recipe_combo.currentTextChanged.connect(
            self._sequence_recipe_changed
        )
        self.sequence_inputs_tree.itemChanged.connect(
            self._sequence_input_changed
        )
        self.sequence_inputs_tree.itemSelectionChanged.connect(
            self._sequence_take_selected
        )
        self._update_stage_inputs_visibility()

    def scan_updates(self) -> None:
        selected_status = self._selected_status() if self.rows else None
        selected_identity = selected_status.identity if selected_status else None
        selected_scope = self._tree_scope() if hasattr(self, "shot_tree") else None
        self.scan_btn.setEnabled(False)
        self.footer_label.setText("Scanning shots...")
        QtWidgets.QApplication.processEvents()
        try:
            self.rows = self.service.scan()
            self._populate_filters()
            self._populate_tree()
            self._apply_filters()
            if self._startup_context_applied:
                self._restore_shot_selection(selected_identity, selected_scope)
            else:
                self._focus_working_shot()
            self._update_build_buttons()
            dirty = sum(row.state == "DIRTY" for row in self.rows)
            missing = sum(row.state == "MISSING" for row in self.rows)
            self.footer_label.setText(
                f"{dirty} shots require rebuild  |  {missing} package missing  |  Worker: not connected"
            )
        except Exception as exc:
            self.footer_label.setText(f"Scan failed: {exc}")
            QtWidgets.QMessageBox.critical(self, "Review Scan Failed", str(exc))
        finally:
            self.scan_btn.setEnabled(True)

    def _populate_filters(self) -> None:
        selected = self.current_filter
        states = ["ALL", "DIRTY", "READY", "MISSING", "UP TO DATE"]
        counts = {"ALL": len(self.rows)}
        counts.update({state: sum(row.state == state for row in self.rows) for state in states[1:]})
        self.filter_list.blockSignals(True)
        self.filter_list.clear()
        for state in states:
            item = QtWidgets.QListWidgetItem(f"{state:<12} {counts[state]}")
            item.setData(QtCore.Qt.UserRole, state)
            color = STATE_COLORS.get(state)
            if color:
                item.setForeground(QtGui.QColor(color))
            self.filter_list.addItem(item)
            if state == selected:
                self.filter_list.setCurrentItem(item)
        self.filter_list.blockSignals(False)

    def _populate_tree(self) -> None:
        self.shot_tree.blockSignals(True)
        self.shot_tree.clear()
        episodes: dict[str, QtWidgets.QTreeWidgetItem] = {}
        sequences: dict[tuple[str, str], QtWidgets.QTreeWidgetItem] = {}
        for row in self.rows:
            identity = row.identity
            episode_item = episodes.get(identity.episode)
            if episode_item is None:
                episode_item = QtWidgets.QTreeWidgetItem([identity.episode])
                episode_item.setData(0, QtCore.Qt.UserRole, ("episode", identity.episode))
                self.shot_tree.addTopLevelItem(episode_item)
                episodes[identity.episode] = episode_item
            key = (identity.episode, identity.sequence)
            sequence_item = sequences.get(key)
            if sequence_item is None:
                sequence_item = QtWidgets.QTreeWidgetItem([identity.sequence])
                sequence_item.setData(0, QtCore.Qt.UserRole, ("sequence", *key))
                episode_item.addChild(sequence_item)
                sequences[key] = sequence_item
            shot_item = QtWidgets.QTreeWidgetItem([identity.shot])
            shot_item.setData(
                0,
                QtCore.Qt.UserRole,
                ("shot", identity.episode, identity.sequence, identity.shot),
            )
            sequence_item.addChild(shot_item)
        self.shot_tree.expandAll()
        self.shot_tree.blockSignals(False)

    def _focus_working_shot(self) -> None:
        if self._startup_context_applied:
            return
        self._startup_context_applied = True
        identity = self._working_shot_identity()
        if identity is None:
            return

        self.current_filter = "ALL"
        for index in range(self.filter_list.count()):
            item = self.filter_list.item(index)
            if item.data(QtCore.Qt.UserRole) == "ALL":
                self.filter_list.setCurrentItem(item)
                break

        self._restore_shot_selection(
            identity,
            ("shot", identity.episode, identity.sequence, identity.shot),
        )

    def _restore_shot_selection(self, identity, scope=None) -> None:
        target = scope
        if target is None and identity is not None:
            target = (
                "shot",
                identity.episode,
                identity.sequence,
                identity.shot,
            )
        if target is None:
            return

        iterator = QtWidgets.QTreeWidgetItemIterator(self.shot_tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, QtCore.Qt.UserRole) == target:
                self.shot_tree.setCurrentItem(item)
                item.setSelected(True)
                self.shot_tree.scrollToItem(item)
                break
            iterator += 1

        self._apply_filters()
        if identity is None:
            return
        identity_key = (identity.episode, identity.sequence, identity.shot)
        for row in range(self.shot_table.rowCount()):
            item = self.shot_table.item(row, 2)
            if item and tuple(item.data(QtCore.Qt.UserRole) or ()) == identity_key:
                self.shot_table.selectRow(row)
                self.shot_table.scrollToItem(item)
                break

    def _working_shot_identity(self):
        scene_path = self._maya_scene_path()
        if scene_path:
            identity = self.service.shots.shot_identity_from_path(scene_path)
            if identity is not None:
                return identity

        tokens = TokenContext.from_environment()
        if tokens.episode and tokens.sequence and tokens.shot:
            from smartlib.apps.shot_manager import ShotIdentity

            identity = ShotIdentity(tokens.episode, tokens.sequence, tokens.shot)
            if any(row.identity == identity for row in self.rows):
                return identity
        return None

    @staticmethod
    def _maya_scene_path() -> str:
        try:
            import maya.cmds as cmds

            return str(cmds.file(query=True, sceneName=True) or "")
        except Exception:
            return ""

    def _apply_filters(self) -> None:
        query = self.search_edit.text().strip().lower()
        tree_scope = self._tree_scope()
        visible = []
        for row in self.rows:
            identity = row.identity
            text = f"{identity.episode}/{identity.sequence}/{identity.shot}".lower()
            if query and query not in text:
                continue
            if self.current_filter != "ALL" and row.state != self.current_filter:
                continue
            if tree_scope and not self._identity_matches_scope(identity, tree_scope):
                continue
            visible.append(row)
        self._populate_shot_table(visible)

    def _populate_shot_table(self, rows: list[ReviewShotStatus]) -> None:
        self.shot_table.blockSignals(True)
        self.shot_table.setRowCount(0)
        for row_data in rows:
            row = self.shot_table.rowCount()
            self.shot_table.insertRow(row)
            self.shot_table.setRowHeight(row, 72)
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.Unchecked)
            check.setData(
                QtCore.Qt.UserRole,
                (
                    row_data.identity.episode,
                    row_data.identity.sequence,
                    row_data.identity.shot,
                ),
            )
            self.shot_table.setItem(row, 0, check)
            thumb = QtWidgets.QTableWidgetItem()
            if row_data.thumbnail:
                pixmap = QtGui.QPixmap(row_data.thumbnail)
                if not pixmap.isNull():
                    thumb.setIcon(QtGui.QIcon(pixmap))
            self.shot_table.setItem(row, 1, thumb)
            identity = row_data.identity
            shot = QtWidgets.QTableWidgetItem(identity.shot)
            shot.setData(QtCore.Qt.UserRole, (identity.episode, identity.sequence, identity.shot))
            self.shot_table.setItem(row, 2, shot)
            plan = self._build_plan(identity)
            mode_item = QtWidgets.QTableWidgetItem(plan.resolved_mode)
            mode_item.setToolTip(plan.summary)
            self.shot_table.setItem(row, 3, mode_item)
            validation_text = (
                "OK"
                if not plan.validations
                else f"{plan.state}: {len(plan.validations)}"
            )
            validation_item = QtWidgets.QTableWidgetItem(validation_text)
            validation_item.setToolTip(
                "\n".join(
                    f"[{item.severity}] {item.code}: {item.message}"
                    for item in plan.validations
                )
            )
            validation_item.setForeground(
                QtGui.QColor(
                    "#ef665d"
                    if plan.state == "BLOCKED"
                    else "#f2ae30"
                    if plan.state == "WARNING"
                    else "#80bd72"
                )
            )
            self.shot_table.setItem(row, 4, validation_item)
            if plan.resolved_mode == "REVIEW ONLY":
                display_state = row_data.state
                version_label = row_data.output_label
            else:
                display_state = plan.state
                if self.scope_combo.currentText() == "Sequence":
                    from smartlib.apps.shot_manager import SequenceIdentity

                    version_label = "Next " + self.service.next_sequence_construct_version(
                        SequenceIdentity(identity.episode, identity.sequence),
                        plan.department,
                        plan.task,
                    )
                else:
                    version_label = "Next " + self.service.next_construct_version(
                        identity,
                        plan.department,
                        plan.task,
                    )
            state = QtWidgets.QTableWidgetItem(display_state)
            state.setForeground(
                QtGui.QColor(STATE_COLORS.get(display_state, "#dddddd"))
            )
            self.shot_table.setItem(row, 5, state)
            self.shot_table.setItem(row, 6, QtWidgets.QTableWidgetItem(version_label))
            self.shot_table.setItem(row, 7, QtWidgets.QTableWidgetItem(row_data.last_review))
            self.shot_table.setItem(row, 8, QtWidgets.QTableWidgetItem(row_data.comment))
            if not plan.buildable:
                check.setFlags(QtCore.Qt.ItemIsEnabled)
        self.shot_table.blockSignals(False)
        self._update_build_buttons()

    def _show_details(self, row_data: ReviewShotStatus | None) -> None:
        self.output_table.setRowCount(0)
        self.construct_list.clear()
        self.open_output_btn.setEnabled(False)
        if not row_data:
            self.current_build_content_rows = []
            self.detail_title.setText("Output History")
            self.detail_summary.setText("Select a shot.")
            self._populate_build_contents(None)
            return
        identity = row_data.identity
        if self.scope_combo.currentText() == "Sequence":
            from smartlib.apps.shot_manager import SequenceIdentity

            sequence_identity = SequenceIdentity(identity.episode, identity.sequence)
            self._populate_sequence_inputs(sequence_identity)
            constructs = self.service.list_sequence_constructs(
                sequence_identity,
                self.department_combo.currentText(),
                self.task_combo.currentText(),
            )
            checked = [
                shot for episode, sequence, shot in self._checked_identities()
                if episode == identity.episode and sequence == identity.sequence
            ]
            self.detail_title.setText(f"Sequence - {identity.episode}/{identity.sequence}")
            self.detail_summary.setText(
                f"Sequence: {identity.episode}/{identity.sequence}\n"
                f"Selected shots: {', '.join(checked) if checked else 'none'}\n"
                f"Construct versions: {len(constructs)}"
            )
            self.construct_title.setText(
                f"Sequence Construct: {identity.sequence} / {len(constructs)} versions"
            )
            for construct in constructs:
                scene = Path(construct["scene"]).name if construct["scene"] else "-"
                item = QtWidgets.QTreeWidgetItem(
                    [construct["version"], construct["state"], construct["updated"], scene]
                )
                item.setData(0, QtCore.Qt.UserRole, construct["scene"])
                shots = construct.get("shots") or []
                item.setToolTip(0, "Shots: " + ", ".join(shots) if shots else "All shots")
                self.construct_list.addTopLevelItem(item)
            self._populate_build_contents(None)
            return
        self.detail_title.setText(
            f"Output History - {identity.episode}/{identity.sequence}/{identity.shot}"
        )
        self.detail_summary.setText(
            f"State: {row_data.state}\n"
            f"Animation Package: {row_data.source_version or '-'}\n"
            f"Output: {row_data.output_label}\n"
            f"{row_data.message}"
        )
        for output in row_data.outputs:
            row = self.output_table.rowCount()
            self.output_table.insertRow(row)
            self.output_table.setItem(row, 0, QtWidgets.QTableWidgetItem(output.version))
            self.output_table.setItem(row, 1, QtWidgets.QTableWidgetItem(output.state))
            self.output_table.setItem(row, 2, QtWidgets.QTableWidgetItem(output.updated))
            movie_name = Path(output.movie).name if output.movie else "-"
            movie = QtWidgets.QTableWidgetItem(movie_name)
            movie.setData(QtCore.Qt.UserRole, output.directory)
            self.output_table.setItem(row, 3, movie)
        self.open_output_btn.setEnabled(bool(row_data.outputs))
        self._populate_build_contents(row_data)
        constructs = self.service.list_constructs(
            identity,
            self.department_combo.currentText(),
            self.task_combo.currentText(),
        )
        self.construct_title.setText(
            f"Construct: {identity.shot} / {len(constructs)} versions"
        )
        for construct in constructs:
            scene = Path(construct["scene"]).name if construct["scene"] else "-"
            item = QtWidgets.QTreeWidgetItem(
                [construct["version"], construct["state"], construct["updated"], scene]
            )
            item.setData(0, QtCore.Qt.UserRole, construct["scene"])
            validation_results = construct.get("validation_results") or []
            if validation_results:
                details = "\n".join(
                    f"[{entry.get('severity', '')}] {entry.get('code', '')}: "
                    f"{entry.get('message', '')}"
                    for entry in validation_results
                )
                for column in range(self.construct_list.columnCount()):
                    item.setToolTip(column, details)
            color = "#80bd72" if construct["state"] in {"PASSED", "READY", "OK"} else "#f2ae30"
            item.setForeground(1, QtGui.QColor(color))
            self.construct_list.addTopLevelItem(item)

    @staticmethod
    def _identity_key(row_data: ReviewShotStatus) -> tuple[str, str, str]:
        identity = row_data.identity
        return identity.episode, identity.sequence, identity.shot

    def _content_settings(self, row_data: ReviewShotStatus) -> dict:
        key = self._identity_key(row_data)
        return self.build_content_settings.setdefault(
            key,
            {"contexts": {}, "excluded": set()},
        )

    def _populate_build_contents(self, row_data: ReviewShotStatus | None) -> None:
        self.build_contents_table.blockSignals(True)
        self.build_contents_table.setRowCount(0)
        if not row_data:
            self.build_contents_group.setTitle("Build Contents")
            self.contents_summary_label.setText("Select a shot")
            self.build_contents_table.blockSignals(False)
            return
        identity = row_data.identity
        settings = self._content_settings(row_data)
        rows = self.service.build_contents(
            identity,
            default_context=self.input_context_combo.currentText(),
            cast_contexts=settings["contexts"],
            excluded_cast=list(settings["excluded"]),
        )
        self.current_build_content_rows = rows
        self.build_contents_group.setTitle(f"Build Contents - {identity.shot}")
        for row_index, data in enumerate(rows):
            row = self.build_contents_table.rowCount()
            self.build_contents_table.insertRow(row)
            self.build_contents_table.setRowHeight(row, 28)
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.Checked if data["enabled"] else QtCore.Qt.Unchecked)
            check.setData(QtCore.Qt.UserRole, row_index)
            check.setData(QtCore.Qt.UserRole + 1, data["cast_key"])
            self.build_contents_table.setItem(row, 0, check)
            values = [data["type"], data["cast_key"], data["role"], data["variant"]]
            for column, value in enumerate(values, start=1):
                self.build_contents_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
            if data["type"] == "rig":
                context_combo = QtWidgets.QComboBox()
                context_combo.addItems(self.service.asset_context_profiles())
                context_combo.setCurrentText(data["context"])
                context_combo.setProperty("content_row", row_index)
                context_combo.currentTextChanged.connect(self._content_context_changed)
                self.build_contents_table.setCellWidget(row, 5, context_combo)
            else:
                context_item = QtWidgets.QTableWidgetItem("-")
                context_item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.build_contents_table.setItem(row, 5, context_item)
            for column, key in ((6, "official"), (7, "latest"), (8, "state"), (9, "note")):
                item = QtWidgets.QTableWidgetItem(str(data[key]))
                if column == 8:
                    color = "#80bd72" if data[key] == "READY" else "#f2ae30" if data[key] == "UPDATE AVAILABLE" else "#999999" if data[key] == "EXCLUDED" else "#ef665d"
                    item.setForeground(QtGui.QColor(color))
                self.build_contents_table.setItem(row, column, item)
        enabled = sum(bool(row["enabled"]) for row in rows)
        self.contents_summary_label.setText(f"{enabled} of {len(rows)} items enabled")
        self.build_contents_table.blockSignals(False)

    def _content_item_changed(self, item) -> None:
        if item.column() != 0:
            return
        status = self._selected_status()
        row_index = item.data(QtCore.Qt.UserRole)
        if status is None or row_index is None:
            return
        data = self.current_build_content_rows[int(row_index)]
        data["component"]["enabled"] = item.checkState() == QtCore.Qt.Checked
        excluded = self._content_settings(status)["excluded"]
        name = str(data["cast_key"])
        if data["component"]["enabled"]:
            excluded.discard(name)
        else:
            excluded.add(name)
        self.service.save_build_contents(status.identity, self.current_build_content_rows)
        self._populate_build_contents(status)

    def _content_context_changed(self, context: str) -> None:
        status = self._selected_status()
        sender = self.sender()
        row_index = sender.property("content_row") if sender else None
        if status is None or row_index is None:
            return
        data = self.current_build_content_rows[int(row_index)]
        name = str(data["cast_key"])
        self._content_settings(status)["contexts"][name] = str(context)
        source = dict(data["component"].get("source") or {})
        source["context"] = str(context)
        data["component"]["source"] = source
        self.service.save_build_contents(status.identity, self.current_build_content_rows)
        self._populate_build_contents(status)

    def _set_content_checks(self, operation: str) -> None:
        rows = self.build_contents_table.selectionModel().selectedRows()
        target_rows = [index.row() for index in rows] or list(range(self.build_contents_table.rowCount()))
        self.build_contents_table.blockSignals(True)
        for row in target_rows:
            item = self.build_contents_table.item(row, 0)
            if not item:
                continue
            if operation == "select":
                item.setCheckState(QtCore.Qt.Checked)
            elif operation == "clear":
                item.setCheckState(QtCore.Qt.Unchecked)
            else:
                item.setCheckState(QtCore.Qt.Unchecked if item.checkState() == QtCore.Qt.Checked else QtCore.Qt.Checked)
        self.build_contents_table.blockSignals(False)
        status = self._selected_status()
        if status:
            settings = self._content_settings(status)
            settings["excluded"] = {
                str(self.build_contents_table.item(row, 0).data(QtCore.Qt.UserRole + 1))
                for row in range(self.build_contents_table.rowCount())
                if self.build_contents_table.item(row, 0).checkState() != QtCore.Qt.Checked
            }
            for row in range(self.build_contents_table.rowCount()):
                self.current_build_content_rows[row]["component"]["enabled"] = (
                    self.build_contents_table.item(row, 0).checkState()
                    == QtCore.Qt.Checked
                )
            self.service.save_build_contents(
                status.identity, self.current_build_content_rows
            )
            self._populate_build_contents(status)

    def _apply_context_to_contents(self) -> None:
        status = self._selected_status()
        if not status:
            return
        rows = self.build_contents_table.selectionModel().selectedRows()
        target_rows = [index.row() for index in rows] or list(range(self.build_contents_table.rowCount()))
        context = self.contents_context_combo.currentText()
        changes = []
        for row in target_rows:
            item = self.build_contents_table.item(row, 0)
            context_widget = self.build_contents_table.cellWidget(row, 5)
            if not item or not isinstance(context_widget, QtWidgets.QComboBox):
                continue
            current_context = (
                context_widget.currentText()
                if isinstance(context_widget, QtWidgets.QComboBox)
                else ""
            )
            changes.append(
                {
                    "row": row,
                    "cast_key": str(item.data(QtCore.Qt.UserRole + 1) or ""),
                    "enabled": item.checkState() == QtCore.Qt.Checked,
                    "current": current_context,
                    "new": context,
                }
            )
        if not changes or not self._confirm_context_changes(status, changes):
            return
        settings = self._content_settings(status)
        for change in changes:
            settings["contexts"][change["cast_key"]] = change["new"]
            data = self.current_build_content_rows[change["row"]]
            source = dict(data["component"].get("source") or {})
            source["context"] = change["new"]
            data["component"]["source"] = source
        self.service.save_build_contents(status.identity, self.current_build_content_rows)
        self._populate_build_contents(status)

    def _confirm_context_changes(self, status, changes: list[dict]) -> bool:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Review Build Content Changes")
        dialog.resize(620, 360)
        layout = QtWidgets.QVBoxLayout(dialog)
        identity = status.identity
        title = QtWidgets.QLabel(
            f"{identity.episode} / {identity.sequence} / {identity.shot}"
        )
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(
            QtWidgets.QLabel(
                "The following cast contexts will be changed for the next build."
            )
        )

        table = QtWidgets.QTableWidget(len(changes), 4)
        table.setHorizontalHeaderLabels(["Cast", "Use", "Current", "New"])
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        for row, change in enumerate(changes):
            values = (
                change["cast_key"],
                "Enabled" if change["enabled"] else "Excluded",
                change["current"] or "-",
                change["new"] or "-",
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in range(1, 4):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(table, 1)

        buttons = QtWidgets.QDialogButtonBox()
        cancel_button = buttons.addButton(
            "Cancel", QtWidgets.QDialogButtonBox.RejectRole
        )
        apply_button = buttons.addButton(
            "Apply Changes", QtWidgets.QDialogButtonBox.AcceptRole
        )
        apply_button.setDefault(True)
        cancel_button.clicked.connect(dialog.reject)
        apply_button.clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        return dialog.exec() == QtWidgets.QDialog.Accepted

    def _selected_status(self) -> ReviewShotStatus | None:
        selected = self.shot_table.selectionModel().selectedRows()
        if not selected:
            return None
        row = selected[0].row()
        item = self.shot_table.item(row, 2)
        identity = item.data(QtCore.Qt.UserRole) if item else None
        if not identity:
            return None
        return next(
            (
                status
                for status in self.rows
                if (status.identity.episode, status.identity.sequence, status.identity.shot)
                == tuple(identity)
            ),
            None,
        )

    def _table_selection_changed(self) -> None:
        self._show_details(self._selected_status())
        self._update_build_buttons()

    def _shot_item_changed(self, item) -> None:
        if item and item.column() == 0:
            self._update_build_buttons()
            if self.scope_combo.currentText() == "Sequence":
                self._show_details(self._selected_status())

    def _filter_changed(self, current, _previous) -> None:
        self.current_filter = str(current.data(QtCore.Qt.UserRole) if current else "ALL")
        self._apply_filters()

    def _tree_selection_changed(self) -> None:
        self._apply_filters()

    def _tree_scope(self):
        selected = self.shot_tree.selectedItems()
        return selected[0].data(0, QtCore.Qt.UserRole) if selected else None

    @staticmethod
    def _identity_matches_scope(identity, scope) -> bool:
        kind = scope[0]
        if kind == "episode":
            return identity.episode == scope[1]
        if kind == "sequence":
            return identity.episode == scope[1] and identity.sequence == scope[2]
        if kind == "shot":
            return (
                identity.episode == scope[1]
                and identity.sequence == scope[2]
                and identity.shot == scope[3]
            )
        return True

    def _open_output_folder(self) -> None:
        status = self._selected_status()
        if not status or not status.outputs:
            return
        path = status.outputs[0].directory
        try:
            os.startfile(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Open Output Folder", str(exc))

    def _accept_to_work(self) -> None:
        status = self._selected_status()
        if not status or not status.outputs:
            return
        output = status.outputs[0]
        if not output.scene:
            QtWidgets.QMessageBox.information(
                self,
                "Accept to Work",
                "The latest output has no generated scene.",
            )
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Accept to Work",
            "Create a new Work version from the generated verification scene?\n"
            "The existing artist Work scene will not be overwritten.",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            path = self.service.accept_output_to_work(
                status.identity,
                output,
                department=self.department_combo.currentText(),
                task=self.task_combo.currentText(),
            )
            self.footer_label.setText(f"Accepted to Work: {path}")
            QtWidgets.QMessageBox.information(
                self,
                "Accepted to Work",
                f"Created a new Work version:\n{path}",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Accept to Work Failed", str(exc))

    def build_selected(self) -> None:
        identities = self._checked_identities()
        if not identities:
            selected = self._selected_status()
            selected_plan = (
                self._build_plan(selected.identity)
                if selected
                else None
            )
            if selected and selected_plan and selected_plan.buildable:
                identities = [
                    (
                        selected.identity.episode,
                        selected.identity.sequence,
                        selected.identity.shot,
                    )
                ]
        self._enqueue_builds(identities)

    def build_all_changes(self) -> None:
        identities = [
            (row.identity.episode, row.identity.sequence, row.identity.shot)
            for row in self.rows
            if row.state != "UP TO DATE"
            and self._build_plan(row.identity).buildable
        ]
        self._enqueue_builds(identities)

    def build_dirty(self) -> None:
        """Compatibility alias for older callers."""
        self.build_all_changes()

    def select_all_shots(self) -> None:
        self._set_shot_checks("select")

    def clear_shot_selection(self) -> None:
        self._set_shot_checks("clear")

    def invert_shot_selection(self) -> None:
        self._set_shot_checks("invert")

    def _set_shot_checks(self, operation: str) -> None:
        self.shot_table.blockSignals(True)
        try:
            for row in range(self.shot_table.rowCount()):
                item = self.shot_table.item(row, 0)
                if item is None:
                    continue
                flags = item.flags()
                if not flags & QtCore.Qt.ItemIsUserCheckable:
                    continue
                if operation == "select":
                    item.setCheckState(QtCore.Qt.Checked)
                elif operation == "clear":
                    item.setCheckState(QtCore.Qt.Unchecked)
                elif operation == "invert":
                    state = (
                        QtCore.Qt.Unchecked
                        if item.checkState() == QtCore.Qt.Checked
                        else QtCore.Qt.Checked
                    )
                    item.setCheckState(state)
        finally:
            self.shot_table.blockSignals(False)
        self._update_build_buttons()

    def _checked_identities(self) -> list[tuple[str, str, str]]:
        identities = []
        for row in range(self.shot_table.rowCount()):
            item = self.shot_table.item(row, 0)
            if item and item.checkState() == QtCore.Qt.Checked:
                identity = item.data(QtCore.Qt.UserRole)
                if identity:
                    identities.append(tuple(identity))
        return identities

    def _enqueue_builds(self, identities: list[tuple[str, str, str]]) -> None:
        from smartlib.apps.shot_manager import SequenceIdentity, ShotIdentity

        scope = self.scope_combo.currentText().lower()
        sequence_shots: dict[tuple[str, str], list[str]] = {}
        if scope == "sequence":
            for episode, sequence, shot in identities:
                if shot:
                    sequence_shots.setdefault((episode, sequence), []).append(shot)
            identities = list(
                dict.fromkeys((episode, sequence, "") for episode, sequence, _ in identities)
            )
        existing = {
            (job.get("scope"), *tuple(job["identity"]))
            for job in self.pending_jobs
        }
        if self.active_job:
            existing.add(
                (self.active_job.get("scope"), *tuple(self.active_job["identity"]))
            )
        for raw_identity in identities:
            if (scope, *tuple(raw_identity)) in existing:
                continue
            identity = (
                SequenceIdentity(raw_identity[0], raw_identity[1])
                if scope == "sequence"
                else ShotIdentity(*raw_identity)
            )
            plan = self._build_plan(identity)
            if plan.buildable and plan.resolved_mode in {"STAGE", "UPDATE", "REBUILD"}:
                try:
                    if scope == "sequence":
                        self.service.ensure_sequence_stage_input(
                            identity,
                            policy=self._input_policy(),
                            department=plan.department,
                            overrides=self._stage_input_overrides(identity),
                            comment=self.input_comment_edit.text().strip(),
                        )
                    elif plan.department == "anim":
                        self.service.ensure_stage_input(
                            identity,
                            policy=self._input_policy(),
                            overrides=self._stage_input_overrides(identity),
                            comment=self.input_comment_edit.text().strip(),
                        )
                    plan = self._build_plan(identity)
                except Exception as exc:
                    self.footer_label.setText(
                        f"Stage Input generation failed for {identity.code}: {exc}"
                    )
                    continue
            if not plan.buildable:
                continue
            self.job_counter += 1
            if scope == "sequence":
                if plan.resolved_mode == "REVIEW ONLY":
                    output_version = self.service.next_sequence_output_version(identity)
                    job_root = (
                        self.service.shots.sequence_workspace_root(
                            identity.episode, identity.sequence
                        )
                        / "output"
                        / "review"
                        / "layout"
                        / "_jobs"
                    )
                else:
                    output_version = self.service.next_sequence_construct_version(
                        identity,
                        plan.department,
                        plan.task,
                    )
                    job_root = (
                        self.service.shots.sequence_workspace_root(
                            identity.episode, identity.sequence
                        )
                        / "output"
                        / "scene_build"
                        / "_jobs"
                    )
                label = identity.sequence
            else:
                if plan.resolved_mode == "REVIEW ONLY":
                    output_version = self.service.next_output_version(identity)
                    job_root = (
                        self.service.shots.shot_root(identity)
                        / "output"
                        / "review"
                        / "animation"
                        / "_jobs"
                    )
                else:
                    output_version = self.service.next_construct_version(
                        identity,
                        plan.department,
                        plan.task,
                    )
                    job_root = (
                        self.service.shots.shot_root(identity)
                        / "output"
                        / "scene_build"
                        / "_jobs"
                    )
                label = identity.shot
            status_file = job_root / (
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
                f"{label}_{output_version}_{self.job_counter:03d}.json"
            )
            construct_snapshot = {}
            if scope != "sequence":
                overrides = self._stage_input_overrides(identity)
                construct_data = self.service.shots.resolved_construct(
                    identity,
                    cast_contexts=overrides.get("cast_contexts") or {},
                    exclude_cast=overrides.get("exclude_cast") or [],
                )
                self.service.shots.write_construct(identity, construct_data)
                construct_snapshot = self.service.shots.construct_snapshot(
                    identity, construct_data
                )
            job = {
                "id": f"#{self.job_counter:04d}",
                "identity": raw_identity,
                "scope": scope,
                "shots": sequence_shots.get((raw_identity[0], raw_identity[1]), []),
                "sequence_options": (
                    self._sequence_options(identity) if scope == "sequence" else {}
                ),
                "construct": construct_snapshot,
                "version": output_version,
                "mode": plan.resolved_mode,
                "department": plan.department,
                "task_name": plan.task,
                "status_file": str(status_file),
                "state": "QUEUED",
                "progress": 0,
                "task": "Queued",
                "elapsed": QtCore.QElapsedTimer(),
                "row": self.queue_table.rowCount(),
                "stderr": "",
            }
            self.pending_jobs.append(job)
            self._append_queue_row(job)
        if self.pending_jobs and not self.active_job:
            self._start_next_job()
        self._update_build_buttons()

    def _append_queue_row(self, job: dict) -> None:
        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)
        self.queue_table.setRowHeight(row, 30)
        identity = job["identity"]
        shot_label = (
            identity[1]
            if job.get("scope") == "sequence"
            else f"{identity[1]}/{identity[2]}"
        )
        for column, value in enumerate(
            [
                job["id"],
                shot_label,
                job["task"],
                job["state"],
                "0%",
                "00:00",
            ]
        ):
            self.queue_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))

    def _start_next_job(self) -> None:
        if self.active_job or not self.pending_jobs:
            return
        job = self.pending_jobs.pop(0)
        self.active_job = job
        job["state"] = "STARTING"
        job["task"] = "Start mayapy"
        job["elapsed"].start()
        self._update_queue_row(job)
        try:
            mayapy = self.service.resolve_mayapy()
        except Exception as exc:
            job["state"] = "FAILED"
            job["task"] = "Resolve mayapy"
            job["stderr"] = str(exc)
            self._finish_active_job(False)
            return
        process = QtCore.QProcess(self)
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        package_root = str(Path(__file__).resolve().parents[3])
        current_pythonpath = environment.value("PYTHONPATH")
        pythonpath = package_root + (os.pathsep + current_pythonpath if current_pythonpath else "")
        environment.insert("PYTHONPATH", pythonpath)
        environment.insert("PROJECT_CONFIG_DIR", str(self.service.project_config.config_dir))
        process.setProcessEnvironment(environment)
        process.setProgram(str(mayapy))
        identity = job["identity"]
        process.setArguments(
            [
                "-m",
                "smartlib.apps.review_build_manager.worker",
                "--config-dir",
                str(self.service.project_config.config_dir),
                "--episode",
                identity[0],
                "--sequence",
                identity[1],
                "--shot",
                identity[2],
                "--output-version",
                job["version"],
                "--status-file",
                job["status_file"],
                "--scope",
                job["scope"],
                "--shots-json",
                json.dumps(job.get("shots") or []),
                "--sequence-options-json",
                json.dumps(job.get("sequence_options") or {}),
                "--construct-json",
                json.dumps(job.get("construct") or {}),
                "--operation",
                job["mode"],
                "--department",
                job["department"],
                "--task-name",
                job["task_name"],
                "--overrides-json",
                json.dumps(self._stage_input_overrides_for_raw(job["identity"])),
            ]
        )
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[4]))
        process.readyReadStandardError.connect(self._read_worker_stderr)
        process.finished.connect(self._worker_finished)
        self.worker_process = process
        process.start()
        self.job_timer.start()
        self.footer_label.setText(f"Building {identity[0]}/{identity[1]}/{identity[2]}...")

    def _poll_active_job(self) -> None:
        job = self.active_job
        if not job:
            self.job_timer.stop()
            return
        status_path = Path(job["status_file"])
        if status_path.is_file():
            try:
                data = json.loads(status_path.read_text(encoding="utf-8-sig"))
                job["state"] = str(data.get("state") or job["state"])
                job["progress"] = int(data.get("progress") or 0)
                job["task"] = str(data.get("task") or job["task"])
                job["message"] = str(data.get("message") or "")
            except (OSError, ValueError, TypeError):
                pass
        self._update_queue_row(job)

    def _read_worker_stderr(self) -> None:
        if not self.worker_process or not self.active_job:
            return
        text = bytes(self.worker_process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        )
        self.active_job["stderr"] += text

    def _worker_finished(self, exit_code: int, _exit_status) -> None:
        self._poll_active_job()
        self._finish_active_job(exit_code == 0)

    def _finish_active_job(self, success: bool) -> None:
        job = self.active_job
        if not job:
            return
        if success:
            job["state"] = "COMPLETE"
            job["progress"] = 100
            job["task"] = "Complete"
        else:
            job["state"] = "FAILED"
            job["progress"] = 100
            if job.get("stderr") and not job.get("message"):
                job["message"] = job["stderr"].strip().splitlines()[-1]
            job["task"] = self._failure_summary(job.get("message") or job.get("stderr"))
        self._update_queue_row(job)
        self.active_job = None
        self.worker_process = None
        self.job_timer.stop()
        if self.pending_jobs:
            self._start_next_job()
        else:
            self.scan_updates()

    def _update_queue_row(self, job: dict) -> None:
        row = int(job["row"])
        if row >= self.queue_table.rowCount():
            return
        elapsed_ms = job["elapsed"].elapsed() if job["elapsed"].isValid() else 0
        elapsed = f"{elapsed_ms // 60000:02d}:{(elapsed_ms // 1000) % 60:02d}"
        values = [
            job["id"],
            (
                job["identity"][1]
                if job.get("scope") == "sequence"
                else f"{job['identity'][1]}/{job['identity'][2]}"
            ),
            job.get("task") or "",
            job.get("state") or "",
            f"{int(job.get('progress') or 0)}%",
            elapsed,
        ]
        for column, value in enumerate(values):
            item = self.queue_table.item(row, column)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.queue_table.setItem(row, column, item)
            item.setText(str(value))
            if column == 3:
                item.setForeground(
                    QtGui.QColor(STATE_COLORS.get(str(value), "#dddddd"))
                )
        message = str(job.get("message") or "")
        if message:
            for column in range(self.queue_table.columnCount()):
                item = self.queue_table.item(row, column)
                if item is not None:
                    item.setToolTip(message)

    @staticmethod
    def _failure_summary(message: str | None) -> str:
        lines = [
            line.strip()
            for line in str(message or "").splitlines()
            if line.strip()
        ]
        if not lines:
            return "Failed"
        detail = next(
            (
                line
                for line in reversed(lines)
                if line.startswith(
                    (
                        "RuntimeError:",
                        "FileNotFoundError:",
                        "ValueError:",
                        "PermissionError:",
                    )
                )
            ),
            lines[-1],
        )
        detail = detail.split(":", 1)[-1].strip()
        return f"Failed: {detail[:96]}"

    def _update_build_buttons(self) -> None:
        busy_identities = {
            tuple(job["identity"])
            for job in self.pending_jobs
        }
        if self.active_job:
            busy_identities.add(tuple(self.active_job["identity"]))
        checked = self._checked_identities()
        selected = self._selected_status()
        selected_plan = (
            self._build_plan(selected.identity)
            if selected
            else None
        )
        selected_valid = bool(
            selected
            and selected_plan
            and selected_plan.buildable
            and (
                selected.identity.episode,
                selected.identity.sequence,
                selected.identity.shot,
            )
            not in busy_identities
        )
        checked_valid = any(identity not in busy_identities for identity in checked)
        changes_available = any(
            self._build_plan(row.identity).buildable
            and row.state != "UP TO DATE"
            and (row.identity.episode, row.identity.sequence, row.identity.shot)
            not in busy_identities
            for row in self.rows
        )
        self.build_selected_btn.setEnabled(checked_valid or selected_valid)
        self.build_all_changes_btn.setEnabled(changes_available)

    def _populate_tasks(self) -> None:
        current = self.task_combo.currentText() if hasattr(self, "task_combo") else ""
        department = self.department_combo.currentText() if hasattr(self, "department_combo") else "anim"
        tasks = self.service.shots.shot_tasks(department)
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        self.task_combo.addItems(tasks or ["main"])
        index = self.task_combo.findText(current)
        self.task_combo.setCurrentIndex(max(0, index))
        self.task_combo.blockSignals(False)

    def _input_policy(self) -> str:
        return self.input_policy_combo.currentText().strip().upper()

    def _stage_input_overrides(self, identity=None) -> dict:
        overrides = {
            "context": self.input_context_combo.currentText().strip(),
            "camera": self.input_camera_edit.text().strip(),
            "layout_overlay": self.input_overlay_check.isChecked(),
            "use_placements": self.input_placements_check.isChecked(),
            "exclude_cast": [
                value.strip()
                for value in self.input_exclude_cast_edit.text().split(",")
                if value.strip()
            ],
        }
        if identity is not None and hasattr(identity, "shot"):
            key = (identity.episode, identity.sequence, identity.shot)
            settings = self.build_content_settings.get(key) or {}
            contexts = dict(settings.get("contexts") or {})
            excluded = set(settings.get("excluded") or set())
            construct = self.service.shots.load_construct(identity)
            for component in construct.get("components") or []:
                if str(component.get("component_type") or "").lower() != "rig":
                    continue
                name = str(component.get("name") or "")
                source = component.get("source") or {}
                saved_context = str(source.get("context") or "")
                if name and saved_context and name not in contexts:
                    contexts[name] = saved_context
                if name and not bool(component.get("enabled", True)):
                    excluded.add(name)
            if contexts:
                overrides["cast_contexts"] = contexts
            if excluded:
                overrides["exclude_cast"] = sorted(
                    set(overrides["exclude_cast"]) | set(excluded)
                )
        return overrides

    def _stage_input_overrides_for_raw(self, raw_identity) -> dict:
        if not raw_identity or len(raw_identity) < 3 or not raw_identity[2]:
            return self._stage_input_overrides()
        from smartlib.apps.shot_manager import ShotIdentity

        return self._stage_input_overrides(ShotIdentity(*raw_identity))

    def _build_plan(self, identity):
        if self.scope_combo.currentText() == "Sequence":
            from smartlib.apps.shot_manager import SequenceIdentity

            sequence_identity = (
                identity
                if isinstance(identity, SequenceIdentity)
                else SequenceIdentity(identity.episode, identity.sequence)
            )
            options = self._sequence_options(sequence_identity)
            return self.service.sequence_build_plan(
                sequence_identity,
                requested_mode=self.mode_combo.currentText(),
                department=self.department_combo.currentText(),
                task=self.task_combo.currentText(),
                input_policy=self._input_policy(),
                overrides=self._stage_input_overrides(sequence_identity),
                **options,
            )
        return self.service.build_plan(
            identity,
            mode=self.mode_combo.currentText(),
            department=self.department_combo.currentText(),
            task=self.task_combo.currentText(),
            input_policy=self._input_policy(),
            overrides=self._stage_input_overrides(identity),
        )

    def _sequence_settings(self, identity) -> dict:
        key = (identity.episode, identity.sequence)
        return self.sequence_input_settings.setdefault(
            key,
            {
                "recipe": self.service.default_sequence_recipe(),
                "virtual_camera_take": "",
                "enabled_inputs": {},
            },
        )

    def _sequence_options(self, identity) -> dict:
        settings = self._sequence_settings(identity)
        return {
            "recipe": str(settings.get("recipe") or ""),
            "virtual_camera_take": str(
                settings.get("virtual_camera_take") or ""
            ),
            "enabled_inputs": dict(settings.get("enabled_inputs") or {}),
        }

    def _populate_sequence_inputs(self, identity) -> None:
        settings = self._sequence_settings(identity)
        recipe = str(settings.get("recipe") or self.service.default_sequence_recipe())
        self.sequence_recipe_combo.blockSignals(True)
        self.sequence_recipe_combo.setCurrentText(recipe)
        self.sequence_recipe_combo.blockSignals(False)
        plan = self.service.sequence_recipe_plan(identity, **self._sequence_options(identity))
        self.sequence_inputs_tree.blockSignals(True)
        self.sequence_inputs_tree.clear()
        try:
            for data in plan.inputs:
                item = QtWidgets.QTreeWidgetItem(
                    [
                        "", data.label, "Required" if data.required else "Optional",
                        data.state, data.version or "-", data.path or "-", data.adapter or "-",
                    ]
                )
                item.setData(0, QtCore.Qt.UserRole, data.key)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(0, QtCore.Qt.Checked if data.enabled else QtCore.Qt.Unchecked)
                self.sequence_inputs_tree.addTopLevelItem(item)
                for child in data.children:
                    child_item = QtWidgets.QTreeWidgetItem(
                        ["", child.label, "Optional", child.state, child.version or "-", child.path or "-", child.adapter or "-"]
                    )
                    child_item.setData(0, QtCore.Qt.UserRole, child.key)
                    child_item.setData(0, QtCore.Qt.UserRole + 1, data.key)
                    if child.key == plan.virtual_camera_take:
                        child_item.setSelected(True)
                    item.addChild(child_item)
                item.setExpanded(True)
        finally:
            self.sequence_inputs_tree.blockSignals(False)
        errors = [row.detail for row in plan.validation if row.state == "ERROR"]
        warnings = [row.detail for row in plan.validation if row.state == "WARNING"]
        if errors:
            self.sequence_validation_label.setText(f"BLOCKED: {len(errors)} errors")
            self.sequence_validation_label.setToolTip("\n".join(errors))
        elif warnings:
            self.sequence_validation_label.setText(f"WARNING: {len(warnings)}")
            self.sequence_validation_label.setToolTip("\n".join(warnings))
        else:
            self.sequence_validation_label.setText("READY")
            self.sequence_validation_label.setToolTip("")

    def _current_sequence_identity(self):
        selected = self._selected_status()
        if not selected:
            return None
        from smartlib.apps.shot_manager import SequenceIdentity
        return SequenceIdentity(selected.identity.episode, selected.identity.sequence)

    def _sequence_recipe_changed(self, recipe: str) -> None:
        identity = self._current_sequence_identity()
        if not identity:
            return
        self._sequence_settings(identity)["recipe"] = recipe
        self._populate_sequence_inputs(identity)
        self._refresh_plan_columns()

    def _sequence_input_changed(self, item, column: int) -> None:
        if column != 0 or item.parent():
            return
        identity = self._current_sequence_identity()
        if not identity:
            return
        key = str(item.data(0, QtCore.Qt.UserRole) or "")
        self._sequence_settings(identity)["enabled_inputs"][key] = (
            item.checkState(0) == QtCore.Qt.Checked
        )
        self._populate_sequence_inputs(identity)
        self._refresh_plan_columns()

    def _sequence_take_selected(self) -> None:
        selected = self.sequence_inputs_tree.selectedItems()
        if not selected or not selected[0].parent():
            return
        item = selected[0]
        if item.data(0, QtCore.Qt.UserRole + 1) != "virtual_camera":
            return
        identity = self._current_sequence_identity()
        if not identity:
            return
        self._sequence_settings(identity)["virtual_camera_take"] = str(
            item.data(0, QtCore.Qt.UserRole) or ""
        )
        self._populate_sequence_inputs(identity)
        self._refresh_plan_columns()

    def _selected_or_checked_identities(self):
        from smartlib.apps.shot_manager import SequenceIdentity, ShotIdentity

        raw_identities = self._checked_identities()
        if not raw_identities:
            selected = self._selected_status()
            if selected:
                raw_identities = [
                    (
                        selected.identity.episode,
                        selected.identity.sequence,
                        selected.identity.shot,
                    )
                ]
        if self.scope_combo.currentText() == "Sequence":
            unique = []
            seen = set()
            for episode, sequence, _shot in raw_identities:
                key = (episode, sequence)
                if key not in seen:
                    seen.add(key)
                    unique.append(SequenceIdentity(*key))
            return unique
        return [ShotIdentity(*raw) for raw in raw_identities]

    def generate_stage_inputs(self) -> None:
        identities = self._selected_or_checked_identities()
        if not identities:
            QtWidgets.QMessageBox.information(
                self,
                "Generate Stage Inputs",
                "Select or check one or more shots.",
            )
            return
        policy = self._input_policy()
        comment = self.input_comment_edit.text().strip()
        generated = []
        failed = []
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for identity in identities:
                try:
                    overrides = self._stage_input_overrides(identity)
                    if self.scope_combo.currentText() == "Sequence":
                        path = self.service.ensure_sequence_stage_input(
                            identity,
                            policy=policy,
                            department=self.department_combo.currentText(),
                            overrides=overrides,
                            comment=comment,
                        )
                        generated.append(f"{identity.sequence}: {path}")
                    else:
                        path = self.service.ensure_stage_input(
                            identity,
                            policy=policy,
                            overrides=overrides,
                            comment=comment,
                        )
                        generated.append(f"{identity.shot}: {path}")
                except Exception as exc:
                    failed.append(f"{identity.code}: {exc}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.scan_updates()
        if failed:
            QtWidgets.QMessageBox.warning(
                self,
                "Generate Stage Inputs",
                f"Generated: {len(generated)}\nFailed: {len(failed)}\n\n"
                + "\n".join(failed),
            )
        else:
            QtWidgets.QMessageBox.information(
                self,
                "Generate Stage Inputs",
                f"Resolved Stage Input Packages for {len(generated)} targets.",
            )

    def _update_stage_inputs_visibility(self, *_args) -> None:
        visible = self.mode_combo.currentText() != "REVIEW ONLY"
        self.stage_inputs_panel.setVisible(visible)
        self.sequence_inputs_panel.setVisible(
            visible and self.scope_combo.currentText() == "Sequence"
        )
        if hasattr(self, "build_contents_group"):
            self.build_contents_group.setVisible(
                visible and self.scope_combo.currentText() != "Sequence"
            )
        if hasattr(self, "construct_title"):
            self.construct_title.setVisible(visible)
        if hasattr(self, "construct_list"):
            self.construct_list.setVisible(visible)

    def _scope_changed(self, scope: str) -> None:
        selected = self._selected_status()
        if scope == "Sequence":
            self.mode_combo.setCurrentText("STAGE")
            layout_index = self.department_combo.findText("layout")
            if layout_index >= 0:
                self.department_combo.setCurrentIndex(layout_index)
        self._refresh_plan_columns()
        self._show_details(selected)
        self._update_stage_inputs_visibility()

    def _department_changed(self, _department: str) -> None:
        self._populate_tasks()
        self._refresh_plan_columns()

    def _refresh_plan_columns(self, *_args) -> None:
        self._apply_filters()

    def dry_run(self) -> None:
        identities = self._selected_or_checked_identities()
        if not identities:
            QtWidgets.QMessageBox.information(self, "Dry Run", "Select or check one or more shots.")
            return
        lines = []
        blocked = 0
        for identity in identities:
            plan = self._build_plan(identity)
            label = (
                f"{identity.episode}/{identity.sequence}"
                if self.scope_combo.currentText() == "Sequence"
                else f"{identity.episode}/{identity.sequence}/{identity.shot}"
            )
            lines.append(
                f"{label}  {plan.resolved_mode}  {plan.state}"
            )
            lines.append(f"  {plan.summary}")
            for validation in plan.validations:
                lines.append(
                    f"  [{validation.severity}] {validation.code}: {validation.message}"
                )
            blocked += int(not plan.buildable)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Scene Build Dry Run")
        dialog.resize(820, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        summary = QtWidgets.QLabel(
            f"{len(identities)} targets  |  {blocked} blocked  |  no scene files will be modified"
        )
        view = QtWidgets.QPlainTextEdit("\n".join(lines))
        view.setReadOnly(True)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(summary)
        layout.addWidget(view, 1)
        layout.addWidget(close_btn)
        dialog.exec()

    @staticmethod
    def _section_label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _settings(self):
        return QtCore.QSettings(self.SETTINGS_ORGANIZATION, self.SETTINGS_APPLICATION)

    def _restore_settings(self) -> None:
        settings = self._settings()
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        splitter = settings.value("splitter")
        if splitter:
            self.main_splitter.restoreState(splitter)
        self.mode_combo.setCurrentText(str(settings.value("mode", "AUTO")))
        self.scope_combo.setCurrentText(str(settings.value("scope", "Shot")))
        self.department_combo.setCurrentText(
            str(settings.value("department", "anim"))
        )
        self._populate_tasks()
        self.task_combo.setCurrentText(str(settings.value("task", "main")))
        self.input_policy_combo.setCurrentText(
            str(settings.value("input_policy", "Generate Missing"))
        )
        self.input_context_combo.setCurrentText(
            str(settings.value("input_context", "WORK"))
        )
        self.input_camera_edit.setText(str(settings.value("input_camera", "")))
        self.input_overlay_check.setChecked(
            str(settings.value("input_overlay", "true")).lower()
            not in {"false", "0"}
        )
        self.input_placements_check.setChecked(
            str(settings.value("input_placements", "true")).lower()
            not in {"false", "0"}
        )
        self.input_exclude_cast_edit.setText(
            str(settings.value("exclude_cast", ""))
        )
        self._update_stage_inputs_visibility()

    def closeEvent(self, event) -> None:
        settings = self._settings()
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("splitter", self.main_splitter.saveState())
        settings.setValue("mode", self.mode_combo.currentText())
        settings.setValue("scope", self.scope_combo.currentText())
        settings.setValue("department", self.department_combo.currentText())
        settings.setValue("task", self.task_combo.currentText())
        settings.setValue("input_policy", self.input_policy_combo.currentText())
        settings.setValue("input_context", self.input_context_combo.currentText())
        settings.setValue("input_camera", self.input_camera_edit.text())
        settings.setValue("input_overlay", self.input_overlay_check.isChecked())
        settings.setValue(
            "input_placements", self.input_placements_check.isChecked()
        )
        settings.setValue("exclude_cast", self.input_exclude_cast_edit.text())
        super().closeEvent(event)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #252728; color: #e5e5e5; }
            QLineEdit, QComboBox, QListWidget, QTreeWidget, QTableWidget {
                background: #202223; border: 1px solid #3a3d3f; selection-background-color: #315f82;
            }
            QLineEdit, QComboBox { min-height: 26px; padding: 1px 6px; }
            QPushButton {
                min-height: 27px; padding: 2px 12px; border: 1px solid #4a4d4f;
                background: #3a3d3f; border-radius: 4px;
            }
            QPushButton:hover { background: #484c4f; }
            QPushButton[primary="true"] { background: #296eaa; border-color: #3986c5; }
            QPushButton:disabled { color: #777; background: #303233; }
            QHeaderView::section {
                background: #333638; color: #dedede; padding: 6px; border: 0;
                border-right: 1px solid #45484a;
            }
            QTableWidget::item { padding: 4px; }
            QLabel#sectionLabel { font-size: 14px; font-weight: bold; padding: 5px 3px; }
            QLabel#detailSummary { background: #2d3032; padding: 8px; }
            QLabel#footerLabel { color: #aeb4b8; padding: 2px; }
            """
        )


_WINDOW = None


def show(
    config_dir: str | os.PathLike[str],
    parent=None,
    *,
    initial_scope: str = "",
) -> ReviewBuildManagerWindow:
    global _WINDOW
    if _WINDOW is not None:
        try:
            _WINDOW.close()
            _WINDOW.deleteLater()
        except Exception:
            pass
    _WINDOW = ReviewBuildManagerWindow(parent=parent, config_dir=config_dir)
    if initial_scope:
        _WINDOW.scope_combo.setCurrentText(initial_scope)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW
