import os
import re
import yaml
import sys
import copy
from PySide6 import QtWidgets, QtCore, QtGui

# パス設定
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_ROOT = os.path.normpath(os.path.join(CURRENT_DIR, ".."))
SMARTPROJECTS_ROOT = os.environ.get("SMARTPIPELINE_STUDIO_CONFIG_DIR") or os.path.normpath(os.path.join(PIPELINE_ROOT, "..", "smartprojects"))
PROJECTS_ROOT = os.environ.get("SMARTPIPELINE_PROJECT_CONFIG_ROOT") or os.path.join(SMARTPROJECTS_ROOT, "config")
DEFAULT_DIR = os.path.join(PIPELINE_ROOT, "config", "default")
GLOBAL_SOFT_PATH = os.path.join(DEFAULT_DIR, "software_settings.yml")
ASSET_LIST_URL_LABEL = "Asset List URL"
SHOT_LIST_URL_LABEL = "Shot List URL"
PACKAGES_DIR = os.path.join(PIPELINE_ROOT, "packages")
if PACKAGES_DIR not in sys.path:
    sys.path.insert(0, PACKAGES_DIR)

from smartlib.apps.asset_manager.subsets import asset_subset_catalog


def google_sheet_id(value):
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]+", text):
        return text
    return ""


def google_sheet_url(settings, prefix):
    url = str((settings or {}).get(f"{prefix}_url") or "").strip()
    if url:
        return url
    sheet_id = str((settings or {}).get(f"{prefix}_id") or "").strip()
    if sheet_id:
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    return ""

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

def merge_dicts(base, override):
    result = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result

