"""Create vehicle spec points directly from a Maya locator Alembic file.

Run inside Houdini:

    from smartlib.dcc.houdini import smart_menu
    smart_menu.import_abc_vehicle_spec()

This bypasses Alembic SOP geometry attributes and reads the Alembic transform
hierarchy with Houdini's Alembic Python API. It is useful when Alembic SOP does
not expose Maya locator names as point/primitive attributes.
"""

from __future__ import annotations

import os


LOCATOR_TOKENS = {
    "car_root": "car_root",
    "wheel_FL": "wheel_FL",
    "wheel_FR": "wheel_FR",
    "wheel_RL": "wheel_RL",
    "wheel_RR": "wheel_RR",
}


def _leaf_name(path):
    leaf = str(path).replace("|", "/").split("/")[-1]
    return leaf.split(":")[-1]


def _canonical_name(path, tokens):
    leaf = _leaf_name(path).lower()
    for canonical, token in tokens.items():
        if token.lower() in leaf:
            return canonical
    return None


def _match_score(path, canonical, tokens):
    leaf = _leaf_name(path).lower()
    token = tokens[canonical].lower()
    if leaf == token:
        return 0
    if leaf.endswith("shape"):
        return 3
    if leaf.endswith(token):
        return 1
    return 2


def _is_schema_label(value):
    labels = (
        "xform",
        "polymesh",
        "subd",
        "curves",
        "points",
        "camera",
        "nupatch",
        "faceset",
    )
    return str(value).lower() in labels


def _append_unique(values, value):
    if value and value not in values:
        values.append(value)


def _extract_paths_from_hierarchy(item, paths, parent=""):
    """Extract Alembic object paths from several hou hierarchy tuple variants."""
    if isinstance(item, str):
        if item.startswith("/"):
            _append_unique(paths, item)
        elif parent and not _is_schema_label(item):
            _append_unique(paths, "{}/{}".format(parent.rstrip("/"), item))
        elif _canonical_name(item, LOCATOR_TOKENS) is not None:
            _append_unique(paths, "/{}".format(item))
        return

    if isinstance(item, (list, tuple)):
        if item and isinstance(item[0], str) and not _is_schema_label(item[0]):
            name = item[0]
            if name == "/":
                current = ""
            elif name.startswith("/"):
                current = name
            else:
                current = "{}/{}".format(parent.rstrip("/"), name) if parent else "/{}".format(name)

            if current:
                _append_unique(paths, current)

            for value in item[1:]:
                _extract_paths_from_hierarchy(value, paths, current)
            return

        for value in item:
            _extract_paths_from_hierarchy(value, paths, parent)


def _world_position(hou, abc_path, object_path, frame, scale):
    time = frame / hou.fps()
    matrix = hou.alembicGetWorldTransform(abc_path, object_path, time)
    if isinstance(matrix, (list, tuple)):
        matrix = matrix[0]

    if hasattr(matrix, "extractTranslates"):
        pos = matrix.extractTranslates()
        return tuple(float(v) * scale for v in pos)

    values = matrix.asTuple()
    return (float(values[12]) * scale, float(values[13]) * scale, float(values[14]) * scale)


def _node_world_position(node, scale):
    pos = node.worldTransform().extractTranslates()
    return tuple(float(v) * scale for v in pos)


def _set_first_existing_parm(node, names, value):
    for name in names:
        parm = node.parm(name)
        if parm is not None:
            parm.set(value)
            return name
    return None


def _press_first_existing_button(node, names):
    for name in names:
        parm = node.parm(name)
        if parm is not None:
            parm.pressButton()
            return name
    return None


