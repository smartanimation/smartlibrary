"""Inline Cast registration; deliberately contains no department load policy."""
from smartlib.apps.shot_manager.usd_handoff_ui import QtCore, QtWidgets
from smartlib.apps.shot_manager.assets_publish import AssetsPublishService


class AssetsPublishPanel(QtWidgets.QWidget):
    published = QtCore.Signal(str)

    def __init__(self, shots, parent=None, *, is_maya_session=False):
        super().__init__(parent)
        self.service = AssetsPublishService(shots)
        self.identity = None
        self._busy = False
        self._pending = None
        self._rows = []
        self.is_maya_session = is_maya_session
        layout = QtWidgets.QVBoxLayout(self)
        self.context_label = QtWidgets.QLabel('Assets - Cast Registration')
        layout.addWidget(self.context_label)
        hint = QtWidgets.QLabel('Pin an Asset USD Release for each Cast. Animation uses this Release; Geometry, Look and Rig are fixed by the Release.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.editor = QtWidgets.QWidget()
        editor_layout = QtWidgets.QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QtWidgets.QHBoxLayout()
        self.sync_btn = QtWidgets.QPushButton('Sync from Cast')
        self.sync_btn.clicked.connect(lambda: self.set_context(self.identity, force=True))
        toolbar.addWidget(self.sync_btn)
        toolbar.addWidget(QtWidgets.QLabel('Base Composition'))
        self.base_combo = QtWidgets.QComboBox()
        toolbar.addWidget(self.base_combo, 1)
        self.validate_btn = QtWidgets.QPushButton('Validate')
        self.validate_btn.clicked.connect(self.validate)
        toolbar.addWidget(self.validate_btn)
        editor_layout.addLayout(toolbar)
        self.placement_check = QtWidgets.QCheckBox('Use Smart Maker placements (STATIC / CURVE) from saved scene')
        self.placement_check.setEnabled(is_maya_session)
        self.placement_check.setChecked(False)
        self.placement_check.setToolTip('Asset USD targets require one attached placement each. Animation USD is not transformed again.')
        editor_layout.addWidget(self.placement_check)
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['Include', 'Cast', 'Asset / Variant', 'Quality',
                                             'USD Release', 'Assets Layer', 'State'])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        editor_layout.addWidget(self.table, 1)
        self.summary = QtWidgets.QLabel()
        editor_layout.addWidget(self.summary)
        hint = QtWidgets.QLabel('Cast pins the Release used by Animation. Changing that Release requires a new Animation publish; other Casts and cameras are retained.')
        hint.setWordWrap(True)
        editor_layout.addWidget(hint)
        layout.addWidget(self.editor, 1)
        self.status = QtWidgets.QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(110)
        layout.addWidget(self.status)
        actions = QtWidgets.QHBoxLayout()
        queue_btn = QtWidgets.QPushButton('Job Queue')
        queue_btn.clicked.connect(self.show_queue)
        actions.addWidget(queue_btn)
        actions.addStretch()
        self.publish_btn = QtWidgets.QPushButton('Publish Assets USD')
        self.publish_btn.clicked.connect(self.publish)
        actions.addWidget(self.publish_btn)
        layout.addLayout(actions)
        self.base_combo.currentIndexChanged.connect(self.populate)
        self.table.itemChanged.connect(self.update_summary)
        self._sync()

    def _sync(self):
        self.editor.setEnabled(not self._busy and self.identity is not None)
        self.publish_btn.setEnabled(not self._busy and self.identity is not None)

    def set_context(self, identity, *, force=False):
        if self._busy:
            self._pending = (identity, force)
            return
        if self.identity == identity and not force:
            return
        self.identity = identity
        self.context_label.setText('Assets - ' + ' / '.join(self.service._identity(identity)) if identity else 'Select a shot')
        self.base_combo.blockSignals(True)
        self.base_combo.clear()
        try:
            for row in self.service.composition_versions(identity) if identity else []:
                self.base_combo.addItem(row['version'], row['path'])
        except Exception as exc:
            self.status.setPlainText(str(exc))
        self.base_combo.addItem('None - Assets only', None)
        self.base_combo.blockSignals(False)
        self.populate()
        self._sync()

    def populate(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._rows = []
        self._discovery_error = ''
        try:
            saved = {}
            animated = set()
            restore_membership = False
            if self.base_combo.currentData():
                base = self.service.load_handoff(self.base_combo.currentData())
                restore_membership = bool(base.get('assets_registration_complete'))
                for ref in base['products']:
                    product = self.service.load_handoff(ref['path'])
                    if product['kind'] == 'assets':
                        saved[product['target']] = product.get('inputs', {})
                    elif product['kind'] == 'animation':
                        animated.add(product['target'])
            for row in self.service.cast_entries(self.identity) if self.identity else []:
                prior = saved.get(row['target'], {})
                index = self.table.rowCount()
                self.table.insertRow(index)
                include = QtWidgets.QTableWidgetItem()
                include.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
                include.setCheckState(QtCore.Qt.Checked if not restore_membership or row['target'] in saved
                                      else QtCore.Qt.Unchecked)
                self.table.setItem(index, 0, include)
                self.table.setItem(index, 1, QtWidgets.QTableWidgetItem(row['target']))
                asset = row['asset']
                self.table.setItem(index, 2, QtWidgets.QTableWidgetItem(asset.name + ' / ' + asset.variant))
                source = QtWidgets.QComboBox()
                source.addItem('proxy', 'proxy')
                self.table.setCellWidget(index, 3, source)
                version = QtWidgets.QComboBox()
                for value in row.get('releases', []):
                    version.addItem(value['label'], value['version'])
                version.addItem('Select Release', None)
                previous = prior.get('registration', {}).get('usd_version')
                if previous:
                    selected = version.findData(previous)
                    if selected < 0:
                        version.addItem(previous + ' (unavailable)', previous)
                        selected = version.count() - 1
                    version.setCurrentIndex(selected)
                elif prior and prior.get('source') is None:
                    version.setCurrentIndex(version.count() - 1)
                self.table.setCellWidget(index, 4, version)
                self.table.setItem(index, 5, QtWidgets.QTableWidgetItem())
                self.table.setItem(index, 6, QtWidgets.QTableWidgetItem())
                row.update(source_combo=source, version_combo=version)
                self._rows.append(row)
                source.currentIndexChanged.connect(self.update_summary)
                version.currentIndexChanged.connect(self.update_summary)
        except Exception as exc:
            self._discovery_error = str(exc)
            self.status.setPlainText(str(exc))
        finally:
            self.table.blockSignals(False)
        self.update_summary()

    def update_summary(self, *_):
        self.table.blockSignals(True)
        included = 0
        for index, row in enumerate(self._rows):
            selected = self.table.item(index, 0).checkState() == QtCore.Qt.Checked
            included += int(selected)
            self.table.item(index, 5).setText('Asset Release')
            state = row['error'] or ('Select Release' if not row['version_combo'].currentData() else 'Selected')
            self.table.item(index, 6).setText(state if selected else 'Excluded')
        self.table.blockSignals(False)
        self.summary.setText(f'{included} Casts selected')

    def selection(self):
        result = []
        for index, row in enumerate(self._rows):
            if self.table.item(index, 0).checkState() != QtCore.Qt.Checked:
                continue
            result.append(dict(target=row['target'], asset=row['asset'],
                geometry_source='asset', quality=row['source_combo'].currentData(), version=row['version_combo'].currentData()))
        return result

    def prepare(self):
        if self._discovery_error:
            raise ValueError('Cannot publish incomplete Cast discovery: ' + self._discovery_error)
        base = self.service.pin(self.base_combo.currentData()) if self.base_combo.currentData() else None
        return self.service.registration_plan(self.identity, self.selection(), base), base

    def validate(self):
        try:
            self.prepare()
            self.status.setPlainText('Registration valid. Full USD dependency / composition validation runs in the Publish worker.')
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def publish(self):
        if self._busy or not self.identity:
            return
        try:
            plan, base = self.prepare()
            from smartlib.apps.review_build_manager.publish_queue import get_queue
            if not hasattr(self, '_queue'):
                self._queue = get_queue(self.service.shots)
                self._queue.changed.connect(self._queue_changed)
            scene_args = {}
            if self.placement_check.isChecked():
                from smartlib.dcc.maya.publish_scene_input import capture_saved_scene
                scene_args = dict(scene_input=capture_saved_scene(self.service, self.identity),
                                  scene_options={}, frame_range=plan['frame_range'])
            self._queue_job = self._queue.submit(self.identity, kind='assets_usd', plan=plan, base=base, **scene_args)
            self._busy = True
            self._sync()
            self.status.setPlainText('Queued in Review Build Manager: ' + self._queue_job)
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def show_queue(self):
        from smartlib.apps.review_build_manager.publish_queue import show_queue
        show_queue(self.service.shots)

    @QtCore.Slot(str)
    def _queue_changed(self, job_id):
        if job_id != getattr(self, '_queue_job', None):
            return
        job = self._queue.jobs[job_id]
        self.status.setPlainText(f"{job_id}: {job['state']} - {job['task']}\n{job['message']}\n{job['stderr'][-3000:]}")
        if job['state'] in ('COMPLETE', 'FAILED'):
            self._busy = False
            self._sync()
            if job['state'] == 'COMPLETE':
                self.published.emit(job['message'])
            if self._pending:
                identity, force = self._pending
                self._pending = None
                self.set_context(identity, force=force)
