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
    asset_sync_signal = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Launcher")
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
            self.ui.setup_button.setText("⚠️ SETUP PROJECT")
            self.ui.setup_button.setStyleSheet("color: orange; font-weight: bold; background-color: #332200;")
            self.ui.setup_button.clicked.connect(self.run_pipeline_setup)
        if hasattr(self.ui, 'favorite_btn'):
            self.ui.favorite_btn.clicked.connect(self.toggle_favorite)

        self.ui.runbutton.clicked.connect(self.launch_selected)
        self.ui.appview.doubleClicked.connect(self.launch_selected)
        self.setup_finished_signal.connect(self._finalize_setup)
        self.asset_sync_signal.connect(self._show_asset_sync_status)

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
            
            #lines = [f"<b><span style='color: #ffffff; font-size: 14px;'>{display_name}</span></b>"]
            lines = []
            if 'fps' in anchors: lines.append(f"FPS: <span style='color: #aaaaaa;'>{anchors['fps']}</span>")
            res = anchors.get('resolution')
            if isinstance(res, list) and len(res) >= 2:
                lines.append(f"RES: <span style='color: #aaaaaa;'>{res[0]}x{res[1]}</span>")
            if self.projectroot:
                lines.append(f"ROOT: <a href='file:///{self.projectroot}' style='color: #55aaff; text-decoration: none;'>{self.projectroot}</a>")
            
            self.ui.info_label.setText("<br>".join(lines))
        
        self.check_project_status(self.projectroot)
        self.check_asset_sheet_cache(folder_name)
        
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
            #else:
            #    item.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ApplicationIcon))
            item.setData(soft_id, QtCore.Qt.UserRole)
            self.app_model.appendRow(item)

    def check_asset_sheet_cache(self, folder_name):
        cfg_dir = os.path.join(PROJECTS_ROOT, folder_name)
        base_cfg = load_yml(os.path.join(cfg_dir, "templates_base.yml"))
        sheet_id = (base_cfg.get("google_sheets") or {}).get("asset_list_id")
        if not sheet_id:
            self.asset_sync_signal.emit("Asset sheet: not configured")
            return

        credentials = (
            os.environ.get("CREDENTIALS_PATH")
            or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            or os.environ.get("CREDENTIALS_DIR")
        )
        if credentials:
            credentials = credentials.strip().strip('"')
        if not credentials:
            cache_path = os.path.join(cfg_dir, ".cache", "asset_list.json")
            if os.path.exists(cache_path):
                self.asset_sync_signal.emit("Asset sheet: using cache")
            else:
                self.asset_sync_signal.emit("Asset sheet: credentials not set")
            return

        script = os.path.join(SCRIPTS_DIR, "sync_asset_sheet.py")
        threading.Thread(
            target=self._sync_asset_sheet_worker,
            args=(cfg_dir, credentials, script),
            daemon=True,
        ).start()

    def _sync_asset_sheet_worker(self, cfg_dir, credentials, script):
        env = os.environ.copy()
        env["PROJECT_CONFIG_DIR"] = cfg_dir
        if os.path.isdir(credentials):
            env["CREDENTIALS_DIR"] = credentials
        else:
            env["CREDENTIALS_PATH"] = credentials

        try:
            result = subprocess.run(
                [sys.executable, script, "--config-dir", cfg_dir, "--credentials", credentials],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as e:
            self.asset_sync_signal.emit(f"Asset sheet: sync failed ({e})")
            return

        output = (result.stdout or result.stderr or "").strip().splitlines()
        last_line = output[-1] if output else ""
        if result.returncode == 0:
            if "Up to date" in last_line:
                self.asset_sync_signal.emit("Asset sheet: up to date")
            elif "Synced" in last_line:
                self.asset_sync_signal.emit(last_line.replace(str(cfg_dir), "cache"))
            else:
                self.asset_sync_signal.emit("Asset sheet: checked")
        else:
            self.asset_sync_signal.emit(f"Asset sheet: sync failed ({last_line})")

    @QtCore.Slot(str)
    def _show_asset_sync_status(self, message):
        if hasattr(self.ui, "info_label"):
            current = self.ui.info_label.text()
            lines = current.split("<br>") if current else []
            lines = [line for line in lines if not line.startswith("ASSETS:")]
            lines.append(f"ASSETS: <span style='color: #aaaaaa;'>{message}</span>")
            self.ui.info_label.setText("<br>".join(lines))

    def launch_selected(self):
        """アプリ起動：個別設定のパスを最優先し、batはクリーンに起動する"""
        idx = self.ui.appview.selectedIndexes()
        if not idx: return
        soft_id = idx[0].data(QtCore.Qt.UserRole)
        display_project = self.ui.projectCombo.currentText()
        folder_name = self.project_map.get(display_project)
        if not folder_name: return

        # --- 1. パスの決定 (個別設定を最優先) ---
        # プロジェクト固有の software_xxx.yml をロード
        specific_conf_path = os.path.join(PROJECTS_ROOT, folder_name, f"software_{soft_id}.yml")
        spec_data = load_yml(specific_conf_path)
        
        # マスターデータをロード
        master_data = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        master_info = master_data.get(soft_id, {})

        # 個別設定のpathがあればそれを使う、なければマスターを使う
        raw_exe_path = spec_data.get('path') or master_info.get('path', "")
        
        if not raw_exe_path:
            QtWidgets.QMessageBox.warning(self, "Error", f"Executable path not defined for: {soft_id}")
            return

        exe_p = os.path.normpath(raw_exe_path.replace("{project_root}", self.projectroot))

        if not os.path.exists(exe_p):
            QtWidgets.QMessageBox.warning(self, "Error", f"Executable not found: {exe_p}")
            return

        # --- 2. 起動準備 ---
        is_batch = exe_p.lower().endswith(('.bat', '.cmd'))
        full_env = os.environ.copy()
        full_env["PROJECT_CONFIG_DIR"] = os.path.join(PROJECTS_ROOT, folder_name)
        full_env["SMARTLIBRARY_ROOT"] = CURRENT_DIR

        python_paths = [CURRENT_DIR]
        existing_pythonpath = full_env.get("PYTHONPATH", "")
        if existing_pythonpath:
            python_paths.append(existing_pythonpath)
        full_env["PYTHONPATH"] = os.pathsep.join(python_paths)

        for k, v in spec_data.get('env_vars', {}).items():
            full_env[str(k)] = str(v).replace("{project_root}", self.projectroot)

        for k, p_list in spec_data.get('paths', {}).items():
            if isinstance(p_list, list):
                formatted = [p.replace("{project_root}", self.projectroot) for p in p_list]
                existing = full_env.get(k, "")
                full_env[str(k)] = os.pathsep.join(formatted) + (os.pathsep + existing if existing else "")

        try:
            if is_batch:
                # BATファイル：環境変数を一切渡さず、OS標準の環境で実行
                print(f"[LAUNCH] Batch Mode (Clean Env): {exe_p}")
                subprocess.Popen(
                    f'"{exe_p}"',
                    cwd=os.path.dirname(exe_p),
                    shell=True,
                    env=full_env,
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                    # env引数を渡さないことで現在のOS環境をそのまま使用
                )
            else:
                # EXEファイル：環境変数を構築して実行
                print(f"[LAUNCH] EXE Mode (Custom Env): {exe_p}")
                full_env = os.environ.copy()
                full_env["PROJECT_CONFIG_DIR"] = os.path.join(PROJECTS_ROOT, folder_name)
                full_env["SMARTLIBRARY_ROOT"] = CURRENT_DIR

                python_paths = [CURRENT_DIR]
                existing_pythonpath = full_env.get("PYTHONPATH", "")
                if existing_pythonpath:
                    python_paths.append(existing_pythonpath)
                full_env["PYTHONPATH"] = os.pathsep.join(python_paths)
                
                # env_vars の反映
                for k, v in spec_data.get('env_vars', {}).items():
                    full_env[str(k)] = str(v).replace("{project_root}", self.projectroot)
                
                # paths の反映
                for k, p_list in spec_data.get('paths', {}).items():
                    if isinstance(p_list, list):
                        formatted = [p.replace("{project_root}", self.projectroot) for p in p_list]
                        existing = full_env.get(k, "")
                        full_env[str(k)] = os.pathsep.join(formatted) + (os.pathsep + existing if existing else "")

                subprocess.Popen(
                    [exe_p],
                    env=full_env,
                    cwd=self.projectroot if os.path.exists(self.projectroot) else None,
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
        
        # 1. New Project
        new_action = file_menu.addAction("New Project", self.open_config_creator)
        new_action.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileIcon))
        new_action.setShortcut("Ctrl+N")
        
        # 2. Run Pipeline Setup
        # 直接 self.run_pipeline_setup を指定します。
        # すでに内部でプロジェクト名の取得などの処理が含まれているため、これで動作します。
        setup_action = file_menu.addAction("Run Pipeline Setup", self.run_pipeline_setup)
        setup_action.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
        setup_action.setToolTip("Execute init_project.py for the current project")
        setup_action.setShortcut("Ctrl+R")
        
        file_menu.addSeparator()
        
        # 3. Delete Current Project
        del_action = file_menu.addAction("Delete Current Project", self.delete_current_project)
        del_action.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TrashIcon))
        
        # 4. Refresh
        ref_action = file_menu.addAction("Refresh", self.refresh_projects)
        ref_action.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload))
        ref_action.setShortcut("F5")

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
