"""Review camera layer rules linked to an immutable Primary Camera Publish."""
from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path

from . import camera_live, camera_output as co, primary_camera


SCHEMA = "smartpipeline.review_camera_rules.v1"


def collect(primary, rows, reference_resolution, primary_publish, cmds):
    primary, _shape = co.camera_nodes(primary, cmds)
    primary_publish = Path(primary_publish)
    primary_data = json.loads(primary_publish.read_text(encoding="utf-8-sig"))
    if primary_data.get("schema") != primary_camera.SCHEMA:
        raise ValueError("Publish Primary Camera before publishing Review Camera Rules.")
    if primary.rsplit("|", 1)[-1].split(":")[-1] != str(primary_data.get("camera") or ""):
        raise ValueError("Selected Primary does not match the resolved Primary Camera Publish.")
    output_rows = []
    for incoming in rows:
        row = dict(incoming)
        rule = row.get("camera_rule") or {"mode": "shared"}
        width, height = camera_live.output_size(reference_resolution, rule)
        row.update(
            camera_rule=rule, width=width, height=height, output_override="",
            camera=(primary.rsplit("|", 1)[-1] if rule["mode"] == "shared" else
                    "smartCam_" + re.sub(r"[^A-Za-z0-9_]", "_", row["layer"])),
        )
        output_rows.append(row)
    return {
        "schema": SCHEMA,
        "role": "review_camera_rules",
        "primary_camera": {
            "schema": primary_camera.SCHEMA,
            "path": str(primary_publish),
            "version": str(primary_data.get("version") or primary_publish.parent.name),
            "camera": str(primary_data.get("camera") or ""),
            "sha256": hashlib.sha256(primary_publish.read_bytes()).hexdigest(),
        },
        "reference_resolution": list(reference_resolution),
        "rows": output_rows,
        "frame_range": [min(row["start"] for row in output_rows),
                        max(row["end"] for row in output_rows)],
    }


def collect_for_review_layers(
    primary,
    rows,
    review_layers,
    reference_resolution,
    primary_publish,
    cmds,
    *,
    layer_rules=None,
):
    """Collect rules for every published Review Layer, independent of render checks."""

    def key(value):
        return re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").strip().lower())

    available = {}
    for incoming in rows or []:
        row = dict(incoming or {})
        for value in (
            row.get("review_layer_id"), row.get("layer"), row.get("display_layer")
        ):
            if key(value):
                available.setdefault(key(value), row)

    selected = []
    missing = []
    rules = dict(layer_rules or {})
    normalized_rules = {key(name): value for name, value in rules.items()}
    for layer_id, definition in (review_layers or {}).items():
        if not bool((definition or {}).get("enabled", True)):
            continue
        lookup = key(layer_id)
        row = dict(available.get(lookup) or {})
        if not row:
            missing.append(str(layer_id))
            continue
        display_layer = str((definition or {}).get("display_layer") or layer_id)
        row.update(
            enabled=True,
            review_layer_id=str(layer_id),
            layer=str(layer_id),
            display_layer=display_layer,
            camera_rule=dict(
                rules.get(layer_id)
                or normalized_rules.get(lookup)
                or row.get("camera_rule")
                or {"mode": "shared"}
            ),
        )
        selected.append(row)
    if missing:
        raise ValueError(
            "Camera rule rows are missing for published Review Layers: "
            + ", ".join(missing)
        )
    if not selected:
        raise ValueError("Published Review Layer Definition has no enabled layers.")
    return collect(
        primary, selected, reference_resolution, primary_publish, cmds
    )


def make_reference_relative(data, destination):
    result = dict(data)
    reference = dict(result["primary_camera"])
    reference["path"] = Path(os.path.relpath(reference["path"], Path(destination).parent)).as_posix()
    result["primary_camera"] = reference
    return result


def resolve_primary_path(data, provenance):
    path = Path(str((data.get("primary_camera") or {}).get("path") or ""))
    return path if path.is_absolute() else (Path(provenance).parent / path).resolve()


def restore(data, *, cmds, provenance, frame_offset=0.0):
    if data.get("schema") != SCHEMA:
        raise ValueError("Unsupported Review Camera Rules schema.")
    primary_path = resolve_primary_path(data, provenance)
    if not primary_path.is_file():
        raise FileNotFoundError("Referenced Primary Camera Publish was not found: " + str(primary_path))
    primary_data = json.loads(primary_path.read_text(encoding="utf-8-sig"))
    expected_hash = str((data.get("primary_camera") or {}).get("sha256") or "")
    actual_hash = hashlib.sha256(primary_path.read_bytes()).hexdigest()
    if not expected_hash or actual_hash != expected_hash:
        raise ValueError("Referenced Primary Camera Publish fingerprint does not match.")
    root, primary = primary_camera.restore_with_root(
        primary_data, cmds=cmds, provenance=str(primary_path), frame_offset=frame_offset
    )
    results = camera_live.configure(
        primary, data.get("rows") or [], data.get("reference_resolution") or [], cmds=cmds
    )
    from .review_playblast import save_scene_playblast_settings
    rows = []
    for incoming, result in zip(data.get("rows") or [], results):
        rows.append({**incoming, "camera": result["camera"].rsplit("|", 1)[-1], "mode": "Custom"})
    prefs = {
        "primary": primary,
        "primary_uuid": cmds.ls(primary, uuid=True)[0],
        "reference_resolution": data["reference_resolution"],
        "layer_rules": {row["layer"]: row["camera_rule"] for row in rows},
        "primary_publish_source": str(primary_path),
        "publish_source": str(provenance),
        "live": True,
        "auto_update": True,
    }
    node = ":smartCameraPlayblastInfo"
    if not cmds.objExists(node):
        cmds.createNode("network", name=node, skipSelect=True)
    co._string_attr(cmds, node, "settingsJson", json.dumps(prefs))
    save_scene_playblast_settings({
        "rows": rows,
        "layer_order": [row["layer"] for row in rows],
        "camera_package_source": str(provenance),
    }, cmds)
    return root
