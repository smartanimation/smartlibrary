"""Explicit Nuke OCIO setup and image roundtrip evidence."""
from smartlib.core.color_settings import environment_contract, config_fingerprint
from smartlib.color.validation import sha256, save_json


def apply(contract=None):
    import nuke
    contract = contract or environment_contract()
    if not contract:
        return
    root = nuke.root()
    root["colorManagement"].setValue("OCIO")
    root["OCIO_config"].setValue("custom")
    root["customOCIOConfigPath"].setValue(contract["config"])
    root["workingSpaceLUT"].setValue(contract["working_space"])
    process = f'{contract["view"]} ({contract["display"]})'
    root["monitorLut"].setValue(process) if "monitorLut" in root.knobs() else root["monitorLUT"].setValue(process)
    for viewer in nuke.allNodes("Viewer"):
        viewer["viewerProcess"].setValue(process)


def install():
    import nuke
    if not environment_contract():
        return
    apply()
    nuke.addOnScriptLoad(apply)
    nuke.addOnCreate(apply, nodeClass="Root")
    nuke.addOnCreate(apply, nodeClass="Viewer")


def run(manifest):
    """Run in an empty, disposable Nuke process, not a user's open script."""
    import nuke
    import PyOpenColorIO as ocio
    if nuke.allNodes():
        raise RuntimeError("Color validation requires an empty Nuke script.")
    contract = manifest["contract"]
    apply(contract)
    p = manifest["paths"]
    source = nuke.nodes.Read(file=p["reference.exr"])
    source["colorspace"].setValue("ACEScg")
    display = nuke.nodes.OCIODisplay()
    display.setInput(0, source)
    display["in_colorspace"].setValue("ACEScg")
    display["display"].setValue(contract["display"])
    display["view"].setValue(contract["view"])
    for name, node in (("Nuke_output.exr", source), ("Nuke_display.exr", display)):
        write = nuke.nodes.Write(file=p[name], file_type="exr")
        write.setInput(0, node)
        write["datatype"].setValue("32 bit float")
        write["channels"].setValue("rgb")
        write["raw"].setValue(True)
        nuke.execute(write, 1, 1)
    root = nuke.root()
    config = root["customOCIOConfigPath"].value()
    state = {"host": "Nuke", "run_id": manifest["run_id"], "status": "partial",
             "state_source": "host_readback", "host_version": nuke.NUKE_VERSION_STRING,
             "ocio_version": ocio.GetVersion(), "config": config, "config_sha256": sha256(config),
             "bundle_sha256": config_fingerprint(config), "working_space": root["workingSpaceLUT"].value(),
             "display": display["display"].value(), "view": display["view"].value(),
             "artifacts": {name: sha256(p[name]) for name in ("Nuke_output.exr", "Nuke_display.exr")},
             "note": "Nuke node CPU output; viewer framebuffer capture remains required."}
    nuke.scriptSaveAs(p["color_validation.nk"])
    save_json(p["Nuke_state.json"], state)
    return state
