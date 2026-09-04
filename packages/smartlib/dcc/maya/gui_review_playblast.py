"""One isolated GUI Maya per review shot; immutable, file-based render request.

Artifact paths are supplied by the worker's existing resolvers. Local temporary
files below are process transport/preferences, not another pipeline hierarchy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

from smartlib.core.metadata import sidecar_path

SCHEMA = "smartpipeline.gui_review_playblast.v1"
BACKEND_VERSION = "review_builder_v4_gui_playblast"


def _maya_executable():
    # The worker is launched with the configured Maya's mayapy, so use its
    # sibling executable rather than discovering a different installed version.
    return Path(sys.executable).with_name("maya.exe" if os.name == "nt" else "maya")


def _write(path, payload):
    path = Path(path)
    temporary = sidecar_path(path, ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def validate_layers(layers):
    if not layers:
        raise ValueError("GUI Playblast requires at least one layer")
    for layer in layers:
        start, end = layer["frame_range"]
        width, height = layer["resolution"]
        pattern = layer["output_pattern"]
        if end < start or min(width, height) < 1:
            raise ValueError(f"Invalid Playblast range/resolution: {layer['name']}")
        if pattern.count("####") != 1 or Path(pattern).name != pattern:
            raise ValueError(f"Invalid Playblast file pattern: {pattern}")
        if Path(pattern).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("GUI Review Playblast supports PNG/JPEG; export a PNG Render Manifest for this review.")
        if not layer.get("camera"):
            raise ValueError(f"Missing Playblast camera: {layer['name']}")


def launch_playblast(cmds, *, scene, layers, all_layers, status_path,
                     plugins=(), progress=None, startup_timeout=180,
                     stall_timeout=300):
    """Launch only our own Maya, keep logs on failure, and reap it in all cases."""
    if not layers:
        return {}
    validate_layers(layers)
    executable = _maya_executable()
    if not executable.is_file():
        raise FileNotFoundError(f"GUI Maya executable not found: {executable}")
    request_path = sidecar_path(status_path, ".playblast.request.json")
    result_path = sidecar_path(status_path, ".playblast.result.json")
    event_path = sidecar_path(status_path, ".playblast.progress.json")
    log_path = sidecar_path(status_path, ".playblast.log")
    console_path = sidecar_path(status_path, ".playblast.console.log")
    # Export the current constructed state (including reconstructed layers), not
    # the artist's work file. No save/rename or resolver runs in the GUI process.
    scene_types = cmds.file(query=True, type=True) or ["mayaAscii"]
    scene_type = scene_types[0] if isinstance(scene_types, (list, tuple)) else scene_types
    if scene_type not in {"mayaAscii", "mayaBinary"}:
        raise ValueError(f"Unsupported constructed scene format: {scene_type}")
    snapshot_path = sidecar_path(status_path, ".playblast.ma" if scene_type == "mayaAscii" else ".playblast.mb")
    request_path.parent.mkdir(parents=True, exist_ok=True)
    for path in (request_path, result_path, event_path, snapshot_path):
        if path.exists():
            raise FileExistsError(f"Playblast job transport already exists: {path}")
    if progress:
        progress(0, "Prepare constructed scene for GUI Playblast")
    cmds.file(str(snapshot_path), exportAll=True, type=scene_type,
              preserveReferences=True, force=False)
    payload = {"schema": SCHEMA, "source_scene": str(scene),
               "scene": str(snapshot_path), "layers": layers,
               "all_layers": all_layers, "plugins": list(plugins),
               "result": str(result_path), "progress": str(event_path)}
    _write(request_path, payload)
    process = None
    with tempfile.TemporaryDirectory(prefix="smart_review_gui_") as runtime:
        runtime = Path(runtime)
        bootstrap = runtime / "start.mel"
        code = ("import maya.utils; from smartlib.dcc.maya.gui_review_playblast import run_gui; "
                f"maya.utils.executeDeferred(lambda: run_gui({str(request_path)!r}))")
        bootstrap.write_text("python(" + json.dumps(code) + ");\n", encoding="utf-8")
        env = dict(os.environ)
        env["MAYA_APP_DIR"] = str(runtime / "prefs")
        env["MAYA_SKIP_USERSETUP_PY"] = "1"
        env["MAYA_DISABLE_CIP"] = "1"
        env["MAYA_DISABLE_CLIC_IPM"] = "1"
        # Mayapy launchers can set Qt to offscreen; GUI Playblast needs a real
        # VP2 model panel. Do not inherit a forced headless platform.
        env.pop("QT_QPA_PLATFORM", None)
        package_root = str(Path(__file__).resolve().parents[3])
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_root, env.get("PYTHONPATH", "")]))
        options = {}
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            options["startupinfo"] = startup
        command = [str(executable), "-noAutoloadPlugins", "-log", str(log_path), "-script", str(bootstrap)]
        began = last_change = time.monotonic()
        last_event = None
        try:
            with console_path.open("w", encoding="utf-8") as console:
                process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                           stdout=console, stderr=subprocess.STDOUT, **options)
                if progress:
                    progress(0, f"Start GUI Maya (PID {process.pid}); log: {log_path}")
                while True:
                    result = _read(result_path)
                    if result:
                        if result.get("state") != "COMPLETE":
                            raise RuntimeError(result.get("error") or "GUI Playblast failed")
                        if progress:
                            progress(1, "GUI Playblast complete")
                        return result
                    event = _read(event_path)
                    now = time.monotonic()
                    if event and event != last_event:
                        last_change, last_event = now, event
                        if progress:
                            progress(float(event.get("fraction", 0)), str(event.get("message", "Playblast")))
                    if process.poll() is not None:
                        # The result write happens before normal process exit.
                        if _read(result_path):
                            continue
                        raise RuntimeError(f"GUI Maya exited without a result (exit {process.returncode})")
                    if not last_event and now - began > startup_timeout:
                        raise TimeoutError(f"GUI Maya startup timed out after {startup_timeout}s")
                    if last_event and now - last_change > stall_timeout:
                        raise TimeoutError(f"GUI Maya stalled after {stall_timeout}s: {last_event.get('message')}")
                    time.sleep(0.25)
        except Exception as exc:
            raise RuntimeError(f"{exc}\nGUI Maya log: {log_path}\nLauncher log: {console_path}\nRequest: {request_path}") from exc
        finally:
            if process is not None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()  # Only the process this job launched.
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def run_gui(request_path):
    """Called once via Maya's deferred startup; never operates on the artist GUI."""
    import maya.cmds as cmds
    from .playblast_preset import _apply_panel_preset, _force_playblast_overlays_off
    try:
        from PySide2.QtGui import QImage
    except ImportError:
        from PySide6.QtGui import QImage

    request = _read(request_path)
    try:
        if request.get("schema") != SCHEMA:
            raise ValueError("Unsupported GUI Playblast request schema")
        validate_layers(request["layers"])
        event_path = request["progress"]
        def event(message, fraction=0):
            _write(event_path, {"message": message, "fraction": fraction, "time": time.time()})
        event("GUI Maya ready; loading plug-ins")
        for plugin in request.get("plugins", []):
            try:
                cmds.loadPlugin(plugin["path"], quiet=True)
            except Exception:
                if plugin.get("required"):
                    raise
        event("Open constructed Review Source")
        cmds.file(request["scene"], open=True, force=True, prompt=False, executeScriptNodes=False)
        # No look/rig re-resolution, UV relinking, or material replacement here.
        for node in cmds.ls(type="SmartViewportGateGuide") or []:
            cmds.setAttr(node + ".visibility", False)
        panels = cmds.getPanel(type="modelPanel") or []
        if not panels:
            raise RuntimeError("GUI Maya has no model panel for Playblast")
        panel = panels[0]
        total = sum(row["frame_range"][1] - row["frame_range"][0] + 1 for row in request["layers"])
        completed = 0
        outputs = {}
        for layer in request["layers"]:
            for display_layer in request["all_layers"]:
                plug = display_layer + ".visibility"
                if not cmds.objExists(plug):
                    raise RuntimeError(f"Constructed Review Layer missing: {display_layer}")
                cmds.setAttr(plug, display_layer == layer["display_layer"])
            camera = layer["camera"]
            if not cmds.objExists(camera):
                raise RuntimeError(f"Constructed camera missing: {camera}")
            shapes = cmds.listRelatives(camera, shapes=True, fullPath=True, type="camera") or []
            if not shapes:
                raise RuntimeError(f"Not a camera transform: {camera}")
            cmds.lookThru(panel, camera)
            cmds.setAttr(shapes[0] + ".overscan", float(layer.get("overscan", 1)))
            cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer")
            display = {"display_appearance": "smoothShaded", "display_textures": True,
                       "display_lights": "all", "use_default_material": False,
                       "shadows": True, "image_planes": False, **layer.get("display", {})}
            _apply_panel_preset(cmds, panel, display)
            _force_playblast_overlays_off(cmds, panel)
            cmds.modelEditor(panel, edit=True, locators=False, joints=False)
            width, height = layer["resolution"]
            output_dir = Path(layer["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            extension = Path(layer["output_pattern"]).suffix.lower()
            compression = "png" if extension == ".png" else "jpg"
            start, end = layer["frame_range"]
            for frame in range(start, end + 1):
                target = output_dir / layer["output_pattern"].replace("####", f"{frame:04d}")
                if target.exists():
                    raise FileExistsError(f"Refusing to overwrite Playblast frame: {target}")
                event(f"Playblast {layer['name']}: {frame}/{end}", completed / total)
                cmds.currentTime(frame, edit=True)
                cmds.refresh(force=True)
                cmds.playblast(format="image", compression=compression, completeFilename=str(target),
                               frame=frame, widthHeight=[width, height], percent=100,
                               viewer=False, forceOverwrite=False, offScreen=True,
                               showOrnaments=False, editorPanelName=panel)
                image = QImage(str(target))
                if image.isNull() or (image.width(), image.height()) != (width, height):
                    raise RuntimeError(f"Playblast image missing/invalid/wrong resolution: {target}")
                completed += 1
            outputs[layer["name"]] = {"frames": end - start + 1, "resolution": [width, height]}
        event("GUI Playblast complete", 1)
        _write(request["result"], {"state": "COMPLETE", "backend": BACKEND_VERSION, "layers": outputs})
    except Exception:
        if request.get("result"):
            _write(request["result"], {"state": "FAILED", "error": traceback.format_exc()})
    finally:
        cmds.quit(force=True)
