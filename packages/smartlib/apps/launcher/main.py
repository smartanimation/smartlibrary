import os
import yaml
import json
import subprocess
import sys
import threading
import shutil
from PySide6 import QtWidgets, QtCore, QtGui, QtUiTools

from smartlib.apps.launcher.project_config_transfer import (
    export_project_config,
    import_project_config,
    inspect_project_config_archive,
)

# --- パス設定 ---
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CURRENT_DIR = os.environ.get("SMARTPIPELINE_ROOT") or os.path.abspath(
    os.path.join(APP_DIR, "..", "..", "..", "..")
)
UI_FILE_PATH = os.path.join(APP_DIR, "ui", "launcher.ui")
SMARTPROJECTS_ROOT = os.environ.get("SMARTPIPELINE_STUDIO_CONFIG_DIR") or os.path.normpath(os.path.join(CURRENT_DIR, "..", "smartprojects"))
PROJECTS_ROOT = os.environ.get("SMARTPIPELINE_PROJECT_CONFIG_ROOT") or os.path.join(SMARTPROJECTS_ROOT, "config")
DEFAULT_CONFIG_ROOT = os.path.join(CURRENT_DIR, "config", "default")
SMARTPIPELINE_TOOLS = os.environ.get("SMARTPIPELINE_TOOLS") or os.path.normpath(os.path.join(CURRENT_DIR, "..", "smarttools"))
SCRIPTS_DIR = os.path.join(CURRENT_DIR, "scripts")
GLOBAL_SOFT_PATH = os.path.join(DEFAULT_CONFIG_ROOT, "software_settings.yml")

USER_DATA_DIR = os.path.join(os.environ["APPDATA"], "smartuserdata")
USER_SETTINGS_PATH = os.path.join(USER_DATA_DIR, "smartlauncher_settings.yml")
AE_CONTEXT_PATH = os.path.join(USER_DATA_DIR, "smart_ae_browser_context.json")
MAYA_BOOTSTRAP_STARTUP_DIR = os.path.join(USER_DATA_DIR, "maya_startup")
DEFAULT_MAYA_USER_SETUP = os.path.join(CURRENT_DIR, "packages", "smartlib", "dcc", "maya", "startup", "userSetup.py")

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


def allowed_launcher_tools(studio_config):
    """Return an allowlist for SmartTools, or None when every tool is allowed."""
    data = studio_config if isinstance(studio_config, dict) else {}
    role = str((data.get("studio") or {}).get("role") or "internal").strip().lower()
    configured = (data.get("launcher") or {}).get("allowed_tools")
    if isinstance(configured, (list, tuple, set)):
        return {str(tool).strip().lower() for tool in configured if str(tool).strip()}
    if role == "vendor":
        return {"smart_delivery"}
    return None

def is_maya_software(soft_id, exe_path):
    name = str(soft_id or "").lower()
    exe_name = os.path.basename(str(exe_path or "")).lower()
    return "maya" in name or exe_name.startswith("maya")


def is_after_effects_software(soft_id, exe_path):
    name = str(soft_id or "").lower()
    exe_name = os.path.basename(str(exe_path or "")).lower()
    return "aftereffects" in name or name.startswith("ae") or exe_name.startswith("afterfx")


def is_openrv_software(soft_id):
    return str(soft_id or "").lower() in {"openrv", "rv", "rvplayer", "rv_player"}


def apply_project_context_env(env, *, project_name, project_root, config_dir, source="launcher", episode="", sequence="", shot=""):
    env["PROJECT_CONFIG_DIR"] = config_dir
    env["SMART_PROJECT_CONFIG_DIR"] = config_dir
    env["SMARTPIPELINE_STUDIO_CONFIG_DIR"] = SMARTPROJECTS_ROOT
    env["SMARTPIPELINE_STUDIO_CONFIG"] = os.path.join(SMARTPROJECTS_ROOT, "studio.yml")
    env["SMARTPIPELINE_TOOLS"] = SMARTPIPELINE_TOOLS
    env["SMART_PROJECT"] = str(project_name or "")
    env["SMART_PROJECT_ROOT"] = str(project_root or "")
    env["SMART_REVIEW_PROJECT"] = str(project_name or "")
    env["SMART_REVIEW_PROJECT_ROOT"] = str(project_root or "")
    env["SMART_REVIEW_CONFIG_DIR"] = str(config_dir or "")
    env["SMART_REVIEW_PROJECT_CONFIG_ROOT"] = os.path.dirname(str(config_dir or "")) if config_dir else PROJECTS_ROOT
    env["PROJECT_NAME"] = str(project_name or "")
    env["PROJECT_ROOT"] = str(project_root or "")
    env["SMART_CONTEXT_SOURCE"] = str(source or "")
    env["SMART_EPISODE"] = str(episode or "")
    env["SMART_SEQUENCE"] = str(sequence or "")
    env["SMART_SHOT"] = str(shot or "")
    if not any(env.get(name) for name in ("CREDENTIALS_PATH", "GOOGLE_APPLICATION_CREDENTIALS", "CREDENTIALS_DIR")):
        configured_credentials = studio_credentials_path() or project_credentials_path(
            config_dir, project_root
        )
        if configured_credentials:
            env["CREDENTIALS_PATH"] = configured_credentials


