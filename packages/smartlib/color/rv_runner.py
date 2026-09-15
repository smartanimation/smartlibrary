"""Run the same RV installation resolved by Smart Launcher."""
import json
import os
import subprocess
from pathlib import Path

from smartlib.core.path_resolver import ProjectPaths, configured_project_paths
from smartlib.color.validation import save_json, sha256


def run(project, manifest):
    from smartlib.apps.launcher.main import (
        apply_project_context_env, apply_pipeline_pythonpath,
        apply_project_color_env, resolve_openrv_executable,
    )
    env = os.environ.copy()
    apply_project_context_env(env, project_name=project.project_name,
                              project_root=str(project.project_root), config_dir=str(project.config_dir))
    # Resolver reads the calling process's studio setting, as in Launcher.
    from smartlib.core.config_loader import STUDIO_CONFIG_ENV
    previous = os.environ.get(STUDIO_CONFIG_ENV)
    try:
        os.environ[STUDIO_CONFIG_ENV] = env[STUDIO_CONFIG_ENV]
        rv = resolve_openrv_executable(str(project.config_dir), str(project.project_root))
    finally:
        if previous is None:
            os.environ.pop(STUDIO_CONFIG_ENV, None)
        else:
            os.environ[STUDIO_CONFIG_ENV] = previous
    if not rv:
        raise ValueError("Smart Launcher could not resolve RV.")
    apply_pipeline_pythonpath(env)
    apply_project_color_env(env, str(project.config_dir), "rv")
    p = manifest["paths"]
    paths = configured_project_paths(project.project_root, project)
    logfile = paths.color_validation_file(manifest["run_id"], "RV.log")
    expression = "from smartlib.dcc.rv.color_validation import capture_session; capture_session(" + repr(p["manifest.json"]) + ")"
    with logfile.open("w", encoding="utf-8") as log:
        subprocess.run([rv, "-pyeval", expression, p["reference.exr"]], env=env,
                       stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
        state = json.loads(Path(p["RV_state.json"]).read_text(encoding="utf-8"))
        if state.get("status") == "failed":
            return state
        rvio = ProjectPaths.rvio_executable(rv)
        state.update(capture_method="RVIO GPU session export", artifacts={})
        try:
            from smartlib.color.images import read_exr
            for source, target in (("reference.exr", "RV_output.exr"), ("color_validation.rv", "RV_display.exr")):
                # Existing images are never accepted as evidence for a failed rerun.
                destination = Path(p[target])
                if destination.exists():
                    raise ValueError("Use a new validation run ID; output already exists: " + target)
                subprocess.run([str(rvio), p[source], "-outformat", "32", "float", "-o", str(destination)],
                               env=env, stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
                read_exr(destination)
                state["artifacts"][target] = sha256(destination)
            state["status"] = "complete"
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            state.update(status="partial", export_error=str(exc))
    state["log"] = str(logfile)
    save_json(p["RV_state.json"], state)
    return state
