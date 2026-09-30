"""Inline Primary Camera publish followed by immutable USD composition."""
from smartlib.apps.shot_manager.usd_handoff_ui import QtCore, QtWidgets
from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService


class CameraPublishPanel(QtWidgets.QWidget):
    published = QtCore.Signal(str)

    def __init__(self, shots, parent=None, *, is_maya_session=False):
        super().__init__(parent)
        self.service = UsdHandoffService(shots)
        self.is_maya_session = is_maya_session
        self.identity, self.target, self.process = None, '', None
        self._busy, self._pending = False, None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        self.context_label = QtWidgets.QLabel('Select a Primary Camera')
        layout.addWidget(self.context_label)
        self.source_group = QtWidgets.QGroupBox('Source')
        form = QtWidgets.QFormLayout(self.source_group)
        self.source_label = QtWidgets.QLabel()
        self.source_label.setWordWrap(True)
        self.source_caption = QtWidgets.QLabel('Cameras')
        form.addRow(self.source_caption, self.source_label)
        self.base_combo = QtWidgets.QComboBox()
        form.addRow('Base Composition', self.base_combo)
        hint = self.policy_hint = QtWidgets.QLabel('Primary publishes all used cameras. Selecting smartCams updates only those cameras; their published Primary must match.')
        hint.setWordWrap(True)
        form.addRow(hint)
        layout.addWidget(self.source_group)
        self.output_group = QtWidgets.QGroupBox('Output Settings')
        grid = QtWidgets.QGridLayout(self.output_group)
        grid.addWidget(QtWidgets.QLabel('Output Format'), 0, 0)
        usd = QtWidgets.QCheckBox('USD')
        usd.setChecked(True)
        usd.setEnabled(False)
        grid.addWidget(usd, 1, 0)
        self.range_mode = QtWidgets.QComboBox()
        self.range_mode.addItems(['Shot Range', 'Custom Range', 'Static'])
        grid.addWidget(self.range_mode, 0, 1, 1, 3)
        self.start_frame, self.end_frame, self.step = (QtWidgets.QSpinBox() for _ in range(3))
        for column, (label, widget) in enumerate(zip(('Start', 'End', 'Step'),
                (self.start_frame, self.end_frame, self.step)), 1):
            grid.addWidget(QtWidgets.QLabel(label), 1, column)
            widget.setRange(-1000000, 1000000)
            grid.addWidget(widget, 2, column)
        self.step.setRange(1, 1)
        self.step.setValue(1)
        self.step.setEnabled(False)
        hint = self.pipeline_hint = QtWidgets.QLabel('Saved Scene → Camera Data → world-baked USD → camera.usda. Maya / FBX and per-camera resolution are retained.')
        hint.setWordWrap(True)
        grid.addWidget(hint, 3, 0, 1, 4)
        self.static_frame = QtWidgets.QSpinBox()
        self.static_frame.setRange(-1000000, 1000000)
        self.static_label = QtWidgets.QLabel('Static Frame')
        grid.addWidget(self.static_label, 4, 0)
        grid.addWidget(self.static_frame, 4, 1)
        self.static_hint = QtWidgets.QLabel('Animation not required — freeze transform and lens at this frame; retain the shot range.')
        self.static_hint.setWordWrap(True)
        grid.addWidget(self.static_hint, 5, 0, 1, 4)
        layout.addWidget(self.output_group)
        self.comment = QtWidgets.QLineEdit()
        self.comment.setPlaceholderText('Comment')
        layout.addWidget(self.comment)
        layout.addStretch(1)
        self.status = QtWidgets.QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(110)
        layout.addWidget(self.status)
        actions = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton('Refresh Sources')
        self.refresh_btn.clicked.connect(lambda: self.set_context(self.identity, self.target, force=True))
        actions.addWidget(self.refresh_btn)
        queue_btn = QtWidgets.QPushButton('Job Queue')
        queue_btn.clicked.connect(self.show_queue)
        actions.addWidget(queue_btn)
        actions.addStretch(1)
        self.publish_btn = QtWidgets.QPushButton('Publish Selected Cameras')
        self.publish_btn.clicked.connect(self.publish)
        actions.addWidget(self.publish_btn)
        layout.addLayout(actions)
        self.range_mode.currentIndexChanged.connect(self._range_changed)
        self._range_changed()
        self._sync()

    def _running(self):
        return self._busy

    def _sync(self):
        for widget in (self.source_group, self.output_group, self.comment, self.refresh_btn):
            widget.setEnabled(not self._busy)
        self.publish_btn.setEnabled(bool(self.is_maya_session and self.identity and self.target and not self._busy))

    def set_context(self, identity, target, *, force=False):
        if self._busy:
            self._pending = (identity, target)
            return
        if (identity, target) == (self.identity, self.target) and not force:
            return
        changed_shot = identity != self.identity
        self.identity, self.target = identity, target
        self.context_label.setText(' / '.join((identity.episode, identity.sequence, identity.shot, target)) if identity else 'Select a shot')
        self.source_label.setText(target + ' — Current Maya Scene' if target else 'Select a camera')
        if changed_shot or force:
            self.base_combo.clear()
            try:
                for row in self.service.composition_versions(identity) if identity else []:
                    self.base_combo.addItem(row['version'], row['path'])
            except Exception as exc:
                self.status.setPlainText(str(exc))
            self.base_combo.addItem('None — camera only', None)
            self.range_mode.setCurrentIndex(0)
            if identity:
                self.static_frame.setValue(self.service.shots.shot_frame_range(identity)[0])
        self._range_changed()
        self._sync()

    def set_targets(self, identity, targets):
        self.set_context(identity, ', '.join(targets))

    def _range_changed(self):
        custom = self.range_mode.currentIndex() == 1
        static = self.range_mode.currentText() == 'Static'
        for widget in (self.static_frame, self.static_label, self.static_hint):
            widget.setVisible(static)
        self.start_frame.setEnabled(custom)
        self.end_frame.setEnabled(custom)
        if not custom and self.identity:
            start, end = self.service.shots.shot_frame_range(self.identity)
            self.start_frame.setValue(start)
            self.end_frame.setValue(end)

    def publish(self):
        if self._busy or not self.is_maya_session or not self.identity or not self.target:
            return
        try:
            bounds = [self.start_frame.value(), self.end_frame.value()]
            if bounds[1] < bounds[0]:
                raise ValueError('End frame must not precede Start frame')
            base = self.service.pin(self.base_combo.currentData()) if self.base_combo.currentData() else None
            if base:
                data = self.service.load_handoff(self.service.check(base))
                if data['frame_range'] != bounds:
                    raise ValueError('Base Composition has a different frame range. Match it or choose None.')
            self._job = (self.identity, bounds, base)
            self._busy = True
            self._sync()
            self.start_camera_export(bounds)
        except Exception as exc:
            self.status.setPlainText(str(exc))
            self._busy = False
            self._sync()

    def start_camera_export(self, bounds):
        from smartlib.dcc.maya.publish_scene_input import capture_saved_scene
        scene_input = capture_saved_scene(self.service, self.identity)
        from smartlib.apps.review_build_manager.publish_queue import get_queue
        if not hasattr(self, '_queue'):
            self._queue = get_queue(self.service.shots)
            self._queue.changed.connect(self._queue_changed)
        identity, bounds, base = self._job
        self._queue_job = self._queue.submit(identity, kind='camera_batch_usd', scene_input=scene_input,
            scene_options=dict(targets=self.target.split(', '), comment=self.comment.text().strip(),
                motion_mode='static' if self.range_mode.currentText() == 'Static' else 'animated',
                sample_frame=self.static_frame.value()),
            frame_range=bounds, base=base)
        self.status.setPlainText('Starting independent runner. Wait for Accepted before closing Maya.')

    def show_queue(self):
        from smartlib.apps.review_build_manager.publish_queue import show_queue
        show_queue(self.service.shots)

    @QtCore.Slot(str)
    def _queue_changed(self, job_id):
        if job_id != getattr(self, '_queue_job', None):
            return
        job = self._queue.jobs[job_id]
        self.status.setPlainText(f"{job_id}: {job['state']} — {job['task']}\n{job['message']}\n{job['stderr'][-3000:]}")
        if job['state'] in ('COMPLETE', 'FAILED'):
            self._busy = False
            self._sync()
            if job['state'] == 'COMPLETE':
                self.published.emit(job['message'])
            if self._pending:
                identity, target = self._pending
                self._pending = None
                self.set_context(identity, target)
            elif job['state'] == 'COMPLETE':
                self.set_context(self.identity, self.target, force=True)