def write_ae_context(context):
    os.makedirs(os.path.dirname(AE_CONTEXT_PATH), exist_ok=True)
    payload = dict(context)
    payload["context_path"] = AE_CONTEXT_PATH
    with open(AE_CONTEXT_PATH, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    return AE_CONTEXT_PATH

def apply_pipeline_pythonpath(env, *, maya_safe=False):
    if maya_safe:
        env.pop("PYTHONPATH", None)
        return
    python_paths = [os.path.join(CURRENT_DIR, "packages"), CURRENT_DIR]
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        python_paths.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(python_paths)


def prepend_env_path(env, key, paths):
    existing = env.get(key, "")
    clean_paths = [str(path) for path in paths if path]
    env[str(key)] = os.pathsep.join(clean_paths) + (os.pathsep + existing if existing else "")


def append_unique_env_path(env, key, paths):
    existing = [path for path in env.get(key, "").split(os.pathsep) if path]
    seen = {os.path.normcase(os.path.normpath(path)) for path in existing}
    for path in paths:
        if not path:
            continue
        clean_path = str(path)
        norm = os.path.normcase(os.path.normpath(clean_path))
        if norm in seen:
            continue
        existing.append(clean_path)
        seen.add(norm)
    if existing:
        env[str(key)] = os.pathsep.join(existing)


def _maya_user_setup_from_spec(spec_data, projectroot, env):
    raw_value = (
        env.get("SMART_MAYA_USER_SETUP")
        or spec_data.get("maya_user_setup")
        or spec_data.get("user_setup")
        or spec_data.get("userSetup")
        or ""
    )
    if isinstance(raw_value, dict):
        raw_value = raw_value.get("path") or raw_value.get("file") or ""
    if isinstance(raw_value, (list, tuple)):
        raw_value = raw_value[0] if raw_value else ""

    candidate = os.path.normpath(format_config_value(raw_value, projectroot)) if raw_value else ""
    if candidate and os.path.isdir(candidate):
        candidate = os.path.join(candidate, "userSetup.py")
    if candidate and os.path.exists(candidate):
        return candidate
    if os.path.exists(DEFAULT_MAYA_USER_SETUP):
        return DEFAULT_MAYA_USER_SETUP
    return ""


def _write_maya_user_setup_bootstrap(target_path):
    os.makedirs(MAYA_BOOTSTRAP_STARTUP_DIR, exist_ok=True)
    bootstrap_path = os.path.join(MAYA_BOOTSTRAP_STARTUP_DIR, "userSetup.py")
    normalized_target = str(target_path or "").replace("\\", "/")
    payload = f'''from __future__ import annotations

import os
import runpy
import traceback

target = os.environ.get("SMART_MAYA_USER_SETUP") or r"{normalized_target}"
if target:
    target = target.replace("\\\\", "/")

marker = target.lower() if target else ""
already_executed = os.environ.get("SMART_MAYA_USER_SETUP_EXECUTED", "").lower()

if marker and already_executed == marker:
    pass
elif target and os.path.exists(target):
    try:
        os.environ["SMART_MAYA_USER_SETUP_EXECUTED"] = target
        runpy.run_path(target, run_name="__smartpipeline_userSetup__")
    except Exception:
        os.environ.pop("SMART_MAYA_USER_SETUP_EXECUTED", None)
        traceback.print_exc()
else:
    print("[SmartPipeline] userSetup.py was not found: {{}}".format(target))
'''
    with open(bootstrap_path, "w", encoding="utf-8") as stream:
        stream.write(payload)
    return MAYA_BOOTSTRAP_STARTUP_DIR


def maya_bootstrap_command():
    bootstrap_path = os.path.join(MAYA_BOOTSTRAP_STARTUP_DIR, "userSetup.py")
    normalized = bootstrap_path.replace("\\", "/")
    python_code = (
        "import runpy; "
        f"runpy.run_path(r'{normalized}', run_name='__smartpipeline_bootstrap__')"
    )
    escaped = python_code.replace("\\", "\\\\").replace('"', '\\"')
    return f'python("{escaped}")'


def apply_maya_startup_path(env, spec_data=None, projectroot=""):
    spec_data = spec_data or {}
    target_path = _maya_user_setup_from_spec(spec_data, projectroot, env)
    if not target_path:
        return
    env["SMART_MAYA_USER_SETUP"] = target_path
    startup_path = _write_maya_user_setup_bootstrap(target_path)
    prepend_env_path(env, "MAYA_SCRIPT_PATH", [startup_path])


def apply_config_env_vars(env, env_vars, projectroot, *, maya_safe=False):
    for k, v in env_vars.items():
        key = str(k)
        value = format_config_value(v, projectroot)
        if maya_safe and key.upper() == "PYTHONPATH":
            if value.strip():
                env[key] = value
            else:
                env.pop(key, None)
            continue
        env[key] = value


def software_process_env_vars(spec_data):
    """Merge legacy top-level Maya switches with explicit environment data."""

    values = {
        str(key): value
        for key, value in (spec_data or {}).items()
        if str(key).upper().startswith("MAYA_")
    }
    values.update((spec_data or {}).get("env_vars", {}) or {})
    return values


def runtime_python_path():
    candidates = [
        os.path.join(SMARTPIPELINE_TOOLS, "python", "python.exe"),
        os.path.join(CURRENT_DIR, "runtime", "python", "python.exe"),
    ]
    for runtime in candidates:
        if os.path.exists(runtime):
            return runtime
    return sys.executable


def studio_credentials_path():
    studio_config = load_yml(
        os.environ.get("SMARTPIPELINE_STUDIO_CONFIG")
        or os.path.join(SMARTPROJECTS_ROOT, "studio.yml")
    )
    raw_value = str(
        ((studio_config.get("google_sheets") or {}).get("credentials_path") or "")
    ).strip()
    if not raw_value:
        return ""
    candidate = os.path.normpath(
        os.path.expandvars(os.path.expanduser(raw_value.strip().strip('"')))
    )
    if os.path.isdir(candidate):
        candidate = os.path.join(candidate, "credentials.json")
    return candidate


def project_credentials_path(config_dir, project_root=""):
    base_config = load_yml(os.path.join(str(config_dir or ""), "templates_base.yml"))
    raw_value = str(
        ((base_config.get("google_sheets") or {}).get("credentials_path") or "")
    ).strip()
    if not raw_value:
        return ""
    candidate = format_config_value(raw_value, str(project_root or ""))
    candidate = os.path.normpath(os.path.expandvars(os.path.expanduser(candidate.strip().strip('"'))))
    if os.path.isdir(candidate):
        candidate = os.path.join(candidate, "credentials.json")
    return candidate


def credentials_path_for_sync(config_dir="", project_root=""):
    raw_value = (
        os.environ.get("CREDENTIALS_PATH")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or os.environ.get("CREDENTIALS_DIR")
        or ""
    )
    if raw_value:
        candidate = os.path.normpath(raw_value.strip().strip('"'))
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, "credentials.json")
        return candidate

    configured = studio_credentials_path() or project_credentials_path(
        config_dir, project_root
    )
    if configured:
        return configured

    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "credentials.json")
    return ""


