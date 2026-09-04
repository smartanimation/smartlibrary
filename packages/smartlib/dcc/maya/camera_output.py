"""Baked output cameras for Smart Camera Playblast; no pipeline path ownership.

The source rendering frustum is evaluated by Maya, not reconstructed from
filmFit enums. Focal length remains the creative lens; the effective film gate
is normalized for each output aspect. Generated cameras are disposable data.
"""
from __future__ import annotations

import json
import math
import re
from typing import Callable

OWNER_ATTR = "smartCameraOutputSchema"
OWNER = "smartpipeline.camera_output.v1"
PRIMARY_ATTR = "smartPrimaryCamera"
SPEC_ATTR = "smartCameraOutputSpec"
SAMPLE_RANGE_ATTR = "smartCameraSampleRange"
POLICIES = ("horizontal", "vertical", "fit", "fill")
OUTPUT_POLICIES = POLICIES + ("pixel_scale",)
TRANSFORM_ATTRS = ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz", "shearXY", "shearXZ", "shearYZ")
SHAPE_ATTRS = (
    "focalLength", "horizontalFilmAperture", "verticalFilmAperture",
    "horizontalFilmOffset", "verticalFilmOffset", "nearClipPlane", "farClipPlane",
    "focusDistance", "fStop", "centerOfInterest", "depthOfField",
)


def fit_frustum(frustum, aspect: float, policy: str):
    """Resize a centered-on-source gate, retaining its center and one axis."""
    left, right, bottom, top = map(float, frustum)
    if not all(math.isfinite(v) for v in (left, right, bottom, top, aspect)):
        raise ValueError("Frustum and aspect must be finite.")
    width, height = right - left, top - bottom
    if width <= 0 or height <= 0 or aspect <= 0 or policy not in POLICIES:
        raise ValueError("Invalid frustum, aspect or fit policy.")
    source_aspect = width / height
    horizontal = policy == "horizontal"
    if policy == "fit":
        horizontal = aspect <= source_aspect
    elif policy == "fill":
        horizontal = aspect >= source_aspect
    elif policy == "vertical":
        horizontal = False
    if horizontal:
        height = width / aspect
    else:
        width = height * aspect
    cx, cy = (left + right) / 2, (bottom + top) / 2
    return (cx - width / 2, cx + width / 2, cy - height / 2, cy + height / 2)


def output_frustum(frustum, reference_resolution, output_resolution, policy):
    """Pixel-scale mode changes canvas extent, not projected object pixel size."""
    rw, rh = reference_resolution
    ow, oh = output_resolution
    if min(rw, rh, ow, oh) <= 0:
        raise ValueError("Resolutions must be positive.")
    if policy != "pixel_scale":
        return fit_frustum(frustum, ow / oh, policy)
    left, right, bottom, top = frustum
    cx, cy = (left + right) / 2, (bottom + top) / 2
    width, height = (right - left) * ow / rw, (top - bottom) * oh / rh
    return cx - width / 2, cx + width / 2, cy - height / 2, cy + height / 2


def film_gate(frustum, focal_length: float, near_clip: float):
    """Convert a near-plane frustum to Maya film aperture/offset, in inches."""
    if focal_length <= 0 or near_clip <= 0:
        raise ValueError("Focal length and near clip must be positive.")
    left, right, bottom, top = frustum
    factor = focal_length / (near_clip * 25.4)
    return {
        "horizontalFilmAperture": (right - left) * factor,
        "verticalFilmAperture": (top - bottom) * factor,
        "horizontalFilmOffset": (right + left) * 0.5 * factor,
        "verticalFilmOffset": (top + bottom) * 0.5 * factor,
    }


def camera_nodes(camera, cmds=None):
    if cmds is None:
        import maya.cmds as cmds
    found = cmds.ls(camera, long=True) or []
    if len(found) != 1:
        raise ValueError(f"Camera must resolve uniquely: {camera}")
    node = found[0]
    if cmds.nodeType(node) == "camera":
        shape = node
        node = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]
    else:
        shapes = cmds.listRelatives(node, shapes=True, type="camera", fullPath=True) or []
        if len(shapes) != 1:
            raise ValueError(f"Expected one camera shape: {camera}")
        shape = shapes[0]
    return node, shape


def _camera_fn(shape):
    import maya.api.OpenMaya as om
    selection = om.MSelectionList()
    selection.add(shape)
    return om.MFnCamera(selection.getDagPath(0))


def _string_attr(cmds, node, attr, value):
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, dataType="string")
    cmds.setAttr(f"{node}.{attr}", value, type="string")


def primary_camera(camera, cmds=None):
    """Return the creative source of a generated camera (or the camera itself)."""
    if cmds is None:
        import maya.cmds as cmds
    transform, _ = camera_nodes(camera, cmds)
    if cmds.objExists(f"{transform}.{OWNER_ATTR}") and cmds.getAttr(f"{transform}.{OWNER_ATTR}") == OWNER:
        sources = cmds.listConnections(f"{transform}.{PRIMARY_ATTR}", source=True, destination=False) or []
        if len(sources) == 1:
            return camera_nodes(sources[0], cmds)[0]
    return transform


