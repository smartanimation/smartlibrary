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
SHOT_PATH_TEMPLATE_KEYS = {
    "shot_root", "shot_work_root", "shot_work", "shot_data_root",
    "shot_publish_root", "shot_output_root", "shot_render_root",
    "shot_build_root", "shot_build", "sequence_build_root", "sequence_build",
}
ASSET_PATH_TEMPLATE_KEYS = {
    "asset_root", "asset_work_root", "asset_work", "asset_data_root",
    "asset_publish_root", "asset_reference_root",
}
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


def registration_id(source_id, existing_ids):
    """Return a stable, unique id for another registration of a tool."""
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(source_id or "software").strip())
    base = base.strip("._-") or "software"
    existing = {str(value).lower() for value in (existing_ids or [])}
    if base.lower() not in existing:
        return base
    index = 2
    while "{}_{}".format(base, index).lower() in existing:
        index += 1
    return "{}_{}".format(base, index)


def source_software_id(registration_id_value, config=None):
    """Resolve an alias registration back to its master software definition."""
    return str((config or {}).get("source_software") or registration_id_value)

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
        self._current_asset_class_key = "default"
        self.context_active_versions = {}
        self.asset_subset_catalog = {}
        self.workspace_representation = "maya"
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
        self.anchors_table = self.create_anchors_page()
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
        self.review_tab = self.setup_review_tab()

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
        self.tabs.addTab(self.review_tab, "Review")
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

    def setup_review_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        note = QtWidgets.QLabel(
            "Review Profiles control generated pixels and Layer cache invalidation. "
            "Delivery Profiles control codec, naming and the final Shot destination."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        editors = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        review_group = QtWidgets.QGroupBox("Review Profiles")
        review_layout = QtWidgets.QVBoxLayout(review_group)
        self.review_profiles_edit = QtWidgets.QPlainTextEdit()
        self.review_profiles_edit.setPlaceholderText(
            "work_default:\n  stage: WORK\n  image_format: png"
        )
        review_layout.addWidget(self.review_profiles_edit)
        delivery_group = QtWidgets.QGroupBox("Delivery Profiles")
        delivery_layout = QtWidgets.QVBoxLayout(delivery_group)
        self.delivery_profiles_edit = QtWidgets.QPlainTextEdit()
        self.delivery_profiles_edit.setPlaceholderText(
            "internal:\n  review_profile: work_default\n  container: mov"
        )
        delivery_layout.addWidget(self.delivery_profiles_edit)
        editors.addWidget(review_group)
        editors.addWidget(delivery_group)
        layout.addWidget(editors, 1)
        policy = QtWidgets.QFormLayout()
        self.missing_precomp_policy_combo = QtWidgets.QComboBox()
        self.missing_precomp_policy_combo.addItems(
            ["allow_project_default", "block", "auto_create_candidate"]
        )
        self.default_precomp_edit = QtWidgets.QLineEdit()
        self.default_precomp_edit.setPlaceholderText(
            "{project_root}/templates/review/base_comp.aep"
        )
        self.review_success_days = QtWidgets.QSpinBox()
        self.review_success_days.setRange(0, 3650)
        self.review_failed_days = QtWidgets.QSpinBox()
        self.review_failed_days.setRange(0, 3650)
        self.review_logs_days = QtWidgets.QSpinBox()
        self.review_logs_days.setRange(0, 3650)
        policy.addRow("Missing PreComp:", self.missing_precomp_policy_combo)
        policy.addRow("Default PreComp:", self.default_precomp_edit)
        policy.addRow("Keep Successful Jobs (days):", self.review_success_days)
        policy.addRow("Keep Failed Jobs (days):", self.review_failed_days)
        policy.addRow("Keep Logs (days):", self.review_logs_days)
        layout.addLayout(policy)
        return page

    def _load_review_editor(self, project_name=None):
        default_data = load_yml(os.path.join(DEFAULT_DIR, "review.yml"))
        project_data = {}
        if project_name:
            project_data = load_yml(os.path.join(PROJECTS_ROOT, project_name, "review.yml"))
        data = merge_dicts(default_data, project_data)
        review_profiles = copy.deepcopy(data.get("review_profiles") or {})
        for profile in review_profiles.values():
            if isinstance(profile, dict) and profile.get("resolution_scale") is not None:
                profile.pop("resolution", None)
        self.review_profiles_edit.setPlainText(
            yaml.dump(review_profiles, sort_keys=False, allow_unicode=True)
        )
        self.delivery_profiles_edit.setPlainText(
            yaml.dump(data.get("delivery_profiles") or {}, sort_keys=False, allow_unicode=True)
        )
        policy = str(data.get("missing_precomp_policy") or "allow_project_default")
        index = self.missing_precomp_policy_combo.findText(policy)
        self.missing_precomp_policy_combo.setCurrentIndex(max(0, index))
        self.default_precomp_edit.setText(
            str(data.get("default_precomp") or "{project_root}/templates/review/base_comp.aep")
        )
        jobs = data.get("jobs") or {}
        self.review_success_days.setValue(int(jobs.get("retain_success_days", 3)))
        self.review_failed_days.setValue(int(jobs.get("retain_failed_days", 30)))
        self.review_logs_days.setValue(int(jobs.get("retain_logs_days", 90)))

    def _review_config_from_ui(self):
        review_profiles = yaml.safe_load(self.review_profiles_edit.toPlainText()) or {}
        delivery_profiles = yaml.safe_load(self.delivery_profiles_edit.toPlainText()) or {}
        if not isinstance(review_profiles, dict) or not isinstance(delivery_profiles, dict):
            raise ValueError("Review and Delivery Profiles must be YAML mappings.")
        for name, profile in review_profiles.items():
            if not isinstance(profile, dict):
                raise ValueError(f"Review Profile {name} must be a mapping.")
            stage = str(profile.get("stage") or "WORK").upper()
            if stage not in {"WORK", "REND"}:
                raise ValueError(f"Review Profile {name}: stage must be WORK or REND.")
            image_format = str(profile.get("image_format") or "png").lower()
            if image_format not in {"png", "exr", "jpg", "jpeg"}:
                raise ValueError(f"Review Profile {name}: unsupported image_format {image_format}.")
            if profile.get("resolution_scale") is not None:
                try:
                    resolution_scale = float(profile["resolution_scale"])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Review Profile {name}: resolution_scale must be a number."
                    ) from exc
                if resolution_scale <= 0:
                    raise ValueError(
                        f"Review Profile {name}: resolution_scale must be positive."
                    )
        for name, profile in delivery_profiles.items():
            if not isinstance(profile, dict):
                raise ValueError(f"Delivery Profile {name} must be a mapping.")
            review_profile = str(profile.get("review_profile") or "")
            if review_profile and review_profile not in review_profiles:
                raise ValueError(
                    f"Delivery Profile {name}: Review Profile {review_profile} was not found."
                )
        return {
            "schema_version": 1,
            "review_profiles": review_profiles,
            "delivery_profiles": delivery_profiles,
            "missing_precomp_policy": self.missing_precomp_policy_combo.currentText(),
            "default_precomp": self.default_precomp_edit.text().strip(),
            "jobs": {
                "retain_success_days": self.review_success_days.value(),
                "retain_failed_days": self.review_failed_days.value(),
                "retain_logs_days": self.review_logs_days.value(),
            },
        }

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
        structure_help = QtWidgets.QLabel(
            "Folder Structure convention: put shared/published folders in root/ and "
            "artist folders in work/. Flat templates copy to the workspace entity root."
        )
        structure_help.setWordWrap(True)
        layout.addWidget(structure_help)

        buttons = QtWidgets.QHBoxLayout()
        browse = QtWidgets.QPushButton("Browse Project Template")
        open_location = QtWidgets.QPushButton("Create / Open Location")
        clear = QtWidgets.QPushButton("Clear Project Template")
        refresh = QtWidgets.QPushButton("Refresh")
        browse.clicked.connect(self._browse_project_template)
        open_location.clicked.connect(self._open_template_location)
        clear.clicked.connect(self._clear_project_template)
        refresh.clicked.connect(self._refresh_template_files_table)
        buttons.addWidget(browse)
        buttons.addWidget(open_location)
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
                "folder_structure.shot",
                "Shot Folder Structure",
                "settings/templates/folder_structure/shot",
                "templates/folder_structure/shot",
            ),
            (
                "folder_structure.asset",
                "Asset Folder Structure",
                "settings/templates/folder_structure/asset",
                "templates/folder_structure/asset",
            ),
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
            expects_directory = key.startswith("folder_structure.")
            project_exists = (
                os.path.isdir(resolved_project)
                if expects_directory else os.path.isfile(resolved_project)
            )
            fallback_exists = (
                os.path.isdir(fallback)
                if expects_directory else os.path.isfile(fallback)
            )
            if resolved_project and project_exists:
                status, color = "PROJECT", "#80bd72"
            elif fallback_exists:
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
        key = str(
            self.template_files_table.item(row, 0).data(
                QtCore.Qt.ItemDataRole.UserRole
            )
        )
        if key.startswith("folder_structure."):
            path = QtWidgets.QFileDialog.getExistingDirectory(
                self,
                "Select Physical Folder Structure",
                self._template_project_root(),
            )
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "Select Template File",
                self._template_project_root(),
                "Template Files (*.*)",
            )
        if not path:
            return
        self._set_template_setting(key, path.replace("\\", "/"))
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

    def _open_template_location(self):
        row = self.template_files_table.currentRow()
        if row < 0:
            return
        key_item = self.template_files_table.item(row, 0)
        path_item = self.template_files_table.item(row, 1)
        key = str(key_item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        configured_path = path_item.text().strip() if path_item else ""
        path = self._resolved_template_path(
            configured_path, self._template_project_root()
        )
        if not path:
            return
        location = path if key.startswith("folder_structure.") else os.path.dirname(path)
        try:
            os.makedirs(location, exist_ok=True)
            os.startfile(os.path.normpath(location))
        except OSError as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Could Not Open Template Location",
                str(exc),
            )

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
        layout = QtWidgets.QVBoxLayout(page)
        tokens = QtWidgets.QLabel(
            "Tokens: {project_name} {episode} {sequence} {shot} {department} "
            "{preview} {version} {take} {frame} {ext}"
        )
        tokens.setWordWrap(True)
        layout.addWidget(tokens)
        layout.addWidget(QtWidgets.QLabel("Logical Outputs (used by all tools)"))
        self.output_naming_table = QtWidgets.QTableWidget(0, 3)
        self.output_naming_table.setHorizontalHeaderLabels(
            ["Output Key", "Directory", "Filename"]
        )
        self.output_naming_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.Stretch
        )
        layout.addWidget(self.output_naming_table, 1)
        buttons = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("+")
        remove = QtWidgets.QPushButton("-")
        add.clicked.connect(lambda: self.output_naming_table.insertRow(self.output_naming_table.rowCount()))
        remove.clicked.connect(lambda: self.output_naming_table.removeRow(self.output_naming_table.currentRow()))
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        layout.addLayout(buttons)
        return page

    def _load_output_naming(self, outputs):
        self.output_naming_table.setRowCount(0)
        for key, definition in sorted((outputs or {}).items()):
            row = self.output_naming_table.rowCount()
            self.output_naming_table.insertRow(row)
            values = (key, (definition or {}).get("directory", ""), (definition or {}).get("filename", ""))
            for column, value in enumerate(values):
                self.output_naming_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))

    def _output_naming_from_ui(self):
        outputs = {}
        for row in range(self.output_naming_table.rowCount()):
            values = [
                self.output_naming_table.item(row, column).text().strip()
                if self.output_naming_table.item(row, column) else ""
                for column in range(3)
            ]
            if values[0]:
                outputs[values[0]] = {"directory": values[1], "filename": values[2]}
        return outputs

    def _validate_output_naming(self):
        errors = []
        allowed_tokens = {
            "project_root", "project_name", "project", "workspace_root",
            "shot_root", "shot_work_root", "shot_work", "shot_build_root", "shot_build",
            "sequence_build_root", "sequence_build", "asset_root", "asset_work_root", "asset_work",
            "episode", "sequence", "seq", "shot", "category", "group",
            "asset", "asset_name", "variant", "department", "dept",
            "task", "dcc", "tool", "option", "preview", "layer", "cam",
            "version", "take", "frame", "ext",
        }
        for key, definition in self._output_naming_from_ui().items():
            directory = definition.get("directory", "")
            filename = definition.get("filename", "")
            if not directory:
                errors.append(f"Output '{key}': directory is required")
            if not filename:
                errors.append(f"Output '{key}': filename is required")
            if re.search(r'[<>:"/\\|?*]', filename):
                errors.append(f"Output '{key}': filename contains a Windows-forbidden character")
            unknown = (
                set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", directory + filename))
                - allowed_tokens
            )
            if unknown:
                errors.append(f"Output '{key}': unknown tokens: {', '.join(sorted(unknown))}")
        return errors

    def setup_context_tab(self):
        page = QtWidgets.QWidget()
        page_layout = QtWidgets.QVBoxLayout(page)
        page_layout.setContentsMargins(8, 8, 8, 8)
        policy_group = QtWidgets.QGroupBox("Shot Build Policy")
        policy_layout = QtWidgets.QFormLayout(policy_group)
        self.workspace_representation_combo = QtWidgets.QComboBox()
        self.workspace_representation_combo.addItem("Maya Reference", "maya")
        self.workspace_representation_combo.addItem("USD Payload", "usd")
        self.workspace_representation_combo.setToolTip(
            "Default used by Review Build Manager when Representation is Project Default."
        )
        policy_layout.addRow("Default Representation:", self.workspace_representation_combo)
        page_layout.addWidget(policy_group)

        content = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(content)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        page_layout.addWidget(content, 1)

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

        self.context_editor_tabs = QtWidgets.QTabWidget()
        self.context_editor_tabs.setDocumentMode(True)

        stage_page = QtWidgets.QWidget()
        stage_layout = QtWidgets.QVBoxLayout(stage_page)
        stage_layout.setContentsMargins(8, 8, 8, 8)
        stage_help = QtWidgets.QLabel(
            "Stage Profiles select an Asset Context by asset class. Purpose and load flags "
            "control the Maya USD viewport policy."
        )
        stage_help.setWordWrap(True)
        stage_layout.addWidget(stage_help)
        self.context_stage_table = QtWidgets.QTableWidget(0, 7)
        self.context_stage_table.setHorizontalHeaderLabels(
            ["Stage", "Character", "Environment", "Prop", "USD Purpose", "Payloads", "Crowds"]
        )
        self._configure_context_table(self.context_stage_table)
        stage_layout.addWidget(self.context_stage_table)
        self.context_editor_tabs.addTab(stage_page, "Stage Profiles")

        profile_page = QtWidgets.QWidget()
        profile_layout = QtWidgets.QVBoxLayout(profile_page)
        profile_layout.setContentsMargins(8, 8, 8, 8)
        class_actions = QtWidgets.QHBoxLayout()
        class_actions.addWidget(QtWidgets.QLabel("Asset Type"))
        self.context_asset_class_combo = QtWidgets.QComboBox()
        self.context_asset_class_combo.setMinimumWidth(180)
        class_actions.addWidget(self.context_asset_class_combo)
        self.context_add_asset_class_btn = QtWidgets.QPushButton("+ Add Class")
        self.context_delete_asset_class_btn = QtWidgets.QPushButton("Delete Class")
        class_actions.addWidget(self.context_add_asset_class_btn)
        class_actions.addWidget(self.context_delete_asset_class_btn)
        class_actions.addStretch()
        profile_layout.addLayout(class_actions)
        match_layout = QtWidgets.QHBoxLayout()
        match_layout.addWidget(QtWidgets.QLabel("Asset Type Match"))
        self.context_asset_class_match = QtWidgets.QLineEdit()
        self.context_asset_class_match.setPlaceholderText("CH, character, characters")
        match_layout.addWidget(self.context_asset_class_match, 1)
        profile_layout.addLayout(match_layout)
        profile_actions = QtWidgets.QHBoxLayout()
        profile_actions.addStretch()
        self.context_add_profile_btn = QtWidgets.QPushButton("+ Add Profile")
        self.context_delete_profile_btn = QtWidgets.QPushButton("Delete")
        profile_actions.addWidget(self.context_add_profile_btn)
        profile_actions.addWidget(self.context_delete_profile_btn)
        profile_layout.addLayout(profile_actions)
        self.context_profile_table = QtWidgets.QTableWidget(0, 6)
        self.context_profile_table.setHorizontalHeaderLabels(
            ["Profile", "Model", "Assembly", "Look", "Rig", "Groom"]
        )
        self._configure_context_table(self.context_profile_table)
        profile_layout.addWidget(self.context_profile_table)
        self.context_editor_tabs.addTab(profile_page, "Quality Profiles")

        representation_page = QtWidgets.QWidget()
        representation_layout = QtWidgets.QVBoxLayout(representation_page)
        representation_layout.setContentsMargins(8, 8, 8, 8)
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
        self.context_editor_tabs.addTab(representation_page, "Representations")

        output_page = QtWidgets.QWidget()
        output_layout = QtWidgets.QVBoxLayout(output_page)
        output_layout.setContentsMargins(8, 8, 8, 8)
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
        self.context_editor_tabs.addTab(output_page, "Output Formats")
        main_layout.addWidget(self.context_editor_tabs, 1)

        root.addWidget(left)
        root.addWidget(main, 1)

        self.context_list.currentItemChanged.connect(self._on_context_changed)
        self.context_version_list.currentItemChanged.connect(self._on_context_version_changed)
        self.context_new_version_btn.clicked.connect(self._add_context_version)
        self.context_set_active_btn.clicked.connect(self._set_active_context_version)
        self.context_add_profile_btn.clicked.connect(self._add_context_profile)
        self.context_delete_profile_btn.clicked.connect(self._delete_context_profile)
        self.context_asset_class_combo.currentIndexChanged.connect(self._on_asset_class_changed)
        self.context_add_asset_class_btn.clicked.connect(self._add_asset_class)
        self.context_delete_asset_class_btn.clicked.connect(self._delete_asset_class)
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
                "Stage / Asset Context",
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
        values = values or ["shot", "default", "WORK", "approved", "ma, mb, usd", "FAST", "latest"]
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
                    str(rule.get("context", "WORK")).upper(),
                    rule.get("version", "approved"),
                    ", ".join(str(value) for value in formats),
                    ", ".join(str(value).upper() for value in fallback_contexts),
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

        review_build_layout = QtWidgets.QFormLayout()
        self.review_build_maya_combo = QtWidgets.QComboBox()
        self.review_build_maya_combo.setToolTip(
            "Maya registration used by Review Build Manager and its mayapy worker."
        )
        review_build_layout.addRow("Review Build Maya:", self.review_build_maya_combo)
        self.review_build_ae_combo = QtWidgets.QComboBox()
        self.review_build_ae_combo.setToolTip(
            "After Effects registration used for Review relink and render workers."
        )
        review_build_layout.addRow("Review Build After Effects:", self.review_build_ae_combo)
        left_layout.addLayout(review_build_layout)
        
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

        self.plugin_profile_table = QtWidgets.QTableWidget(0, 3)
        self.plugin_profile_table.setHorizontalHeaderLabels(
            ["Build Profile", "Required Plugins", "Optional Plugins"]
        )
        self.plugin_profile_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.Stretch
        )
        self.plugin_profile_table.verticalHeader().setVisible(False)
        plugin_note = QtWidgets.QLabel(
            "Comma separated plug-in names. Profiles: core, work_stage, rend_stage, update."
        )
        plugin_note.setWordWrap(True)
        
        right_layout.addWidget(QtWidgets.QLabel("Software Settings:"))
        right_layout.addWidget(self.software_settings_table, 1)
        right_layout.addLayout(settings_btns)
        right_layout.addWidget(QtWidgets.QLabel("Path Settings:"))
        right_layout.addWidget(self.env_tree)
        right_layout.addLayout(tree_btns)
        right_layout.addWidget(QtWidgets.QLabel("Build Plugin Profiles:"))
        right_layout.addWidget(self.plugin_profile_table)
        right_layout.addWidget(plugin_note)
        
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
            self.env_tree.clear()
            self.plugin_profile_table.setRowCount(0)
            return
            
        soft_id = current.text()
        if soft_id not in self.software_configs:
            proj_dir = os.path.join(PROJECTS_ROOT, self.name_input.text().strip())
            spec_path = os.path.join(proj_dir, f"software_{soft_id}.yml")
            project_config = load_yml(spec_path) if os.path.exists(spec_path) else {}
            source_id = source_software_id(soft_id, project_config)
            def_path = os.path.join(DEFAULT_DIR, f"software_{source_id}.yml")
            default_config = load_yml(def_path)
            if project_config:
                self.software_configs[soft_id] = merge_dicts(
                    default_config, project_config
                )
            else:
                self.software_configs[soft_id] = default_config
        
        self._populate_tree(self.software_configs[soft_id])

    def add_to_selected(self):
        curr = self.global_list.currentItem()
        if not curr: return
        source_id = curr.text()
        existing_ids = [
            self.selected_list.item(i).text()
            for i in range(self.selected_list.count())
        ]
        sid = registration_id(source_id, existing_ids)
            
        master_data = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        master_info = master_data.get(source_id, {})
        
        # アイコンまたはパスからアイコン付きアイテムを作成
        self._add_item_with_icon(self.selected_list, sid, master_info.get('icon', ""), master_info.get('path', ""))
        
        default_path = os.path.join(DEFAULT_DIR, f"software_{source_id}.yml")
        base_config = copy.deepcopy(load_yml(default_path))
        base_config['path'] = master_info.get('path', "")
        base_config['icon'] = master_info.get('icon', "")
        base_config['source_software'] = source_id
        if sid != source_id:
            base_config['name'] = f"{master_info.get('name', source_id)} ({sid})"
        
        self.software_configs[sid] = base_config
        self.selected_list.setCurrentRow(self.selected_list.count() - 1)
        self._populate_tree(self.software_configs[sid])
        self._refresh_review_build_maya_combo()
        self._refresh_review_build_ae_combo()

    def add_custom_software(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Exe/Bat", "", "Executables (*.exe *.bat *.cmd)")
        if p:
            source_id = os.path.splitext(os.path.basename(p))[0]
            existing_ids = [
                self.selected_list.item(i).text()
                for i in range(self.selected_list.count())
            ]
            sid = registration_id(source_id, existing_ids)

            # カスタムツールはパスからアイコンを抽出
            self._add_item_with_icon(self.selected_list, sid, "", p)
            
            self.software_configs[sid] = {
                'path': p.replace("\\", "/"),
                'icon': "",
                'source_software': source_id,
                'name': sid,
                'env_vars': {},
                'paths': {}
            }
            self.selected_list.setCurrentRow(self.selected_list.count()-1)
            self._populate_tree(self.software_configs[sid])
            self._refresh_review_build_maya_combo()
            self._refresh_review_build_ae_combo()

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
        workspace_policy = asset_config.get("workspace_load_policy") or {}
        self.workspace_representation = str(
            workspace_policy.get("representation") or "maya"
        ).strip().lower()
        representation_index = self.workspace_representation_combo.findData(
            self.workspace_representation
        )
        self.workspace_representation_combo.setCurrentIndex(
            representation_index if representation_index >= 0 else 0
        )
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
        representations = copy.deepcopy(result.get("representations") or {})

        def normalize_profiles(raw_profiles):
            normalized = {}
            for profile_name, profile in (raw_profiles or {}).items():
                if not isinstance(profile, dict):
                    continue
                normalized[str(profile_name)] = {}
                for publish_type, value in profile.items():
                    values = list(value.values()) if isinstance(value, dict) else [value]
                    values = [str(item) for item in values if item not in (None, "", "none")]
                    selected = (
                        str(value.get("default") or next(iter(value.values()), "none"))
                        if isinstance(value, dict)
                        else str(value or "none")
                    )
                    normalized[str(profile_name)][str(publish_type)] = selected
                    existing = representations.setdefault(str(publish_type), [])
                    if isinstance(existing, dict):
                        existing = list(existing.keys())
                        representations[str(publish_type)] = existing
                    for item in values:
                        if item not in existing:
                            existing.append(item)
            return normalized

        result["quality_profiles"] = normalize_profiles(profiles)
        recipes = result.get("asset_context_recipes") or {}
        normalized_recipes = {}
        for asset_class, recipe in recipes.items():
            if not isinstance(recipe, dict):
                continue
            normalized_recipe = copy.deepcopy(recipe)
            normalized_recipe["profiles"] = normalize_profiles(recipe.get("profiles"))
            normalized_recipes[str(asset_class)] = normalized_recipe
        if normalized_recipes:
            result["asset_context_recipes"] = normalized_recipes
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
        self.context_asset_class_combo.clear()
        self.context_asset_class_match.clear()
        self.context_profile_table.setRowCount(0)
        self.context_stage_table.setRowCount(0)
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
        recipes = data.get("asset_context_recipes") or {}
        if self._current_context_key == "asset" and recipes:
            labels = {
                "character": "Character",
                "environment": "Background",
                "prop": "Prop",
            }
            ordered = [
                value for value in ("character", "environment", "prop")
                if value in recipes
            ]
            ordered.extend(sorted(value for value in recipes if value not in ordered))
            for asset_class in ordered:
                self.context_asset_class_combo.addItem(
                    labels.get(asset_class, asset_class.replace("_", " ").title()),
                    asset_class,
                )
            self._current_asset_class_key = str(
                self.context_asset_class_combo.itemData(0) or "character"
            )
        else:
            self.context_asset_class_combo.addItem("Default / Legacy", "default")
            for asset_class in sorted(recipes):
                self.context_asset_class_combo.addItem(asset_class, asset_class)
            self._current_asset_class_key = "default"
        self._populate_stage_profiles(data)
        representations = self.asset_subset_catalog
        for publish_type in sorted(representations):
            for name in representations[publish_type]:
                row = self.context_representation_table.rowCount()
                self.context_representation_table.insertRow(row)
                self.context_representation_table.setItem(row, 0, QtWidgets.QTableWidgetItem(publish_type))
                self.context_representation_table.setItem(row, 1, QtWidgets.QTableWidgetItem(name))
        self._populate_asset_class_profiles(data, representations)
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
        for column, publish_type in enumerate(("model", "assembly", "look", "rig", "groom"), 1):
            combo = QtWidgets.QComboBox()
            combo.addItem("none")
            combo.addItems(list(representations.get(publish_type, [])))
            value = str(profile.get(publish_type, "none"))
            if combo.findText(value) < 0 and value:
                combo.addItem(value)
            combo.setCurrentText(value or "none")
            self.context_profile_table.setCellWidget(row, column, combo)

    def _populate_stage_profiles(self, data):
        self.context_stage_table.setRowCount(0)
        defaults = {
            "FAST": {"character": "LO", "environment": "PROXY", "prop": "LO", "purpose": "proxy", "load_payloads": False, "load_crowds": False},
            "WORK": {"character": "ANIM", "environment": "PROXY", "prop": "LO", "purpose": "proxy", "load_payloads": True, "load_crowds": True},
            "REND": {"character": "REND", "environment": "REND", "prop": "REND", "purpose": "render", "load_payloads": True, "load_crowds": True},
        }
        policies = data.get("stage_profiles") or defaults
        for stage in ("FAST", "WORK", "REND"):
            policy = dict(defaults[stage])
            policy.update(policies.get(stage) or policies.get(stage.lower()) or {})
            row = self.context_stage_table.rowCount()
            self.context_stage_table.insertRow(row)
            self.context_stage_table.setItem(row, 0, QtWidgets.QTableWidgetItem(stage))
            for column, key in enumerate(("character", "environment", "prop"), 1):
                self.context_stage_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(policy[key]).upper()))
            purpose = QtWidgets.QComboBox()
            purpose.addItems(["proxy", "render", "bbox"])
            purpose.setCurrentText(str(policy.get("purpose") or "proxy").lower())
            self.context_stage_table.setCellWidget(row, 4, purpose)
            for column, key in ((5, "load_payloads"), (6, "load_crowds")):
                item = QtWidgets.QTableWidgetItem()
                item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    QtCore.Qt.CheckState.Checked if bool(policy.get(key, True))
                    else QtCore.Qt.CheckState.Unchecked
                )
                self.context_stage_table.setItem(row, column, item)

    def _stage_profiles_from_table(self):
        result = {}
        for row in range(self.context_stage_table.rowCount()):
            stage_item = self.context_stage_table.item(row, 0)
            stage = stage_item.text().strip().upper() if stage_item else ""
            if not stage:
                continue
            values = []
            for column in (1, 2, 3):
                item = self.context_stage_table.item(row, column)
                values.append(item.text().strip().upper() if item else "")
            purpose = self.context_stage_table.cellWidget(row, 4)
            result[stage] = {
                "character": values[0],
                "environment": values[1],
                "prop": values[2],
                "purpose": purpose.currentText().strip().lower() if purpose else "proxy",
                "load_payloads": self.context_stage_table.item(row, 5).checkState() == QtCore.Qt.CheckState.Checked,
                "load_crowds": self.context_stage_table.item(row, 6).checkState() == QtCore.Qt.CheckState.Checked,
            }
        return result

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
            for column, publish_type in enumerate(("model", "assembly", "look", "rig", "groom"), 1):
                combo = self.context_profile_table.cellWidget(row, column)
                profile[publish_type] = combo.currentText().strip() if combo else "none"
            profiles[name] = profile
        self.asset_subset_catalog = representations
        data["stage_profiles"] = self._stage_profiles_from_table()
        data.pop("representations", None)
        if self._current_asset_class_key == "default":
            data["quality_profiles"] = profiles
        else:
            recipe = data.setdefault("asset_context_recipes", {}).setdefault(
                self._current_asset_class_key, {}
            )
            recipe.setdefault("inherit_common_profiles", False)
            recipe["profiles"] = profiles
            matches = [
                value.strip()
                for value in self.context_asset_class_match.text().split(",")
                if value.strip()
            ]
            recipe["match"] = {"asset_type": matches}

    def _populate_asset_class_profiles(self, data=None, representations=None):
        data = data or self._current_context_data() or {}
        representations = representations or self._representations_from_table()
        self.context_profile_table.setRowCount(0)
        if self._current_asset_class_key == "default":
            profiles = data.get("quality_profiles") or {}
            self.context_asset_class_match.clear()
            self.context_asset_class_match.setEnabled(False)
            self.context_delete_asset_class_btn.setEnabled(False)
        else:
            recipe = (data.get("asset_context_recipes") or {}).get(
                self._current_asset_class_key, {}
            )
            profiles = recipe.get("profiles") or {}
            matches = (recipe.get("match") or {}).get("asset_type") or []
            if isinstance(matches, str):
                matches = [matches]
            self.context_asset_class_match.setText(", ".join(str(value) for value in matches))
            self.context_asset_class_match.setEnabled(True)
            self.context_delete_asset_class_btn.setEnabled(True)
        for profile_name, profile in profiles.items():
            self._append_profile_row(profile_name, profile, representations)

    def _on_asset_class_changed(self, index):
        if self._context_loading or index < 0:
            return
        self._store_context_editor()
        self._current_asset_class_key = str(
            self.context_asset_class_combo.itemData(index) or "default"
        )
        self._context_loading = True
        self._populate_asset_class_profiles()
        self._context_loading = False

    def _add_asset_class(self):
        data = self._current_context_data()
        if not data:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Add Asset Class", "Class name:")
        name = name.strip().lower()
        if not ok or not name:
            return
        recipes = data.setdefault("asset_context_recipes", {})
        if name in recipes:
            QtWidgets.QMessageBox.warning(self, "Duplicate Asset Class", f"Asset class already exists: {name}")
            return
        self._store_context_editor()
        recipes[name] = {"match": {"asset_type": [name]}, "profiles": {}}
        self._context_loading = True
        self.context_asset_class_combo.addItem(name, name)
        index = self.context_asset_class_combo.count() - 1
        self.context_asset_class_combo.setCurrentIndex(index)
        self._current_asset_class_key = name
        self._populate_asset_class_profiles(data)
        self._context_loading = False

    def _delete_asset_class(self):
        if self._current_asset_class_key == "default":
            return
        data = self._current_context_data()
        if not data:
            return
        (data.get("asset_context_recipes") or {}).pop(self._current_asset_class_key, None)
        self._context_loading = True
        self.context_asset_class_combo.removeItem(self.context_asset_class_combo.currentIndex())
        self.context_asset_class_combo.setCurrentIndex(0)
        self._current_asset_class_key = "default"
        self._populate_asset_class_profiles(data)
        self._context_loading = False

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
            for column, publish_type in enumerate(("model", "assembly", "look", "rig", "groom"), 1):
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
                recipe_profiles = [
                    (asset_class, recipe.get("profiles") or {})
                    for asset_class, recipe in (data.get("asset_context_recipes") or {}).items()
                    if isinstance(recipe, dict)
                ]
                representations = self.asset_subset_catalog
                if not profiles and not recipe_profiles:
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
                for asset_class, class_profiles in recipe_profiles:
                    for profile_name, profile in class_profiles.items():
                        if not profile_name.strip():
                            errors.append(f"{context_name}/{version}/{asset_class}: Profile name is required")
                        for publish_type, value in profile.items():
                            if value not in ("", "none") and value not in representations.get(publish_type, []):
                                errors.append(
                                    f"{context_name}/{version}/{asset_class}/{profile_name}: "
                                    f"{publish_type}/{value} is not registered"
                                )
                if str(context_name).lower() == "asset":
                    stage_profiles = data.get("stage_profiles") or {}
                    for required_stage in ("FAST", "WORK", "REND"):
                        if required_stage not in {str(value).upper() for value in stage_profiles}:
                            errors.append(f"{context_name}/{version}: Stage Profile {required_stage} is required")
                    recipes = data.get("asset_context_recipes") or {}
                    for stage_name, policy in stage_profiles.items():
                        if not isinstance(policy, dict):
                            errors.append(f"{context_name}/{version}/{stage_name}: policy must be a mapping")
                            continue
                        for asset_class in ("character", "environment", "prop"):
                            selected = str(policy.get(asset_class) or "").upper()
                            available = {
                                str(value).upper()
                                for value in ((recipes.get(asset_class) or {}).get("profiles") or {})
                            }
                            if selected and available and selected not in available:
                                errors.append(
                                    f"{context_name}/{version}/{stage_name}: "
                                    f"{asset_class} context {selected} is not registered"
                                )
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
        workspace_policy = dict(asset_config.get("workspace_load_policy") or {})
        workspace_policy["representation"] = str(
            self.workspace_representation_combo.currentData() or "maya"
        )
        asset_config["workspace_load_policy"] = workspace_policy
        save_yml(asset_config_path, asset_config)

    def _load_sequence_builder_editor(self, project_name=None):
        default_data = load_yml(os.path.join(DEFAULT_DIR, "sequence_builder.yml"))
        project_data = (
            load_yml(os.path.join(PROJECTS_ROOT, project_name, "sequence_builder.yml"))
            if project_name else {}
        )
        data = merge_dicts(default_data, project_data)
        recipes = data.get("recipes") or {}
        combo = self.shot_depts_list["sequence_recipe"]
        combo.clear()
        combo.addItems([str(name) for name in recipes])
        selected = str(data.get("default_recipe") or "Standard Sequence")
        index = combo.findText(selected)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _save_sequence_builder_config(self, project_dir):
        path = os.path.join(project_dir, "sequence_builder.yml")
        data = load_yml(path)
        selected = self.shot_depts_list["sequence_recipe"].currentText().strip()
        data["default_recipe"] = selected or "Standard Sequence"
        save_yml(path, data)

    def save_config(self):
        self._save_tree_to_memory()
        name = self.name_input.text().strip()
        base = self.path_input.text().strip()
        if not name or not base: return

        try:
            review_config = self._review_config_from_ui()
        except (ValueError, yaml.YAMLError) as exc:
            QtWidgets.QMessageBox.warning(
                self, "Review Configuration Failed", str(exc)
            )
            return
        config_errors = (
            self._validate_context_configs()
            + self._validate_resolver_rules()
            + self._validate_output_naming()
        )
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
            'shot_dept_partitions': self._shot_dept_partitions_from_ui(),
            'asset_depts': [self.asset_depts_list["list"].item(i).text() for i in range(self.asset_depts_list["list"].count())],
            'templates': {},
            'template_files': self._template_files_from_ui(),
        }
        review_build_maya = str(
            self.review_build_maya_combo.currentData() or ""
        ).strip()
        review_build_ae = str(
            self.review_build_ae_combo.currentData() or ""
        ).strip()
        if review_build_maya or review_build_ae:
            config['review_build'] = {}
            if review_build_maya:
                config['review_build']['maya_software'] = review_build_maya
            if review_build_ae:
                config['review_build']['after_effects_software'] = review_build_ae
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
        shot_templates = {}
        asset_templates = {}
        for r in range(t_tab.rowCount()):
            k_item = t_tab.item(r, 0); v_item = t_tab.item(r, 1)
            k = k_item.text() if k_item else ""; v = v_item.text() if v_item else ""
            if not k:
                continue
            if k in SHOT_PATH_TEMPLATE_KEYS:
                shot_templates[k] = v
            elif k in ASSET_PATH_TEMPLATE_KEYS:
                asset_templates[k] = v
            else:
                config['templates'][k] = v

        save_yml(os.path.join(proj_dir, "templates_base.yml"), config)
        for filename, templates in (
            ("templates_shots.yml", shot_templates),
            ("templates_assets.yml", asset_templates),
        ):
            domain_path = os.path.join(proj_dir, filename)
            domain_data = load_yml(domain_path)
            merged_templates = dict(domain_data.get("templates") or {})
            merged_templates.update(templates)
            domain_data["templates"] = merged_templates
            save_yml(domain_path, domain_data)
        naming_path = os.path.join(proj_dir, "naming.yml")
        naming_data = load_yml(naming_path)
        naming_data["outputs"] = self._output_naming_from_ui()
        save_yml(naming_path, naming_data)
        self._save_preflight_config(proj_dir)
        self._save_context_configs(proj_dir)
        self._save_resolver_rules(proj_dir)
        self._save_sequence_builder_config(proj_dir)
        save_yml(os.path.join(proj_dir, "review.yml"), review_config)
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
            project_conf = load_yml(p) if os.path.exists(p) else {}
            source_id = source_software_id(sid, project_conf)
            default_conf = load_yml(os.path.join(DEFAULT_DIR, f"software_{source_id}.yml"))
            conf = merge_dicts(default_conf, project_conf)
            self._add_item_with_icon(self.selected_list, sid, conf.get('icon', ""), conf.get('path', ""))
            self.software_configs[sid] = conf
            
        self._apply_data_to_ui(data)
        self._append_domain_path_templates(proj_dir)
        naming = merge_dicts(
            load_yml(os.path.join(DEFAULT_DIR, "naming.yml")),
            load_yml(os.path.join(proj_dir, "naming.yml")),
        )
        self._load_output_naming(naming.get("outputs") or {})
        self._load_preflight_editor(project_name)
        self._load_context_editor(project_name)
        self._load_resolver_editor(project_name)
        self._load_review_editor(project_name)
        self._load_sequence_builder_editor(project_name)

    def init_ui_from_default(self):
        master = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        self.global_list.clear()
        self.selected_list.clear()
        for sid in sorted(master.keys()):
            self._add_item_with_icon(self.global_list, sid, master[sid].get('icon', ""), master[sid].get('path', ""))
        self._apply_data_to_ui(load_yml(os.path.join(DEFAULT_DIR, "templates_base.yml")))
        self._append_domain_path_templates()
        naming = load_yml(os.path.join(DEFAULT_DIR, "naming.yml"))
        self._load_output_naming(naming.get("outputs") or {})
        self._load_preflight_editor()
        self._load_context_editor()
        self._load_resolver_editor()
        self._load_review_editor()
        self._load_sequence_builder_editor()

    def remove_from_selected(self):
        row = self.selected_list.currentRow()
        if row >= 0:
            self.selected_list.takeItem(row)
            self._refresh_review_build_maya_combo()
            self._refresh_review_build_ae_combo()

    def _refresh_review_build_maya_combo(self, selected_id=None):
        """List enabled Maya registrations while preserving the current choice."""

        if selected_id is None:
            selected_id = self.review_build_maya_combo.currentData()
        self.review_build_maya_combo.blockSignals(True)
        self.review_build_maya_combo.clear()
        for index in range(self.selected_list.count()):
            sid = self.selected_list.item(index).text()
            config = self.software_configs.get(sid) or {}
            source_id = source_software_id(sid, config).lower()
            if source_id == "maya" or source_id.startswith("maya"):
                self.review_build_maya_combo.addItem(sid, sid)
        selected_index = self.review_build_maya_combo.findData(str(selected_id or ""))
        if selected_index >= 0:
            self.review_build_maya_combo.setCurrentIndex(selected_index)
        self.review_build_maya_combo.setEnabled(
            self.review_build_maya_combo.count() > 0
        )
        self.review_build_maya_combo.blockSignals(False)

    def _refresh_review_build_ae_combo(self, selected_id=None):
        """List enabled After Effects registrations for review rendering."""

        if selected_id is None:
            selected_id = self.review_build_ae_combo.currentData()
        self.review_build_ae_combo.blockSignals(True)
        self.review_build_ae_combo.clear()
        for index in range(self.selected_list.count()):
            sid = self.selected_list.item(index).text()
            config = self.software_configs.get(sid) or {}
            source_id = source_software_id(sid, config).lower()
            if source_id.startswith("aftereffects") or source_id.startswith("after_effects"):
                self.review_build_ae_combo.addItem(sid, sid)
        selected_index = self.review_build_ae_combo.findData(str(selected_id or ""))
        if selected_index >= 0:
            self.review_build_ae_combo.setCurrentIndex(selected_index)
        self.review_build_ae_combo.setEnabled(self.review_build_ae_combo.count() > 0)
        self.review_build_ae_combo.blockSignals(False)

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
            if key and key not in {"env_vars", "paths", "plugin_profiles"}:
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
        plugin_profiles = {}
        for row in range(self.plugin_profile_table.rowCount()):
            profile_item = self.plugin_profile_table.item(row, 0)
            required_item = self.plugin_profile_table.item(row, 1)
            optional_item = self.plugin_profile_table.item(row, 2)
            profile = profile_item.text().strip().lower() if profile_item else ""
            if not profile:
                continue
            plugin_profiles[profile] = {
                "required": self._comma_values(required_item.text() if required_item else ""),
                "optional": self._comma_values(optional_item.text() if optional_item else ""),
            }
        self.software_configs[soft_id]['plugin_profiles'] = plugin_profiles

    def _populate_tree(self, conf):
        self.software_settings_table.setRowCount(0)
        for key, value in conf.items():
            if key in {"env_vars", "paths", "plugin_profiles"}:
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

        self.plugin_profile_table.setRowCount(0)
        profiles = conf.get("plugin_profiles") or {}
        ordered_profiles = ["core", "work_stage", "rend_stage", "update"]
        for profile in [*ordered_profiles, *sorted(set(profiles) - set(ordered_profiles))]:
            values = profiles.get(profile) or {}
            row = self.plugin_profile_table.rowCount()
            self.plugin_profile_table.insertRow(row)
            self.plugin_profile_table.setItem(row, 0, QtWidgets.QTableWidgetItem(profile))
            self.plugin_profile_table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(", ".join(values.get("required") or []))
            )
            self.plugin_profile_table.setItem(
                row, 2, QtWidgets.QTableWidgetItem(", ".join(values.get("optional") or []))
            )

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

    def create_anchors_page(self):
        page = self.create_table_page("Anchors", ["Key", "Value"])
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel("Resolution Preset"))
        self.anchor_resolution_preset = QtWidgets.QComboBox()
        self.anchor_resolution_preset.addItem("Custom", "")
        self.anchor_resolution_preset.addItem(
            "1920 x 1080 (Full HD / 1080p)", "1920x1080"
        )
        self.anchor_resolution_preset.addItem(
            "2048 x 858 (DCP / 2K)", "2048x858"
        )
        preset_row.addWidget(self.anchor_resolution_preset)
        preset_row.addStretch()
        page["widget"].layout().insertLayout(0, preset_row)
        self.anchor_resolution_preset.currentIndexChanged.connect(
            self._apply_anchor_resolution_preset
        )
        page["table"].itemChanged.connect(self._sync_anchor_resolution_preset)
        return page

    def _apply_anchor_resolution_preset(self, _index):
        preset = str(self.anchor_resolution_preset.currentData() or "")
        if not preset:
            return
        width, height = preset.split("x", 1)
        table = self.anchors_table["table"]
        rows = {}
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            key = item.text().strip().lower() if item else ""
            if key in {"resolution x", "resolution y"}:
                rows[key] = row
        table.blockSignals(True)
        try:
            for key, value in (("resolution x", width), ("resolution y", height)):
                row = rows.get(key)
                if row is None:
                    row = table.rowCount()
                    table.insertRow(row)
                    table.setItem(
                        row,
                        0,
                        QtWidgets.QTableWidgetItem(
                            "resolution X" if key.endswith("x") else "resolution Y"
                        ),
                    )
                table.setItem(row, 1, QtWidgets.QTableWidgetItem(value))
        finally:
            table.blockSignals(False)

    def _sync_anchor_resolution_preset(self, _item=None):
        table = self.anchors_table["table"]
        values = {}
        for row in range(table.rowCount()):
            key_item = table.item(row, 0)
            value_item = table.item(row, 1)
            key = key_item.text().strip().lower() if key_item else ""
            if key in {"resolution x", "resolution y"}:
                values[key] = value_item.text().strip() if value_item else ""
        preset = f"{values.get('resolution x', '')}x{values.get('resolution y', '')}"
        index = self.anchor_resolution_preset.findData(preset)
        self.anchor_resolution_preset.blockSignals(True)
        try:
            self.anchor_resolution_preset.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.anchor_resolution_preset.blockSignals(False)

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

        partition_widget = QtWidgets.QWidget()
        partition_layout = QtWidgets.QVBoxLayout(partition_widget)
        partition_layout.setContentsMargins(0, 0, 0, 0)
        partition_layout.addWidget(QtWidgets.QLabel("Default workspace partition"))
        default_partition_edit = QtWidgets.QLineEdit("cg")
        default_partition_edit.setPlaceholderText("cg")
        default_partition_edit.setToolTip(
            "Used by Asset departments and any Shot department without an override."
        )
        partition_layout.addWidget(default_partition_edit)
        partition_layout.addSpacing(12)
        partition_layout.addWidget(QtWidgets.QLabel("Selected department override"))
        partition_edit = QtWidgets.QLineEdit("cg")
        partition_edit.setPlaceholderText("cg, drawing, editorial ...")
        partition_layout.addWidget(partition_edit)
        partition_help = QtWidgets.QLabel(
            "Top-level workspace group for the selected department."
        )
        partition_help.setWordWrap(True)
        partition_layout.addWidget(partition_help)
        partition_layout.addStretch(1)

        recipe_widget = QtWidgets.QWidget()
        recipe_layout = QtWidgets.QVBoxLayout(recipe_widget)
        recipe_layout.setContentsMargins(0, 0, 0, 0)
        recipe_layout.addWidget(QtWidgets.QLabel("Default Sequence Recipe"))
        sequence_recipe_combo = QtWidgets.QComboBox()
        recipe_layout.addWidget(sequence_recipe_combo)
        recipe_help = QtWidgets.QLabel(
            "Initial recipe used by Review Build Manager in Sequence scope."
        )
        recipe_help.setWordWrap(True)
        recipe_layout.addWidget(recipe_help)
        recipe_layout.addStretch(1)

        layout.addWidget(department_widget, 1)
        layout.addWidget(task_widget, 1)
        layout.addWidget(partition_widget, 1)
        layout.addWidget(recipe_widget, 1)

        add_department.clicked.connect(
            lambda: self._add_editable_list_item(
                department_list,
                "new_department",
                ["main"],
                default_partition_edit.text().strip() or "cg",
            )
        )
        remove_department.clicked.connect(
            lambda: department_list.takeItem(department_list.currentRow())
        )
        add_task.clicked.connect(lambda: self._add_editable_list_item(task_list, "new_task"))
        remove_task.clicked.connect(lambda: task_list.takeItem(task_list.currentRow()))
        department_list.currentItemChanged.connect(self._on_shot_department_editor_changed)
        return {
            "widget": widget,
            "list": department_list,
            "tasks": task_list,
            "partition": partition_edit,
            "default_partition": default_partition_edit,
            "sequence_recipe": sequence_recipe_combo,
        }

    def _add_editable_list_item(self, list_widget, text, data=None, partition=None):
        item = QtWidgets.QListWidgetItem(text)
        item.setFlags(
            item.flags()
            | QtCore.Qt.ItemFlag.ItemIsEditable
            | QtCore.Qt.ItemFlag.ItemIsEnabled
            | QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        if data is not None:
            item.setData(QtCore.Qt.ItemDataRole.UserRole, list(data))
        if partition is not None:
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, str(partition))
        list_widget.addItem(item)
        list_widget.setCurrentItem(item)
        list_widget.editItem(item)
        return item

    def _on_shot_department_editor_changed(self, current, previous):
        task_list = self.shot_depts_list["tasks"]
        partition_edit = self.shot_depts_list["partition"]
        if previous is not None:
            previous.setData(
                QtCore.Qt.ItemDataRole.UserRole,
                [task_list.item(index).text().strip() for index in range(task_list.count()) if task_list.item(index).text().strip()],
            )
            previous.setData(
                QtCore.Qt.ItemDataRole.UserRole + 1,
                partition_edit.text().strip() or "cg",
            )
        task_list.clear()
        if current is None:
            partition_edit.clear()
            return
        tasks = current.data(QtCore.Qt.ItemDataRole.UserRole) or ["main"]
        for task in tasks:
            self._add_editable_list_item(task_list, str(task))
        partition_edit.setText(
            str(current.data(QtCore.Qt.ItemDataRole.UserRole + 1) or "cg")
        )

    def _shot_dept_partitions_from_ui(self):
        department_list = self.shot_depts_list["list"]
        current = department_list.currentItem()
        if current is not None:
            current.setData(
                QtCore.Qt.ItemDataRole.UserRole + 1,
                self.shot_depts_list["partition"].text().strip() or "cg",
            )
        default_partition = (
            self.shot_depts_list["default_partition"].text().strip() or "cg"
        )
        result = {"default": default_partition}
        for index in range(department_list.count()):
            item = department_list.item(index)
            department = item.text().strip()
            if department:
                result[department] = str(
                    item.data(QtCore.Qt.ItemDataRole.UserRole + 1)
                    or default_partition
                ).strip() or default_partition
        return result

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
        review_build = data.get("review_build") or {}
        self._refresh_review_build_maya_combo(review_build.get("maya_software"))
        self._refresh_review_build_ae_combo(
            review_build.get("after_effects_software")
        )
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
        partitions = data.get("shot_dept_partitions") or {}
        default_partition = str(partitions.get("default") or "cg")
        self.shot_depts_list["default_partition"].setText(default_partition)
        for department in data.get("shot_depts", []):
            self._add_editable_list_item(
                self.shot_depts_list["list"],
                str(department),
                shot_tasks.get(str(department)) or ["main"],
                partitions.get(str(department)) or default_partition,
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

    def _append_domain_path_templates(self, project_dir=""):
        table = self.template_table["table"]
        existing = {
            table.item(row, 0).text().strip()
            for row in range(table.rowCount())
            if table.item(row, 0)
        }
        for filename, allowed in (
            ("templates_shots.yml", SHOT_PATH_TEMPLATE_KEYS),
            ("templates_assets.yml", ASSET_PATH_TEMPLATE_KEYS),
        ):
            data = load_yml(os.path.join(DEFAULT_DIR, filename))
            if project_dir:
                data = merge_dicts(data, load_yml(os.path.join(project_dir, filename)))
            for key, value in (data.get("templates") or {}).items():
                if key not in allowed or key in existing:
                    continue
                row = table.rowCount()
                table.insertRow(row)
                table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(key)))
                table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(value)))
                existing.add(key)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = ConfigCreatorApp()
    win.show()
    sys.exit(app.exec())