def format_config_value(value, projectroot):
    return (
        str(value)
        .replace("{project_root}", projectroot)
        .replace("{smartpipeline_root}", CURRENT_DIR)
        .replace("{smartlibrary_root}", CURRENT_DIR)
        .replace("{smartpipeline_tools}", SMARTPIPELINE_TOOLS)
        .replace("{smarttools_root}", SMARTPIPELINE_TOOLS)
        .replace("{SMARTPIPELINE_TOOLS}", SMARTPIPELINE_TOOLS)
    )


def apply_maya_common_pythonpath(env, projectroot=""):
    """Expose the studio's DCC-safe shared Python packages to Maya."""

    studio_config = load_yml(os.path.join(SMARTPROJECTS_ROOT, "studio.yml"))
    raw_path = (
        ((studio_config.get("third_party") or {}).get("python") or {}).get("path")
        or ""
    )
    python_path = os.path.normpath(format_config_value(raw_path, projectroot)) if raw_path else ""
    if python_path and os.path.isdir(python_path):
        prepend_env_path(env, "PYTHONPATH", [python_path])


def resolve_openrv_executable(config_dir, projectroot):
    for tools_path in (
        os.path.join(config_dir, "tools.yml") if config_dir else "",
        os.environ.get("SMARTPIPELINE_STUDIO_CONFIG", ""),
        os.path.join(SMARTPROJECTS_ROOT, "tools.yml"),
        os.path.join(DEFAULT_CONFIG_ROOT, "tools.yml"),
    ):
        data = load_yml(tools_path) if tools_path else {}
        raw_path = (((data.get("tools") or {}).get("openrv") or {}).get("path") or "").strip()
        if not raw_path:
            continue
        path = os.path.normpath(format_config_value(raw_path, projectroot))
        if "{version}" in path:
            import glob

            matches = sorted(glob.glob(path.replace("{version}", "*")))
            if matches:
                path = matches[-1]
        if path and os.path.exists(path):
            return path

    for env_name in ("OPENRV_PATH", "RV_PATH", "SMART_RENDER_RV_PATH"):
        value = os.environ.get(env_name, "").strip().strip('"')
        if value and os.path.exists(value):
            return os.path.normpath(value)

    openrv_root = os.path.join(CURRENT_DIR, "tools", "OpenRV")
    if os.path.isdir(openrv_root):
        candidates = sorted(
            os.path.join(openrv_root, name, "bin", "rv.exe")
            for name in os.listdir(openrv_root)
            if name.startswith("OpenRV-")
        )
        for path in reversed(candidates):
            if os.path.exists(path):
                return os.path.normpath(path)

    for name in ("rv.exe", "rv"):
        found = shutil.which(name)
        if found:
            return os.path.normpath(found)
    return ""


def resolve_configured_tool(config_dir, projectroot, tool_name):
    for tools_path in (
        os.path.join(config_dir, "tools.yml") if config_dir else "",
        os.environ.get("SMARTPIPELINE_STUDIO_CONFIG", ""),
        os.path.join(SMARTPROJECTS_ROOT, "tools.yml"),
        os.path.join(DEFAULT_CONFIG_ROOT, "tools.yml"),
    ):
        data = load_yml(tools_path) if tools_path else {}
        raw_path = (((data.get("tools") or {}).get(tool_name) or {}).get("path") or "").strip()
        if not raw_path:
            continue
        path = os.path.normpath(format_config_value(raw_path, projectroot))
        if path and os.path.exists(path):
            return path
    env_value = os.environ.get(f"{str(tool_name).upper()}_PATH", "").strip().strip('"')
    if env_value and os.path.exists(env_value):
        return os.path.normpath(env_value)
    return ""