def _match_from_nodes(hou, abc_path, scale, debug_lines):
    obj = hou.node("/obj")
    existing = obj.node("TEMP_vehicle_spec_abc_archive")
    if existing is not None:
        existing.destroy()
    archive = obj.createNode("alembicarchive", "TEMP_vehicle_spec_abc_archive")
    matched = {}
    matched_scores = {}

    try:
        file_parm = _set_first_existing_parm(
            archive,
            ("fileName", "filename", "file", "abcfile", "alembicfile"),
            abc_path,
        )
        if file_parm is None:
            parm_names = [parm.name() for parm in archive.parms()]
            raise hou.Error("Could not find Alembic Archive file parameter. Parms: {}".format(parm_names))

        build_button = _press_first_existing_button(
            archive,
            ("buildHierarchy", "buildhierarchy", "build", "reload"),
        )
        if build_button is None:
            parm_names = [parm.name() for parm in archive.parms()]
            raise hou.Error("Could not find Alembic Archive build button. Parms: {}".format(parm_names))

        children = archive.allSubChildren()
        debug_lines.append("Archive node fallback:")
        debug_lines.append("  file parm: {}".format(file_parm))
        debug_lines.append("  build button: {}".format(build_button))
        debug_lines.append("  child count: {}".format(len(children)))
        debug_lines.append("")
        debug_lines.append("Archive nodes:")

        for node in children:
            path = node.path()
            debug_lines.append(path)
            canonical = _canonical_name(node.name(), LOCATOR_TOKENS)
            if canonical is None:
                canonical = _canonical_name(path, LOCATOR_TOKENS)
            if canonical is None:
                continue

            score = _match_score(node.name(), canonical, LOCATOR_TOKENS)
            if canonical in matched and matched_scores[canonical] <= score:
                continue

            matched[canonical] = {
                "path": path,
                "position": _node_world_position(node, scale),
            }
            matched_scores[canonical] = score
    finally:
        archive.destroy()

    return matched


def _match_from_hom_alembic(hou, abc_path, scale, frame, debug_lines):
    hierarchy = hou.alembicGetSceneHierarchy(abc_path, "/")
    paths = []
    _extract_paths_from_hierarchy(hierarchy, paths)

    debug_lines.append("Raw hierarchy:")
    debug_lines.append(repr(hierarchy))
    debug_lines.append("")
    debug_lines.append("Object paths:")
    debug_lines.extend(paths)
    debug_lines.append("")

    matched = {}
    matched_scores = {}

    for path in paths:
        canonical = _canonical_name(path, LOCATOR_TOKENS)
        if canonical is None:
            continue

        score = _match_score(path, canonical, LOCATOR_TOKENS)
        if canonical in matched and matched_scores[canonical] <= score:
            continue

        matched[canonical] = {
            "path": path,
            "position": _world_position(hou, abc_path, path, frame, scale),
        }
        matched_scores[canonical] = score

    return matched


def _write_debug_report(path, lines):
    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    out_dir = os.path.join(workspace, "generated_vehicle_specs")
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(
        out_dir,
        "{}_vehicle_spec_debug.txt".format(os.path.splitext(os.path.basename(path))[0]),
    )
    with open(report_path, "w") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")
    return report_path