def _check_supported(cmds, shape):
    # Rendering frusta do not encode Maya post-projection transforms. Fail
    # explicitly rather than silently generating a subtly different camera.
    defaults = {
        "orthographic": 0, "panZoomEnabled": 0, "filmRollValue": 0,
        "filmTranslateH": 0, "filmTranslateV": 0, "postScale": 1, "preScale": 1,
    }
    for attr, default in defaults.items():
        if cmds.objExists(f"{shape}.{attr}"):
            value = float(cmds.getAttr(f"{shape}.{attr}"))
            if not math.isclose(value, default, abs_tol=1e-8):
                raise ValueError(f"Unsupported source camera setting: {attr}={value}. Use a centered perspective camera without post-projection/2D Pan Zoom.")


def _find_output(cmds, source, layer):
    candidates = cmds.ls(f"*.{OWNER_ATTR}", objectsOnly=True, long=True, recursive=True) or []
    matches = []
    for candidate in candidates:
        if cmds.getAttr(f"{candidate}.{OWNER_ATTR}") != OWNER:
            continue
        try:
            spec = json.loads(cmds.getAttr(f"{candidate}.{SPEC_ATTR}"))
            if spec.get("layer") == layer and primary_camera(candidate, cmds) == source:
                matches.append(candidate)
        except (ValueError, RuntimeError):
            continue
    if len(matches) > 1:
        raise ValueError(f"Multiple managed cameras found for {layer}; resolve duplicates first.")
    if matches and cmds.referenceQuery(matches[0], isNodeReferenced=True):
        raise ValueError(f"Cannot update a referenced output camera: {matches[0]}")
    return matches[0] if matches else None