class SmartLauncher(QtWidgets.QMainWindow):
    setup_finished_signal = QtCore.Signal()
    asset_sync_signal = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Launcher")
        self.studio_config = load_yml(os.path.join(SMARTPROJECTS_ROOT, "studio.yml"))
        self.allowed_smart_tools = allowed_launcher_tools(self.studio_config)
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
        
        self.restore_window_geometry()

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
        self.ui.appview.setUniformItemSizes(True)
        self.ui.appview.setSpacing(0)
        self.ui.appview.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

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

    def application_icon(self, info, exe_path, provider):
        raw_icon = str(info.get("icon") or "").strip()
        if raw_icon:
            formatted = format_config_value(raw_icon, self.projectroot)
            candidates = [formatted]
            if not os.path.isabs(formatted):
                candidates.extend(
                    [
                        os.path.join(CURRENT_DIR, formatted),
                        os.path.join(CURRENT_DIR, "resources", "icons", formatted),
                    ]
                )
            for candidate in candidates:
                icon_path = os.path.normpath(candidate)
                if not os.path.exists(icon_path):
                    continue
                if os.path.splitext(icon_path)[1].lower() in {".png", ".jpg", ".jpeg", ".svg", ".ico"}:
                    icon = QtGui.QIcon(icon_path)
                else:
                    icon = provider.icon(QtCore.QFileInfo(icon_path))
                if not icon.isNull():
                    return icon

        if exe_path and os.path.exists(exe_path):
            icon = provider.icon(QtCore.QFileInfo(exe_path))
            if not icon.isNull():
                return icon

        return self.style().standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon
        )

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

    def restore_window_geometry(self):
        encoded = str(self.user_settings.get("window_geometry") or "").strip()
        if encoded:
            geometry = QtCore.QByteArray.fromBase64(encoded.encode("ascii"))
            if not geometry.isEmpty() and self.restoreGeometry(geometry):
                return
        size = self.user_settings.get("last_window_size", [1000, 800])
        if isinstance(size, (list, tuple)) and len(size) >= 2:
            self.resize(int(size[0]), int(size[1]))

    def closeEvent(self, event):
        geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.user_settings["window_geometry"] = geometry
        normal = self.normalGeometry() if self.isMaximized() else self.geometry()
        self.user_settings["last_window_size"] = [normal.width(), normal.height()]
        save_yml(USER_SETTINGS_PATH, self.user_settings)
        super().closeEvent(event)

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
        cfg_dir = os.path.join(PROJECTS_ROOT, folder_name)
        enabled = []
        for soft_id in cfg.get('enabled_softwares', []):
            project_info = load_yml(os.path.join(cfg_dir, f"software_{soft_id}.yml"))
            source_id = project_info.get("source_software") or soft_id
            if not is_openrv_software(source_id):
                enabled.append(soft_id)
        if getattr(self, "usdview_action", None) is not None:
            usdview_path = resolve_configured_tool(cfg_dir, self.projectroot, "usdview")
            self.usdview_action.setEnabled(bool(usdview_path))
            self.usdview_action.setToolTip(
                usdview_path or "Set tools.usdview.path in tools.yml or USDVIEW_PATH."
            )
        master_data = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        provider = QtWidgets.QFileIconProvider()
        for soft_id in enabled:
            project_info = load_yml(os.path.join(cfg_dir, f"software_{soft_id}.yml"))
            source_id = project_info.get("source_software") or soft_id
            info = dict(master_data.get(source_id, {}))
            info.update(project_info)
            item = QtGui.QStandardItem(info.get('name', soft_id.upper()))
            item.setEditable(False)
            raw_path = info.get('path', "")
            exe_path = os.path.normpath(format_config_value(raw_path, self.projectroot)) if raw_path else ""
            item.setIcon(self.application_icon(info, exe_path, provider))
            item.setSizeHint(QtCore.QSize(0, 52))
            item.setData(soft_id, QtCore.Qt.UserRole)
            self.app_model.appendRow(item)

    def check_asset_sheet_cache(self, folder_name):
        cfg_dir = os.path.join(PROJECTS_ROOT, folder_name)
        base_cfg = load_yml(os.path.join(cfg_dir, "templates_base.yml"))
        sheet_id = (base_cfg.get("google_sheets") or {}).get("asset_list_id")
        if not sheet_id:
            self.asset_sync_signal.emit("Asset sheet: not configured")
            return

        credentials = credentials_path_for_sync(
            cfg_dir,
            (base_cfg.get("anchors") or {}).get("project_root", self.projectroot),
        )
        if not credentials or not os.path.isfile(credentials):
            cache_path = os.path.join(cfg_dir, ".cache", "asset_list.json")
            if os.path.exists(cache_path):
                self.asset_sync_signal.emit("Asset sheet: using cache")
            else:
                self.asset_sync_signal.emit(
                    f"Asset sheet: credentials not found ({credentials or 'not configured'})"
                )
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
        env["CREDENTIALS_PATH"] = credentials

        try:
            result = subprocess.run(
                [runtime_python_path(), script, "--config-dir", cfg_dir, "--credentials", credentials],
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
            detail = str(message or "").strip()
            normalized = detail.lower()
            synced = (
                "sync failed" not in normalized
                and any(
                    marker in normalized
                    for marker in ("synced", "up to date", "checked")
                )
            )
            status = "Synced" if synced else "Not synced"
            color = "#77d66b" if synced else "#e57373"
            current = self.ui.info_label.text()
            lines = current.split("<br>") if current else []
            lines = [line for line in lines if not line.startswith("ASSETS:")]
            lines.append(f"ASSETS: <span style='color: {color};'>{status}</span>")
            self.ui.info_label.setText("<br>".join(lines))
            self.ui.info_label.setToolTip(detail)

    def launch_selected(self):
        """アプリ起動：個別設定のパスを最優先し、batはクリーンに起動する"""
        idx = self.ui.appview.selectedIndexes()
        if not idx: return
        soft_id = idx[0].data(QtCore.Qt.UserRole)
        display_project = self.ui.projectCombo.currentText()
        folder_name = self.project_map.get(display_project)
        if not folder_name: return
        cfg_dir = os.path.join(PROJECTS_ROOT, folder_name)
        base_cfg = load_yml(os.path.join(cfg_dir, "templates_base.yml"))
        anchors = base_cfg.get("anchors", {})
        project_name = anchors.get("project_name", display_project)
        project_root = anchors.get("project_root", self.projectroot)

        preliminary_spec = load_yml(os.path.join(cfg_dir, f"software_{soft_id}.yml"))
        preliminary_source_id = preliminary_spec.get('source_software') or soft_id
        if is_openrv_software(preliminary_source_id):
            self.launch_openrv_player(
                cfg_dir=cfg_dir,
                project_name=project_name,
                project_root=project_root,
            )
            return

        # --- 1. パスの決定 (個別設定を最優先) ---
        # プロジェクト固有の software_xxx.yml をロード
        specific_conf_path = os.path.join(PROJECTS_ROOT, folder_name, f"software_{soft_id}.yml")
        spec_data = load_yml(specific_conf_path)
        
        # マスターデータをロード
        master_data = load_yml(GLOBAL_SOFT_PATH).get('softwares', {})
        source_id = spec_data.get('source_software') or soft_id
        master_info = master_data.get(source_id, {})

        # 個別設定のpathがあればそれを使う、なければマスターを使う
        raw_exe_path = spec_data.get('path') or master_info.get('path', "")
        
        if not raw_exe_path:
            QtWidgets.QMessageBox.warning(self, "Error", f"Executable path not defined for: {soft_id}")
            return

        exe_p = os.path.normpath(format_config_value(raw_exe_path, self.projectroot))

        if not os.path.exists(exe_p):
            QtWidgets.QMessageBox.warning(self, "Error", f"Executable not found: {exe_p}")
            return

        # --- 2. 起動準備 ---
        is_batch = exe_p.lower().endswith(('.bat', '.cmd'))
        maya_safe = is_maya_software(soft_id, exe_p)
        after_effects = is_after_effects_software(soft_id, exe_p)
        full_env = os.environ.copy()
        full_env["SMARTLIBRARY_ROOT"] = CURRENT_DIR
        full_env["SMARTPIPELINE_ROOT"] = CURRENT_DIR
        apply_project_context_env(
            full_env,
            project_name=project_name,
            project_root=project_root,
            config_dir=cfg_dir,
            source="launcher",
        )
        if after_effects:
            write_ae_context(
                {
                    "source": "launcher",
                    "project": project_name,
                    "projectRoot": project_root,
                    "configDir": cfg_dir,
                    "episode": "",
                    "sequence": "",
                    "shot": "",
                }
            )

        apply_pipeline_pythonpath(full_env, maya_safe=maya_safe)

        apply_config_env_vars(
            full_env,
            software_process_env_vars(spec_data),
            self.projectroot,
            maya_safe=maya_safe,
        )

        for k, p_list in spec_data.get('paths', {}).items():
            if isinstance(p_list, list):
                formatted = [format_config_value(p, self.projectroot) for p in p_list]
                prepend_env_path(full_env, str(k), formatted)
        if maya_safe:
            apply_maya_common_pythonpath(full_env, self.projectroot)
            apply_maya_startup_path(full_env, spec_data, self.projectroot)

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
                full_env["SMARTLIBRARY_ROOT"] = CURRENT_DIR
                full_env["SMARTPIPELINE_ROOT"] = CURRENT_DIR
                apply_project_context_env(
                    full_env,
                    project_name=project_name,
                    project_root=project_root,
                    config_dir=cfg_dir,
                    source="launcher",
                )

                apply_pipeline_pythonpath(full_env, maya_safe=maya_safe)
                
                # env_vars の反映
                apply_config_env_vars(
                    full_env,
                    software_process_env_vars(spec_data),
                    self.projectroot,
                    maya_safe=maya_safe,
                )
                
                # paths の反映
                for k, p_list in spec_data.get('paths', {}).items():
                    if isinstance(p_list, list):
                        formatted = [format_config_value(p, self.projectroot) for p in p_list]
                        prepend_env_path(full_env, str(k), formatted)
                if maya_safe:
                    apply_maya_common_pythonpath(full_env, self.projectroot)
                    apply_maya_startup_path(full_env, spec_data, self.projectroot)

                launch_args = [exe_p]
                if maya_safe:
                    launch_args.extend(["-command", maya_bootstrap_command()])

                subprocess.Popen(
                    launch_args,
                    env=full_env,
                    cwd=self.projectroot if os.path.exists(self.projectroot) else None,
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Launch failed: {e}")

    def launch_openrv_player(self, *, cfg_dir, project_name, project_root, show_smart_review=False):
        rv_path = resolve_openrv_executable(cfg_dir, project_root)
        if not rv_path:
            QtWidgets.QMessageBox.warning(
                self,
                "OpenRV Not Found",
                "Set tools.openrv.path in tools.yml or set OPENRV_PATH / RV_PATH.",
            )
            return

        env = os.environ.copy()
        env["SMARTLIBRARY_ROOT"] = CURRENT_DIR
        env["SMARTPIPELINE_ROOT"] = CURRENT_DIR
        apply_project_context_env(
            env,
            project_name=project_name,
            project_root=project_root,
            config_dir=cfg_dir,
            source="launcher",
        )
        if show_smart_review:
            env["SMART_REVIEW_SHOW_PANEL"] = "1"
        env["OPENRV_PATH"] = rv_path
        env["RV_PATH"] = rv_path
        rv_support_roots = [
            os.path.join(os.environ.get("APPDATA", ""), "RV"),
            os.path.join(os.environ.get("APPDATA", ""), "TweakSoftware", "RV"),
        ]
        append_unique_env_path(
            env,
            "MU_MODULE_PATH",
            [os.path.join(root, "Mu") for root in rv_support_roots],
        )
        append_unique_env_path(
            env,
            "RV_SUPPORT_PATH",
            [os.path.join(root, "Packages") for root in rv_support_roots],
        )
        append_unique_env_path(
            env,
            "PYTHONPATH",
            [os.path.join(root, "Python") for root in rv_support_roots],
        )
        apply_project_context_env(
            env,
            project_name=project_name,
            project_root=project_root,
            config_dir=cfg_dir,
            source="launcher",
        )

        try:
            subprocess.Popen(
                [rv_path, "-flags", "ModeManagerVerbose=true"],
                cwd=project_root if project_root and os.path.exists(project_root) else os.path.dirname(rv_path),
                env=env,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "OpenRV", f"Launch failed: {e}")

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
        studio_path = os.environ.get("SMARTPIPELINE_STUDIO_CONFIG") or os.path.join(SMARTPROJECTS_ROOT, "studio.yml")
        studio = load_yml(studio_path).get("studio") or {}
        mode = "vendor" if str(studio.get("role") or "").lower() == "vendor" else "internal"
        creator = self._config_creator_class(mode)
        self.creator_win = creator(config_mode=mode)
        self.creator_win.config_saved.connect(self.refresh_projects)
        self.creator_win.show()

    def open_config_creator_edit(self):
        folder_name = self.project_map.get(self.ui.projectCombo.currentText())
        if folder_name:
            studio_path = os.environ.get("SMARTPIPELINE_STUDIO_CONFIG") or os.path.join(SMARTPROJECTS_ROOT, "studio.yml")
            studio = load_yml(studio_path).get("studio") or {}
            mode = "vendor" if str(studio.get("role") or "").lower() == "vendor" else "internal"
            creator = self._config_creator_class(mode)
            self.creator_win = creator(target_project=folder_name, config_mode=mode)
            self.creator_win.config_saved.connect(self.refresh_projects)
            self.creator_win.show()

    @staticmethod
    def _config_creator_class(mode):
        if mode == "vendor":
            from smartlib.apps.launcher.vendor_studio_config import ConfigCreatorApp
            return ConfigCreatorApp
        try:
            from scripts.config_creator import ConfigCreatorApp
        except ImportError:
            from config_creator import ConfigCreatorApp
        return ConfigCreatorApp

    def current_project_config_dir(self):
        folder_name = self.project_map.get(self.ui.projectCombo.currentText())
        if not folder_name:
            return ""
        return os.path.join(PROJECTS_ROOT, folder_name)

    def launch_smart_tool(self, tool_name):
        if not self.is_smart_tool_allowed(tool_name):
            QtWidgets.QMessageBox.warning(
                self,
                "SmartTools",
                f"{tool_name} is not enabled by the studio Launcher policy.",
            )
            return
        cfg_dir = self.current_project_config_dir()
        if not cfg_dir:
            QtWidgets.QMessageBox.warning(self, "SmartTools", "Select a project first.")
            return
        python = runtime_python_path()
        env = os.environ.copy()
        env["SMARTLIBRARY_ROOT"] = CURRENT_DIR
        env["SMARTPIPELINE_ROOT"] = CURRENT_DIR
        display_project = self.ui.projectCombo.currentText()
        base_cfg = load_yml(os.path.join(cfg_dir, "templates_base.yml"))
        anchors = base_cfg.get("anchors", {})
        apply_project_context_env(
            env,
            project_name=anchors.get("project_name", display_project),
            project_root=anchors.get("project_root", self.projectroot),
            config_dir=cfg_dir,
            source="launcher",
        )
        apply_pipeline_pythonpath(env, maya_safe=False)

        tool_commands = {
            "asset_manager": [python, os.path.join(SCRIPTS_DIR, "asset_manager_ui.py")],
            "assembly_manager": [python, "-m", "smartlib.apps.assembly_manager", "--config-dir", cfg_dir],
            "sequence_manager": [python, "-m", "smartlib.apps.sequence_manager", "--config-dir", cfg_dir],
            "editorial_intake": [python, "-m", "smartlib.apps.editorial_intake"],
            "smart_ingest": [python, "-m", "smartlib.apps.smart_ingest"],
            "smart_casting": [python, "-m", "smartlib.apps.smart_casting", cfg_dir],
            "shot_manager": [python, os.path.join(SCRIPTS_DIR, "shot_manager_ui.py")],
            "review_build_manager": [
                python,
                "-m",
                "smartlib.apps.review_build_manager",
                "--config-dir",
                cfg_dir,
            ],
            "smart_delivery": [
                python,
                "-m",
                "smartlib.apps.smart_delivery",
                "--config-dir",
                cfg_dir,
            ],
        }
        command = tool_commands.get(tool_name)
        if not command:
            QtWidgets.QMessageBox.warning(self, "SmartTools", f"Unknown tool: {tool_name}")
            return
        try:
            log_dir = os.path.join(USER_DATA_DIR, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, f"{tool_name}.log")
            log_stream = open(log_path, "w", encoding="utf-8")
            process = subprocess.Popen(
                command,
                cwd=CURRENT_DIR,
                env=env,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            log_stream.close()
            QtCore.QTimer.singleShot(
                1200,
                lambda p=process, name=tool_name, path=log_path: self._report_smart_tool_exit(
                    p, name, path
                ),
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "SmartTools", f"Launch failed: {e}")

    def _report_smart_tool_exit(self, process, tool_name, log_path):
        exit_code = process.poll()
        if exit_code is None or exit_code == 0:
            return
        details = ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as stream:
                details = stream.read().strip()
        except OSError:
            pass
        if len(details) > 1200:
            details = details[-1200:]
        message = f"{tool_name} exited with code {exit_code}.\nLog: {log_path}"
        if details:
            message += f"\n\n{details}"
        QtWidgets.QMessageBox.critical(self, "SmartTools Launch Failed", message)

    def launch_current_project_openrv(self):
        cfg_dir = self.current_project_config_dir()
        if not cfg_dir:
            QtWidgets.QMessageBox.warning(self, "OpenRV", "Select a project first.")
            return
        display_project = self.ui.projectCombo.currentText()
        base_cfg = load_yml(os.path.join(cfg_dir, "templates_base.yml"))
        anchors = base_cfg.get("anchors", {})
        self.launch_openrv_player(
            cfg_dir=cfg_dir,
            project_name=anchors.get("project_name", display_project),
            project_root=anchors.get("project_root", self.projectroot),
        )

    def launch_current_project_smart_review(self):
        cfg_dir = self.current_project_config_dir()
        if not cfg_dir:
            QtWidgets.QMessageBox.warning(self, "Smart Review", "Select a project first.")
            return
        display_project = self.ui.projectCombo.currentText()
        base_cfg = load_yml(os.path.join(cfg_dir, "templates_base.yml"))
        anchors = base_cfg.get("anchors", {})
        self.launch_openrv_player(
            cfg_dir=cfg_dir,
            project_name=anchors.get("project_name", display_project),
            project_root=anchors.get("project_root", self.projectroot),
            show_smart_review=True,
        )

    def launch_current_project_usdview(self):
        cfg_dir = self.current_project_config_dir()
        if not cfg_dir:
            QtWidgets.QMessageBox.warning(self, "USD View", "Select a project first.")
            return
        usdview_path = resolve_configured_tool(cfg_dir, self.projectroot, "usdview")
        if not usdview_path:
            QtWidgets.QMessageBox.warning(
                self,
                "USD View Not Found",
                "Set tools.usdview.path in tools.yml or set USDVIEW_PATH.",
            )
            return
        usd_file, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open USD in usdview",
            self.projectroot if self.projectroot and os.path.exists(self.projectroot) else "",
            "USD Files (*.usd *.usda *.usdc *.usdz);;All Files (*)",
        )
        if not usd_file:
            return
        env = os.environ.copy()
        env["SMARTLIBRARY_ROOT"] = CURRENT_DIR
        env["SMARTPIPELINE_ROOT"] = CURRENT_DIR
        env["SMARTPIPELINE_TOOLS"] = SMARTPIPELINE_TOOLS
        display_project = self.ui.projectCombo.currentText()
        base_cfg = load_yml(os.path.join(cfg_dir, "templates_base.yml"))
        anchors = base_cfg.get("anchors", {})
        apply_project_context_env(
            env,
            project_name=anchors.get("project_name", display_project),
            project_root=anchors.get("project_root", self.projectroot),
            config_dir=cfg_dir,
            source="launcher",
        )
        suffix = os.path.splitext(usdview_path)[1].lower()
        if suffix in {".bat", ".cmd"}:
            batch_command = subprocess.list2cmdline([usdview_path, os.path.normpath(usd_file)])
            command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", batch_command]
        else:
            command = [usdview_path, os.path.normpath(usd_file)]
        try:
            subprocess.Popen(
                command,
                cwd=self.projectroot if self.projectroot and os.path.exists(self.projectroot) else CURRENT_DIR,
                env=env,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "USD View", f"Launch failed: {e}")

    def check_project_status(self, root):
        is_ready = os.path.exists(root) if root else False
        if hasattr(self.ui, 'setup_button'):
            self.ui.setup_button.setVisible(not is_ready)

    def run_pipeline_setup(self):
        folder_name = self.project_map.get(self.ui.projectCombo.currentText())
        env = os.environ.copy()
        cfg_dir = os.path.join(PROJECTS_ROOT, folder_name)
        env["PROJECT_CONFIG_DIR"] = cfg_dir
        env["SMARTPIPELINE_ROOT"] = CURRENT_DIR
        env["SMARTLIBRARY_ROOT"] = CURRENT_DIR
        env["SMARTPIPELINE_TOOLS"] = SMARTPIPELINE_TOOLS
        env["SMARTPIPELINE_STUDIO_CONFIG_DIR"] = SMARTPROJECTS_ROOT
        env["SMARTPIPELINE_STUDIO_CONFIG"] = os.path.join(SMARTPROJECTS_ROOT, "studio.yml")
        script = os.path.join(SCRIPTS_DIR, "init_project.py")
        threading.Thread(target=lambda: (subprocess.run([sys.executable, script], env=env), self.setup_finished_signal.emit()), daemon=True).start()

    def is_smart_tool_allowed(self, tool_name):
        return (
            self.allowed_smart_tools is None
            or str(tool_name).strip().lower() in self.allowed_smart_tools
        )

    def is_vendor_studio(self):
        studio = (self.studio_config or {}).get("studio") or {}
        return str(studio.get("role") or "internal").strip().lower() == "vendor"

    def export_current_project_config(self):
        config_dir = self.current_project_config_dir()
        if not config_dir:
            QtWidgets.QMessageBox.warning(self, "Export Project Config", "Select a project first.")
            return
        project = os.path.basename(config_dir)
        archive, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Project Config",
            f"{project}-smartproject.zip",
            "SmartProject Archives (*.zip)",
        )
        if not archive:
            return
        if not archive.lower().endswith(".zip"):
            archive += ".zip"
        try:
            exported = export_project_config(config_dir, archive, project)
            QtWidgets.QMessageBox.information(
                self, "Export Project Config", f"Project config exported:\n{exported}"
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export Project Config", str(exc))

    def import_project_config_archive(self):
        archive, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import Project Config",
            "",
            "SmartProject Archives (*.zip)",
        )
        if not archive:
            return
        try:
            project = inspect_project_config_archive(archive)["project"]
            target = os.path.join(PROJECTS_ROOT, project)
            replace = False
            if os.path.exists(target):
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Replace Project Config",
                    f"Project config '{project}' already exists. Replace it?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                if answer != QtWidgets.QMessageBox.Yes:
                    return
                replace = True
            imported = import_project_config(archive, PROJECTS_ROOT, replace=replace)
            self.refresh_projects()
            QtWidgets.QMessageBox.information(
                self, "Import Project Config", f"Project config installed:\n{imported}"
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import Project Config", str(exc))

    def setup_menus(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("FILE")
        tools_menu = menubar.addMenu("SmartTools")
        tool_specs = (
            ("asset_manager", "Asset Manager", QtWidgets.QStyle.StandardPixmap.SP_DirIcon),
            ("assembly_manager", "Assembly Manager", QtWidgets.QStyle.StandardPixmap.SP_FileDialogListView),
            ("sequence_manager", "Sequence Manager", QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView),
            ("smart_ingest", "Smart Ingest", QtWidgets.QStyle.StandardPixmap.SP_DriveHDIcon),
            ("smart_casting", "Smart Casting", QtWidgets.QStyle.StandardPixmap.SP_DialogApplyButton),
            ("shot_manager", "Shot Manager", QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon),
            ("review_build_manager", "Review Build Manager", QtWidgets.QStyle.StandardPixmap.SP_MediaPlay),
            ("smart_delivery", "Smart Delivery", QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton),
        )
        for tool_id, label, icon_type in tool_specs:
            if not self.is_smart_tool_allowed(tool_id):
                continue
            action = tools_menu.addAction(
                label, lambda _checked=False, name=tool_id: self.launch_smart_tool(name)
            )
            action.setIcon(self.style().standardIcon(icon_type))
        if self.is_smart_tool_allowed("smart_review"):
            smart_review_action = tools_menu.addAction("Smart Review", self.launch_current_project_smart_review)
            smart_review_action.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.usdview_action = None
        if self.is_smart_tool_allowed("usdview"):
            self.usdview_action = tools_menu.addAction("Open USD in usdview", self.launch_current_project_usdview)
            self.usdview_action.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogContentsView))
            self.usdview_action.setEnabled(False)
        
        if self.is_vendor_studio():
            studio_action = file_menu.addAction(
                "Studio Settings...", self.open_config_creator
            )
            studio_action.setIcon(
                self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogInfoView)
            )
            import_action = file_menu.addAction(
                "Import Project Config...", self.import_project_config_archive
            )
            import_action.setIcon(
                self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogOpenButton)
            )
        else:
            new_action = file_menu.addAction("New Project", self.open_config_creator)
            new_action.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileIcon))
            new_action.setShortcut("Ctrl+N")
            export_action = file_menu.addAction(
                "Export Current Project Config...", self.export_current_project_config
            )
            export_action.setIcon(
                self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton)
            )
        
        # 2. Run Pipeline Setup
        # 直接 self.run_pipeline_setup を指定します。
        # すでに内部でプロジェクト名の取得などの処理が含まれているため、これで動作します。
        setup_action = file_menu.addAction("Run Pipeline Setup", self.run_pipeline_setup)
        setup_action.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
        setup_action.setToolTip("Execute init_project.py for the current project")
        setup_action.setShortcut("Ctrl+R")
        
        file_menu.addSeparator()
        
        if not self.is_vendor_studio():
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

def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    launcher = SmartLauncher()
    launcher.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
