from __future__ import annotations

import json
import os
import subprocess
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
    root = os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or str(Path(__file__).resolve().parents[1])
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
    from smartlib.apps.viewer import ViewerService
    from smartlib.core.config_loader import ProjectConfig

    return ViewerService(ProjectConfig(config_dir or _default_config_dir()))


class ViewerWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir: str | os.PathLike[str] | None = None):
        super().__init__()
        self.service = _service(config_dir)
        self.packages = []
        self.setWindowTitle(f"Viewer - {self.service.project_config.project_name}")
        self.resize(980, 620)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        toolbar = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.open_rv_btn = QtWidgets.QPushButton("Open Package in RV")
        self.open_layer_btn = QtWidgets.QPushButton("Open Layer in RV")
        self.show_hud_btn = QtWidgets.QPushButton("Show HUD Data")
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.open_rv_btn)
        toolbar.addWidget(self.open_layer_btn)
        toolbar.addWidget(self.show_hud_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        splitter = QtWidgets.QSplitter()
        layout.addWidget(splitter, 1)

        self.review_table = QtWidgets.QTableWidget(0, 7)
        self.review_table.setHorizontalHeaderLabels(["Episode", "Sequence", "Shot", "Dept", "Version", "Frames", "review.json"])
        self.review_table.horizontalHeader().setStretchLastSection(True)
        self.review_table.verticalHeader().setVisible(False)
        self.review_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.review_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        splitter.addWidget(self.review_table)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)
        self.layer_table = QtWidgets.QTableWidget(0, 7)
        self.layer_table.setHorizontalHeaderLabels(["Layer", "Take", "Output", "Frames", "First", "Last", "AE Slot"])
        self.layer_table.horizontalHeader().setStretchLastSection(True)
        self.layer_table.verticalHeader().setVisible(False)
        self.layer_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.layer_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.hud_view = QtWidgets.QPlainTextEdit()
        self.hud_view.setReadOnly(True)
        right_layout.addWidget(self.layer_table, 2)
        right_layout.addWidget(self.hud_view, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        self.status_label = QtWidgets.QLabel("")
        layout.addWidget(self.status_label)

        self.refresh_btn.clicked.connect(self.refresh)
        self.open_rv_btn.clicked.connect(self.open_package_in_rv)
        self.open_layer_btn.clicked.connect(self.open_layer_in_rv)
        self.show_hud_btn.clicked.connect(self.show_hud)
        self.review_table.currentCellChanged.connect(lambda *_args: self.populate_layers())
        self.review_table.itemDoubleClicked.connect(lambda _item: self.open_package_in_rv())
        self.layer_table.itemDoubleClicked.connect(lambda _item: self.open_layer_in_rv())

    def refresh(self) -> None:
        self.packages = self.service.list_review_packages()
        self.review_table.setRowCount(0)
        for package in self.packages:
            row = self.review_table.rowCount()
            self.review_table.insertRow(row)
            values = [
                package.episode,
                package.sequence,
                package.shot,
                package.department,
                package.version,
                f"{package.frame_range[0]}-{package.frame_range[1]}" if len(package.frame_range) >= 2 else "",
                package.review_json,
            ]
            for column, value in enumerate(values):
                self.review_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
        self.review_table.resizeColumnsToContents()
        if self.review_table.rowCount():
            self.review_table.setCurrentCell(0, 0)
        self.status_label.setText(f"{len(self.packages)} review packages")

    def current_package(self):
        row = self.review_table.currentRow()
        if row < 0 or row >= len(self.packages):
            return None
        return self.packages[row]

    def current_layer_name(self) -> str:
        row = self.layer_table.currentRow()
        item = self.layer_table.item(row, 0) if row >= 0 else None
        return item.text() if item else ""

    def populate_layers(self) -> None:
        package = self.current_package()
        self.layer_table.setRowCount(0)
        self.hud_view.clear()
        if not package:
            return
        for layer in package.layers:
            row = self.layer_table.rowCount()
            self.layer_table.insertRow(row)
            values = [
                layer.layer,
                layer.take,
                layer.output,
                layer.file_count,
                layer.first_file,
                layer.last_file,
                layer.ae_slot,
            ]
            for column, value in enumerate(values):
                self.layer_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
        self.layer_table.resizeColumnsToContents()
        if self.layer_table.rowCount():
            self.layer_table.setCurrentCell(0, 0)
        self.show_hud()

    def open_package_in_rv(self) -> None:
        package = self.current_package()
        if package:
            self._launch_rv(self.service.rv_args_for_package(package))

    def open_layer_in_rv(self) -> None:
        package = self.current_package()
        layer_name = self.current_layer_name()
        if package and layer_name:
            self._launch_rv(self.service.rv_args_for_layer(package, layer_name))

    def show_hud(self) -> None:
        package = self.current_package()
        if not package:
            return
        self.hud_view.setPlainText(json.dumps(self.service.hud_data(package), indent=2, ensure_ascii=False))

    def _launch_rv(self, args: list[str]) -> None:
        if not args:
            self.status_label.setText("No sequence files were found for RV.")
            return
        rv = self.service.rv_executable()
        if not rv:
            QtWidgets.QMessageBox.warning(
                self,
                "OpenRV Not Found",
                "Set tools.openrv.path in config/STKB/tools.yml or set OPENRV_PATH.",
            )
            return
        subprocess.Popen([str(rv), *args])
        self.status_label.setText(f"Launched RV: {len(args)} source(s)")


_WINDOW = None


def show(config_dir: str | os.PathLike[str] | None = None):
    global _WINDOW
    try:
        _WINDOW.close()
    except Exception:
        pass
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _WINDOW = ViewerWindow(config_dir=config_dir)
    _WINDOW.show()
    return _WINDOW


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    show()
    sys.exit(app.exec())
