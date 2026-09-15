from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.path_resolver import AssetIdentity
from smartlib.core.maya_runtime import resolve_mayapy, process_environment
from .service import RetargetService


class RetargetWindow(QtWidgets.QMainWindow):
    def __init__(self, manager, asset=None, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.config = ProjectConfig(manager.config_dir)
        self.service = None
        self.profile = {}
        self.dirty = False
        self.loading = False
        self.data_path = None
        self.job = None
        self.process = None
        self.current_index = -1
        self.setWindowTitle("Retarget Setup")
        self.resize(1050, 780)
        base = QtWidgets.QWidget()
        self.setCentralWidget(base)
        layout = QtWidgets.QVBoxLayout(base)
        context = QtWidgets.QHBoxLayout()
        context.addWidget(QtWidgets.QLabel("Character"))
        self.characters = QtWidgets.QComboBox()
        context.addWidget(self.characters, 1)
        context.addWidget(QtWidgets.QLabel("Mocap → MCR → ANM"))
        layout.addLayout(context)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.setup_page = QtWidgets.QWidget()
        setup = QtWidgets.QVBoxLayout(self.setup_page)
        actions = QtWidgets.QHBoxLayout()
        self.add_button(actions, "New from Template", self.new_profile)
        self.add_button(actions, "Import Profile", self.import_profile)
        self.add_button(actions, "Refresh Rig Dependencies", self.refresh_rigs)
        setup.addLayout(actions)
        form = QtWidgets.QFormLayout()
        self.template = QtWidgets.QLabel()
        form.addRow("Template snapshot", self.template)
        self.rigs = {}
        for key, label in (("mcr_scene", "MCR / intermediate rig"), ("animation_rig_scene", "ANM / target rig")):
            row = QtWidgets.QHBoxLayout()
            edit = QtWidgets.QLineEdit()
            edit.setReadOnly(True)
            row.addWidget(edit, 1)
            self.add_button(row, "Browse", lambda _checked=False, k=key: self.browse_rig(k))
            self.rigs[key] = edit
            form.addRow(label, row)
        setup.addLayout(form)
        setup.addWidget(QtWidgets.QLabel("Character settings are shared across clothing. Test clips are stored separately."))
        pole_box = QtWidgets.QGroupBox("Pole vectors / character adjustment")
        pole_form = QtWidgets.QFormLayout(pole_box)
        self.poles = {}
        for name in ("left_arm", "right_arm", "left_leg", "right_leg"):
            row = QtWidgets.QHBoxLayout()
            enabled = QtWidgets.QCheckBox("Enabled")
            ratio = QtWidgets.QDoubleSpinBox()
            ratio.setRange(0.01, 100)
            ratio.setDecimals(3)
            ratio.setSingleStep(0.05)
            row.addWidget(enabled)
            row.addWidget(QtWidgets.QLabel("Distance ratio"))
            row.addWidget(ratio)
            row.addStretch()
            pole_form.addRow(name.replace("_", " ").title(), row)
            self.poles[name] = (enabled, ratio)
            enabled.toggled.connect(lambda _value, n=name: self.pole_changed(n))
            ratio.valueChanged.connect(lambda _value, n=name: self.pole_changed(n))
        setup.addWidget(pole_box)
        setup.addWidget(QtWidgets.QLabel("Transfer nodes / uncheck to exclude from transfer"))
        self.nodes = QtWidgets.QTreeWidget()
        self.nodes.setHeaderLabels(["Group / node", "Transfer"])
        self.nodes.itemChanged.connect(self.node_changed)
        setup.addWidget(self.nodes, 1)
        self.advanced_toggle = QtWidgets.QCheckBox("Advanced settings (transfer nodes, wrists, rig settings)")
        setup.addWidget(self.advanced_toggle)
        self.editor = QtWidgets.QPlainTextEdit()
        self.editor.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        setup.addWidget(self.editor, 1)
        self.editor.setVisible(False)
        self.advanced_toggle.toggled.connect(self.editor.setVisible)
        self.editor.textChanged.connect(self.edited)
        self.tabs.addTab(self.setup_page, "Settings")
        self.test_page = QtWidgets.QWidget()
        test = QtWidgets.QVBoxLayout(self.test_page)
        motion_row = QtWidgets.QHBoxLayout()
        self.motion = QtWidgets.QLineEdit()
        self.motion.setPlaceholderText("Received or standard motion FBX")
        motion_row.addWidget(self.motion, 1)
        self.motion_browse_button = self.add_button(motion_row, "Browse Motion", self.browse_motion)
        test.addLayout(motion_row)
        frames = QtWidgets.QHBoxLayout()
        self.start = QtWidgets.QSpinBox()
        self.end = QtWidgets.QSpinBox()
        for spin, value in ((self.start, 1), (self.end, 1012)):
            spin.setRange(-1000000, 1000000)
            spin.setValue(value)
            spin.valueChanged.connect(self.test_input_changed)
        frames.addWidget(QtWidgets.QLabel("Start"))
        frames.addWidget(self.start)
        frames.addWidget(QtWidgets.QLabel("End"))
        frames.addWidget(self.end)
        frames.addStretch()
        test.addLayout(frames)
        run = QtWidgets.QHBoxLayout()
        self.add_button(run, "Check Settings", self.check_settings)
        self.run_button = self.add_button(run, "Run Maya Test", self.run_test)
        self.cancel_button = self.add_button(run, "Cancel", self.cancel_test)
        self.cancel_button.setEnabled(False)
        self.open_button = self.add_button(run, "Open Result in Maya", self.open_result)
        self.open_button.setEnabled(False)
        test.addLayout(run)
        self.review = QtWidgets.QCheckBox("I checked the resulting ANM motion in Maya")
        self.review.setEnabled(False)
        self.review.toggled.connect(self.reviewed)
        test.addWidget(self.review)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        test.addWidget(self.log, 1)
        self.tabs.addTab(self.test_page, "Motion Test")
        self.motion.textChanged.connect(self.test_input_changed)
        history_page = QtWidgets.QWidget()
        history = QtWidgets.QVBoxLayout(history_page)
        self.versions = QtWidgets.QTreeWidget()
        self.versions.setHeaderLabels(["Type", "Version", "Source Data", "Comment", "Storage"])
        history.addWidget(self.versions, 1)
        history_actions = QtWidgets.QHBoxLayout()
        self.add_button(history_actions, "Load as Draft", self.load_version)
        self.add_button(history_actions, "Open Folder", self.open_folder)
        history.addLayout(history_actions)
        self.tabs.addTab(history_page, "History")
        self.state = QtWidgets.QLabel("Select a character")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.comment = QtWidgets.QLineEdit()
        self.comment.setPlaceholderText("Version comment")
        layout.addWidget(self.comment)
        footer = QtWidgets.QHBoxLayout()
        self.save_draft_button = self.add_button(footer, "Save Draft", self.save_draft)
        self.save_data_button = self.add_button(footer, "Save Data Version", self.save_data)
        footer.addStretch()
        self.publish_button = self.add_button(footer, "Publish for Scene Build", self.publish)
        self.publish_button.setEnabled(False)
        layout.addLayout(footer)
        assets = [a for a in manager.list_assets() if a.category.lower() in {"ch", "cha", "character", "characters"}]
        if asset is not None and not any(a.root == asset.root for a in assets):
            assets.append(asset)
        for entry in assets:
            self.characters.addItem(f"{entry.name} / {entry.group}", entry)
        index = next((i for i, a in enumerate(assets) if asset is not None and a.root == asset.root), 0)
        self.characters.setCurrentIndex(index)
        self.characters.currentIndexChanged.connect(self.select_character)
        if assets:
            self.select_character(index)
        else:
            self.setup_page.setEnabled(False)
            self.test_page.setEnabled(False)
            self.save_draft_button.setEnabled(False)
            self.save_data_button.setEnabled(False)

    @staticmethod
    def add_button(layout, text, callback):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def error(self, exc):
        self.state.setText(str(exc))
        QtWidgets.QMessageBox.warning(self, "Retarget Setup", str(exc))

    def confirm_discard(self):
        if not self.dirty:
            return True
        answer = QtWidgets.QMessageBox.question(self, "Unsaved Draft", "Discard unsaved Retarget settings?", QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        return answer == QtWidgets.QMessageBox.Yes

    def select_character(self, index):
        if not self.confirm_discard():
            self.characters.blockSignals(True)
            self.characters.setCurrentIndex(self.current_index)
            self.characters.blockSignals(False)
            return
        asset = self.characters.itemData(index)
        if asset is None:
            return
        self.current_index = index
        self.service = RetargetService(self.manager.paths, AssetIdentity(asset.category, asset.group, asset.name), self.config)
        self.data_path = None
        self.invalidate_test()
        try:
            self.display(self.service.load_initial())
            self.dirty = False
            self.refresh_history()
            self.state.setText(f"{asset.name} · Draft · Test not run")
        except Exception as exc:
            self.display({"asset": asset.name})
            self.error(exc)

    def display(self, profile):
        self.loading = True
        self.profile = profile
        self.template.setText(json.dumps(profile.get("template", {}), ensure_ascii=False))
        for key, edit in self.rigs.items():
            edit.setText(profile.get(key, ""))
        fields = {k: v for k, v in profile.items() if k not in {"asset", "schema_version", "profile_kind", "template", "mcr_scene", "animation_rig_scene"}}
        self.editor.setPlainText(json.dumps(fields, ensure_ascii=False, indent=2))
        for name, (enabled, ratio) in self.poles.items():
            definition = profile.get("pole_vectors", {}).get(name)
            enabled.setEnabled(definition is not None)
            ratio.setEnabled(definition is not None)
            enabled.setChecked(bool(definition is not None and definition.get("enabled", True)))
            ratio.setValue((definition or {}).get("distance_ratio", 1.0))
        self.sync_nodes(profile)
        self.loading = False

    def sync_nodes(self, profile):
        self.nodes.clear()
        excluded = set(profile.get("excluded_transfer_nodes", []))
        for group, names in profile.get("transfer_nodes", {}).items():
            parent = QtWidgets.QTreeWidgetItem([group, ""])
            self.nodes.addTopLevelItem(parent)
            for name in names:
                item = QtWidgets.QTreeWidgetItem([name, ""])
                item.setData(0, QtCore.Qt.UserRole, name)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                parent.addChild(item)
                item.setCheckState(1, QtCore.Qt.Unchecked if name in excluded else QtCore.Qt.Checked)
            parent.setExpanded(group == "controls")
        self.nodes.resizeColumnToContents(0)

    def node_changed(self, item, column):
        if self.loading or column != 1:
            return
        name = item.data(0, QtCore.Qt.UserRole)
        if not name:
            return
        try:
            profile = self.current_profile()
            excluded = set(profile.get("excluded_transfer_nodes", []))
            if item.checkState(1) == QtCore.Qt.Checked:
                excluded.discard(name)
            else:
                excluded.add(name)
            profile["excluded_transfer_nodes"] = sorted(excluded)
            self.display(profile)
            self.edited()
        except Exception as exc:
            self.error(exc)

    def pole_changed(self, name):
        if self.loading:
            return
        try:
            profile = self.current_profile()
            definition = profile.get("pole_vectors", {}).get(name)
            if definition is None:
                return
            enabled, ratio = self.poles[name]
            definition.update(enabled=enabled.isChecked(), distance_ratio=ratio.value())
            self.display(profile)
            self.edited()
        except Exception as exc:
            self.error(exc)

    def current_profile(self):
        fields = json.loads(self.editor.toPlainText())
        if not isinstance(fields, dict):
            raise ValueError("Transfer settings must be a JSON object")
        return self.service.clean({**fields, "asset": self.service.identity.name, "template": self.profile.get("template", {}), **{key: edit.text() for key, edit in self.rigs.items()}})

    def edited(self):
        if self.loading:
            return
        # Keep the basic controls in sync when advanced JSON is edited.
        try:
            fields = json.loads(self.editor.toPlainText())
            self.loading = True
            for name, (enabled, ratio) in self.poles.items():
                definition = fields.get("pole_vectors", {}).get(name)
                enabled.setEnabled(definition is not None)
                ratio.setEnabled(definition is not None)
                enabled.setChecked(bool(definition is not None and definition.get("enabled", True)))
                ratio.setValue((definition or {}).get("distance_ratio", 1.0))
            self.sync_nodes(fields)
        except (ValueError, AttributeError, TypeError):
            pass
        finally:
            self.loading = False
        self.dirty = True
        self.data_path = None
        self.invalidate_test()
        self.state.setText("Draft changed · Save and retest before Publish")

    def invalidate_test(self):
        self.job = None
        self.review.blockSignals(True)
        self.review.setChecked(False)
        self.review.blockSignals(False)
        self.review.setEnabled(False)
        self.open_button.setEnabled(False)
        self.publish_button.setEnabled(False)

    def test_input_changed(self, *args):
        if not self.loading:
            self.invalidate_test()
            self.state.setText("Test input changed · Run Maya Test")

    def new_profile(self):
        if self.confirm_discard():
            try:
                self.display(self.service.new_profile())
                self.edited()
            except Exception as exc:
                self.error(exc)

    def import_profile(self):
        if not self.confirm_discard():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import Retarget Profile", "", "JSON (*.json)")
        if path:
            try:
                self.display(self.service.import_profile(path))
                self.edited()
            except Exception as exc:
                self.error(exc)

    def browse_rig(self, key):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Rig", self.rigs[key].text(), "Maya scenes (*.ma *.mb)")
        if path:
            self.rigs[key].setText(path)
            self.edited()

    def refresh_rigs(self):
        try:
            for key, path in self.service.resolve_rigs().items():
                self.rigs[key].setText(path)
            self.edited()
        except Exception as exc:
            self.error(exc)

    def browse_motion(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Test Motion", str(self.service.paths.retarget_library("test_motion")), "Motion capture (*.fbx)")
        if path:
            self.motion.setText(path)

    def check_settings(self):
        try:
            errors = self.service.validate(self.current_profile())
            self.state.setText("\n".join(errors) if errors else "Settings OK · Maya motion test is separate")
        except Exception as exc:
            self.error(exc)

    def save_draft(self):
        try:
            path = self.service.save_draft(self.current_profile())
            self.dirty = False
            self.state.setText(f"Draft saved: {path}")
        except Exception as exc:
            self.error(exc)

    def save_data(self):
        try:
            profile = self.current_profile()
            self.data_path = self.service.save_data(profile, self.comment.text())
            self.service.save_draft(profile)
            self.dirty = False
            self.refresh_history()
            self.update_publish()
            self.state.setText(f"Saved {self.data_path.parent.name}")
        except Exception as exc:
            self.error(exc)

    def refresh_history(self):
        self.versions.clear()
        for area in ("data", "publish"):
            for entry in self.service.history(area):
                source = entry["manifest"].get("source_data") or {}
                item = QtWidgets.QTreeWidgetItem([area.title(), entry["version"], source.get("version", ""), entry["manifest"].get("comment", ""), "Legacy (read only)" if entry["legacy"] else "Character"])
                item.setData(0, QtCore.Qt.UserRole, entry)
                self.versions.addTopLevelItem(item)
        for column in range(3):
            self.versions.resizeColumnToContents(column)

    def load_version(self):
        item = self.versions.currentItem()
        if item and self.confirm_discard():
            try:
                self.display(self.service.import_profile(item.data(0, QtCore.Qt.UserRole)["profile"]))
                self.edited()
                self.tabs.setCurrentIndex(0)
            except Exception as exc:
                self.error(exc)

    def open_folder(self):
        item = self.versions.currentItem()
        if item:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(item.data(0, QtCore.Qt.UserRole)["profile"].parent)))

    def set_running(self, running):
        self.characters.setEnabled(not running)
        self.setup_page.setEnabled(not running)
        self.versions.setEnabled(not running)
        self.tabs.setTabEnabled(2, not running)
        for widget in (self.motion, self.motion_browse_button, self.start, self.end, self.run_button, self.save_data_button, self.save_draft_button):
            widget.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.review.setEnabled(not running and bool(self.job and self.job.get("status") == "passed"))

    def run_test(self):
        if self.process is not None:
            return
        try:
            mayapy = resolve_mayapy(self.config)
            profile = self.current_profile()
            self.invalidate_test()
            self.job = self.service.prepare_test(profile, self.motion.text(), self.start.value(), self.end.value())
            run = self.job["run_id"]
            script = Path(__file__).resolve().parents[4] / "tools" / "maya" / "bake_mocap_to_rig.py"
            process = QtCore.QProcess(self)
            process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            env = QtCore.QProcessEnvironment.systemEnvironment()
            env.remove("PYTHONHOME")
            env.remove("PYTHONPATH")
            values, paths = process_environment(self.config)
            for key, value in values.items():
                env.insert(key, value)
            for key, entries in paths.items():
                env.insert(key, os.pathsep.join([*entries, env.value(key)]).rstrip(os.pathsep))
            process.setProcessEnvironment(env)
            process.readyReadStandardOutput.connect(self.read_log)
            process.finished.connect(self.test_finished)
            process.errorOccurred.connect(self.process_error)
            self.process = process
            self.log.clear()
            self.set_running(True)
            self.state.setText("Maya test running in a separate process…")
            process.start(str(mayapy), [str(script), str(self.service.path("test", run, "profile.json")), str(self.service.path("test", run, "result.mb")), "--report", str(self.service.path("test", run, "report.json"))])
        except Exception as exc:
            self.set_running(False)
            self.error(exc)

    def read_log(self):
        if self.process:
            text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self.log.moveCursor(QtGui.QTextCursor.End)
            self.log.insertPlainText(text)

    def process_error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self.log.appendPlainText(self.process.errorString())
            self.test_finished(-1)

    def test_finished(self, code, *args):
        if self.process is None:
            return
        self.read_log()
        process = self.process
        self.process = None
        process.deleteLater()
        try:
            self.job = self.service.complete_test(self.job, code)
            self.service.path("test", self.job["run_id"], "maya.log").write_text(self.log.toPlainText(), encoding="utf-8")
            passed = self.job["status"] == "passed"
            report = self.job.get("report", {})
            self.log.appendPlainText("\nTest summary: " + json.dumps({key: len(report.get(key, [])) for key in ("locked_plugs", "missing_plugs", "failed_plugs")}))
            self.open_button.setEnabled(passed)
            self.state.setText("Maya test completed · Open Result and check the motion" if passed else "Maya test failed or cancelled · See log / report")
        except Exception as exc:
            self.invalidate_test()
            self.error(exc)
        self.set_running(False)

    def cancel_test(self):
        if self.process:
            self.process.kill()

    def open_result(self):
        try:
            maya = resolve_mayapy(self.config).with_name("maya.exe")
            subprocess.Popen([str(maya), "-file", str(self.service.path("test", self.job["run_id"], "result.mb"))])
        except Exception as exc:
            self.error(exc)

    def reviewed(self, checked):
        if self.job:
            try:
                self.job = self.service.review_test(self.job, checked)
                self.update_publish()
            except Exception as exc:
                self.error(exc)

    def update_publish(self):
        self.publish_button.setEnabled(bool(self.data_path and self.job and self.job.get("status") == "passed" and self.job.get("reviewed")))

    def publish(self):
        try:
            if self.service.fingerprint(self.current_profile()) != self.job["fingerprint"]:
                raise ValueError("Current settings changed; rerun the test")
            path = self.service.publish(self.data_path, self.job, self.comment.text())
            self.refresh_history()
            self.publish_button.setEnabled(False)
            self.state.setText(f"Published for Scene Build: {path}")
        except Exception as exc:
            self.error(exc)

    def closeEvent(self, event):
        if self.process is not None:
            self.state.setText("Cancel the running Maya test before closing")
            event.ignore()
        elif self.confirm_discard():
            event.accept()
        else:
            event.ignore()
