"""Shared Maya worker runtime resolution for Build and DCC background jobs."""
from __future__ import annotations

import os
from pathlib import Path
import re

from .config_loader import expand_config_tokens


def software_config_name(project_config) -> str:
    available = {
        path.stem.removeprefix("software_"): path
        for path in project_config.config_dir.glob("software_maya*.yml")
    }
    base = project_config.load("templates_base.yml") or {}
    enabled = [str(value) for value in (base.get("enabled_softwares") or [])]
    candidates = [available[name] for name in enabled if name in available]
    if not candidates:
        candidates = list(available.values())
    configured = str((base.get("review_build") or {}).get("maya_software") or "").strip()
    configured_path = available.get(configured)
    if configured_path and (not enabled or configured in enabled):
        return configured_path.name
    for path in reversed(candidates):
        data = project_config.load(path.name) or {}
        if data.get("review_build_worker") or data.get("build_worker"):
            return path.name
    for path in reversed(candidates):
        data = project_config.load(path.name) or {}
        profiles = data.get("plugin_profiles") or {}
        if any((profile or {}).get("required") for profile in profiles.values()):
            return path.name
    preferred = project_config.config_dir / "software_maya2024.yml"
    if preferred.is_file():
        return preferred.name
    return candidates[-1].name if candidates else "software_maya2024.yml"


def resolve_mayapy(project_config) -> Path:
    configured = os.environ.get("SMARTPIPELINE_MAYAPY")
    if configured and Path(configured).is_file():
        return Path(configured)
    selected = project_config.config_dir / software_config_name(project_config)
    candidates = [selected]
    candidates.extend(path for path in sorted(project_config.config_dir.glob("software_maya*.yml"))
                      if path not in candidates)
    for config_path in candidates:
        data = project_config.load(config_path.name) or {}
        maya_path = Path(str(data.get("path") or ""))
        if maya_path.suffix.lower() in {".bat", ".cmd"} and maya_path.is_file():
            text = maya_path.read_text(encoding="utf-8-sig", errors="ignore")
            match = re.search(r"^\s*set\s+MAYAINSTPATH\s*=\s*(.+?)\s*$", text,
                              flags=re.IGNORECASE | re.MULTILINE)
            if match:
                mayapy = Path(match.group(1).strip().strip('"')) / "bin" / "mayapy.exe"
                if mayapy.is_file():
                    return mayapy
        mayapy = maya_path.with_name("mayapy.exe")
        if mayapy.is_file():
            return mayapy
    raise FileNotFoundError(
        "mayapy.exe was not resolved. Set SMARTPIPELINE_MAYAPY or configure software_maya*.yml."
    )


def software_config(project_config) -> dict:
    return project_config.load(software_config_name(project_config)) or {}


def process_environment(project_config) -> tuple[dict[str, str], dict[str, list[str]]]:
    return environment_from_config(software_config(project_config), project_config)


def environment_from_config(config, project_config) -> tuple[dict[str, str], dict[str, list[str]]]:
    configured_env = {str(key): value for key, value in config.items()
                      if str(key).upper().startswith("MAYA_")}
    configured_env.update(config.get("env_vars") or {})
    env_vars = {str(key): expand_config_tokens(str(value), project_config)
                for key, value in configured_env.items() if str(key).strip()}
    paths = {}
    for key, values in (config.get("paths") or {}).items():
        if isinstance(values, str):
            text = values.strip()
            values = [] if text in {"", "[]", "null", "None"} else [text]
        paths[str(key)] = [expand_config_tokens(str(value), project_config)
                           for value in (values or []) if str(value).strip()]
    return env_vars, paths


def validate_worker_version(mayapy, authoring_version):
    authoring = _major_version(authoring_version)
    worker = _major_version(str(Path(mayapy).parent.parent))
    if authoring and worker and worker < authoring:
        raise ValueError(
            f"Configured Review Build Maya {worker} is older than authoring Maya {authoring}."
        )


def _major_version(value):
    match = re.search(r"(?<!\d)(20\d{2})(?!\d)", str(value or ""))
    return int(match.group(1)) if match else None
