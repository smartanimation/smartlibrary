import os
import yaml
import subprocess
import sys
from PySide6 import QtWidgets, QtCore, QtGui

# --- パス設定 ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_ROOT = os.path.join(CURRENT_DIR, "config")

def load_yml(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"Error loading YAML {path}: {e}")
            return {}
    return {}

class AppItemDelegate(QtWidgets.QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(55)
        return size

class SmartLauncher(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SMART LAUNCHER 1.0.0")
        self.setFixedSize(400, 580)
        self.project_map = {}
        self.setup_style()
        self.setup_ui()
        self.refresh_projects()

    def setup_style(self):
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; font-family: 'Segoe UI', sans-serif; }
            QComboBox { background-color: #333333; border: 1px solid #444444; padding: 5px; border-radius: 4px; font-size: 16px; }
            QListWidget { background-color: #252525; border: 1px solid #333333; border-radius: 6px; outline: none; }
            QListWidget::item { padding: 10px; border-bottom: 1px solid #2d2d2d; font-size: 15px; }
            QListWidget::item:selected { background-color: #0078d4; color: white; border-radius: 4px; }
            QPushButton#runBtn { background-color: #2d2d2d; color: #4a9eff; border: 1px solid #3d3d3d; border-radius: 8px; font-size: 24px; font-weight: bold; }
            QPushButton#runBtn:hover { background-color: #333333; color: #70b5ff; }
            QPushButton#refreshBtn { background-color: #333333; border-radius: 4px; font-size: 18px; }
        """)

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 25, 20, 20)
        layout.setSpacing(15)

        proj_layout = QtWidgets.QHBoxLayout()
        proj_title = QtWidgets.QLabel("Project:")
        proj_title.setStyleSheet("font-size: 28px; font-weight: 400;")
        self.proj_combo = QtWidgets.QComboBox()
        self.proj_combo.currentIndexChanged.connect(self.on_project_changed)
        refresh_btn = QtWidgets.QPushButton("↺")
        refresh_btn.setObjectName("refreshBtn")
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.clicked.connect(self.refresh_projects)
        proj_layout.addWidget(proj_title)
        proj_layout.addWidget(self.proj_combo, 1)
        proj_layout.addWidget(refresh_btn)
        layout.addLayout(proj_layout)

        layout.addWidget(QtWidgets.QLabel("Application"))
        self.app_list = QtWidgets.QListWidget()
        self.app_list.setItemDelegate(AppItemDelegate())
        self.app_list.setIconSize(QtCore.QSize(32, 32)) # アイコンサイズを指定
        self.app_list.itemDoubleClicked.connect(self.launch_selected)
        layout.addWidget(self.app_list)

        self.run_button = QtWidgets.QPushButton("Run Application")
        self.run_button.setObjectName("runBtn")
        self.run_button.setFixedHeight(60)
        self.run_button.clicked.connect(self.launch_selected)
        layout.addWidget(self.run_button)

    def refresh_projects(self):
        self.proj_combo.clear()
        self.project_map.clear()
        if not os.path.exists(PROJECTS_ROOT): return
        for folder in os.listdir(PROJECTS_ROOT):
            folder_path = os.path.join(PROJECTS_ROOT, folder)
            config_path = os.path.join(folder_path, "templates_base.yml")
            if os.path.isdir(folder_path) and os.path.exists(config_path):
                base_cfg = load_yml(config_path)
                display_name = base_cfg.get('anchors', {}).get('project_name', folder)
                self.project_map[display_name] = folder
                self.proj_combo.addItem(display_name)

    def on_project_changed(self):
        """プロジェクトが選ばれたらソフト一覧を更新"""
        self.app_list.clear()
        display_name = self.proj_combo.currentText()
        if not display_name: return

        folder_name = self.project_map.get(display_name)
        config_path = os.path.join(PROJECTS_ROOT, folder_name, "templates_base.yml")
        base_cfg = load_yml(config_path)
        project_root = base_cfg.get('anchors', {}).get('project_root', '')
        softwares = base_cfg.get('softwares', {})

        # OSのファイルアイコンを取得するためのプロバイダー
        icon_provider = QtWidgets.QFileIconProvider()

        for soft_name, soft_info in softwares.items():
            item = QtWidgets.QListWidgetItem(soft_name.upper())
            
            soft_exe = ""
            icon_path = ""
            
            # YAMLのデータ形式を解析
            if isinstance(soft_info, dict):
                soft_exe = soft_info.get('path', "").format(project_root=project_root)
                icon_path = soft_info.get('icon', "").format(project_root=project_root)
            else:
                soft_exe = soft_info.format(project_root=project_root)

            # --- アイコン設定ロジック ---
            q_icon = None
            
            # 1. YAMLでアイコン画像が指定されている場合
            if icon_path and os.path.exists(icon_path):
                q_icon = QtGui.QIcon(icon_path)
            
            # 2. 指定がない、または画像が見つからない場合、exe本体から取得
            if not q_icon and soft_exe and os.path.exists(soft_exe):
                file_info = QtCore.QFileInfo(soft_exe)
                q_icon = icon_provider.icon(file_info)
            
            # 3. それでもダメならOS標準のPCアイコン
            if not q_icon:
                q_icon = self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
            
            item.setIcon(q_icon)
            self.app_list.addItem(item)

    def launch_selected(self):
        selected = self.app_list.currentItem()
        if not selected: return
        
        soft_key = selected.text().lower()
        display_name = self.proj_combo.currentText()
        folder_name = self.project_map.get(display_name)
        project_config_dir = os.path.join(PROJECTS_ROOT, folder_name)
        
        base_cfg = load_yml(os.path.join(project_config_dir, 'templates_base.yml'))
        soft_cfg = load_yml(os.path.join(project_config_dir, f'software_{soft_key}.yml'))

        anchors = base_cfg.get('anchors', {})
        project_root = anchors.get('project_root', '')
        soft_data = base_cfg.get('softwares', {}).get(soft_key)

        # 実行ファイルパスの取得
        if isinstance(soft_data, dict):
            soft_exe = soft_data.get('path')
        else:
            soft_exe = soft_data

        if not soft_exe or not os.path.exists(soft_exe):
            QtWidgets.QMessageBox.warning(self, "Error", f"Executable not found:\n{soft_exe}")
            return

        env = os.environ.copy()
        env["PROJECT_CONFIG_DIR"] = project_config_dir
        
        if soft_cfg:
            for k, v in soft_cfg.get('env_vars', {}).items(): env[k] = str(v)
            for k, v in soft_cfg.get('paths', {}).items():
                p_list = [p.format(project_root=project_root) for p in v]
                env[k] = os.pathsep.join(p_list) + (os.pathsep + env.get(k, "") if env.get(k) else "")

        subprocess.Popen([soft_exe], env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    launcher = SmartLauncher()
    launcher.show()
    sys.exit(app.exec())