class ConfigCreatorApp(QtWidgets.QMainWindow):
    config_saved = QtCore.Signal()

    def __init__(self, target_project=None):
        super().__init__()
        self.setWindowTitle("Project Config Creator")
        #self.setMinimumWidth(1100); self.setMinimumHeight(900)
        
        # OSのアイコンを取得するためのプロバイダー
        self.icon_provider = QtWidgets.QFileIconProvider()
        
        self.target_project = target_project
        self.software_configs = {} # sid をキーに設定データを保持
        self.context_configs = {}
        self.context_active_versions = {}
        self.asset_subset_catalog = {}
        self.template_file_settings = {}
        self._context_loading = False
        self._current_context_key = None
        self._current_context_version = None
        self._current_output_key = None

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
        self.anchors_table = self.create_table_page("Anchors", ["Key", "Value"])
        self.shot_depts_list = self.create_shot_departments_page()
        self.asset_depts_list = self.create_list_page("Asset Depts")
        self.template_table = self.create_table_page(
            "Folder Structure",
            ["Key", "Path Value"],
        )
        self.template_files_tab = self.setup_template_files_tab()
        self.naming_tab = self.setup_naming_tab()
        self.preflight_tab = self.setup_preflight_tab()
        self.context_tab = self.setup_context_tab()
        self.resolvers_tab = self.setup_resolvers_tab()

        self.tabs.addTab(self.soft_tab, "Softwares")
        self.tabs.addTab(self.anchors_table["widget"], "Anchors")
        self.tabs.addTab(self.shot_depts_list["widget"], "Shot Depts")
        self.tabs.addTab(self.asset_depts_list["widget"], "Asset Depts")
        self.tabs.addTab(self.template_table["widget"], "Folder Structure")
        self.tabs.addTab(self.template_files_tab, "Templates")
        self.tabs.addTab(self.naming_tab, "Naming")
        self.tabs.addTab(self.preflight_tab, "Preflight")
        self.tabs.addTab(self.context_tab, "Contexts")
        self.tabs.addTab(self.resolvers_tab, "Resolvers")
        self.tabs.currentChanged.connect(self._refresh_template_files_table)
        self.name_input.textChanged.connect(self._refresh_template_files_table)
        self.path_input.textChanged.connect(self._refresh_template_files_table)
        main_layout.addWidget(self.tabs)

        command_layout = QtWidgets.QHBoxLayout()
        command_layout.addStretch()
        self.revert_btn = QtWidgets.QPushButton("Revert")
        self.revert_btn.setFixedSize(130, 38)
        self.revert_btn.clicked.connect(self.revert_config)
        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.setFixedSize(130, 38)
        self.save_btn.setStyleSheet("background-color: #2869ad; color: white; font-weight: bold;")
        self.save_btn.clicked.connect(self.save_config)
        command_layout.addWidget(self.revert_btn)
        command_layout.addWidget(self.save_btn)
        main_layout.addLayout(command_layout)

    def setup_preflight_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(page)
        self.preflight_maya_versions = QtWidgets.QLineEdit()
        self.preflight_maya_versions.setPlaceholderText("2024.2, 2026")
        self.preflight_linear_unit = QtWidgets.QComboBox()
        self.preflight_linear_unit.setEditable(True)
        self.preflight_linear_unit.addItems(["mm", "cm", "m", "in", "ft", "yd"])
        self.preflight_background_categories = QtWidgets.QLineEdit()
        self.preflight_background_categories.setPlaceholderText("BG, BGA, ENV, background")
        self.preflight_forbidden_characters = QtWidgets.QLineEdit()
        self.preflight_forbidden_characters.setPlaceholderText("<space>, ., -")
        self.preflight_geometry_set = QtWidgets.QLineEdit()
        self.preflight_geometry_set.setPlaceholderText("cache_geo_set")
        self.preflight_skeleton_set = QtWidgets.QLineEdit()
        self.preflight_skeleton_set.setPlaceholderText("skel_export_set")
        self.preflight_root_joint_source = QtWidgets.QComboBox()
        self.preflight_root_joint_source.addItems(["rig_metadata", "scene_detection"])
        self.preflight_root_joint_metadata_key = QtWidgets.QLineEdit()
        self.preflight_root_joint_metadata_key.setPlaceholderText("root_joint")
        self.preflight_root_joint_detection = QtWidgets.QComboBox()
        self.preflight_root_joint_detection.addItems(
            ["skin_influence_root", "skeleton_set_root", "joint_hierarchy_root"]
        )
        layout.addRow("Allowed Maya Versions:", self.preflight_maya_versions)
        layout.addRow("Linear Unit:", self.preflight_linear_unit)
        layout.addRow("Background Categories:", self.preflight_background_categories)
        layout.addRow("Forbidden Name Characters:", self.preflight_forbidden_characters)
        export_title = QtWidgets.QLabel("USD Skel Export Contract")
        export_title.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addRow(export_title)
        layout.addRow("Geometry Set:", self.preflight_geometry_set)
        layout.addRow("Skeleton Set:", self.preflight_skeleton_set)
        layout.addRow("Root Joint Source:", self.preflight_root_joint_source)
        layout.addRow("Root Joint Metadata Key:", self.preflight_root_joint_metadata_key)
        layout.addRow("Root Joint Auto Detection:", self.preflight_root_joint_detection)
        note = QtWidgets.QLabel(
            "Saved to preflight.yml. Use <space> to represent a space character.\n"
            "Background assets skip the configured Geometry Set; asset.json preflight_profile takes precedence.\n"
            "The actual root joint name is stored per asset in rig metadata."
        )
        note.setWordWrap(True)
        layout.addRow(note)
        return page

    @staticmethod
    def _comma_values(text):
        return [value.strip() for value in str(text or "").split(",") if value.strip()]

    def _load_preflight_editor(self, project_name=None):
        default_data = load_yml(os.path.join(DEFAULT_DIR, "preflight.yml"))
        project_data = (
            load_yml(os.path.join(PROJECTS_ROOT, project_name, "preflight.yml"))
            if project_name else {}
        )
        policy = merge_dicts(default_data, project_data).get("preflight") or {}
        versions = policy.get("maya_versions") or []
        if isinstance(versions, str):
            versions = [versions]
        backgrounds = policy.get("background_categories") or []
        forbidden = policy.get("forbidden_name_characters") or []
        usd_skel = policy.get("usd_skel") or {}
        display_forbidden = ["<space>" if value == " " else str(value) for value in forbidden]
        self.preflight_maya_versions.setText(", ".join(map(str, versions)))
        self.preflight_linear_unit.setCurrentText(str(policy.get("linear_unit") or "cm"))
        self.preflight_background_categories.setText(", ".join(map(str, backgrounds)))
        self.preflight_forbidden_characters.setText(", ".join(display_forbidden))
        self.preflight_geometry_set.setText(str(usd_skel.get("geometry_set") or "cache_geo_set"))
        self.preflight_skeleton_set.setText(str(usd_skel.get("skeleton_set") or "skel_export_set"))
        self.preflight_root_joint_source.setCurrentText(
            str(usd_skel.get("root_joint_source") or "rig_metadata")
        )
        self.preflight_root_joint_metadata_key.setText(
            str(usd_skel.get("root_joint_metadata_key") or "root_joint")
        )
        self.preflight_root_joint_detection.setCurrentText(
            str(usd_skel.get("root_joint_detection") or "skin_influence_root")
        )

    def _save_preflight_config(self, project_dir):
        forbidden = [
            " " if value.casefold() == "<space>" else value
            for value in self._comma_values(self.preflight_forbidden_characters.text())
        ]
        data = {
            "preflight": {
                "maya_versions": self._comma_values(self.preflight_maya_versions.text()),
                "linear_unit": self.preflight_linear_unit.currentText().strip() or "cm",
                "background_categories": self._comma_values(
                    self.preflight_background_categories.text()
                ),
                "forbidden_name_characters": forbidden,
                "usd_skel": {
                    "geometry_set": self.preflight_geometry_set.text().strip() or "cache_geo_set",
                    "skeleton_set": self.preflight_skeleton_set.text().strip() or "skel_export_set",
                    "root_joint_source": self.preflight_root_joint_source.currentText().strip() or "rig_metadata",
                    "root_joint_metadata_key": self.preflight_root_joint_metadata_key.text().strip() or "root_joint",
                    "root_joint_detection": self.preflight_root_joint_detection.currentText().strip()
                    or "skin_influence_root",
                },
            }
        }
        save_yml(os.path.join(project_dir, "preflight.yml"), data)

    def setup_template_files_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.template_files_table = QtWidgets.QTableWidget(0, 4)
        self.template_files_table.setHorizontalHeaderLabels(
            ["Template", "Project File", "Fallback", "Status"]
        )
        self.template_files_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        self.template_files_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )
        self.template_files_table.verticalHeader().setVisible(False)
        header = self.template_files_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.template_files_table, 1)

        buttons = QtWidgets.QHBoxLayout()
        browse = QtWidgets.QPushButton("Browse Project Template")
        clear = QtWidgets.QPushButton("Clear Project Template")
        refresh = QtWidgets.QPushButton("Refresh")
        browse.clicked.connect(self._browse_project_template)
        clear.clicked.connect(self._clear_project_template)
        refresh.clicked.connect(self._refresh_template_files_table)
        buttons.addWidget(browse)
        buttons.addWidget(clear)
        buttons.addStretch()
        buttons.addWidget(refresh)
        layout.addLayout(buttons)
        return page

    def _template_project_root(self):
        base = self.path_input.text().strip()
        project = self.name_input.text().strip()
        if not base or not project:
            return ""
        return os.path.normpath(os.path.join(base, project)).replace("\\", "/")

    def _template_rows(self):
        departments = []
        department_list = self.shot_depts_list["list"]
        for index in range(department_list.count()):
            value = department_list.item(index).text().strip().lower()
            if value and value not in departments:
                departments.append(value)
        rows = [
            (
                "maya.asset.model",
                "Maya Asset / Model Base",
                "settings/templates/maya/asset/model_base.ma",
                "templates/maya/asset/model_base.ma",
            ),
            (
                "maya.asset.rig",
                "Maya Asset / Rig Base",
                "settings/templates/maya/asset/rig_base.ma",
                "templates/maya/asset/rig_base.ma",
            ),
            (
                "maya.asset.look",
                "Maya Asset / Look Base",
                "settings/templates/maya/asset/look_base.ma",
                "templates/maya/asset/look_base.ma",
            ),
            (
                "maya.shot.base",
                "Maya Shot / Base",
                "settings/templates/maya/shot/shot_base.ma",
                "templates/maya/shot/shot_base.ma",
            ),
        ]
        rows.extend(
            (
                f"maya.shot.departments.{department}",
                f"Maya Shot / {department.title()}",
                f"settings/templates/maya/shot/{department}_base.ma",
                f"templates/maya/shot/{department}_base.ma",
            )
            for department in departments
        )
        rows.extend(
            [
                (
                    "maya.camera_rig",
                    "Maya Layout / Camera Rig",
                    "library/layout/camerarig/camerarig.ma",
                    "templates/maya/shot/camerarig.ma",
                ),
                (
                    "after_effects.review.base",
                    "After Effects / Review Base",
                    "settings/templates/ae/review/review_base.aep",
                    "templates/ae/review/review_base.aep",
                ),
            ]
        )
        rows.extend(
            (
                f"after_effects.review.departments.{department}",
                f"After Effects / Review {department.title()}",
                f"settings/templates/ae/review/review_{department}.aep",
                f"templates/ae/review/review_{department}.aep",
            )
            for department in departments
        )
        rows.extend(
            [
                (
                    "usd.look.base",
                    "USD Look / Base",
                    "settings/templates/usd/look/look_base.usda",
                    "templates/usd/look/look_base.usda",
                ),
                (
                    "usd.look.geometry",
                    "USD Look / Geometry",
                    "settings/templates/usd/look/look_geo.usda",
                    "templates/usd/look/look_geo.usda",
                ),
                (
                    "usd.look.material",
                    "USD Look / Material",
                    "settings/templates/usd/look/look_material.usda",
                    "templates/usd/look/look_material.usda",
                ),
                (
                    "usd.look.light",
                    "USD Look / Light",
                    "settings/templates/usd/look/look_light.usda",
                    "templates/usd/look/look_light.usda",
                ),
                (
                    "usd.light.rig_base",
                    "USD Light / Rig Base",
                    "settings/templates/usd/light/light_rig_base.usda",
                    "templates/usd/light/light_rig_base.usda",
                ),
            ]
        )
        return rows

    def _template_setting(self, key):
        value = self.template_file_settings
        for token in str(key).split("."):
            if not isinstance(value, dict):
                return ""
            value = value.get(token)
        return str(value or "")

    def _set_template_setting(self, key, value):
        tokens = str(key).split(".")
        target = self.template_file_settings
        for token in tokens[:-1]:
            target = target.setdefault(token, {})
        target[tokens[-1]] = value

    @staticmethod
    def _resolved_template_path(value, project_root):
        text = str(value or "")
        text = text.replace("{project_root}", project_root)
        text = text.replace("{pipeline_root}", PIPELINE_ROOT.replace("\\", "/"))
        return os.path.expandvars(text).replace("\\", "/")

    def _refresh_template_files_table(self, *_args):
        if not hasattr(self, "template_files_table"):
            return
        current_key = ""
        current_row = self.template_files_table.currentRow()
        if current_row >= 0:
            current_item = self.template_files_table.item(current_row, 0)
            current_key = str(
                current_item.data(QtCore.Qt.ItemDataRole.UserRole) or ""
            ) if current_item else ""
        project_root = self._template_project_root()
        table = self.template_files_table
        table.blockSignals(True)
        table.setRowCount(0)
        selected_row = -1
        for key, label, project_relative, fallback_relative in self._template_rows():
            row = table.rowCount()
            table.insertRow(row)
            label_item = QtWidgets.QTableWidgetItem(label)
            label_item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
            label_item.setFlags(label_item.flags() & ~QtCore.Qt.ItemIsEditable)
            table.setItem(row, 0, label_item)

            configured = self._template_setting(key)
            expected = (
                f"{project_root}/{project_relative}"
                if project_root else ""
            )
            project_value = configured or expected
            table.setItem(row, 1, QtWidgets.QTableWidgetItem(project_value))

            fallback = os.path.join(
                PIPELINE_ROOT, fallback_relative
            ).replace("\\", "/")
            if (
                key.startswith("maya.shot.departments.")
                and not os.path.isfile(fallback)
            ):
                fallback = os.path.join(
                    PIPELINE_ROOT, "templates", "maya", "shot", "shot_base.ma"
                ).replace("\\", "/")
            if (
                key.startswith("after_effects.review.departments.")
                and not os.path.isfile(fallback)
            ):
                fallback = os.path.join(
                    PIPELINE_ROOT, "templates", "ae", "review", "review_base.aep"
                ).replace("\\", "/")
            fallback_item = QtWidgets.QTableWidgetItem(fallback)
            fallback_item.setFlags(
                fallback_item.flags() & ~QtCore.Qt.ItemIsEditable
            )
            table.setItem(row, 2, fallback_item)

            resolved_project = self._resolved_template_path(
                project_value, project_root
            )
            if resolved_project and os.path.isfile(resolved_project):
                status, color = "PROJECT", "#80bd72"
            elif os.path.isfile(fallback):
                status, color = "FALLBACK", "#f2ae30"
            else:
                status, color = "MISSING", "#ef665d"
            status_item = QtWidgets.QTableWidgetItem(status)
            status_item.setFlags(
                status_item.flags() & ~QtCore.Qt.ItemIsEditable
            )
            status_item.setForeground(QtGui.QColor(color))
            table.setItem(row, 3, status_item)
            if key == current_key:
                selected_row = row
        table.blockSignals(False)
        if selected_row >= 0:
            table.selectRow(selected_row)

    def _browse_project_template(self):
        row = self.template_files_table.currentRow()
        if row < 0:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Maya Template",
            self._template_project_root(),
            "Maya Scene (*.ma *.mb)",
        )
        if not path:
            return
        key = self.template_files_table.item(row, 0).data(
            QtCore.Qt.ItemDataRole.UserRole
        )
        self._set_template_setting(str(key), path.replace("\\", "/"))
        self._refresh_template_files_table()

    def _clear_project_template(self):
        row = self.template_files_table.currentRow()
        if row < 0:
            return
        key = str(
            self.template_files_table.item(row, 0).data(
                QtCore.Qt.ItemDataRole.UserRole
            )
        )
        self._set_template_setting(key, "")
        self._refresh_template_files_table()

    def _template_files_from_ui(self):
        result = copy.deepcopy(self.template_file_settings)
        project_root = self._template_project_root()
        for row in range(self.template_files_table.rowCount()):
            key_item = self.template_files_table.item(row, 0)
            path_item = self.template_files_table.item(row, 1)
            if not key_item:
                continue
            key = str(key_item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
            value = path_item.text().strip() if path_item else ""
            row_definition = next(
                (definition for definition in self._template_rows() if definition[0] == key),
                None,
            )
            expected = (
                f"{project_root}/{row_definition[2]}"
                if project_root and row_definition else ""
            )
            self._set_template_setting(
                key,
                "" if value == expected else value,
            )
        return copy.deepcopy(self.template_file_settings)

    def setup_naming_tab(self):
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        form.setContentsMargins(12, 12, 12, 12)
        self.playblast_filename_input = QtWidgets.QLineEdit()
        self.playblast_filename_input.setPlaceholderText(
            "{project}*{episode}*{sequence}*{shot}*{dept}_{preview}_v{version}*t{take}*####.{ext}"
        )
        form.addRow("Smart Playblast File Name:", self.playblast_filename_input)
        tokens = QtWidgets.QLabel(
            "Tokens: {project} {episode} {sequence} {shot} {dept} "
            "{preview} {version} {take} {ext}"
        )
        tokens.setWordWrap(True)
        form.addRow("", tokens)
        return page

    def setup_context_tab(self):
        page = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(page)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        left = QtWidgets.QWidget()
        left.setFixedWidth(220)
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QtWidgets.QLabel("<b>Context</b>"))
        self.context_list = QtWidgets.QListWidget()
        self.context_list.setStyleSheet("QListWidget::item { height: 30px; }")
        left_layout.addWidget(self.context_list, 2)
        left_layout.addWidget(QtWidgets.QLabel("<b>Versions</b>"))
        self.context_version_list = QtWidgets.QListWidget()
        self.context_version_list.setStyleSheet("QListWidget::item { height: 28px; }")
        left_layout.addWidget(self.context_version_list, 2)
        self.context_active_label = QtWidgets.QLabel("Active  -")
        self.context_active_label.setStyleSheet("color: #77d66b; font-weight: bold; padding: 6px;")
        left_layout.addWidget(self.context_active_label)
        version_buttons = QtWidgets.QHBoxLayout()
        self.context_new_version_btn = QtWidgets.QPushButton("+ New Version")
        self.context_set_active_btn = QtWidgets.QPushButton("Set Active")
        version_buttons.addWidget(self.context_new_version_btn)
        version_buttons.addWidget(self.context_set_active_btn)
        left_layout.addLayout(version_buttons)

        main = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        profile_group = QtWidgets.QGroupBox("Quality Profiles")
        profile_layout = QtWidgets.QVBoxLayout(profile_group)
        profile_actions = QtWidgets.QHBoxLayout()
        profile_actions.addStretch()
        self.context_add_profile_btn = QtWidgets.QPushButton("+ Add Profile")
        self.context_delete_profile_btn = QtWidgets.QPushButton("Delete")
        profile_actions.addWidget(self.context_add_profile_btn)
        profile_actions.addWidget(self.context_delete_profile_btn)
        profile_layout.addLayout(profile_actions)
        self.context_profile_table = QtWidgets.QTableWidget(0, 5)
        self.context_profile_table.setHorizontalHeaderLabels(
            ["Profile", "Model", "Look", "Rig", "Groom"]
        )
        self._configure_context_table(self.context_profile_table)
        profile_layout.addWidget(self.context_profile_table)
        main_layout.addWidget(profile_group, 1)

        lower_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        representation_group = QtWidgets.QGroupBox("Representations")
        representation_layout = QtWidgets.QVBoxLayout(representation_group)
        representation_actions = QtWidgets.QHBoxLayout()
        representation_actions.addStretch()
        self.context_add_representation_btn = QtWidgets.QPushButton("+ Add")
        self.context_delete_representation_btn = QtWidgets.QPushButton("Delete")
        representation_actions.addWidget(self.context_add_representation_btn)
        representation_actions.addWidget(self.context_delete_representation_btn)
        representation_layout.addLayout(representation_actions)
        self.context_representation_table = QtWidgets.QTableWidget(0, 2)
        self.context_representation_table.setHorizontalHeaderLabels(["Type", "Name"])
        self._configure_context_table(self.context_representation_table)
        representation_layout.addWidget(self.context_representation_table)
        lower_splitter.addWidget(representation_group)

        output_group = QtWidgets.QGroupBox("Output Formats")
        output_layout = QtWidgets.QVBoxLayout(output_group)
        output_header = QtWidgets.QHBoxLayout()
        self.context_output_target_label = QtWidgets.QLabel("Select a representation")
        self.context_output_target_label.setStyleSheet("color: #aeb7c2;")
        output_header.addWidget(self.context_output_target_label)
        output_header.addStretch()
        self.context_add_output_btn = QtWidgets.QPushButton("+ Add")
        self.context_delete_output_btn = QtWidgets.QPushButton("Delete")
        output_header.addWidget(self.context_add_output_btn)
        output_header.addWidget(self.context_delete_output_btn)
        output_layout.addLayout(output_header)
        self.context_output_table = QtWidgets.QTableWidget(0, 3)
        self.context_output_table.setHorizontalHeaderLabels(["Format", "Enabled", "Options"])
        self._configure_context_table(self.context_output_table)
        self.context_output_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.context_output_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.context_output_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        output_layout.addWidget(self.context_output_table, 1)

        details = QtWidgets.QFormLayout()
        self.context_output_extension = QtWidgets.QLineEdit()
        self.context_output_bake = QtWidgets.QComboBox()
        self.context_output_bake.addItems(["Off", "On"])
        self.context_output_root_motion = QtWidgets.QComboBox()
        self.context_output_root_motion.addItems(["None", "Preserve", "Extract"])
        self.context_output_axis = QtWidgets.QComboBox()
        self.context_output_axis.addItems(["Y-up", "Z-up"])
        self.context_output_unit = QtWidgets.QComboBox()
        self.context_output_unit.addItems(["mm", "cm", "m"])
        details.addRow("File Extension", self.context_output_extension)
        details.addRow("Bake Animation", self.context_output_bake)
        details.addRow("Root Motion", self.context_output_root_motion)
        details.addRow("Axis", self.context_output_axis)
        details.addRow("Unit", self.context_output_unit)
        output_layout.addLayout(details)
        lower_splitter.addWidget(output_group)
        lower_splitter.setStretchFactor(0, 1)
        lower_splitter.setStretchFactor(1, 1)
        main_layout.addWidget(lower_splitter, 2)

        root.addWidget(left)
        root.addWidget(main, 1)

        self.context_list.currentItemChanged.connect(self._on_context_changed)
        self.context_version_list.currentItemChanged.connect(self._on_context_version_changed)
        self.context_new_version_btn.clicked.connect(self._add_context_version)
        self.context_set_active_btn.clicked.connect(self._set_active_context_version)
        self.context_add_profile_btn.clicked.connect(self._add_context_profile)
        self.context_delete_profile_btn.clicked.connect(self._delete_context_profile)
        self.context_add_representation_btn.clicked.connect(self._add_context_representation)
        self.context_delete_representation_btn.clicked.connect(self._delete_context_representation)
        self.context_representation_table.itemChanged.connect(self._on_representation_item_changed)
        self.context_representation_table.itemSelectionChanged.connect(self._on_representation_selected)
        self.context_add_output_btn.clicked.connect(self._add_context_output)
        self.context_delete_output_btn.clicked.connect(self._delete_context_output)
        self.context_output_table.itemSelectionChanged.connect(self._on_output_selected)
        return page

    @staticmethod
    def _configure_context_table(table):
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        table.horizontalHeader().setStretchLastSection(True)

    def setup_resolvers_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        header = QtWidgets.QHBoxLayout()
        header.addWidget(QtWidgets.QLabel("<b>Asset Publish Resolvers</b>"))
        header.addStretch()
        add_btn = QtWidgets.QPushButton("+ Add")
        delete_btn = QtWidgets.QPushButton("Delete")
        header.addWidget(add_btn)
        header.addWidget(delete_btn)
        layout.addLayout(header)

        self.resolver_table = QtWidgets.QTableWidget(0, 7)
        self.resolver_table.setHorizontalHeaderLabels(
            [
                "Consumer",
                "Department",
                "Asset Context",
                "Version",
                "Formats",
                "Fallback Contexts",
                "Fallback Version",
            ]
        )
        self._configure_context_table(self.resolver_table)
        header_view = self.resolver_table.horizontalHeader()
        for column in (0, 1, 2, 3, 6):
            header_view.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.resolver_table)
        add_btn.clicked.connect(self._add_resolver_row)
        delete_btn.clicked.connect(self._delete_resolver_row)
        return page

    def _add_resolver_row(self, values=None):
        values = values or ["shot", "default", "work", "approved", "ma, mb", "asset_work", "latest"]
        row = self.resolver_table.rowCount()
        self.resolver_table.insertRow(row)
        for column, value in enumerate(values):
            self.resolver_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
        self.resolver_table.selectRow(row)

    def _delete_resolver_row(self):
        row = self.resolver_table.currentRow()
        if row >= 0:
            self.resolver_table.removeRow(row)

    def _load_resolver_editor(self, project_name=None):
        data = {}
        for root in self._context_config_roots(project_name):
            data = merge_dicts(data, load_yml(os.path.join(root, "resolvers.yml")))
        self.resolver_table.setRowCount(0)
        for rule in data.get("asset_resolvers") or []:
            if not isinstance(rule, dict):
                continue
            formats = rule.get("formats") or []
            fallback_contexts = rule.get("fallback_contexts") or []
            if isinstance(formats, str):
                formats = [formats]
            if isinstance(fallback_contexts, str):
                fallback_contexts = [fallback_contexts]
            self._add_resolver_row(
                [
                    rule.get("consumer", "shot"),
                    rule.get("department", "default"),
                    rule.get("context", "work"),
                    rule.get("version", "approved"),
                    ", ".join(str(value) for value in formats),
                    ", ".join(str(value) for value in fallback_contexts),
                    rule.get("fallback_version", "latest"),
                ]
            )

    def _resolver_rules_from_table(self):
        rules = []
        for row in range(self.resolver_table.rowCount()):
            values = []
            for column in range(self.resolver_table.columnCount()):
                item = self.resolver_table.item(row, column)
                values.append(item.text().strip() if item else "")
            consumer, department, context, version, formats, fallbacks, fallback_version = values
            rules.append(
                {
                    "consumer": consumer.lower(),
                    "department": department.lower(),
                    "context": context.lower(),
                    "version": version.lower(),
                    "formats": [value.strip().lower().lstrip(".") for value in formats.split(",") if value.strip()],
                    "fallback_contexts": [value.strip().lower() for value in fallbacks.split(",") if value.strip()],
                    "fallback_version": fallback_version.lower(),
                }
            )
        return rules

    def _validate_resolver_rules(self):
        errors = []
        seen = set()
        valid_aliases = {"latest", "approved", "released", "stable"}
        for index, rule in enumerate(self._resolver_rules_from_table(), 1):
            label = f"Resolver row {index}"
            for key in ("consumer", "department", "context", "version"):
                if not rule.get(key):
                    errors.append(f"{label}: {key} is required")
            key = (rule.get("consumer"), rule.get("department"))
            if key in seen:
                errors.append(f"{label}: duplicate consumer/department: {'/'.join(key)}")
            seen.add(key)
            if not rule.get("formats"):
                errors.append(f"{label}: at least one format is required")
            for version_key in ("version", "fallback_version"):
                value = rule.get(version_key) or ""
                if value not in valid_aliases and not re.fullmatch(r"v\d+", value):
                    errors.append(f"{label}: invalid {version_key}: {value}")
        return errors

    def _save_resolver_rules(self, proj_dir):
        save_yml(
            os.path.join(proj_dir, "resolvers.yml"),
            {"asset_resolvers": self._resolver_rules_from_table()},
        )

    def setup_software_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        left_layout = QtWidgets.QVBoxLayout()
        
        icon_size = QtCore.QSize(24, 24)
        list_style = "QListWidget::item { height: 32px; }"

        # 上段
        left_layout.addWidget(QtWidgets.QLabel("<b>Global Software List (Master)</b>"))
        self.global_list = QtWidgets.QListWidget()
        self.global_list.setIconSize(icon_size)
        self.global_list.setStyleSheet(list_style)
        left_layout.addWidget(self.global_list)
        
        self.add_to_proj_btn = QtWidgets.QPushButton("▼ Add to Project ▼")
        self.add_to_proj_btn.clicked.connect(self.add_to_selected)
        left_layout.addWidget(self.add_to_proj_btn)
        
        # 下段
        left_layout.addWidget(QtWidgets.QLabel("<b>Project Custom / Enabled</b>"))
        self.selected_list = QtWidgets.QListWidget()
        self.selected_list.setIconSize(icon_size)
        self.selected_list.setStyleSheet(list_style)
        self.selected_list.currentItemChanged.connect(self.on_soft_selection_changed)
        left_layout.addWidget(self.selected_list)
        
        sel_btns = QtWidgets.QHBoxLayout()
        self.rem_soft_btn = QtWidgets.QPushButton("▲ Remove ▲")
        self.rem_soft_btn.clicked.connect(self.remove_from_selected)
        self.add_custom_exe_btn = QtWidgets.QPushButton("+ Add Custom Exe/Bat")
        self.add_custom_exe_btn.clicked.connect(self.add_custom_software)
        sel_btns.addWidget(self.rem_soft_btn); sel_btns.addWidget(self.add_custom_exe_btn)
        left_layout.addLayout(sel_btns)

        # 右側
        right_layout = QtWidgets.QVBoxLayout()
        self.software_settings_table = QtWidgets.QTableWidget(0, 3)
        self.software_settings_table.setHorizontalHeaderLabels(["Setting", "Type", "Value"])
        self.software_settings_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeToContents
        )
        self.software_settings_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeToContents
        )
        self.software_settings_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.Stretch
        )
        self.software_settings_table.verticalHeader().setVisible(False)
        self.software_settings_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )

        settings_btns = QtWidgets.QHBoxLayout()
        add_setting_btn = QtWidgets.QPushButton("+ Setting")
        add_env_setting_btn = QtWidgets.QPushButton("+ Env Variable")
        del_setting_btn = QtWidgets.QPushButton("- Setting")
        add_setting_btn.clicked.connect(self.add_software_setting)
        add_env_setting_btn.clicked.connect(self.add_environment_setting)
        del_setting_btn.clicked.connect(self.remove_software_setting)
        settings_btns.addWidget(add_setting_btn)
        settings_btns.addWidget(add_env_setting_btn)
        settings_btns.addStretch()
        settings_btns.addWidget(del_setting_btn)

        self.env_tree = QtWidgets.QTreeWidget()
        self.env_tree.setColumnCount(1)
        self.env_tree.setHeaderLabels(["Path Variable / Entry"])
        self.env_tree.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked)

        tree_btns = QtWidgets.QHBoxLayout()
        add_var_btn = QtWidgets.QPushButton("+ Path Variable")
        add_path_btn = QtWidgets.QPushButton("+ Path Entry")
        del_node_btn = QtWidgets.QPushButton("- Del")
        add_var_btn.clicked.connect(self.add_tree_path_variable); add_path_btn.clicked.connect(self.add_tree_path); del_node_btn.clicked.connect(self.remove_tree_node)
        tree_btns.addWidget(add_var_btn); tree_btns.addWidget(add_path_btn); tree_btns.addStretch(); tree_btns.addWidget(del_node_btn)
        
        right_layout.addWidget(QtWidgets.QLabel("Software Settings:"))
        right_layout.addWidget(self.software_settings_table, 1)
        right_layout.addLayout(settings_btns)
        right_layout.addWidget(QtWidgets.QLabel("Path Settings:"))
        right_layout.addWidget(self.env_tree)
        right_layout.addLayout(tree_btns)
        
        layout.addLayout(left_layout, 1); layout.addLayout(right_layout, 2)
        return page

    def _add_item_with_icon(self, list_widget, sid, icon_name, exe_path=""):
        """アイコン名がなければ実行ファイルからアイコンを抽出してアイテムを追加"""
        item = QtWidgets.QListWidgetItem(sid)
        icon = None

        # 1. 指定アイコンがある場合
        if icon_name:
            icon_path = os.path.normpath(os.path.join(CURRENT_DIR, "..", "icons", icon_name))
            if os.path.exists(icon_path):
                icon = QtGui.QIcon(icon_path)

        # 2. なければ実行ファイルのOS標準アイコンを取得
        if not icon and exe_path:
            clean_path = os.path.normpath(exe_path)
            if os.path.exists(clean_path):
                file_info = QtCore.QFileInfo(clean_path)
                icon = self.icon_provider.icon(file_info)

        if icon:
            item.setIcon(icon)
        
        list_widget.addItem(item)
        return item

    def on_soft_selection_changed(self, current, previous):
        if previous:
            self._save_tree_to_memory(previous.text())
        if not current:
            self.software_settings_table.setRowCount(0)
            self.env_tree.clear(); return
            
        soft_id = current.text()
        if soft_id not in self.software_configs:
            proj_dir = os.path.join(PROJECTS_ROOT, self.name_input.text().strip())
            spec_path = os.path.join(proj_dir, f"software_{soft_id}.yml")
            def_path = os.path.join(DEFAULT_DIR, f"software_{soft_id}.yml")
            default_config = load_yml(def_path)
            if os.path.exists(spec_path):
                self.software_configs[soft_id] = merge_dicts(
                    default_config, load_yml(spec_path)
                )
            else:
                self.software_configs[soft_id] = default_config
        
        self._populate_tree(self.software_configs[soft_id])

    def add_to_selected(self):
        curr = self.global_list.currentItem()
        if not curr: return
        sid = curr.text()
        
        for i in range(self.selected_list.count()):
            if self.selected_list.item(i).text() == sid: return
            
        master_data = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        master_info = master_data.get(sid, {})
        
        # アイコンまたはパスからアイコン付きアイテムを作成
        self._add_item_with_icon(self.selected_list, sid, master_info.get('icon', ""), master_info.get('path', ""))
        
        default_path = os.path.join(DEFAULT_DIR, f"software_{sid}.yml")
        base_config = load_yml(default_path)
        base_config['path'] = master_info.get('path', "")
        base_config['icon'] = master_info.get('icon', "")
        
        self.software_configs[sid] = base_config
        self.selected_list.setCurrentRow(self.selected_list.count() - 1)
        self._populate_tree(self.software_configs[sid])

    def add_custom_software(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Exe/Bat", "", "Executables (*.exe *.bat *.cmd)")
        if p:
            sid = os.path.splitext(os.path.basename(p))[0]
            for i in range(self.selected_list.count()):
                if self.selected_list.item(i).text() == sid: return

            # カスタムツールはパスからアイコンを抽出
            self._add_item_with_icon(self.selected_list, sid, "", p)
            
            self.software_configs[sid] = {
                'path': p.replace("\\", "/"),
                'icon': "",
                'env_vars': {},
                'paths': {}
            }
            self.selected_list.setCurrentRow(self.selected_list.count()-1)
            self._populate_tree(self.software_configs[sid])

    def _context_config_roots(self, project_name=None):
        roots = [DEFAULT_DIR]
        name = project_name or self.target_project or self.name_input.text().strip()
        if name:
            roots.append(os.path.join(PROJECTS_ROOT, name))
        return roots

    def _load_context_editor(self, project_name=None):
        self._context_loading = True
        self.context_configs = {}
        self.context_active_versions = {}
        roots = self._context_config_roots(project_name)
        asset_config = {}
        for root in roots:
            asset_config = merge_dicts(
                asset_config,
                load_yml(os.path.join(root, "templates_assets.yml")),
            )
        self.asset_subset_catalog = asset_subset_catalog(asset_config)
        context_names = set()
        for root in roots:
            context_root = os.path.join(root, "contexts")
            if os.path.isdir(context_root):
                context_names.update(
                    name for name in os.listdir(context_root)
                    if os.path.isdir(os.path.join(context_root, name))
                )
        context_names.update(["asset", "layout", "anim", "lighting"])

        for context_name in sorted(context_names, key=lambda value: (["asset", "layout", "anim", "lighting"].index(value) if value in ["asset", "layout", "anim", "lighting"] else 99, value)):
            versions = set()
            for root in roots:
                folder = os.path.join(root, "contexts", context_name)
                if os.path.isdir(folder):
                    versions.update(
                        os.path.splitext(name)[0]
                        for name in os.listdir(folder)
                        if re.fullmatch(r"v\d+\.ya?ml", name, re.IGNORECASE)
                    )
            self.context_configs[context_name] = {}
            for version in sorted(versions):
                data = {}
                for root in roots:
                    for extension in (".yml", ".yaml"):
                        path = os.path.join(root, "contexts", context_name, version + extension)
                        if os.path.isfile(path):
                            data = merge_dicts(data, load_yml(path))
                if context_name == "asset":
                    for department, values in (data.get("representations") or {}).items():
                        if isinstance(values, str):
                            values = [values]
                        target = self.asset_subset_catalog.setdefault(str(department), [])
                        if isinstance(values, list):
                            for value in values:
                                text = str(value).strip()
                                if text and text not in target:
                                    target.append(text)
                self.context_configs[context_name][version] = self._normalize_context_config(
                    context_name, version, data
                )

        settings = {}
        for root in roots:
            settings = merge_dicts(settings, load_yml(os.path.join(root, "project_settings.yml")))
        self.context_active_versions = dict(settings.get("active_contexts") or {})

        self.context_list.clear()
        for context_name in self.context_configs:
            item = QtWidgets.QListWidgetItem(context_name.title())
            item.setData(QtCore.Qt.ItemDataRole.UserRole, context_name)
            self.context_list.addItem(item)
        self._context_loading = False
        if self.context_list.count():
            self.context_list.setCurrentRow(0)

    @staticmethod
    def _normalize_context_config(context_name, version, data):
        result = copy.deepcopy(data or {})
        result["name"] = str(result.get("name") or context_name)
        match = re.search(r"(\d+)$", version)
        result["version"] = int(match.group(1)) if match else result.get("version", 1)
        profiles = result.get("quality_profiles") or {}
        normalized_profiles = {}
        representations = copy.deepcopy(result.get("representations") or {})
        for profile_name, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            normalized_profiles[str(profile_name)] = {}
            for publish_type, value in profile.items():
                values = list(value.values()) if isinstance(value, dict) else [value]
                values = [str(item) for item in values if item not in (None, "", "none")]
                selected = str(value.get("default") or next(iter(value.values()), "none")) if isinstance(value, dict) else str(value or "none")
                normalized_profiles[str(profile_name)][str(publish_type)] = selected
                existing = representations.setdefault(str(publish_type), [])
                if isinstance(existing, dict):
                    existing = list(existing.keys())
                    representations[str(publish_type)] = existing
                for item in values:
                    if item not in existing:
                        existing.append(item)
        result["quality_profiles"] = normalized_profiles
        result.pop("representations", None)
        result.setdefault("output_formats", {})
        return result

    def _on_context_changed(self, current, _previous):
        if self._context_loading:
            return
        self._store_context_editor()
        self._current_context_key = current.data(QtCore.Qt.ItemDataRole.UserRole) if current else None
        self._current_context_version = None
        self.context_version_list.clear()
        versions = sorted(self.context_configs.get(self._current_context_key, {}))
        for version in versions:
            self.context_version_list.addItem(version)
        active = self.context_active_versions.get(self._current_context_key, "")
        self.context_active_label.setText(f"Active  {active or '-'}")
        index = versions.index(active) if active in versions else (len(versions) - 1)
        if index >= 0:
            self.context_version_list.setCurrentRow(index)
        else:
            self._clear_context_editor()

    def _on_context_version_changed(self, current, _previous):
        if self._context_loading:
            return
        self._store_context_editor()
        self._current_context_version = current.text() if current else None
        self._populate_context_editor()

    def _current_context_data(self):
        return self.context_configs.get(self._current_context_key, {}).get(self._current_context_version)

    def _clear_context_editor(self):
        self._context_loading = True
        self.context_profile_table.setRowCount(0)
        self.context_representation_table.setRowCount(0)
        self.context_output_table.setRowCount(0)
        self.context_output_target_label.setText("Select a representation")
        self._current_output_key = None
        self._current_output_format = None
        self._context_loading = False

    def _populate_context_editor(self):
        data = self._current_context_data()
        self._clear_context_editor()
        if not data:
            return
        self._context_loading = True
        representations = self.asset_subset_catalog
        for publish_type in sorted(representations):
            for name in representations[publish_type]:
                row = self.context_representation_table.rowCount()
                self.context_representation_table.insertRow(row)
                self.context_representation_table.setItem(row, 0, QtWidgets.QTableWidgetItem(publish_type))
                self.context_representation_table.setItem(row, 1, QtWidgets.QTableWidgetItem(name))
        for profile_name, profile in (data.get("quality_profiles") or {}).items():
            self._append_profile_row(profile_name, profile, representations)
        self.context_profile_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.context_representation_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._context_loading = False
        if self.context_representation_table.rowCount():
            self.context_representation_table.selectRow(0)

    def _append_profile_row(self, profile_name, profile=None, representations=None):
        profile = profile or {}
        representations = representations or self._representations_from_table()
        row = self.context_profile_table.rowCount()
        self.context_profile_table.insertRow(row)
        self.context_profile_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(profile_name)))
        for column, publish_type in enumerate(("model", "look", "rig", "groom"), 1):
            combo = QtWidgets.QComboBox()
            combo.addItem("none")
            combo.addItems(list(representations.get(publish_type, [])))
            value = str(profile.get(publish_type, "none"))
            if combo.findText(value) < 0 and value:
                combo.addItem(value)
            combo.setCurrentText(value or "none")
            self.context_profile_table.setCellWidget(row, column, combo)

    def _representations_from_table(self):
        result = {}
        for row in range(self.context_representation_table.rowCount()):
            type_item = self.context_representation_table.item(row, 0)
            name_item = self.context_representation_table.item(row, 1)
            publish_type = type_item.text().strip() if type_item else ""
            name = name_item.text().strip() if name_item else ""
            if publish_type and name:
                result.setdefault(publish_type, []).append(name)
        return result

    def _store_context_editor(self):
        data = self._current_context_data()
        if not data or self._context_loading:
            return
        self._store_output_table()
        representations = self._representations_from_table()
        profiles = {}
        for row in range(self.context_profile_table.rowCount()):
            name_item = self.context_profile_table.item(row, 0)
            name = name_item.text().strip() if name_item else ""
            if not name:
                continue
            profile = {}
            for column, publish_type in enumerate(("model", "look", "rig", "groom"), 1):
                combo = self.context_profile_table.cellWidget(row, column)
                profile[publish_type] = combo.currentText().strip() if combo else "none"
            profiles[name] = profile
        self.asset_subset_catalog = representations
        data.pop("representations", None)
        data["quality_profiles"] = profiles

    def _add_context_version(self):
        if not self._current_context_key:
            return
        self._store_context_editor()
        versions = self.context_configs.setdefault(self._current_context_key, {})
        numbers = [int(match.group(1)) for value in versions for match in [re.match(r"v(\d+)$", value)] if match]
        version = f"v{(max(numbers) + 1 if numbers else 1):03d}"
        source = self._current_context_data() or {
            "name": self._current_context_key,
            "quality_profiles": {},
            "output_formats": {},
        }
        versions[version] = copy.deepcopy(source)
        versions[version]["version"] = int(version[1:])
        self._context_loading = True
        self.context_version_list.addItem(version)
        self._context_loading = False
        self.context_version_list.setCurrentRow(self.context_version_list.count() - 1)

    def _set_active_context_version(self):
        if self._current_context_key and self._current_context_version:
            self.context_active_versions[self._current_context_key] = self._current_context_version
            self.context_active_label.setText(f"Active  {self._current_context_version}")

    def _add_context_profile(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Add Profile", "Profile name:")
        name = name.strip().upper()
        if not ok or not name:
            return
        existing = [self.context_profile_table.item(row, 0).text().strip() for row in range(self.context_profile_table.rowCount())]
        if name in existing:
            QtWidgets.QMessageBox.warning(self, "Duplicate Profile", f"Profile already exists: {name}")
            return
        self._append_profile_row(name)

    def _delete_context_profile(self):
        row = self.context_profile_table.currentRow()
        if row >= 0:
            self.context_profile_table.removeRow(row)

    def _add_context_representation(self):
        row = self.context_representation_table.rowCount()
        self.context_representation_table.insertRow(row)
        self.context_representation_table.setItem(row, 0, QtWidgets.QTableWidgetItem("model"))
        self.context_representation_table.setItem(row, 1, QtWidgets.QTableWidgetItem("new"))
        self.context_representation_table.selectRow(row)
        self._refresh_profile_choices()

    def _delete_context_representation(self):
        row = self.context_representation_table.currentRow()
        if row >= 0:
            self._store_output_table()
            key = self._selected_representation_key()
            data = self._current_context_data()
            if key and data:
                publish_type, representation = key
                by_type = (data.get("output_formats") or {}).get(publish_type) or {}
                by_type.pop(representation, None)
            self.context_representation_table.removeRow(row)
            self._current_output_key = None
            self.context_output_table.setRowCount(0)
            self._refresh_profile_choices()

    def _refresh_profile_choices(self, *_args):
        if self._context_loading:
            return
        self._context_loading = True
        representations = self._representations_from_table()
        for row in range(self.context_profile_table.rowCount()):
            for column, publish_type in enumerate(("model", "look", "rig", "groom"), 1):
                combo = self.context_profile_table.cellWidget(row, column)
                if not combo:
                    continue
                current = combo.currentText()
                combo.clear()
                combo.addItem("none")
                combo.addItems(representations.get(publish_type, []))
                combo.setCurrentText(current if combo.findText(current) >= 0 else "none")
        self._context_loading = False

    def _on_representation_item_changed(self, _item):
        if self._context_loading:
            return
        old_key = self._current_output_key
        new_key = self._selected_representation_key()
        if old_key and new_key and old_key != new_key:
            self._store_output_table()
            data = self._current_context_data()
            output_formats = data.setdefault("output_formats", {}) if data else {}
            old_type, old_name = old_key
            new_type, new_name = new_key
            old_formats = (output_formats.get(old_type) or {}).pop(old_name, None)
            if old_formats is not None:
                output_formats.setdefault(new_type, {})[new_name] = old_formats
            self._current_output_key = new_key
            self.context_output_target_label.setText(" / ".join(new_key))
        self._refresh_profile_choices()

    def _selected_representation_key(self):
        row = self.context_representation_table.currentRow()
        if row < 0:
            return None
        type_item = self.context_representation_table.item(row, 0)
        name_item = self.context_representation_table.item(row, 1)
        publish_type = type_item.text().strip() if type_item else ""
        name = name_item.text().strip() if name_item else ""
        return (publish_type, name) if publish_type and name else None

    def _on_representation_selected(self):
        if self._context_loading:
            return
        self._store_output_table()
        self._current_output_key = self._selected_representation_key()
        self._populate_output_table()

    def _output_config_for_key(self, create=False):
        data = self._current_context_data()
        if not data or not self._current_output_key:
            return {}
        publish_type, name = self._current_output_key
        output_formats = data.setdefault("output_formats", {})
        if create:
            return output_formats.setdefault(publish_type, {}).setdefault(name, {})
        return (output_formats.get(publish_type) or {}).get(name) or {}

    def _populate_output_table(self):
        self._context_loading = True
        self.context_output_table.setRowCount(0)
        self._current_output_format = None
        if self._current_output_key:
            self.context_output_target_label.setText(" / ".join(self._current_output_key))
            for format_name, settings in self._output_config_for_key().items():
                row = self.context_output_table.rowCount()
                self.context_output_table.insertRow(row)
                self.context_output_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(format_name)))
                enabled = QtWidgets.QTableWidgetItem()
                enabled.setFlags(enabled.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                enabled.setCheckState(QtCore.Qt.CheckState.Checked if settings.get("enabled", True) else QtCore.Qt.CheckState.Unchecked)
                self.context_output_table.setItem(row, 1, enabled)
                self.context_output_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(settings.get("summary") or "")))
        else:
            self.context_output_target_label.setText("Select a representation")
        self._context_loading = False
        if self.context_output_table.rowCount():
            self.context_output_table.selectRow(0)
        else:
            self._set_output_details_enabled(False)

    def _store_output_table(self):
        if self._context_loading or not self._current_output_key:
            return
        self._store_output_details()
        formats = {}
        previous = self._output_config_for_key()
        for row in range(self.context_output_table.rowCount()):
            format_item = self.context_output_table.item(row, 0)
            enabled_item = self.context_output_table.item(row, 1)
            summary_item = self.context_output_table.item(row, 2)
            name = format_item.text().strip().lower() if format_item else ""
            if not name:
                continue
            settings = copy.deepcopy(previous.get(name) or {})
            settings["enabled"] = enabled_item.checkState() == QtCore.Qt.CheckState.Checked if enabled_item else True
            settings["summary"] = summary_item.text().strip() if summary_item else ""
            formats[name] = settings
        data = self._current_context_data()
        publish_type, representation = self._current_output_key
        data.setdefault("output_formats", {}).setdefault(publish_type, {})[representation] = formats

    def _on_output_selected(self):
        if self._context_loading:
            return
        self._store_output_details()
        row = self.context_output_table.currentRow()
        item = self.context_output_table.item(row, 0) if row >= 0 else None
        self._current_output_format = item.text().strip().lower() if item else None
        settings = self._output_config_for_key().get(self._current_output_format, {}) if self._current_output_format else {}
        self._context_loading = True
        self._set_output_details_enabled(bool(self._current_output_format))
        self.context_output_extension.setText(str(settings.get("extension") or (f".{self._current_output_format}" if self._current_output_format else "")))
        self.context_output_bake.setCurrentText("On" if settings.get("bake_animation") else "Off")
        self.context_output_root_motion.setCurrentText(str(settings.get("root_motion") or "None").title())
        self.context_output_axis.setCurrentText(str(settings.get("axis") or "Y-up"))
        self.context_output_unit.setCurrentText(str(settings.get("unit") or "cm"))
        self._context_loading = False

    def _set_output_details_enabled(self, enabled):
        for widget in (self.context_output_extension, self.context_output_bake, self.context_output_root_motion, self.context_output_axis, self.context_output_unit):
            widget.setEnabled(enabled)

    def _store_output_details(self):
        if self._context_loading or not getattr(self, "_current_output_format", None):
            return
        settings = self._output_config_for_key(create=True).setdefault(self._current_output_format, {})
        settings["extension"] = self.context_output_extension.text().strip()
        settings["bake_animation"] = self.context_output_bake.currentText() == "On"
        settings["root_motion"] = self.context_output_root_motion.currentText().lower()
        settings["axis"] = self.context_output_axis.currentText()
        settings["unit"] = self.context_output_unit.currentText()

    def _add_context_output(self):
        if not self._selected_representation_key():
            QtWidgets.QMessageBox.information(self, "Output Format", "Select a representation first.")
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Add Output Format", "Format name:")
        name = name.strip().lower()
        if not ok or not name:
            return
        existing = [self.context_output_table.item(row, 0).text().strip().lower() for row in range(self.context_output_table.rowCount())]
        if name in existing:
            QtWidgets.QMessageBox.warning(self, "Duplicate Format", f"Output format already exists: {name}")
            return
        row = self.context_output_table.rowCount()
        self.context_output_table.insertRow(row)
        self.context_output_table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
        enabled = QtWidgets.QTableWidgetItem()
        enabled.setFlags(enabled.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        enabled.setCheckState(QtCore.Qt.CheckState.Checked)
        self.context_output_table.setItem(row, 1, enabled)
        self.context_output_table.setItem(row, 2, QtWidgets.QTableWidgetItem(""))
        self.context_output_table.selectRow(row)

    def _delete_context_output(self):
        row = self.context_output_table.currentRow()
        if row >= 0:
            self.context_output_table.removeRow(row)
            self._current_output_format = None

    def _validate_context_configs(self):
        self._store_context_editor()
        errors = []
        for context_name, versions in self.context_configs.items():
            for version, data in versions.items():
                profiles = data.get("quality_profiles") or {}
                representations = self.asset_subset_catalog
                if not profiles:
                    errors.append(f"{context_name}/{version}: at least one Quality Profile is required")
                for publish_type, names in representations.items():
                    clean = [str(name).strip() for name in names]
                    if not publish_type.strip() or any(not name for name in clean):
                        errors.append(f"{context_name}/{version}: representation Type and Name are required")
                    if len(clean) != len(set(clean)):
                        errors.append(f"{context_name}/{version}: duplicate representation in {publish_type}")
                for profile_name, profile in profiles.items():
                    if not profile_name.strip():
                        errors.append(f"{context_name}/{version}: Profile name is required")
                    for publish_type, value in profile.items():
                        if value not in ("", "none") and value not in representations.get(publish_type, []):
                            errors.append(f"{context_name}/{version}/{profile_name}: {publish_type}/{value} is not registered")
                for publish_type, by_representation in (data.get("output_formats") or {}).items():
                    for representation, formats in (by_representation or {}).items():
                        if representation not in representations.get(publish_type, []):
                            errors.append(f"{context_name}/{version}: output target {publish_type}/{representation} is not registered")
                        for format_name, settings in (formats or {}).items():
                            if not str(format_name).strip():
                                errors.append(f"{context_name}/{version}/{publish_type}/{representation}: format name is required")
                            if settings.get("enabled", True) and not str(settings.get("extension") or "").strip():
                                errors.append(f"{context_name}/{version}/{publish_type}/{representation}/{format_name}: extension is required")
        return errors

    def _save_context_configs(self, proj_dir):
        for context_name, versions in self.context_configs.items():
            for version, data in versions.items():
                save_yml(os.path.join(proj_dir, "contexts", context_name, f"{version}.yml"), data)
        settings_path = os.path.join(proj_dir, "project_settings.yml")
        settings = load_yml(settings_path)
        settings["active_contexts"] = dict(self.context_active_versions)
        save_yml(settings_path, settings)
        asset_config_path = os.path.join(proj_dir, "templates_assets.yml")
        asset_config = load_yml(asset_config_path)
        asset_config["asset_subsets"] = copy.deepcopy(self.asset_subset_catalog)
        save_yml(asset_config_path, asset_config)

    def save_config(self):
        self._save_tree_to_memory()
        name = self.name_input.text().strip()
        base = self.path_input.text().strip()
        if not name or not base: return

        config_errors = self._validate_context_configs() + self._validate_resolver_rules()
        if config_errors:
            QtWidgets.QMessageBox.warning(
                self,
                "Configuration Validation Failed",
                "Configuration could not be saved:\n\n" + "\n".join(f"- {error}" for error in config_errors[:20]),
            )
            return

        google_inputs = {}
        a_tab = self.anchors_table["table"]
        google_labels = {
            ASSET_LIST_URL_LABEL: "asset_list",
            SHOT_LIST_URL_LABEL: "shot_list",
        }
        for row in range(a_tab.rowCount()):
            key_item = a_tab.item(row, 0)
            value_item = a_tab.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            if key not in google_labels:
                continue
            value = value_item.text().strip() if value_item else ""
            if value and not google_sheet_id(value):
                QtWidgets.QMessageBox.warning(
                    self,
                    "Invalid Google Sheets URL",
                    f"{key} is not a valid Google Sheets URL or spreadsheet ID.",
                )
                return
            google_inputs[google_labels[key]] = value
        
        proj_dir = os.path.join(PROJECTS_ROOT, name); os.makedirs(proj_dir, exist_ok=True)
        existing_config = load_yml(os.path.join(proj_dir, "templates_base.yml"))
        config = {
            'anchors': {'project_name': name, 'project_root': f"{base}/{name}".replace("\\", "/")},
            'enabled_softwares': [],
            'shot_depts': [self.shot_depts_list["list"].item(i).text() for i in range(self.shot_depts_list["list"].count())],
            'shot_tasks': self._shot_tasks_from_ui(),
            'asset_depts': [self.asset_depts_list["list"].item(i).text() for i in range(self.asset_depts_list["list"].count())],
            'templates': {},
            'template_files': self._template_files_from_ui(),
        }
        google_sheets = dict(existing_config.get('google_sheets') or {})
        for prefix, value in google_inputs.items():
            if value:
                google_sheets[f"{prefix}_url"] = value
                google_sheets[f"{prefix}_id"] = google_sheet_id(value)
            else:
                google_sheets.pop(f"{prefix}_url", None)
                google_sheets.pop(f"{prefix}_id", None)
        if google_sheets:
            config['google_sheets'] = google_sheets
        
        for i in range(self.selected_list.count()):
            sid = self.selected_list.item(i).text()
            config['enabled_softwares'].append(sid)
            if sid in self.software_configs:
                save_yml(os.path.join(proj_dir, f"software_{sid}.yml"), self.software_configs[sid])
        
        # Anchors/Templates の保存 (省略せず記述)
        res = [1920, 1080]
        for r in range(a_tab.rowCount()):
            k_item = a_tab.item(r, 0); v_item = a_tab.item(r, 1)
            k = k_item.text() if k_item else ""; v = v_item.text() if v_item else ""
            if "resolution X" in k: res[0] = int(v) if v.isdigit() else 1920
            elif "resolution Y" in k: res[1] = int(v) if v.isdigit() else 1080
            elif k in google_labels: continue
            elif k: config['anchors'][k] = int(v) if v.isdigit() else v
        config['anchors']['resolution'] = res

        t_tab = self.template_table["table"]
        for r in range(t_tab.rowCount()):
            k_item = t_tab.item(r, 0); v_item = t_tab.item(r, 1)
            k = k_item.text() if k_item else ""; v = v_item.text() if v_item else ""
            if k: config['templates'][k] = v

        save_yml(os.path.join(proj_dir, "templates_base.yml"), config)
        naming_path = os.path.join(proj_dir, "naming.yml")
        naming_data = load_yml(naming_path)
        naming_data["smart_playblast"] = {
            "filename": self.playblast_filename_input.text().strip()
            or "{project}*{episode}*{sequence}*{shot}*{dept}_{preview}_v{version}*t{take}*####.{ext}"
        }
        save_yml(naming_path, naming_data)
        self._save_preflight_config(proj_dir)
        self._save_context_configs(proj_dir)
        self._save_resolver_rules(proj_dir)
        self.config_saved.emit()
        QtWidgets.QMessageBox.information(self, "Saved", "Success")
        self.close()

    def load_project_config(self, project_name):
        proj_dir = os.path.join(PROJECTS_ROOT, project_name)
        data = load_yml(os.path.join(proj_dir, "templates_base.yml"))
        self.name_input.setText(project_name)
        root = data.get('anchors', {}).get('project_root', "")
        if root: self.path_input.setText(os.path.dirname(root).replace("\\", "/"))
        
        # リスト初期化
        self.global_list.clear(); self.selected_list.clear()
        master = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        for sid in sorted(master.keys()):
            self._add_item_with_icon(self.global_list, sid, master[sid].get('icon', ""), master[sid].get('path', ""))
            
        for sid in data.get('enabled_softwares', []):
            p = os.path.join(proj_dir, f"software_{sid}.yml")
            default_conf = load_yml(os.path.join(DEFAULT_DIR, f"software_{sid}.yml"))
            project_conf = load_yml(p) if os.path.exists(p) else {}
            conf = merge_dicts(default_conf, project_conf)
            self._add_item_with_icon(self.selected_list, sid, conf.get('icon', ""), conf.get('path', ""))
            self.software_configs[sid] = conf
            
        self._apply_data_to_ui(data)
        naming = merge_dicts(
            load_yml(os.path.join(DEFAULT_DIR, "naming.yml")),
            load_yml(os.path.join(proj_dir, "naming.yml")),
        )
        self.playblast_filename_input.setText(
            str((naming.get("smart_playblast") or {}).get("filename") or "")
        )
        self._load_preflight_editor(project_name)
        self._load_context_editor(project_name)
        self._load_resolver_editor(project_name)

    def init_ui_from_default(self):
        master = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        self.global_list.clear()
        self.selected_list.clear()
        for sid in sorted(master.keys()):
            self._add_item_with_icon(self.global_list, sid, master[sid].get('icon', ""), master[sid].get('path', ""))
        self._apply_data_to_ui(load_yml(os.path.join(DEFAULT_DIR, "templates_base.yml")))
        naming = load_yml(os.path.join(DEFAULT_DIR, "naming.yml"))
        self.playblast_filename_input.setText(
            str((naming.get("smart_playblast") or {}).get("filename") or "")
        )
        self._load_preflight_editor()
        self._load_context_editor()
        self._load_resolver_editor()

    def remove_from_selected(self):
        row = self.selected_list.currentRow()
        if row >= 0: self.selected_list.takeItem(row)

    def _save_tree_to_memory(self, soft_id=None):
        if not soft_id:
            curr = self.selected_list.currentItem()
            if not curr: return
            soft_id = curr.text()
        settings = {}
        env_vars = {}
        for row in range(self.software_settings_table.rowCount()):
            key_item = self.software_settings_table.item(row, 0)
            value_item = self.software_settings_table.item(row, 2)
            key = key_item.text().strip() if key_item else ""
            value = value_item.text() if value_item else ""
            if key and key not in {"env_vars", "paths"}:
                setting_type = key_item.data(QtCore.Qt.ItemDataRole.UserRole)
                if setting_type == "env_var":
                    env_vars[key] = value
                else:
                    settings[key] = self._parse_setting_value(value)

        paths = {}
        for i in range(self.env_tree.topLevelItemCount()):
            it = self.env_tree.topLevelItem(i)
            paths[it.text(0)] = [it.child(j).text(0) for j in range(it.childCount())]
        if soft_id not in self.software_configs: self.software_configs[soft_id] = {}
        self.software_configs[soft_id].clear()
        self.software_configs[soft_id].update(settings)
        self.software_configs[soft_id]['env_vars'] = env_vars
        self.software_configs[soft_id]['paths'] = paths

    def _populate_tree(self, conf):
        self.software_settings_table.setRowCount(0)
        for key, value in conf.items():
            if key in {"env_vars", "paths"}:
                continue
            self._append_software_setting_row(
                key, self._format_setting_value(value), "setting"
            )

        for key, value in conf.get('env_vars', {}).items():
            self._append_software_setting_row(key, value, "env_var")

        self.env_tree.clear()
        for k, p_list in conf.get('paths', {}).items():
            parent = QtWidgets.QTreeWidgetItem([str(k)])
            parent.setData(0, QtCore.Qt.ItemDataRole.UserRole, "path")
            parent.setFlags(parent.flags() | QtCore.Qt.ItemFlag.ItemIsEditable | QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            self.env_tree.addTopLevelItem(parent)
            if isinstance(p_list, list):
                for p in p_list:
                    child = QtWidgets.QTreeWidgetItem([str(p)])
                    child.setFlags(child.flags() | QtCore.Qt.ItemFlag.ItemIsEditable | QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
                    parent.addChild(child)
            parent.setExpanded(True)

    def _append_software_setting_row(self, key, value, setting_type):
        row = self.software_settings_table.rowCount()
        self.software_settings_table.insertRow(row)
        key_item = QtWidgets.QTableWidgetItem(str(key))
        key_item.setData(QtCore.Qt.ItemDataRole.UserRole, setting_type)
        key_item.setToolTip(
            "Environment variable" if setting_type == "env_var" else "Software setting"
        )
        self.software_settings_table.setItem(row, 0, key_item)
        type_item = QtWidgets.QTableWidgetItem(
            "Environment" if setting_type == "env_var" else "Software"
        )
        type_item.setData(QtCore.Qt.ItemDataRole.UserRole, setting_type)
        type_item.setFlags(type_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self.software_settings_table.setItem(row, 1, type_item)
        self.software_settings_table.setItem(
            row, 2, QtWidgets.QTableWidgetItem(str(value) if value is not None else "")
        )

    @staticmethod
    def _format_setting_value(value):
        if isinstance(value, (dict, list, tuple)):
            return yaml.safe_dump(
                value,
                default_flow_style=True,
                sort_keys=False,
                allow_unicode=True,
            ).strip()
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _parse_setting_value(value):
        text = value.strip()
        if not text:
            return ""
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            return value
        return value if parsed is None else parsed

    def add_software_setting(self):
        self._append_software_setting_row("new_setting", "", "setting")
        row = self.software_settings_table.rowCount() - 1
        self.software_settings_table.setCurrentCell(row, 0)
        self.software_settings_table.editItem(
            self.software_settings_table.item(row, 0)
        )

    def add_environment_setting(self):
        self._append_software_setting_row("NEW_ENV_VAR", "value", "env_var")
        row = self.software_settings_table.rowCount() - 1
        self.software_settings_table.setCurrentCell(row, 0)
        self.software_settings_table.editItem(
            self.software_settings_table.item(row, 0)
        )

    def remove_software_setting(self):
        row = self.software_settings_table.currentRow()
        if row >= 0:
            self.software_settings_table.removeRow(row)

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
        add.clicked.connect(lambda: (i := QtWidgets.QListWidgetItem("new"), i.setFlags(i.flags()|QtCore.Qt.ItemFlag.ItemIsEditable|QtCore.Qt.ItemFlag.ItemIsEnabled|QtCore.Qt.ItemFlag.ItemIsSelectable), lw.addItem(i)))
        rem.clicked.connect(lambda: lw.takeItem(lw.currentRow()))
        bl.addWidget(add); bl.addWidget(rem); l.addLayout(bl)
        return {"widget": w, "list": lw}

    def create_shot_departments_page(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)

        department_widget = QtWidgets.QWidget()
        department_layout = QtWidgets.QVBoxLayout(department_widget)
        department_layout.setContentsMargins(0, 0, 0, 0)
        department_layout.addWidget(QtWidgets.QLabel("Departments"))
        department_list = QtWidgets.QListWidget()
        department_list.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked)
        department_layout.addWidget(department_list, 1)
        department_buttons = QtWidgets.QHBoxLayout()
        add_department = QtWidgets.QPushButton("+")
        remove_department = QtWidgets.QPushButton("-")
        department_buttons.addWidget(add_department)
        department_buttons.addWidget(remove_department)
        department_layout.addLayout(department_buttons)

        task_widget = QtWidgets.QWidget()
        task_layout = QtWidgets.QVBoxLayout(task_widget)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.addWidget(QtWidgets.QLabel("Tasks for selected department"))
        task_list = QtWidgets.QListWidget()
        task_list.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked)
        task_layout.addWidget(task_list, 1)
        task_buttons = QtWidgets.QHBoxLayout()
        add_task = QtWidgets.QPushButton("+")
        remove_task = QtWidgets.QPushButton("-")
        task_buttons.addWidget(add_task)
        task_buttons.addWidget(remove_task)
        task_layout.addLayout(task_buttons)

        layout.addWidget(department_widget, 1)
        layout.addWidget(task_widget, 1)

        add_department.clicked.connect(
            lambda: self._add_editable_list_item(department_list, "new_department", ["main"])
        )
        remove_department.clicked.connect(
            lambda: department_list.takeItem(department_list.currentRow())
        )
        add_task.clicked.connect(lambda: self._add_editable_list_item(task_list, "new_task"))
        remove_task.clicked.connect(lambda: task_list.takeItem(task_list.currentRow()))
        department_list.currentItemChanged.connect(self._on_shot_department_editor_changed)
        return {"widget": widget, "list": department_list, "tasks": task_list}

    def _add_editable_list_item(self, list_widget, text, data=None):
        item = QtWidgets.QListWidgetItem(text)
        item.setFlags(
            item.flags()
            | QtCore.Qt.ItemFlag.ItemIsEditable
            | QtCore.Qt.ItemFlag.ItemIsEnabled
            | QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        if data is not None:
            item.setData(QtCore.Qt.ItemDataRole.UserRole, list(data))
        list_widget.addItem(item)
        list_widget.setCurrentItem(item)
        list_widget.editItem(item)
        return item

    def _on_shot_department_editor_changed(self, current, previous):
        task_list = self.shot_depts_list["tasks"]
        if previous is not None:
            previous.setData(
                QtCore.Qt.ItemDataRole.UserRole,
                [task_list.item(index).text().strip() for index in range(task_list.count()) if task_list.item(index).text().strip()],
            )
        task_list.clear()
        if current is None:
            return
        tasks = current.data(QtCore.Qt.ItemDataRole.UserRole) or ["main"]
        for task in tasks:
            self._add_editable_list_item(task_list, str(task))

    def _shot_tasks_from_ui(self):
        department_list = self.shot_depts_list["list"]
        current = department_list.currentItem()
        if current is not None:
            current.setData(
                QtCore.Qt.ItemDataRole.UserRole,
                [
                    self.shot_depts_list["tasks"].item(index).text().strip()
                    for index in range(self.shot_depts_list["tasks"].count())
                    if self.shot_depts_list["tasks"].item(index).text().strip()
                ],
            )
        result = {}
        for index in range(department_list.count()):
            item = department_list.item(index)
            department = item.text().strip()
            if not department:
                continue
            tasks = [str(task).strip() for task in (item.data(QtCore.Qt.ItemDataRole.UserRole) or ["main"]) if str(task).strip()]
            result[department] = tasks or ["main"]
        return result

    def browse_path(self):
        res = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Directory")
        if res: self.path_input.setText(res.replace("\\", "/"))

    def revert_config(self):
        if self.target_project:
            self.software_configs.clear()
            self.load_project_config(self.target_project)
        else:
            self.software_configs.clear()
            self.init_ui_from_default()

    def add_tree_var(self):
        i = QtWidgets.QTreeWidgetItem(["NEW_VAR"])
        i.setData(0, QtCore.Qt.ItemDataRole.UserRole, "env")
        i.setFlags(i.flags()|QtCore.Qt.ItemFlag.ItemIsEditable|QtCore.Qt.ItemFlag.ItemIsEnabled|QtCore.Qt.ItemFlag.ItemIsSelectable)
        self.env_tree.addTopLevelItem(i)

    def add_tree_path_variable(self):
        item = QtWidgets.QTreeWidgetItem(["NEW_PATH"])
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, "path")
        item.setFlags(
            item.flags()
            | QtCore.Qt.ItemFlag.ItemIsEditable
            | QtCore.Qt.ItemFlag.ItemIsEnabled
            | QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        self.env_tree.addTopLevelItem(item)
        self.env_tree.setCurrentItem(item)
        self.env_tree.editItem(item, 0)

    def add_tree_path(self):
        p = self.env_tree.currentItem()
        if p and not p.parent():
            p.setData(0, QtCore.Qt.ItemDataRole.UserRole, "path")
            c = QtWidgets.QTreeWidgetItem(["/path/to/folder"])
            c.setFlags(c.flags()|QtCore.Qt.ItemFlag.ItemIsEditable|QtCore.Qt.ItemFlag.ItemIsEnabled|QtCore.Qt.ItemFlag.ItemIsSelectable)
            p.addChild(c); p.setExpanded(True)

    def remove_tree_node(self):
        i = self.env_tree.currentItem()
        if i:
            if i.parent(): i.parent().removeChild(i)
            else: self.env_tree.takeTopLevelItem(self.env_tree.indexOfTopLevelItem(i))

    def _apply_data_to_ui(self, data):
        self.template_file_settings = copy.deepcopy(
            data.get("template_files") or {}
        )
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
        google_sheets = data.get('google_sheets') or {}
        for label, prefix in (
            (ASSET_LIST_URL_LABEL, "asset_list"),
            (SHOT_LIST_URL_LABEL, "shot_list"),
        ):
            row = tab.rowCount(); tab.insertRow(row)
            key_item = QtWidgets.QTableWidgetItem(label)
            key_item.setFlags(key_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            tab.setItem(row, 0, key_item)
            tab.setItem(
                row,
                1,
                QtWidgets.QTableWidgetItem(google_sheet_url(google_sheets, prefix)),
            )
        self.shot_depts_list["list"].clear()
        self.shot_depts_list["tasks"].clear()
        default_shot_tasks = (
            load_yml(os.path.join(DEFAULT_DIR, "templates_base.yml")).get("shot_tasks")
            or {}
        )
        shot_tasks = merge_dicts(default_shot_tasks, data.get("shot_tasks") or {})
        for department in data.get("shot_depts", []):
            self._add_editable_list_item(
                self.shot_depts_list["list"],
                str(department),
                shot_tasks.get(str(department)) or ["main"],
            )
        if self.shot_depts_list["list"].count():
            self.shot_depts_list["list"].setCurrentRow(0)
        self.asset_depts_list["list"].clear()
        for department in data.get("asset_depts", []):
            self._add_editable_list_item(self.asset_depts_list["list"], str(department))
        tab = self.template_table["table"]; tab.setRowCount(0)
        for k, v in data.get('templates', {}).items():
            r = tab.rowCount(); tab.insertRow(r)
            tab.setItem(r, 0, QtWidgets.QTableWidgetItem(k)); tab.setItem(r, 1, QtWidgets.QTableWidgetItem(v))
        self._refresh_template_files_table()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = ConfigCreatorApp()
    win.show()
    sys.exit(app.exec())
