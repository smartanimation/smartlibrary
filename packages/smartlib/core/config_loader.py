from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        return _load_simple_yaml(path)

    with path.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    """Small YAML fallback for project configs when PyYAML is unavailable."""

    data: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, data)]

    with path.open("r", encoding="utf-8") as stream:
        for raw_line in stream:
            line = raw_line.split("#", 1)[0].rstrip()
            if not line.strip():
                continue

            indent = len(line) - len(line.lstrip(" "))
            stripped = line.strip()

            is_list_item = stripped.startswith("- ")
            while stack and (indent < stack[-1][0] if is_list_item else indent <= stack[-1][0]):
                stack.pop()
            parent = stack[-1][1]

            if is_list_item:
                if isinstance(parent, list):
                    parent.append(_parse_scalar(stripped[2:].strip()))
                continue

            if ":" not in stripped or not isinstance(parent, dict):
                continue

            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value:
                parent[key] = _parse_scalar(value)
                continue

            next_container: Any = [] if key.endswith("_depts") else {}
            parent[key] = next_container
            stack.append((indent, next_container))

    return data


def _parse_scalar(value: str) -> Any:
    if value == "":
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.isdigit():
        return int(value)
    return value


def load_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a YAML or JSON config file."""

    config_path = Path(path)
    if not config_path.exists():
        return {}
    suffix = config_path.suffix.lower()
    if suffix in {".yml", ".yaml"}:
        return _load_yaml(config_path)
    if suffix == ".json":
        with config_path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    raise ValueError(f"Unsupported config extension: {config_path.suffix}")


class ProjectConfig:
    """Small project config facade used by tools before larger services exist."""

    def __init__(self, config_dir: str | os.PathLike[str]):
        self.config_dir = Path(config_dir)

    def load(self, name: str) -> dict[str, Any]:
        return load_config(self.config_dir / name)

    @property
    def base(self) -> dict[str, Any]:
        return self.load("templates_base.yml")

    @property
    def project_root(self) -> Path | None:
        root = (self.base.get("anchors") or {}).get("project_root")
        return Path(root) if root else None

    @property
    def project_name(self) -> str:
        return (self.base.get("anchors") or {}).get("project_name", self.config_dir.name)


def current_project_config() -> ProjectConfig | None:
    config_dir = os.environ.get("PROJECT_CONFIG_DIR")
    return ProjectConfig(config_dir) if config_dir else None
