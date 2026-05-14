import os
import yaml
import sys
import shutil
from PySide6 import QtWidgets, QtCore, QtGui

# パス設定
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_ROOT = os.path.normpath(os.path.join(CURRENT_DIR, "..", "config"))
DEFAULT_DIR = os.path.join(PROJECTS_ROOT, "default")
GLOBAL_SOFT_PATH = os.path.join(DEFAULT_DIR, "software_settings.yml")

def load_yml(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"YAML Read Error: {e}")
    return {}

def save_yml(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

class ConfigCreatorApp(QtWidgets.QMainWindow):
    config_saved = QtCore.Signal()

    def __init__(self, target_project=None):
        super().__init__()
        self.setWindowTitle("Project Config Creator - Layout Split")
        self.setMinimumWidth(1100); self.setMinimumHeight(900)
        
        self.target_project = target_project
        self.software_configs = {} # sid をキーに {env_vars: {}, paths: {}} を保持

        self.setup_ui()

        if self.target_project:
            self.load_project_config(self.target_project)
        else:
            self.init_ui_from_default()

    def setup_ui(self):
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        # プロジェクト基本情報
        form = QtWidgets.QFormLayout()
        self.name_input = QtWidgets.QLineEdit()
        self.path_input = QtWidgets.QLineEdit()
        self.browse_btn = QtWidgets.QPushButton("Browse")
        self.browse_btn.clicked.connect(self.browse_path)
        path_layout = QtWidgets.QHBoxLayout()
        path_layout.addWidget(self.path_input); path_layout.addWidget(self.browse_btn)
        form.addRow("Project Code:", self.name_input)
        form.addRow("Base Directory:", path_layout)
        main_layout.addLayout(form)

        self.tabs = QtWidgets.QTabWidget()
        self.soft_tab = self.setup_software_tab()
        
        # 他のタブ（既存のまま）
        self.anchors_table = self.create_table_page("Anchors", ["Key", "Value"])
        self.shot_depts_list = self.create_list_page("Shot Depts")
        self.asset_depts_list = self.create_list_page("Asset Depts")
        self.template_table = self.create_table_page("Templates", ["Key", "Path Value"])

        self.tabs.addTab(self.soft_tab, "Softwares")
        self.tabs.addTab(self.anchors_table["widget"], "Anchors")
        self.tabs.addTab(self.shot_depts_list["widget"], "Shot Depts")
        self.tabs.addTab(self.asset_depts_list["widget"], "Asset Depts")
        self.tabs.addTab(self.template_table["widget"], "Templates")
        main_layout.addWidget(self.tabs)

        self.save_btn = QtWidgets.QPushButton("SAVE CONFIG")
        self.save_btn.setFixedHeight(50)
        self.save_btn.setStyleSheet("background-color: #2d5a27; color: white; font-weight: bold;")
        self.save_btn.clicked.connect(self.save_config)
        main_layout.addWidget(self.save_btn)

    def setup_software_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        
        # --- 左側：上下分割リスト ---
        left_layout = QtWidgets.QVBoxLayout()
        
        # 上段：Global
        left_layout.addWidget(QtWidgets.QLabel("<b>Global Software List (Master)</b>"))
        self.global_list = QtWidgets.QListWidget()
        left_layout.addWidget(self.global_list)
        
        # 中央：追加ボタン
        self.add_to_proj_btn = QtWidgets.QPushButton("▼ Add to Project ▼")
        self.add_to_proj_btn.clicked.connect(self.add_to_selected)
        left_layout.addWidget(self.add_to_proj_btn)
        
        # 下段：Project Selected
        left_layout.addWidget(QtWidgets.QLabel("<b>Project Custom / Enabled</b>"))
        self.selected_list = QtWidgets.QListWidget()
        self.selected_list.currentItemChanged.connect(self.on_soft_selection_changed)
        left_layout.addWidget(self.selected_list)
        
        # 下段操作ボタン
        sel_btns = QtWidgets.QHBoxLayout()
        self.rem_soft_btn = QtWidgets.QPushButton("▲ Remove ▲")
        self.rem_soft_btn.clicked.connect(self.remove_from_selected)
        self.add_custom_exe_btn = QtWidgets.QPushButton("+ Add Custom Exe/Bat")
        self.add_custom_exe_btn.clicked.connect(self.add_custom_software)
        sel_btns.addWidget(self.rem_soft_btn); sel_btns.addWidget(self.add_custom_exe_btn)
        left_layout.addLayout(sel_btns)

        # --- 右側：環境変数エディタ (既存ロジックを維持) ---
        right_layout = QtWidgets.QVBoxLayout()
        self.env_tree = QtWidgets.QTreeWidget()
        self.env_tree.setColumnCount(2)
        self.env_tree.setHeaderLabels(["Variable / Path", "Value"])
        self.env_tree.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked)
        
        tree_btns = QtWidgets.QHBoxLayout()
        add_var_btn = QtWidgets.QPushButton("+ Var"); add_path_btn = QtWidgets.QPushButton("+ Path")
        del_node_btn = QtWidgets.QPushButton("- Del")
        add_var_btn.clicked.connect(self.add_tree_var); add_path_btn.clicked.connect(self.add_tree_path); del_node_btn.clicked.connect(self.remove_tree_node)
        tree_btns.addWidget(add_var_btn); tree_btns.addWidget(add_path_btn); tree_btns.addStretch(); tree_btns.addWidget(del_node_btn)
        
        right_layout.addWidget(QtWidgets.QLabel("Individual Env Editor:"))
        right_layout.addWidget(self.env_tree)
        right_layout.addLayout(tree_btns)
        
        layout.addLayout(left_layout, 1); layout.addLayout(right_layout, 2)
        return page

    def on_soft_selection_changed(self, current, previous):
        """下段のリスト選択が変わったときに環境変数を表示"""
        if previous:
            self._save_tree_to_memory(previous.text())
            
        if not current:
            self.env_tree.clear()
            return
            
        soft_id = current.text()
        if soft_id not in self.software_configs:
            proj_dir = os.path.join(PROJECTS_ROOT, self.name_input.text().strip())
            spec_path = os.path.join(proj_dir, f"software_{soft_id}.yml")
            # 既存、なければデフォルトからロード
            data = load_yml(spec_path) if os.path.exists(spec_path) else load_yml(os.path.join(DEFAULT_DIR, f"software_{soft_id}.yml"))
            self.software_configs[soft_id] = data
        
        self._populate_tree(self.software_configs[soft_id])

    def add_to_selected(self):
        """上段から下段へコピー"""
        curr = self.global_list.currentItem()
        if not curr: return
        sid = curr.text()
        # 重複チェック
        for i in range(self.selected_list.count()):
            if self.selected_list.item(i).text() == sid: return
        self.selected_list.addItem(sid)

    def remove_from_selected(self):
        """下段から除外"""
        row = self.selected_list.currentRow()
        if row >= 0: self.selected_list.takeItem(row)

    def _populate_tree(self, conf):
        self.env_tree.clear()
        # env_vars
        for k, v in conf.get('env_vars', {}).items():
            it = QtWidgets.QTreeWidgetItem([str(k), str(v)])
            it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsEditable | QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            self.env_tree.addTopLevelItem(it)
        # paths
        for k, paths in conf.get('paths', {}).items():
            parent = QtWidgets.QTreeWidgetItem([str(k), ""])
            parent.setFlags(parent.flags() | QtCore.Qt.ItemFlag.ItemIsEditable | QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            self.env_tree.addTopLevelItem(parent)
            if isinstance(paths, list):
                for p in paths:
                    child = QtWidgets.QTreeWidgetItem([str(p), ""])
                    child.setFlags(child.flags() | QtCore.Qt.ItemFlag.ItemIsEditable | QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
                    parent.addChild(child)
            parent.setExpanded(True)

    def _save_tree_to_memory(self, soft_id=None):
        if not soft_id:
            curr = self.selected_list.currentItem()
            if not curr: return
            soft_id = curr.text()

        env_vars = {}; paths = {}
        for i in range(self.env_tree.topLevelItemCount()):
            it = self.env_tree.topLevelItem(i)
            if it.childCount() > 0:
                paths[it.text(0)] = [it.child(j).text(0) for j in range(it.childCount())]
            else:
                env_vars[it.text(0)] = it.text(1)
        self.software_configs[soft_id] = {'env_vars': env_vars, 'paths': paths}

    def load_project_config(self, project_name):
        proj_dir = os.path.join(PROJECTS_ROOT, project_name)
        data = load_yml(os.path.join(proj_dir, "templates_base.yml"))
        self.name_input.setText(project_name)
        
        # RootパスからBaseディレクトリを推測
        root_path = data.get('anchors', {}).get('project_root', "")
        if root_path:
            self.path_input.setText(os.path.dirname(root_path).replace("\\", "/"))

        # リスト初期化
        self.global_list.clear(); self.selected_list.clear()
        master_soft = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        for sid in sorted(master_soft.keys()): self.global_list.addItem(sid)
        
        enabled = data.get('enabled_softwares', [])
        for sid in enabled:
            self.selected_list.addItem(sid)
            spec_path = os.path.join(proj_dir, f"software_{sid}.yml")
            if os.path.exists(spec_path):
                self.software_configs[sid] = load_yml(spec_path)

        self._apply_data_to_ui(data)

    def init_ui_from_default(self):
        self.global_list.clear(); self.selected_list.clear()
        master = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        for sid in sorted(master.keys()): self.global_list.addItem(sid)
        self._apply_data_to_ui(load_yml(os.path.join(DEFAULT_DIR, "templates_base.yml")))

    def save_config(self):
        self._save_tree_to_memory()
        name = self.name_input.text().strip(); base = self.path_input.text().strip()
        if not name or not base:
            QtWidgets.QMessageBox.warning(self, "Error", "Project Code and Base Directory are required.")
            return
        
        proj_dir = os.path.join(PROJECTS_ROOT, name); os.makedirs(proj_dir, exist_ok=True)
        config = {
            'anchors': {'project_name': name, 'project_root': f"{base}/{name}".replace("\\", "/")},
            'enabled_softwares': [],
            'shot_depts': [self.shot_depts_list["list"].item(i).text() for i in range(self.shot_depts_list["list"].count())],
            'asset_depts': [self.asset_depts_list["list"].item(i).text() for i in range(self.asset_depts_list["list"].count())],
            'templates': {}
        }
        
        # Anchors
        a_tab = self.anchors_table["table"]; res = [1920, 1080]
        for r in range(a_tab.rowCount()):
            k = a_tab.item(r, 0).text() if a_tab.item(r, 0) else ""
            v = a_tab.item(r, 1).text() if a_tab.item(r, 1) else ""
            if not k: continue
            if "resolution X" in k: res[0] = int(v) if v.isdigit() else 1920
            elif "resolution Y" in k: res[1] = int(v) if v.isdigit() else 1080
            else: config['anchors'][k] = int(v) if v.isdigit() else v
        config['anchors']['resolution'] = res

        # Templates
        t_tab = self.template_table["table"]
        for r in range(t_tab.rowCount()):
            k = t_tab.item(r, 0).text() if t_tab.item(r, 0) else ""
            v = t_tab.item(r, 1).text() if t_tab.item(r, 1) else ""
            if k: config['templates'][k] = v

        # Enabled Softwares & Individual Configs
        for i in range(self.selected_list.count()):
            sid = self.selected_list.item(i).text()
            config['enabled_softwares'].append(sid)
            if sid in self.software_configs:
                save_yml(os.path.join(proj_dir, f"software_{sid}.yml"), self.software_configs[sid])
        
        save_yml(os.path.join(proj_dir, "templates_base.yml"), config)
        self.config_saved.emit()
        QtWidgets.QMessageBox.information(self, "Saved", "Project configuration saved successfully.")
        self.close()

    # --- ヘルパーメソッド群 (既存と同等) ---
    def create_table_page(self, title, headers):
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        t = QtWidgets.QTableWidget(0, 2); t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        l.addWidget(t); bl = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("+"); rem = QtWidgets.QPushButton("-")
        add.clicked.connect(lambda: t.insertRow(t.rowCount()))
        rem.clicked.connect(lambda: t.removeRow(t.currentRow()))
        bl.addWidget(add); bl.addWidget(rem); l.addLayout(bl)
        return {"widget": w, "table": t}

    def create_list_page(self, title):
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        lw = QtWidgets.QListWidget(); lw.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked)
        l.addWidget(lw); bl = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("+"); rem = QtWidgets.QPushButton("-")
        add.clicked.connect(lambda: (i := QtWidgets.QListWidgetItem("new"), i.setFlags(i.flags()|QtCore.Qt.ItemFlag.ItemIsEditable), lw.addItem(i)))
        rem.clicked.connect(lambda: lw.takeItem(lw.currentRow()))
        bl.addWidget(add); bl.addWidget(rem); l.addLayout(bl)
        return {"widget": w, "list": lw}

    def browse_path(self):
        res = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Base Directory")
        if res: self.path_input.setText(res.replace("\\", "/"))

    def add_custom_software(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Exe/Bat", "", "Executables (*.exe *.bat *.cmd)")
        if p:
            sid = os.path.splitext(os.path.basename(p))[0]
            self.selected_list.addItem(sid)
            # カスタムソフトは空のenvで初期化
            self.software_configs[sid] = {'env_vars':{}, 'paths':{}}

    def add_tree_var(self):
        i = QtWidgets.QTreeWidgetItem(["NEW_VAR", "value"])
        i.setFlags(i.flags()|QtCore.Qt.ItemFlag.ItemIsEditable|QtCore.Qt.ItemFlag.ItemIsEnabled|QtCore.Qt.ItemFlag.ItemIsSelectable)
        self.env_tree.addTopLevelItem(i)

    def add_tree_path(self):
        p = self.env_tree.currentItem()
        if p and not p.parent():
            c = QtWidgets.QTreeWidgetItem(["/path/to/folder", ""])
            c.setFlags(c.flags()|QtCore.Qt.ItemFlag.ItemIsEditable|QtCore.Qt.ItemFlag.ItemIsEnabled|QtCore.Qt.ItemFlag.ItemIsSelectable)
            p.addChild(c); p.setExpanded(True)

    def remove_tree_node(self):
        i = self.env_tree.currentItem()
        if i:
            if i.parent(): i.parent().removeChild(i)
            else: self.env_tree.takeTopLevelItem(self.env_tree.indexOfTopLevelItem(i))

    def _apply_data_to_ui(self, data):
        # (既存のUI反映ロジック)
        tab = self.anchors_table["table"]; tab.setRowCount(0)
        for k, v in data.get('anchors', {}).items():
            if k in ["project_name", "project_root"]: continue
            if k == "resolution" and isinstance(v, list):
                for i, ax in enumerate(["X", "Y"]):
                    r = tab.rowCount(); tab.insertRow(r)
                    tab.setItem(r, 0, QtWidgets.QTableWidgetItem(f"resolution {ax}"))
                    tab.setItem(r, 1, QtWidgets.QTableWidgetItem(str(v[i])))
            else:
                r = tab.rowCount(); tab.insertRow(r)
                tab.setItem(r, 0, QtWidgets.QTableWidgetItem(k)); tab.setItem(r, 1, QtWidgets.QTableWidgetItem(str(v)))
        
        for key, obj in [('shot_depts', self.shot_depts_list), ('asset_depts', self.asset_depts_list)]:
            obj["list"].clear()
            for t in data.get(key, []):
                li = QtWidgets.QListWidgetItem(str(t)); li.setFlags(li.flags() | QtCore.Qt.ItemFlag.ItemIsEditable | QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
                obj["list"].addItem(li)
        
        tab = self.template_table["table"]; tab.setRowCount(0)
        for k, v in data.get('templates', {}).items():
            r = tab.rowCount(); tab.insertRow(r)
            tab.setItem(r, 0, QtWidgets.QTableWidgetItem(k)); tab.setItem(r, 1, QtWidgets.QTableWidgetItem(v))

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = ConfigCreatorApp()
    win.show()
    sys.exit(app.exec())