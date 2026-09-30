import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from smartlib.apps.review_build_manager.input_tree import InputTree, QtCore, QtWidgets


def populate(tree):
    tree.blockSignals(True)
    tree.setRowCount(0)
    tree.set_hierarchy([
        dict(type='rig', cast_key='DLI_main', parent_keys=[]),
        dict(type='animation_curve', cast_key='DLI_main', parent_keys=['DLI_main']),
        dict(type='rig', cast_key='BG', parent_keys=[]),
        dict(type='set_dress', cast_key='desk', parent_keys=['BG']),
        dict(type='animation_curve', cast_key='JIN_main', parent_keys=['JIN_main']),
        dict(type='set_dress', cast_key='shared', parent_keys=['BG', 'DLI_main']),
    ])
    for row in range(6):
        tree.insertRow(row)
        item = QtWidgets.QTableWidgetItem()
        item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.Checked)
        item.setData(QtCore.Qt.UserRole, row)
        tree.setItem(row, 0, item)
        tree.setItem(row, 2, QtWidgets.QTableWidgetItem('name'))
        combo = QtWidgets.QComboBox()
        combo.addItems(['v001', 'v002'])
        combo.setProperty('content_row', row)
        tree.setCellWidget(row, 5, combo)
    tree.blockSignals(False)


def test_real_tree_parentage_selection_signals_versions_and_collapse():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    tree = InputTree(8)
    populate(tree)
    assert tree.topLevelItemCount() == 4
    rig, bg = tree.topLevelItem(0), tree.topLevelItem(1)
    assert rig.childCount() == bg.childCount() == 1
    assert rig.child(0).text(2) == 'DLI_main'
    assert bg.child(0).text(2) == 'desk'
    assert rig.isExpanded()
    bg.child(0).setSelected(True)
    assert tree.selected_input_rows() == [3]  # NOT sibling row zero.
    changes = []
    tree.cellChanged.connect(lambda cell: changes.append((cell.row(), cell.column(), cell.data(QtCore.Qt.UserRole))))
    tree.item(3, 0).setCheckState(QtCore.Qt.Unchecked)
    assert changes[-1] == (3, 0, 3)
    widget = tree.cellWidget(3, 5)
    widget.setCurrentIndex(1)
    assert tree.itemWidget(bg.child(0), 5).currentText() == 'v002'
    assert widget.property('content_row') == 3
    rig.setExpanded(False)
    populate(tree)
    assert not tree.topLevelItem(0).isExpanded()
    assert tree.topLevelItem(1).isExpanded()
    tree.close()
