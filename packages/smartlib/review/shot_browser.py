"""Non-modal RV shot browser, with asynchronous inventory and thumbnail decoding."""
import hashlib
import shutil
import threading
from collections import OrderedDict, deque
from datetime import datetime

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide6 import QtCore, QtGui, QtWidgets

from .shot_catalog import filter_catalog, is_new, scan_catalog, version_key


class ScanSignals(QtCore.QObject):
    finished = QtCore.Signal(int, object, object)


class Scan(QtCore.QRunnable):
    def __init__(self, generation, factory, tasks, profile):
        super().__init__()
        self.generation, self.factory, self.tasks, self.profile = generation, factory, tasks, profile
        self.signals = ScanSignals()
        self.cancelled = threading.Event()

    def run(self):
        try:
            rows, errors = scan_catalog(self.factory(), self.tasks, self.profile, self.cancelled.is_set)
        except Exception as exc:
            rows, errors = [], [str(exc)]
        self.signals.finished.emit(self.generation, rows, errors)


class Thumbnails(QtCore.QObject):
    ready = QtCore.Signal()

    def __init__(self, executable, parent):
        super().__init__(parent)
        self.executable = executable
        self.cache = OrderedDict()
        self.queue = deque()
        self.active = None
        self.process = QtCore.QProcess(self)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._failed)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.process.kill)

    @staticmethod
    def key(row):
        return row.movie, row.modified

    def request(self, rows):
        self.queue = deque(dict.fromkeys(self.key(r) for r in rows if r.movie and self.key(r) not in self.cache and self.key(r) != self.active))
        self._next()

    def _next(self):
        if self.active is not None or not self.queue or not self.executable:
            return
        self.active = self.queue.popleft()
        self.process.start(self.executable, ["-v", "error", "-i", self.active[0], "-frames:v", "1", "-vf", "scale=480:270:force_original_aspect_ratio=decrease", "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
        self.timer.start(12000)

    def _failed(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self._finished(-1, None)

    def _finished(self, code, status):
        self.timer.stop()
        if self.active is None:
            return
        pixmap = QtGui.QPixmap()
        output = bytes(self.process.readAllStandardOutput())
        self.process.readAllStandardError()
        if code == 0:
            pixmap.loadFromData(output)
        self.cache[self.active] = pixmap
        while len(self.cache) > 256:
            self.cache.popitem(last=False)
        self.active = None
        self.ready.emit()
        QtCore.QTimer.singleShot(0, self._next)

    def stop(self):
        self.queue.clear()
        self.timer.stop()
        if self.active:
            self.process.kill()


class Cards(QtWidgets.QStyledItemDelegate):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser

    def sizeHint(self, option, index):
        return QtCore.QSize(264, 224)

    def paint(self, painter, option, index):
        row = index.data(QtCore.Qt.UserRole)
        painter.save()
        rect = option.rect.adjusted(3, 3, -3, -3)
        painter.setClipRect(rect)
        painter.setFont(option.font)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        painter.fillRect(rect, QtGui.QColor("#19364a" if selected else "#24292e"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#22b9ff" if selected else "#414950"), 2 if selected else 1))
        painter.drawRect(rect)
        thumbnail = QtCore.QRect(rect.x() + 1, rect.y() + 1, rect.width() - 2, 140)
        painter.fillRect(thumbnail, QtGui.QColor("#15191d"))
        pixmap = self.browser.thumbnails.cache.get(Thumbnails.key(row))
        if pixmap is not None and not pixmap.isNull():
            scaled = pixmap.scaled(thumbnail.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            painter.drawPixmap(thumbnail.x() + (thumbnail.width() - scaled.width()) // 2, thumbnail.y() + (thumbnail.height() - scaled.height()) // 2, scaled)
        else:
            painter.setPen(QtGui.QColor("#929ca5"))
            painter.drawText(thumbnail, QtCore.Qt.AlignCenter, "No video" if not row.movie else "Thumbnail unavailable" if pixmap is not None or not self.browser.thumbnails.executable else "Loading thumbnail…")
        if is_new(row, self.browser.seen):
            badge = QtCore.QRect(thumbnail.x() + 6, thumbnail.y() + 6, 114, 24)
            painter.fillRect(badge, QtGui.QColor("#48350c"))
            painter.setPen(QtGui.QColor("#ffd16b"))
            painter.drawText(badge, QtCore.Qt.AlignCenter, "NEW VERSION")
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#edf1f5"))
        painter.drawText(rect.x() + 9, rect.y() + 159, "%s / %s / %s" % (row.episode, row.sequence, row.shot))
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#54c6ff"))
        painter.drawText(rect.x() + 9, rect.y() + 180, "%s   %s" % (row.version or "—", row.task))
        painter.setPen(QtGui.QColor("#a4afb8"))
        stamp = datetime.fromtimestamp(row.modified).strftime("%m/%d %H:%M") if row.modified else "No video"
        painter.drawText(rect.x() + 9, rect.y() + 199, stamp)
        previous = self.browser.seen.get(row.group)
        painter.drawText(rect.x() + 9, rect.y() + 214, "Last loaded: %s" % previous if previous else "Not loaded on this device")
        painter.restore()


class ShotBrowser(QtWidgets.QDialog):
    def __init__(self, projects, current_project, paths_factory, load_media, tasks, repo_root, parent=None):
        # RV must not own the native window: owned dialogs follow its movement.
        super().__init__(None, QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        if parent is not None:
            parent.destroyed.connect(self.close)
        self.setWindowTitle("Smart Review Shot Browser")
        self.resize(1380, 900)
        self.paths_factory, self.load_media, self.tasks = paths_factory, load_media, tasks
        self.rows, self.seen, self.workers = [], {}, []
        self.generation = 0
        self.settings = QtCore.QSettings("SmartLibrary", "SmartReviewShotBrowser")
        try:
            from .ffmpeg import ffmpeg_executable
            executable = str(ffmpeg_executable(repo_root))
        except FileNotFoundError:
            executable = shutil.which("ffmpeg") or ""
        self.thumbnails = Thumbnails(executable, self)
        self.thumbnails.ready.connect(lambda: self.grid.viewport().update())
        self.setStyleSheet("QDialog { background: #191e23; color: #e2e7ec; } QLabel { color: #dce3ea; } QListWidget, QLineEdit, QComboBox { background: #22282e; color: #e2e7ec; border: 1px solid #3b444d; } QListWidget::item:selected { background: #164e70; } QPushButton { background: #29333d; color: #e2e7ec; padding: 7px 12px; border: 1px solid #46525d; border-radius: 3px; } QPushButton:checked { background: #126a98; } QPushButton:disabled { color: #69747e; } QPushButton#push { background: #008fc6; font-weight: bold; }")
        root = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Smart Review Shot Browser")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        top.addWidget(title)
        top.addStretch()
        self.project = QtWidgets.QComboBox()
        self.project.addItems(projects)
        self.project.setCurrentText(current_project)
        top.addWidget(QtWidgets.QLabel("Project"))
        top.addWidget(self.project)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        top.addWidget(refresh)
        root.addLayout(top)
        body = QtWidgets.QHBoxLayout()
        sidebar = QtWidgets.QVBoxLayout()
        self.episodes, self.sequences = QtWidgets.QListWidget(), QtWidgets.QListWidget()
        self.sequences.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.sequences.setToolTip("Ctrl: select multiple sequences · Shift: select a range")
        self._sequence_selection = {"All sequences"}
        for label, widget in (("EPISODES", self.episodes), ("SEQUENCES", self.sequences)):
            sidebar.addWidget(QtWidgets.QLabel(label))
            widget.setFixedWidth(165)
            sidebar.addWidget(widget)
        body.addLayout(sidebar)
        main = QtWidgets.QVBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search shot, sequence, task or version…")
        main.addWidget(self.search)
        self.task = self._chips(main, "Task", ["All"] + list(tasks))
        self.profile = self._chips(main, "Source", ["working", "internal", "client", "publish"])
        filters = QtWidgets.QHBoxLayout()
        self.versions = QtWidgets.QComboBox()
        self.versions.addItems(["Latest", "All versions"])
        self.availability = QtWidgets.QComboBox()
        self.availability.addItems(["All", "New versions", "Has video", "Missing"])
        for label, widget in (("Version", self.versions), ("Availability", self.availability)):
            filters.addWidget(QtWidgets.QLabel(label))
            filters.addWidget(widget)
        filters.addStretch()
        main.addLayout(filters)
        self.summary = QtWidgets.QLabel()
        main.addWidget(self.summary)
        self.grid = QtWidgets.QListWidget()
        self.grid.setViewMode(QtWidgets.QListView.IconMode)
        self.grid.setResizeMode(QtWidgets.QListView.Adjust)
        self.grid.setMovement(QtWidgets.QListView.Static)
        self.grid.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.grid.setSelectionRectVisible(True)
        self.grid.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self.grid.setSpacing(6)
        self.grid.setUniformItemSizes(True)
        self.grid.setItemDelegate(Cards(self))
        main.addWidget(self.grid, 1)
        main.addWidget(QtWidgets.QLabel("Drag from empty space to select · Ctrl to add · Shift for range"))
        body.addLayout(main, 1)
        root.addLayout(body, 1)
        bottom = QtWidgets.QHBoxLayout()
        self.selection = QtWidgets.QLabel("0 selected")
        self.selection.setMinimumWidth(200)
        bottom.addWidget(self.selection)
        for label, slot in (("Select all", self.grid.selectAll), ("Clear", self.grid.clearSelection)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            bottom.addWidget(button)
        bottom.addStretch()
        self.new_session = QtWidgets.QPushButton("Open New Session")
        self.new_session.clicked.connect(lambda: self.push(True))
        self.push_button = QtWidgets.QPushButton("Push to RV (0)")
        self.push_button.setObjectName("push")
        self.push_button.setMinimumWidth(160)
        self.push_button.clicked.connect(lambda: self.push(False))
        bottom.addWidget(self.new_session)
        bottom.addWidget(self.push_button)
        root.addLayout(bottom)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.project.currentTextChanged.connect(self.refresh)
        self.profile.buttonClicked.connect(self.refresh)
        self.task.buttonClicked.connect(self.apply_filters)
        self.episodes.currentTextChanged.connect(self._episode_changed)
        self.sequences.itemSelectionChanged.connect(self._sequences_changed)
        self.search.textChanged.connect(self.apply_filters)
        self.versions.currentTextChanged.connect(self.apply_filters)
        self.availability.currentTextChanged.connect(self.apply_filters)
        self.grid.itemSelectionChanged.connect(self._selection_changed)
        self.grid.verticalScrollBar().valueChanged.connect(self._request_thumbnails)
        self.refresh()

    def _chips(self, layout, label, values):
        row, group = QtWidgets.QHBoxLayout(), QtWidgets.QButtonGroup(self)
        row.addWidget(QtWidgets.QLabel(label))
        for i, value in enumerate(values):
            button = QtWidgets.QPushButton(value)
            button.setCheckable(True)
            button.setChecked(i == 0)
            group.addButton(button)
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        return group

    def refresh(self, *args):
        for worker in self.workers:
            worker.cancelled.set()
        self.generation += 1
        self.rows = []
        self.grid.clear()
        self._selection_changed()
        self.status.setText("Scanning review movies…")
        project = self.project.currentText()
        worker = Scan(self.generation, lambda: self.paths_factory(project), self.tasks, self.profile.checkedButton().text())
        worker.signals.finished.connect(self._scanned)
        self.workers.append(worker)
        QtCore.QThreadPool.globalInstance().start(worker)

    def _history_key(self, group):
        value = repr((self.project.currentText(), group)).encode("utf-8")
        return "loaded/" + hashlib.sha256(value).hexdigest()

    def _scanned(self, generation, rows, errors):
        self.workers = [w for w in self.workers if w.generation != generation]
        if generation != self.generation:
            return
        self.rows = rows
        self.seen = {r.group: str(self.settings.value(self._history_key(r.group), "")) for r in rows}
        self._populate(self.episodes, sorted({r.episode for r in rows}), "All episodes")
        self._populate(self.versions, sorted({r.version for r in rows if r.version}, key=version_key, reverse=True), "Latest", ["All versions"])
        self._episode_changed()
        self.status.setText("%d scan error(s) — hover for details" % len(errors) if errors else "Ready" + (" · FFmpeg unavailable; thumbnails disabled" if not self.thumbnails.executable else ""))
        self.status.setToolTip("\n".join(errors[:30]))

    @staticmethod
    def _populate(widget, values, first, extras=()):
        current = widget.currentText() if isinstance(widget, QtWidgets.QComboBox) else (widget.currentItem().text() if widget.currentItem() else "")
        multi = isinstance(widget, QtWidgets.QListWidget) and widget.selectionMode() == QtWidgets.QAbstractItemView.ExtendedSelection
        selected = {item.text() for item in widget.selectedItems()} if multi else set()
        widget.blockSignals(True)
        widget.clear()
        choices = [first] + list(extras) + list(values)
        widget.addItems(choices)
        index = choices.index(current) if current in choices else 0
        if isinstance(widget, QtWidgets.QComboBox):
            widget.setCurrentIndex(index)
        elif multi:
            retained = selected.intersection(choices)
            for i, choice in enumerate(choices):
                widget.item(i).setSelected(choice in (retained or {first}))
        else:
            widget.setCurrentRow(index)
        widget.blockSignals(False)

    def _episode_changed(self, *args):
        episode = self._scope(self.episodes)
        self._populate(self.sequences, sorted({r.sequence for r in self.rows if not episode or r.episode == episode}), "All sequences")
        self._sequence_selection = {item.text() for item in self.sequences.selectedItems()}
        self.apply_filters()

    def _sequences_changed(self):
        selected = {item.text() for item in self.sequences.selectedItems()}
        all_label = "All sequences"
        if all_label in selected and len(selected) > 1:
            selected = {all_label} if all_label not in self._sequence_selection else selected - {all_label}
        selected = selected or {all_label}
        self.sequences.blockSignals(True)
        for i in range(self.sequences.count()):
            item = self.sequences.item(i)
            item.setSelected(item.text() in selected)
        self.sequences.blockSignals(False)
        self._sequence_selection = selected
        self.apply_filters()

    def _sequence_scope(self):
        selected = {item.text() for item in self.sequences.selectedItems()}
        return "" if "All sequences" in selected else selected

    @staticmethod
    def _scope(widget):
        return widget.currentItem().text() if widget.currentRow() > 0 else ""

    def apply_filters(self, *args):
        selected = {i.data(QtCore.Qt.UserRole).identity for i in self.grid.selectedItems()}
        version = self.versions.currentText()
        task = self.task.checkedButton().text()
        rows = filter_catalog(self.rows, self._scope(self.episodes), self._sequence_scope(), "" if task == "All" else task,
                              version == "Latest", "" if version in ("Latest", "All versions") else version,
                              self.availability.currentText(), self.search.text(), self.seen)
        self.grid.clear()
        for row in rows:
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, row)
            item.setToolTip(row.movie or "No movie available for this version")
            self.grid.addItem(item)
            item.setSelected(row.identity in selected)
        self.summary.setText("%d shots · %d entries · %d new versions · %d missing" % (len({r.group[:3] for r in rows}), len(rows), sum(is_new(r, self.seen) for r in rows), sum(not r.movie for r in rows)))
        self._selection_changed()
        QtCore.QTimer.singleShot(0, self._request_thumbnails)

    def _request_thumbnails(self, *args):
        visible = self.grid.viewport().rect()
        rows = [self.grid.item(i).data(QtCore.Qt.UserRole) for i in range(self.grid.count()) if self.grid.visualItemRect(self.grid.item(i)).intersects(visible)]
        self.thumbnails.request(rows)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "grid"):
            QtCore.QTimer.singleShot(0, self._request_thumbnails)

    def _selection_changed(self):
        rows = [i.data(QtCore.Qt.UserRole) for i in self.grid.selectedItems()]
        count = sum(bool(r.movie) for r in rows)
        self.selection.setText("%d selected · %d playable" % (len(rows), count))
        self.push_button.setText("Push to RV (%d)" % count)
        self.push_button.setEnabled(bool(count))
        self.new_session.setEnabled(bool(count))

    def push(self, new_session=False):
        from pathlib import Path
        rows = sorted((i.data(QtCore.Qt.UserRole) for i in self.grid.selectedItems()), key=lambda r: (r.group, version_key(r.version)))
        playable = [r for r in rows if r.movie and Path(r.movie).is_file()]
        if not playable:
            self.status.setText("No selected movies are available. Refresh the browser.")
            return
        try:
            self.load_media(list(dict.fromkeys(r.movie for r in playable)), new_session)
        except Exception as exc:
            self.status.setText("RV load failed: %s" % exc)
            return
        for row in playable:
            previous = self.seen.get(row.group, "")
            if not previous or version_key(row.version) > version_key(previous):
                self.seen[row.group] = row.version
                self.settings.setValue(self._history_key(row.group), row.version)
        self.settings.sync()
        self.apply_filters()
        self.status.setText("Loaded %d movies · %d unavailable entries skipped" % (len(playable), len(rows) - len(playable)))

    def closeEvent(self, event):
        for worker in self.workers:
            worker.cancelled.set()
        self.generation += 1
        self.thumbnails.stop()
        super().closeEvent(event)