def import_abc_vehicle_spec_from_path(abc_path, scale=0.01):
    import hou

    abc_path = hou.expandString(abc_path)
    if not os.path.exists(abc_path):
        raise hou.Error("Alembic file does not exist: {}".format(abc_path))

    frame = hou.frame()
    debug_lines = [
        "Alembic: {}".format(abc_path),
        "Frame: {}".format(frame),
        "Input scale: {}".format(scale),
        "",
    ]

    if hasattr(hou, "alembicGetSceneHierarchy") and hasattr(hou, "alembicGetWorldTransform"):
        matched = _match_from_hom_alembic(hou, abc_path, scale, frame, debug_lines)
    else:
        debug_lines.append("hou.alembicGetSceneHierarchy unavailable; using Alembic Archive node fallback.")
        debug_lines.append("")
        matched = _match_from_nodes(hou, abc_path, scale, debug_lines)

    debug_lines.append("Matches:")

    for name in LOCATOR_TOKENS:
        if name in matched:
            debug_lines.append("{} <- {} P={}".format(name, matched[name]["path"], matched[name]["position"]))

    missing = [name for name in LOCATOR_TOKENS if name not in matched]
    if missing:
        debug_lines.append("")
        debug_lines.append("Missing: {}".format(", ".join(missing)))
        report_path = _write_debug_report(abc_path, debug_lines)
        raise hou.Error(
            "Could not find required ABC locator paths: {}. Debug report: {}".format(
                ", ".join(missing), report_path
            )
        )

    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    out_dir = os.path.join(workspace, "generated_vehicle_specs")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(abc_path))[0]
    bgeo_path = os.path.join(out_dir, "{}_from_abc.bgeo.sc".format(base))

    geo = hou.Geometry()
    name_attrib = geo.addAttrib(hou.attribType.Point, "name", "")
    pscale_attrib = geo.addAttrib(hou.attribType.Point, "pscale", 0.1)
    geo.addAttrib(hou.attribType.Global, "wheel_radius", 0.34 * scale)
    geo.addAttrib(hou.attribType.Global, "wheel_center_height", 0.34 * scale)

    for name in LOCATOR_TOKENS:
        point = geo.createPoint()
        point.setPosition(matched[name]["position"])
        point.setAttribValue(name_attrib, name)
        point.setAttribValue(pscale_attrib, 0.1)

    geo.saveToFile(bgeo_path)
    report_path = _write_debug_report(abc_path, debug_lines)

    obj = hou.node("/obj")
    node_name = "vehicle_spec_{}_abc".format(base)
    existing = obj.node(node_name)
    if existing is not None:
        existing.destroy()

    geo_node = obj.createNode("geo", node_name)
    for child in geo_node.children():
        child.destroy()

    file_sop = geo_node.createNode("file", "vehicle_spec_points")
    file_sop.parm("file").set(bgeo_path)
    file_sop.setDisplayFlag(True)
    file_sop.setRenderFlag(True)
    geo_node.layoutChildren()

    print("Imported ABC vehicle spec:")
    print("  {}".format(abc_path.replace("\\", "/")))
    print("Wrote:")
    print("  {}".format(bgeo_path.replace("\\", "/")))
    print("Debug report:")
    print("  {}".format(report_path.replace("\\", "/")))
    print("Connect this SOP to input 2 of smart::car_path_locators::1.0:")
    print("  {}".format(file_sop.path()))
    return file_sop


def import_abc_vehicle_spec():
    import hou

    default_path = ""
    if os.path.exists(default_path):
        result, use_default = hou.ui.displayMessage(
            "Use default Alembic?\n{}".format(default_path),
            buttons=("Use Default", "Choose File", "Cancel"),
            default_choice=0,
            close_choice=2,
            title="ABC Vehicle Spec",
        ), None
        if result == 0:
            abc_path = default_path
        elif result == 1:
            abc_path = ""
        else:
            return
    else:
        abc_path = ""

    if not abc_path:
        abc_path = hou.ui.selectFile(
            title="Import ABC Vehicle Spec",
            file_type=hou.fileType.Any,
            chooser_mode=hou.fileChooserMode.Read,
            default_value=default_path,
        )
        if not abc_path:
            return

    result, scale_text = hou.ui.readInput(
        "Input scale for Alembic locator positions",
        buttons=("OK", "Cancel"),
        initial_contents="0.01",
        title="ABC Vehicle Spec Input Scale",
    )
    if result != 0:
        return

    import_abc_vehicle_spec_from_path(abc_path, float(scale_text))


def import_default_carA():
    raise RuntimeError("Set an Alembic path and call import_abc_vehicle_spec_from_path(path, scale).")


if globals().get("ABC_VEHICLE_SPEC_IMPORT_DEFAULT", False):
    import_default_carA()
else:
    import_abc_vehicle_spec()
