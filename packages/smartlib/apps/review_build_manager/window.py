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
from smartlib.core.config_loader import ProjectConfig


STATE_COLORS = {
    "DIRTY": "#f2ae30",
    "READY": "#79bd69",
    "BUILDING": "#4a98e8",
    "FAILED": "#ef665d",
    "MISSING": "#ef665d",
    "UP TO DATE": "#80bd72",
}


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
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search shot")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMaximumWidth(420)
        self.scan_btn = QtWidgets.QPushButton("Refresh")
        self.build_selected_btn = QtWidgets.QPushButton("Build Selected")
        self.build_dirty_btn = QtWidgets.QPushButton("Build Dirty")
        self.build_dirty_btn.setProperty("primary", True)
        self.build_selected_btn.setEnabled(False)
        self.build_dirty_btn.setEnabled(False)
        toolbar.addWidget(project_label)
        toolbar.addWidget(self.project_combo)
        toolbar.addSpacing(12)
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addStretch(1)
        toolbar.addWidget(self.scan_btn)
        toolbar.addWidget(self.build_selected_btn)
        toolbar.addWidget(self.build_dirty_btn)
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
        self.shot_table = QtWidgets.QTableWidget(0, 7)
        self.shot_table.setHorizontalHeaderLabels(
            ["Build", "Thumbnail", "Shot", "State", "Output Version", "Last Review", "Comment"]
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
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.shot_table, 1)

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
        self.detail_thumbnail = QtWidgets.QLabel("No Thumbnail")
        self.detail_thumbnail.setAlignment(QtCore.Qt.AlignCenter)
        self.detail_thumbnail.setFixedHeight(175)
        self.detail_thumbnail.setObjectName("detailThumbnail")
        layout.addWidget(self.detail_thumbnail)
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
        self.open_output_btn = QtWidgets.QPushButton("Open Output Folder")
        self.open_output_btn.setEnabled(False)
        layout.addWidget(self.open_output_btn)
        return panel

    def _connect_signals(self) -> None:
        self.scan_btn.clicked.connect(self.scan_updates)
        self.build_selected_btn.clicked.connect(self.build_selected)
        self.build_dirty_btn.clicked.connect(self.build_dirty)
        self.search_edit.textChanged.connect(self._apply_filters)
        self.filter_list.currentItemChanged.connect(self._filter_changed)
        self.shot_tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self.shot_table.itemSelectionChanged.connect(self._table_selection_changed)
        self.shot_table.itemChanged.connect(self._shot_item_changed)
        self.open_output_btn.clicked.connect(self._open_output_folder)

    def scan_updates(self) -> None:
        self.scan_btn.setEnabled(False)
        self.footer_label.setText("Scanning shots...")
        QtWidgets.QApplication.processEvents()
        try:
            self.rows = self.service.scan()
            self._populate_filters()
            self._populate_tree()
            self._apply_filters()
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
            if row_data.state not in {"READY", "DIRTY"}:
                check.setFlags(QtCore.Qt.ItemIsEnabled)
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
            state = QtWidgets.QTableWidgetItem(row_data.state)
            state.setForeground(QtGui.QColor(STATE_COLORS.get(row_data.state, "#dddddd")))
            self.shot_table.setItem(row, 3, state)
            self.shot_table.setItem(row, 4, QtWidgets.QTableWidgetItem(row_data.output_label))
            self.shot_table.setItem(row, 5, QtWidgets.QTableWidgetItem(row_data.last_review))
            self.shot_table.setItem(row, 6, QtWidgets.QTableWidgetItem(row_data.comment))
        self.shot_table.blockSignals(False)
        self._update_build_buttons()

    def _show_details(self, row_data: ReviewShotStatus | None) -> None:
        self.output_table.setRowCount(0)
        self.detail_thumbnail.clear()
        self.open_output_btn.setEnabled(False)
        if not row_data:
            self.detail_title.setText("Output History")
            self.detail_thumbnail.setText("No Thumbnail")
            self.detail_summary.setText("Select a shot.")
            return
        identity = row_data.identity
        self.detail_title.setText(
            f"Output History - {identity.episode}/{identity.sequence}/{identity.shot}"
        )
        if row_data.thumbnail:
            pixmap = QtGui.QPixmap(row_data.thumbnail)
            if not pixmap.isNull():
                self.detail_thumbnail.setPixmap(
                    pixmap.scaled(
                        self.detail_thumbnail.size(),
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    )
                )
        if self.detail_thumbnail.pixmap() is None:
            self.detail_thumbnail.setText(identity.shot)
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

    def build_selected(self) -> None:
        identities = self._checked_identities()
        if not identities:
            selected = self._selected_status()
            if selected and selected.state in {"READY", "DIRTY"}:
                identities = [
                    (
                        selected.identity.episode,
                        selected.identity.sequence,
                        selected.identity.shot,
                    )
                ]
        self._enqueue_builds(identities)

    def build_dirty(self) -> None:
        identities = [
            (row.identity.episode, row.identity.sequence, row.identity.shot)
            for row in self.rows
            if row.state in {"READY", "DIRTY"}
        ]
        self._enqueue_builds(identities)

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
        from smartlib.apps.shot_manager import ShotIdentity

        existing = {
            tuple(job["identity"])
            for job in self.pending_jobs
        }
        if self.active_job:
            existing.add(tuple(self.active_job["identity"]))
        for raw_identity in identities:
            if tuple(raw_identity) in existing:
                continue
            identity = ShotIdentity(*raw_identity)
            status = next(
                (
                    row
                    for row in self.rows
                    if row.identity == identity and row.state in {"READY", "DIRTY"}
                ),
                None,
            )
            if not status:
                continue
            self.job_counter += 1
            output_version = self.service.next_output_version(identity)
            job_root = (
                self.service.shots.shot_root(identity)
                / "output"
                / "review"
                / "animation"
                / "_jobs"
            )
            status_file = job_root / (
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
                f"{identity.shot}_{output_version}_{self.job_counter:03d}.json"
            )
            job = {
                "id": f"#{self.job_counter:04d}",
                "identity": raw_identity,
                "version": output_version,
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
        for column, value in enumerate(
            [
                job["id"],
                f"{identity[1]}/{identity[2]}",
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
            f"{job['identity'][1]}/{job['identity'][2]}",
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
            self.queue_table.item(row, 2).setToolTip(message)

    def _update_build_buttons(self) -> None:
        busy_identities = {
            tuple(job["identity"])
            for job in self.pending_jobs
        }
        if self.active_job:
            busy_identities.add(tuple(self.active_job["identity"]))
        checked = self._checked_identities()
        selected = self._selected_status()
        selected_valid = bool(
            selected
            and selected.state in {"READY", "DIRTY"}
            and (
                selected.identity.episode,
                selected.identity.sequence,
                selected.identity.shot,
            )
            not in busy_identities
        )
        checked_valid = any(identity not in busy_identities for identity in checked)
        dirty_available = any(
            row.state in {"READY", "DIRTY"}
            and (row.identity.episode, row.identity.sequence, row.identity.shot)
            not in busy_identities
            for row in self.rows
        )
        self.build_selected_btn.setEnabled(checked_valid or selected_valid)
        self.build_dirty_btn.setEnabled(dirty_available)

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

    def closeEvent(self, event) -> None:
        settings = self._settings()
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("splitter", self.main_splitter.saveState())
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
            QLabel#detailThumbnail { background: #1d1f20; border: 1px solid #3a3d3f; }
            QLabel#detailSummary { background: #2d3032; padding: 8px; }
            QLabel#footerLabel { color: #aeb4b8; padding: 2px; }
            """
        )


_WINDOW = None


def show(config_dir: str | os.PathLike[str], parent=None) -> ReviewBuildManagerWindow:
    global _WINDOW
    if _WINDOW is not None:
        try:
            _WINDOW.close()
            _WINDOW.deleteLater()
        except Exception:
            pass
    _WINDOW = ReviewBuildManagerWindow(parent=parent, config_dir=config_dir)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW
