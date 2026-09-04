from __future__ import annotations

from datetime import datetime
import json
import os
import subprocess
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
from smartlib.apps.review_build_manager.job_request import write_job_request
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.icons import build_content_icon_path, sequence_input_icon_path, tool_ico_path
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


def build_content_state_color(state: str) -> str:
    return {
        "READY": "#80bd72",
        "UPDATE AVAILABLE": "#f2ae30",
        "EXCLUDED": "#999999",
    }.get(str(state or "").upper(), "#ef665d")


class ReviewBuildManagerWindow(QtWidgets.QMainWindow):
    SETTINGS_ORGANIZATION = "SmartPipeline"
    SETTINGS_APPLICATION = "ReviewBuildManager"

    def __init__(self, parent=None, *, config_dir: str | os.PathLike[str]):
        super().__init__(parent)
        icon_path = tool_ico_path("build_manager")
        if icon_path:
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))
        self.service = ReviewBuildManagerService(ProjectConfig(config_dir))
        self.rows: list[ReviewShotStatus] = []
        self.current_filter = "ALL"
        self.pending_jobs: list[dict] = []
        self.queue_jobs: list[dict] = []
        self.active_job: dict | None = None
        self.worker_process: QtCore.QProcess | None = None
        self.job_counter = 0
        self.build_content_settings: dict[tuple[str, str, str], dict] = {}
        self.sequence_input_settings: dict[tuple[str, str], dict] = {}
        self._review_submission_profiles: dict[tuple[str, str, str], dict] = {}
        self._planned_snapshots: dict[str, dict] = {}
        self.current_build_content_rows: list[dict] = []
        self._build_plan_cache: dict[tuple[str, str, str], object] = {}
        self._open_after_build_identity: tuple[str, str, str] | None = None
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
        self.mode_combo.setToolTip(
            "WORK STAGE builds an editable scene; REND STAGE builds render outputs; "
            "UPDATE applies Construct differences."
        )
        # Kept as an internal compatibility flag for the current worker CLI.
        # Review submission is exposed through the dedicated Review tab.
        self.generate_review_check = QtWidgets.QCheckBox()
        self.generate_review_check.setChecked(False)
        self.generate_review_check.setVisible(False)
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

        self.main_tabs = QtWidgets.QTabWidget()
        root.addWidget(self.main_tabs, 1)
        build_page = QtWidgets.QWidget()
        build_page_layout = QtWidgets.QVBoxLayout(build_page)
        build_page_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        build_page_layout.addWidget(splitter)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.setSizes([220, 1210])
        splitter.setChildrenCollapsible(False)
        self.main_splitter = splitter
        self.main_tabs.addTab(build_page, "Build")
        self.job_queue_page = self._build_job_queue_page()
        self.main_tabs.addTab(self.job_queue_page, "Job Queue")

        self.footer_label = QtWidgets.QLabel("Ready")
        self.footer_label.setObjectName("footerLabel")
        root.addWidget(self.footer_label)
        self._apply_style()

    def _build_stage_inputs_panel(self) -> QtWidgets.QWidget:
        self.stage_inputs_panel = QtWidgets.QGroupBox("Stage Inputs")
        layout = QtWidgets.QHBoxLayout(self.stage_inputs_panel)
        layout.setContentsMargins(8, 7, 8, 7)
        self.stage_inputs_summary = QtWidgets.QLabel()
        self.stage_inputs_summary.setWordWrap(True)
        self.stage_inputs_settings_btn = QtWidgets.QPushButton("Settings...")
        self.stage_inputs_settings_btn.setToolTip(
            "Open Stage Input settings only when overrides or regeneration are needed."
        )
        layout.addWidget(self.stage_inputs_summary, 1)
        layout.addWidget(self.stage_inputs_settings_btn)
        self._build_stage_inputs_dialog()
        self._update_stage_inputs_summary()
        return self.stage_inputs_panel

    def _build_stage_inputs_dialog(self) -> None:
        self.stage_inputs_dialog = QtWidgets.QDialog(self)
        self.stage_inputs_dialog.setWindowTitle("Stage Input Settings")
        self.stage_inputs_dialog.setModal(False)
        self.stage_inputs_dialog.resize(540, 330)
        root = QtWidgets.QVBoxLayout(self.stage_inputs_dialog)
        layout = QtWidgets.QGridLayout()
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(7)
        self.input_policy_combo = QtWidgets.QComboBox()
        self.input_policy_combo.addItems(
            ["Generate Missing", "Regenerate Selected", "Use Existing"]
        )
        self.input_context_combo = QtWidgets.QComboBox()
        self.input_context_combo.addItems(self.service.stage_profiles())
        self.input_representation_combo = QtWidgets.QComboBox()
        self.input_representation_combo.addItem("Project Default", "project")
        self.input_representation_combo.addItem("Maya", "maya")
        self.input_representation_combo.addItem("USD", "usd")
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
        self.generate_inputs_btn = QtWidgets.QPushButton("Regenerate Input Snapshot")
        layout.addWidget(QtWidgets.QLabel("Policy"), 0, 0)
        layout.addWidget(self.input_policy_combo, 0, 1)
        layout.addWidget(QtWidgets.QLabel("Stage Profile"), 1, 0)
        layout.addWidget(self.input_context_combo, 1, 1)
        layout.addWidget(QtWidgets.QLabel("Representation"), 2, 0)
        layout.addWidget(self.input_representation_combo, 2, 1)
        layout.addWidget(QtWidgets.QLabel("Camera"), 3, 0)
        layout.addWidget(self.input_camera_edit, 3, 1)
        option_row = QtWidgets.QHBoxLayout()
        option_row.addWidget(self.input_placements_check)
        option_row.addWidget(self.input_overlay_check)
        layout.addLayout(option_row, 4, 0, 1, 2)
        layout.addWidget(QtWidgets.QLabel("Exclude Cast"), 5, 0)
        layout.addWidget(self.input_exclude_cast_edit, 5, 1)
        layout.addWidget(QtWidgets.QLabel("Comment"), 6, 0)
        layout.addWidget(self.input_comment_edit, 6, 1)
        layout.setColumnStretch(1, 1)
        root.addLayout(layout)
        help_label = QtWidgets.QLabel(
            "These settings are optional overrides. Normal builds reuse or generate "
            "the required input snapshot automatically."
        )
        help_label.setWordWrap(True)
        root.addWidget(help_label)
        actions = QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        close_btn = QtWidgets.QPushButton("Close")
        actions.addWidget(close_btn)
        actions.addWidget(self.generate_inputs_btn)
        root.addLayout(actions)
        close_btn.clicked.connect(self.stage_inputs_dialog.close)

    def open_stage_inputs_dialog(self) -> None:
        self.stage_inputs_dialog.show()
        self.stage_inputs_dialog.raise_()
        self.stage_inputs_dialog.activateWindow()

    def _update_stage_inputs_summary(self, *_args) -> None:
        if not hasattr(self, "stage_inputs_summary"):
            return
        representation = self.input_representation_combo.currentText() or "Project Default"
        options = []
        if self.input_placements_check.isChecked():
            options.append("Placements")
        if self.input_overlay_check.isChecked():
            options.append("Layout Overlay")
        option_text = ", ".join(options) if options else "No optional inputs"
        self.stage_inputs_summary.setText(
            f"{self.input_policy_combo.currentText()} · "
            f"{self.input_context_combo.currentText()} · {representation}\n{option_text}"
        )

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
        self.sequence_inputs_tree.setIconSize(QtCore.QSize(24, 24))
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
        outer_layout = QtWidgets.QVBoxLayout(panel)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        self.center_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.center_splitter.setChildrenCollapsible(False)
        outer_layout.addWidget(self.center_splitter)
        top_panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(top_panel)
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
        self.center_splitter.addWidget(top_panel)

        lower_panel = QtWidgets.QWidget()
        lower_layout = QtWidgets.QVBoxLayout(lower_panel)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(0)

        self.build_contents_group = QtWidgets.QGroupBox("Build Contents")
        contents_layout = QtWidgets.QVBoxLayout(self.build_contents_group)
        contents_layout.setContentsMargins(6, 6, 6, 6)
        contents_layout.setSpacing(5)
        contents_tools = QtWidgets.QHBoxLayout()
        self.contents_select_all_btn = QtWidgets.QPushButton("Select All")
        self.contents_clear_btn = QtWidgets.QPushButton("Clear")
        self.contents_invert_btn = QtWidgets.QPushButton("Invert")
        self.contents_summary_label = QtWidgets.QLabel("Select a shot")
        contents_tools.addWidget(self.contents_select_all_btn)
        contents_tools.addWidget(self.contents_clear_btn)
        contents_tools.addWidget(self.contents_invert_btn)
        contents_tools.addStretch(1)
        contents_tools.addWidget(self.contents_summary_label)
        contents_layout.addLayout(contents_tools)
        self.build_contents_table = QtWidgets.QTableWidget(0, 10)
        self.build_contents_table.setHorizontalHeaderLabels(
            ["Use", "Type", "Name", "Category", "Variant", "Context", "Build Version", "Last Review Version", "State", "Note"]
        )
        self.build_contents_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.build_contents_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.build_contents_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.build_contents_table.setShowGrid(False)
        self.build_contents_table.setIconSize(QtCore.QSize(24, 24))
        self.build_contents_table.verticalHeader().setVisible(False)
        contents_header = self.build_contents_table.horizontalHeader()
        for column in range(9):
            contents_header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        contents_header.setSectionResizeMode(9, QtWidgets.QHeaderView.Stretch)
        self.build_contents_table.setMinimumHeight(205)
        contents_layout.addWidget(self.build_contents_table)
        self.workflow_tabs = QtWidgets.QTabWidget()
        build_page = QtWidgets.QWidget()
        build_layout = QtWidgets.QVBoxLayout(build_page)
        build_layout.setContentsMargins(0, 0, 0, 0)
        build_layout.addWidget(self.build_contents_group, 1)
        build_layout.addWidget(self._build_sequence_inputs_panel(), 1)
        self.workflow_tabs.addTab(build_page, "Build")
        self.planned_snapshot_page = self._build_planned_snapshot_page()
        self.workflow_tabs.addTab(self.planned_snapshot_page, "Planned Snapshot")
        self.workflow_tabs.addTab(self._build_right_panel(), "Output")
        lower_layout.addWidget(self.workflow_tabs)
        self.center_splitter.addWidget(lower_panel)
        self.center_splitter.setSizes([330, 430])
        return panel

    def _build_job_queue_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QGroupBox("Review Submission")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._section_label("Job Queue"))
        self.queue_table = QtWidgets.QTableWidget(0, 7)
        self.queue_table.setHorizontalHeaderLabels(
            ["Job", "Shot", "Task", "Status", "Progress", "Elapsed", "File Name"]
        )
        self.queue_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.queue_table.setShowGrid(False)
        self.queue_table.verticalHeader().setVisible(False)
        queue_header = self.queue_table.horizontalHeader()
        for column in (0, 1, 3, 4, 5):
            queue_header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        queue_header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        queue_header.setSectionResizeMode(6, QtWidgets.QHeaderView.Stretch)
        self.queue_table.itemSelectionChanged.connect(self._show_selected_job_details)
        self.job_queue_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.job_queue_splitter.setChildrenCollapsible(False)
        self.job_queue_splitter.addWidget(self.queue_table)
        detail_group = QtWidgets.QGroupBox("Job Details / Error Details")
        detail_layout = QtWidgets.QVBoxLayout(detail_group)
        detail_layout.setContentsMargins(7, 7, 7, 7)
        self.job_detail_text = QtWidgets.QPlainTextEdit()
        self.job_detail_text.setReadOnly(True)
        self.job_detail_text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.job_detail_text.setPlaceholderText(
            "Select a Job Queue row to inspect its full result or error."
        )
        detail_layout.addWidget(self.job_detail_text)
        self.job_queue_splitter.addWidget(detail_group)
        self.job_queue_splitter.setSizes([510, 230])
        layout.addWidget(self.job_queue_splitter, 1)
        return page

    def _build_review_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(6, 6, 6, 6)
        settings = QtWidgets.QHBoxLayout()
        self.review_profile_combo = QtWidgets.QComboBox()
        self.review_profile_combo.addItems(self.service.review_profile_ids())
        default_review_profile = self.service.default_review_profile_id()
        default_review_index = self.review_profile_combo.findText(default_review_profile)
        if default_review_index >= 0:
            self.review_profile_combo.setCurrentIndex(default_review_index)
        self.delivery_profile_combo = QtWidgets.QComboBox()
        self.delivery_profile_combo.addItems(self.service.delivery_profile_ids())
        self.precomp_combo = QtWidgets.QComboBox()
        self.precomp_combo.addItem("Latest Approved", "latest_approved")
        self.layer_definition_label = QtWidgets.QLabel("Layer Definition: draft")
        settings.addWidget(QtWidgets.QLabel("Review Profile"))
        settings.addWidget(self.review_profile_combo)
        settings.addWidget(QtWidgets.QLabel("Delivery Profile"))
        settings.addWidget(self.delivery_profile_combo)
        settings.addWidget(QtWidgets.QLabel("PreComp"))
        settings.addWidget(self.precomp_combo)
        settings.addStretch(1)
        settings.addWidget(self.layer_definition_label)
        root.addLayout(settings)

        actions = QtWidgets.QHBoxLayout()
        self.review_changes_btn = QtWidgets.QPushButton("Review Changes")
        self.submit_review_btn = QtWidgets.QPushButton("Submit for Review")
        self.submit_review_btn.setProperty("primary", True)
        self.review_actions_btn = QtWidgets.QToolButton()
        self.review_actions_btn.setText("Review Actions")
        self.review_actions_btn.setVisible(False)
        self.review_actions_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(self.review_actions_btn)
        self.rebuild_selected_layers_action = menu.addAction("Rebuild Selected Layers")
        self.rebuild_all_layers_action = menu.addAction("Rebuild All Layers")
        self.ignore_cache_submit_action = menu.addAction("Ignore Cache and Submit")
        menu.addSeparator()
        menu.addAction("View Source Manifest")
        self.review_actions_btn.setMenu(menu)
        self.rebuild_selected_layers_action.triggered.connect(
            self._set_rebuild_selected_layers
        )
        self.rebuild_all_layers_action.triggered.connect(
            self._set_rebuild_all_layers
        )
        self.ignore_cache_submit_action.triggered.connect(
            self._ignore_cache_and_submit
        )
        self.review_status_label = QtWidgets.QLabel("Select a shot")
        actions.addWidget(self.review_actions_btn)
        actions.addStretch(1)
        actions.addWidget(self.review_status_label)
        actions.addWidget(self.review_changes_btn)
        actions.addWidget(self.submit_review_btn)
        root.addLayout(actions)
        return page

    def _build_planned_snapshot_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(self._section_label("Final Planned Snapshot"))
        self.review_page = self._build_review_tab()
        root.addWidget(self.review_page)

        cache_row = QtWidgets.QHBoxLayout()
        self.planned_cache_policy_combo = QtWidgets.QComboBox()
        self.planned_cache_policy_combo.addItem("Auto (Reuse matching cache)", "use")
        self.planned_cache_policy_combo.addItem("Rebuild Selected Layers", "rebuild_selected")
        self.planned_cache_policy_combo.addItem("Rebuild All Layers", "rebuild_all")
        self.planned_snapshot_state_label = QtWidgets.QLabel("Select a shot")
        cache_row.addWidget(QtWidgets.QLabel("Cache Policy"))
        cache_row.addWidget(self.planned_cache_policy_combo)
        cache_row.addStretch(1)
        cache_row.addWidget(self.planned_snapshot_state_label)
        root.addLayout(cache_row)

        self.planned_snapshot_tabs = QtWidgets.QTabWidget()
        self.planned_snapshot_tabs.setDocumentMode(True)
        inputs_page = QtWidgets.QWidget()
        inputs_layout = QtWidgets.QVBoxLayout(inputs_page)
        inputs_layout.setContentsMargins(4, 4, 4, 4)
        self.planned_inputs_table = QtWidgets.QTableWidget(0, 7)
        self.planned_inputs_table.setHorizontalHeaderLabels(
            ["Use", "Type", "Name", "Category", "Context", "Version", "State"]
        )
        self.planned_inputs_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.planned_inputs_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.planned_inputs_table.setShowGrid(False)
        self.planned_inputs_table.setIconSize(QtCore.QSize(24, 24))
        self.planned_inputs_table.verticalHeader().setVisible(False)
        self.planned_inputs_table.horizontalHeader().setStretchLastSection(True)
        inputs_layout.addWidget(self.planned_inputs_table)
        self.planned_snapshot_tabs.addTab(inputs_page, "Resolved Inputs")

        layers_page = QtWidgets.QWidget()
        layers_layout = QtWidgets.QVBoxLayout(layers_page)
        layers_layout.setContentsMargins(4, 4, 4, 4)
        self.planned_layers_table = QtWidgets.QTableWidget(0, 5)
        self.planned_layers_table.setHorizontalHeaderLabels(
            ["Rebuild", "Layer", "Members", "Camera", "Expected"]
        )
        self.planned_layers_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.planned_layers_table.setShowGrid(False)
        self.planned_layers_table.verticalHeader().setVisible(False)
        self.planned_layers_table.horizontalHeader().setStretchLastSection(True)
        self.planned_layers_table.itemChanged.connect(self._planned_layer_changed)
        layers_layout.addWidget(self.planned_layers_table)
        self.planned_snapshot_tabs.addTab(layers_page, "Review Layers")
        root.addWidget(self.planned_snapshot_tabs, 1)
        return page

    def _build_right_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
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
        self.task_combo.currentTextChanged.connect(self._status_basis_changed)
        self.mode_combo.currentTextChanged.connect(self._refresh_plan_columns)
        self.mode_combo.currentTextChanged.connect(self._status_basis_changed)
        self.mode_combo.currentTextChanged.connect(self._update_stage_inputs_visibility)
        self.scope_combo.currentTextChanged.connect(self._scope_changed)
        self.input_policy_combo.currentTextChanged.connect(self._refresh_plan_columns)
        self.input_policy_combo.currentTextChanged.connect(self._update_stage_inputs_summary)
        self.input_context_combo.currentTextChanged.connect(self._refresh_plan_columns)
        self.input_context_combo.currentTextChanged.connect(self._update_stage_inputs_summary)
        self.input_representation_combo.currentIndexChanged.connect(
            self._refresh_plan_columns
        )
        self.input_representation_combo.currentIndexChanged.connect(
            self._update_stage_inputs_summary
        )
        self.input_representation_combo.currentIndexChanged.connect(
            self._status_basis_changed
        )
        self.generate_review_check.toggled.connect(self._generate_review_toggled)
        self.input_camera_edit.textChanged.connect(self._refresh_plan_columns)
        self.input_placements_check.toggled.connect(self._refresh_plan_columns)
        self.input_placements_check.toggled.connect(self._update_stage_inputs_summary)
        self.input_overlay_check.toggled.connect(self._refresh_plan_columns)
        self.input_overlay_check.toggled.connect(self._update_stage_inputs_summary)
        self.input_exclude_cast_edit.textChanged.connect(self._refresh_plan_columns)
        self.generate_inputs_btn.clicked.connect(self.generate_stage_inputs)
        self.stage_inputs_settings_btn.clicked.connect(self.open_stage_inputs_dialog)
        self.search_edit.textChanged.connect(self._apply_filters)
        self.filter_list.currentItemChanged.connect(self._filter_changed)
        self.shot_tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self.shot_table.itemSelectionChanged.connect(self._table_selection_changed)
        self.shot_table.itemChanged.connect(self._shot_item_changed)
        self.open_output_btn.clicked.connect(self._open_output_folder)
        self.contents_select_all_btn.clicked.connect(lambda: self._set_content_checks("select"))
        self.contents_clear_btn.clicked.connect(lambda: self._set_content_checks("clear"))
        self.contents_invert_btn.clicked.connect(lambda: self._set_content_checks("invert"))
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
        self.review_changes_btn.clicked.connect(self._review_submission_changes)
        self.submit_review_btn.clicked.connect(self._submit_for_review)
        self.planned_cache_policy_combo.currentIndexChanged.connect(
            self._planned_controls_changed
        )
        self.review_profile_combo.currentTextChanged.connect(
            self._planned_controls_changed
        )
        self.delivery_profile_combo.currentTextChanged.connect(
            self._planned_controls_changed
        )
        self.precomp_combo.currentIndexChanged.connect(
            self._planned_controls_changed
        )
        self.planned_inputs_table.itemChanged.connect(self._planned_input_changed)
        self._update_stage_inputs_visibility()

    def scan_updates(self) -> None:
        selected_status = self._selected_status() if self.rows else None
        selected_identity = selected_status.identity if selected_status else None
        selected_scope = self._tree_scope() if hasattr(self, "shot_tree") else None
        self.scan_btn.setEnabled(False)
        self.footer_label.setText("Scanning shots...")
        QtWidgets.QApplication.processEvents()
        try:
            self._build_plan_cache.clear()
            self.rows = [
                self.service.shot_status(
                    identity,
                    mode=self.mode_combo.currentText(),
                    department=self.department_combo.currentText(),
                    task=self.task_combo.currentText(),
                    generate_review=self.generate_review_check.isChecked(),
                    overrides=self._stage_input_overrides(identity),
                )
                for identity in self.service.shots.list_shots()
            ]
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
                f"{dirty} shots require rebuild  |  {missing} inputs missing  |  Worker: not connected"
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
            plan = self._cached_build_plan(identity)
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
            display_state = (
                plan.state if plan.state in {"BLOCKED", "WARNING"} else row_data.state
            )
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
            self._populate_review_tab(None)
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
            self._populate_review_tab(None)
            return
        self.detail_title.setText(
            f"Output History - {identity.episode}/{identity.sequence}/{identity.shot}"
        )
        self.detail_summary.setText(
            f"State: {row_data.state}\n"
            f"Animation Curves: {row_data.source_version or '-'}\n"
            f"Construct: {row_data.output_label}\n"
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
        self._populate_review_tab(row_data)
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
        stage_context = (
            "REND" if self.mode_combo.currentText() == "REND STAGE"
            else self.input_context_combo.currentText()
        )
        saved_snapshot = self._planned_snapshots.get(
            self._planned_snapshot_key(identity)
        ) or {}
        contexts = self._snapshot_contexts(
            settings["contexts"], saved_snapshot,
            stage_context,
        )
        rows = self.service.build_contents(
            identity,
            default_context=stage_context,
            cast_contexts=contexts,
            excluded_cast=list(settings["excluded"]),
            representation=str(
                self.input_representation_combo.currentData() or "project"
            ),
        )
        saved_snapshot = self._planned_snapshots.get(
            self._planned_snapshot_key(identity)
        ) or {}
        saved_inputs = {
            (str(entry.get("type") or ""), str(entry.get("name") or "")): dict(entry)
            for entry in (saved_snapshot.get("inputs") or [])
        }
        latest_review_snapshot = getattr(self.service, "latest_review_snapshot", None)
        review_department = (
            self.department_combo.currentText()
            if hasattr(self, "department_combo") else "anim"
        )
        review_delivery_profile = (
            self.delivery_profile_combo.currentText()
            if hasattr(self, "delivery_profile_combo") else "internal"
        )
        reviewed_snapshot = (
            latest_review_snapshot(
                identity,
                review_department,
                review_delivery_profile or "internal",
            )
            if callable(latest_review_snapshot) else {}
        )
        reviewed_inputs = {
            (str(entry.get("type") or ""), str(entry.get("name") or "")): dict(entry)
            for entry in (reviewed_snapshot.get("inputs") or [])
        }
        for data in rows:
            key = (
                str(data.get("type") or ""),
                str(data.get("cast_key") or data.get("name") or ""),
            )
            data["build_version"] = str(data.get("latest") or data.get("official") or "")
            data["last_review_version"] = str(
                (reviewed_inputs.get(key) or {}).get("version") or ""
            )
            component = data.get("component")
            latest_camera = next(
                (option for option in (data.get("camera_versions") or []) if option.get("latest")),
                None,
            )
            if isinstance(component, dict) and latest_camera:
                component["path"] = str(latest_camera.get("path") or "")
                component["version"] = str(latest_camera.get("version") or "")
            if key not in saved_inputs:
                continue
            saved_input = saved_inputs[key]
            if "context_override" in saved_input:
                data["context_override"] = bool(saved_input["context_override"])
            enabled = (bool(saved_input.get("enabled", True))
                       if data.get("allow_disable", True) else True)
            data["enabled"] = enabled
            component = data.get("component")
            if isinstance(component, dict):
                component["enabled"] = enabled
                same_context = not saved_input.get("context") or (
                    saved_input["context"] == data.get("context")
                )
                saved_path = str(saved_input.get("path") or "")
                managed_version_type = str(data.get("type") or "")
                unavailable_managed_version = (
                    managed_version_type in {"camera", "review_layers"}
                    and saved_path
                    and not Path(saved_path).is_file()
                )
                if same_context and saved_input.get("version") and not unavailable_managed_version:
                    component["version"] = str(saved_input["version"])
                    data["build_version"] = str(saved_input["version"])
                if same_context and saved_path and not unavailable_managed_version:
                    component["path"] = saved_path
                if unavailable_managed_version:
                    input_label = (
                        "Camera Package"
                        if managed_version_type == "camera"
                        else "Review Layers"
                    )
                    data["note"] = (
                        f"Saved {input_label} {saved_input.get('version') or '-'} is unavailable; "
                        f"using Latest {data.get('build_version') or '-'}"
                    )
            data["state"] = self._local_content_state(data, enabled)
            if enabled:
                settings["excluded"].discard(key[1])
            else:
                settings["excluded"].add(key[1])
        self.current_build_content_rows = rows
        self.build_contents_group.setTitle(f"Build Contents - {identity.shot}")
        for row_index, data in enumerate(rows):
            row = self.build_contents_table.rowCount()
            self.build_contents_table.insertRow(row)
            self.build_contents_table.setRowHeight(row, 28)
            check = QtWidgets.QTableWidgetItem()
            check_flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
            if data.get("allow_disable", True):
                check_flags |= QtCore.Qt.ItemIsUserCheckable
            check.setFlags(check_flags)
            check.setCheckState(QtCore.Qt.Checked if data["enabled"] else QtCore.Qt.Unchecked)
            check.setData(QtCore.Qt.UserRole, row_index)
            check.setData(QtCore.Qt.UserRole + 1, data["cast_key"])
            self.build_contents_table.setItem(row, 0, check)
            type_item = QtWidgets.QTableWidgetItem(str(data["type"]))
            icon_path = build_content_icon_path(data["type"], size=24)
            if icon_path:
                type_item.setIcon(QtGui.QIcon(str(icon_path)))
            self.build_contents_table.setItem(row, 1, type_item)
            values = [
                data["cast_key"],
                data.get("category") or "-",
                data["variant"],
            ]
            for column, value in enumerate(values, start=2):
                self.build_contents_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
            context_options = list(data.get("context_options") or [])
            if context_options:
                context_combo = QtWidgets.QComboBox()
                context_combo.addItems(context_options)
                selected_context = str(data.get("context") or "").strip().upper()
                if selected_context not in context_options:
                    selected_context = context_options[0]
                context_combo.setCurrentText(selected_context)
                context_combo.setProperty("content_row", row_index)
                context_combo.currentTextChanged.connect(self._content_context_changed)
                self.build_contents_table.setCellWidget(row, 5, context_combo)
            else:
                context_item = QtWidgets.QTableWidgetItem("-")
                context_item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.build_contents_table.setItem(row, 5, context_item)
            for column, key in ((6, "build_version"), (7, "last_review_version"), (8, "state"), (9, "note")):
                item = QtWidgets.QTableWidgetItem(str(data[key]))
                if column == 8:
                    item.setForeground(
                        QtGui.QColor(build_content_state_color(data[key]))
                    )
                self.build_contents_table.setItem(row, column, item)
            if data.get('camera_versions'):
                version_combo = QtWidgets.QComboBox()
                for option in data['camera_versions']:
                    version_combo.addItem(option['version'], option['path'])
                    version_combo.setItemData(version_combo.count() - 1,
                        option['summary'] + '\n\n' + option['path'], QtCore.Qt.ToolTipRole)
                selected_path = str(data['component'].get('path') or '')
                index = version_combo.findData(selected_path)
                if index < 0:
                    version_combo.addItem(str(data['build_version']) + ' (unavailable)', selected_path)
                    index = version_combo.count() - 1
                version_combo.setCurrentIndex(index)
                version_combo.setToolTip(str(data['note']) + '\n\n' + selected_path)
                version_combo.setProperty('content_row', row_index)
                version_combo.activated.connect(self._camera_package_version_changed)
                self.build_contents_table.setCellWidget(row, 6, version_combo)
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
        if not data.get("allow_disable", True):
            return
        enabled = item.checkState() == QtCore.Qt.Checked
        data["component"]["enabled"] = enabled
        data["enabled"] = enabled
        excluded = self._content_settings(status)["excluded"]
        name = str(data["cast_key"])
        if data["component"]["enabled"]:
            excluded.discard(name)
        else:
            excluded.add(name)
        self.service.save_build_contents(status.identity, self.current_build_content_rows)
        state = self._local_content_state(data, enabled)
        data["state"] = state
        state_item = self.build_contents_table.item(item.row(), 8)
        if state_item:
            state_item.setText(state)
            state_item.setForeground(
                QtGui.QColor(build_content_state_color(state))
            )
        enabled_count = sum(bool(row.get("enabled")) for row in self.current_build_content_rows)
        self.contents_summary_label.setText(
            f"{enabled_count} of {len(self.current_build_content_rows)} items enabled"
        )

    def _camera_package_version_changed(self, *_args):
        status = self._selected_status()
        combo = self.sender()
        if not status or combo is None:
            return
        try:
            row_index = int(combo.property('content_row'))
            self.service.select_camera_package_version(status.identity, self.current_build_content_rows,
                                                       row_index, str(combo.currentData()))
            settings = self._content_settings(status)
            for row in self.current_build_content_rows:
                if (row.get('component', {}).get('source') or {}).get('camera_package'):
                    if row['enabled']:
                        settings['excluded'].discard(row['cast_key'])
                    else:
                        settings['excluded'].add(row['cast_key'])
            self._planned_controls_changed()
            self._populate_build_contents(status)
            self._populate_planned_snapshot(status)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, 'Camera Package Selection', str(exc))

    def _content_context_changed(self, context: str) -> None:
        status = self._selected_status()
        sender = self.sender()
        row_index = sender.property("content_row") if sender else None
        if status is None or row_index is None:
            return
        self._change_planned_context(int(row_index), context)

    @staticmethod
    def _snapshot_contexts(contexts: dict, snapshot: dict, stage: str) -> dict:
        result = dict(contexts)
        for entry in snapshot.get("inputs") or []:
            if entry.get("type") not in {"rig", "usd"}:
                continue
            if "context_override" not in entry or not entry.get("name"):
                continue
            result[str(entry["name"])] = (
                str(entry.get("context") or stage)
                if entry["context_override"] else stage
            )
        return result

    @staticmethod
    def _set_snapshot_context(snapshot: dict, content_type: str, name: str, context: str) -> dict:
        result = dict(snapshot)
        result["inputs"] = []
        for incoming in snapshot.get("inputs") or []:
            entry = dict(incoming)
            if entry.get("type") == content_type and entry.get("name") == name:
                entry["context"] = context
                entry["context_override"] = bool(context)
                # Version and path locks belong to the previous context.
                entry.pop("version", None)
                entry.pop("path", None)
            result["inputs"].append(entry)
        return result

    def _planned_context_changed(self, *_args) -> None:
        combo = self.sender()
        row_index = combo.property("content_row") if combo else None
        if row_index is not None:
            self._change_planned_context(int(row_index), str(combo.currentData() or ""))

    def _change_planned_context(self, row_index: int, context: str) -> None:
        status = self._selected_status()
        if not status or not 0 <= row_index < len(self.current_build_content_rows):
            return
        row = self.current_build_content_rows[row_index]
        if context and context not in (row.get("context_options") or []):
            return
        key = self._planned_snapshot_key(status.identity)
        payload = self._planned_snapshot_payload(status.identity)
        self._planned_snapshots[key] = self._set_snapshot_context(
            payload, str(row.get("type") or ""), str(row.get("cast_key") or ""), context,
        )
        self._build_plan_cache.clear()
        self._populate_build_contents(status)
        self._populate_planned_snapshot(status)

    def _set_content_checks(self, operation: str) -> None:
        rows = self.build_contents_table.selectionModel().selectedRows()
        target_rows = [index.row() for index in rows] or list(range(self.build_contents_table.rowCount()))
        self.build_contents_table.blockSignals(True)
        for row in target_rows:
            item = self.build_contents_table.item(row, 0)
            if not item or not self.current_build_content_rows[row].get("allow_disable", True):
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
                if self.current_build_content_rows[row].get("allow_disable", True)
                and self.build_contents_table.item(row, 0).checkState() != QtCore.Qt.Checked
            }
            for row in range(self.build_contents_table.rowCount()):
                data = self.current_build_content_rows[row]
                data["component"]["enabled"] = (
                    self.build_contents_table.item(row, 0).checkState() == QtCore.Qt.Checked
                    if data.get("allow_disable", True) else True)
            self.service.save_build_contents(
                status.identity, self.current_build_content_rows
            )
            for row, data in enumerate(self.current_build_content_rows):
                enabled = bool(data["component"].get("enabled", True))
                data["enabled"] = enabled
                state = self._local_content_state(data, enabled)
                data["state"] = state
                state_item = self.build_contents_table.item(row, 8)
                if state_item:
                    state_item.setText(state)
                    state_item.setForeground(
                        QtGui.QColor(build_content_state_color(state))
                    )
            enabled_count = sum(
                bool(row.get("enabled")) for row in self.current_build_content_rows
            )
            self.contents_summary_label.setText(
                f"{enabled_count} of {len(self.current_build_content_rows)} items enabled"
            )

    @staticmethod
    def _local_content_state(data: dict, enabled: bool) -> str:
        if not enabled:
            return "EXCLUDED"
        component_path = str((data.get("component") or {}).get("path") or "")
        if not component_path or not Path(component_path).exists():
            return "MISSING"
        build_version = str(data.get("build_version") or data.get("latest") or "")
        reviewed_version = str(data.get("last_review_version") or "")
        return (
            "UPDATE AVAILABLE"
            if reviewed_version and build_version and build_version != reviewed_version
            else "READY"
        )

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
            if context_widget.findText(context) < 0:
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

    def _populate_review_tab(self, row_data: ReviewShotStatus | None) -> None:
        enabled = bool(row_data and self.scope_combo.currentText() == "Shot")
        for widget in (
            self.review_profile_combo,
            self.delivery_profile_combo,
            self.precomp_combo,
            self.review_changes_btn,
            self.review_actions_btn,
        ):
            widget.setEnabled(enabled)
        self.submit_review_btn.setEnabled(False)
        if not enabled:
            self.layer_definition_label.setText("Definitions: -")
            self.review_status_label.setText("Select a shot")
            self._populate_planned_snapshot(None)
            return
        identity = row_data.identity
        layers = self.service.layer_definition(
            identity, self.department_combo.currentText()
        )
        layer_count = len(layers.get("layers") or [])
        workflow = self.service.review_workflow(identity)
        _assembly_data, assembly_path = workflow.latest_assembly()
        _layers_data, layers_path = workflow.latest_layer_definition()
        self.layer_definition_label.setText(
            f"Definitions: Shot Composition {assembly_path.parent.name if assembly_path else 'draft'} / "
            f"Layers {layers_path.parent.name if layers_path else 'draft'}"
        )
        precomp = workflow.latest_precomp()
        self.precomp_combo.clear()
        self.precomp_combo.addItem(
            "Latest Approved" if precomp else "Project Default",
            str(precomp or "project_default"),
        )
        for candidate in sorted(
            workflow.precomp_root.glob("v*/aftereffects/precomp.aep"),
            reverse=True,
        ):
            if not candidate.is_file() or candidate == precomp:
                continue
            self.precomp_combo.addItem(candidate.parent.parent.name, str(candidate))
        self.review_status_label.setText(
            f"Shot Composition {assembly_path.parent.name if assembly_path else 'draft'} / "
            f"{layer_count} layers"
        )
        task = self.task_combo.currentText() or "main"
        readiness = self.service.review_definition_validation(
            identity,
            self.department_combo.currentText(),
            task,
        )
        settings_path = readiness.get("render_manifest_path")
        settings_version = (
            settings_path.parent.name if settings_path else "missing"
        )
        definitions_ready = bool(readiness.get("ready"))
        self.submit_review_btn.setEnabled(enabled and definitions_ready)
        if not definitions_ready:
            self.review_status_label.setText(
                "Review validation: " + "; ".join(readiness.get("errors") or [])
            )
        else:
            self.review_status_label.setText(
                f"Shot Composition {assembly_path.parent.name} / "
                f"{layer_count} layers / render_manifest {settings_version}"
            )
        self._populate_planned_snapshot(row_data)

    def _planned_snapshot_key(self, identity) -> str:
        return "/".join(
            [
                self.service.project_name,
                identity.episode,
                identity.sequence,
                identity.shot,
                self.department_combo.currentText(),
                self.task_combo.currentText() or "main",
            ]
        )

    def _planned_snapshot_payload(self, identity) -> dict:
        rebuild_layers = []
        for row in range(self.planned_layers_table.rowCount()):
            item = self.planned_layers_table.item(row, 0)
            name = self.planned_layers_table.item(row, 1)
            if (
                item
                and name
                and item.checkState() == QtCore.Qt.Checked
            ):
                rebuild_layers.append(name.text())
        inputs = []
        for row in self.current_build_content_rows:
            component = row.get("component") or {}
            inputs.append({
                "enabled": bool(row.get("enabled")),
                "type": str(row.get("type") or ""),
                "name": str(row.get("cast_key") or row.get("name") or ""),
                "category": str(row.get("category") or ""),
                "context": str(row.get("context") or ""),
                "context_override": bool(row.get(
                    "context_override", (component.get("source") or {}).get("context_override", False)
                )),
                "version": str(
                    row.get("build_version") or row.get("latest") or row.get("version") or ""
                ),
                "path": str(component.get("path") or ""),
                "state": str(row.get("state") or ""),
                "required": bool(row.get("required", True)),
            })
        return {
            "schema": "smartpipeline.planned_review_snapshot.v1",
            "identity": {
                "episode": identity.episode,
                "sequence": identity.sequence,
                "shot": identity.shot,
            },
            "department": self.department_combo.currentText(),
            "task": self.task_combo.currentText() or "main",
            "review_profile": self.review_profile_combo.currentText(),
            "delivery_profile": self.delivery_profile_combo.currentText(),
            "precomp": str(self.precomp_combo.currentData() or "latest_approved"),
            "review_cache_policy": str(
                self.planned_cache_policy_combo.currentData() or "use"
            ),
            "rebuild_layers": rebuild_layers,
            "inputs": inputs,
        }

    def _planned_controls_changed(self, *_args) -> None:
        status = self._selected_status()
        if not status or not hasattr(self, "planned_layers_table"):
            return
        policy = str(self.planned_cache_policy_combo.currentData() or "use")
        selected_mode = policy == "rebuild_selected"
        self.planned_layers_table.setColumnHidden(0, not selected_mode)
        for row in range(self.planned_layers_table.rowCount()):
            expected = self.planned_layers_table.item(row, 4)
            rebuild = self.planned_layers_table.item(row, 0)
            forced = policy == "rebuild_all" or (
                selected_mode and rebuild and rebuild.checkState() == QtCore.Qt.Checked
            )
            if expected:
                expected.setText("REBUILD" if forced else "AUTO (HIT/MISS at job start)")
        payload = self._planned_snapshot_payload(status.identity)
        self._planned_snapshots[self._planned_snapshot_key(status.identity)] = payload
        self._settings().setValue(
            "planned_snapshots",
            json.dumps(self._planned_snapshots, ensure_ascii=False),
        )
        self.planned_snapshot_state_label.setText(
            "Saved planned override / exact cache result is resolved when the job starts"
        )

    def _planned_layer_changed(self, item) -> None:
        if item and item.column() == 0:
            self._planned_controls_changed()

    def _planned_input_changed(self, item) -> None:
        if not item or item.column() != 0:
            return
        row_index = item.data(QtCore.Qt.UserRole)
        if row_index is None or int(row_index) >= len(self.current_build_content_rows):
            return
        status = self._selected_status()
        if not status:
            return
        enabled = item.checkState() == QtCore.Qt.Checked
        data = self.current_build_content_rows[int(row_index)]
        if not data.get("allow_disable", True):
            return
        data["enabled"] = enabled
        if isinstance(data.get("component"), dict):
            data["component"]["enabled"] = enabled
        excluded = self._content_settings(status)["excluded"]
        name = str(data.get("cast_key") or data.get("name") or "")
        if enabled:
            excluded.discard(name)
        else:
            excluded.add(name)
        self.service.save_build_contents(status.identity, self.current_build_content_rows)
        self._planned_controls_changed()

    @staticmethod
    def _apply_planned_snapshot_to_construct(
        construct_data: dict, planned_snapshot: dict
    ) -> dict:
        type_aliases = {"virtual_camera": "camera"}
        overrides = {}
        for row in planned_snapshot.get("inputs") or []:
            component_type = str(row.get("type") or "").lower()
            component_type = type_aliases.get(component_type, component_type)
            name = str(row.get("name") or "")
            if name:
                overrides[(component_type, name)] = dict(row)
        result = dict(construct_data or {})
        components = []
        for incoming in result.get("components") or []:
            component = dict(incoming or {})
            key = (
                str(component.get("component_type") or component.get("type") or "").lower(),
                str(component.get("name") or ""),
            )
            if key in overrides:
                planned = overrides[key]
                component["enabled"] = bool(planned.get("enabled", True))
                resolved_context = str((component.get("source") or {}).get("context") or "")
                same_context = not (planned.get("context") and resolved_context) or (
                    str(planned["context"]).upper() == resolved_context.upper()
                )
                if same_context and planned.get("version"):
                    component["version"] = str(planned["version"])
                if same_context and planned.get("path"):
                    component["path"] = str(planned["path"])
            components.append(component)
        result["components"] = components
        return result

    def _populate_planned_snapshot(self, row_data: ReviewShotStatus | None) -> None:
        self.planned_inputs_table.blockSignals(True)
        self.planned_layers_table.blockSignals(True)
        try:
            self.planned_inputs_table.setRowCount(0)
            self.planned_layers_table.setRowCount(0)
            if not row_data:
                self.planned_snapshot_state_label.setText("Select a shot")
                return
            identity = row_data.identity
            saved = dict(self._planned_snapshots.get(self._planned_snapshot_key(identity)) or {})
            for combo, value, by_data in (
                (self.review_profile_combo, saved.get("review_profile"), False),
                (self.delivery_profile_combo, saved.get("delivery_profile"), False),
                (self.precomp_combo, saved.get("precomp"), True),
                (
                    self.planned_cache_policy_combo,
                    saved.get("review_cache_policy") or "use",
                    True,
                ),
            ):
                if not value:
                    continue
                index = combo.findData(value) if by_data else combo.findText(str(value))
                if index >= 0:
                    was_blocked = combo.blockSignals(True)
                    combo.setCurrentIndex(index)
                    combo.blockSignals(was_blocked)
            for row_index, data in enumerate(self.current_build_content_rows):
                row = self.planned_inputs_table.rowCount()
                self.planned_inputs_table.insertRow(row)
                use = QtWidgets.QTableWidgetItem()
                use_flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                if data.get("allow_disable", True):
                    use_flags |= QtCore.Qt.ItemIsUserCheckable
                use.setFlags(use_flags)
                use.setCheckState(
                    QtCore.Qt.Checked if data.get("enabled") else QtCore.Qt.Unchecked
                )
                use.setData(QtCore.Qt.UserRole, row_index)
                self.planned_inputs_table.setItem(row, 0, use)
                type_value = str(data.get("type") or "-")
                type_item = QtWidgets.QTableWidgetItem(type_value)
                icon_path = build_content_icon_path(type_value, size=24)
                if icon_path:
                    type_item.setIcon(QtGui.QIcon(str(icon_path)))
                self.planned_inputs_table.setItem(row, 1, type_item)
                values = [
                    data.get("cast_key") or data.get("name"),
                    data.get("category"),
                    data.get("context"),
                    data.get("build_version") or data.get("latest") or data.get("version"),
                    data.get("state"),
                ]
                for column, value in enumerate(values, start=2):
                    item = QtWidgets.QTableWidgetItem(str(value or "-"))
                    if column == 6:
                        item.setForeground(
                            QtGui.QColor(build_content_state_color(str(value or "")))
                        )
                    self.planned_inputs_table.setItem(row, column, item)
                options = list(data.get("context_options") or [])
                if options:
                    combo = QtWidgets.QComboBox()
                    combo.addItem("Stage Default", "")
                    for option in options:
                        combo.addItem(option, option)
                    explicit = bool(data.get(
                        "context_override",
                        (data.get("component", {}).get("source") or {}).get("context_override", False),
                    ))
                    selected = str(data.get("context") or "") if explicit else ""
                    combo.setCurrentIndex(max(0, combo.findData(selected)))
                    combo.setToolTip("Resolved context: " + str(data.get("context") or "-"))
                    combo.setProperty("content_row", row_index)
                    combo.currentIndexChanged.connect(self._planned_context_changed)
                    self.planned_inputs_table.setCellWidget(row, 4, combo)
            selected = set(saved.get("rebuild_layers") or [])
            definition = self.service.layer_definition(
                identity, self.department_combo.currentText()
            )
            for layer in definition.get("layers") or []:
                row = self.planned_layers_table.rowCount()
                self.planned_layers_table.insertRow(row)
                name = str(layer.get("slug") or layer.get("name") or "")
                rebuild = QtWidgets.QTableWidgetItem()
                rebuild.setFlags(
                    QtCore.Qt.ItemIsEnabled
                    | QtCore.Qt.ItemIsSelectable
                    | QtCore.Qt.ItemIsUserCheckable
                )
                rebuild.setCheckState(
                    QtCore.Qt.Checked if name in selected else QtCore.Qt.Unchecked
                )
                self.planned_layers_table.setItem(row, 0, rebuild)
                camera = layer.get("camera") or {}
                camera_name = (
                    camera.get("name") if isinstance(camera, dict) else camera
                )
                values = [
                    name,
                    ", ".join(str(value) for value in (layer.get("members") or [])),
                    camera_name,
                    "AUTO (HIT/MISS at job start)",
                ]
                for column, value in enumerate(values, start=1):
                    self.planned_layers_table.setItem(
                        row, column, QtWidgets.QTableWidgetItem(str(value or "-"))
                    )
        finally:
            self.planned_inputs_table.blockSignals(False)
            self.planned_layers_table.blockSignals(False)
        self._planned_controls_changed()

    def _review_submission_changes(self) -> None:
        status = self._selected_status()
        if not status:
            return
        profile = self.service.review_profiles.review_profile(
            self.review_profile_combo.currentText()
        )
        delivery = self.service.review_profiles.delivery_profile(
            self.delivery_profile_combo.currentText()
        )
        layers = self.service.layer_definition(
            status.identity, self.department_combo.currentText()
        )
        readiness = self.service.review_definition_validation(
            status.identity,
            self.department_combo.currentText(),
            self.task_combo.currentText() or "main",
        )
        settings = readiness.get("render_manifest") or {}
        settings_path = readiness.get("render_manifest_path")
        setting_rows = []
        for row in settings.get("rows") or []:
            if not row.get("enabled", True):
                continue
            setting_rows.append(
                f"  {row.get('layer') or row.get('display_layer')}: "
                f"{row.get('camera') or '-'} / "
                f"{row.get('width') or 0}x{row.get('height') or 0} / "
                f"{row.get('start')}-{row.get('end')} / "
                f"{row.get('output_format') or 'png'}"
            )
        message = (
            f"Review Profile: {profile['id']} ({profile.get('image_format', 'png').upper()})\n"
            f"Delivery Profile: {delivery['id']} ({delivery.get('codec', '-')})\n"
            f"Layers: {len(layers.get('layers') or [])}\n"
            f"render_manifest: {settings_path.parent.name if settings_path else 'missing'}\n"
            + ("\n".join(setting_rows) + "\n" if setting_rows else "")
            + "Exact HIT/MISS results are calculated from JSON snapshots when the job starts."
        )
        QtWidgets.QMessageBox.information(self, "Review Changes", message)

    def _set_review_cache_policy(
        self, policy: str, layers: list[str] | None = None
    ) -> bool:
        status = self._selected_status()
        if not status:
            QtWidgets.QMessageBox.information(
                self, "Review Actions", "Select a shot first."
            )
            return False
        identity = status.identity
        key = (identity.episode, identity.sequence, identity.shot)
        settings = self._review_submission_profiles.setdefault(key, {})
        settings["review_cache_policy"] = str(policy)
        settings["rebuild_layers"] = list(layers or [])
        label = {
            "rebuild_all": "Next Submit: rebuild all Review Layers",
            "rebuild_selected": (
                "Next Submit: rebuild " + ", ".join(layers or [])
            ),
            "ignore_all": "Submitting without Construct or Layer cache",
        }.get(policy, "Next Submit: use cache")
        self.review_status_label.setText(label)
        self.footer_label.setText(label)
        return True

    def _set_rebuild_all_layers(self) -> None:
        self._set_review_cache_policy("rebuild_all")

    def _set_rebuild_selected_layers(self) -> None:
        status = self._selected_status()
        if not status:
            QtWidgets.QMessageBox.information(
                self, "Review Actions", "Select a shot first."
            )
            return
        definition = self.service.layer_definition(
            status.identity, self.department_combo.currentText()
        )
        names = [
            str(layer.get("slug") or layer.get("name") or "")
            for layer in (definition.get("layers") or [])
            if str(layer.get("slug") or layer.get("name") or "")
        ]
        if not names:
            QtWidgets.QMessageBox.information(
                self, "Review Actions", "No published Review Layers were found."
            )
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Rebuild Selected Layers")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel("Select layers to rebuild on the next Submit."))
        layer_list = QtWidgets.QListWidget()
        layer_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        layer_list.addItems(names)
        layer_list.selectAll()
        layout.addWidget(layer_list)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        selected = [item.text() for item in layer_list.selectedItems()]
        if selected:
            self._set_review_cache_policy("rebuild_selected", selected)

    def _ignore_cache_and_submit(self) -> None:
        if self._set_review_cache_policy("ignore_all"):
            self._submit_for_review()

    def _submit_for_review(self) -> None:
        try:
            self._submit_for_review_impl()
        except Exception as exc:
            self.generate_review_check.setChecked(False)
            message = str(exc) or exc.__class__.__name__
            self.footer_label.setText("Submit for Review failed: " + message)
            QtWidgets.QMessageBox.critical(
                self, "Submit for Review", "Could not submit the Review:\n\n" + message
            )

    def _submit_for_review_impl(self) -> None:
        status = self._selected_status()
        if not status:
            return
        if self.scope_combo.currentText() != "Shot":
            QtWidgets.QMessageBox.information(self, "Submit for Review", "Select Shot scope.")
            return
        workflow = self.service.review_workflow(status.identity)
        _assembly, assembly_path = workflow.latest_assembly()
        _layers, layers_path = workflow.latest_layer_definition()
        readiness = self.service.review_definition_validation(
            status.identity,
            self.department_combo.currentText(),
            self.task_combo.currentText() or "main",
        )
        if not readiness.get("ready"):
            QtWidgets.QMessageBox.information(
                self,
                "Submit for Review",
                "Resolve the following before submitting:\n\n"
                + "\n".join(f"- {message}" for message in readiness.get("errors") or []),
            )
            return
        identity = status.identity
        planned_snapshot = self._planned_snapshot_payload(identity)
        missing_required = [
            f"{row.get('type') or 'data'} / {row.get('name') or 'main'}"
            for row in planned_snapshot.get("inputs") or []
            if row.get("enabled", True)
            and row.get("required", True)
            and str(row.get("state") or "").upper() == "MISSING"
        ]
        if missing_required:
            QtWidgets.QMessageBox.information(
                self,
                "Submit for Review",
                "Required Planned Snapshot inputs are missing:\n\n- "
                + "\n- ".join(missing_required),
            )
            return
        self.generate_review_check.setChecked(True)
        planned_snapshot["created_at"] = datetime.now().isoformat(timespec="seconds")
        self._review_submission_profiles = {
            (identity.episode, identity.sequence, identity.shot): {
                "review_profile": planned_snapshot["review_profile"],
                "delivery_profile": planned_snapshot["delivery_profile"],
                "precomp": planned_snapshot["precomp"],
                "review_cache_policy": planned_snapshot["review_cache_policy"],
                "rebuild_layers": planned_snapshot["rebuild_layers"],
                "planned_snapshot": planned_snapshot,
            }
        }
        submission_key = (identity.episode, identity.sequence, identity.shot)
        try:
            self._enqueue_builds([submission_key])
        finally:
            self.generate_review_check.setChecked(False)

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
            if plan.buildable and plan.resolved_mode in {"WORK STAGE", "REND STAGE", "UPDATE"}:
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
                output_version = self.service.next_sequence_construct_version(
                    identity, plan.department, plan.task
                )
                job_root = (
                    self.service.shots.sequence_build_root(identity, plan.department)
                    / plan.department / "maya" / plan.task / "_jobs"
                )
                label = identity.sequence
            else:
                if self.generate_review_check.isChecked():
                    workflow = self.service.review_workflow(identity)
                    output_version = workflow.next_construct_version(
                        plan.department, "maya", plan.task
                    )
                    job_root = workflow.jobs_root
                else:
                    output_version = self.service.next_construct_version(
                        identity, plan.department, plan.task
                    )
                    job_root = (
                        self.service.shots.shot_build_root(identity, plan.department)
                        / plan.department / "maya" / plan.task / "_jobs"
                    )
                label = identity.shot
            status_file = job_root / (
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
                f"{label}_{output_version}_{self.job_counter:03d}.json"
            )
            construct_snapshot = {}
            construct_changes = []
            canonical_fingerprint = ""
            reuse_construct = ""
            review_options = self._review_submission_profiles.get(
                tuple(raw_identity), {}
            )
            if scope != "sequence":
                overrides = self._stage_input_overrides(identity)
                previous_construct = self.service.shots.load_construct(identity)
                construct_data = self.service.shots.resolved_construct(
                    identity,
                    cast_contexts=overrides.get("cast_contexts") or {},
                    exclude_cast=overrides.get("exclude_cast") or [],
                    representation=overrides.get("representation") or "project",
                )
                construct_data = self._apply_planned_snapshot_to_construct(
                    construct_data,
                    review_options.get("planned_snapshot") or {},
                )
                self.service.shots.write_construct(identity, construct_data)
                construct_snapshot = self.service.shots.construct_snapshot(
                    identity, construct_data
                )
                construct_changes = self.service.construct_diff(
                    identity,
                    current=previous_construct,
                    desired=construct_data,
                )
                if self.generate_review_check.isChecked():
                    workflow = self.service.review_workflow(identity)
                    planned_snapshot = review_options.get("planned_snapshot") or {}
                    planned_layers, _planned_layers_path = self.service.planned_layer_definition(
                        identity, plan.department, planned_snapshot)
                    canonical_fingerprint = workflow.canonical_construct_fingerprint(
                        construct_snapshot=construct_snapshot,
                        assembly_definition=self.service.assembly_definition(identity),
                        layer_definition=planned_layers,
                    )
                    cached_construct = (
                        None
                        if review_options.get("review_cache_policy") == "ignore_all"
                        else workflow.find_canonical_construct(
                            plan.department, "maya", plan.task, canonical_fingerprint
                        )
                    )
                    if cached_construct:
                        output_version = cached_construct["version"]
                        reuse_construct = cached_construct["scene"]
            job = {
                "id": f"#{self.job_counter:04d}",
                "identity": raw_identity,
                "scope": scope,
                "shots": sequence_shots.get((raw_identity[0], raw_identity[1]), []),
                "sequence_options": (
                    self._sequence_options(identity) if scope == "sequence" else {}
                ),
                "construct": construct_snapshot,
                "input_overrides": self._stage_input_overrides(identity),
                "construct_changes": construct_changes,
                "canonical_fingerprint": canonical_fingerprint,
                "reuse_construct": reuse_construct,
                "version": output_version,
                "mode": plan.resolved_mode,
                "generate_review": self.generate_review_check.isChecked(),
                **review_options,
                "department": plan.department,
                "task_name": plan.task,
                "status_file": str(status_file),
                "state": "QUEUED",
                "progress": 0,
                "task": "Queued",
                "elapsed": QtCore.QElapsedTimer(),
                "row": self.queue_table.rowCount(),
                "stderr": "",
                "open_after_build": (
                    scope == "shot"
                    and self._open_after_build_identity == tuple(raw_identity)
                ),
            }
            self.pending_jobs.append(job)
            self.queue_jobs.append(job)
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
                "",
            ]
        ):
            self.queue_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
        queue_index = self.main_tabs.indexOf(self.job_queue_page)
        self.main_tabs.setTabText(
            queue_index, f"Job Queue ({self.queue_table.rowCount()})"
        )
        self.main_tabs.setCurrentIndex(queue_index)
        self.queue_table.selectRow(row)

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
        env_vars, path_vars = self.service.maya_process_environment()
        for key, value in env_vars.items():
            environment.insert(key, os.path.expandvars(value))
        for key, values in path_vars.items():
            resolved = [os.path.expandvars(value) for value in values if value]
            current = environment.value(key)
            if current:
                resolved.append(current)
            environment.insert(key, os.pathsep.join(resolved))
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
                "--maya-config",
                self.service.maya_software_config_name(),
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
                "--construct-diff-json",
                json.dumps(job.get("construct_changes") or []),
                "--canonical-fingerprint",
                str(job.get("canonical_fingerprint") or ""),
                "--reuse-construct",
                str(job.get("reuse_construct") or ""),
                "--operation",
                job["mode"],
                "--generate-review",
                "1" if job.get("generate_review") else "0",
                "--review-profile",
                str(job.get("review_profile") or "work_default"),
                "--delivery-profile",
                str(job.get("delivery_profile") or "internal"),
                "--precomp",
                str(job.get("precomp") or "latest_approved"),
                "--review-cache-policy",
                str(job.get("review_cache_policy") or "use"),
                "--rebuild-layers-json",
                json.dumps(job.get("rebuild_layers") or []),
                "--planned-snapshot-json",
                json.dumps(job.get("planned_snapshot") or {}, ensure_ascii=False),
                "--department",
                job["department"],
                "--task-name",
                job["task_name"],
                "--overrides-json",
                json.dumps(job.get("input_overrides") or {}),
            ]
        )
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[4]))
        try:
            request_path = write_job_request(job["status_file"], process.arguments()[2:])
            job["request_file"] = str(request_path)
            process.setArguments([
                "-m", "smartlib.apps.review_build_manager.worker",
                "--job-file", str(request_path),
            ])
        except (OSError, TypeError, ValueError) as exc:
            job["message"] = f"Could not save worker job request: {exc}"
            process.deleteLater()
            self._finish_active_job(False)
            return
        process.readyReadStandardError.connect(self._read_worker_stderr)
        process.readyReadStandardOutput.connect(self._read_worker_stdout)
        process.started.connect(self._worker_started)
        process.errorOccurred.connect(self._worker_process_error)
        process.finished.connect(self._worker_finished)
        self.worker_process = process
        job["launch_details"] = self._worker_launch_details(process)
        self.job_timer.start()
        self.footer_label.setText(f"Building {identity[0]}/{identity[1]}/{identity[2]}...")
        # Start the timer before start(): an immediate FailedToStart may finish
        # this job and start the next one from the error signal.
        process.start()

    @staticmethod
    def _worker_launch_details(process) -> str:
        length = len(subprocess.list2cmdline([process.program(), *process.arguments()]))
        lines = [
            "Program: " + process.program(),
            "Working directory: " + process.workingDirectory(),
            f"Command line characters (quoted): {length}",
        ]
        if os.name == "nt" and length >= 32767:
            lines.append("Command line exceeds the Windows CreateProcess limit (32767 characters).")
        return "\n".join(lines)

    def _worker_started(self) -> None:
        process = self.sender()
        if process is not self.worker_process or not self.active_job:
            return
        self.active_job["task"] = f"Initialize Maya (mayapy PID {process.processId()})"
        self._update_queue_row(self.active_job)

    def _worker_process_error(self, error) -> None:
        process = self.sender()
        if process is not self.worker_process or not self.active_job:
            return
        failed_start = error == QtCore.QProcess.FailedToStart
        label = "mayapy failed to start" if failed_start else "mayapy process error"
        message = f"{label}: {process.errorString()}"
        job = self.active_job
        job["message"] = message
        job["stderr"] = str(job.get("stderr") or "") + "\n" + message
        if failed_start:
            # Qt does not emit finished() when the process could not start.
            self._finish_active_job(False)
        else:
            self._update_queue_row(job)

    def _read_worker_stdout(self) -> None:
        process = self.sender()
        if process is not self.worker_process or not self.active_job:
            return
        text = self._decode_process_output(bytes(process.readAllStandardOutput()))
        self.active_job["stdout"] = (str(self.active_job.get("stdout") or "") + text)[-65536:]

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
        if self.sender() is not self.worker_process or not self.active_job:
            return
        text = self._decode_process_output(
            bytes(self.worker_process.readAllStandardError())
        )
        self.active_job["stderr"] += text

    def _worker_finished(self, exit_code: int, _exit_status) -> None:
        if self.sender() is not self.worker_process or not self.active_job:
            return
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
        if success and job.get("open_after_build"):
            scene_path = Path(str(job.get("message") or ""))
            if scene_path.is_file():
                try:
                    from smartlib.apps.shot_manager import ShotIdentity
                    from smartlib.dcc.maya.shot_builder import open_work_scene

                    identity = ShotIdentity(*job["identity"])
                    open_work_scene(
                        scene_path,
                        self.service.shots.load_shot(identity),
                    )
                    self.footer_label.setText(
                        f"Updated and opened: {scene_path.name}"
                    )
                except Exception as exc:
                    self.footer_label.setText(
                        f"Build completed, but Open failed: {exc}"
                    )
            self._open_after_build_identity = None
        self.active_job = None
        process = self.worker_process
        self.worker_process = None
        if process is not None:
            process.deleteLater()
        self.job_timer.stop()
        if self.pending_jobs:
            self._start_next_job()
        else:
            self.scan_updates()

    def queue_update_and_open(self, identity) -> None:
        """Queue one UPDATE and open the generated Construct on completion."""
        raw = (identity.episode, identity.sequence, identity.shot)
        self.scope_combo.setCurrentText("Shot")
        self.mode_combo.setCurrentText("UPDATE")
        self._restore_shot_selection(identity, ("shot", *raw))
        self._open_after_build_identity = raw
        self._enqueue_builds([raw])

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
            self._job_file_name(job),
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
        if self.queue_table.currentRow() == row:
            self._show_selected_job_details()

    def _show_selected_job_details(self) -> None:
        if not hasattr(self, "job_detail_text"):
            return
        row = self.queue_table.currentRow()
        if row < 0 or row >= len(self.queue_jobs):
            self.job_detail_text.clear()
            return
        job = self.queue_jobs[row]
        identity = job.get("identity") or ("", "", "")
        shot = identity[1] if job.get("scope") == "sequence" else "/".join(identity[1:3])
        message = self._clean_diagnostic_text(job.get("message") or "")
        stderr = self._clean_diagnostic_text(job.get("stderr") or "")
        sections = [
            f"Job: {job.get('id', '')}",
            f"Shot: {shot}",
            f"State: {job.get('state', '')}",
            f"Task: {self._clean_diagnostic_text(job.get('task') or '')}",
            f"Status File: {job.get('status_file', '')}",
        ]
        if message:
            sections.extend(["", "Message / Traceback:", message])
        if job.get("launch_details"):
            sections.extend(["", "Worker launch:", str(job["launch_details"])])
        if job.get("request_file"):
            sections.extend(["Job Request File: " + str(job["request_file"])])
        if job.get("stdout"):
            sections.extend(["", "Worker stdout (last 64K):", self._clean_diagnostic_text(job["stdout"])])
        if stderr and stderr not in message:
            sections.extend(["", "Worker stderr:", stderr])
        self.job_detail_text.setPlainText("\n".join(sections))

    @staticmethod
    def _decode_process_output(payload: bytes) -> str:
        if not payload:
            return ""
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            return payload.decode("cp932", errors="replace")

    @staticmethod
    def _clean_diagnostic_text(value: str) -> str:
        text = str(value or "")
        # U+FFFD cannot be restored, but replacing it makes the damaged portion
        # explicit while preserving the readable exception and traceback.
        return text.replace("\ufffd", "[unreadable byte]")

    @staticmethod
    def _job_file_name(job: dict) -> str:
        """Return the generated artifact name once a job completes."""
        if str(job.get("state") or "").upper() != "COMPLETE":
            return ""
        message = str(job.get("message") or "").strip()
        if not message or "\n" in message or "\r" in message:
            return ""
        candidate = Path(message)
        if candidate.suffix.lower() not in {
            ".ma", ".mb", ".usd", ".usda", ".usdc", ".mov", ".mp4", ".aep"
        }:
            return ""
        return candidate.name

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
            self._cached_build_plan(selected.identity)
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
        status_by_key = {
            self._identity_key(row): row for row in self.rows
        }
        checked_valid = any(
            identity not in busy_identities
            and identity in status_by_key
            and self._cached_plan_buildable(status_by_key[identity])
            for identity in checked
        )
        changes_available = any(
            self._cached_plan_buildable(row)
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
        stage_context = (
            "REND"
            if self.mode_combo.currentText() == "REND STAGE"
            else self.input_context_combo.currentText().strip()
        )
        overrides = {
            "context": stage_context,
            "camera": self.input_camera_edit.text().strip(),
            "layout_overlay": self.input_overlay_check.isChecked(),
            "use_placements": self.input_placements_check.isChecked(),
            "representation": str(
                self.input_representation_combo.currentData() or "project"
            ),
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
                name = str(component.get("name") or "")
                source = component.get("source") or {}
                if not str(source.get("asset") or ""):
                    continue
                saved_context = str(source.get("context") or "")
                if (
                    name and saved_context and bool(source.get("context_override"))
                    and name not in contexts
                ):
                    contexts[name] = saved_context
                if name and not bool(component.get("enabled", True)):
                    excluded.add(name)
            contexts = self._snapshot_contexts(
                contexts,
                self._planned_snapshots.get(self._planned_snapshot_key(identity)) or {},
                stage_context,
            )
            contexts = self.service.normalize_cast_contexts(
                identity,
                contexts,
                default_context=stage_context,
            )
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

    def _cached_build_plan(self, identity):
        """Return the plan resolved during the last UI refresh.

        Toggle handlers must not traverse project publishes on the Qt main
        thread. Build submission still calls ``_build_plan`` and therefore
        performs an authoritative validation immediately before queueing.
        """

        key = (identity.episode, identity.sequence, getattr(identity, "shot", ""))
        plan = self._build_plan_cache.get(key)
        if plan is None:
            plan = self._build_plan(identity)
            self._build_plan_cache[key] = plan
        return plan

    def _cached_plan_buildable(self, status: ReviewShotStatus) -> bool:
        """Check cached validation without resolving files from a toggle."""

        key = self._identity_key(status)
        plan = self._build_plan_cache.get(key)
        if plan is not None:
            return bool(plan.buildable)
        return status.state in BUILDABLE_STATES

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
            recipe_inputs = set(getattr(plan, "recipe_inputs", ()))
            for data in plan.inputs:
                if recipe_inputs and data.key not in recipe_inputs:
                    continue
                item = QtWidgets.QTreeWidgetItem(
                    [
                        "", data.label, "Required" if data.required else "Optional",
                        data.state, data.version or "-", data.path or "-", data.adapter or "-",
                    ]
                )
                item.setData(0, QtCore.Qt.UserRole, data.key)
                icon_path = sequence_input_icon_path(data.key, size=24)
                if icon_path:
                    item.setIcon(1, QtGui.QIcon(str(icon_path)))
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(0, QtCore.Qt.Checked if data.enabled else QtCore.Qt.Unchecked)
                self.sequence_inputs_tree.addTopLevelItem(item)
                for child in data.children:
                    child_item = QtWidgets.QTreeWidgetItem(
                        ["", child.label, "Optional", child.state, child.version or "-", child.path or "-", child.adapter or "-"]
                    )
                    child_item.setData(0, QtCore.Qt.UserRole, child.key)
                    child_item.setData(0, QtCore.Qt.UserRole + 1, data.key)
                    if icon_path:
                        child_item.setIcon(1, QtGui.QIcon(str(icon_path)))
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
        visible = True
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
            self.mode_combo.setCurrentText("WORK STAGE")
            layout_index = self.department_combo.findText("layout")
            if layout_index >= 0:
                self.department_combo.setCurrentIndex(layout_index)
        self._refresh_plan_columns()
        self._show_details(selected)
        self._update_stage_inputs_visibility()

    def _department_changed(self, _department: str) -> None:
        self._populate_tasks()
        self._refresh_plan_columns()
        self._status_basis_changed()

    def _refresh_plan_columns(self, *_args) -> None:
        self._apply_filters()

    def _status_basis_changed(self, *_args) -> None:
        # During settings restoration there is no scan result yet; the normal
        # startup scan will use the restored controls.
        if self.rows:
            self.scan_updates()

    def _generate_review_toggled(self, enabled: bool) -> None:
        """Apply the MOV requirement without resolving every shot again."""

        if not self.rows:
            return
        previous = {self._identity_key(row): row for row in self.rows}
        self.rows = [self.service.apply_generate_review_requirement(row, enabled) for row in self.rows]
        current = {self._identity_key(row): row for row in self.rows}
        self._populate_filters()
        for table_row in range(self.shot_table.rowCount()):
            shot_item = self.shot_table.item(table_row, 2)
            key = tuple(shot_item.data(QtCore.Qt.UserRole) or ()) if shot_item else ()
            before = previous.get(key)
            after = current.get(key)
            state_item = self.shot_table.item(table_row, 5)
            if not before or not after or not state_item:
                continue
            # Preserve BLOCKED/WARNING states supplied by Build validation.
            if state_item.text() == before.state and before.state != after.state:
                state_item.setText(after.state)
                state_item.setForeground(
                    QtGui.QColor(STATE_COLORS.get(after.state, "#dddddd"))
                )
        selected = self._selected_status()
        if selected and self.scope_combo.currentText() != "Sequence":
            self.detail_summary.setText(
                f"State: {selected.state}\n"
                f"Animation Curves: {selected.source_version or '-'}\n"
                f"Construct: {selected.output_label}\n"
                f"{selected.message}"
            )

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
        center_splitter = settings.value("center_splitter")
        if center_splitter:
            self.center_splitter.restoreState(center_splitter)
        self.main_tabs.setCurrentIndex(
            max(0, min(self.main_tabs.count() - 1, int(settings.value("main_tab", 0))))
        )
        saved_mode = str(settings.value("mode", "WORK STAGE"))
        saved_mode = {"AUTO": "WORK STAGE", "STAGE": "WORK STAGE", "REBUILD": "WORK STAGE", "REVIEW ONLY": "WORK STAGE"}.get(saved_mode, saved_mode)
        self.mode_combo.setCurrentText(saved_mode)
        self.scope_combo.setCurrentText(str(settings.value("scope", "Shot")))
        self.department_combo.setCurrentText(
            str(settings.value("department", "anim"))
        )
        self._populate_tasks()
        self.task_combo.setCurrentText(str(settings.value("task", "main")))
        self.input_policy_combo.setCurrentText(
            str(settings.value("input_policy", "Generate Missing"))
        )
        saved_stage_profile = str(settings.value("input_context", "WORK")).upper()
        self.input_context_combo.setCurrentText(
            "REND" if saved_stage_profile == "FINAL" else saved_stage_profile
        )
        representation = str(settings.value("input_representation", "project"))
        representation_index = self.input_representation_combo.findData(representation)
        self.input_representation_combo.setCurrentIndex(
            representation_index if representation_index >= 0 else 0
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
        try:
            self._planned_snapshots = json.loads(
                str(settings.value("planned_snapshots", "{}") or "{}")
            )
        except (TypeError, ValueError):
            self._planned_snapshots = {}
        self._update_stage_inputs_visibility()

    def closeEvent(self, event) -> None:
        settings = self._settings()
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("splitter", self.main_splitter.saveState())
        settings.setValue("center_splitter", self.center_splitter.saveState())
        settings.setValue("main_tab", self.main_tabs.currentIndex())
        settings.setValue("mode", self.mode_combo.currentText())
        settings.setValue("scope", self.scope_combo.currentText())
        settings.setValue("department", self.department_combo.currentText())
        settings.setValue("task", self.task_combo.currentText())
        settings.setValue("input_policy", self.input_policy_combo.currentText())
        settings.setValue("input_context", self.input_context_combo.currentText())
        settings.setValue(
            "input_representation",
            str(self.input_representation_combo.currentData() or "project"),
        )
        settings.setValue("input_camera", self.input_camera_edit.text())
        settings.setValue("input_overlay", self.input_overlay_check.isChecked())
        settings.setValue(
            "input_placements", self.input_placements_check.isChecked()
        )
        settings.setValue("exclude_cast", self.input_exclude_cast_edit.text())
        settings.setValue(
            "planned_snapshots",
            json.dumps(self._planned_snapshots, ensure_ascii=False),
        )
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
