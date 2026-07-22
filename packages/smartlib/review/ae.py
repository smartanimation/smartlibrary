from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig, load_config
from smartlib.core.metadata import read_json, write_json

_AE_SCRIPT_RUN_DELAY_SECONDS = 6.0


@dataclass(frozen=True)
class AETemplateResult:
    source_template: Path | None
    copied_to: Path
    template_used_json: Path
    candidates: list[Path]


@dataclass(frozen=True)
class AEBuildResult:
    manifest: Path
    script: Path
    project: Path
    log: Path
    launched: bool = False
    message: str = ""


def ae_template_candidates(
    shot_root: str | Path,
    project_root: str | Path,
    pipeline_root: str | Path,
    department: str,
) -> list[Path]:
    shot_root = Path(shot_root)
    project_root = Path(project_root)
    pipeline_root = Path(pipeline_root)
    names = [f"review_{department}.aep", "review_custom.aep", "review_base.aep"]
    candidates = []

    shot_template_root = shot_root / "review" / "templates"
    for name in (f"review_{department}.aep", "review_custom.aep"):
        candidates.append(shot_template_root / name)

    project_template_root = project_root / "settings" / "templates" / "ae" / "review"
    for name in (f"review_{department}.aep", "review_base.aep"):
        candidates.append(project_template_root / name)

    pipeline_template_root = pipeline_root / "templates" / "ae" / "review"
    for name in names:
        candidates.append(pipeline_template_root / name)

    return candidates


def resolve_ae_template(
    shot_root: str | Path,
    project_root: str | Path,
    pipeline_root: str | Path,
    department: str,
) -> tuple[Path | None, list[Path]]:
    candidates = ae_template_candidates(shot_root, project_root, pipeline_root, department)
    for path in candidates:
        if path.exists():
            return path, candidates
    return None, candidates


def copy_ae_template_to_publish(
    version_dir: str | Path,
    shot_root: str | Path,
    project_root: str | Path,
    pipeline_root: str | Path,
    department: str,
    *,
    template_project_path: str | Path | None = None,
    template_used_json_path: str | Path | None = None,
) -> AETemplateResult:
    version_dir = Path(version_dir)
    target = Path(template_project_path) if template_project_path else version_dir / "ae" / "review_project.aep"
    template_used_json = Path(template_used_json_path) if template_used_json_path else version_dir / "ae" / "template_used.json"
    source, candidates = resolve_ae_template(shot_root, project_root, pipeline_root, department)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source:
        shutil.copy2(source, target)
    write_json(
        template_used_json,
        {
            "department": department,
            "source_template": str(source) if source else "",
            "copied_to": "ae/review_project.aep",
            "status": "copied" if source else "missing_template",
            "candidates": [str(path) for path in candidates],
        },
    )
    return AETemplateResult(
        source_template=source,
        copied_to=target,
        template_used_json=template_used_json,
        candidates=candidates,
    )


def prepare_review_ae_build(
    *,
    publish_root: str | Path,
    slots: list[dict[str, Any]],
    project_config: ProjectConfig,
    shot_root: str | Path,
    department: str,
    stage: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    manifest_path: str | Path | None = None,
    script_path: str | Path | None = None,
    log_path: str | Path | None = None,
    template_project_path: str | Path | None = None,
    template_used_json_path: str | Path | None = None,
    update_review_json: bool = True,
) -> AEBuildResult:
    publish_root = Path(publish_root)
    pipeline_root = project_config.config_dir.parent.parent
    template = copy_ae_template_to_publish(
        publish_root,
        shot_root,
        project_config.project_root or pipeline_root,
        pipeline_root,
        department,
        template_project_path=template_project_path,
        template_used_json_path=template_used_json_path,
    )
    stage_data = _stage_data(slots, stage)
    launch_context = _normalize_ae_context(project_config, shot_root, slots, context)
    manifest_path = Path(manifest_path) if manifest_path else publish_root / "ae" / "data" / "review_build.json"
    log_path = Path(log_path) if log_path else publish_root / "ae" / "data" / "build_review.log"
    script_path = Path(script_path) if script_path else publish_root / "ae" / "scripts" / "build_review.jsx"
    manifest = {
        "schema": "smart_render_ae_build",
        "version": 1,
        "package_root": publish_root.as_posix(),
        "review_json": "metadata/review.json",
        "template_project": _relative_to(publish_root, template.copied_to),
        "template_source": str(template.source_template) if template.source_template else "",
        "template_comp": "review_base.comp",
        "auto_save": False,
        "project": launch_context.get("project", ""),
        "projectRoot": launch_context.get("projectRoot", ""),
        "configDir": launch_context.get("configDir", ""),
        "episode": launch_context.get("episode", ""),
        "sequence": launch_context.get("sequence", ""),
        "shot": launch_context.get("shot", ""),
        "log": _relative_to(publish_root, log_path),
        "stage": stage_data,
        "layers": [_ae_layer_row(publish_root, row, stage_data) for row in slots],
        "slate": _ae_slate_row(publish_root, slots, stage_data),
        "script": _relative_to(publish_root, script_path),
    }
    write_json(manifest_path, manifest)
    _write_initial_build_log(log_path, manifest_path, script_path, template.copied_to)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_review_build_jsx(manifest_path), encoding="utf-8")
    if update_review_json:
        _update_review_json_for_ae(publish_root, manifest, template, manifest_path)
    return AEBuildResult(manifest=manifest_path, script=script_path, project=template.copied_to, log=log_path)


