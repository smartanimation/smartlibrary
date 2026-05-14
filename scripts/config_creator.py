import os
import yaml
import sys
from PySide6 import QtWidgets, QtCore, QtGui

# パス設定 (もともとの config_creator1.0.0.py のロジックを維持)
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
    # ランチャー側に更新を伝えるためのカスタムシグナル
    config_saved = QtCore.Signal()

    def __init__(self, target_project=None):
        super().__init__()
        self.setWindowTitle("Project Config Creator")
        self.setMinimumWidth(1000); self.setMinimumHeight(900)
        
        self.target_project = target_project
        self.software_configs = {} # メモリ保持用

        self.setup_ui()

        # 起動時のデータロード
        if self.target_project:
            self.load_project_config(self.target_project)
        else:
            self.init_ui_from_default()

    def setup_ui(self):
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        # --- 1. 基本設定 ---
        form = QtWidgets.QFormLayout()
        self.name_input = QtWidgets.QLineEdit()
        self.path_input = QtWidgets.QLineEdit()
        self.browse_btn = QtWidgets.QPushButton("Browse")
        self.browse_btn.clicked.connect(self.browse_path)
        
        path_layout = QtWidgets.QHBoxLayout()
        path_layout.addWidget(self.path_input)
        path_layout.addWidget(self.browse_btn)
        
        form.addRow("Project Code:", self.name_input)
        form.addRow("Base Directory:", path_layout)
        main_layout.addLayout(form)

        # --- 2. タブエリア ---
        self.tabs = QtWidgets.QTabWidget()
        self.anchors_table = self.create_table_page("Anchors", ["Key", "Value"])
        self.soft_tab = self.setup_software_tab() # 環境変数エディタ
        self.shot_depts_list = self.create_list_page("Shot Depts")
        self.asset_depts_list = self.create_list_page("Asset Depts")
        self.template_table = self.create_table_page("Templates", ["Key", "Path Value"])

        self.tabs.addTab(self.anchors_table["widget"], "Anchors")
        self.tabs.addTab(self.soft_tab, "Softwares")
        self.tabs.addTab(self.shot_depts_list["widget"], "Shot Depts")
        self.tabs.addTab(self.asset_depts_list["widget"], "Asset Depts")
        self.tabs.addTab(self.template_table["widget"], "Templates")
        main_layout.addWidget(self.tabs)

        # --- 3. 保存ボタン ---
        self.save_btn = QtWidgets.QPushButton("SAVE CONFIG")
        self.save_btn.setFixedHeight(50)
        self.save_btn.setStyleSheet("background-color: #2d5a27; color: white; font-weight: bold;")
        self.save_btn.clicked.connect(self.save_config)
        main_layout.addWidget(self.save_btn)

    def setup_software_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        
        # 左: ソフトリスト
        left_layout = QtWidgets.QVBoxLayout()
        self.soft_list_widget = QtWidgets.QListWidget()
        self.soft_list_widget.currentRowChanged.connect(self.on_soft_selection_changed)
        
        btn_layout = QtWidgets.QHBoxLayout()
        self.add_soft_btn = QtWidgets.QPushButton("+ Add")
        self.rem_soft_btn = QtWidgets.QPushButton("- Rem")
        self.add_soft_btn.clicked.connect(self.add_custom_software)
        self.rem_soft_btn.clicked.connect(self.remove_software)
        btn_layout.addWidget(self.add_soft_btn); btn_layout.addWidget(self.rem_soft_btn)
        
        left_layout.addWidget(QtWidgets.QLabel("Enabled Softwares:"))
        left_layout.addWidget(self.soft_list_widget)
        left_layout.addLayout(btn_layout)
        
        # 右: 環境変数ツリー (画像にあった機能)
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

    def on_soft_selection_changed(self, row):
        """ソフト選択時にメモリまたはディスクからロード"""
        self._save_tree_to_memory()
        item = self.soft_list_widget.item(row)
        if not item: return
        soft_id = item.text()
        
        # メモリになければ読み込み
        if soft_id not in self.software_configs:
            proj_dir = os.path.join(PROJECTS_ROOT, self.name_input.text().strip())
            spec_path = os.path.join(proj_dir, f"software_{soft_id}.yml")
            if os.path.exists(spec_path):
                self.software_configs[soft_id] = load_yml(spec_path).get('softwares', {}).get(soft_id, {})
            else:
                def_path = os.path.join(DEFAULT_DIR, f"software_{soft_id}.yml")
                self.software_configs[soft_id] = load_yml(def_path).get('softwares', {}).get(soft_id, {'env_vars':{}, 'paths':{}})
        
        self._populate_tree(self.software_configs[soft_id])

    def _populate_tree(self, conf):
        self.env_tree.clear()
        for k, v in conf.get('env_vars', {}).items():
            it = QtWidgets.QTreeWidgetItem([str(k), str(v)])
            it.setFlags(it.flags() | QtCore.Qt.ItemIsEditable); self.env_tree.addTopLevelItem(it)
        for k, paths in conf.get('paths', {}).items():
            parent = QtWidgets.QTreeWidgetItem([str(k), ""])
            parent.setFlags(parent.flags() | QtCore.Qt.ItemIsEditable); self.env_tree.addTopLevelItem(parent)
            for p in paths:
                child = QtWidgets.QTreeWidgetItem([str(p), ""])
                child.setFlags(child.flags() | QtCore.Qt.ItemIsEditable); parent.addChild(child)
            parent.setExpanded(True)

    def _save_tree_to_memory(self):
        row = self.soft_list_widget.currentRow()
        if row < 0: return
        soft_id = self.soft_list_widget.item(row).text()
        env_vars = {}; paths = {}
        for i in range(self.env_tree.topLevelItemCount()):
            it = self.env_tree.topLevelItem(i)
            if it.childCount() > 0:
                paths[it.text(0)] = [it.child(j).text(0) for j in range(it.childCount())]
            else:
                env_vars[it.text(0)] = it.text(1)
        if soft_id not in self.software_configs: self.software_configs[soft_id] = {}
        self.software_configs[soft_id].update({'env_vars': env_vars, 'paths': paths})

    def load_project_config(self, project_name):
        proj_dir = os.path.join(PROJECTS_ROOT, project_name)
        data = load_yml(os.path.join(proj_dir, "templates_base.yml"))
        self.name_input.setText(project_name)
        root = data.get('anchors', {}).get('project_root', "")
        if root: self.path_input.setText(os.path.dirname(root.rstrip("/")))
        
        self.soft_list_widget.clear()
        master_soft = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        enabled = data.get('enabled_softwares', [])
        
        all_ids = sorted(list(set(list(master_soft.keys()) + enabled)))
        for sid in all_ids:
            it = QtWidgets.QListWidgetItem(sid)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            it.setCheckState(QtCore.Qt.Checked if sid in enabled else QtCore.Qt.Unchecked)
            self.soft_list_widget.addItem(it)
            
            # 既存プロジェクトなら個別ファイルを即メモリへ
            spec_path = os.path.join(proj_dir, f"software_{sid}.yml")
            if os.path.exists(spec_path):
                self.software_configs[sid] = load_yml(spec_path).get('softwares', {}).get(sid, {})

        self._apply_data_to_ui(data)

    def _apply_data_to_ui(self, data):
        # Anchors
        tab = self.anchors_table["table"]; tab.setRowCount(0)
        for k, v in data.get('anchors', {}).items():
            if k in ["project_name", "project_root"]: continue
            if k == "resolution":
                for i, ax in enumerate(["X", "Y"]):
                    r = tab.rowCount(); tab.insertRow(r)
                    tab.setItem(r, 0, QtWidgets.QTableWidgetItem(f"resolution {ax}"))
                    tab.setItem(r, 1, QtWidgets.QTableWidgetItem(str(v[i])))
            else:
                r = tab.rowCount(); tab.insertRow(r)
                tab.setItem(r, 0, QtWidgets.QTableWidgetItem(k)); tab.setItem(r, 1, QtWidgets.QTableWidgetItem(str(v)))
        # Depts
        for key, obj in [('shot_depts', self.shot_depts_list), ('asset_depts', self.asset_depts_list)]:
            obj["list"].clear()
            for t in data.get(key, []):
                li = QtWidgets.QListWidgetItem(str(t)); li.setFlags(li.flags() | QtCore.Qt.ItemIsEditable); obj["list"].addItem(li)
        # Templates
        tab = self.template_table["table"]; tab.setRowCount(0)
        for k, v in data.get('templates', {}).items():
            r = tab.rowCount(); tab.insertRow(r)
            tab.setItem(r, 0, QtWidgets.QTableWidgetItem(k)); tab.setItem(r, 1, QtWidgets.QTableWidgetItem(v))

    def save_config(self):
        self._save_tree_to_memory()
        name = self.name_input.text().strip(); base = self.path_input.text().strip()
        if not name or not base: return
        
        proj_dir = os.path.join(PROJECTS_ROOT, name); os.makedirs(proj_dir, exist_ok=True)
        config = {
            'anchors': {'project_name': name, 'project_root': f"{base}/{name}".replace("\\", "/")},
            'enabled_softwares': [],
            'shot_depts': [self.shot_depts_list["list"].item(i).text() for i in range(self.shot_depts_list["list"].count())],
            'asset_depts': [self.asset_depts_list["list"].item(i).text() for i in range(self.asset_depts_list["list"].count())],
            'templates': {}
        }
        # テーブル解析
        a_tab = self.anchors_table["table"]; res = [1920, 1080]
        for r in range(a_tab.rowCount()):
            k = a_tab.item(r, 0).text(); v = a_tab.item(r, 1).text()
            if "resolution X" in k: res[0] = int(v)
            elif "resolution Y" in k: res[1] = int(v)
            else: config['anchors'][k] = int(v) if v.isdigit() else v
        config['anchors']['resolution'] = res
        for r in range(self.template_table["table"].rowCount()):
            k = self.template_table["table"].item(r, 0).text(); v = self.template_table["table"].item(r, 1).text()
            if k: config['templates'][k] = v

        for i in range(self.soft_list_widget.count()):
            it = self.soft_list_widget.item(i); sid = it.text()
            if it.checkState() == QtCore.Qt.Checked:
                config['enabled_softwares'].append(sid)
                if sid in self.software_configs:
                    save_yml(os.path.join(proj_dir, f"software_{sid}.yml"), {"softwares": {sid: self.software_configs[sid]}})
        
        save_yml(os.path.join(proj_dir, "templates_base.yml"), config)
        self.config_saved.emit()
        QtWidgets.QMessageBox.information(self, "Saved", "Success")
        self.close()

    # --- 共通UI作成 ---
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
        add.clicked.connect(lambda: (i := QtWidgets.QListWidgetItem("new"), i.setFlags(i.flags()|QtCore.Qt.ItemIsEditable), lw.addItem(i)))
        rem.clicked.connect(lambda: lw.takeItem(lw.currentRow()))
        bl.addWidget(add); bl.addWidget(rem); l.addLayout(bl)
        return {"widget": w, "list": lw}

    def browse_path(self):
        res = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Directory")
        if res: self.path_input.setText(res.replace("\\", "/"))

    def init_ui_from_default(self):
        master = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        for sid in master.keys():
            it = QtWidgets.QListWidgetItem(sid)
            it.setFlags(it.flags()|QtCore.Qt.ItemIsUserCheckable|QtCore.Qt.ItemIsEnabled|QtCore.Qt.ItemIsSelectable)
            it.setCheckState(QtCore.Qt.Unchecked); self.soft_list_widget.addItem(it)
        self._apply_data_to_ui(load_yml(os.path.join(DEFAULT_DIR, "templates_base.yml")))

    def add_custom_software(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Exe", "", "Exe (*.exe *.bat)")
        if p:
            sid = os.path.splitext(os.path.basename(p))[0]
            it = QtWidgets.QListWidgetItem(sid)
            it.setFlags(it.flags()|QtCore.Qt.ItemIsUserCheckable|QtCore.Qt.ItemIsEnabled|QtCore.Qt.ItemIsSelectable)
            it.setCheckState(QtCore.Qt.Checked); self.soft_list_widget.addItem(it)
            self.software_configs[sid] = {'path': p.replace("\\", "/"), 'env_vars':{}, 'paths':{}}

    def remove_software(self):
        r = self.soft_list_widget.currentRow()
        if r >= 0: self.soft_list_widget.takeItem(r)

    def add_tree_var(self):
        i = QtWidgets.QTreeWidgetItem(["NEW_VAR", "value"])
        i.setFlags(i.flags()|QtCore.Qt.ItemIsEditable); self.env_tree.addTopLevelItem(i)

    def add_tree_path(self):
        p = self.env_tree.currentItem()
        if p and not p.parent():
            c = QtWidgets.QTreeWidgetItem(["/path/to", ""])
            c.setFlags(c.flags()|QtCore.Qt.ItemIsEditable); p.addChild(c); p.setExpanded(True)

    def remove_tree_node(self):
        i = self.env_tree.currentItem()
        if i:
            if i.parent(): i.parent().removeChild(i)
            else: self.env_tree.takeTopLevelItem(self.env_tree.indexOfTopLevelItem(i))

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv); win = ConfigCreatorApp(); win.show(); sys.exit(app.exec())