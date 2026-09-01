from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets

from smartlib.core.config_loader import ProjectConfig, default_config_dir
from smartlib.core.path_resolver import ProjectPaths
from smartlib.core.texture_reconnect import collect_texture_items, texture_root_from_package
from smartlib.dcc.maya.texture_reconnect import apply_reconnect_plan, ingested_package_candidates, inspect_current_file_nodes, reconnect_file_nodes


class TexturePathRepairWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir=None, parent=None):
        super().__init__(parent)
        self.config = ProjectConfig(Path(config_dir or os.environ.get("PROJECT_CONFIG_DIR") or default_config_dir()))
        self.plan = []
        self.setWindowTitle("Texture Path Repair")
        self.resize(920, 520)
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        source = QtWidgets.QHBoxLayout()
        source.addWidget(QtWidgets.QLabel("Ingested Package (optional)"))
        self.package_combo = QtWidgets.QComboBox()
        self.package_combo.setEditable(True)
        source.addWidget(self.package_combo, 1)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_packages)
        source.addWidget(refresh)
        layout.addLayout(source)
        filters = QtWidgets.QHBoxLayout()
        filters.addWidget(QtWidgets.QLabel("Filter"))
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(["All", "Resolved", "Unresolved", "Missing", "Ambiguous"])
        self.filter_combo.currentTextChanged.connect(self.apply_filter)
        filters.addWidget(self.filter_combo)
        filters.addStretch(1)
        layout.addLayout(filters)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Status", "Node", "Match", "Original Path", "Resolved Path"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.table, 1)
        controls = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("Open an Asset scene, then scan an ingested package.")
        controls.addWidget(self.status, 1)
        scan = QtWidgets.QPushButton("Scan")
        scan.clicked.connect(self.scan)
        controls.addWidget(scan)
        self.collect_button = QtWidgets.QPushButton("Collect Selected for Smart Delivery")
        self.collect_button.setEnabled(False)
        self.collect_button.clicked.connect(self.collect_selected)
        controls.addWidget(self.collect_button)
        self.apply_button = QtWidgets.QPushButton("Apply Ready")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        controls.addWidget(self.apply_button)
        layout.addLayout(controls)
        self.setCentralWidget(central)
        self.refresh_packages()

    def refresh_packages(self):
        from maya import cmds
        scene = str(cmds.file(query=True, sceneName=True) or "")
        candidates = ingested_package_candidates(self.config, scene)
        self.package_combo.clear()
        self.package_combo.addItems([path.as_posix() for path in candidates])
        self.status.setText(f"Found {len(candidates)} ingested package(s).")

    def scan(self):
        package = self.package_combo.currentText().strip()
        try:
            self.plan = reconnect_file_nodes(package, apply=False) if package else inspect_current_file_nodes()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Texture Path Repair", str(exc))
            return
        self.table.setRowCount(len(self.plan))
        for row, item in enumerate(self.plan):
            values = (item.status, item.node, item.match_method, item.source_path, item.resolved_path.as_posix() if item.resolved_path else "")
            for column, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                if column == 0:
                    cell.setToolTip(f"Candidates: {len(item.candidates)}")
                self.table.setItem(row, column, cell)
        counts = {name: sum(item.status == name for item in self.plan) for name in ("ready", "missing", "ambiguous")}
        self.status.setText(f"Ready {counts['ready']} / Missing {counts['missing']} / Ambiguous {counts['ambiguous']}")
        self.apply_button.setEnabled(bool(counts["ready"] and self.package_combo.currentText().strip()))
        self.collect_button.setEnabled(bool(self.plan))
        self.apply_filter()

    def apply_filter(self):
        selected = self.filter_combo.currentText() if hasattr(self, "filter_combo") else "All"
        for row, item in enumerate(self.plan):
            visible = (selected == "All" or selected == "Resolved" and item.status == "ready" or selected == "Unresolved" and item.status != "ready" or selected.casefold() == item.status)
            self.table.setRowHidden(row, not visible)

    def collect_selected(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        selected = [self.plan[row] for row in rows if not self.table.isRowHidden(row)]
        if not selected:
            QtWidgets.QMessageBox.warning(self, "Texture Path Repair", "Select one or more visible texture rows.")
            return
        paths = ProjectPaths(self.config.project_root, self.config.templates, self.config.project_name)
        job_id = f"TEX-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        destination = paths.delivery_staging_root() / job_id / "sourceimages"
        package_text = self.package_combo.currentText().strip()
        package_root = Path(package_text) if package_text else None
        try:
            copied, _manifest = collect_texture_items(selected, destination, texture_root=texture_root_from_package(package_root) if package_root else None)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Texture Path Repair", str(exc))
            return
        self.status.setText(f"Collected {len(copied)} file(s). Smart Delivery Texture Root: {destination.as_posix()}")

    def apply(self):
        ready = sum(item.status == "ready" for item in self.plan)
        answer = QtWidgets.QMessageBox.question(self, "Texture Path Repair", f"Reconnect {ready} file node(s)?", QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        applied = apply_reconnect_plan(self.plan)
        self.status.setText(f"Applied {applied} texture path(s). Save the scene to keep the changes.")
        self.apply_button.setEnabled(False)


_WINDOW = None


def show(config_dir=None, parent=None):
    global _WINDOW
    from smartlib.core.qt import parent_for_maya
    if _WINDOW is not None:
        _WINDOW.close()
    _WINDOW = TexturePathRepairWindow(config_dir=config_dir, parent=parent_for_maya(QtWidgets, parent))
    _WINDOW.show()
    _WINDOW.raise_()
    return _WINDOW
