from __future__ import annotations

import sys
from pathlib import Path

from smartlib.apps.sequence_cast_publisher.service import SequenceCastPublisherService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.qt import parent_for_maya


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt()
_WINDOW = None


class SequenceCastPublisherWindow(QtWidgets.QMainWindow):
    def __init__(self, config_dir, parent=None):
        super().__init__(parent)
        self.service = SequenceCastPublisherService(ProjectConfig(config_dir))
        self.sequence_identity = None
        self.initial_candidates = []
        self.analyses = []
        self.selections = {}
        self.published = set()
        self.sample_count = 9
        self._changing_candidates = False
        self.setWindowTitle("Sequence Cast Publisher")
        self.resize(980, 720)
        self.setMinimumSize(760, 520)
        self._build_ui()
        self._load_current_scene()

    def _build_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Sequence File"))
        self.scene_path = QtWidgets.QLineEdit()
        self.scene_path.setReadOnly(True)
        top.addWidget(self.scene_path, 1)
        self.analyze_button = QtWidgets.QPushButton("Analyze Cameras")
        self.analyze_button.setDefault(True)
        top.addWidget(self.analyze_button)
        outer.addLayout(top)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)

        shot_panel = QtWidgets.QWidget()
        shot_layout = QtWidgets.QVBoxLayout(shot_panel)
        shot_layout.setContentsMargins(0, 0, 0, 0)
        shot_head = QtWidgets.QHBoxLayout()
        shot_head.addWidget(QtWidgets.QLabel("Shots"))
        shot_head.addStretch(1)
        self.reviewed_label = QtWidgets.QLabel("0 / 0 reviewed")
        shot_head.addWidget(self.reviewed_label)
        shot_layout.addLayout(shot_head)
        self.shot_list = QtWidgets.QListWidget()
        self.shot_list.setIconSize(QtCore.QSize(112, 63))
        self.shot_list.setStyleSheet("QListWidget::item { min-height: 72px; }")
        shot_layout.addWidget(self.shot_list, 1)
        refresh_row = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("Refresh Shot List")
        self.settings_button = QtWidgets.QToolButton()
        self.settings_button.setText("Settings")
        self.settings_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        settings_menu = QtWidgets.QMenu(self.settings_button)
        for count in (3, 5, 9, 17):
            action = settings_menu.addAction(f"Sample {count} frames")
            action.setCheckable(True)
            action.setChecked(count == self.sample_count)
            action.triggered.connect(lambda checked=False, value=count: self._set_sample_count(value))
        self.settings_button.setMenu(settings_menu)
        refresh_row.addWidget(self.refresh_button, 1)
        refresh_row.addWidget(self.settings_button)
        shot_layout.addLayout(refresh_row)
        splitter.addWidget(shot_panel)

        cast_panel = QtWidgets.QWidget()
        cast_layout = QtWidgets.QVBoxLayout(cast_panel)
        cast_layout.setContentsMargins(0, 0, 0, 0)
        cast_layout.addWidget(QtWidgets.QLabel("Cast Candidates"))
        search_row = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search assets...")
        search_row.addWidget(self.search_edit, 1)
        cast_layout.addLayout(search_row)
        self.cast_table = QtWidgets.QTableWidget(0, 4)
        self.cast_table.setHorizontalHeaderLabels(["Include", "Asset Name", "Namespace", "Evidence"])
        self.cast_table.verticalHeader().setVisible(False)
        self.cast_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.cast_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.cast_table.horizontalHeader().setStretchLastSection(True)
        self.cast_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.cast_table.setColumnWidth(0, 64)
        self.cast_table.setColumnWidth(2, 145)
        cast_layout.addWidget(self.cast_table, 1)
        cast_actions = QtWidgets.QHBoxLayout()
        self.suggestions_button = QtWidgets.QPushButton("Use Suggestions")
        self.clear_button = QtWidgets.QPushButton("Clear")
        cast_actions.addWidget(self.suggestions_button)
        cast_actions.addStretch(1)
        cast_actions.addWidget(self.clear_button)
        cast_layout.addLayout(cast_actions)
        splitter.addWidget(cast_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        publish_top = QtWidgets.QHBoxLayout()
        self.shot_summary = QtWidgets.QLabel("Select Analyze Cameras")
        publish_top.addWidget(self.shot_summary, 1)
        self.publish_button = QtWidgets.QPushButton("Publish Shot Cast")
        self.publish_button.setEnabled(False)
        publish_top.addWidget(self.publish_button)
        outer.addLayout(publish_top)
        publish_bottom = QtWidgets.QHBoxLayout()
        publish_bottom.addWidget(QtWidgets.QLabel("Comment"))
        self.comment_edit = QtWidgets.QLineEdit("Camera visibility review")
        publish_bottom.addWidget(self.comment_edit, 1)
        self.auto_advance = QtWidgets.QCheckBox("Auto advance to next shot")
        self.auto_advance.setChecked(True)
        publish_bottom.addWidget(self.auto_advance)
        outer.addLayout(publish_bottom)
        self.status_label = QtWidgets.QLabel()
        outer.addWidget(self.status_label)

        self.analyze_button.clicked.connect(self.analyze)
        self.refresh_button.clicked.connect(self.refresh_shots)
        self.shot_list.currentItemChanged.connect(self._shot_changed)
        self.cast_table.itemChanged.connect(lambda _item: self._candidate_changed())
        self.search_edit.textChanged.connect(self._filter_candidates)
        self.suggestions_button.clicked.connect(self.use_suggestions)
        self.clear_button.clicked.connect(self.clear_candidates)
        self.publish_button.clicked.connect(self.publish_current)

    def _load_current_scene(self):
        try:
            import maya.cmds as cmds
            self.scene_path.setText(cmds.file(query=True, sceneName=True) or "")
            self.sequence_identity = self.service.identity_from_scene()
            self.initial_candidates = self.service.candidates(self.sequence_identity)
            self.refresh_shots()
            self._populate_initial_candidates()
        except Exception:
            self.status_label.setText("Open this tool inside Maya.")
            self.analyze_button.setEnabled(False)

    def _ensure_scene_open(self):
        import maya.cmds as cmds
        current_text = cmds.file(query=True, sceneName=True) or ""
        if not current_text:
            raise RuntimeError("Open a saved Sequence scene first.")
        current = Path(current_text)
        if not current.is_file() or current.suffix.lower() not in {".ma", ".mb"}:
            raise RuntimeError("The open Maya scene is not a valid Sequence file.")
        self.scene_path.setText(str(current))

    def refresh_shots(self):
        self.shot_list.clear()
        try:
            from smartlib.dcc.maya.smart_shot import list_sequencer_shots
            for shot in list_sequencer_shots():
                item = QtWidgets.QListWidgetItem(f"{shot.shot}\n{shot.start}–{shot.end}\nNot analyzed")
                item.setData(QtCore.Qt.UserRole, shot.shot)
                self.shot_list.addItem(item)
            if self.shot_list.count() and self.shot_list.currentRow() < 0:
                self.shot_list.setCurrentRow(0)
            self.reviewed_label.setText(f"{len(self.published)} / {self.shot_list.count()} reviewed")
        except Exception as exc:
            self.status_label.setText(str(exc))

    def _populate_initial_candidates(self):
        """Show Sequence Cast immediately without running camera visibility analysis."""
        current = self.shot_list.currentItem()
        shot_name = str(current.data(QtCore.Qt.UserRole) or "") if current else ""
        default_keys = {candidate.cast_key for candidate in self.initial_candidates}
        selected = self.selections.setdefault(shot_name, default_keys) if shot_name else default_keys
        self._changing_candidates = True
        self.cast_table.setRowCount(0)
        for candidate in self.initial_candidates:
            row = self.cast_table.rowCount()
            self.cast_table.insertRow(row)
            include = QtWidgets.QTableWidgetItem()
            include.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            include.setCheckState(
                QtCore.Qt.Checked if candidate.cast_key in selected else QtCore.Qt.Unchecked
            )
            include.setData(QtCore.Qt.UserRole, candidate.cast_key)
            self.cast_table.setItem(row, 0, include)
            self.cast_table.setItem(row, 1, QtWidgets.QTableWidgetItem(candidate.asset))
            self.cast_table.setItem(row, 2, QtWidgets.QTableWidgetItem(candidate.namespace))
            self.cast_table.setItem(row, 3, QtWidgets.QTableWidgetItem("NOT ANALYZED"))
        self._changing_candidates = False
        self._filter_candidates(self.search_edit.text())
        self._update_summary()

    def analyze(self):
        try:
            self._ensure_scene_open()
            self.sequence_identity = self.service.identity_from_scene()
            candidates = self.initial_candidates or self.service.candidates(self.sequence_identity)
            if not candidates:
                raise RuntimeError("Sequence Cast has no candidates.")
            from smartlib.apps.sequence_cast_publisher.maya_analysis import analyze_sequence
            self.analyze_button.setEnabled(False)
            self.status_label.setText("Analyzing Camera Sequencer shots...")
            QtWidgets.QApplication.processEvents()
            current = self.shot_list.currentItem()
            if current:
                self._remember_selection(str(current.data(QtCore.Qt.UserRole) or ""))
            self.analyses = analyze_sequence(candidates, sample_count=self.sample_count)
            for row in self.analyses:
                self.selections.setdefault(
                    row.shot,
                    {item.candidate.cast_key for item in row.candidates if item.included},
                )
            self._populate_shots()
            self.status_label.setText(
                f"Analyzed {len(self.analyses)} shots and {len(candidates)} referenced assets."
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Sequence Cast Publisher", str(exc))
            self.status_label.setText(str(exc))
        finally:
            self.analyze_button.setEnabled(True)

    def _populate_shots(self):
        self.shot_list.blockSignals(True)
        self.shot_list.clear()
        for analysis in self.analyses:
            status = "Ready" if analysis.shot in self.published else "Review"
            item = QtWidgets.QListWidgetItem(f"{analysis.shot}\n{analysis.start}–{analysis.end}\n{status}")
            item.setData(QtCore.Qt.UserRole, analysis.shot)
            identity = self.service.shot_identity(self.sequence_identity, analysis.shot)
            thumbnail = self.service.thumbnail_path(identity)
            if thumbnail:
                item.setIcon(QtGui.QIcon(str(thumbnail)))
            elif analysis.thumbnail:
                item.setIcon(QtGui.QIcon(analysis.thumbnail))
            self.shot_list.addItem(item)
        self.shot_list.blockSignals(False)
        if self.shot_list.count():
            self.shot_list.setCurrentRow(0)
        self.reviewed_label.setText(f"{len(self.published)} / {len(self.analyses)} reviewed")

    def _analysis(self, shot_name):
        return next((row for row in self.analyses if row.shot == shot_name), None)

    def _shot_changed(self, current, previous):
        if previous:
            self._remember_selection(str(previous.data(QtCore.Qt.UserRole) or ""))
        shot_name = str(current.data(QtCore.Qt.UserRole) or "") if current else ""
        analysis = self._analysis(shot_name)
        if not analysis and not self.analyses:
            self._populate_initial_candidates()
            return
        self._changing_candidates = True
        self.cast_table.setRowCount(0)
        if analysis:
            selected = self.selections.get(shot_name, set())
            for row_data in analysis.candidates:
                row = self.cast_table.rowCount()
                self.cast_table.insertRow(row)
                include = QtWidgets.QTableWidgetItem()
                include.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
                include.setCheckState(QtCore.Qt.Checked if row_data.candidate.cast_key in selected else QtCore.Qt.Unchecked)
                include.setData(QtCore.Qt.UserRole, row_data.candidate.cast_key)
                self.cast_table.setItem(row, 0, include)
                self.cast_table.setItem(row, 1, QtWidgets.QTableWidgetItem(row_data.candidate.asset))
                self.cast_table.setItem(row, 2, QtWidgets.QTableWidgetItem(row_data.candidate.namespace))
                evidence = QtWidgets.QTableWidgetItem(row_data.evidence)
                if row_data.evidence == "IN CAMERA":
                    evidence.setForeground(QtGui.QColor("#73d987"))
                elif row_data.evidence.startswith("EDGE"):
                    evidence.setForeground(QtGui.QColor("#e0aa45"))
                elif row_data.evidence == "REQUIRED SET":
                    evidence.setForeground(QtGui.QColor("#78aef8"))
                self.cast_table.setItem(row, 3, evidence)
        self._changing_candidates = False
        self._filter_candidates(self.search_edit.text())
        self._update_summary()

    def _remember_selection(self, shot_name):
        if shot_name:
            self.selections[shot_name] = set(self._selected_keys())

    def _selected_keys(self):
        return [
            str(self.cast_table.item(row, 0).data(QtCore.Qt.UserRole))
            for row in range(self.cast_table.rowCount())
            if self.cast_table.item(row, 0).checkState() == QtCore.Qt.Checked
        ]

    def _candidate_changed(self):
        if self._changing_candidates:
            return
        current = self.shot_list.currentItem()
        if current:
            self.selections[str(current.data(QtCore.Qt.UserRole))] = set(self._selected_keys())
        self._update_summary()

    def _filter_candidates(self, text):
        needle = str(text or "").strip().lower()
        for row in range(self.cast_table.rowCount()):
            values = " ".join(self.cast_table.item(row, column).text() for column in (1, 2, 3)).lower()
            self.cast_table.setRowHidden(row, bool(needle and needle not in values))

    def use_suggestions(self):
        current = self.shot_list.currentItem()
        analysis = self._analysis(str(current.data(QtCore.Qt.UserRole))) if current else None
        if not analysis:
            return
        suggested = {row.candidate.cast_key for row in analysis.candidates if row.included}
        self._set_checked(suggested)

    def clear_candidates(self):
        self._set_checked(set())

    def _set_checked(self, keys):
        self._changing_candidates = True
        for row in range(self.cast_table.rowCount()):
            item = self.cast_table.item(row, 0)
            item.setCheckState(QtCore.Qt.Checked if item.data(QtCore.Qt.UserRole) in keys else QtCore.Qt.Unchecked)
        self._changing_candidates = False
        self._candidate_changed()

    def _update_summary(self):
        current = self.shot_list.currentItem()
        shot_name = str(current.data(QtCore.Qt.UserRole)) if current else ""
        if not shot_name or not self.sequence_identity:
            self.shot_summary.setText("Select a Shot")
            self.publish_button.setEnabled(False)
            return
        identity = self.service.shot_identity(self.sequence_identity, shot_name)
        current_version = self.service.current_publish_version(identity) or "new"
        next_version = self.service.next_publish_version(identity)
        count = len(self._selected_keys())
        self.shot_summary.setText(f"{shot_name}   ·   {count} assets selected   ·   Cast {current_version} → {next_version}")
        self.publish_button.setEnabled(count > 0)

    def publish_current(self):
        current = self.shot_list.currentItem()
        shot_name = str(current.data(QtCore.Qt.UserRole)) if current else ""
        if not shot_name or not self.sequence_identity:
            return
        identity = self.service.shot_identity(self.sequence_identity, shot_name)
        try:
            path = self.service.publish(identity, self._selected_keys(), comment=self.comment_edit.text().strip())
            self.published.add(shot_name)
            current.setText(current.text().rsplit("\n", 1)[0] + "\nReady")
            self.reviewed_label.setText(f"{len(self.published)} / {self.shot_list.count()} reviewed")
            self.status_label.setText(f"Published: {path}")
            self._update_summary()
            if self.auto_advance.isChecked() and self.shot_list.currentRow() + 1 < self.shot_list.count():
                self.shot_list.setCurrentRow(self.shot_list.currentRow() + 1)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Publish Shot Cast", str(exc))

    def _set_sample_count(self, count):
        self.sample_count = int(count)
        for action in self.settings_button.menu().actions():
            action.setChecked(action.text() == f"Sample {count} frames")


def show(config_dir=None):
    global _WINDOW
    if _WINDOW is not None:
        try:
            _WINDOW.close()
            _WINDOW.deleteLater()
        except RuntimeError:
            pass
    config_dir = config_dir or Path.cwd() / "config" / "STKB"
    _WINDOW = SequenceCastPublisherWindow(config_dir, parent=parent_for_maya(QtWidgets))
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW


def main(argv=None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    show()
    return app.exec() if hasattr(app, "exec") else app.exec_()
