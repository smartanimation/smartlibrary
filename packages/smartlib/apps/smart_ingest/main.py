from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from smartlib.apps.smart_ingest.service import IngestMetadata, PlanItem, SmartIngestService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json


TARGET_FILTERS = [
    ("storyreel", "storyreel"),
    ("editorial", ".mov .edl .xml .otio"),
    ("audio", ".wav"),
    ("design", ".pdf .jpeg .png"),
    ("asset", ".ma .fbx .abc .usd"),
    ("shot", ".ma .aep .mov"),
    ("reference", ".pdf .jpeg .png"),
    ("intake", "intake"),
    ("others", "(others)"),
    ("rejected", ""),
]


class SmartIngestWindow(QtWidgets.QMainWindow):
    def __init__(self, service: SmartIngestService):
        super().__init__()
        self.service = service
        self.items: list[PlanItem] = []
        self.current_row = -1
        self._updating_metadata_form = False
        self._dirty_metadata_fields: set[str] = set()
        self._reset_selection_on_next_auto_plan = False

        self.setWindowTitle("Smart Ingest")
        self.resize(1360, 760)
        self.setMinimumSize(1100, 620)
        self._build_ui()
        self._apply_style()
        self._set_default_dates()
        self.auto_plan()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        layout.addWidget(self._build_left_panel(), 0)
        layout.addWidget(self._build_center_tabs(), 1)
        layout.addWidget(self._build_metadata_panel(), 0)

    def _build_center_tabs(self) -> QtWidgets.QTabWidget:
        self.center_tabs = QtWidgets.QTabWidget()
        self.center_tabs.addTab(self._build_plan_panel(), "Incoming")
        self.center_tabs.addTab(self._build_history_panel(), "History")
        self.center_tabs.currentChanged.connect(self._center_tab_changed)
        return self.center_tabs

    def _build_left_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("panel")
        panel.setMinimumWidth(260)
        panel.setMaximumWidth(360)
        layout = QtWidgets.QVBoxLayout(panel)

        title = QtWidgets.QLabel("Date Range")
        layout.addWidget(title)
        date_row = QtWidgets.QHBoxLayout()
        self.date_from = QtWidgets.QDateEdit(calendarPopup=True)
        self.date_to = QtWidgets.QDateEdit(calendarPopup=True)
        for widget in (self.date_from, self.date_to):
            widget.setDisplayFormat("yyyy/MM/dd")
            date_row.addWidget(widget)
        layout.addLayout(date_row)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        layout.addWidget(line)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(QtWidgets.QLabel("Incoming"))
        header.addStretch()
        refresh = QtWidgets.QToolButton()
        refresh.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload))
        refresh.clicked.connect(self.auto_plan)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.incoming_tree = QtWidgets.QTreeWidget()
        self.incoming_tree.setHeaderHidden(True)
        self.incoming_tree.setMinimumHeight(220)
        layout.addWidget(self.incoming_tree)

        filter_header = QtWidgets.QLabel("Filter (Target Type)")
        filter_header.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        filter_header.customContextMenuRequested.connect(self._show_filter_context_menu)
        layout.addWidget(filter_header)
        self.target_filter_checks: dict[str, QtWidgets.QCheckBox] = {}
        for label, hint in TARGET_FILTERS:
            row = QtWidgets.QHBoxLayout()
            check = QtWidgets.QCheckBox(label)
            check.setChecked(True)
            check.stateChanged.connect(self._target_filter_changed)
            check.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            check.customContextMenuRequested.connect(self._show_filter_context_menu)
            row.addWidget(check)
            row.addStretch()
            row.addWidget(QtWidgets.QLabel(hint))
            layout.addLayout(row)
            self.target_filter_checks[label] = check

        layout.addStretch()
        self.summary_label = QtWidgets.QLabel("Total: 0 files")
        layout.addWidget(self.summary_label)
        return panel

    def _build_plan_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("panel")
        layout = QtWidgets.QVBoxLayout(panel)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(QtWidgets.QLabel("Ingest Plan"))
        header.addStretch()
        open_source_btn = QtWidgets.QToolButton()
        open_source_btn.setToolTip("Open Source in Explorer")
        open_source_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon))
        open_source_btn.clicked.connect(self.open_source_folder)
        header.addWidget(open_source_btn)
        auto_btn = QtWidgets.QPushButton("Auto Plan")
        auto_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_CommandLink))
        auto_btn.clicked.connect(self.auto_plan)
        header.addWidget(auto_btn)
        clear_btn = QtWidgets.QPushButton("Clear Plan")
        clear_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TrashIcon))
        clear_btn.clicked.connect(self.clear_plan)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["", "Source Path", "Target Path", "Type", "Action", "Target Type", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(2, 520)
        for column in (3, 4, 5, 6):
            self.table.horizontalHeader().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_plan_context_menu)
        self.table.itemSelectionChanged.connect(self._table_selection_changed)
        self.table.itemChanged.connect(self._table_item_changed)
        layout.addWidget(self.table)

        self.plan_summary_label = QtWidgets.QLabel("Plan Items: 0")
        layout.addWidget(self.plan_summary_label)
        return panel

    def _build_history_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("panel")
        layout = QtWidgets.QVBoxLayout(panel)

        toolbar = QtWidgets.QHBoxLayout()
        self.history_search = QtWidgets.QLineEdit()
        self.history_search.setPlaceholderText("Search processed files")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(self._refresh_history)
        toolbar.addWidget(self.history_search, 1)
        self.history_type_combo = QtWidgets.QComboBox()
        self.history_type_combo.addItems(["All", "Editorial", "Asset", "Shot", "Sequence", "Intake", "Ignored"])
        self.history_type_combo.currentTextChanged.connect(self._refresh_history)
        toolbar.addWidget(self.history_type_combo)
        refresh = QtWidgets.QToolButton()
        refresh.setToolTip("Refresh History")
        refresh.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload))
        refresh.clicked.connect(self._refresh_history)
        toolbar.addWidget(refresh)
        layout.addLayout(toolbar)

        self.history_table = QtWidgets.QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(
            ["Processed", "Source", "Type", "Target Type", "Target", "Status"]
        )
        self.history_table.verticalHeader().hide()
        self.history_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.history_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setShowGrid(False)
        self.history_table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self._show_history_context_menu)
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.setColumnWidth(1, 210)
        self.history_table.itemDoubleClicked.connect(self._open_history_target)
        layout.addWidget(self.history_table)

        buttons = QtWidgets.QHBoxLayout()
        open_source = QtWidgets.QPushButton("Open Source")
        open_source.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon))
        open_source.clicked.connect(self._open_history_source)
        buttons.addWidget(open_source)
        open_target = QtWidgets.QPushButton("Open Target")
        open_target.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon))
        open_target.clicked.connect(self._open_history_target)
        buttons.addWidget(open_target)
        restore = QtWidgets.QPushButton("Retry Ingest")
        restore.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowBack))
        restore.clicked.connect(self._restore_history_to_incoming)
        buttons.addWidget(restore)
        layout.addLayout(buttons)

        self.history_summary_label = QtWidgets.QLabel("Processed: 0")
        layout.addWidget(self.history_summary_label)
        return panel

    def _build_metadata_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("panel")
        panel.setMinimumWidth(320)
        panel.setMaximumWidth(460)
        layout = QtWidgets.QVBoxLayout(panel)

        layout.addWidget(QtWidgets.QLabel("Target / Metadata"))
        self.target_type_combo = QtWidgets.QComboBox()
        self.target_type_combo.addItems(["Asset", "Shot", "Sequence", "Editorial", "Intake"])
        layout.addWidget(self._form_row("Target Type", self.target_type_combo))

        self.project_edit = QtWidgets.QLineEdit(self.service.project_name)
        self.asset_edit = self._editable_combo()
        self.category_edit = self._editable_combo(self.service.asset_categories())
        self.group_edit = self._editable_combo(["main"])
        self.variant_edit = self._editable_combo(["default"])
        self.department_edit = self._editable_combo(self.service.asset_departments())
        self.sequence_data_type_combo = QtWidgets.QComboBox()
        self.sequence_data_type_combo.addItems(self.service.sequence_data_types())
        self.editorial_data_role_combo = QtWidgets.QComboBox()
        self.editorial_data_role_combo.addItems(self.service.editorial_data_roles())
        self.subset_edit = self._editable_combo(["client", "main"])
        self.format_edit = QtWidgets.QLineEdit()
        self.episode_edit = QtWidgets.QLineEdit("ep001")
        self.sequence_edit = QtWidgets.QLineEdit("sq010")
        self.shot_edit = QtWidgets.QLineEdit()
        self.vendor_edit = QtWidgets.QLineEdit()
        self.delivery_date_edit = QtWidgets.QLineEdit()
        self.comment_edit = QtWidgets.QPlainTextEdit("ingest via Smart Ingest")
        self.comment_edit.setFixedHeight(72)

        self.metadata_rows: dict[str, QtWidgets.QWidget] = {}
        self.metadata_labels: dict[str, QtWidgets.QLabel] = {}
        for label, widget, key in [
            ("Project", self.project_edit, "project"),
            ("Category", self.category_edit, "category"),
            ("Group", self.group_edit, "group"),
            ("Asset", self.asset_edit, "asset"),
            ("Variant", self.variant_edit, "variant"),
            ("Department", self.department_edit, "department"),
            ("Data Type", self.sequence_data_type_combo, "sequence_data_type"),
            ("Data Role", self.editorial_data_role_combo, "editorial_data_role"),
            ("Subset", self.subset_edit, "subset"),
            ("Format", self.format_edit, "format"),
            ("Episode", self.episode_edit, "episode"),
            ("Sequence", self.sequence_edit, "sequence"),
            ("Shot", self.shot_edit, "shot"),
            ("Source", self.vendor_edit, "vendor"),
            ("Delivery Date", self.delivery_date_edit, "delivery_date"),
        ]:
            row = self._form_row(label, widget)
            self.metadata_rows[key] = row
            row_label = row.findChild(QtWidgets.QLabel)
            if row_label:
                self.metadata_labels[key] = row_label
            layout.addWidget(row)
        layout.addWidget(QtWidgets.QLabel("Comment"))
        layout.addWidget(self.comment_edit)

        self.create_folders_check = QtWidgets.QCheckBox("Create folder structure if not exists")
        self.create_folders_check.setChecked(True)
        layout.addWidget(self.create_folders_check)

        self.apply_metadata_btn = QtWidgets.QPushButton("Apply Metadata to Selected")
        self.apply_metadata_btn.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogContentsView)
        )
        self.apply_metadata_btn.clicked.connect(self.apply_metadata_to_selection)
        layout.addWidget(self.apply_metadata_btn)

        self.ignore_btn = QtWidgets.QPushButton("Mark Not CG / Ignore Selected")
        self.ignore_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogDiscardButton))
        self.ignore_btn.clicked.connect(self.ignore_selected)
        layout.addWidget(self.ignore_btn)

        self.ingest_btn = QtWidgets.QPushButton("Ingest Selected")
        self.ingest_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogApplyButton))
        self.ingest_btn.clicked.connect(self.ingest_selected)
        layout.addWidget(self.ingest_btn)

        open_btn = QtWidgets.QPushButton("Open Target Folder")
        open_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon))
        open_btn.clicked.connect(self.open_target_folder)
        layout.addWidget(open_btn)
        layout.addStretch()

        self.target_type_combo.currentTextChanged.connect(self._target_type_changed)
        self.category_edit.currentTextChanged.connect(self._asset_scope_changed)
        self.group_edit.currentTextChanged.connect(self._asset_scope_changed)
        self.asset_edit.currentTextChanged.connect(self._asset_scope_changed)
        self.department_edit.currentTextChanged.connect(self._asset_department_changed)
        self.sequence_data_type_combo.currentTextChanged.connect(self._sequence_data_type_changed)
        self.editorial_data_role_combo.currentTextChanged.connect(
            lambda _value: self._mark_metadata_dirty("subset")
        )
        line_field_widgets = {
            "project": self.project_edit,
            "format": self.format_edit,
            "episode": self.episode_edit,
            "sequence": self.sequence_edit,
            "shot": self.shot_edit,
            "vendor": self.vendor_edit,
            "delivery_date": self.delivery_date_edit,
        }
        for field_name, widget in line_field_widgets.items():
            widget.editingFinished.connect(
                lambda name=field_name: self._mark_metadata_dirty(name)
            )
        for field_name, widget in {
            "asset": self.asset_edit,
            "category": self.category_edit,
            "group": self.group_edit,
            "variant": self.variant_edit,
            "department": self.department_edit,
            "subset": self.subset_edit,
        }.items():
            widget.currentTextChanged.connect(lambda _value, name=field_name: self._mark_metadata_dirty(name))
        self.comment_edit.textChanged.connect(lambda: self._mark_metadata_dirty("comment"))
        return panel

    def _editable_combo(self, values: list[str] | None = None) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        combo.addItems(values or [])
        if values:
            combo.setCurrentIndex(0)
        return combo

    def _form_row(self, label: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        text = QtWidgets.QLabel(label)
        text.setFixedWidth(92)
        layout.addWidget(text)
        layout.addWidget(widget, 1)
        return row

    def _set_default_dates(self) -> None:
        incoming_range = self.service.incoming_date_range()
        if incoming_range:
            start, end = incoming_range
        else:
            end = date.today()
            start = end - timedelta(days=7)
        self.date_from.setDate(QtCore.QDate(start.year, start.month, start.day))
        self.date_to.setDate(QtCore.QDate(end.year, end.month, end.day))

    def auto_plan(self) -> None:
        if self.items:
            self._sync_checkboxes_to_items()
        selected_by_source = (
            {}
            if self._reset_selection_on_next_auto_plan
            else {str(item.source_path): item.selected for item in self.items}
        )
        self._reset_selection_on_next_auto_plan = False
        date_from = self.date_from.date().toPython()
        date_to = self.date_to.date().toPython()
        rejected_check = self.target_filter_checks.get("rejected")
        planned = self._filter_plan_items(
            self.service.auto_plan(
                date_from=date_from,
                date_to=date_to,
                include_rejected=bool(rejected_check and rejected_check.isChecked()),
            )
        )
        self.items = [
            replace(item, selected=selected_by_source.get(str(item.source_path), item.selected))
            for item in planned
        ]
        self._refresh_tree()
        self._refresh_table()

    def clear_plan(self) -> None:
        self.items = [replace(item, selected=False) for item in self.items]
        self._reset_selection_on_next_auto_plan = True
        self._refresh_table(select_row=self.current_row)

    def apply_metadata_to_selection(self) -> None:
        if not self._dirty_metadata_fields:
            QtWidgets.QMessageBox.information(
                self,
                "Apply Metadata",
                "No metadata fields have been changed.",
            )
            return
        rows = self._selected_rows()
        if not rows and self.current_row >= 0:
            rows = [self.current_row]
        if not rows:
            return
        form_metadata = self._metadata_from_form()
        for row in rows:
            previous = self.items[row]
            changes = {
                field_name: getattr(form_metadata, field_name)
                for field_name in self._dirty_metadata_fields
            }
            metadata = replace(previous.metadata, **changes)
            self.items[row] = replace(
                self.service.replan(previous, metadata),
                selected=previous.selected,
            )
        self._dirty_metadata_fields.clear()
        self._refresh_table(select_row=rows[0] if rows else -1)

    def ingest_selected(self) -> None:
        self._sync_checkboxes_to_items()
        result = self.service.ingest_selected(self.items, create_folders=self.create_folders_check.isChecked())
        QtWidgets.QMessageBox.information(
            self,
            "Smart Ingest",
            f"Copied: {len(result.copied)}\nProcessed: {len(result.processed_sources)}\nRejected: {len(result.rejected)}\nSkipped: {len(result.skipped)}",
        )
        self.auto_plan()
        self._refresh_history()

    def ignore_selected(self) -> None:
        rows = self._selected_rows()
        if not rows and self.current_row >= 0:
            rows = [self.current_row]
        if not rows:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Mark Not CG / Ignore",
            f"Mark {len(rows)} file(s) as not related to CG ingest?\n\n"
            "Files stay in incoming, but will be hidden from Auto Plan until retried from History.",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        selected_items = [replace(item, selected=index in rows) for index, item in enumerate(self.items)]
        result = self.service.ignore_items(selected_items)
        QtWidgets.QMessageBox.information(
            self,
            "Mark Not CG / Ignore",
            f"Ignored: {len(result.processed_sources)}\nSkipped: {len(result.skipped)}",
        )
        self.auto_plan()
        self._refresh_history()

    def open_target_folder(self) -> None:
        item = self._current_item()
        if not item or not item.target_path:
            return
        folder = item.target_path if item.target_path.is_dir() else item.target_path.parent
        try:
            os.startfile(str(folder))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Open Target Folder", str(exc))

    def open_source_folder(self) -> None:
        item = self._current_item()
        if not item:
            return
        self._reveal_in_explorer(item.source_path, "Open Source")

    def _show_plan_context_menu(self, position: QtCore.QPoint) -> None:
        index = self.table.indexAt(position)
        if index.isValid() and index.row() != self.current_row:
            self.table.selectRow(index.row())
        item = self._current_item()

        menu = QtWidgets.QMenu(self.table)
        select_all = menu.addAction("Select All")
        select_none = menu.addAction("Deselect All")
        invert_selection = menu.addAction("Invert Selection")
        menu.addSeparator()
        open_source = menu.addAction(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon),
            "Open Source in Explorer",
        )
        open_target = menu.addAction(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon),
            "Open Target Folder",
        )
        open_source.setEnabled(item is not None)
        open_target.setEnabled(item is not None and item.target_path is not None)
        menu.addSeparator()
        ignore = menu.addAction(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogDiscardButton),
            "Mark Not CG / Ignore Selected",
        )
        selected = menu.exec(self.table.viewport().mapToGlobal(position))
        if selected == select_all:
            self._set_incoming_checked("all")
        elif selected == select_none:
            self._set_incoming_checked("none")
        elif selected == invert_selection:
            self._set_incoming_checked("invert")
        elif selected == open_source:
            self.open_source_folder()
        elif selected == open_target:
            self.open_target_folder()
        elif selected == ignore:
            self.ignore_selected()

    def _reveal_in_explorer(self, path: Path, label: str) -> None:
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, label, f"Path was not found:\n{path}")
            return
        try:
            if path.is_file():
                QtCore.QProcess.startDetached("explorer.exe", ["/select,", str(path)])
            else:
                os.startfile(str(path))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, label, str(exc))

    def _center_tab_changed(self, index: int) -> None:
        if self.center_tabs.tabText(index) == "History":
            self._refresh_history()

    def _refresh_history(self) -> None:
        if not hasattr(self, "history_table"):
            return
        search = self.history_search.text().strip().lower()
        target_filter = self.history_type_combo.currentText()
        records = []
        if self.service.incoming_root.exists():
            for state_path in self.service.incoming_root.rglob("processed.json"):
                state = read_json(state_path, {}) or {}
                files = state.get("files") if isinstance(state.get("files"), dict) else {}
                for record_key, data in files.items():
                    if not isinstance(data, dict) or data.get("status") not in {"processed", "ignored"}:
                        continue
                    target_type = str(data.get("target_type") or "")
                    if target_filter != "All" and not self._history_type_matches(target_type, target_filter):
                        continue
                    if not self._history_passes_target_filters(data):
                        continue
                    searchable = " ".join(
                        str(data.get(key) or "")
                        for key in ("source_path", "source", "output_path", "target_type")
                    ).lower()
                    if search and search not in searchable:
                        continue
                    records.append((state_path, str(record_key), data, False))
            for manifest_path in sorted(
                self.service.incoming_root.rglob("*.ingest.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            ):
                data = read_json(manifest_path, {}) or {}
                if data.get("state") != "processed":
                    continue
                target_type = str(data.get("target_type") or "")
                if target_filter != "All" and not self._history_type_matches(target_type, target_filter):
                    continue
                if not self._history_passes_target_filters(data):
                    continue
                searchable = " ".join(
                    str(data.get(key) or "")
                    for key in ("source_path", "processed_source_path", "output_path", "target_type")
                ).lower()
                if search and search not in searchable:
                    continue
                records.append((manifest_path, "", data, True))

        records.sort(
            key=lambda value: str(value[2].get("processed_at") or value[2].get("created_at") or ""),
            reverse=True,
        )

        self.history_table.setRowCount(len(records))
        for row, (manifest_path, record_key, data, legacy) in enumerate(records):
            source_path = Path(str(data.get("source_path") or data.get("processed_source_path") or ""))
            target_path = Path(str(data.get("output_path") or ""))
            values = [
                str(data.get("processed_at") or data.get("created_at") or "").replace("T", " "),
                source_path.name,
                source_path.suffix.lower().lstrip(".").upper() or "FILE",
                self._display_target_type(str(data.get("target_type") or "-")),
                str(target_path),
                str(data.get("status") or data.get("state") or "-"),
            ]
            for column, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                cell.setToolTip(value)
                if column == 0:
                    cell.setData(QtCore.Qt.ItemDataRole.UserRole, str(manifest_path))
                    cell.setData(QtCore.Qt.ItemDataRole.UserRole + 1, str(source_path))
                    cell.setData(QtCore.Qt.ItemDataRole.UserRole + 2, str(target_path))
                    cell.setData(QtCore.Qt.ItemDataRole.UserRole + 3, record_key)
                    cell.setData(QtCore.Qt.ItemDataRole.UserRole + 4, legacy)
                self.history_table.setItem(row, column, cell)
        self.history_summary_label.setText(f"Processed: {len(records)}")

    @staticmethod
    def _history_type_matches(target_type: str, target_filter: str) -> bool:
        if target_filter == "Intake" and target_type == "Vendor":
            return True
        return target_type == target_filter

    @staticmethod
    def _display_target_type(target_type: str) -> str:
        return "Intake" if target_type == "Vendor" else target_type

    def _history_passes_target_filters(self, data: dict) -> bool:
        enabled = {key for key, checkbox in self.target_filter_checks.items() if checkbox.isChecked()}
        return self._history_filter_key(data) in enabled

    def _history_filter_key(self, data: dict) -> str:
        target_type = str(data.get("target_type") or "").lower()
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        output_path = Path(str(data.get("output_path") or data.get("source_path") or ""))
        file_type = output_path.suffix.lower().lstrip(".")
        subset = str(metadata.get("subset") or "").lower()
        department = str(metadata.get("department") or "").lower()
        status = str(data.get("status") or data.get("state") or "").lower()
        if status == "ignored" or target_type == "ignored":
            return "others"
        if target_type in {"rejected", "reject"}:
            return "rejected"
        if target_type == "shot" and (department == "audio" or file_type == "wav"):
            return "audio"
        if target_type == "editorial" and subset == "storyreel":
            return "storyreel"
        if target_type == "vendor":
            return "intake"
        if target_type in {"asset", "shot", "editorial", "intake", "design", "reference"}:
            return target_type
        return "others"

    def _show_filter_context_menu(self, position: QtCore.QPoint) -> None:
        sender = self.sender()
        menu = QtWidgets.QMenu(self)
        select_all = menu.addAction("Select All")
        select_none = menu.addAction("Deselect All")
        invert_selection = menu.addAction("Invert Selection")
        selected = menu.exec(sender.mapToGlobal(position) if isinstance(sender, QtWidgets.QWidget) else QtGui.QCursor.pos())
        if selected == select_all:
            self._set_filter_checks("all")
        elif selected == select_none:
            self._set_filter_checks("none")
        elif selected == invert_selection:
            self._set_filter_checks("invert")

    def _set_filter_checks(self, mode: str) -> None:
        for checkbox in self.target_filter_checks.values():
            checkbox.blockSignals(True)
            if mode == "all":
                checkbox.setChecked(True)
            elif mode == "none":
                checkbox.setChecked(False)
            elif mode == "invert":
                checkbox.setChecked(not checkbox.isChecked())
            checkbox.blockSignals(False)
        self._target_filter_changed()

    def _target_filter_changed(self) -> None:
        self.auto_plan()
        self._refresh_history()

    def _show_history_context_menu(self, position: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self.history_table)
        select_all = menu.addAction("Select All")
        select_none = menu.addAction("Deselect All")
        invert_selection = menu.addAction("Invert Selection")
        selected = menu.exec(self.history_table.viewport().mapToGlobal(position))
        if selected == select_all:
            self.history_table.selectAll()
        elif selected == select_none:
            self.history_table.clearSelection()
        elif selected == invert_selection:
            self._invert_table_row_selection(self.history_table)

    def _set_incoming_checked(self, mode: str) -> None:
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item is None:
                    continue
                checked = item.checkState() == QtCore.Qt.CheckState.Checked
                if mode == "all":
                    item.setCheckState(QtCore.Qt.CheckState.Checked)
                elif mode == "none":
                    item.setCheckState(QtCore.Qt.CheckState.Unchecked)
                elif mode == "invert":
                    item.setCheckState(QtCore.Qt.CheckState.Unchecked if checked else QtCore.Qt.CheckState.Checked)
        finally:
            self.table.blockSignals(False)
        self._sync_checkboxes_to_items()
        self._update_summary()

    @staticmethod
    def _invert_table_row_selection(table: QtWidgets.QTableWidget) -> None:
        selected_rows = {index.row() for index in table.selectionModel().selectedRows()}
        table.clearSelection()
        selection_model = table.selectionModel()
        flags = QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows
        for row in range(table.rowCount()):
            if row in selected_rows:
                continue
            first = table.model().index(row, 0)
            last = table.model().index(row, table.columnCount() - 1)
            selection_model.select(QtCore.QItemSelection(first, last), flags)

    def _history_paths(self) -> tuple[Path | None, Path | None]:
        row = self.history_table.currentRow()
        cell = self.history_table.item(row, 0) if row >= 0 else None
        if cell is None:
            return None, None
        source_text = cell.data(QtCore.Qt.ItemDataRole.UserRole + 1)
        target_text = cell.data(QtCore.Qt.ItemDataRole.UserRole + 2)
        return Path(source_text) if source_text else None, Path(target_text) if target_text else None

    def _open_history_source(self) -> None:
        source, _ = self._history_paths()
        self._open_history_path(source, "Source")

    def _open_history_target(self, *_args) -> None:
        _, target = self._history_paths()
        self._open_history_path(target, "Target")

    def _open_history_path(self, path: Path | None, label: str) -> None:
        if path is None:
            return
        folder = path if path.is_dir() else path.parent
        if not folder.exists():
            QtWidgets.QMessageBox.warning(self, label, f"Folder was not found:\n{folder}")
            return
        try:
            os.startfile(str(folder))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, label, str(exc))

    def _restore_history_to_incoming(self) -> None:
        rows = sorted({index.row() for index in self.history_table.selectionModel().selectedRows()})
        if not rows:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Retry Ingest",
            f"Mark {len(rows)} processed package(s) for ingest again?\n\n"
            "Incoming originals and history will remain unchanged.",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        retried = []
        errors = []
        for row in rows:
            cell = self.history_table.item(row, 0)
            manifest_path = cell.data(QtCore.Qt.ItemDataRole.UserRole) if cell else None
            record_key = cell.data(QtCore.Qt.ItemDataRole.UserRole + 3) if cell else ""
            legacy = bool(cell.data(QtCore.Qt.ItemDataRole.UserRole + 4)) if cell else False
            if not manifest_path:
                continue
            try:
                if legacy:
                    retried.extend(self.service.restore_processed_manifest(manifest_path))
                else:
                    retried.append(self.service.retry_processed_record(manifest_path, record_key))
            except Exception as exc:
                errors.append(str(exc))

        self._refresh_history()
        self.auto_plan()
        message = f"Ready to retry: {len(retried)}"
        if errors:
            message += "\n\nErrors:\n" + "\n".join(errors)
            QtWidgets.QMessageBox.warning(self, "Retry Ingest", message)
        else:
            QtWidgets.QMessageBox.information(self, "Retry Ingest", message)

    def _refresh_tree(self) -> None:
        self.incoming_tree.clear()
        root = QtWidgets.QTreeWidgetItem([str(self.service.incoming_root)])
        self.incoming_tree.addTopLevelItem(root)
        counts: dict[str, int] = {}
        for item in self.items:
            try:
                key = item.source_path.relative_to(self.service.incoming_root).parts[0]
            except Exception:
                key = "incoming"
            counts[key] = counts.get(key, 0) + 1
        for key, count in sorted(counts.items()):
            QtWidgets.QTreeWidgetItem(root, [f"{key}  ({count})"])
        root.setExpanded(True)

    def _refresh_table(self, *, select_row: int = -1) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(QtCore.Qt.CheckState.Checked if item.selected else QtCore.Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(item.source_path.name))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(self._short_path(item.target_path)))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(item.file_type))
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(item.action))
            self.table.setItem(row, 5, QtWidgets.QTableWidgetItem(item.target_type or "-"))
            status = QtWidgets.QTableWidgetItem(item.status)
            status.setToolTip(item.reason)
            status.setForeground(self._status_color(item.status))
            self.table.setItem(row, 6, status)
        self.table.blockSignals(False)
        self.plan_summary_label.setText(f"Plan Items: {len(self.items)}   Total Size: {self._format_size(sum(item.size for item in self.items))}")
        selected = sum(1 for item in self.items if item.selected and item.actionable)
        self.ingest_btn.setText(f"Ingest Selected ({selected})")
        self._update_apply_metadata_label()
        if select_row >= 0 and select_row < len(self.items):
            self.table.selectRow(select_row)
        elif self.items:
            self.table.selectRow(0)
        self._update_summary()

    def _table_selection_changed(self) -> None:
        rows = self._selected_rows()
        self.current_row = rows[0] if rows else -1
        item = self._current_item()
        if item:
            self._metadata_to_form(item.metadata)
        self._update_apply_metadata_label()

    def _table_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        self._sync_checkboxes_to_items()
        self._update_summary()
        selected = sum(1 for plan_item in self.items if plan_item.selected and plan_item.actionable)
        self.ingest_btn.setText(f"Ingest Selected ({selected})")
        self._update_apply_metadata_label()

    def _update_apply_metadata_label(self) -> None:
        if not hasattr(self, "apply_metadata_btn"):
            return
        rows = self._selected_rows()
        count = len(rows)
        field_count = len(self._dirty_metadata_fields)
        prefix = f"Apply {field_count} Changed Field(s)" if field_count else "Apply Metadata"
        self.apply_metadata_btn.setText(
            f"{prefix} to Selected ({count})" if count else f"{prefix} to Selected"
        )
        self.apply_metadata_btn.setToolTip(
            "Changed fields: " + ", ".join(sorted(self._dirty_metadata_fields))
            if self._dirty_metadata_fields
            else "Edit one or more metadata fields, then apply them to selected rows."
        )

    def _target_type_changed(self) -> None:
        self._update_metadata_visibility()
        self._mark_metadata_dirty("target_type")

    def _sequence_data_type_changed(self) -> None:
        self._update_metadata_visibility()
        self._mark_metadata_dirty("department")

    def _mark_metadata_dirty(self, field_name: str) -> None:
        if self._updating_metadata_form:
            return
        self._dirty_metadata_fields.add(field_name)
        self._update_apply_metadata_label()

    def _metadata_to_form(self, metadata: IngestMetadata) -> None:
        self._updating_metadata_form = True
        try:
            self.target_type_combo.blockSignals(True)
            target_type = "Intake" if metadata.target_type == "Vendor" else metadata.target_type
            if target_type in ["Asset", "Shot", "Sequence", "Editorial", "Intake"]:
                self.target_type_combo.setCurrentText(target_type)
            self.target_type_combo.blockSignals(False)
            self.project_edit.setText(metadata.project)
            self._set_combo_value(self.category_edit, metadata.category)
            self._refresh_asset_metadata_choices()
            self._set_combo_value(self.group_edit, metadata.group)
            self._refresh_asset_metadata_choices()
            self._set_combo_value(self.asset_edit, metadata.asset)
            self._refresh_asset_metadata_choices()
            self._set_combo_value(self.variant_edit, metadata.variant)
            self._set_combo_value(self.department_edit, metadata.department)
            self._refresh_asset_subset_choices()
            self._set_combo_value(self.sequence_data_type_combo, metadata.department)
            self._set_combo_value(self.editorial_data_role_combo, metadata.subset.split("/", 1)[0])
            self._set_combo_value(self.subset_edit, metadata.subset)
            self.format_edit.setText(metadata.format)
            self.episode_edit.setText(metadata.episode)
            self.sequence_edit.setText(metadata.sequence)
            self.shot_edit.setText(metadata.shot)
            self.vendor_edit.setText(metadata.vendor)
            self.delivery_date_edit.setText(metadata.delivery_date)
            self.comment_edit.setPlainText(metadata.comment)
            self._update_metadata_visibility()
            self._dirty_metadata_fields.clear()
        finally:
            self.target_type_combo.blockSignals(False)
            self._updating_metadata_form = False

    def _update_metadata_visibility(self) -> None:
        target_type = self.target_type_combo.currentText()
        visible_by_type = {
            "Asset": {
                "project",
                "asset",
                "category",
                "group",
                "variant",
                "department",
                "subset",
                "format",
                "delivery_date",
            },
            "Shot": {
                "project",
                "episode",
                "sequence",
                "shot",
                "department",
                "subset",
                "format",
                "delivery_date",
            },
            "Sequence": {
                "project",
                "episode",
                "sequence",
                "sequence_data_type",
                "subset",
                "format",
                "delivery_date",
            },
            "Editorial": {
                "project",
                "episode",
                "sequence",
                "shot",
                "editorial_data_role",
                "format",
                "delivery_date",
            },
            "Intake": {
                "project",
                "vendor",
                "episode",
                "sequence",
                "shot",
                "department",
                "subset",
                "format",
                "delivery_date",
            },
        }
        visible = visible_by_type.get(target_type, set(self.metadata_rows))
        for key, row in self.metadata_rows.items():
            row.setVisible(key in visible)
        subset_label = self.metadata_labels.get("subset")
        if subset_label:
            is_virtual_camera = (
                target_type == "Sequence"
                and self.sequence_data_type_combo.currentText() == "virtual_camera"
            )
            subset_label.setText("Take" if is_virtual_camera else "Subset")

    def _metadata_from_form(self) -> IngestMetadata:
        target_type = self.target_type_combo.currentText()
        shot = self.shot_edit.text().strip()
        if target_type == "Editorial":
            editorial_role = self.editorial_data_role_combo.currentText().strip() or "edit_source"
            subset = f"shot_media/{shot}" if editorial_role == "shot_media" and shot else editorial_role
        else:
            subset = self._combo_text(self.subset_edit) or "main"
        department = (
            self.sequence_data_type_combo.currentText().strip()
            if target_type == "Sequence"
            else self._combo_text(self.department_edit)
        )
        if target_type == "Asset" and department == "assembly" and subset in {"", "main"}:
            subset = "client"
        return IngestMetadata(
            target_type=target_type,
            project=self.project_edit.text().strip(),
            asset=self._combo_text(self.asset_edit),
            category=self._combo_text(self.category_edit) or "character",
            group=self._combo_text(self.group_edit) or "main",
            variant=self._combo_text(self.variant_edit) or "default",
            department=department,
            subset=subset,
            format=self.format_edit.text().strip(),
            episode=self.episode_edit.text().strip() or "ep001",
            sequence=self.sequence_edit.text().strip() or "sq010",
            shot=shot,
            vendor=self.vendor_edit.text().strip(),
            delivery_date=self.delivery_date_edit.text().strip(),
            comment=self.comment_edit.toPlainText().strip(),
        )

    @staticmethod
    def _combo_text(combo: QtWidgets.QComboBox) -> str:
        return combo.currentText().strip()

    @staticmethod
    def _set_combo_value(combo: QtWidgets.QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(value)
        elif combo.count() and not value:
            combo.setCurrentIndex(0)

    def _asset_scope_changed(self) -> None:
        if self._updating_metadata_form:
            return
        self._refresh_asset_metadata_choices()

    def _asset_department_changed(self) -> None:
        if self._updating_metadata_form:
            return
        self._refresh_asset_subset_choices()
        if self._combo_text(self.department_edit) == "assembly" and self._combo_text(self.subset_edit) in {"", "main"}:
            self._set_combo_value(self.subset_edit, "client")

    def _refresh_asset_metadata_choices(self) -> None:
        self._refresh_combo_items(self.category_edit, self.service.asset_categories())
        category = self._combo_text(self.category_edit) or "character"
        self._refresh_combo_items(self.group_edit, self.service.asset_groups(category))
        group = self._combo_text(self.group_edit) or "main"
        self._refresh_combo_items(self.asset_edit, self.service.asset_names(category, group))
        asset = self._combo_text(self.asset_edit)
        self._refresh_combo_items(self.variant_edit, self.service.asset_variants(category, group, asset))

    def _refresh_asset_subset_choices(self) -> None:
        self._refresh_combo_items(self.subset_edit, self.service.asset_subsets(self._combo_text(self.department_edit)))

    def _refresh_combo_items(self, combo: QtWidgets.QComboBox, values: list[str]) -> None:
        current = combo.currentText().strip()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(values)
            if current:
                self._set_combo_value(combo, current)
            elif combo.count():
                combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(False)

    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    def _current_item(self) -> PlanItem | None:
        if self.current_row < 0 or self.current_row >= len(self.items):
            return None
        return self.items[self.current_row]

    def _sync_checkboxes_to_items(self) -> None:
        for row, item in enumerate(self.items):
            table_item = self.table.item(row, 0)
            selected = bool(table_item and table_item.checkState() == QtCore.Qt.CheckState.Checked)
            self.items[row] = replace(item, selected=selected)

    def _filter_plan_items(self, items: list[PlanItem]) -> list[PlanItem]:
        enabled = {key for key, checkbox in self.target_filter_checks.items() if checkbox.isChecked()}
        return [item for item in items if self._filter_key(item) in enabled]

    def _filter_key(self, item: PlanItem) -> str:
        if item.status == "Reject" or item.target_type == "Rejected":
            return "rejected"
        target_type = item.target_type.lower()
        file_type = item.file_type.lower()
        subset = item.metadata.subset.lower()
        if target_type == "shot" and (item.metadata.department == "audio" or file_type == "wav"):
            return "audio"
        if target_type == "editorial" and subset == "storyreel":
            return "storyreel"
        if target_type == "vendor":
            return "intake"
        if target_type in {"asset", "shot", "editorial", "intake"}:
            return target_type
        if target_type in {"design", "reference"}:
            return target_type
        return "others"

    def _update_summary(self) -> None:
        total = len(self.items)
        selected = sum(1 for item in self.items if item.selected)
        size = self._format_size(sum(item.size for item in self.items))
        self.summary_label.setText(f"Total: {total} files ({size})   Selected: {selected}")

    def _short_path(self, path: Path | None) -> str:
        if path is None:
            return "(unresolved)"
        try:
            return "..." + path.relative_to(self.service.project_root).as_posix()
        except ValueError:
            return str(path)

    def _status_color(self, status: str) -> QtGui.QBrush:
        colors = {
            "Ready": "#54b9ff",
            "Reject": "#ff5d5d",
            "Conflict": "#ffbf42",
            "Needs Metadata": "#ffbf42",
            "Missing": "#ff5d5d",
        }
        return QtGui.QBrush(QtGui.QColor(colors.get(status, "#dbe5ee")))

    def _format_size(self, size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{value:.1f} GB"

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #111a21; color: #dbe5ee; }
            QFrame#panel { background: #162029; border: 1px solid #2b3945; border-radius: 6px; }
            QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QTreeWidget, QTableWidget {
                background: #111820; border: 1px solid #314251; border-radius: 4px; padding: 4px;
                selection-background-color: #1d6fd0;
            }
            QPushButton { background: #24323d; border: 1px solid #334755; border-radius: 4px; padding: 7px 10px; }
            QPushButton:hover { background: #2c3d4a; }
            QHeaderView::section { background: #1d2832; color: #dbe5ee; padding: 5px; border: 0; }
            QTableWidget { gridline-color: #253541; }
            """
        )


def _default_config_dir() -> Path:
    config_dir = os.environ.get("PROJECT_CONFIG_DIR")
    if config_dir:
        return Path(config_dir)
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or Path(__file__).resolve().parents[4])
    return root / "config" / "STKB"


def main(argv: list[str] | None = None) -> int:
    _ = argv
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    service = SmartIngestService(ProjectConfig(_default_config_dir()))
    window = SmartIngestWindow(service)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
