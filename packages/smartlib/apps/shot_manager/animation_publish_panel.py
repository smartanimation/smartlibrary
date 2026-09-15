"""Current Maya scene -> versioned Curve Data -> isolated USD rebuild."""
from pathlib import Path

from smartlib.apps.shot_manager.usd_handoff_ui import UsdPublishDialog, QtCore, QtWidgets
from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
from smartlib.core.metadata import read_json


def version_label(path):
    return next((p for p in reversed(Path(path).parts) if p.startswith('v') and p[1:].isdigit()), Path(path).name)


class AnimationPublishPanel(QtWidgets.QWidget):
    """Reuse the existing asynchronous worker, but render directly in the tab."""
    compose = UsdPublishDialog.compose
    accept_result = UsdPublishDialog.accept_result
    data_published = QtCore.Signal(str)

    def __init__(self, shots, parent=None, *, is_maya_session=False):
        super().__init__(parent)
        self.service = UsdHandoffService(shots)
        self.is_maya_session = is_maya_session
        self.identity = None
        self.target = ''
        self.process = None
        self._key = None
        self._pending_context = None
        self._rigs = {}
        self._drafts = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        self.context_label = QtWidgets.QLabel('Select a cast member')
        layout.addWidget(self.context_label)

        self.source_group = QtWidgets.QGroupBox('Source Versions')
        grid = QtWidgets.QGridLayout(self.source_group)
        self.curve_source = QtWidgets.QLabel('Current Maya Scene → Data (new Version)')
        self.curve_source.setWordWrap(True)
        grid.addWidget(QtWidgets.QLabel('Animation Curves'), 0, 0)
        grid.addWidget(self.curve_source, 0, 1, 1, 2)
        self.rig_context = QtWidgets.QComboBox()
        self.rig_version = QtWidgets.QComboBox()
        self.sculpt_subset = QtWidgets.QComboBox()
        self.sculpt_version = QtWidgets.QComboBox()
        self.sculpt_subset.addItem('main')
        self.sculpt_subset.setEnabled(False)
        for index, (label, subset, version) in enumerate([
            ('Rig (Asset Context)', self.rig_context, self.rig_version),
            ('Shot Sculpt', self.sculpt_subset, self.sculpt_version),
        ], 1):
            grid.addWidget(QtWidgets.QLabel(label), index, 0)
            grid.addWidget(subset, index, 1)
            grid.addWidget(version, index, 2)
            subset.setMinimumContentsLength(10)
            version.setMinimumContentsLength(10)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        self.source_hint = QtWidgets.QLabel()
        self.source_hint.setWordWrap(True)
        grid.addWidget(self.source_hint, 3, 0, 1, 3)
        layout.addWidget(self.source_group)

        self.output_group = QtWidgets.QGroupBox('Output Settings')
        output = QtWidgets.QGridLayout(self.output_group)
        output.addWidget(QtWidgets.QLabel('Output Format'), 0, 0)
        self.usd_check = QtWidgets.QCheckBox('USD')
        self.usd_check.setChecked(True)
        self.usd_check.setEnabled(False)
        self.abc_check = QtWidgets.QCheckBox('Alembic (Geometry Cache)')
        for widget, tip in [
            (self.usd_check, 'This Data rebuild publishes final-deform USD.'),
            (self.abc_check, 'Not yet supported by the Data-driven USD worker.'),
        ]:
            widget.setToolTip(tip)
            if widget is not self.usd_check:
                widget.setEnabled(False)
        for row, widget in enumerate([self.usd_check, self.abc_check], 1):
            output.addWidget(widget, row, 0)
        self.range_mode = QtWidgets.QComboBox()
        self.range_mode.addItems(['Shot Range', 'Custom Range'])
        output.addWidget(self.range_mode, 0, 1, 1, 3)
        self.start_frame, self.end_frame, self.step = (QtWidgets.QSpinBox() for _ in range(3))
        for col, (label, widget) in enumerate([('Start', self.start_frame), ('End', self.end_frame), ('Step', self.step)], 1):
            output.addWidget(QtWidgets.QLabel(label), 1, col)
            widget.setRange(-1000000, 1000000)
            output.addWidget(widget, 2, col)
        self.step.setRange(1, 1)
        self.step.setValue(1)
        self.step.setEnabled(False)
        self.step.setToolTip('The current validation contract samples every integer frame.')
        output.addWidget(QtWidgets.QLabel('Options'), 4, 0, 1, 4)
        self.strip_namespaces = QtWidgets.QCheckBox('Strip Namespaces')
        self.strip_namespaces.setEnabled(False)
        self.strip_namespaces.setToolTip('Namespaces are retained to preserve Data and Sculpt prim mappings.')
        output.addWidget(self.strip_namespaces, 5, 0, 1, 4)
        self.output_hint = QtWidgets.QLabel('Animation Curves are always published to Data first. USD is rebuilt from that new Version.')
        self.output_hint.setWordWrap(True)
        output.addWidget(self.output_hint, 6, 0, 1, 4)
        layout.addWidget(self.output_group)
        layout.addStretch(1)
        self.status = QtWidgets.QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(90)
        self.status.setPlaceholderText('Publish status')
        layout.addWidget(self.status)
        actions = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton('Refresh Sources')
        self.refresh_btn.clicked.connect(lambda: self.set_context(self.identity, self.target, force=True))
        actions.addWidget(self.refresh_btn)
        self.compose_btn = QtWidgets.QPushButton('Compose Existing…')
        self.compose_btn.clicked.connect(self.compose)
        actions.addWidget(self.compose_btn)
        actions.addStretch(1)
        self.publish_btn = QtWidgets.QPushButton('Publish Animation USD')
        self.publish_btn.setEnabled(False)
        self.publish_btn.clicked.connect(self.publish)
        actions.addWidget(self.publish_btn)
        layout.addLayout(actions)
        self.rig_context.currentIndexChanged.connect(self._populate_rig_versions)
        self.rig_version.currentIndexChanged.connect(self._update_ready)
        self.range_mode.currentIndexChanged.connect(self._range_changed)
        self._range_changed()

    def _running(self):
        return self.process is not None and self.process.state() != QtCore.QProcess.NotRunning

    def _remember(self):
        if self._key:
            self._drafts[self._key] = (self.rig_context.currentText(), self.rig_version.currentData(),
                self.sculpt_version.currentData(), self.range_mode.currentIndex(), self.start_frame.value(), self.end_frame.value())

    def set_context(self, identity, target, *, force=False):
        key = (identity.episode, identity.sequence, identity.shot, target) if identity else None
        if self._running():
            self._pending_context = (identity, target)
            return
        if key == self._key and not force:
            return
        self._remember()
        self.identity, self.target, self._key = identity, target, key
        self.status.clear()
        self.compose_btn.setEnabled(identity is not None)
        self.context_label.setText(' / '.join(key) if key else 'Select a shot and cast member')
        self._rigs = {}
        if identity and target:
            try:
                self._rigs = self.service.animation_rig_versions(identity, target)
            except Exception as exc:
                self.status.setPlainText(str(exc))
        self.rig_context.blockSignals(True)
        self.rig_context.clear()
        self.rig_context.addItems(sorted(self._rigs, key=lambda name: (name.lower() != 'anim', name)))
        self.rig_context.blockSignals(False)
        self._populate_rig_versions()
        self._populate_sculpt_versions()
        self.range_mode.setCurrentIndex(0)
        self._range_changed()
        draft = self._drafts.get(key)
        if draft:
            self.rig_context.setCurrentText(draft[0])
            for widget, value in zip((self.rig_version, self.sculpt_version), draft[1:3]):
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            self.range_mode.setCurrentIndex(draft[3])
            if draft[3]:
                self.start_frame.setValue(draft[4])
                self.end_frame.setValue(draft[5])
        self._update_ready()

    def _populate_rig_versions(self):
        self.rig_version.clear()
        for row in self._rigs.get(self.rig_context.currentText(), []):
            self.rig_version.addItem(row['version'], row['path'])
            self.rig_version.setItemData(self.rig_version.count()-1, row['path'], QtCore.Qt.ToolTipRole)
        self.source_hint.setText('Select the rebuild Rig context/version. Curve Data is captured from the open Maya scene.'
            if self.is_maya_session else 'Publishing current-scene Animation Data is available inside Maya only.')
        self._update_ready()

    def _populate_sculpt_versions(self):
        self.sculpt_version.clear()
        self.sculpt_version.addItem('None (optional)', None)
        if self.identity and self.target:
            try:
                root = self.service.paths.shot_data_dir(self.identity.episode, self.identity.sequence,
                    self.identity.shot, 'shot_sculpt', self.target, 'main')
                for directory in sorted(root.glob('v*'), reverse=True):
                    path = self.service.paths.artifact_file(directory, 'shot_sculpt.json')
                    if path.is_file():
                        sculpt = read_json(path, {})
                        if sculpt.get('schema') != 'smartpipeline.shot_sculpt.v1':
                            continue
                        self.sculpt_version.addItem(directory.name, str(path))
                        self.sculpt_version.setItemData(self.sculpt_version.count()-1, str(path), QtCore.Qt.ToolTipRole)
            except Exception as exc:
                self.source_hint.setText(str(exc))
        self._update_ready()

    def _range_changed(self):
        custom = self.range_mode.currentIndex() == 1
        self.start_frame.setEnabled(custom)
        self.end_frame.setEnabled(custom)
        if not custom and self.identity:
            start, end = self.service.shots.shot_frame_range(self.identity)
            self.start_frame.setValue(start)
            self.end_frame.setValue(end)

    def _update_ready(self):
        self.publish_btn.setEnabled(bool(self.is_maya_session and self.identity and self.target
                                        and self.rig_version.currentData()) and not self._running())

    def publish(self):
        if self._running() or not self.is_maya_session:
            return
        source = None
        try:
            bounds = [self.start_frame.value(), self.end_frame.value()]
            if bounds[1] < bounds[0]:
                raise ValueError('End frame must not precede Start frame')
            rig = self.rig_version.currentData()
            if not self.identity or not self.target or not rig:
                raise ValueError('Select a shot, cast and published Rig version')
            self.service.pin(rig)
            self.publish_btn.setEnabled(False)
            source = self.export_current_data(bounds)
            self.data_published.emit(str(source))
            row = dict(kind='animation', target=self.target, source=str(source),
                       rig=rig, rig_context=self.rig_context.currentText(), sculpt=self.sculpt_version.currentData())
            plan = self.service.plan(self.identity, [row], frame_range=bounds)
            self.start_worker(plan)
        except Exception as exc:
            self.status.setPlainText(str(exc))
        finally:
            if source:
                self.status.appendPlainText('Animation Data saved (retained if USD fails): ' + str(source))
            self._update_ready()

    def export_current_data(self, bounds):
        from smartlib.dcc.maya.animation_data_publish import publish_current_animation_data
        return publish_current_animation_data(self.service.shots, self.identity, target=self.target,
            frame_range=bounds, comment='Captured from current Maya scene for USD Publish')

    def finished_worker(self, code, status):
        try:
            UsdPublishDialog.finished_worker(self, code, status)
        except Exception as exc:
            self.status.appendPlainText(str(exc))
        self._sync_running_ui()
        if self._pending_context:
            identity, target = self._pending_context
            self._pending_context = None
            self.set_context(identity, target, force=True)
        self._update_ready()

    def start_worker(self, plan):
        UsdPublishDialog.start_worker(self, plan)
        self.process.stateChanged.connect(lambda _state: self._sync_running_ui())
        self._sync_running_ui()

    def _sync_running_ui(self):
        running = self._running()
        self.source_group.setEnabled(not running)
        self.output_group.setEnabled(not running)
        self.refresh_btn.setEnabled(not running)
        self._update_ready()

    def closeEvent(self, event):
        if self._running():
            event.ignore()
            return
        super().closeEvent(event)
