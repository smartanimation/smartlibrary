from __future__ import annotations

import os
from pathlib import Path

try:
    if __package__:
        from .asset_manager import Asset, AssetManager
    else:
        from asset_manager import Asset, AssetManager
except ImportError:
    from scripts.asset_manager import Asset, AssetManager


def _qt_modules():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt_modules()


class AssetManagerWindow(QtWidgets.QDialog):
    def __init__(self, manager: AssetManager | None = None, parent=None):
        super().__init__(parent)
        self.manager = manager or AssetManager()
        self.assets: list[Asset] = []
        self.setWindowTitle(f"Asset Manager - {self.manager.project_name}")
        self.resize(760, 460)
        self._build_ui()
        self.refresh_assets()

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)

        filter_layout = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search asset")
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        filter_layout.addWidget(self.search_edit)
        filter_layout.addWidget(self.refresh_btn)
        root_layout.addLayout(filter_layout)

        splitter = QtWidgets.QSplitter()
        root_layout.addWidget(splitter, 1)

        self.asset_list = QtWidgets.QListWidget()
        splitter.addWidget(self.asset_list)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        splitter.addWidget(right)

        right_layout.addWidget(QtWidgets.QLabel("Work Scenes"))
        self.work_list = QtWidgets.QListWidget()
        right_layout.addWidget(self.work_list, 1)

        right_layout.addWidget(QtWidgets.QLabel("Publish Files"))
        self.publish_list = QtWidgets.QListWidget()
        right_layout.addWidget(self.publish_list, 1)

        action_layout = QtWidgets.QHBoxLayout()
        self.open_data_btn = QtWidgets.QPushButton("Open Data")
        self.open_work_btn = QtWidgets.QPushButton("Open Work")
        self.open_publish_btn = QtWidgets.QPushButton("Open Publish")
        self.copy_path_btn = QtWidgets.QPushButton("Copy Path")
        self.open_scene_btn = QtWidgets.QPushButton("Open Scene")
        self.save_scene_btn = QtWidgets.QPushButton("Save Scene")
        self.import_btn = QtWidgets.QPushButton("Import Latest")
        action_layout.addWidget(self.open_data_btn)
        action_layout.addWidget(self.open_work_btn)
        action_layout.addWidget(self.open_publish_btn)
        action_layout.addWidget(self.copy_path_btn)
        action_layout.addWidget(self.open_scene_btn)
        action_layout.addWidget(self.save_scene_btn)
        action_layout.addWidget(self.import_btn)
        right_layout.addLayout(action_layout)

        self.status_label = QtWidgets.QLabel("")
        root_layout.addWidget(self.status_label)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.refresh_btn.clicked.connect(self.refresh_assets)
        self.asset_list.currentRowChanged.connect(self._show_current_asset)
        self.open_data_btn.clicked.connect(lambda: self._open_current("data"))
        self.open_work_btn.clicked.connect(lambda: self._open_current("work"))
        self.open_publish_btn.clicked.connect(lambda: self._open_current("publish"))
        self.copy_path_btn.clicked.connect(self._copy_selected_path)
        self.open_scene_btn.clicked.connect(self._open_selected_scene)
        self.save_scene_btn.clicked.connect(self._save_scene)
        self.import_btn.clicked.connect(self._import_latest_publish)

    def refresh_assets(self) -> None:
        self.assets = self.manager.list_assets()
        self._apply_filter()
        self.status_label.setText(f"{len(self.assets)} assets")

    def _apply_filter(self) -> None:
        text = self.search_edit.text().strip().lower()
        self.asset_list.clear()
        for asset in self.assets:
            label = f"{asset.category}/{asset.group}/{asset.name}"
            if text and text not in label.lower():
                continue
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, asset)
            self.asset_list.addItem(item)
        if self.asset_list.count():
            self.asset_list.setCurrentRow(0)

    def _current_asset(self) -> Asset | None:
        item = self.asset_list.currentItem()
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _show_current_asset(self) -> None:
        asset = self._current_asset()
        self.work_list.clear()
        self.publish_list.clear()
        if not asset:
            return

        work_files = self.manager.list_work_files(
            asset,
            extensions=["ma", "mb", "hip", "hiplc", "hipnc"],
        )
        if not work_files:
            item = QtWidgets.QListWidgetItem(f"No work scenes found under: {asset.work_dir}")
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
            self.work_list.addItem(item)

        for path in work_files:
            item = QtWidgets.QListWidgetItem(path.relative_to(asset.root).as_posix())
            item.setData(QtCore.Qt.UserRole, str(path))
            self.work_list.addItem(item)

        for path in self.manager.list_publish_files(asset):
            item = QtWidgets.QListWidgetItem(path.name)
            item.setData(QtCore.Qt.UserRole, str(path))
            self.publish_list.addItem(item)

    def _open_current(self, path_type: str) -> None:
        asset = self._current_asset()
        if not asset:
            return
        path = asset.paths()[path_type]
        path.mkdir(parents=True, exist_ok=True)
        self.manager.open_in_explorer(path)

    def _copy_selected_path(self) -> None:
        if self.work_list.currentItem() and self.work_list.currentItem().data(QtCore.Qt.UserRole):
            text = self.work_list.currentItem().data(QtCore.Qt.UserRole)
        elif self.publish_list.currentItem():
            text = self.publish_list.currentItem().data(QtCore.Qt.UserRole)
        else:
            asset = self._current_asset()
            text = str(asset.root) if asset else ""
        if text:
            QtWidgets.QApplication.clipboard().setText(text)
            self.status_label.setText(f"Copied: {text}")

    def _open_selected_scene(self) -> None:
        item = self.work_list.currentItem()
        if not item:
            self.status_label.setText("Select a work scene first")
            return
        path = item.data(QtCore.Qt.UserRole)
        try:
            open_scene_in_current_dcc(path)
            self.status_label.setText(f"Opened: {Path(path).name}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Scene Failed", str(exc))

    def _selected_work_path(self) -> str | None:
        item = self.work_list.currentItem()
        if not item:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _save_scene(self) -> None:
        asset = self._current_asset()
        if not asset:
            self.status_label.setText("Select an asset first")
            return

        selected_path = self._selected_work_path()
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Save Scene")
        msg.setText("How do you want to save the current scene?")
        overwrite_btn = msg.addButton("Overwrite", QtWidgets.QMessageBox.AcceptRole)
        next_take_btn = msg.addButton("Next Take", QtWidgets.QMessageBox.ActionRole)
        msg.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
        msg.exec()

        if msg.clickedButton() == overwrite_btn and selected_path:
            target = Path(selected_path)
        elif msg.clickedButton() == next_take_btn or msg.clickedButton() == overwrite_btn:
            target = self.manager.next_work_take_path(asset, current_path=selected_path)
        else:
            return

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            save_scene_in_current_dcc(target)
            self.status_label.setText(f"Saved: {target.name}")
            self.refresh_assets()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Scene Failed", str(exc))

    def _import_latest_publish(self) -> None:
        asset = self._current_asset()
        if not asset:
            return
        publish = self.manager.latest_publish(asset)
        if not publish:
            self.status_label.setText("No publish file found")
            return
        import_file_to_current_dcc(publish)
        self.status_label.setText(f"Imported: {publish.name}")