def launch_after_effects_build(manifest_path: str | Path, project_config: ProjectConfig) -> AEBuildResult:
    manifest_path = Path(manifest_path)
    data = read_json(manifest_path, default={}) or {}
    publish_root = Path(str(data.get("package_root") or manifest_path.parents[2]))
    script = publish_root / str(data.get("script") or "ae/scripts/build_review.jsx")
    project = publish_root / str(data.get("template_project") or "ae/review_project.aep")
    log = publish_root / str(data.get("log") or "ae/data/build_review.log")
    executable = find_after_effects_executable(project_config)
    launch_context = _normalize_ae_context(project_config, publish_root, [], data)
    launch_context.update({"source": "smart_render", "manifest": str(manifest_path), "publishRoot": str(publish_root)})
    if not executable:
        _append_build_log(log, "After Effects executable was not found.")
        return AEBuildResult(manifest=manifest_path, script=script, project=project, log=log, launched=False, message="After Effects executable was not found.")
    if not script.exists():
        _append_build_log(log, f"AE build script was not found: {script}")
        return AEBuildResult(manifest=manifest_path, script=script, project=project, log=log, launched=False, message=f"AE build script was not found: {script}")
    try:
        command = [executable]
        if project.exists():
            command.append(str(project))
        _append_build_log(log, f"Opening After Effects: {' '.join(command)}")
        env = os.environ.copy()
        _apply_ae_context_env(env, launch_context)
        _write_ae_browser_context(launch_context)
        subprocess.Popen(command, cwd=str(publish_root), env=env)
        _schedule_after_effects_script(executable, script, publish_root, log, env)
    except Exception as exc:
        _append_build_log(log, f"Launch failed: {exc}")
        return AEBuildResult(manifest=manifest_path, script=script, project=project, log=log, launched=False, message=str(exc))
    return AEBuildResult(manifest=manifest_path, script=script, project=project, log=log, launched=True, message=str(project))


def find_after_effects_executable(project_config: ProjectConfig | None = None) -> str:
    import os
    import shutil as _shutil

    for env_name in ("AFTER_EFFECTS_PATH", "AFTERFX_PATH", "SMARTLIB_AFTER_EFFECTS"):
        value = os.environ.get(env_name)
        if value and Path(value).exists():
            return str(Path(value))
    if project_config is not None:
        for path in _after_effects_config_paths(project_config):
            data = load_config(path)
            value = str(data.get("path") or "").strip()
            if value and Path(value).exists():
                return str(Path(value))
    found = _shutil.which("AfterFX.exe") or _shutil.which("AfterFX")
    return found or ""


def _after_effects_config_paths(project_config: ProjectConfig) -> list[Path]:
    pipeline_root = project_config.config_dir.parent.parent
    paths = []
    paths.extend(sorted(project_config.config_dir.glob("software_AfterEffects*.yml"), reverse=True))
    paths.extend(sorted((pipeline_root / "config" / "default").glob("software_AfterEffects*.yml"), reverse=True))
    return paths


