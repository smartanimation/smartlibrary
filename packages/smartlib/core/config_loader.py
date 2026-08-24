from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROJECT_ONLY_CONFIGS = {"templates_base.yml"}
STUDIO_INLINE_CONFIGS = {"tools.yml", "project_settings.yml"}
STUDIO_CONFIG_ENV = "SMARTPIPELINE_STUDIO_CONFIG"
STUDIO_CONFIG_DIR_ENV = "SMARTPIPELINE_STUDIO_CONFIG_DIR"
PIPELINE_ROOT_ENV_VARS = ("SMARTPIPELINE_ROOT", "SMARTLIBRARY_ROOT")


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

    with path.open("r", encoding="utf-8-sig") as stream:
        raw_lines = stream.readlines()
        for line_index, raw_line in enumerate(raw_lines):
            line = _strip_yaml_comment(raw_line).rstrip()
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

            next_is_list = False
            for following_line in raw_lines[line_index + 1 :]:
                following = _strip_yaml_comment(following_line).rstrip()
                if not following.strip():
                    continue
                following_indent = len(following) - len(following.lstrip(" "))
                next_is_list = (
                    following_indent >= indent
                    and following.strip().startswith("- ")
                )
                break
            next_container: Any = [] if key.endswith("_depts") or next_is_list else {}
            parent[key] = next_container
            stack.append((indent, next_container))

    return data


def _strip_yaml_comment(line: str) -> str:
    """Strip YAML comments without treating hashes inside quotes as comments."""

    quote = ""
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {"'", '"'}:
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
            continue
        if char == "#" and not quote:
            return line[:index]
    return line


def _parse_scalar(value: str) -> Any:
    if value == "":
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        contents = value[1:-1].strip()
        if not contents:
            return []
        return [_parse_scalar(item.strip()) for item in contents.split(",")]
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none"}:
        return None
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


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def pipeline_root() -> Path:
    for env_name in PIPELINE_ROOT_ENV_VARS:
        value = os.environ.get(env_name)
        if value:
            return Path(value)
    return Path(__file__).resolve().parents[3]


def default_config_dir() -> Path:
    return pipeline_root() / "config" / "default"


def studio_config_path() -> Path | None:
    value = os.environ.get(STUDIO_CONFIG_ENV)
    if value:
        return Path(value)
    value = os.environ.get(STUDIO_CONFIG_DIR_ENV)
    if value:
        candidate = Path(value) / "studio.yml"
        return candidate if candidate.exists() else None
    return None


def studio_config_dir() -> Path | None:
    value = os.environ.get(STUDIO_CONFIG_DIR_ENV)
    if value:
        return Path(value)
    path = studio_config_path()
    return path.parent if path else None


def smartpipeline_tools_root() -> Path:
    value = os.environ.get("SMARTPIPELINE_TOOLS") or os.environ.get("SMARTTOOLS_ROOT")
    if value:
        return Path(value)
    return pipeline_root().parent / "smarttools"


def expand_config_tokens(value: str | os.PathLike[str], project_config: "ProjectConfig | None" = None) -> str:
    text = str(value)
    project_root = ""
    if project_config is not None:
        try:
            root = project_config.project_root
            project_root = root.as_posix() if root else ""
        except Exception:
            project_root = ""
    replacements = {
        "{smartpipeline_root}": pipeline_root().as_posix(),
        "{SMARTPIPELINE_ROOT}": pipeline_root().as_posix(),
        "{smartlibrary_root}": pipeline_root().as_posix(),
        "{SMARTLIBRARY_ROOT}": pipeline_root().as_posix(),
        "{smartpipeline_tools}": smartpipeline_tools_root().as_posix(),
        "{SMARTPIPELINE_TOOLS}": smartpipeline_tools_root().as_posix(),
        "{smarttools_root}": smartpipeline_tools_root().as_posix(),
        "{SMARTTOOLS_ROOT}": smartpipeline_tools_root().as_posix(),
        "{project_root}": project_root,
        "{PROJECT_ROOT}": project_root,
    }
    for token, replacement in replacements.items():
        text = text.replace(token, replacement)
    return text


class ProjectConfig:
    """Small project config facade used by tools before larger services exist."""

    def __init__(self, config_dir: str | os.PathLike[str]):
        self.config_dir = Path(config_dir)

    def load(self, name: str) -> dict[str, Any]:
        project_path = self.config_dir / name
        if name in PROJECT_ONLY_CONFIGS:
            return load_config(project_path)

        merged = load_config(default_config_dir() / name)

        studio_path = studio_config_path()
        if name in STUDIO_INLINE_CONFIGS and studio_path:
            merged = deep_merge(merged, load_config(studio_path))

        studio_dir = studio_config_dir()
        if studio_dir:
            merged = deep_merge(merged, load_config(studio_dir / name))

        return deep_merge(merged, load_config(project_path))

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

    @property
    def templates(self) -> dict[str, str]:
        """Merged path templates from base and domain-specific config files."""

        merged: dict[str, str] = {}
        for filename in ("templates_base.yml", "templates_assets.yml", "templates_assemblies.yml", "templates_shots.yml"):
            data = self.load(filename)
            templates = data.get("templates") or {}
            if isinstance(templates, dict):
                merged.update({str(key): str(value) for key, value in templates.items()})
        return merged

    @property
    def usd_skel_contract(self) -> dict[str, str]:
        """Return the merged Maya-to-USD skeleton export contract."""

        policy = self.load("preflight.yml").get("preflight") or {}
        configured = policy.get("usd_skel") or {}
        defaults = {
            "geometry_set": "cache_geo_set",
            "skeleton_set": "skel_export_set",
            "root_joint_source": "rig_metadata",
            "root_joint_metadata_key": "root_joint",
            "root_joint_detection": "skin_influence_root",
        }
        defaults.update({str(key): str(value) for key, value in configured.items() if value is not None})
        return defaults


def current_project_config() -> ProjectConfig | None:
    config_dir = os.environ.get("PROJECT_CONFIG_DIR")
    return ProjectConfig(config_dir) if config_dir else None
