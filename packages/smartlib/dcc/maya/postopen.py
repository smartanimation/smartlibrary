"""Ordered project scripts after successful interactive Maya scene opens."""
from __future__ import annotations

import os
import runpy
import traceback

from smartlib.core.config_loader import current_project_config, deep_merge, default_config_dir, load_config
from smartlib.core.path_resolver import configured_project_paths

_job = None
_running = False


def execute(project_config, registration_id, file_path):
    """Run only registered files; stop on the first error without undoing the open."""
    if not registration_id or not registration_id.replace("-", "_").isidentifier():
        raise ValueError("Invalid software registration")
    settings = project_config.load(f"software_{registration_id}.yml")
    source = settings.get("source_software") or registration_id
    if source != registration_id:
        if not source.replace("-", "_").isidentifier():
            raise ValueError("Invalid source software registration")
        settings = deep_merge(load_config(default_config_dir() / f"software_{source}.yml"), settings)
    scripts = settings.get("postopen_scripts", [])
    if not isinstance(scripts, list) or any(not isinstance(s, str) for s in scripts):
        raise ValueError("postopen_scripts must be an ordered list of relative Python paths")
    if not scripts:
        return []
    root = project_config.project_root
    if root is None:
        raise ValueError("Project root is not configured")
    paths = configured_project_paths(root, project_config)
    context = {
        "project_id": project_config.project_name,
        "project_root": str(paths.project_root.resolve()),
        "config_dir": str(project_config.config_dir.resolve()),
        "file_path": str(file_path),
        "software": "maya",
        "software_id": registration_id,
        "event": "postopen",
    }
    # Validate the whole list before executing any script.
    resolved = [paths.software_postopen_script("maya", ref) for ref in scripts]
    for path in resolved:
        if not path.is_file():
            raise FileNotFoundError(f"Postopen script not found: {path}")
    completed = []
    for path in resolved:
        try:
            script_context = dict(context)
            namespace = runpy.run_path(str(path), init_globals={"context": script_context},
                                       run_name="__smartpipeline_postopen__")
            if "run" in namespace:
                namespace["run"](script_context)
        except Exception as exc:
            raise RuntimeError(f"Postopen failed: {path}: {exc}") from exc
        completed.append(str(path))
    return completed


def on_scene_opened(*_args):
    global _running
    if _running:
        return
    import maya.cmds as cmds
    config = current_project_config()
    registration = os.environ.get("SMART_SOFTWARE_ID")
    if config is None or not registration:
        return
    scene = cmds.file(query=True, sceneName=True)
    if not scene:
        return
    _running = True
    try:
        execute(config, registration, scene)
    except Exception as exc:
        traceback.print_exc()
        cmds.warning(str(exc))
    finally:
        _running = False


def install():
    """Idempotent; userSetup and launcher bootstrap may both install this."""
    global _job
    import maya.cmds as cmds
    if cmds.about(batch=True):
        return None
    if _job is not None and cmds.scriptJob(exists=_job):
        return _job
    _job = cmds.scriptJob(event=["SceneOpened", on_scene_opened], protected=True)
    return _job
