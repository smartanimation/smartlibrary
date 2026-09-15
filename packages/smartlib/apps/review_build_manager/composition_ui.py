"""Composition Snapshot controls in Review Build Manager."""
from pathlib import Path
import os
import json

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.apps.shot_manager.animation_publish import AnimationCompositionService
from smartlib.core.metadata import read_json


class CompositionSnapshotMixin:
    def _composition_service(self):
        return AnimationCompositionService(self.service.shots)

    def _composition_identity(self):
        if self.scope_combo.currentText() != "Shot":
            return None
        status = self._selected_status()
        return status.identity if status else None

    def _setup_composition_tab(self):
        page = QtWidgets.QWidget()
        self.composition_tab = page
        self.workflow_tabs.addTab(page, "Composition Snapshot")
        layout = QtWidgets.QVBoxLayout(page)
        self.composition_profile_label = QtWidgets.QLabel()
        layout.addWidget(self.composition_profile_label)
        layout.addWidget(QtWidgets.QLabel("Construct Scenes — submitted for review"))
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.composition_source_splitter = split
        layout.addWidget(split, 1)
        source_page = QtWidgets.QWidget()
        source_page.setMaximumWidth(480)
        source_layout = QtWidgets.QVBoxLayout(source_page)
        source_layout.setContentsMargins(0, 0, 8, 0)
        split.addWidget(source_page)
        self.composition_details_tabs = QtWidgets.QTabWidget()
        split.addWidget(self.composition_details_tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([360, 800])
        published_page = QtWidgets.QWidget()
        published_layout = QtWidgets.QVBoxLayout(published_page)
        self.composition_details_tabs.addTab(published_page, "Published Snapshot")
        self.review_sources_table = QtWidgets.QTableWidget(0, 3)
        self.review_sources_table.setHorizontalHeaderLabels(
            ["Build", "Construct Scene", "State"])
        self.review_sources_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.review_sources_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.review_sources_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.review_sources_table.horizontalHeader().setStretchLastSection(True)
        self.review_sources_table.itemSelectionChanged.connect(self._review_source_selected)
        self.review_sources_table.setMinimumWidth(280)
        self.review_sources_table.setWordWrap(True)
        self.review_sources_table.verticalHeader().setVisible(False)
        self.review_sources_table.horizontalHeader().setStretchLastSection(False)
        self.review_sources_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        source_layout.addWidget(self.review_sources_table, 1)
        source_refresh = QtWidgets.QPushButton("Refresh Constructs")
        source_refresh.clicked.connect(self._refresh_compositions)
        source_layout.addWidget(source_refresh)
        self.review_source_state = QtWidgets.QLabel()
        self.review_source_state.setWordWrap(True)
        self.review_source_state.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        source_layout.addWidget(self.review_source_state)
        choices = QtWidgets.QHBoxLayout()
        self.composition_department = QtWidgets.QComboBox()
        self.composition_department.addItems(["animation", "effects", "lighting"])
        self.composition_versions = QtWidgets.QComboBox()
        choices.addWidget(self.composition_department)
        choices.addWidget(self.composition_versions, 1)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_compositions)
        choices.addWidget(refresh)
        published_layout.addLayout(choices)
        self.composition_state = QtWidgets.QLabel()
        self.composition_state.setWordWrap(True)
        published_layout.addWidget(self.composition_state)
        self.composition_table = QtWidgets.QTableWidget(0, 7)
        self.composition_table.setHorizontalHeaderLabels(
            ["Use", "Type", "Name", "Category", "Context", "Version", "State"]
        )
        self.composition_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.composition_table.setAlternatingRowColors(True)
        self.composition_table.horizontalHeader().setStretchLastSection(True)
        published_layout.addWidget(self.composition_table, 1)
        from . import composition_draft_ui as editor
        self.composition_table.itemChanged.connect(lambda item: editor.changed(self, item.row()) if item.column() == 0 else None)
        actions = QtWidgets.QHBoxLayout()
        self.composition_save_btn = QtWidgets.QPushButton("Publish Snapshot Revision")
        self.composition_save_btn.clicked.connect(lambda: editor.publish(self))
        actions.addWidget(self.composition_save_btn)
        self.composition_build_btn = QtWidgets.QPushButton("Build & Publish Animation")
        self.composition_adopt_btn = QtWidgets.QPushButton("Adopt Upstream Snapshot")
        self.composition_look_btn = QtWidgets.QPushButton("Adopt Look Publish")
        self.composition_open_btn = QtWidgets.QPushButton("Open Shot")
        self.composition_review_btn = QtWidgets.QPushButton("Create Internal Review")
        def review_selected():
            from .composition_review import enqueue
            path = self.composition_versions.currentData()
            if path:
                try:
                    enqueue(self, path)
                except Exception as exc:
                    QtWidgets.QMessageBox.critical(self, "Snapshot Review", str(exc))
        self.composition_review_btn.clicked.connect(review_selected)
        actions.addWidget(self.composition_review_btn)
        for button, callback in (
            (self.composition_build_btn, self._build_and_publish_animation),
            (self.composition_adopt_btn, self._adopt_composition),
            (self.composition_look_btn, self._adopt_composition_look),
            (self.composition_open_btn, self._open_composition),
        ):
            actions.addWidget(button)
            button.clicked.connect(callback)
        layout.addLayout(actions)
        self.composition_department.currentTextChanged.connect(self._refresh_compositions)
        self.composition_versions.currentIndexChanged.connect(self._show_composition)
        self._refresh_compositions()

    def _refresh_compositions(self, *_args):
        if not hasattr(self, "composition_versions"):
            return
        selected = self.composition_versions.currentData()
        self.composition_versions.blockSignals(True)
        self.composition_versions.clear()
        self.composition_versions.addItem("New from selected Construct", "")
        identity = self._composition_identity()
        try:
            profile = self.service.project_config.pipeline_profile
            self.composition_profile_label.setText(
                f"Project Profile: {profile.name} / v{profile.version}" if profile
                else "Choose a Project Profile in Config Creator to publish animation."
            )
            department = self.composition_department.currentText()
            if identity:
                for path, data in self._composition_service().list_snapshots(identity, department):
                    self.composition_versions.addItem(
                        f"{data['version']} — {data.get('comment', '')}", str(path)
                    )
            index = self.composition_versions.findData(selected)
            if index >= 0:
                self.composition_versions.setCurrentIndex(index)
            enabled = bool(identity and profile)
            self._refresh_review_sources(identity)
            self.composition_adopt_btn.setEnabled(bool(identity) and department != "animation")
            self.composition_look_btn.setEnabled(bool(identity) and department != "animation")
        except Exception as exc:
            self.composition_state.setText(str(exc))
        finally:
            self.composition_versions.blockSignals(False)
        self._show_composition()

    def _show_composition(self, *_args):
        from .composition_draft_ui import render
        render(self)

    def _selected_review_source(self):
        row = self.review_sources_table.currentRow()
        item = self.review_sources_table.item(row, 0) if row >= 0 else None
        return item.data(QtCore.Qt.UserRole) if item else None

    def _refresh_review_sources(self, identity):
        previous = self._selected_review_source()
        table = self.review_sources_table
        table.blockSignals(True)
        table.setRowCount(0)
        rows = self.service.list_review_animation_sources(
            identity, department=self.department_combo.currentText(), task=self.task_combo.currentText()
        ) if identity else []
        table.setRowCount(len(rows))
        selected = -1
        for i, row in enumerate(rows):
            receipt = f"Review {row['review_version']} / {row['delivery_profile']} / {row['task']}"
            description = f"{Path(row['scene']).name}\n{receipt}\n{row['updated']}"
            for j, value in enumerate((row["build_version"], description, row["state"])):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(row["scene"] + "\n" + receipt + "\n" + row.get("reason", ""))
                if j == 0:
                    item.setData(QtCore.Qt.UserRole, row)
                table.setItem(i, j, item)
            if previous and previous["source_manifest"] == row["source_manifest"]:
                selected = i
        table.clearSelection()
        table.setCurrentCell(-1, -1)
        if selected >= 0:
            table.selectRow(selected)
        for column in (0, 2):
            table.resizeColumnToContents(column)
        table.resizeRowsToContents()
        table.blockSignals(False)
        self._review_source_selected()

    def _review_source_selected(self):
        row = self._selected_review_source()
        enabled = bool(row and row["state"] == "READY" and self.service.project_config.pipeline_profile
                       and self.composition_department.currentText() == "animation"
                       and not any(job.get("kind") == "animation_publish" and job.get("state") not in {"COMPLETE", "FAILED"}
                                   for job in getattr(self, "queue_jobs", [])))
        self.composition_build_btn.setEnabled(enabled)
        message = "Select a submitted Construct scene from the list."
        if row:
            message = row["reason"] or row["scene"]
            if not row["checksum_recorded"]:
                message += " | Legacy submission: original scene checksum unavailable; current scene will be pinned."
        self.review_source_state.setText(message)
        source_key = row["source_manifest"] if row else None
        if source_key != getattr(self, "_last_draft_source", None):
            self._last_draft_source = source_key
            self.composition_versions.setCurrentIndex(0)
        self._show_composition()

    def _publish_composition_path(self, identity, path):
        try:
            published = self._composition_service().publish_build(identity, path)
            if identity == self._composition_identity():
                self.composition_department.setCurrentText("animation")
                self._refresh_compositions()
                self.workflow_tabs.setCurrentWidget(self.composition_tab)
                index = self.composition_versions.findData(str(published))
                self.composition_versions.setCurrentIndex(index)
            self.footer_label.setText(f"Published Animation Composition: {published}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Animation Publish Failed", str(exc))

    def _build_and_publish_animation(self, *_args):
        identity = self._composition_identity()
        if not identity:
            return
        if any(job.get("kind") == "animation_publish" and job.get("state") not in {"COMPLETE", "FAILED"}
               for job in getattr(self, "queue_jobs", [])):
            return
        row = self._selected_review_source()
        if not row:
            return
        try:
            selection = self.service.resolve_review_animation_source(
                identity, row["source_manifest"], department=self.department_combo.currentText(),
                task=self.task_combo.currentText())
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Animation Source Unavailable", str(exc))
            return
        from .composition_draft_ui import source_payload
        try:
            selection["animation_draft"] = source_payload(self)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid Snapshot Draft", str(exc))
            return
        source = selection["scene"]
        service = self._composition_service()
        job_root = self.service.shots.paths.animation_build_dir(identity.episode, identity.sequence, identity.shot)
        _version, job_dir = service._reserve(job_root)
        result_path = self.service.shots.paths.artifact_file(job_dir, "worker_result.json")
        selection_path = self.service.shots.paths.artifact_file(job_dir, "review_source.json")
        selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
        self.job_counter += 1
        job = {
            "kind": "animation_publish", "id": f"#{self.job_counter:04d}",
            "identity": (identity.episode, identity.sequence, identity.shot), "scope": "shot",
            "version": _version, "mode": "ANIMATION PUBLISH", "department": "anim", "task_name": "animation",
            "state": "QUEUED", "task": "Animation Build & Publish", "progress": 0,
            "elapsed": QtCore.QElapsedTimer(), "row": self.queue_table.rowCount(), "stderr": "",
            "status_file": str(self.service.shots.paths.artifact_file(job_dir, "worker_status.json")),
            "result_file": str(result_path), "selection_file": str(selection_path), "source_scene": source,
            "log_file": str(self.service.shots.paths.artifact_file(job_dir, "worker.log")),
        }
        self.pending_jobs.append(job)
        self.queue_jobs.append(job)
        self._append_queue_row(job)
        self._review_source_selected()
        self._start_next_job()

    def _start_animation_job(self, job):
        try:
            mayapy = self.service.resolve_mayapy()
            process = QtCore.QProcess(self)
            environment = QtCore.QProcessEnvironment.systemEnvironment()
            env_vars, path_vars = self.service.maya_process_environment()
            for key, value in env_vars.items():
                environment.insert(key, os.path.expandvars(value))
            for key, values in path_vars.items():
                resolved = [os.path.expandvars(value) for value in values if value]
                if environment.value(key):
                    resolved.append(environment.value(key))
                environment.insert(key, os.pathsep.join(resolved))
            environment.insert("PROJECT_CONFIG_DIR", str(self.service.project_config.config_dir))
            package_root = str(Path(__file__).resolve().parents[3])
            environment.insert("PYTHONPATH", package_root + os.pathsep + environment.value("PYTHONPATH"))
            process.setProcessEnvironment(environment)
            process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            process.setStandardOutputFile(job["log_file"])
            process.setProgram(str(mayapy))
            ep, seq, shot = job["identity"]
            process.setArguments([
                "-m", "smartlib.dcc.maya.animation_build",
                "--config", str(self.service.project_config.config_dir),
                "--episode", ep, "--sequence", seq, "--shot", shot,
                "--source", job["source_scene"], "--result", job["result_file"],
                "--review-source", job["selection_file"], "--status", job["status_file"],
            ])
            process.started.connect(self._worker_started)
            process.errorOccurred.connect(self._worker_process_error)
            process.finished.connect(self._worker_finished)
            self.worker_process = process
            job["launch_details"] = self._worker_launch_details(process)
            self.job_timer.start()
            process.start()
        except Exception as exc:
            job["message"] = str(exc)
            self._finish_active_job(False)

    def _complete_animation_job(self, job, success):
        try:
            result = read_json(job["result_file"], {})
            if not success or not result.get("ok"):
                raise ValueError(result.get("error") or job.get("message") or "Animation worker produced no valid result")
            from smartlib.apps.shot_manager import ShotIdentity
            job["task"] = "Publish Animation Composition"
            self._update_queue_row(job)
            published = self._composition_service().publish_build(ShotIdentity(*job["identity"]), result["manifest"])
            job["message"] = str(published)
            from .composition_review import enqueue
            try:
                enqueue(self, published)
            except Exception as exc:
                job["message"] += "\nPublished successfully, but Review could not be queued: " + str(exc)
                self.footer_label.setText(job["message"])
            return True
        except Exception as exc:
            job["message"] = str(exc)
            return False

    def _adopt_composition(self, *_args):
        identity = self._composition_identity()
        if not identity:
            return
        department = self.composition_department.currentText()
        upstream_departments = ["animation"] if department == "effects" else ["animation", "effects"]
        options = {}
        service = self._composition_service()
        for upstream in upstream_departments:
            for path, data in service.list_snapshots(identity, upstream):
                options[f"{upstream} {data['version']} — {data.get('comment', '')}"] = path
        if not options:
            self.composition_state.setText("Publish an upstream composition first.")
            return
        selected, ok = QtWidgets.QInputDialog.getItem(self, "Adopt Snapshot", "Fixed upstream version", list(options), 0, False)
        if not ok:
            return
        try:
            path = service.adopt(identity, options[selected], department=department)
            self._refresh_compositions()
            self.composition_versions.setCurrentIndex(self.composition_versions.findData(str(path)))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Adoption Failed", str(exc))

    def _adopt_composition_look(self, *_args):
        current = self.composition_versions.currentData()
        if not current:
            return
        try:
            service = self._composition_service()
            data = service.load(current)
            targets = [m["instance_id"] for m in data["members"]]
            target, ok = QtWidgets.QInputDialog.getItem(self, "Adopt Look", "Instance", targets, 0, False)
            if not ok:
                return
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Look Publish Manifest", "", "Look Manifest (*.json)")
            if not path:
                return
            look = read_json(path, {})
            look["path"] = str(self.service.shots.paths.manifest_source(path, look["path"]))
            looks = dict(data.get("looks", {}))
            looks[target] = look
            published = service.revise_looks(self._composition_identity(), current, looks)
            self._refresh_compositions()
            self.composition_versions.setCurrentIndex(self.composition_versions.findData(str(published)))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Look Adoption Failed", str(exc))

    def _open_composition(self, *_args):
        path = self.composition_versions.currentData()
        if path:
            try:
                data = self._composition_service().load(path, identity=self._composition_identity())
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(data["entrypoint"]["path"]))
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "Open Shot Failed", str(exc))
