import os
import yaml
import sys
from PySide6 import QtWidgets, QtCore, QtGui

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_ROOT = os.path.normpath(os.path.join(CURRENT_DIR, "..", "config"))
GLOBAL_SOFT_PATH = os.path.join(PROJECTS_ROOT, "default", "software_settings.yml")

def load_yml(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"YAML Read Error: {e}")
    return {}

class ConfigCreatorApp(QtWidgets.QMainWindow):
    def __init__(self, target_project=None):
        super().__init__()
        self.setWindowTitle("Project Config Creator")
        self.setMinimumWidth(800); self.setMinimumHeight(850)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        # --- 1. 基本設定 ---
        form = QtWidgets.QFormLayout()
        self.name_input = QtWidgets.QLineEdit()
        form.addRow("Project Code:", self.name_input)
        
        path_layout = QtWidgets.QHBoxLayout()
        self.path_input = QtWidgets.QLineEdit()
        self.browse_btn = QtWidgets.QPushButton("Browse")
        self.browse_btn.clicked.connect(self.browse_path)
        path_layout.addWidget(self.path_input); path_layout.addWidget(self.browse_btn)
        form.addRow("Base Directory:", path_layout)
        main_layout.addLayout(form)

        # --- 2. タブエリア ---
        self.tabs = QtWidgets.QTabWidget()
        
        self.anchors_table = self.create_table_page("Anchors Settings", ["Key", "Value"])
        self.soft_select_list = self.create_check_list_page("Software Selection") # ソフト選択タブ
        self.shot_depts_list = self.create_list_page("Shot Depts")
        self.asset_depts_list = self.create_list_page("Asset Depts")
        self.template_table = self.create_table_page("Folder Templates", ["Key", "Path Value"])

        self.tabs.addTab(self.anchors_table["widget"], "Anchors")
        self.tabs.addTab(self.soft_select_list["widget"], "Softwares")
        self.tabs.addTab(self.shot_depts_list["widget"], "Shot Depts")
        self.tabs.addTab(self.asset_depts_list["widget"], "Asset Depts")
        self.tabs.addTab(self.template_table["widget"], "Templates")
        main_layout.addWidget(self.tabs)

        self.save_btn = QtWidgets.QPushButton("SAVE CONFIG")
        self.save_btn.setFixedHeight(50)
        self.save_btn.setStyleSheet("background-color: #2d5a27; color: white; font-weight: bold;")
        self.save_btn.clicked.connect(self.save_config)
        main_layout.addWidget(self.save_btn)

        if target_project:
            self.load_project_config(target_project)
        else:
            self.init_ui_from_default()

    def create_check_list_page(self, title):
        """software_settings.yml からリストを読み込み、チェックボックス付きで表示"""
        page = QtWidgets.QWidget(); layout = QtWidgets.QVBoxLayout(page)
        list_widget = QtWidgets.QListWidget()
        layout.addWidget(QtWidgets.QLabel("Select softwares to enable in this project:"))
        layout.addWidget(list_widget)
        
        # マスターデータからツール名を読み込む
        soft_data = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        for soft_name in soft_data.keys():
            item = QtWidgets.QListWidgetItem(soft_name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Unchecked)
            list_widget.addItem(item)
            
        return {"widget": page, "list": list_widget}

    def create_list_page(self, title):
        page = QtWidgets.QWidget(); layout = QtWidgets.QVBoxLayout(page)
        list_widget = QtWidgets.QListWidget()
        list_widget.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed)
        layout.addWidget(list_widget)
        btn_layout = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("+ Add"); del_btn = QtWidgets.QPushButton("- Remove")
        add_btn.clicked.connect(lambda: self._add_item_to_list(list_widget, "new_item", True))
        del_btn.clicked.connect(lambda: list_widget.takeItem(list_widget.currentRow()))
        btn_layout.addWidget(add_btn); btn_layout.addWidget(del_btn)
        layout.addLayout(btn_layout)
        return {"widget": page, "list": list_widget}

    def _add_item_to_list(self, list_widget, text, edit=False):
        item = QtWidgets.QListWidgetItem(str(text))
        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
        list_widget.addItem(item)
        if edit: list_widget.editItem(item)

    def create_table_page(self, title, headers):
        page = QtWidgets.QWidget(); layout = QtWidgets.QVBoxLayout(page)
        table = QtWidgets.QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(table)
        btn_layout = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("+ Add Row"); del_btn = QtWidgets.QPushButton("- Remove Row")
        add_btn.clicked.connect(lambda: table.insertRow(table.rowCount()))
        del_btn.clicked.connect(lambda: table.removeRow(table.currentRow()))
        btn_layout.addWidget(add_btn); btn_layout.addWidget(del_btn)
        layout.addLayout(btn_layout)
        return {"widget": page, "table": table}

    def init_ui_from_default(self):
        path = os.path.join(PROJECTS_ROOT, "default", "templates_base.yml")
        self._apply_data_to_ui(load_yml(path))

    def load_project_config(self, project_name):
        path = os.path.join(PROJECTS_ROOT, project_name, "templates_base.yml")
        if not os.path.exists(path): return
        data = load_yml(path)
        self.name_input.setText(project_name)
        root = data.get('anchors', {}).get('project_root', "")
        if root: self.path_input.setText(os.path.dirname(root.rstrip("/")))
        self._apply_data_to_ui(data)

    def _apply_data_to_ui(self, data):
        # 1. Anchors
        anchors = data.get('anchors', {})
        a_table = self.anchors_table["table"]; a_table.setRowCount(0)
        exclude = ["project_name", "project_root"]
        for k, v in anchors.items():
            if k in exclude: continue
            if k == "resolution" and isinstance(v, list):
                for i, ax in enumerate(["X", "Y"]):
                    row = a_table.rowCount(); a_table.insertRow(row)
                    a_table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"resolution {ax}"))
                    a_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(v[i])))
            else:
                row = a_table.rowCount(); a_table.insertRow(row)
                a_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(k))); a_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(v)))

        # 2. Software Selection (enabled_softwaresを元にチェックを入れる)
        enabled = data.get('enabled_softwares', [])
        for i in range(self.soft_select_list["list"].count()):
            item = self.soft_select_list["list"].item(i)
            item.setCheckState(QtCore.Qt.Checked if item.text() in enabled else QtCore.Qt.Unchecked)

        # 3. Depts
        for key, obj in [('shot_depts', self.shot_depts_list), ('asset_depts', self.asset_depts_list)]:
            obj["list"].clear()
            for t in data.get(key, []): self._add_item_to_list(obj["list"], t)

        # 4. Templates
        t_table = self.template_table["table"]; t_table.setRowCount(0)
        for k, v in data.get('templates', {}).items():
            row = t_table.rowCount(); t_table.insertRow(row)
            t_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(k))); t_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(v)))

    def browse_path(self):
        res = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Base Directory")
        if res: self.path_input.setText(res.replace("\\", "/"))

    def save_config(self):
        name = self.name_input.text().strip()
        base = self.path_input.text().strip()
        if not name or not base: return

        config = {
            'anchors': {'project_name': name, 'project_root': f"{base}/{name}".replace("\\", "/")},
            'enabled_softwares': [], # チェックされた名前だけ保存
            'shot_depts': [], 'asset_depts': [], 'templates': {}
        }

        # ソフト選択の取得
        for i in range(self.soft_select_list["list"].count()):
            item = self.soft_select_list["list"].item(i)
            if item.checkState() == QtCore.Qt.Checked:
                config['enabled_softwares'].append(item.text())

        # Anchors/Depts/Templates の取得 (中略: 前回のロジックと同じ)
        a_table = self.anchors_table["table"]
        res = [1920, 1080]
        for r in range(a_table.rowCount()):
            k = a_table.item(r, 0).text(); v = a_table.item(r, 1).text()
            if "resolution X" in k: res[0] = int(v)
            elif "resolution Y" in k: res[1] = int(v)
            else: config['anchors'][k] = int(v) if v.isdigit() else v
        config['anchors']['resolution'] = res
        config['shot_depts'] = [self.shot_depts_list["list"].item(i).text() for i in range(self.shot_depts_list["list"].count())]
        config['asset_depts'] = [self.asset_depts_list["list"].item(i).text() for i in range(self.asset_depts_list["list"].count())]
        for r in range(self.template_table["table"].rowCount()):
            k = self.template_table["table"].item(r, 0).text(); v = self.template_table["table"].item(r, 1).text()
            if k: config['templates'][k] = v

        save_path = os.path.join(PROJECTS_ROOT, name, "templates_base.yml")
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        self.close()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv); app.setStyle("Fusion")
    window = ConfigCreatorApp(); window.show(); sys.exit(app.exec())