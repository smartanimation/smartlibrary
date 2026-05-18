from __future__ import annotations

import os
from pathlib import Path


def prepend_path(env: dict[str, str], key: str, paths: list[str | os.PathLike[str]]) -> dict[str, str]:
    existing = env.get(key, "")
    values = [str(Path(path)) for path in paths if path]
    if existing:
        values.append(existing)
    env[key] = os.pathsep.join(values)
    return env


def build_base_environment(
    smartpipeline_root: str | os.PathLike[str],
    project_config_dir: str | os.PathLike[str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
    extra_pythonpaths: list[str | os.PathLike[str]] | None = None,
) -> dict[str, str]:
    root = Path(smartpipeline_root)
    env = os.environ.copy()
    env["SMARTPIPELINE_ROOT"] = str(root)
    env["SMARTLIBRARY_ROOT"] = str(root)
    if project_config_dir:
        env["PROJECT_CONFIG_DIR"] = str(project_config_dir)
    if project_root:
        env["PROJECT_ROOT"] = str(project_root)
    pythonpaths = [root / "packages", root]
    pythonpaths.extend(extra_pythonpaths or [])
    return prepend_path(env, "PYTHONPATH", pythonpaths)


def build_maya_environment(*args, **kwargs) -> dict[str, str]:
    """Build a Maya-safe environment without injecting standalone Qt bindings."""

    env = build_base_environment(*args, **kwargs)
    return env
