"""Place Asset: published prop cards and stable background group placement."""
try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

from smartlib.apps.asset_manager.published_catalog import published_prop_cards
from smartlib.apps.common.asset_cards import configure_asset_card_list, asset_card_text, asset_icon, asset_tooltip
from smartlib.core.path_resolver import AssetIdentity


class PlaceAssetPanel(QtWidgets.QWidget):
    extract_requested = QtCore.Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.cards = []
        self.loaded = False
        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel('Choose a published prop, then place it or replace the contents of a background group.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        search = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Search published props (name / group / variant / context)')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_cards)
        search.addWidget(self.search, 1)
        reload_button = QtWidgets.QPushButton('Refresh Assets')
        reload_button.clicked.connect(self.reload_assets)
        search.addWidget(reload_button)
        layout.addLayout(search)
        self.asset_list = QtWidgets.QListWidget()
        configure_asset_card_list(self.asset_list, QtCore, QtWidgets)
        self.asset_list.setMinimumHeight(190)
        self.asset_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.asset_list.currentItemChanged.connect(self.select_asset)
        layout.addWidget(self.asset_list, 2)
        self.empty = QtWidgets.QLabel('')
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        extract = QtWidgets.QPushButton('Unpublished asset? Open Extract / Publish')
        extract.clicked.connect(self.extract_requested.emit)
        layout.addWidget(extract)
        choices = QtWidgets.QHBoxLayout()
        self.variant = QtWidgets.QComboBox()
        self.context = QtWidgets.QComboBox()
        self.version = QtWidgets.QComboBox()
        for name, widget in [('Variant', self.variant), ('Context', self.context), ('Version', self.version)]:
            choices.addWidget(QtWidgets.QLabel(name))
            choices.addWidget(widget, 1)
        layout.addLayout(choices)
        self.variant.currentTextChanged.connect(self.select_variant)
        self.context.currentTextChanged.connect(self.select_context)
        self.version.currentIndexChanged.connect(self.show_source)
        self.source = QtWidgets.QLabel('')
        self.source.setWordWrap(True)
        self.source.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.source)
        actions = QtWidgets.QHBoxLayout()
        self.new_button = QtWidgets.QPushButton('New Placement in Selected Group')
        self.new_button.clicked.connect(self.place_new)
        self.replace_button = QtWidgets.QPushButton('Replace / Update Selected Group')
        self.replace_button.clicked.connect(self.replace_selected)
        actions.addWidget(self.new_button)
        actions.addWidget(self.replace_button)
        layout.addLayout(actions)
        self.table = QtWidgets.QTreeWidget()
        self.table.setHeaderLabels(['Background group', 'Prop / Context', 'Version'])
        self.table.header().setStretchLastSection(False)
        self.table.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.table.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.itemClicked.connect(self.select_row)
        layout.addWidget(self.table, 1)
        buttons = QtWidgets.QHBoxLayout()
        refresh = QtWidgets.QPushButton('Refresh Placements')
        refresh.clicked.connect(self.refresh)
        restore = QtWidgets.QPushButton('Restore Original Geometry')
        restore.clicked.connect(self.restore_selected)
        buttons.addWidget(refresh)
        rename = QtWidgets.QPushButton('Use Asset Namespace')
        rename.setToolTip('Rename the selected Assembly reference without reloading it or changing its ID.')
        rename.clicked.connect(self.rename_selected)
        buttons.addWidget(rename)
        buttons.addWidget(restore)
        layout.addLayout(buttons)
        self.status = QtWidgets.QLabel('Select a published prop card.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.show_source()

    def selected_card(self):
        item = self.asset_list.currentItem()
        return item.data(QtCore.Qt.UserRole) if item and not item.isHidden() else None

    def identity(self):
        card = self.selected_card()
        if not card or not self.version.currentData():
            raise ValueError('Select a published prop, variant, context and version.')
        return AssetIdentity('prop', card['group'], card['asset'], self.variant.currentText())

    def reload_assets(self):
        card = self.selected_card()
        previous = ((card['group'], card['asset'], self.variant.currentText(),
                     self.context.currentText(), self.version.currentText()) if card else None)
        self.asset_list.clear()
        self.loaded = False
        try:
            self.cards = published_prop_cards(self.config)
            for card in self.cards:
                item = QtWidgets.QListWidgetItem(asset_card_text(
                    asset=card['asset'], category='prop', group=card['group'], status=card['status'],
                    description=card['description']))
                item.setData(QtCore.Qt.UserRole, card)
                item.setIcon(asset_icon(QtCore, QtGui, thumbnail=card['thumbnail'], label=card['asset']))
                item.setToolTip(asset_tooltip(asset=card['asset'], category='prop', group=card['group'],
                                             status=card['status'], extra={'Published variants': ', '.join(card['variants'])}))
                self.asset_list.addItem(item)
            self.loaded = True
            self.filter_cards()
            if previous:
                self.select_publish(*previous)
        except Exception as exc:
            self.cards = []
            self.asset_list.clear()
            self.empty.setText('Could not load published props: ' + str(exc))
            self.empty.show()

    def filter_cards(self, *_):
        query = self.search.text().strip().casefold()
        visible = 0
        for index in range(self.asset_list.count()):
            item = self.asset_list.item(index)
            card = item.data(QtCore.Qt.UserRole)
            paths = ['/'.join(['prop', card['group'], card['asset'], variant, context])
                     for variant, contexts in card['variants'].items() for context in contexts]
            hidden = bool(query and not any(query in path.casefold() for path in paths))
            item.setHidden(hidden)
            visible += not hidden
        if self.asset_list.currentItem() and self.asset_list.currentItem().isHidden():
            self.asset_list.setCurrentRow(-1)
        self.empty.setText('No matching published props.' if self.cards else
                           'No published props. Create unpublished assets in Extract / Publish, then publish an Asset Context.')
        self.empty.setVisible(not visible)

    def select_asset(self, *_):
        self.variant.clear()
        card = self.selected_card()
        if card:
            self.variant.addItems(sorted(card['variants'], key=lambda value: (value != 'default', value)))

    def select_variant(self, *_):
        self.context.clear()
        card = self.selected_card()
        contexts = card['variants'].get(self.variant.currentText(), {}) if card else {}
        self.context.addItems(sorted(contexts, key=lambda value: (value != 'anim', value)))

    def select_context(self, *_):
        self.version.clear()
        card = self.selected_card()
        if card:
            for row in card['variants'].get(self.variant.currentText(), {}).get(self.context.currentText(), []):
                self.version.addItem(row['version'], row['path'])

    def show_source(self, *_):
        path = self.version.currentData()
        card = self.selected_card()
        label = '/'.join(['prop', card['group'], card['asset'], self.variant.currentText(),
                          self.context.currentText(), self.version.currentText()]) if card and path else ''
        self.source.setText(label)
        self.source.setToolTip(str(path or ''))
        self.new_button.setEnabled(bool(path and card))
        self.replace_button.setEnabled(bool(path and card))

    def selected_group(self):
        import maya.cmds as cmds
        selected = cmds.ls(selection=True, long=True) or []
        if len(selected) != 1:
            raise ValueError('Select exactly one background group in Maya.')
        return selected[0]

    def apply_placement(self, *, new=False):
        from smartlib.dcc.maya.assembly_replacement import replace_group, place_new_group
        try:
            operation = place_new_group if new else replace_group
            record = operation(self.config, self.selected_group(), self.identity(),
                               context=self.context.currentText(), version=self.version.currentText())
            self.refresh()
            self.status.setText('Updated ' + record['target_path'] + '. Check placement, save, then Release & Pack.')
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, 'Place Asset', str(exc))

    def place_new(self):
        self.apply_placement(new=True)

    def replace_selected(self):
        self.apply_placement()

    def refresh(self):
        from smartlib.dcc.maya.assembly_replacement import list_replacements
        if not self.loaded:
            self.reload_assets()
        self.table.clear()
        try:
            for row in list_replacements():
                item = QtWidgets.QTreeWidgetItem([row['target'], row['asset']['name'] + ' / ' + row['context'], row['version']])
                item.setData(0, QtCore.Qt.UserRole, row)
                self.table.addTopLevelItem(item)
        except Exception as exc:
            self.status.setText(str(exc))

    def select_publish(self, group, asset, variant, context, version):
        for index in range(self.asset_list.count()):
            item = self.asset_list.item(index)
            card = item.data(QtCore.Qt.UserRole)
            if (card['group'], card['asset']) != (group, asset):
                continue
            if item.isHidden():
                self.search.clear()
            self.asset_list.setCurrentItem(item)
            self.variant.setCurrentIndex(self.variant.findText(variant))
            self.context.setCurrentIndex(self.context.findText(context))
            self.version.setCurrentIndex(self.version.findText(version))
            if self.version.currentIndex() >= 0:
                return True
            break
        self.version.setCurrentIndex(-1)
        self.status.setText('The saved publish is unavailable. Refresh Assets and choose an available publish explicitly.')
        return False

    def select_row(self, item, *_):
        import maya.cmds as cmds
        row = item.data(0, QtCore.Qt.UserRole)
        cmds.select(row['target'], replace=True)
        self.select_publish(row['asset']['group'], row['asset']['name'], row['asset']['variant'], row['context'], row['version'])

    def restore_selected(self):
        from smartlib.dcc.maya.assembly_replacement import restore_group
        try:
            restore_group(self.selected_group())
            self.refresh()
            self.status.setText('Original geometry restored. Save the work scene to keep this change.')
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, 'Restore Original Geometry', str(exc))

    def rename_selected(self):
        from smartlib.dcc.maya.assembly_replacement import rename_group_reference
        try:
            record = rename_group_reference(self.selected_group())
            self.status.setText('Reference namespace: ' + record['namespace'] + '. Save the scene to keep this change.')
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, 'Use Asset Namespace', str(exc))