def generate_output_cameras(primary, rows, reference_resolution, *, cmds=None,
                            progress: Callable[[int, int], bool] | None = None):
    """Generate/update only owned cameras, keying every integer output frame.

    No render globals, Primary attributes, memberships, output files or exports
    are modified. Failure/cancel rolls back this generation transaction.
    """
    if cmds is None:
        import maya.cmds as cmds
    source, source_shape = camera_nodes(primary, cmds)
    if primary_camera(source, cmds) != source or cmds.objExists(f"{source}.{OWNER_ATTR}"):
        raise ValueError("Choose an original Primary camera, not a generated output camera.")
    if not cmds.undoInfo(query=True, state=True):
        raise RuntimeError("Enable Maya Undo before generating cameras (required for rollback).")
    ref_width, ref_height = map(int, reference_resolution)
    if ref_width <= 0 or ref_height <= 0:
        raise ValueError("Reference resolution must be positive (square pixels).")
    if not math.isclose(float(cmds.getAttr("defaultResolution.pixelAspect")), 1.0, abs_tol=1e-6):
        raise ValueError("This experimental tool supports square pixels only. Set pixel aspect to 1 before generating.")
    specs = []
    seen = set()
    output_names = set()
    for row in rows:
        layer = str(row.get("layer") or "").strip()
        if not layer or layer in seen:
            raise ValueError("Output layer names must be nonempty and unique.")
        seen.add(layer)
        spec = {key: int(row[key]) for key in ("width", "height", "start", "end")}
        spec.update(layer=layer, policy=str(row.get("camera_fit") or "horizontal"))
        if min(spec["width"], spec["height"]) <= 0 or spec["end"] < spec["start"]:
            raise ValueError(f"Invalid resolution or frame range: {layer}")
        if spec["policy"] not in OUTPUT_POLICIES:
            raise ValueError(f"Invalid fit policy: {layer}")
        if cmds.objExists(f"{source}.{SAMPLE_RANGE_ATTR}"):
            first, last = json.loads(cmds.getAttr(f"{source}.{SAMPLE_RANGE_ATTR}"))
            if spec["start"] < first or spec["end"] > last:
                raise ValueError(f"{layer}: requested range exceeds the published Primary samples ({first}–{last}). Publish a wider Primary range first.")
        spec["existing"] = _find_output(cmds, source, layer)
        name = "smartCam_" + re.sub(r"[^A-Za-z0-9_]", "_", layer)
        if name in output_names:
            raise ValueError(f"Layer names resolve to the same camera name: {name}")
        output_names.add(name)
        occupied = cmds.ls(f":{name}", long=True) or []
        if any(node != spec["existing"] for node in occupied):
            raise ValueError(f"Camera name is already in use: {name}. Rename the other node before generating; it will not be overwritten.")
        spec["output_name"] = name
        specs.append(spec)
    if not specs:
        raise ValueError("Enable at least one layer to generate cameras.")
    original_time = cmds.currentTime(query=True)
    original_selection = cmds.ls(selection=True, long=True) or []
    source_fn = _camera_fn(source_shape)
    total = sum(s["end"] - s["start"] + 1 for s in specs)
    done = 0
    results = []
    renamed = {}
    cmds.undoInfo(openChunk=True, chunkName="Smart Camera Playblast: Generate")
    failed = False
    try:
        for spec in specs:
            node = spec.pop("existing")
            name = spec.pop("output_name")
            if node is None:
                node, _ = cmds.camera(name=f":{name}")
            node, shape = camera_nodes(node, cmds)
            if node.rsplit("|", 1)[-1] != name:
                old_path = node
                node = cmds.rename(node, f":{name}", ignoreShape=True)
                node, shape = camera_nodes(node, cmds)
                renamed[old_path] = node
            if node.rsplit("|", 1)[-1] != name:
                raise ValueError(f"Could not assign exact output camera name: {name}")
            # Only these tool-owned channels are replaced, never the Primary.
            cmds.cutKey(node, attribute=list(TRANSFORM_ATTRS), clear=True)
            cmds.cutKey(shape, attribute=list(SHAPE_ATTRS), clear=True)
            for attr, value in {"filmFit": 1, "overscan": 1, "cameraScale": 1,
                                "lensSqueezeRatio": 1, "orthographic": 0,
                                "panZoomEnabled": 0, "filmRollValue": 0,
                                "filmTranslateH": 0, "filmTranslateV": 0,
                                "postScale": 1, "preScale": 1, "filmFitOffset": 0,
                                "renderable": 0}.items():
                cmds.setAttr(f"{shape}.{attr}", value)
            max_error = 0.0
            for frame in range(spec["start"], spec["end"] + 1):
                if progress and progress(done, total) is False:
                    raise RuntimeError("Camera generation cancelled; all camera changes rolled back.")
                cmds.currentTime(frame, edit=True)
                _check_supported(cmds, source_shape)
                frustum = source_fn.getRenderingFrustum(ref_width / ref_height)
                target = output_frustum(frustum, (ref_width, ref_height), (spec["width"], spec["height"]), spec["policy"])
                values = {attr: cmds.getAttr(f"{source_shape}.{attr}") for attr in SHAPE_ATTRS}
                values.update(film_gate(target, values["focalLength"], values["nearClipPlane"]))
                matrix = cmds.xform(source, query=True, worldSpace=True, matrix=True)
                cmds.xform(node, worldSpace=True, matrix=matrix)
                cmds.setKeyframe(node, attribute=list(TRANSFORM_ATTRS), time=frame)
                for attr, value in values.items():
                    cmds.setAttr(f"{shape}.{attr}", value)
                    cmds.setKeyframe(shape, attribute=attr, time=frame)
                actual = _camera_fn(shape).getRenderingFrustum(spec["width"] / spec["height"])
                error = max(abs(a - b) / max(1.0, abs(b)) for a, b in zip(actual, target))
                max_error = max(max_error, error)
                if error > 1e-5:
                    raise RuntimeError(f"Projection validation failed: {spec['layer']} frame {frame}: {error:g}")
                actual_matrix = cmds.xform(node, query=True, worldSpace=True, matrix=True)
                if max(abs(a - b) for a, b in zip(actual_matrix, matrix)) > 1e-5:
                    raise RuntimeError(f"Transform validation failed: {spec['layer']} frame {frame}")
                done += 1
            cmds.keyTangent(node, attribute=list(TRANSFORM_ATTRS), inTangentType="linear", outTangentType="linear")
            cmds.keyTangent(shape, attribute=list(SHAPE_ATTRS), inTangentType="linear", outTangentType="linear")
            cmds.filterCurve([f"{node}.rx", f"{node}.ry", f"{node}.rz"])
            _string_attr(cmds, node, OWNER_ATTR, OWNER)
            _string_attr(cmds, node, SPEC_ATTR, json.dumps({**spec, "reference_resolution": [ref_width, ref_height]}))
            if not cmds.attributeQuery(PRIMARY_ATTR, node=node, exists=True):
                cmds.addAttr(node, longName=PRIMARY_ATTR, attributeType="message")
            if not cmds.isConnected(f"{source}.message", f"{node}.{PRIMARY_ATTR}"):
                cmds.connectAttr(f"{source}.message", f"{node}.{PRIMARY_ATTR}", force=True)
            results.append({"layer": spec["layer"], "camera": node, "max_projection_error": max_error})
    except Exception:
        failed = True
        raise
    finally:
        cmds.currentTime(original_time, edit=True)
        if original_selection:
            restored_selection = []
            for selected in original_selection:
                for old, new in renamed.items():
                    if selected == old or selected.startswith(old + "|") or selected.startswith(old + "."):
                        selected = new + selected[len(old):]
                        break
                restored_selection.append(selected)
            cmds.select(restored_selection, replace=True)
        else:
            cmds.select(clear=True)
        cmds.undoInfo(closeChunk=True)
        if failed:
            cmds.undo()
    return results
