"""Per-character choices; frame range and source scene are shared by the batch."""
from .usd_handoff_ui import QtCore, QtWidgets
from smartlib.core.metadata import read_json


class AnimationBatchSettings(QtWidgets.QGroupBox):
    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__('Selected Characters - Source Versions', parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Cast', 'Rig Context', 'Rig Version', 'Shot Sculpt', 'Cast Asset Release'])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table)
        hint = QtWidgets.QLabel('One saved scene / shared frame range. All targets must succeed before updating Composition. Unselected characters are retained.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.rows = []

    def populate(self, service, identity, targets, drafts):
        self.rows = []
        self.table.setRowCount(0)
        for target in targets:
            rigs = service.animation_rig_versions(identity, target)
            index = self.table.rowCount()
            self.table.insertRow(index)
            self.table.setItem(index, 0, QtWidgets.QTableWidgetItem(target))
            context, version, sculpt = (QtWidgets.QComboBox() for _ in range(3))
            skel = QtWidgets.QComboBox()
            skel.addItem('From published Cast', None)
            skel.setEnabled(False)
            skel.currentIndexChanged.connect(lambda *_: self.changed.emit())
            context.addItems(sorted(rigs, key=lambda name: (name.lower() != 'anim', name)))
            sculpt.addItem('None (optional)', None)
            root = service.paths.shot_data_dir(identity.episode, identity.sequence,
                identity.shot, 'shot_sculpt', target, 'main')
            for directory in sorted(root.glob('v*'), reverse=True):
                path = service.paths.artifact_file(directory, 'shot_sculpt.json')
                if read_json(path, {}).get('schema') == 'smartpipeline.shot_sculpt.v1':
                    sculpt.addItem(directory.name, str(path))
            def refresh(_=None, c=context, v=version, available=rigs):
                v.clear()
                for row in available.get(c.currentText(), []):
                    v.addItem(row['version'], row['path'])
                self.changed.emit()
            context.currentIndexChanged.connect(refresh)
            version.currentIndexChanged.connect(lambda *_: self.changed.emit())
            sculpt.currentIndexChanged.connect(lambda *_: self.changed.emit())
            refresh()
            draft = drafts.get((identity.episode, identity.sequence, identity.shot, target))
            if draft:
                if len(draft) > 6:
                    selected = skel.findData(draft[6])
                    if selected >= 0:
                        skel.setCurrentIndex(selected)
                context.setCurrentText(draft[0])
                for widget, value in ((version, draft[1]), (sculpt, draft[2])):
                    selected = widget.findData(value)
                    if selected >= 0:
                        widget.setCurrentIndex(selected)
            for col, widget in enumerate((context, version, sculpt, skel), 1):
                self.table.setCellWidget(index, col, widget)
            self.rows.append((target, context, version, sculpt, skel))
        self.changed.emit()

    def selections(self):
        return [dict(target=t, rig=v.currentData(), rig_context=c.currentText(), sculpt=s.currentData(), skel=k.currentData())
                for t, c, v, s, k in self.rows]