def _normalize_ae_context(project_config: ProjectConfig, shot_root: str | Path, slots: list[dict[str, Any]], override: dict[str, Any] | None = None) -> dict[str, str]:
    override = override or {}
    first = slots[0] if slots else {}
    project_root = project_config.project_root
    episode = str(override.get("episode") or first.get("episode") or "")
    sequence = str(override.get("sequence") or first.get("sequence") or "")
    shot = str(override.get("shot") or first.get("shot") or "")

    if not (episode and sequence and shot) and project_root:
        try:
            relative = Path(shot_root).resolve().relative_to((project_root / "shots").resolve())
            if len(relative.parts) >= 3:
                episode = episode or relative.parts[0]
                sequence = sequence or relative.parts[1]
                shot = shot or relative.parts[2]
        except Exception:
            pass

    return {
        "source": str(override.get("source") or "smart_render"),
        "project": str(override.get("project") or override.get("projectName") or project_config.project_name),
        "projectRoot": str(override.get("projectRoot") or project_root or ""),
        "configDir": str(override.get("configDir") or project_config.config_dir),
        "episode": episode,
        "sequence": sequence,
        "shot": shot,
    }


def _apply_ae_context_env(env: dict[str, str], context: dict[str, str]) -> None:
    config_dir = str(context.get("configDir") or "")
    project = str(context.get("project") or "")
    project_root = str(context.get("projectRoot") or "")
    env["PROJECT_CONFIG_DIR"] = config_dir
    env["SMART_PROJECT_CONFIG_DIR"] = config_dir
    env["SMART_PROJECT"] = project
    env["PROJECT_NAME"] = project
    env["SMART_PROJECT_ROOT"] = project_root
    env["PROJECT_ROOT"] = project_root
    env["SMART_CONTEXT_SOURCE"] = str(context.get("source") or "")
    env["SMART_EPISODE"] = str(context.get("episode") or "")
    env["SMART_SEQUENCE"] = str(context.get("sequence") or "")
    env["SMART_SHOT"] = str(context.get("shot") or "")
    if context.get("manifest"):
        env["SMART_AE_MANIFEST"] = str(context.get("manifest") or "")


def _write_ae_browser_context(context: dict[str, str]) -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    path = Path(appdata) / "smartuserdata" / "smart_ae_browser_context.json"
    payload = dict(context)
    payload["context_path"] = str(path)
    try:
        write_json(path, payload)
    except Exception:
        return None
    return path


