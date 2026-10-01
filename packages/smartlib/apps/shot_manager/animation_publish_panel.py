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
        self._targets = ()
        self._pending_targets = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        self.context_label = QtWidgets.QLabel('Select a cast member')
        layout.addWidget(self.context_label)

        self.source_group = QtWidgets.QGroupBox('Source Versions')
        grid = QtWidgets.QGridLayout(self.source_group)
        self.curve_source = QtWidgets.QLabel('Saved Scene Snapshot → Worker → Data (new Version)')
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
        self.skel_version = QtWidgets.QComboBox()
        self.skel_version.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.skel_version.setMinimumContentsLength(16)
        grid.addWidget(QtWidgets.QLabel('Asset USD source'), 4, 0)
        grid.addWidget(self.skel_version, 4, 1, 1, 2)
        self.skel_version.setToolTip('Compare joint animation with evaluated Maya deformation at every frame. Fall back to deform cache if it differs.')
        layout.addWidget(self.source_group)
        from .animation_batch_settings import AnimationBatchSettings
        self.batch_settings = AnimationBatchSettings(self)
        self.batch_settings.hide()
        self.batch_settings.changed.connect(self._update_ready)
        layout.addWidget(self.batch_settings)

        self.output_group = QtWidgets.QGroupBox('Output Settings')
        output = QtWidgets.QGridLayout(self.output_group)
        output.addWidget(QtWidgets.QLabel('Output Format'), 0, 0)
        self.usd_check = QtWidgets.QCheckBox('USD')
        self.usd_check.setChecked(True)
        self.usd_check.setEnabled(False)
        self.abc_check = QtWidgets.QCheckBox('Alembic (Geometry Cache)')
        for widget, tip in [
            (self.usd_check, 'Verified UsdSkel Animation when a static Rig USD is selected; otherwise final-deform USD.'),
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
        self.output_hint = QtWidgets.QLabel('Curve Data → rebuild → compare every frame. Matching Rig USD: animation.usd; unsupported deformation: deform.usdc. The result and fallback reason appear in the job log.')
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
        self.refresh_btn.clicked.connect(lambda: self.set_targets(self.identity, self._targets or (self.target,), force=True))
        actions.addWidget(self.refresh_btn)
        queue_btn = QtWidgets.QPushButton('Job Queue')
        queue_btn.clicked.connect(self.show_queue)
        actions.addWidget(queue_btn)
        self.compose_btn = QtWidgets.QPushButton('Compose Existing…')
        self.compose_btn.clicked.connect(self.compose)
        actions.addWidget(self.compose_btn)
        self.composition_btn = QtWidgets.QPushButton('Open Smart Composition')
        self.composition_btn.clicked.connect(self.open_composition)
        actions.addStretch(1)
        self.publish_btn = QtWidgets.QPushButton('Publish Animation USD')
        self.publish_btn.setEnabled(False)
        self.publish_btn.clicked.connect(self.publish)
        actions.addWidget(self.publish_btn)
        layout.addLayout(actions)
        layout.addWidget(self.composition_btn)
        self.rig_context.currentIndexChanged.connect(self._populate_rig_versions)
        self.rig_version.currentIndexChanged.connect(self._update_ready)
        self.range_mode.currentIndexChanged.connect(self._range_changed)
        self._range_changed()

    def _running(self):
        if getattr(self, '_queue_job', None):
            return self._queue.jobs[self._queue_job]['state'] not in ('COMPLETE', 'FAILED')
        return self.process is not None and self.process.state() != QtCore.QProcess.NotRunning

    def _remember(self):
        if self._key:
            self._drafts[self._key] = (self.rig_context.currentText(), self.rig_version.currentData(),
                self.sculpt_version.currentData(), self.range_mode.currentIndex(), self.start_frame.value(), self.end_frame.value(), self.skel_version.currentData())

    def set_targets(self, identity, targets, *, force=False):
        targets = tuple(dict.fromkeys(t for t in targets if t))
        if self._running():
            self._pending_targets = (identity, targets)
            return
        if self.identity == identity and self._targets == targets and not force:
            return
        if len(self._targets) > 1 and self.identity:
            for row in self.batch_settings.selections():
                key = (self.identity.episode, self.identity.sequence, self.identity.shot, row['target'])
                self._drafts[key] = (row['rig_context'], row['rig'], row['sculpt'],
                    self.range_mode.currentIndex(), self.start_frame.value(), self.end_frame.value(), row.get('skel'))
            self._key = None  # hidden single-target controls must not overwrite batch choices
        else:
            self._remember()
        self._targets = targets
        self.set_context(identity, targets[0] if targets else '', force=force)
        multiple = len(targets) > 1
        self.source_group.setVisible(not multiple)
        self.batch_settings.setVisible(multiple)
        if multiple:
            try:
                self.batch_settings.populate(self.service, identity, targets, self._drafts)
                self.context_label.setText(' / '.join((identity.episode, identity.sequence, identity.shot))
                                           + f' / {len(targets)} characters')
            except Exception as exc:
                self.batch_settings.rows = []
                self.status.setPlainText(str(exc))
        self._update_ready()

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
        self.skel_version.clear()
        self.skel_version.addItem('From published Cast (Auto / deform fallback)', None)
        self.skel_version.setEnabled(False)
        self.range_mode.setCurrentIndex(0)
        self._range_changed()
        draft = self._drafts.get(key)
        if draft:
            if len(draft) > 6:
                index = self.skel_version.findData(draft[6])
                if index >= 0:
                    self.skel_version.setCurrentIndex(index)
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
        self.source_hint.setText('Select the rebuild Rig context/version. Worker captures Curve Data from a fixed saved-scene snapshot.'
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
        if not hasattr(self, 'publish_btn'):
            return
        ready = bool(self.target and self.rig_version.currentData())
        if len(self._targets) > 1:
            rows = self.batch_settings.selections()
            ready = len(rows) == len(self._targets) and all(row['rig'] for row in rows)
        self.publish_btn.setEnabled(bool(self.is_maya_session and self.identity and ready) and not self._running())
        self.publish_btn.setText(f'Publish Animation USD ({len(self._targets)})' if len(self._targets) > 1
                                 else 'Publish Animation USD')

    def publish(self):
        if self._running() or not self.is_maya_session:
            return
        source = None
        try:
            bounds = [self.start_frame.value(), self.end_frame.value()]
            if bounds[1] < bounds[0]:
                raise ValueError('End frame must not precede Start frame')
            selections = self.batch_settings.selections() if len(self._targets) > 1 else [
                dict(target=self.target, rig=self.rig_version.currentData(),
                     rig_context=self.rig_context.currentText(), sculpt=self.sculpt_version.currentData(), skel=self.skel_version.currentData())]
            if not self.identity or not selections or any(not r['target'] or not r['rig'] for r in selections):
                raise ValueError('Select a shot, cast and published Rig version for every target')
            cast_data = self.service.shots.load_cast(self.identity).get('cast') or {}
            options = []
            from .cast_release import cast_product
            compositions = self.service.composition_versions(self.identity)
            base = self.service.pin(compositions[0]['path']) if compositions else None
            for row in selections:
                cast = cast_data.get(row['target'])
                if not cast:
                    raise ValueError('Cast was not found: ' + row['target'])
                fixed_cast = cast_product(self.service, self.identity, row['target'], base)
                if not fixed_cast:
                    raise ValueError(row['target'] + ': publish Cast with an Asset USD Release first')
                options.append(dict(target=row['target'], rig=self.service.pin(row['rig']),
                    cast_asset=fixed_cast, skel=None,
                    rig_context=row['rig_context'], sculpt=self.service.pin(row['sculpt']) if row['sculpt'] else None,
                    cast=cast))
            self.publish_btn.setEnabled(False)
            from smartlib.dcc.maya.publish_scene_input import capture_saved_scene
            scene_input = capture_saved_scene(self.service, self.identity)
            from smartlib.apps.review_build_manager.publish_queue import get_queue
            if not hasattr(self, '_queue'):
                self._queue = get_queue(self.service.shots)
                self._queue.changed.connect(self._queue_changed)
            self._queue_job = self._queue.submit(self.identity, kind='animation_usd',
                scene_input=scene_input, scene_options=options[0] if len(options) == 1 else {'targets': options},
                frame_range=bounds, merge_animation=True)
            self.status.setPlainText('Starting independent runner. Wait for Accepted before closing Maya.')
            self._sync_running_ui()
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
        from smartlib.apps.review_build_manager.publish_queue import get_queue
        if not hasattr(self, '_queue'):
            self._queue = get_queue(self.service.shots)
            self._queue.changed.connect(self._queue_changed)
        self._queue_job = self._queue.submit(self.identity, kind='animation_usd', plan=plan, merge_animation=True)
        self.status.setPlainText('Queued in Review Build Manager: ' + self._queue_job)
        self._sync_running_ui()

    def show_queue(self):
        from smartlib.apps.review_build_manager.publish_queue import show_queue
        show_queue(self.service.shots)

    def open_composition(self):
        try:
            import os, subprocess
            if not self.identity:
                raise ValueError('Select a Shot')
            versions = self.service.composition_versions(self.identity)
            if not versions:
                raise ValueError('No published Composition for this Shot')
            self.service.load_handoff(versions[0]['path'])
            launcher = Path(__file__).resolve().parents[4] / 'tools' / 'usd' / 'usdpython.bat'
            env = os.environ.copy()
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[3]) + os.pathsep + env.get('PYTHONPATH', '')
            subprocess.Popen([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c',
                subprocess.list2cmdline([str(launcher), '-m', 'smartlib.apps.smart_composition.main',
                    '--config', str(self.service.config.config_dir), versions[0]['path']])],
                env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception as exc:
            self.status.setPlainText(str(exc))

    @QtCore.Slot(str)
    def _queue_changed(self, job_id):
        if job_id != getattr(self, '_queue_job', None):
            return
        job = self._queue.jobs[job_id]
        self.status.setPlainText(f"{job_id}: {job['state']} — {job['task']}\n{job['message']}\n{job['stderr'][-3000:]}")
        self._sync_running_ui()
        if job['state'] in ('COMPLETE', 'FAILED'):
            if job['state'] == 'COMPLETE':
                self.data_published.emit(job['message'])
            if self._pending_context:
                identity, target = self._pending_context
                self._pending_context = None
                self.set_context(identity, target, force=True)
            if self._pending_targets:
                identity, targets = self._pending_targets
                self._pending_targets = None
                self.set_targets(identity, targets, force=True)

    def _sync_running_ui(self):
        running = self._running()
        self.source_group.setEnabled(not running)
        self.batch_settings.setEnabled(not running)
        self.output_group.setEnabled(not running)
        self.refresh_btn.setEnabled(not running)
        self._update_ready()

    def closeEvent(self, event):
        super().closeEvent(event)
