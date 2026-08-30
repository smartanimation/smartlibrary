from __future__ import annotations

import os
from pathlib import Path

from smartlib.core.config_loader import load_config, studio_config_path


CREDENTIAL_ENV_VARS = ("CREDENTIALS_PATH", "GOOGLE_APPLICATION_CREDENTIALS", "CREDENTIALS_DIR")


def _credential_file(value: str | os.PathLike[str]) -> Path:
    path = Path(os.path.expandvars(str(value).strip().strip('"'))).expanduser()
    if path.is_dir():
        path = path / "credentials.json"
    return path


def _project_credentials_path() -> Path | None:
    config_dir_value = (
        os.environ.get("PROJECT_CONFIG_DIR")
        or os.environ.get("SMART_PROJECT_CONFIG_DIR")
        or ""
    )
    if not config_dir_value:
        return None
    config_dir = Path(config_dir_value.strip().strip('"'))
    config_path = config_dir / "templates_base.yml"
    if not config_path.is_file():
        return None
    try:
        data = load_config(config_path)
    except OSError:
        return None
    raw_path = str((data.get("google_sheets") or {}).get("credentials_path") or "").strip()
    if not raw_path:
        return None
    anchors = data.get("anchors") or {}
    project_root = str(anchors.get("project_root") or os.environ.get("PROJECT_ROOT") or "")
    raw_path = raw_path.replace("{project_root}", project_root)
    return _credential_file(raw_path)


def _studio_credentials_path() -> Path | None:
    config_path = studio_config_path()
    if not config_path or not config_path.is_file():
        return None
    try:
        data = load_config(config_path)
    except OSError:
        return None
    raw_path = str((data.get("google_sheets") or {}).get("credentials_path") or "").strip()
    return _credential_file(raw_path) if raw_path else None


def credentials_path() -> Path | None:
    for name in CREDENTIAL_ENV_VARS:
        value = os.environ.get(name)
        if not value:
            continue
        return _credential_file(value)

    configured = _studio_credentials_path() or _project_credentials_path()
    if configured:
        return configured

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "credentials.json"
    return None
