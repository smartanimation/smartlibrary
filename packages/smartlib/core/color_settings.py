"""Project color contract. Host applications must report their actual state."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

DEFAULTS = {
    "enabled": False,
    "config": "",
    "working_space": "ACEScg",
    "display": "sRGB - Display",
    "view": "ACES 1.0 - SDR Video",
}


def color_settings(settings):
    raw = settings.get("color", {})
    if not isinstance(raw, dict):
        raise ValueError("Color settings must be a mapping.")
    result = dict(DEFAULTS, **raw)
    if not isinstance(result["enabled"], bool):
        raise ValueError("Color enabled must be a boolean.")
    if result["enabled"]:
        for key in ("config", "working_space", "display", "view"):
            if not isinstance(result[key], str) or not result[key].strip():
                raise ValueError(f"Color {key} must be specified.")
        if result["working_space"] != "ACEScg":
            raise ValueError("Color working space must be ACEScg.")
    result["validation_hosts"] = validation_hosts(result.get("validation_hosts", ["Maya", "Nuke", "RV"]))
    return result


def config_fingerprint(path):
    """Fingerprint a self-contained config; reject untracked LUT dependencies."""
    path = Path(path).resolve(strict=True)
    content = path.read_bytes()
    if b"!<FileTransform>" in content:
        raise ValueError("External FileTransform bundles are not supported by this validation version.")
    return hashlib.sha256(content).hexdigest()


def config_profile(path):
    match = re.search(r"^ocio_profile_version:\s*([0-9]+)\.([0-9]+)", Path(path).read_text(encoding="utf-8-sig"), re.M)
    if not match:
        raise ValueError("Missing OCIO profile version.")
    return tuple(map(int, match.groups()))


def require_compatible(path, sdk_version):
    match = re.search(r"(\d+)\.(\d+)", str(sdk_version))
    if not match:
        raise ValueError(f"Unknown OCIO SDK version: {sdk_version}")
    if config_profile(path) > tuple(map(int, match.groups())):
        raise ValueError(f"OCIO config {config_profile(path)} requires a newer SDK than {sdk_version}")


def project_color_contract(project):
    from smartlib.core.path_resolver import configured_project_paths
    settings = color_settings(project.load("project_settings.yml"))
    if not settings["enabled"]:
        return None
    paths = configured_project_paths(project.project_root or project.config_dir, project)
    path = paths.color_config_file(project.config_dir, settings["config"])
    return dict(settings, config=str(path), config_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bundle_sha256=config_fingerprint(path), profile=list(config_profile(path)))


def apply_color_environment(env, project):
    """Apply after software overrides; unconfigured projects retain legacy policy."""
    import json
    contract = project_color_contract(project)
    env.pop("SMARTPIPELINE_COLOR_CONTRACT", None)
    if contract is None:
        return
    env["OCIO"] = contract["config"]
    env["OCIO_ACTIVE_DISPLAYS"] = contract["display"]
    env["OCIO_ACTIVE_VIEWS"] = contract["view"]
    env["SMARTPIPELINE_COLOR_CONTRACT"] = json.dumps(contract)


def environment_contract():
    import json
    import os
    text = os.environ.get("SMARTPIPELINE_COLOR_CONTRACT")
    return json.loads(text) if text else None


def validation_hosts(hosts):
    if not isinstance(hosts, (list, tuple)) or not hosts or any(not isinstance(h, str) or h not in {"Maya", "Nuke", "RV"} for h in hosts) or len(hosts) != len(set(hosts)):
        raise ValueError("Color validation_hosts must be a nonempty unique list of Maya, Nuke, RV.")
    return list(hosts)
