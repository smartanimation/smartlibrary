"""Asset Manager tab hosting the shared Retarget editor and test history."""
from types import SimpleNamespace
from .window import QtCore, QtWidgets, RetargetWindow


class RetargetTab(QtWidgets.QWidget):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.asset = None
        self.views = {}
        self.stack = QtWidgets.QStackedLayout(self)
        self.empty = QtWidgets.QLabel('Select a character to configure and test Retarget.')
        self.stack.addWidget(self.empty)

    def set_asset(self, asset):
        self.asset = asset
        if self.isVisible():
            self.activate()

    def showEvent(self, event):
        super().showEvent(event)
        self.activate()

    def activate(self):
        asset = self.asset
        if not asset or asset.category.lower() not in {'ch','cha','character','characters'}:
            self.stack.setCurrentWidget(self.empty)
            return
        key = str(asset.root)
        if key not in self.views:
            manager = SimpleNamespace(paths=self.manager.paths, config_dir=self.manager.config_dir,
                                      list_assets=lambda: [asset])
            view = RetargetWindow(manager, asset, parent=self)
            view.setWindowFlags(QtCore.Qt.Widget)
            self.views[key] = view
            self.stack.addWidget(view)
        self.stack.setCurrentWidget(self.views[key])
        self.views[key].refresh_tests()

    def can_close(self):
        for view in self.views.values():
            if view.process is not None:
                view.state.setText('Cancel the running Motion Test before closing Asset Manager.')
                return False
        return all(view.confirm_discard() for view in self.views.values())
