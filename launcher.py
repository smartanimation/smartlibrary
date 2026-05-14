import os
import yaml
import subprocess
import sys
import threading
import shutil
from PySide6 import QtWidgets, QtCore, QtGui, QtUiTools

# 自作モジュールのインポート
try:
    from scripts import config_creator 
except ImportError:
    import config_creator 

# --- パス設定 ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
UI_FILE_PATH = os.path.join(CURRENT_DIR, "launcherUI.ui")
PROJECTS_ROOT = os.path.join(CURRENT_DIR, "config")
SCRIPTS_DIR = os.path.join(CURRENT_DIR, "scripts")
GLOBAL_SOFT_PATH = os.path.join(PROJECTS_ROOT, "default", "software_settings.yml")

USER_DATA_DIR = os.path.join(os.environ["APPDATA"], "smartuserdata")
USER_SETTINGS_PATH = os.path.join(USER_DATA_DIR, "smartlauncher_settings.yml")

def load_yml(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"YAML Load Error: {e}")
    return {}

def save_yml(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

class SmartLauncher(QtWidgets.QMainWindow):
    setup_finished_signal = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.load_ui()
        self.project_map = {}
        self.projectroot = ""
        self.creator_win = None
        
        self.user_settings = self.load_user_settings()
        self.star_icon = self.create_symbol_icon("★", "#FFD700")
        self.empty_icon = self.create_symbol_icon("", "transparent")
        self.gear_icon = self.create_symbol_icon("⚙", "#CCCCCC", size=20)

        self.setup_custom_ui_elements()

        # UI接続
        self.ui.projectCombo.currentIndexChanged.connect(self.on_project_changed)
        if hasattr(self.ui, 'setup_button'):
            self.ui.setup_button.clicked.connect(self.run_pipeline_setup)
        if hasattr(self.ui, 'favorite_btn'):
            self.ui.favorite_btn.clicked.connect(self.toggle_favorite)

        self.ui.runbutton.clicked.connect(self.launch_selected)
        self.ui.appview.doubleClicked.connect(self.launch_selected)
        self.setup_finished_signal.connect(self._finalize_setup)

        self.setup_menus()
        self.refresh_projects()
        
        size = self.user_settings.get("last_window_size", [1000, 800])
        self.resize(size[0], size[1])

    def load_ui(self):
        loader = QtUiTools.QUiLoader()
        # --- 修正箇所: QtCore.File -> QtCore.QFile ---
        ui_file = QtCore.QFile(UI_FILE_PATH)
        if not ui_file.open(QtCore.QFile.ReadOnly): sys.exit(-1)
        self.ui = loader.load(ui_file)
        ui_file.close()
        self.setCentralWidget(self.ui.centralwidget)
        self.app_model = QtGui.QStandardItemModel()
        self.ui.appview.setModel(self.app_model)
        self.ui.appview.setIconSize(QtCore.QSize(40, 40))

    def create_symbol_icon(self, char, color_str, size=18):
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        if color_str != "transparent":
            painter.setPen(QtGui.QColor(color_str))
            font = painter.font(); font.setPixelSize(size); font.setBold(True)
            painter.setFont(font)
            painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, char)
        painter.end()
        return QtGui.QIcon(pixmap)

    def setup_custom_ui_elements(self):
        if hasattr(self.ui, 'edit_btn'):
            self.ui.edit_btn.setText("") # テキストを消去
            self.ui.edit_btn.setIcon(self.gear_icon)
            self.ui.edit_btn.setIconSize(QtCore.QSize(20, 20))
            self.ui.edit_btn.setToolTip("Edit Project Settings")
            self.ui.edit_btn.setFixedSize(32, 32)
            # スタイリッシュなボタンデザイン（マウスホバーで反応）
            self.ui.edit_btn.setStyleSheet("""
                QPushButton { border: none; background: none; }
                QPushButton:hover { background-color: #444444; border-radius: 4px; }
            """)
            self.ui.edit_btn.clicked.connect(self.open_config_creator_edit)

        if hasattr(self.ui, 'favorite_btn'):
            self.ui.favorite_btn.setFixedSize(32, 32)
            self.ui.favorite_btn.setStyleSheet("QPushButton { border: none; background: none; }")
        
        if hasattr(self.ui, 'info_label'):
            self.ui.info_label.setOpenExternalLinks(True)

    def load_user_settings(self):
        return load_yml(USER_SETTINGS_PATH) or {"favorites": [], "last_window_size": [1000, 800]}

    def refresh_projects(self):
        self.ui.projectCombo.blockSignals(True)
        current = self.ui.projectCombo.currentText()
        self.ui.projectCombo.clear()
        self.project_map.clear()
        
        all_projs = []
        if os.path.exists(PROJECTS_ROOT):
            for d in os.listdir(PROJECTS_ROOT):
                if d == "default": continue
                cfg_p = os.path.join(PROJECTS_ROOT, d, "templates_base.yml")
                if os.path.exists(cfg_p):
                    cfg_data = load_yml(cfg_p)
                    name = cfg_data.get('anchors', {}).get('project_name', d)
                    all_projs.append(name)
                    self.project_map[name] = d

        favs = self.user_settings.get("favorites", [])
        sorted_projs = sorted([p for p in all_projs if p in favs]) + sorted([p for p in all_projs if p not in favs])

        for p in sorted_projs:
            icon = self.star_icon if p in favs else self.empty_icon
            self.ui.projectCombo.addItem(icon, p)

        self.ui.projectCombo.blockSignals(False)
        if current in sorted_projs: self.ui.projectCombo.setCurrentText(current)
        self.on_project_changed()

    def on_project_changed(self):
        """プロジェクト切替時の処理 (Shot Info表示 & アプリリスト更新)"""
        self.update_favorite_button_ui()
        self.app_model.clear()
        display_name = self.ui.projectCombo.currentText()
        if not display_name: return
        folder_name = self.project_map.get(display_name)
        if not folder_name: return
        
        cfg = load_yml(os.path.join(PROJECTS_ROOT, folder_name, "templates_base.yml"))
        
        # --- 1. Shot Info (Project Info) の HTML 表示 ---
        if hasattr(self.ui, 'info_label'):
            anchors = cfg.get('anchors', {})
            self.projectroot = anchors.get('project_root', '')
            
            lines = [f"<b><span style='color: #ffffff; font-size: 14px;'>{display_name}</span></b>"]
            if 'fps' in anchors: lines.append(f"FPS: <span style='color: #aaaaaa;'>{anchors['fps']}</span>")
            res = anchors.get('resolution')
            if isinstance(res, list) and len(res) >= 2:
                lines.append(f"RES: <span style='color: #aaaaaa;'>{res[0]}x{res[1]}</span>")
            if self.projectroot:
                lines.append(f"ROOT: <a href='file:///{self.projectroot}' style='color: #55aaff; text-decoration: none;'>{self.projectroot}</a>")
            
            self.ui.info_label.setText("<br>".join(lines))
        
        self.check_project_status(self.projectroot)
        
        # --- 2. アプリリストの更新 ---
        enabled = cfg.get('enabled_softwares', [])
        master_data = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        provider = QtWidgets.QFileIconProvider()
        for soft_id in enabled:
            info = master_data.get(soft_id, {})
            item = QtGui.QStandardItem(info.get('name', soft_id.upper()))
            raw_path = info.get('path', "")
            exe_path = os.path.normpath(raw_path.replace("{project_root}", self.projectroot)) if raw_path else ""
            if exe_path and os.path.exists(exe_path):
                item.setIcon(provider.icon(QtCore.QFileInfo(exe_path)))
            else:
                item.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ApplicationIcon))
            item.setData(soft_id, QtCore.Qt.UserRole)
            self.app_model.appendRow(item)

    def launch_selected(self):
        """アプリ起動 (環境変数の確実な反映)"""
        idx = self.ui.appview.selectedIndexes()
        if not idx: return
        soft_id = idx[0].data(QtCore.Qt.UserRole)
        folder_name = self.project_map.get(self.ui.projectCombo.currentText())
        
        master_data = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        soft_info = master_data.get(soft_id, {})
        exe_p = os.path.normpath(soft_info.get('path', "").replace("{project_root}", self.projectroot))

        if not os.path.exists(exe_p):
            QtWidgets.QMessageBox.warning(self, "Error", f"Executable not found: {exe_p}")
            return

        # 環境変数の構築
        full_env = os.environ.copy()
        specific_conf_path = os.path.join(PROJECTS_ROOT, folder_name, f"software_{soft_id}.yml")
        if os.path.exists(specific_conf_path):
            spec_data = load_yml(specific_conf_path).get('softwares', {}).get(soft_id, {})
            # env_vars: 単純上書き
            for k, v in spec_data.get('env_vars', {}).items():
                full_env[str(k)] = str(v).replace("{project_root}", self.projectroot)
            # paths: 既存の変数の先頭に追加
            for k, p_list in spec_data.get('paths', {}).items():
                if not isinstance(p_list, list): continue
                formatted = [p.replace("{project_root}", self.projectroot) for p in p_list]
                existing = full_env.get(k, "")
                if existing:
                    full_env[k] = os.pathsep.join(formatted) + os.pathsep + existing
                else:
                    full_env[k] = os.pathsep.join(formatted)

        try:
            is_batch = exe_p.lower().endswith(('.bat', '.cmd'))
            subprocess.Popen(
                f'"{exe_p}"' if is_batch else [exe_p],
                env=full_env,
                cwd=self.projectroot if os.path.exists(self.projectroot) else None,
                shell=is_batch,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Launch failed: {e}")

    def delete_current_project(self):
        """プロジェクト削除 (コンフィグのみ / フォルダ含め全ての選択)"""
        display_name = self.ui.projectCombo.currentText()
        folder_name = self.project_map.get(display_name)
        if not folder_name: return
        
        cfg_dir = os.path.join(PROJECTS_ROOT, folder_name)
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Delete Project")
        msg.setIcon(QtWidgets.QMessageBox.Warning)
        msg.setText(f"プロジェクト '{display_name}' を削除しますか？")
        msg.setInformativeText(f"【重要】作業用フォルダも削除するか選択してください。\n\n作業パス: {self.projectroot}")
        
        btn_all = msg.addButton("作業フォルダ含め全て削除", QtWidgets.QMessageBox.DestructiveRole)
        btn_config = msg.addButton("コンフィグのみ削除", QtWidgets.QMessageBox.ActionRole)
        msg.addButton("キャンセル", QtWidgets.QMessageBox.RejectRole)
        
        msg.exec()
        
        try:
            if msg.clickedButton() == btn_all:
                if os.path.exists(self.projectroot):
                    shutil.rmtree(self.projectroot)
                if os.path.exists(cfg_dir):
                    shutil.rmtree(cfg_dir)
                QtWidgets.QMessageBox.information(self, "Done", "全てのデータを削除しました。")
            elif msg.clickedButton() == btn_config:
                if os.path.exists(cfg_dir):
                    shutil.rmtree(cfg_dir)
                QtWidgets.QMessageBox.information(self, "Done", "設定ファイルのみ削除しました。")
            else:
                return
            
            self.refresh_projects()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"削除中にエラーが発生しました:\n{e}")

    def open_config_creator(self):
        self.creator_win = config_creator.ConfigCreatorApp()
        self.creator_win.config_saved.connect(self.refresh_projects)
        self.creator_win.show()

    def open_config_creator_edit(self):
        folder_name = self.project_map.get(self.ui.projectCombo.currentText())
        if folder_name:
            self.creator_win = config_creator.ConfigCreatorApp(target_project=folder_name)
            self.creator_win.config_saved.connect(self.refresh_projects)
            self.creator_win.show()

    def check_project_status(self, root):
        is_ready = os.path.exists(root) if root else False
        if hasattr(self.ui, 'setup_button'):
            self.ui.setup_button.setVisible(not is_ready)

    def run_pipeline_setup(self):
        folder_name = self.project_map.get(self.ui.projectCombo.currentText())
        env = os.environ.copy()
        env["PROJECT_CONFIG_DIR"] = os.path.join(PROJECTS_ROOT, folder_name)
        script = os.path.join(SCRIPTS_DIR, "init_project.py")
        threading.Thread(target=lambda: (subprocess.run([sys.executable, script], env=env), self.setup_finished_signal.emit()), daemon=True).start()

    def setup_menus(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("FILE")
        file_menu.addAction("New Project", self.open_config_creator)
        file_menu.addAction("Delete Current Project", self.delete_current_project)
        file_menu.addAction("Refresh", self.refresh_projects)

    def toggle_favorite(self):
        project = self.ui.projectCombo.currentText()
        if not project: return
        favs = self.user_settings.get("favorites", [])
        if project in favs: favs.remove(project)
        else: favs.append(project)
        self.user_settings["favorites"] = favs
        save_yml(USER_SETTINGS_PATH, self.user_settings)
        self.refresh_projects()

    def update_favorite_button_ui(self):
        if not hasattr(self.ui, 'favorite_btn'): return
        is_fav = self.ui.projectCombo.currentText() in self.user_settings.get("favorites", [])
        self.ui.favorite_btn.setText("★" if is_fav else "☆")

    @QtCore.Slot()
    def _finalize_setup(self): self.on_project_changed()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    launcher = SmartLauncher()
    launcher.show()
    sys.exit(app.exec())