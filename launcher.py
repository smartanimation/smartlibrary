import os
import yaml
import subprocess
import sys
import threading
from PySide6 import QtWidgets, QtCore, QtGui, QtUiTools

# --- パス設定 ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
UI_FILE_PATH = os.path.join(CURRENT_DIR, "launcherUI.ui")
PROJECTS_ROOT = os.path.join(CURRENT_DIR, "config")
SCRIPTS_DIR = os.path.join(CURRENT_DIR, "scripts")
GLOBAL_SOFT_PATH = os.path.join(PROJECTS_ROOT, "default", "software_settings.yml")

# --- ユーザーデータ保存先 ---
USER_DATA_DIR = os.path.join(os.environ["APPDATA"], "smartuserdata")
USER_SETTINGS_PATH = os.path.join(USER_DATA_DIR, "smartlauncher_settings.yml")

if SCRIPTS_DIR not in sys.path:
    sys.path.append(SCRIPTS_DIR)

try:
    import config_creator
except ImportError:
    config_creator = None

# --------------------------------------------------------------------------------
# 補助関数
# --------------------------------------------------------------------------------
def load_yml(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"YAML Load Error: {e}")
    return {}

def save_yml(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception as e:
        print(f"YAML Save Error: {e}")

# --------------------------------------------------------------------------------
# Main Launcher Class
# --------------------------------------------------------------------------------
class SmartLauncher(QtWidgets.QMainWindow):
    setup_finished_signal = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.load_ui()
        self.project_map = {}
        self.projectroot = ""
        
        # 1. ユーザー設定ロード
        self.user_settings = self.load_user_settings()

        # 2. カスタムアイコンの生成
        self.star_icon = self.create_symbol_icon("★", "#FFD700")
        self.empty_icon = self.create_symbol_icon("", "transparent")
        self.gear_icon = self.create_symbol_icon("⚙", "#CCCCCC", size=20)

        # 3. UIの初期セットアップ（ボタンのアイコン化など）
        self.setup_custom_ui_elements()

        # 4. UIシグナル接続
        self.ui.projectCombo.currentIndexChanged.connect(self.on_project_changed)
        if hasattr(self.ui, 'setup_button'):
            self.ui.setup_button.clicked.connect(self.run_pipeline_setup)
        if hasattr(self.ui, 'favorite_btn'):
            self.ui.favorite_btn.clicked.connect(self.toggle_favorite)

        self.ui.runbutton.clicked.connect(self.launch_selected)
        self.ui.appview.doubleClicked.connect(self.launch_selected)
        self.setup_finished_signal.connect(self._finalize_setup)

        # 5. データ初期化
        self.setup_menus()
        self.refresh_projects()
        
        # ウィンドウ復元
        size = self.user_settings.get("last_window_size", [1000, 800])
        self.resize(size[0], size[1])

    def load_ui(self):
        loader = QtUiTools.QUiLoader()
        ui_file = QtCore.QFile(UI_FILE_PATH)
        if not ui_file.open(QtCore.QFile.ReadOnly): sys.exit(-1)
        self.ui = loader.load(ui_file)
        ui_file.close()

        self.setCentralWidget(self.ui.centralwidget)
        self.setWindowTitle("SMART LAUNCHER 1.5.0")

        self.app_model = QtGui.QStandardItemModel()
        self.ui.appview.setModel(self.app_model)
        self.ui.appview.setIconSize(QtCore.QSize(40, 40))
        self.ui.appview.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

    def create_symbol_icon(self, char, color_str, size=18):
        """指定した文字をアイコン化する共通関数"""
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        if color_str != "transparent":
            painter.setPen(QtGui.QColor(color_str))
            font = painter.font()
            font.setPixelSize(size)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, char)
        painter.end()
        return QtGui.QIcon(pixmap)
    
    def open_explorer(self, path):
        """リンク（パス）がクリックされたときにエクスプローラーで開く"""
        path = os.path.normpath(path) # パス形式をOSに合わせる
        if os.path.exists(path):
            os.startfile(path)
        else:
            QtWidgets.QMessageBox.warning(self, "Folder Not Found", f"Could not find folder:\n{path}")

    def setup_custom_ui_elements(self):
        """UI部品の見た目を調整"""
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
            self.ui.info_label.setWordWrap(True)
            self.ui.info_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
            # リンクをクリック可能にする
            self.ui.info_label.setOpenExternalLinks(False) 
            self.ui.info_label.linkActivated.connect(self.open_explorer) # クリック時の関数を接続

        if hasattr(self.ui, 'info_label'):
            self.ui.info_label.setWordWrap(True)
            self.ui.info_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
            self.ui.info_label.setOpenExternalLinks(False) 
            self.ui.info_label.linkActivated.connect(self.open_explorer)
            
            # --- [追加] 右クリックメニューの設定 ---
            self.ui.info_label.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.ui.info_label.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, pos):
        """右クリックメニューの表示"""
        menu = QtWidgets.QMenu(self)
        
        # 再セットアップアクション
        re_setup_action = menu.addAction("🔄 Force Re-Setup Project")
        
        # メニューを表示した位置で実行
        action = menu.exec(self.ui.info_label.mapToGlobal(pos))
        
        if action == re_setup_action:
            # 念のため確認ダイアログを出す
            confirm = QtWidgets.QMessageBox.question(
                self, "Confirm Re-Setup",
                "Are you sure you want to run the setup pipeline again?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if confirm == QtWidgets.QMessageBox.Yes:
                self.run_pipeline_setup()

    def load_user_settings(self):
        data = load_yml(USER_SETTINGS_PATH)
        default = {"recent_projects": [], "favorites": [], "last_window_size": [1000, 800]}
        if not data: return default
        for k, v in default.items():
            if k not in data: data[k] = v
        return data

    def refresh_projects(self):
        self.ui.projectCombo.blockSignals(True)
        current_selection = self.ui.projectCombo.currentText()
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
        sorted_projs = sorted([p for p in all_projs if p in favs]) + \
                       sorted([p for p in all_projs if p not in favs])

        for p in sorted_projs:
            icon = self.star_icon if p in favs else self.empty_icon
            self.ui.projectCombo.addItem(icon, p)

        self.ui.projectCombo.blockSignals(False)
        if current_selection in sorted_projs:
            self.ui.projectCombo.setCurrentText(current_selection)
        self.on_project_changed()

    def on_project_changed(self):
        self.update_favorite_button_ui()
        self.app_model.clear()
        display_name = self.ui.projectCombo.currentText()
        if not display_name: return

        folder_name = self.project_map.get(display_name)
        if not folder_name: return
        
        # templates_base.yml をロード
        cfg_path = os.path.join(PROJECTS_ROOT, folder_name, "templates_base.yml")
        cfg = load_yml(cfg_path)
        
        # Project Info 表示更新
        self.update_project_info_ui(cfg)

        # 1. 基礎情報の取得
        self.projectroot = cfg.get('anchors', {}).get('project_root', '')
        self.check_project_status(self.projectroot)
        
        # 2. 表示するソフトウェア情報の収集用辞書
        display_apps = {}

        # --- A. templates_base.yml 内の 'softwares' 記載を読み込む ---
        proj_specific_softs = cfg.get('softwares', {})
        for soft_id, info in proj_specific_softs.items():
            display_apps[soft_id] = info

        # --- B. 'enabled_softwares' リストにあるものをマスターから読み込む ---
        # (既に display_apps にある場合は上書きしない = プロジェクト固有設定を優先)
        master_soft_data = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        enabled_list = cfg.get('enabled_softwares', [])
        
        for soft_id in enabled_list:
            if soft_id not in display_apps:
                if soft_id in master_soft_data:
                    display_apps[soft_id] = master_soft_data[soft_id]

        # 3. appview (QStandardItemModel) への追加処理
        provider = QtWidgets.QFileIconProvider()
        
        for soft_id, info in display_apps.items():
            # 表示名はIDを大文字にしたもの（または設定があればそれを使う）
            name = info.get('name', soft_id.upper())
            item = QtGui.QStandardItem(name)
            
            # パスの解決 ({project_root} の置換)
            raw_path = info.get('path', "")
            exe_path = raw_path.format(project_root=self.projectroot) if raw_path else ""
            
            # アイコンの設定
            icon_path = info.get('icon', "")
            if icon_path and os.path.exists(icon_path):
                # 指定アイコンがある場合
                item.setIcon(QtGui.QIcon(icon_path))
            elif exe_path and os.path.exists(exe_path):
                # 実行ファイルからアイコンを抽出
                item.setIcon(provider.icon(QtCore.QFileInfo(exe_path)))
            else:
                # どちらもない場合はデフォルト
                item.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ApplicationIcon))

            # 内部データとして soft_id を保持 (起動時に使用)
            item.setData(soft_id, QtCore.Qt.UserRole)
            self.app_model.appendRow(item)

    def update_project_info_ui(self, cfg):
        if not hasattr(self.ui, 'info_label'): return
        anchors = cfg.get('anchors', {})
        lines = ["<b><span style='color: #888888;'>PROJECT INFO</span></b>"]
        
        if 'fps' in anchors:
            lines.append(f"FPS: <span style='color: #ffffff;'>{anchors['fps']}</span>")
        
        res = anchors.get('resolution')
        if isinstance(res, list) and len(res) >= 2:
            lines.append(f"Resolution: <span style='color: #ffffff;'>{res[0]} x {res[1]}</span>")
        
        if 'project_root' in anchors:
            full_path = anchors['project_root']
            if len(full_path) > 45:
                display_path = f"{full_path[:20]}...{full_path[-20:]}"
            else:
                display_path = full_path
            
            # --- [修正] パスを <a> タグで囲み、青色などでリンクっぽく見せる ---
            lines.append(f"Root: <a href='{full_path}' style='color: #55aaff; text-decoration: underline;'>{display_path}</a>")
            self.ui.info_label.setToolTip(f"Click to open in Explorer:\n{full_path}")

        self.ui.info_label.setText("<br>".join(lines))

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
        project = self.ui.projectCombo.currentText()
        is_fav = project in self.user_settings.get("favorites", [])
        self.ui.favorite_btn.setText("★" if is_fav else "☆")
        color = "#FFD700" if is_fav else "#FFFFFF"
        self.ui.favorite_btn.setStyleSheet(f"color: {color}; font-size: 18px; border: none; background: none;")

    def launch_selected(self):
        """以前のYAML形式を読み込み、.batファイルも考慮して起動"""
        idx = self.ui.appview.selectedIndexes()
        if not idx: return
        
        soft_id = idx[0].data(QtCore.Qt.UserRole).lower()
        display_name = self.ui.projectCombo.currentText()
        folder_name = self.project_map.get(display_name)
        if not folder_name: return

        # 1. 設定のロード（templates_base と software_{soft_id}.yml をマージ）
        # --- templates_base.yml から直接記載の情報を取得 ---
        cfg_path = os.path.join(PROJECTS_ROOT, folder_name, "templates_base.yml")
        cfg = load_yml(cfg_path)
        proj_soft_info = cfg.get('softwares', {}).get(soft_id, {})

        # --- マスターデータの取得 ---
        master_data = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        soft_info = master_data.get(soft_id, {}).copy()
        
        # プロジェクト固有設定があれば上書き
        soft_info.update(proj_soft_info)

        # 2. 環境変数の構築
        full_env = os.environ.copy()
        sep = os.pathsep
        
        # 固有設定ファイル (software_maya.yml等) があれば読み込み
        specific_conf_path = os.path.join(PROJECTS_ROOT, folder_name, f"software_{soft_id}.yml")
        if os.path.exists(specific_conf_path):
            conf = load_yml(specific_conf_path)
            # env_vars 処理
            env_vars = conf.get('env_vars', {})
            for key, val in env_vars.items():
                full_env[str(key)] = str(val).format(project_root=self.projectroot)
            # paths 処理
            paths_dict = conf.get('paths', {})
            for key, path_list in paths_dict.items():
                if isinstance(path_list, list):
                    formatted_paths = [p.format(project_root=self.projectroot) for p in path_list]
                    new_str = sep.join(formatted_paths)
                    existing = full_env.get(key, "")
                    full_env[key] = f"{new_str}{sep}{existing}" if existing else new_str

        # 3. 実行パスの確定
        raw_path = soft_info.get('path', "")
        if not raw_path: return
        exe_p = os.path.normpath(raw_path.format(project_root=self.projectroot))

        # 4. 起動処理 (.bat 対応)
        if os.path.exists(exe_p):
            try:
                # 拡張子が .bat または .cmd の場合
                is_batch = exe_p.lower().endswith(('.bat', '.cmd'))
                
                if is_batch:
                    # バッチファイルの場合は cmd /c を経由させ、shell=True で実行
                    subprocess.Popen(
                        f'"{exe_p}"', # パスをダブルクォートで囲む（スペース対策）
                        env=full_env,
                        cwd=self.projectroot,
                        shell=True,
                        creationflags=subprocess.CREATE_NEW_CONSOLE
                    )
                else:
                    # 通常の .exe の場合
                    subprocess.Popen(
                        [exe_p],
                        env=full_env,
                        cwd=self.projectroot,
                        creationflags=subprocess.CREATE_NEW_CONSOLE
                    )
                print(f"Launched: {exe_p} (Batch: {is_batch})")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Launch Error", str(e))
        else:
            QtWidgets.QMessageBox.warning(self, "Error", f"Path not found:\n{exe_p}")

    def closeEvent(self, event):
        self.user_settings["last_window_size"] = [self.width(), self.height()]
        save_yml(USER_SETTINGS_PATH, self.user_settings)
        super().closeEvent(event)

    def setup_menus(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("FILE")
        new_action = QtGui.QAction("New Project Config", self)
        new_action.triggered.connect(self.open_config_creator)
        file_menu.addAction(new_action)
        refresh_action = QtGui.QAction("Refresh List", self)
        refresh_action.triggered.connect(self.refresh_projects)
        file_menu.addAction(refresh_action)

    def open_config_creator(self):
        if config_creator:
            self.creator_win = config_creator.ConfigCreatorApp()
            self.creator_win.destroyed.connect(self.refresh_projects)
            self.creator_win.show()

    def open_config_creator_edit(self):
        if config_creator:
            folder_name = self.project_map.get(self.ui.projectCombo.currentText())
            if folder_name:
                self.creator_win = config_creator.ConfigCreatorApp(target_project=folder_name)
                self.creator_win.destroyed.connect(self.refresh_projects)
                self.creator_win.show()

    def check_project_status(self, project_root):
        """セットアップ状態を確認し、ボタンの表示を切り替える"""
        is_ready = os.path.exists(project_root) if project_root else False
        
        if is_ready:
            # セットアップ済みならボタンを隠す
            self.ui.setup_button.hide()
        else:
            # 未完了ならボタンを表示して注意を促す
            self.ui.setup_button.show()
            self.ui.setup_button.setText("⚠️ SETUP PROJECT")
            self.ui.setup_button.setStyleSheet("color: orange; font-weight: bold; background-color: #332200;")

    def run_pipeline_setup(self):
        folder_name = self.project_map.get(self.ui.projectCombo.currentText())
        env = os.environ.copy()
        env["PROJECT_CONFIG_DIR"] = os.path.join(PROJECTS_ROOT, folder_name)
        script = os.path.join(SCRIPTS_DIR, "init_project.py")
        threading.Thread(target=lambda: (subprocess.run([sys.executable, script], env=env), self.setup_finished_signal.emit()), daemon=True).start()

    @QtCore.Slot()
    def _finalize_setup(self):
        self.on_project_changed()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    launcher = SmartLauncher()
    launcher.show()
    sys.exit(app.exec())