def _write_initial_build_log(log_path: Path, manifest_path: Path, script_path: Path, project_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                "Smart Render AE build prepared.",
                f"manifest: {manifest_path}",
                f"script: {script_path}",
                f"project: {project_path}",
                "Waiting for After Effects launch.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _append_build_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(f"{message}\n")


def _schedule_after_effects_script(executable: str, script: Path, publish_root: Path, log: Path, env: dict[str, str] | None = None) -> None:
    delay = max(0.0, float(_AE_SCRIPT_RUN_DELAY_SECONDS))

    def run_script() -> None:
        if delay:
            time.sleep(delay)
        try:
            _append_build_log(log, f"Running AE build script: {executable} -r {script}")
            subprocess.Popen([executable, "-r", str(script)], cwd=str(publish_root), env=env)
        except Exception as exc:
            _append_build_log(log, f"Script launch failed: {exc}")

    if delay:
        thread = threading.Thread(target=run_script, name="SmartRenderAEBuild", daemon=True)
        thread.start()
    else:
        run_script()


def _stage_data(slots: list[dict[str, Any]], override: dict[str, Any] | None = None) -> dict[str, Any]:
    override = override or {}
    first = slots[0] if slots else {}
    frame_range = list(override.get("frame_range") or first.get("frame_range") or [1, 1])
    if len(frame_range) < 2:
        frame_range = [frame_range[0] if frame_range else 1, frame_range[0] if frame_range else 1]
    width = int(override.get("width") or first.get("width") or 1920)
    height = int(override.get("height") or first.get("height") or 1080)
    fps = float(override.get("fps") or first.get("fps") or 24)
    start = int(frame_range[0])
    end = int(frame_range[1])
    return {
        "comp_name": str(override.get("comp_name") or "stage"),
        "template_comp": str(override.get("template_comp") or "review_base.comp"),
        "width": width,
        "height": height,
        "fps": fps,
        "start_frame": start,
        "end_frame": end,
        "duration_frames": max(1, end - start + 1),
        "duration_seconds": max(1, end - start + 1) / fps if fps else 1,
    }


def _ae_layer_row(publish_root: Path, row: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
    start = int(row.get("start_frame") or stage.get("start_frame") or 1)
    end = int(row.get("end_frame") or stage.get("end_frame") or start)
    sequence = str(row.get("image_sequence") or "")
    return {
        "slot": int(row.get("slot") or 0),
        "layer": str(row.get("layer") or row.get("output") or ""),
        "precomp": str(row.get("layer") or row.get("output") or ""),
        "image_sequence": sequence,
        "first_frame_file": _first_frame_file(publish_root, sequence, start),
        "start_frame": start,
        "end_frame": end,
        "duration_frames": max(1, end - start + 1),
        "width": int(row.get("width") or stage.get("width") or 1920),
        "height": int(row.get("height") or stage.get("height") or 1080),
        "fps": float(row.get("fps") or stage.get("fps") or 24),
    }


def _ae_slate_row(publish_root: Path, rows: list[dict[str, Any]], stage: dict[str, Any]) -> dict[str, Any]:
    for row in rows:
        sequence = str(row.get("slate_sequence") or "")
        if not sequence:
            continue
        start = int(row.get("start_frame") or stage.get("start_frame") or 1)
        end = int(row.get("end_frame") or stage.get("end_frame") or start)
        return {
            "layer": "Slate",
            "image_sequence": sequence,
            "first_frame_file": _first_frame_file(publish_root, sequence, start),
            "start_frame": start,
            "end_frame": end,
            "duration_frames": max(1, end - start + 1),
            "width": int(row.get("width") or stage.get("width") or 1920),
            "height": int(row.get("height") or stage.get("height") or 1080),
            "fps": float(row.get("fps") or stage.get("fps") or 24),
        }
    return {}


def _first_frame_file(publish_root: Path, sequence: str, start_frame: int) -> str:
    frame = f"{int(start_frame):04d}"
    path = sequence.replace("####", frame)
    return (publish_root / path).resolve(strict=False).as_posix()


def _update_review_json_for_ae(publish_root: Path, manifest: dict[str, Any], template: AETemplateResult, manifest_path: Path) -> None:
    review_path = publish_root / "metadata" / "review.json"
    data = read_json(review_path, default={}) or {}
    if not isinstance(data, dict):
        data = {}
    ae = data.setdefault("ae", {})
    ae.update(
        {
            "project": manifest["template_project"],
            "build_manifest": _relative_to(publish_root, manifest_path),
            "build_script": manifest["script"],
            "template_used": _relative_to(publish_root, template.template_used_json),
            "layer_order": [row.get("layer", "") for row in manifest.get("layers", [])],
            "slate": (manifest.get("slate") or {}).get("image_sequence", ""),
        }
    )
    write_json(review_path, data)


def _relative_to(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except Exception:
        return path.as_posix()


def _review_build_jsx(manifest_path: Path) -> str:
    manifest = manifest_path.as_posix()
    return f"""(function () {{
    if (typeof JSON === "undefined") {{
        JSON = {{}};
    }}
    if (!JSON.parse) {{
        JSON.parse = function (text) {{
            return eval("(" + text + ")");
        }}; 
    }}
    var manifestFile = new File("{manifest}");
    var logFile = null;
    var undoOpen = false;
    function log(message) {{
        try {{
            if (!logFile) {{
                return;
            }}
            logFile.open("a");
            logFile.writeln(new Date().toString() + " " + message);
            logFile.close();
        }} catch (err) {{}}
    }}
    function fail(message) {{
        log("ERROR " + message);
        alert(message);
    }}
    if (!manifestFile.exists) {{
        alert("AE build manifest was not found: " + manifestFile.fsName);
        return;
    }}
    manifestFile.open("r");
    var raw = manifestFile.read();
    manifestFile.close();
    var data = JSON.parse(raw);
    var root = new Folder(data.package_root);
    logFile = new File(root.fsName + "/" + (data.log || "ae/data/build_review.log"));
    try {{
        var logFolder = logFile.parent;
        if (!logFolder.exists) {{
            logFolder.create();
        }}
    }} catch (err) {{}}
    log("Smart Render AE build started in After Effects.");
    var projectFile = new File(root.fsName + "/" + data.template_project);
    try {{
        app.beginUndoGroup("Smart Render AE Build");
        undoOpen = true;
        if (projectFile.exists) {{
            log("Opening project: " + projectFile.fsName);
            app.open(projectFile);
        }} else if (!app.project) {{
            log("Creating new project.");
            app.newProject();
        }}
        var stage = ensureStageComp(data.stage);
        try {{
            stage.displayStartFrame = data.stage.start_frame;
        }} catch (err) {{
            log("displayStartFrame skipped: " + err.toString());
        }}
        clearComp(stage);
        for (var i = 0; i < data.layers.length; i++) {{
            var row = data.layers[i];
            log("Importing layer: " + row.layer + " from " + row.first_frame_file);
            var footage = importSequence(row);
            if (!footage) {{
                log("Skipped missing layer: " + row.layer);
                continue;
            }}
            var precompName = row.precomp || row.layer;
            if (precompName === stage.name) {{
                precompName = precompName + "_precomp";
            }}
            var precomp = ensureComp(precompName, data.stage.width, data.stage.height, data.stage.duration_seconds, data.stage.fps);
            clearComp(precomp);
            var sourceLayer = precomp.layers.add(footage);
            sourceLayer.startTime = 0;
            var stageLayer = stage.layers.add(precomp);
            stageLayer.name = row.layer;
            stageLayer.startTime = 0;
        }}
        addSlateToStage(stage, data.slate);
        if (data.auto_save === true) {{
            log("Saving project: " + projectFile.fsName);
            app.project.save(projectFile);
        }} else {{
            log("Auto save disabled; project left open for inspection.");
        }}
        try {{
            stage.openInViewer();
            app.project.activeItem = stage;
            app.activate();
        }} catch (err) {{
            log("Activate skipped: " + err.toString());
        }}
        log("Smart Render AE build finished.");
    }} catch (err) {{
        fail("Smart Render AE build failed: " + err.toString());
    }} finally {{
        if (undoOpen) {{
            try {{
                app.endUndoGroup();
            }} catch (err) {{}}
        }}
    }}

    function ensureComp(name, width, height, duration, fps) {{
        var comp = findComp(name);
        if (comp) {{
            try {{ comp.width = width; }} catch (err) {{}}
            try {{ comp.height = height; }} catch (err) {{}}
            comp.duration = duration;
            comp.frameRate = fps;
            return comp;
        }}
        return app.project.items.addComp(name, width, height, 1.0, duration, fps);
    }}

    function ensureStageComp(stageData) {{
        var comp = findComp(stageData.comp_name);
        if (!comp) {{
            comp = findComp(stageData.template_comp) || findComp("review_base");
        }}
        if (comp) {{
            comp.name = stageData.comp_name;
            try {{ comp.width = stageData.width; }} catch (err) {{}}
            try {{ comp.height = stageData.height; }} catch (err) {{}}
            comp.duration = stageData.duration_seconds;
            comp.frameRate = stageData.fps;
            return comp;
        }}
        return app.project.items.addComp(stageData.comp_name, stageData.width, stageData.height, 1.0, stageData.duration_seconds, stageData.fps);
    }}

    function findComp(name) {{
        for (var i = 1; i <= app.project.numItems; i++) {{
            var item = app.project.item(i);
            if (item instanceof CompItem && item.name === name) {{
                return item;
            }}
        }}
        return null;
    }}

    function clearComp(comp) {{
        try {{
            while (comp.numLayers > 0) {{
                comp.layer(1).remove();
            }}
        }} catch (err) {{
            log("clearComp failed for " + comp.name + ": " + err.toString());
        }}
    }}

    function addSlateToStage(stage, slateRow) {{
        if (!slateRow || !slateRow.first_frame_file) {{
            log("Slate sequence is not configured.");
            return;
        }}
        log("Importing slate: " + slateRow.first_frame_file);
        var footage = importSequence(slateRow);
        if (!footage) {{
            log("Skipped missing slate.");
            return;
        }}
        var slateLayer = stage.layers.add(footage);
        slateLayer.name = slateRow.layer || "Slate";
        slateLayer.startTime = 0;
    }}

    function importSequence(row) {{
        var file = new File(row.first_frame_file);
        if (!file.exists) {{
            log("First frame file not found: " + file.fsName);
            return null;
        }}
        var options = new ImportOptions(file);
        options.sequence = true;
        options.forceAlphabetical = true;
        var footage = app.project.importFile(options);
        footage.name = row.layer;
        try {{ footage.mainSource.conformFrameRate = row.fps; }} catch (err) {{}}
        return footage;
    }}
}}());
"""
