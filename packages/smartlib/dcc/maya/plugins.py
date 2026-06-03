from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import load_config


DEFAULT_REQUIRED_PLUGINS = ["mayaUsdPlugin"]


def maya_plugin_config(config_dir: str | os.PathLike[str] | None = None) -> dict[str, list[str]]:
    cfg_dir = Path(config_dir or os.environ.get("PROJECT_CONFIG_DIR") or "")
    data = load_config(cfg_dir / "tools.yml") if cfg_dir else {}
    plugins = (((data.get("dcc") or {}).get("maya") or {}).get("plugins") or {})
    required = plugins.get("required")
    optional = plugins.get("optional")
    return {
        "required": [str(item) for item in (required if isinstance(required, list) else DEFAULT_REQUIRED_PLUGINS)],
        "optional": [str(item) for item in (optional if isinstance(optional, list) else [])],
    }


def ensure_plugins(
    cmds: Any,
    plugin_names: list[str] | tuple[str, ...],
    *,
    required: bool = True,
) -> dict[str, Any]:
    loaded: list[str] = []
    failed: dict[str, str] = {}
    for plugin in plugin_names:
        name = str(plugin).strip()
        if not name:
            continue
        try:
            if cmds.pluginInfo(name, query=True, loaded=True):
                loaded.append(name)
                continue
        except Exception:
            pass
        try:
            cmds.loadPlugin(name, quiet=True)
            loaded.append(name)
        except Exception as exc:
            failed[name] = str(exc)
    if required and failed:
        messages = ", ".join(f"{name}: {error}" for name, error in failed.items())
        raise RuntimeError(f"Required Maya plugin load failed: {messages}")
    return {"loaded": loaded, "failed": failed}


def ensure_required_plugins(cmds: Any, *, config_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    config = maya_plugin_config(config_dir)
    return ensure_plugins(cmds, config["required"], required=True)


def ensure_optional_plugins(cmds: Any, *, config_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    config = maya_plugin_config(config_dir)
    return ensure_plugins(cmds, config["optional"], required=False)
