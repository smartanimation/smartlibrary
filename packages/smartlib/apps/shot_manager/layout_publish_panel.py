"""Selected Smart Maker / Smart Set Dress sources to one validated Layout batch."""
from .camera_publish_panel import CameraPublishPanel, QtWidgets, QtCore


class LayoutPublishPanel(CameraPublishPanel):
    def __init__(self, *args, **kwargs):
        self.category = 'placements'
        super().__init__(*args, **kwargs)
        self.publish_btn.setText('Publish Selected Layout')
        self.source_caption.setText('Sources')
        self.policy_hint.setText('All selected sources must succeed before the new Composition is committed.')
        self.pipeline_hint.setText('Saved Scene → versioned Data → Build Layout → new Composition.')
        self._mapping_stage = None
        self.mapping = QtWidgets.QTableWidget(0, 3)
        self.mapping.setHorizontalHeaderLabels(['Set Dress Node', 'USD Asset Prim', 'Mapping Status'])
        self.mapping.horizontalHeader().setStretchLastSection(True)
        self.mapping.setColumnWidth(0, 260)
        self.mapping.setColumnWidth(1, 360)
        self.mapping.setToolTip('Map each recorded Maya node to its corresponding USD prim. Unsupported attributes fail validation.')
        self.layout().insertWidget(3, self.mapping)
        self.mapping_controls = QtWidgets.QWidget()
        controls = QtWidgets.QHBoxLayout(self.mapping_controls)
        self.suggest_btn = QtWidgets.QPushButton('Refresh Mapping Candidates')
        self.suggest_btn.clicked.connect(self.refresh_mapping_candidates)
        self.save_mapping_btn = QtWidgets.QPushButton('Save Mapping in Scene')
        self.save_mapping_btn.clicked.connect(self.save_mapping)
        controls.addWidget(self.suggest_btn)
        controls.addWidget(self.save_mapping_btn)
        self.layout().insertWidget(4, self.mapping_controls)
        self.base_combo.currentIndexChanged.connect(self._populate_mapping)

    def set_sources(self, identity, category, targets):
        changed = (identity, category, ', '.join(targets)) != (self.identity, self.category, self.target)
        if self._busy:
            self._pending_layout = (identity, category, targets)
            return
        if identity != self.identity:
            self.mapping.setRowCount(0)
        self.category = category
        self.set_targets(identity, targets)
        self.base_combo.setItemText(self.base_combo.count() - 1, 'None — markers only')
        self.mapping.setVisible(category == 'set_dress')
        self.mapping_controls.setVisible(category == 'set_dress')
        self._range_changed()
        if changed:
            self._populate_mapping()

    def _range_changed(self):
        # Composition timing is retained even when all Layout opinions are static.
        if self.identity:
            start, end = self.service.shots.shot_frame_range(self.identity)
            self.start_frame.setValue(start)
            self.end_frame.setValue(end)
            self.static_frame.setValue(start)
        modes = set()
        if self.category == 'set_dress':
            label = 'Static'
            hint = 'Set Dress publishes recorded static values. Use Marker CURVE for motion.'
        else:
            try:
                if self.is_maya_session and self.target:
                    from smartlib.dcc.maya.shot_publish_sources import placement_sources
                    sources = {row['target']: row['motion'] for row in placement_sources()}
                    modes = {sources[name] for name in self.target.split(', ')}
                label = {frozenset({'STATIC'}): 'Static', frozenset({'CURVE'}): 'Shot Range',
                         frozenset({'STATIC', 'CURVE'}): 'Mixed — per Marker'}.get(frozenset(modes), 'Marker Settings')
                hint = 'Output follows each Marker: STATIC samples shot start; CURVE samples the shot range.'
            except Exception as exc:
                label, hint = 'Marker Settings — refresh sources', str(exc)
        self.range_mode.blockSignals(True)
        self.range_mode.clear()
        self.range_mode.addItem(label)
        self.range_mode.blockSignals(False)
        self.range_mode.setEnabled(False)
        self.range_mode.setToolTip(hint)
        animated = 'CURVE' in modes
        grid = self.output_group.layout()
        for column, widget in enumerate((self.start_frame, self.end_frame, self.step), 1):
            grid.itemAtPosition(1, column).widget().setVisible(animated)
            widget.setVisible(animated)
            widget.setEnabled(False)
        for widget in (self.static_frame, self.static_label, self.static_hint):
            widget.setVisible('STATIC' in modes)
        self.static_frame.setEnabled(False)
        self.static_hint.setText('STATIC Markers use shot start. Motion is controlled by Marker settings.')
        self.pipeline_hint.setText(hint)

    def refresh_mapping_candidates(self):
        if self._busy or not self.identity:
            return
        try:
            current = self.base_combo.currentData()
            versions = self.service.composition_versions(self.identity)
            self.base_combo.blockSignals(True)
            try:
                self.base_combo.clear()
                for row in versions:
                    self.base_combo.addItem(row['version'], row['path'])
                self.base_combo.addItem('None — markers only', None)
                index = self.base_combo.findData(current)
                self.base_combo.setCurrentIndex(index if index >= 0 else self.base_combo.count() - 1)
            finally:
                self.base_combo.blockSignals(False)
            self._populate_mapping()
            if versions and current and current != versions[0]['path']:
                self.status.appendPlainText('Newer Composition available: ' + versions[0]['version'] +
                    '. Select it in Base Composition to use its Asset versions.')
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def _populate_mapping(self, *_):
        if not hasattr(self, 'mapping') or self._busy:
            return
        previous = self._mapping_records(strict=False) if self._mapping_stage else {}
        self.mapping.setRowCount(0)
        self._mapping_stage = None
        if self.category != 'set_dress' or not self.identity or not self.is_maya_session:
            return
        try:
            from smartlib.dcc.maya.set_dress import load_package_from_scene
            from smartlib.dcc.maya.shot_publish_sources import token
            from smartlib.dcc.maya import setdress_mapping
            from .setdress_mapping import resolve
            package, _ = load_package_from_scene()
            if not package:
                return
            matches = {}
            if self.base_combo.currentData():
                from pxr import Usd
                data = self.service.load_handoff(self.base_combo.currentData())
                stage = Usd.Stage.Open(data['entrypoint']['path'])
                self._mapping_stage = stage
            nodes = {}
            for layer in package.layers:
                if token(layer.name) in self.target.split(', ') and not layer.muted:
                    for change in layer.changes:
                        if change.node_id in nodes and nodes[change.node_id] != change.node:
                            raise ValueError('Duplicate legacy node IDs. Re-record these Set Dress layers to distinguish Asset references.')
                        nodes[change.node_id] = change.node
            nodes, source_ids = setdress_mapping.scene_nodes(nodes)
            if self._mapping_stage:
                saved = setdress_mapping.load(self.identity)
                saved.update(previous)
                matches = resolve(self._mapping_stage, nodes,
                    self.service.shots.load_cast(self.identity).get('cast') or {}, saved, source_ids)
            for key, name in nodes.items():
                row = self.mapping.rowCount()
                self.mapping.insertRow(row)
                item = QtWidgets.QTableWidgetItem(name)
                item.setData(QtCore.Qt.UserRole, key)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.mapping.setItem(row, 0, item)
                combo = QtWidgets.QComboBox()
                combo.setEditable(True)
                match = matches.get(key, dict(candidates=[], selected='', state='Select Base Composition'))
                combo.addItems([''] + match['candidates'])
                combo.setCurrentText(match['selected'])
                self.mapping.setCellWidget(row, 1, combo)
                state = QtWidgets.QTableWidgetItem(match['state'])
                state.setToolTip(match.get('reason', match['state']))
                combo.setToolTip(match.get('reason', match['state']))
                state.setFlags(state.flags() & ~QtCore.Qt.ItemIsEditable)
                self.mapping.setItem(row, 2, state)
                combo.currentTextChanged.connect(lambda text, item=state: item.setText('Manual selection' if text else 'Choose a prim'))
            self.mapping.horizontalScrollBar().setValue(0)
            lines = ['Mapping refreshed — Base Composition: ' + self.base_combo.currentText(),
                     '%d / %d nodes resolved.' % (sum(bool(m['selected']) for m in matches.values()), len(nodes))]
            if self._mapping_stage:
                assets = self._mapping_stage.GetPrimAtPath('/Shot/Assets')
                for prim in assets.GetChildren() if assets else []:
                    info = prim.GetCustomDataByKey('smartpipeline') or {}
                    lines.append('%s: %s' % (prim.GetName(), info.get('usd_path') or 'No Asset USD source'))
            lines.extend(dict.fromkeys(m.get('reason', m['state']) for m in matches.values() if not m['selected']))
            self.status.setPlainText('\n'.join(lines))
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def _mapping_records(self, *, strict=True):
        from .setdress_mapping import prim_record
        records = {}
        if self._mapping_stage:
            for row in range(self.mapping.rowCount()):
                path = self.mapping.cellWidget(row, 1).currentText().strip()
                if path:
                    try:
                        records[self.mapping.item(row, 0).data(QtCore.Qt.UserRole)] = prim_record(self._mapping_stage, path)
                    except ValueError:
                        if strict:
                            raise
        return records

    def save_mapping(self):
        try:
            from smartlib.dcc.maya import setdress_mapping
            if not self.identity or not self.is_maya_session or not self._mapping_stage:
                raise ValueError('Select a Base Composition in Maya first')
            records = self._mapping_records()
            setdress_mapping.save(self.identity, records, replace_keys=[
                self.mapping.item(row, 0).data(QtCore.Qt.UserRole) for row in range(self.mapping.rowCount())])
            self.status.setPlainText('%d / %d mappings stored. Blank rows remain unresolved. Save the Maya scene to retain changes.' %
                                     (len(records), self.mapping.rowCount()))
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def start_camera_export(self, bounds):
        if self.category == 'set_dress' and not self.base_combo.currentData():
            raise ValueError('Select a Base Composition containing the target Assets')
        node_map = {}
        for row in range(self.mapping.rowCount()):
            path = self.mapping.cellWidget(row, 1).currentText().strip()
            if not path.startswith('/Shot/Assets/'):
                raise ValueError('Select a USD Asset prim for every Set Dress node')
            node_map[self.mapping.item(row, 0).data(QtCore.Qt.UserRole)] = path
        from smartlib.dcc.maya.publish_scene_input import capture_saved_scene
        from smartlib.apps.review_build_manager.publish_queue import get_queue
        if self.category == 'set_dress':
            from smartlib.dcc.maya import setdress_mapping
            records = self._mapping_records()
            if len(records) != len(node_map):
                raise ValueError('Refresh and confirm the USD prim mappings')
            setdress_mapping.save(self.identity, records)
        scene_input = capture_saved_scene(self.service, self.identity)
        if not hasattr(self, '_queue'):
            self._queue = get_queue(self.service.shots)
            self._queue.changed.connect(self._queue_changed)
        identity, bounds, base = self._job
        self._queue_job = self._queue.submit(identity, kind='layout_usd', scene_input=scene_input,
            scene_options=dict(category=self.category, targets=self.target.split(', '), node_map=node_map,
                               comment=self.comment.text().strip()), frame_range=bounds, base=base)
        self.status.setPlainText('Starting independent runner. Wait for Accepted before closing Maya.')

    def _sync(self):
        super()._sync()
        if hasattr(self, 'mapping'):
            self.mapping.setEnabled(not self._busy)
            self.mapping_controls.setEnabled(not self._busy)

    def _queue_changed(self, job_id):
        super()._queue_changed(job_id)
        if not self._busy and getattr(self, '_pending_layout', None):
            args = self._pending_layout
            self._pending_layout = None
            self.set_sources(*args)
