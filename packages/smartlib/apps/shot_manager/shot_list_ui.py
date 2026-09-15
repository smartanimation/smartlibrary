"""Select new shots from the configured Google Sheet without changing the sheet."""
try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets

from .shot_list import ShotListService


class _Reader(QtCore.QThread):
    def __init__(self, service, parent):
        super().__init__(parent)
        self.service = service
        self.rows = []
        self.error = ""

    def run(self):
        try:
            self.rows = self.service.read()
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__


class ShotListDialog(QtWidgets.QDialog):
    def __init__(self, shots, parent=None):
        super().__init__(parent)
        self.service = ShotListService(shots)
        self.rows = []
        self.created = []
        self.reader = None
        self.setWindowTitle("Import Shot List")
        self.resize(960, 500)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            "Read Shot List URL from Config Creator. Select new shots to create. Existing shots are preserved."
        ))
        options = QtWidgets.QHBoxLayout()
        self.cut_in = QtWidgets.QSpinBox()
        self.cut_out = QtWidgets.QSpinBox()
        for widget, value in ((self.cut_in, 1001), (self.cut_out, 1240)):
            widget.setRange(-1000000, 1000000)
            widget.setValue(value)
        options.addWidget(QtWidgets.QLabel("Default Range (when range is blank)"))
        options.addWidget(self.cut_in)
        options.addWidget(self.cut_out)
        options.addStretch()
        self.reload_button = QtWidgets.QPushButton("Load Shot List")
        options.addWidget(self.reload_button)
        layout.addLayout(options)
        self.table = QtWidgets.QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Use", "Row", "Episode", "Sequence", "Shot", "Range", "State", "Description"])
        self.table.setToolTip("range: 1001-1240. Blank uses Default Range. Existing shots are not updated.")
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QtWidgets.QHBoxLayout()
        self.create_button = QtWidgets.QPushButton("Create Selected Shots")
        self.close_button = QtWidgets.QPushButton("Close")
        buttons.addStretch()
        buttons.addWidget(self.create_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.reload_button.clicked.connect(self.load)
        self.create_button.clicked.connect(self.create_selected)
        self.close_button.clicked.connect(self.accept)
        self.cut_in.valueChanged.connect(self.populate)
        self.cut_out.valueChanged.connect(self.populate)
        self.create_button.setEnabled(False)
        QtCore.QTimer.singleShot(0, self.load)

    def load(self):
        if self.reader and self.reader.isRunning():
            return
        self.reload_button.setEnabled(False)
        self.create_button.setEnabled(False)
        self.message.setText("Loading Shot List...")
        self.reader = _Reader(self.service, self)
        self.reader.finished.connect(self.loaded)
        self.reader.start()

    def loaded(self):
        self.reload_button.setEnabled(True)
        self.table.setRowCount(0)
        self.rows = [] if self.reader.error else self.reader.rows
        self.populate()
        self.message.setText(self.reader.error or f"{len(self.rows)} rows loaded. Select the shots to create.")
        self.reader.deleteLater()
        self.reader = None

    def populate(self, *_args):
        checked = {self.rows[i].row_number for i in range(min(len(self.rows), self.table.rowCount()))
                   if self.table.item(i, 0) and self.table.item(i, 0).checkState() == QtCore.Qt.Checked}
        self.table.setRowCount(0)
        for index, row in enumerate(self.rows):
            state = self.service.state(row, self.cut_in.value(), self.cut_out.value())
            frame_range = "-"
            try:
                request = self.service.request(row, self.cut_in.value(), self.cut_out.value())
                frame_range = f"{request.cut_in} - {request.cut_out}"
            except ValueError:
                pass
            self.table.insertRow(index)
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable if state == "New" else QtCore.Qt.NoItemFlags)
            check.setCheckState(QtCore.Qt.Checked if state == "New" and row.row_number in checked else QtCore.Qt.Unchecked)
            self.table.setItem(index, 0, check)
            cells = [str(row.row_number), row.values.get("episode", ""), row.values.get("sequence", ""),
                     row.values.get("shot", ""), frame_range, state, row.values.get("description", "")]
            for column, text in enumerate(cells, 1):
                item = QtWidgets.QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        self.create_button.setEnabled(bool(self.rows) and not (self.reader and self.reader.isRunning()))

    def create_selected(self):
        selected = [row for i, row in enumerate(self.rows)
                    if self.table.item(i, 0).checkState() == QtCore.Qt.Checked]
        if not selected:
            self.message.setText("Select at least one new shot.")
            return
        self.create_button.setEnabled(False)
        created = []
        errors = []
        for row in selected:
            try:
                identity = self.service.create(row, self.cut_in.value(), self.cut_out.value())
                created.append(identity)
                self.created.append(identity)
            except Exception as exc:
                errors.append(f"Row {row.row_number}: {exc}")
        self.populate()
        self.message.setText(f"Created {len(created)} shots." + ("\n" + "\n".join(errors) if errors else ""))

    def done(self, result):
        if self.reader and self.reader.isRunning():
            self.message.setText("Please wait for Shot List loading to finish.")
            return
        super().done(result)

    def closeEvent(self, event):
        if self.reader and self.reader.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)
