from __future__ import annotations

from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json


def selected_asset_path(project_config: ProjectConfig) -> Path:
    return project_config.config_dir / ".cache" / "selected_asset.json"


def write_selected_asset(project_config: ProjectConfig, asset_data: dict[str, Any]) -> Path:
    return write_json(selected_asset_path(project_config), asset_data)


def read_selected_asset(project_config: ProjectConfig) -> dict[str, Any]:
    data = read_json(selected_asset_path(project_config), {})
    return data if isinstance(data, dict) else {}
