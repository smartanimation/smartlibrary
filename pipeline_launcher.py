import sys
import os
import subprocess
from PySide6 import QtWidgets, QtCore, QtGui

class PipelineLauncher(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        # --- 設定エリア ---
        # 1. ライブラリがインストールされているPythonのパス
        self.python_exe = r"C:\Users\smart\AppData\Local\Programs\Python\Python312\python.exe"
        
        # 2. パス解決のロジック
        # このスクリプト(pipeline_launcher.py)の絶対パスを取得
        current_script_path = os.path.abspath(__file__)
        current_dir = os.path.dirname(current_script_path)

        # もし launcher が scripts フォルダ内にある場合と、親フォルダにある場合の両方に対応
        if os.path.basename(current_dir) == "scripts":
            self.base_dir = os.path.dirname(current_dir)
        else:
            self.base_dir = current_dir

        # 最終的な各フォルダのパスを確定
        self.config_root = os.path.normpath(os.path.join(self.base_dir, "config"))
        self.script_dir = os.path.normpath(os.path.join(self.base_dir, "scripts"))

        # 【デバッグ用】フォルダが見つかっているかコンソールに出力
        print(f"--- Path Check ---")
        print(f"Base Directory: {self.base_dir}")
        print(f"Config Root:    {self.config_root} (Exists: {os.path.exists(self.config_root)})")
        print(f"Script Directory: {self.script_dir} (Exists: {os.path.exists(self.script_dir)})")
        print(f"------------------")

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Pipeline Launcher")
        self.setMinimumSize(450, 300)

        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        layout = QtWidgets.QVBoxLayout(central_widget)

        # Project Selection
        layout.addWidget(QtWidgets.QLabel("Select Project:"))
        self.project_combo = QtWidgets.QComboBox()
        layout.addWidget(self.project_combo)

        # Script Selection
        layout.addWidget(QtWidgets.QLabel("Select Task:"))
        self.script_combo = QtWidgets.QComboBox()
        layout.addWidget(self.script_combo)

        # 初期リストの読み込み
        self.refresh_lists()

        layout.addSpacing(20)

        # Launch Button
        self.launch_btn = QtWidgets.QPushButton("Launch Task")
        self.launch_btn.setFixedHeight(50)
        self.launch_btn.setStyleSheet("""
            QPushButton {
                background-color: #2E7D32;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
        """)
        self.launch_btn.clicked.connect(self.launch_task)
        layout.addWidget(self.launch_btn)

        # Status Bar
        self.status_label = QtWidgets.QLabel("Status: Ready")
        layout.addWidget(self.status_label)

    def refresh_lists(self):
        """プロジェクトとスクリプトのリストを更新"""
        self.project_combo.clear()
        self.script_combo.clear()

        # Configフォルダの読み込み
        if os.path.exists(self.config_root):
            projects = [f for f in os.listdir(self.config_root) if os.path.isdir(os.path.join(self.config_root, f))]
            if projects:
                self.project_combo.addItems(projects)
            else:
                self.project_combo.addItem("No project found in config folder")
        else:
            self.project_combo.addItem("ERROR: Config folder not found")

        # Scriptsフォルダの読み込み
        if os.path.exists(self.script_dir):
            scripts = [f for f in os.listdir(self.script_dir) if f.endswith(".py") and f != "pipeline_launcher.py"]
            if scripts:
                self.script_combo.addItems(scripts)
            else:
                self.script_combo.addItem("No scripts found")
        else:
            self.script_combo.addItem("ERROR: Scripts folder not found")

    def launch_task(self):
        project = self.project_combo.currentText()
        script = self.script_combo.currentText()

        # エラー表示がある場合は中断
        if "ERROR" in project or "No " in project or "No " in script:
            QtWidgets.QMessageBox.warning(self, "Warning", "有効なProjectとTaskを選択してください。")
            return

        target_config_dir = os.path.join(self.config_root, project)
        target_script_path = os.path.join(self.script_dir, script)

        # 環境変数のコピーと設定
        env = os.environ.copy()
        env["PROJECT_CONFIG_DIR"] = target_config_dir

        try:
            # 指定されたPython.exeを使って独立したプロセスで起動
            subprocess.Popen(
                [self.python_exe, target_script_path],
                env=env,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            self.status_label.setText(f"Status: Launched {script}")
            
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"起動に失敗しました:\n{str(e)}")

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = PipelineLauncher()
    window.show()
    sys.exit(app.exec())