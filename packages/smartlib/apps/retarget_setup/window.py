from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.path_resolver import AssetIdentity
from smartlib.core.maya_runtime import resolve_mayapy, process_environment
from .service import RetargetService


def open_result_in_current_maya(path):
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Open Asset Manager / Retarget inside the running Maya, then use Open Result. A new Maya session will not be started.") from exc
    if cmds.about(batch=True):
        raise RuntimeError("Open Result requires an interactive Maya session.")
    # Match Asset Manager OPEN SCENE, including its unsaved-change confirmation.
    from scripts.asset_manager_ui import open_scene_in_current_dcc
    open_scene_in_current_dcc(path)


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
        context.addWidget(QtWidgets.QLabel("Retarget → ANIM"))
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
        self.rig_rows = {}
        for key, label in (("mcr_scene", "MCR / intermediate rig"), ("animation_rig_scene", "ANIM / target rig")):
            container = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            edit = QtWidgets.QLineEdit()
            edit.setReadOnly(True)
            row.addWidget(edit, 1)
            self.add_button(row, "Browse", lambda _checked=False, k=key: self.browse_rig(k))
            self.rigs[key] = edit
            label_widget = QtWidgets.QLabel(label)
            form.addRow(label_widget, container)
            self.rig_rows[key] = (label_widget, container)
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
        self.pole_box = pole_box
        setup.addWidget(pole_box)
        self.add_button(setup, "Load Common Full-body FK Mappings", self.new_direct_mapping)
        self.reference_frame = QtWidgets.QDoubleSpinBox()
        self.reference_frame.setRange(-100000, 100000)
        self.reference_frame.setPrefix("Offset reference frame: ")
        self.reference_frame.setToolTip(
            "Used only for rows with Keep offset enabled. An animated frame is not "
            "a rest pose. For matching MCR / ANIM bone axes, leave Keep offset off."
        )
        self.reference_frame.valueChanged.connect(self.reference_changed)
        setup.addWidget(self.reference_frame)
        self.add_button(setup, "Add Source → Target row", self.add_mapping)
        setup.addWidget(QtWidgets.QLabel("Source → Target mappings / uncheck Use to exclude"))
        self.nodes = QtWidgets.QTreeWidget()
        self.nodes.setHeaderLabels(["MCR Source / group", "Use", "ANIM Target", "Method", "Keep offset"])
        self.nodes.setToolTip("Direct MCR mode: double-click Source, Target or Method to edit. Methods: orient = rotation, point = translation, parent = both. Keep offset uses the reference frame.")
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
        self.add_button(motion_row, "Use Common Test FBX", self.use_common_motion)
        test.addLayout(motion_row)
        self.common_motion_status = QtWidgets.QLabel()
        self.common_motion_status.setWordWrap(True)
        test.addWidget(self.common_motion_status)
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
        self.run_button = self.add_button(run, "Build Motion Test (Worker)", self.run_test)
        self.cancel_button = self.add_button(run, "Cancel", self.cancel_test)
        self.cancel_button.setEnabled(False)
        self.open_button = self.add_button(run, "Open Result in Maya", self.open_result)
        self.open_button.setEnabled(False)
        self.open_button.setToolTip("Open the result in this Maya session using OPEN SCENE.")
        test.addLayout(run)
        self.review = QtWidgets.QCheckBox("Visual review OK — I checked the ANIM motion in Maya")
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
        results_page = QtWidgets.QWidget()
        results_layout = QtWidgets.QVBoxLayout(results_page)
        self.test_results = QtWidgets.QTreeWidget()
        self.test_results.setHeaderLabels(["Test", "Data", "Motion", "Frames", "Worker", "Visual review", "Created"])
        results_layout.addWidget(self.test_results)
        result_actions = QtWidgets.QHBoxLayout()
        self.add_button(result_actions, "Refresh", self.refresh_tests)
        self.load_test_button = self.add_button(result_actions, "Load Test Result", self.load_test_result)
        self.add_button(result_actions, "Open Selected Result in Maya", self.open_selected_result)
        results_layout.addLayout(result_actions)
        self.test_results.itemDoubleClicked.connect(lambda *_: self.load_test_result())
        self.tabs.addTab(results_page, "Test Results")
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
        self.publish_hint = QtWidgets.QLabel()
        footer.addWidget(self.publish_hint)
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
            self.refresh_tests()
            self.use_common_motion()
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
        direct = profile.get("input_mode") == "mcr_to_anim"
        self.pole_box.setVisible(not direct)
        self.reference_frame.setVisible(direct)
        self.reference_frame.setValue(float(profile.get("reference_frame", 0)))
        self.rigs["mcr_scene"].setEnabled(not direct)
        for widget in self.rig_rows["mcr_scene"]:
            widget.setVisible(not direct)
        self.sync_nodes(profile)
        self.loading = False

    def new_direct_mapping(self):
        try:
            if not self.confirm_discard():
                return
            profile = self.current_profile()
            common = self.service.template_profile()
            if common.get("input_mode") != "mcr_to_anim" or not common.get("mappings"):
                raise ValueError("Project common template does not contain received-MCR FK mappings")
            for key in ("source_skeleton", "transfer_nodes", "pole_vectors", "wrists", "dual_arm_match", "limb_modes", "excluded_transfer_nodes"):
                profile.pop(key, None)
            for key in ("input_mode", "reference_frame", "mappings", "rig_settings", "template", "retarget_method"):
                if key in common:
                    profile[key] = common[key]
            self.display(profile)
            self.edited()
            self.state.setText(f"Common full-body FK mappings loaded: {len(profile['mappings'])} · Test this character before Publish")
        except Exception as exc:
            self.error(exc)

    def add_mapping(self):
        profile = self.current_profile()
        if profile.get("input_mode") != "mcr_to_anim":
            return
        profile.setdefault("mappings", []).append(dict(source="", target="", method="orient", enabled=True, maintain_offset=False))
        self.display(profile)
        self.edited()

    def reference_changed(self, value):
        if self.loading:
            return
        profile = self.current_profile()
        profile["reference_frame"] = value
        self.display(profile)
        self.edited()

    def sync_nodes(self, profile):
        self.nodes.clear()
        if profile.get("input_mode") == "mcr_to_anim":
            for index, row in enumerate(profile.get("mappings", [])):
                item = QtWidgets.QTreeWidgetItem([row.get("source", ""), "", row.get("target", ""), row.get("method", "orient"), ""])
                item.setData(0, QtCore.Qt.UserRole, index)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsUserCheckable)
                self.nodes.addTopLevelItem(item)
                item.setCheckState(1, QtCore.Qt.Checked if row.get("enabled", True) else QtCore.Qt.Unchecked)
                item.setCheckState(4, QtCore.Qt.Checked if row.get("maintain_offset", True) else QtCore.Qt.Unchecked)
            for column in (0, 2, 3):
                self.nodes.resizeColumnToContents(column)
            return
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
        if self.loading:
            return
        if self.profile.get("input_mode") == "mcr_to_anim":
            profile = self.current_profile()
            index = item.data(0, QtCore.Qt.UserRole)
            profile["mappings"][index].update(source=item.text(0), target=item.text(2), method=item.text(3),
                enabled=item.checkState(1) == QtCore.Qt.Checked, maintain_offset=item.checkState(4) == QtCore.Qt.Checked)
            self.store_node_edit(profile)
            return
        if column != 1:
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
            self.store_node_edit(profile)
        except Exception as exc:
            self.error(exc)

    def store_node_edit(self, profile):
        # Never delete the emitting tree item inside itemChanged.
        self.profile = profile
        fields = {k: v for k, v in profile.items() if k not in {"asset", "schema_version", "profile_kind", "template", "mcr_scene", "animation_rig_scene"}}
        self.editor.blockSignals(True)
        self.editor.setPlainText(json.dumps(fields, ensure_ascii=False, indent=2))
        self.editor.blockSignals(False)
        self.dirty = True
        self.data_path = None
        self.invalidate_test()
        self.state.setText("Mappings changed · Save and retest before Publish")

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
        self.update_publish()

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

    def use_common_motion(self):
        if self.process is not None or not self.service:
            return
        try:
            motion = self.service.common_test_motion()
            self.motion.setText(motion["path"])
            self.start.setValue(motion["frame_range"][0])
            self.end.setValue(motion["frame_range"][1])
            self.common_motion_status.setText("Common MCP test · " + motion["time_unit"] + " · Range from manifest")
        except (OSError, ValueError) as exc:
            self.common_motion_status.setText(f"Common test motion unavailable: {exc}")

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
        self.tabs.setTabEnabled(3, not running)
        for widget in (self.motion, self.motion_browse_button, self.start, self.end, self.run_button, self.save_data_button, self.save_draft_button):
            widget.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.review.setEnabled(not running and bool(self.job and self.job.get("status") == "passed"))
        self.update_publish()

    def run_test(self):
        if self.process is not None:
            return
        try:
            mayapy = resolve_mayapy(self.config)
            profile = self.current_profile()
            self.invalidate_test()
            if self.data_path is None:
                self.data_path = self.service.save_data(profile, self.comment.text())
                self.dirty = False
                self.refresh_history()
            self.job = self.service.prepare_test(profile, self.motion.text(), self.start.value(), self.end.value(), data_path=self.data_path)
            run = self.job["run_id"]
            repository = Path(__file__).resolve().parents[4]
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
            env.insert("PYTHONPATH", os.pathsep.join([str(repository / "packages"), str(repository), env.value("PYTHONPATH")]).rstrip(os.pathsep))
            process.setProcessEnvironment(env)
            process.readyReadStandardOutput.connect(self.read_log)
            process.finished.connect(self.test_finished)
            process.errorOccurred.connect(self.process_error)
            self.process = process
            self.log.clear()
            self.set_running(True)
            self.state.setText("Maya test running in a separate process…")
            process.start(str(mayapy), ["-m", "smartlib.apps.review_build_manager.retarget_worker",
                "--profile", str(self.service.path("test", run, "profile.json")),
                "--output", str(self.service.path("test", run, self.job["result_file"])),
                "--report", str(self.service.path("test", run, "report.json")),
                "--audit", str(self.service.path("test", run, "numeric_audit.json"))])
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
        self.refresh_tests()
        self.show_numeric_summary()

    def cancel_test(self):
        if self.process:
            self.process.kill()

    def open_job_result(self, job):
        try:
            result = self.service.path("test", job["run_id"], job.get("result_file", "result.mb"))
            if not result.is_file():
                raise FileNotFoundError(f"Test scene is missing: {result}")
            open_result_in_current_maya(result)
        except Exception as exc:
            self.error(exc)

    def open_result(self):
        if self.job:
            self.open_job_result(self.job)

    def open_selected_result(self):
        item = self.test_results.currentItem()
        if item and self.load_test_result():
            self.open_job_result(self.job)

    def refresh_tests(self):
        if not self.service:
            return
        selected = self.test_results.currentItem()
        selected_id = selected.data(0, QtCore.Qt.UserRole).get("run_id") if selected else None
        self.test_results.clear()
        for job in self.service.test_history():
            item = QtWidgets.QTreeWidgetItem([
                job.get("version") or job["run_id"], job.get("source_data", {}).get("version", "—"),
                Path(job.get("motion", {}).get("path", "")).name,
                "–".join(map(str, job.get("frame_range", []))), job.get("status", "unknown"),
                "OK" if job.get("reviewed") else "Pending",
                job.get("created_at") or job.get("completed_at") or ""])
            item.setData(0, QtCore.Qt.UserRole, job)
            self.test_results.addTopLevelItem(item)
            if job["run_id"] == selected_id:
                self.test_results.setCurrentItem(item)
        for col in range(6):
            self.test_results.resizeColumnToContents(col)

    def load_test_result(self):
        item = self.test_results.currentItem()
        if not item or self.process is not None or not self.confirm_discard():
            return
        try:
            profile, job, data_path = self.service.restore_test(item.data(0, QtCore.Qt.UserRole)["run_id"])
            self.display(profile)
            self.loading = True
            for widget in (self.motion, self.start, self.end, self.review):
                widget.blockSignals(True)
            self.motion.setText(job.get("motion", {}).get("path", ""))
            start, end = job.get("frame_range", [1, 1012])
            self.start.setValue(start)
            self.end.setValue(end)
            self.review.setChecked(bool(job.get("reviewed")))
            for widget in (self.motion, self.start, self.end, self.review):
                widget.blockSignals(False)
            self.loading = False
            self.job, self.data_path, self.dirty = job, data_path, False
            log = self.service.path("test", job["run_id"], "maya.log")
            self.log.setPlainText(log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "")
            self.show_numeric_summary()
            self.open_button.setEnabled(self.service.path("test", job["run_id"], job.get("result_file", "result.mb")).is_file())
            self.set_running(False)
            self.update_publish()
            self.state.setText(f"Loaded test {job['run_id']} · {job['status']}")
            self.tabs.setCurrentWidget(self.test_page)
            return True
        except Exception as exc:
            self.error(exc)

    def show_numeric_summary(self):
        if not self.job:
            return
        path = self.service.path("test", self.job["run_id"], "numeric_audit.json")
        if path.is_file():
            report = json.loads(path.read_text(encoding="utf-8"))
            self.log.appendPlainText("\nNumerical comparison: " + json.dumps({k:v for k,v in report.items() if k not in {"stats", "key_counts"}}, ensure_ascii=False))

    def reviewed(self, checked):
        if self.job:
            try:
                self.job = self.service.review_test(self.job, checked)
                self.update_publish()
                self.refresh_tests()
            except Exception as exc:
                self.error(exc)

    def update_publish(self):
        if self.process is not None:
            reason = "Worker is running"
        elif not self.job:
            reason = "Run a test or load a saved Test Result"
        elif self.job.get("status") != "passed":
            reason = "A successful Motion Test is required"
        elif not self.data_path:
            reason = "Save the tested settings as a Data Version"
        elif not self.job.get("reviewed"):
            reason = "Check Visual review OK in Motion Test"
        else:
            reason = ""
        self.publish_button.setEnabled(not reason)
        self.publish_button.setToolTip(reason or "Publish this reviewed test's settings")
        self.publish_hint.setText(reason or "Ready to Publish")

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
