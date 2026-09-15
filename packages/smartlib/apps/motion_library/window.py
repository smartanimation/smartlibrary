"""Maya entry point: show(config_dir=...). Work must be saved before publishing."""
from pathlib import Path
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide2 import QtWidgets
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.path_resolver import AssetIdentity
from .service import MotionLibraryService


class MotionClipWindow(QtWidgets.QDialog):
    def __init__(self, config_dir, parent=None):
        super().__init__(parent)
        self.service=MotionLibraryService(ProjectConfig(config_dir))
        self.setWindowTitle('Motion Clip Publish');self.resize(650,420)
        layout=QtWidgets.QFormLayout(self)
        self.fields={}
        for key,value in [('category','character'),('group','mob'),('asset','MBGm'),('variant','default'),('clip','sit_idle_01'),('skeleton_root',''),('placement_anchor','')]:
            edit=QtWidgets.QLineEdit(value);layout.addRow(key,edit);self.fields[key]=edit
        try:
            import maya.cmds as c
            current=Path(c.file(q=True,sceneName=True))
            if current.parent.parent.name == 'maya' and 'motion' in current.parts:
                self.fields['clip'].setText(current.parent.name)
        except ImportError:
            pass
        pick=QtWidgets.QPushButton('Use Selected Joint as Skeleton Root / Anchor')
        pick.clicked.connect(self.pick);layout.addRow(pick)
        self.motion=QtWidgets.QComboBox();self.motion.addItems(['Choose root motion','stationary','in_place','locomotion']);layout.addRow('Root motion',self.motion)
        self.forward=QtWidgets.QComboBox();self.forward.addItems(['Choose forward axis','+Z','-Z','+X','-X']);layout.addRow('Forward axis',self.forward)
        self.loop=QtWidgets.QCheckBox();layout.addRow('Loop',self.loop)
        self.status=QtWidgets.QLabel('Open a registered motion Work scene. Playback range is the publish range.');self.status.setWordWrap(True);layout.addRow(self.status)
        button=QtWidgets.QPushButton('Publish Clip');button.clicked.connect(self.publish);layout.addRow(button)

    def pick(self):
        import maya.cmds as c
        selection=c.ls(selection=True,type='joint',long=True) or []
        if len(selection)==1:
            self.fields['skeleton_root'].setText(selection[0]);self.fields['placement_anchor'].setText(selection[0])

    def publish(self):
        try:
            import maya.cmds as c
            from smartlib.dcc.maya.motion_clip import inspect_scene,export_fbx
            v={k:edit.text().strip() for k,edit in self.fields.items()}
            metadata=inspect_scene(v['skeleton_root'],v['placement_anchor'])
            metadata.update(root_motion=self.motion.currentText(),forward_axis=self.forward.currentText(),loop=self.loop.isChecked())
            path=self.service.publish(AssetIdentity(v['category'],v['group'],v['asset'],v['variant']),v['clip'],
                c.file(q=True,sceneName=True),metadata,export_fbx)
            self.status.setText('Published: '+str(path))
        except Exception as exc:QtWidgets.QMessageBox.warning(self,'Clip Publish',str(exc))


_WINDOW=None
def show(config_dir=None):
    global _WINDOW
    from smartlib.core.config_loader import current_project_config
    from smartlib.core.qt import maya_main_window
    cfg=config_dir or current_project_config().config_dir
    _WINDOW=MotionClipWindow(cfg,maya_main_window(QtWidgets));_WINDOW.show()
    return _WINDOW
