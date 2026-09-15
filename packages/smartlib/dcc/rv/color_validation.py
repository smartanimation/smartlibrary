"""RV OCIO setup and native GPU/session evidence."""
from pathlib import Path
from smartlib.core.color_settings import environment_contract, config_fingerprint
from smartlib.color.validation import save_json, sha256


def apply(contract=None):
    from rv import commands
    import PyOpenColorIO as ocio
    contract = contract or environment_contract()
    if not contract:
        return
    sources = commands.nodesOfType("RVFileSource")
    if not sources:
        return
    # This project policy is for scene-linear EXRs. Never reinterpret review movies.
    if any(not all(str(f).lower().endswith(".exr") for f in commands.getStringProperty(s + ".media.movie")) for s in sources):
        raise ValueError("Project color setup currently requires an EXR-only RV session.")
    config = ocio.Config.CreateFromFile(contract["config"])
    config.validate()
    ocio.SetCurrentConfig(config)
    for group in commands.nodesOfType("RVLinearizePipelineGroup"):
        commands.setStringProperty(group + ".pipeline.nodes", ["OCIOFile"], True)
        for node in commands.nodesInGroup(group):
            if commands.nodeType(node) != "OCIOFile":
                continue
            for key, value in {"ocio.function": "color", "ocio.inColorSpace": "ACEScg", "ocio_color.outColorSpace": "ACEScg"}.items():
                commands.setStringProperty(node + "." + key, [value], True)
            commands.ocioUpdateConfig(node)
    for group in commands.nodesOfType("RVDisplayPipelineGroup"):
        commands.setStringProperty(group + ".pipeline.nodes", ["OCIODisplay"], True)
        for node in commands.nodesInGroup(group):
            if commands.nodeType(node) != "OCIODisplay":
                continue
            commands.setIntProperty(node + ".ocio.active", [0], True)
            for key, value in {"ocio.function": "display", "ocio.inColorSpace": "ACEScg", "ocio_display.display": contract["display"], "ocio_display.view": contract["view"]}.items():
                commands.setStringProperty(node + "." + key, [value], True)
            commands.ocioUpdateConfig(node)
            commands.setIntProperty(node + ".ocio.active", [1], True)
    commands.redraw()


def snapshot(manifest):
    from rv import commands
    import PyOpenColorIO as ocio
    contract = manifest["contract"]
    nodes = []
    for kind in ("OCIOFile", "OCIODisplay"):
        for node in commands.nodesOfType(kind):
            row = {"node": node, "type": kind}
            for key in ("ocio.inColorSpace", "ocio_color.outColorSpace", "ocio_display.display", "ocio_display.view"):
                if commands.propertyExists(node + "." + key):
                    row[key] = commands.getStringProperty(node + "." + key, 0)
            row["active"] = commands.getIntProperty(node + ".ocio.active", 0)
            nodes.append(row)
    displays = [n for n in nodes if n["type"] == "OCIODisplay"]
    inputs = [n for n in nodes if n["type"] == "OCIOFile"]
    if not displays or not inputs or any(n["active"] != [1] for n in nodes):
        raise ValueError("RV OCIO nodes are missing or disabled.")
    for n in displays:
        if n["ocio_display.display"] != [contract["display"]] or n["ocio_display.view"] != [contract["view"]]:
            raise ValueError("RV display/view mismatch.")
    if any(n["ocio.inColorSpace"] != ["ACEScg"] for n in nodes) or any(n["ocio_color.outColorSpace"] != ["ACEScg"] for n in inputs):
        raise ValueError("RV input/working space mismatch.")
    active = ocio.GetCurrentConfig()
    disk = ocio.Config.CreateFromFile(contract["config"])
    if active.getCacheID() != disk.getCacheID():
        raise ValueError("RV active config differs from project config.")
    state = {"host": "RV", "run_id": manifest["run_id"], "status": "partial", "state_source": "host_readback",
             "host_version": str(commands.getVersion()), "ocio_version": ocio.GetVersion(),
             "config": contract["config"], "config_sha256": sha256(contract["config"]),
             "bundle_sha256": config_fingerprint(contract["config"]), "working_space": inputs[0]["ocio_color.outColorSpace"][0],
             "display": displays[0]["ocio_display.display"][0], "view": displays[0]["ocio_display.view"][0],
             "ocio_cache_id": active.getCacheID(), "nodes": nodes}
    save_json(manifest["paths"]["RV_state.json"], state)
    return state


_mode = None

def install():
    global _mode
    from rv import commands, rvtypes
    from PySide6.QtCore import QTimer
    if not environment_contract() or _mode is not None:
        return
    def update(event=None):
        if event:
            event.reject()
        def deferred():
            try:
                apply()
            except Exception as exc:
                print("SmartPipeline color setup: " + str(exc))
        QTimer.singleShot(0, deferred)
    _mode = rvtypes.MinorMode()
    _mode.init("SmartPipeline Color", None, [("source-group-complete", update, "Project EXR color"), ("after-session-read", update, "Project EXR color")], None, "source_setup", 20)
    commands.activateMode("SmartPipeline Color")
    update()


def capture_session(manifest_path):
    """Entry point for a disposable RV process launched with -pyeval."""
    from rv import commands
    from PySide6.QtCore import QTimer, QCoreApplication
    from smartlib.color.validation import load_manifest
    manifest = load_manifest(manifest_path)
    def finish():
        try:
            snapshot(manifest)
            commands.saveSession(manifest["paths"]["color_validation.rv"], True, True, False)
        except Exception as exc:
            save_json(manifest["paths"]["RV_state.json"], {"host": "RV", "run_id": manifest["run_id"], "status": "failed", "error": str(exc)})
        finally:
            QCoreApplication.exit(0)
    def setup():
        try:
            apply(manifest["contract"])
            QTimer.singleShot(2000, finish)
        except Exception as exc:
            save_json(manifest["paths"]["RV_state.json"], {"host": "RV", "run_id": manifest["run_id"], "status": "failed", "error": str(exc)})
            QCoreApplication.exit(1)
    QTimer.singleShot(1500, setup)
