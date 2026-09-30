"""Input tree with stable flat coordinates for the existing input editors.

Tree model row numbers are sibling-relative; never use them as content indices.
"""
try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets


class InputCell:
    def __init__(self, tree, item, column):
        self.tree, self.item, self.col = tree, item, column

    def row(self):
        return self.tree._rows.index(self.item)

    def column(self):
        return self.col

    def data(self, role):
        return self.item.data(self.col, role)

    def setData(self, role, value):
        self.item.setData(self.col, role, value)

    def text(self):
        return self.item.text(self.col)

    def setText(self, text):
        if self.col == 2 and self.item.parent() is not None:
            definition = self.tree._definitions[self.row()]
            text = str(definition.get('cast_key') or definition.get('name') or '')
        self.item.setText(self.col, text)

    def checkState(self):
        return self.item.checkState(self.col)

    def setCheckState(self, state):
        self.item.setCheckState(self.col, state)

    def setForeground(self, value):
        self.item.setForeground(self.col, value)

    def setToolTip(self, text):
        self.item.setToolTip(self.col, text)


class InputTree(QtWidgets.QTreeWidget):
    cellChanged = QtCore.Signal(object)

    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.setColumnCount(columns)
        self.setRootIsDecorated(True)
        self.setTreePosition(2)  # Expand/collapse beside the asset name, not Use.
        self.setIndentation(20)
        self._rows = []
        self._definitions = []
        self._parents = {}
        self._expanded = {}
        self.itemChanged.connect(lambda item, column: self.cellChanged.emit(InputCell(self, item, column)))

    def set_hierarchy(self, rows):
        self._definitions = rows

    def setRowCount(self, count):
        if count != 0:
            raise ValueError('InputTree only supports clearing and appending rows')
        for item in self._rows:
            if item.childCount():
                self._expanded[item.data(2, QtCore.Qt.UserRole)] = item.isExpanded()
        self._rows = []
        self._parents = {}
        self.clear()

    def rowCount(self):
        return len(self._rows)

    def insertRow(self, row):
        if row != len(self._rows):
            raise ValueError('Input rows must be appended in dependency order')
        definition = self._definitions[row]
        keys = definition.get('parent_keys') or []
        parent = self._parents.get(keys[0]) if len(keys) == 1 else None
        item = QtWidgets.QTreeWidgetItem(parent if parent is not None else self)
        self._rows.append(item)
        key = str(definition.get('cast_key') or definition.get('name') or '')
        if definition.get('type') in {'rig', 'usd'}:
            self._parents[key] = item
            item.setData(2, QtCore.Qt.UserRole, key)
            item.setExpanded(self._expanded.get(key, True))

    def setRowHeight(self, row, height):
        self._rows[row].setSizeHint(0, QtCore.QSize(0, height))

    def setItem(self, row, column, cell):
        item = self._rows[row]
        if column == 0:
            item.setFlags(cell.flags())
        for role in (QtCore.Qt.DisplayRole, QtCore.Qt.DecorationRole,
                     QtCore.Qt.ToolTipRole, QtCore.Qt.CheckStateRole,
                     QtCore.Qt.ForegroundRole, QtCore.Qt.TextAlignmentRole,
                     QtCore.Qt.UserRole, QtCore.Qt.UserRole + 1):
            value = cell.data(role)
            if value is not None:
                item.setData(column, role, value)
        if column == 2:
            definition = self._definitions[row]
            if len(definition.get('parent_keys') or []) == 1 and item.parent() is not None:
                item.setText(column, str(definition.get('cast_key') or definition.get('name') or ''))

    def item(self, row, column):
        return InputCell(self, self._rows[row], column)

    def setCellWidget(self, row, column, widget):
        self.setItemWidget(self._rows[row], column, widget)

    def cellWidget(self, row, column):
        return self.itemWidget(self._rows[row], column)

    def selected_input_rows(self):
        return [self._rows.index(item) for item in self.selectedItems()]

    def horizontalHeader(self):
        return self.header()

    def setHorizontalHeaderLabels(self, labels):
        self.setHeaderLabels(labels)