def save_scene_in_current_dcc(path: str | os.PathLike[str]) -> None:
    file_path = str(Path(path))
    ext = Path(file_path).suffix.lower()
    try:
        import maya.cmds as cmds

        scene_type = "mayaBinary" if ext == ".mb" else "mayaAscii"
        cmds.file(rename=file_path)
        cmds.file(save=True, type=scene_type)
        return
    except ImportError:
        pass

    try:
        import hou

        hou.hipFile.save(file_path)
        return
    except ImportError:
        pass

    raise RuntimeError("Save Scene is available inside Maya or Houdini.")


def open_scene_in_current_dcc(path: str | os.PathLike[str]) -> None:
    file_path = str(Path(path))
    try:
        import maya.cmds as cmds

        if cmds.file(query=True, modified=True):
            result = cmds.confirmDialog(
                title="Open Scene",
                message="Current scene has unsaved changes. Open selected scene?",
                button=["Open", "Cancel"],
                defaultButton="Open",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if result != "Open":
                return
        cmds.file(file_path, open=True, force=True)
        return
    except ImportError:
        pass

    try:
        import hou

        if hou.hipFile.hasUnsavedChanges():
            result = hou.ui.displayMessage(
                "Current scene has unsaved changes. Open selected scene?",
                buttons=("Open", "Cancel"),
                default_choice=0,
                close_choice=1,
            )
            if result != 0:
                return
        hou.hipFile.load(file_path, suppress_save_prompt=True)
        return
    except ImportError:
        pass

    raise RuntimeError("Open Scene is available inside Maya or Houdini.")


def import_file_to_current_dcc(path: str | os.PathLike[str]) -> None:
    file_path = str(Path(path))
    try:
        import maya.cmds as cmds

        namespace = Path(file_path).stem.replace(".", "_").replace("-", "_")
        cmds.file(
            file_path,
            i=True,
            ignoreVersion=True,
            mergeNamespacesOnClash=False,
            namespace=namespace,
        )
        return
    except ImportError:
        pass

    try:
        import hou

        ext = Path(file_path).suffix.lower()
        if ext in {".hip", ".hiplc", ".hipnc"}:
            hou.hipFile.merge(file_path)
        else:
            obj = hou.node("/obj") or hou.node("/")
            geo = obj.createNode("geo", node_name=Path(file_path).stem)
            file_sop = geo.createNode("file")
            file_sop.parm("file").set(file_path)
            geo.layoutChildren()
        return
    except ImportError:
        pass

    raise RuntimeError("This import action is available inside Maya or Houdini.")


_WINDOW = None


def show() -> AssetManagerWindow:
    global _WINDOW
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    _WINDOW = AssetManagerWindow()
    _WINDOW.show()
    return _WINDOW


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = show()
    app.exec()
