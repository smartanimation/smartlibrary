"""Asset Manager's project-configured Studio Delivery tab."""
from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.apps.asset_manager.studio_delivery import StudioDeliveryService, read
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.maya_runtime import process_environment, resolve_mayapy


class StudioDeliveryTab(QtWidgets.QWidget):
    def __init__(self, config_dir, parent=None):
        super().__init__(parent)
        self.service = StudioDeliveryService(ProjectConfig(config_dir))
        self.identity = None
        self.process = None
        self.active_job = None
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.context = QtWidgets.QComboBox()
        self.context.addItem("Studio Delivery / スタジオ提供用", "studio_delivery")
        self.source = QtWidgets.QComboBox()
        self.preset = QtWidgets.QLabel()
        self.preset.setWordWrap(True)
        form.addRow("Context", self.context)
        form.addRow("Source publish", self.source)
        form.addRow("FBX preset", self.preset)
        layout.addLayout(form)
        actions = QtWidgets.QHBoxLayout()
        self.generate = QtWidgets.QPushButton("Generate && Validate FBX")
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.cancel = QtWidgets.QPushButton("Cancel Export")
        self.cancel.setEnabled(False)
        for widget in (self.generate, self.refresh_button, self.cancel):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self.jobs = QtWidgets.QComboBox()
        form2 = QtWidgets.QFormLayout()
        form2.addRow("Validated FBX", self.jobs)
        self.comment = QtWidgets.QLineEdit()
        form2.addRow("Release comment", self.comment)
        layout.addLayout(form2)
        self.finalize_button = QtWidgets.QPushButton("Finalize Delivery Version")
        layout.addWidget(self.finalize_button)
        self.releases = QtWidgets.QTreeWidget()
        self.releases.setHeaderLabels(["Delivery", "Source", "Variant", "Created", "Comment"])
        self.releases.setRootIsDecorated(False)
        layout.addWidget(self.releases, 1)
        row = QtWidgets.QHBoxLayout()
        self.open_button = QtWidgets.QPushButton("Open Folder")
        self.copy_button = QtWidgets.QPushButton("Copy FBX Path")
        self.sent_button = QtWidgets.QPushButton("Record Sent…")
        for button in (self.open_button, self.copy_button, self.sent_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.events = QtWidgets.QTreeWidget()
        self.events.setHeaderLabels(["Event", "Delivery", "Studio", "Take", "Recorded at", "Note"])
        self.events.setRootIsDecorated(False)
        self.events.setMaximumHeight(140)
        layout.addWidget(self.events)
        receipt_actions = QtWidgets.QHBoxLayout()
        self.receive_button = QtWidgets.QPushButton("Register Received FBX…")
        self.copy_received_button = QtWidgets.QPushButton("Copy Received FBX Path")
        receipt_actions.addWidget(self.receive_button)
        receipt_actions.addWidget(self.copy_received_button)
        layout.addLayout(receipt_actions)
        self.status = QtWidgets.QLabel("Select an asset.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        layout.addWidget(self.log)
        self.generate.clicked.connect(self.generate_fbx)
        self.refresh_button.clicked.connect(self.refresh)
        self.cancel.clicked.connect(self.cancel_export)
        self.finalize_button.clicked.connect(self.finalize)
        self.open_button.clicked.connect(self.open_folder)
        self.copy_button.clicked.connect(self.copy_path)
        self.sent_button.clicked.connect(self.record_sent)
        self.receive_button.clicked.connect(self.record_received)
        self.copy_received_button.clicked.connect(self.copy_received)
        self.events.itemSelectionChanged.connect(self.update_actions)
        self.jobs.currentIndexChanged.connect(self.update_actions)
        self.source.currentIndexChanged.connect(self.update_actions)
        self.releases.itemSelectionChanged.connect(self.update_actions)
        self.update_actions()

    def set_identity(self, identity):
        if self.identity == identity:
            return
        self.identity = identity
        self.comment.clear()
        self.refresh()

    def update_actions(self, *args):
        idle = self.process is None
        self.generate.setEnabled(idle and self.source.currentData() is not None)
        self.finalize_button.setEnabled(idle and self.jobs.currentData() is not None)
        self.source.setEnabled(idle)
        self.context.setEnabled(idle)
        self.cancel.setEnabled(not idle)
        event = self.events.currentItem()
        self.copy_received_button.setEnabled(event is not None and event.data(0, QtCore.Qt.UserRole).get("kind") == "received")
        for button in (self.open_button, self.copy_button, self.sent_button, self.receive_button):
            button.setEnabled(self.releases.currentItem() is not None)

    def refresh(self):
        previous = self.source.currentData()
        self.source.clear()
        self.jobs.clear()
        self.releases.clear()
        self.events.clear()
        self.preset.clear()
        if self.identity is None:
            self.status.setText("Select an asset.")
            self.update_actions()
            return
        try:
            # History remains accessible even after the project disables new exports.
            released_runs = set()
            for release in self.service.releases(self.identity):
                released_runs.add(release["run_id"])
                item = QtWidgets.QTreeWidgetItem([release["version"],
                    f'{release["source"]["context"]} {release["source"]["version"]}',
                    release["identity"]["variant"], release["created_at"], release.get("comment", "")])
                item.setData(0, QtCore.Qt.UserRole, release)
                item.setToolTip(0, json.dumps(release, ensure_ascii=False, indent=2))
                self.releases.addTopLevelItem(item)
            for event in self.service.events(self.identity):
                item = QtWidgets.QTreeWidgetItem([event["kind"], event["version"], event["studio"],
                    event.get("take", ""), event["recorded_at"], event.get("note", "")])
                item.setData(0, QtCore.Qt.UserRole, event)
                self.events.addTopLevelItem(item)
            for column in range(4):
                self.releases.resizeColumnToContents(column)
            for column in range(5):
                self.events.resizeColumnToContents(column)
            settings = self.service.settings(self.identity)
            self.context.setItemText(0, settings["label"])
            self.preset.setText(f'{settings["recipe"]["source_context"]} → FBX · {settings["fbx"]["file_version"]} · '
                f'{settings["fbx"]["units"]} · {settings["fbx"]["up_axis"]}-up · Context {settings["context_version"]}')
            self.preset.setToolTip(json.dumps(settings, ensure_ascii=False, indent=2))
            for source in self.service.sources(self.identity):
                self.source.addItem(source["version"], source["version"])
                self.source.setItemData(self.source.count()-1, source["path"], QtCore.Qt.ToolTipRole)
            index = self.source.findData(previous)
            if index >= 0:
                self.source.setCurrentIndex(index)
            root = self.service.paths.studio_delivery_path(self.identity, "jobs")
            for folder in sorted(root.iterdir(), key=lambda p: p.name) if root.exists() else []:
                job_path = self.service.paths.artifact_file(folder, "job.json")
                report_path = self.service.paths.artifact_file(folder, "validation.json")
                if job_path.is_file() and report_path.is_file():
                    job, report = read(job_path), read(report_path)
                    if (job["run_id"] not in released_runs and report.get("status") == "passed"
                            and job["identity"]["variant"] == self.identity.variant):
                        self.jobs.addItem(f'{job["source"]["version"]} · {job["created_at"]}', job["run_id"])
            self.status.setText("Select a source version, generate and validate, then finalize a delivery version."
                                if self.source.count() else "No packed Maya source versions found for the configured Context.")
        except Exception as exc:
            self.status.setText(str(exc))
        self.update_actions()

    def error(self, exc):
        self.status.setText(str(exc))
        QtWidgets.QMessageBox.warning(self, "Studio Delivery", str(exc))

    def generate_fbx(self):
        if self.process is not None or self.identity is None:
            return
        try:
            mayapy = resolve_mayapy(self.service.config)
            values, paths = process_environment(self.service.config)
            job = self.service.prepare(self.identity, self.source.currentData())
            self.active_job = (self.identity, read(job)["run_id"])
            process = QtCore.QProcess(self)
            process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            env = QtCore.QProcessEnvironment.systemEnvironment()
            env.remove("PYTHONHOME")
            env.remove("PYTHONPATH")
            for key, value in values.items():
                env.insert(key, value)
            for key, entries in paths.items():
                env.insert(key, os.pathsep.join([*entries, env.value(key)]).rstrip(os.pathsep))
            process.setProcessEnvironment(env)
            process.readyReadStandardOutput.connect(self.read_log)
            process.finished.connect(self.finished)
            process.errorOccurred.connect(self.process_error)
            self.process = process
            self.log.clear()
            self.update_actions()
            self.status.setText(f"Generating {self.identity.name} in an isolated Maya process…")
            script = Path(__file__).resolve().parents[4] / "tools" / "maya" / "export_studio_delivery.py"
            process.start(str(mayapy), [str(script), str(job)])
        except Exception as exc:
            self.error(exc)

    def read_log(self):
        if self.process is not None:
            self.log.moveCursor(QtGui.QTextCursor.End)
            self.log.insertPlainText(bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace"))

    def process_error(self, error):
        if error == QtCore.QProcess.FailedToStart and self.process is not None:
            self.log.appendPlainText(self.process.errorString())
            self.finished(-1)

    def finished(self, code, *args):
        if self.process is None:
            return
        self.read_log()
        process, self.process = self.process, None
        process.deleteLater()
        identity, run = self.active_job
        self.active_job = None
        message = f"{identity.name}: export failed or cancelled. See log."
        if code == 0:
            try:
                self.service.validate_job(identity, run)
                message = f"{identity.name}: FBX validation passed. Select it to finalize a delivery version."
            except Exception as exc:
                message = str(exc)
        self.refresh()
        self.status.setText(message)

    def cancel_export(self):
        if self.process is not None:
            self.process.kill()

    def finalize(self):
        try:
            release = self.service.finalize(self.identity, self.jobs.currentData(), self.comment.text())
            self.refresh()
            self.status.setText(f'Finalized {release["version"]}: {release["fbx"]}')
        except Exception as exc:
            self.error(exc)

    def selected_release(self):
        item = self.releases.currentItem()
        if item is None:
            raise ValueError("Select a delivery version.")
        return self.service.release(self.identity, item.data(0, QtCore.Qt.UserRole)["version"])

    def open_folder(self):
        try:
            release = self.selected_release()
            folder = self.service.paths.studio_delivery_path(self.identity, "publish", release["version"])
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))
        except Exception as exc:
            self.error(exc)

    def copy_path(self):
        try:
            QtWidgets.QApplication.clipboard().setText(self.selected_release()["fbx"])
        except Exception as exc:
            self.error(exc)

    def record_sent(self):
        try:
            release = self.selected_release()
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle(f'Record Sent — {release["version"]}')
            form = QtWidgets.QFormLayout(dialog)
            studio, note = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
            form.addRow("Studio", studio)
            form.addRow("Note / actual sent date", note)
            form.addRow(QtWidgets.QLabel("Records a completed handoff. This does not send files."))
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            form.addRow(buttons)
            execute = getattr(dialog, "exec", None) or dialog.exec_
            if execute() == QtWidgets.QDialog.Accepted:
                self.service.record_sent(self.identity, release["version"], studio.text(), note.text())
                self.refresh()
        except Exception as exc:
            self.error(exc)

    def record_received(self):
        try:
            release = self.selected_release()
            identity = self.identity
            source, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Received Motion", "", "FBX (*.fbx)")
            if not source:
                return
            studio, ok = QtWidgets.QInputDialog.getText(self, "Received Motion", "Studio")
            if not ok:
                return
            take, ok = QtWidgets.QInputDialog.getText(self, "Received Motion", "Take / clip name")
            if not ok:
                return
            event = self.service.record_received(identity, release["version"], studio, source, take)
            self.refresh()
            self.status.setText(f'Received: {event["path"]} — use this file in Dependencies / inputs or Retarget Setup.')
        except Exception as exc:
            self.error(exc)

    def copy_received(self):
        from smartlib.apps.asset_manager.studio_delivery import digest
        try:
            item = self.events.currentItem()
            if item is None:
                return
            event = item.data(0, QtCore.Qt.UserRole)
            if event.get("kind") != "received":
                return
            if digest(event["path"]) != event["sha256"]:
                raise ValueError("Received FBX has changed since registration.")
            QtWidgets.QApplication.clipboard().setText(event["path"])
        except Exception as exc:
            self.error(exc)
