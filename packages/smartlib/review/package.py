from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from smartlib.core.metadata import read_json, write_json
from smartlib.core.publish import PublishRecord, write_publish_json
from smartlib.core.versioning import format_version, next_version, parse_version


DEFAULT_REVIEW_OUTPUTS = {
    "CHA": ["beauty", "wireframe"],
    "CHB": ["beauty", "wireframe"],
    "BGA": ["beauty"],
    "FX": ["beauty"],
    "ENV": ["beauty"],
}
TAKE_DIR_RE = re.compile(r"^take(?P<take>\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ReviewPackagePlan:
    version_dir: Path
    publish_json: Path
    review_json: Path
    version: int
    publish_type: str = "review"
    subset: str = ""
    files: dict[str, str] = field(default_factory=dict)
    review_data: dict[str, Any] = field(default_factory=dict)


def next_review_version(shot_root: str | Path, department: str) -> int:
    base_dir = Path(shot_root) / "publish" / "review" / department
    versions = []
    if base_dir.exists():
        versions = [
            parsed
            for parsed in (parse_version(path.name) for path in base_dir.iterdir() if path.is_dir())
            if parsed is not None
        ]
    return next_version(versions)


def latest_review_version(shot_root: str | Path, department: str) -> int | None:
    base_dir = Path(shot_root) / "publish" / "review" / department
    latest = read_json(base_dir / "latest.json", {})
    if isinstance(latest, dict) and latest.get("version"):
        parsed = parse_version(str(latest["version"]))
        if parsed:
            return parsed
    if not base_dir.exists():
        return None
    versions = [
        parsed
        for parsed in (parse_version(path.name) for path in base_dir.iterdir() if path.is_dir())
        if parsed is not None
    ]
    return max(versions) if versions else None


def next_review_take(version_dir: str | Path) -> int:
    version_dir = Path(version_dir)
    takes = []
    layers_dir = version_dir / "layers"
    if layers_dir.exists():
        for take_dir in layers_dir.glob("*/take*"):
            if not take_dir.is_dir():
                continue
            match = TAKE_DIR_RE.match(take_dir.name)
            if match:
                takes.append(int(match.group("take")))
    return next_version(takes)


def build_review_package_plan(
    shot_root: str | Path,
    shot_data: dict[str, Any],
    cast_data: dict[str, Any],
    department: str,
    *,
    version: int | None = None,
    take: int | None = None,
    source_workfile: str = "",
    comment: str = "",
    project_root: str | Path | None = None,
    pipeline_root: str | Path | None = None,
) -> ReviewPackagePlan:
    shot_root = Path(shot_root)
    version = version or next_review_version(shot_root, department)
    version_label = format_version(version)
    version_dir = shot_root / "publish" / "review" / department / version_label
    editorial = shot_data.get("editorial") or {}
    frame_range = [
        int(editorial.get("cut_in", 1001)),
        int(editorial.get("cut_out", editorial.get("cut_in", 1001))),
    ]
    review_layers = cast_data.get("review_layers") or {}

    review_data = {
        "publish_type": "review",
        "subset": department,
        "version": version_label,
        "shot": shot_data.get("shot", shot_root.name),
        "episode": shot_data.get("episode", ""),
        "sequence": shot_data.get("sequence", ""),
        "department": department,
        "fps": editorial.get("fps", 24),
        "frame_range": frame_range,
        "movie": "review.mov",
        "layers": {},
        "thumbnails": {},
        "ae": {
            "project": "ae/review_project.aep",
            "template_used": "ae/template_used.json",
        },
    }

    files = {
        "review": "review.mov",
        "review_json": "review.json",
        "ae_project": "ae/review_project.aep",
        "ae_template_used": "ae/template_used.json",
    }

    layer_order = []
    for layer_name, layer in sorted(review_layers.items(), key=lambda item: int((item[1] or {}).get("order", 0))):
        members = list(layer.get("members") or [])
        if not members:
            continue
        take_label = _take_label(take if take is not None else layer.get("take", 1))
        outputs = {}
        for output_name in _outputs_for_layer(layer_name, layer):
            pattern = f"layers/{layer_name}/{take_label}/{output_name}_####.png"
            outputs[output_name] = pattern
            files[f"{layer_name}_{take_label}_{output_name}"] = pattern
        thumbnail = f"thumbnails/{layer_name}.jpg"
        layer_order.append(layer_name)
        review_data["layers"][layer_name] = {
            "members": members,
            "take": take_label,
            "outputs": outputs,
            "thumbnail": thumbnail,
            "camera": (layer.get("camera") or {}).get("name", ""),
            "camera_publish": (layer.get("camera") or {}).get("version", ""),
            "resolution": [
                int((layer.get("resolution") or {}).get("width", 960)),
                int((layer.get("resolution") or {}).get("height", 540)),
            ],
            "order": int(layer.get("order", 0)),
            "ae_slot": (layer.get("ae") or {}).get("template_slot", layer_name),
        }
        review_data["thumbnails"][layer_name] = thumbnail
        files[f"{layer_name}_thumbnail"] = thumbnail

    review_data["ae"]["layer_order"] = layer_order
    if project_root:
        review_data["project_root"] = str(project_root)
    if pipeline_root:
        review_data["pipeline_root"] = str(pipeline_root)

    publish_record = PublishRecord(
        publish_type="review",
        subset=department,
        version=version,
        files=files,
        source_workfile=source_workfile,
        comment=comment,
        status="planned",
    )
    return ReviewPackagePlan(
        version_dir=version_dir,
        publish_json=version_dir / "publish.json",
        review_json=version_dir / "review.json",
        version=version,
        subset=department,
        files=files,
        review_data={**review_data, "publish": publish_record.to_dict()},
    )


def write_review_package_plan(plan: ReviewPackagePlan) -> ReviewPackagePlan:
    plan.version_dir.mkdir(parents=True, exist_ok=True)
    publish_data = plan.review_data.get("publish") or {}
    record = PublishRecord(
        publish_type=str(publish_data.get("publish_type") or "review"),
        subset=str(publish_data.get("subset") or plan.subset),
        version=plan.version,
        files=dict(publish_data.get("files") or plan.files),
        source_workfile=str(publish_data.get("source_workfile") or ""),
        comment=str(publish_data.get("comment") or ""),
        status=str(publish_data.get("status") or "planned"),
    )
    write_publish_json(plan.version_dir, record)
    review_data = dict(plan.review_data)
    review_data.pop("publish", None)
    write_json(plan.review_json, review_data)
    _copy_ae_template_if_possible(plan, review_data)
    _update_latest_and_versions(plan.version_dir.parent, plan.version)
    return plan


def _copy_ae_template_if_possible(plan: ReviewPackagePlan, review_data: dict[str, Any]) -> None:
    project_root = review_data.get("project_root")
    pipeline_root = review_data.get("pipeline_root")
    if not project_root or not pipeline_root:
        return
    from smartlib.review.ae import copy_ae_template_to_publish

    copy_ae_template_to_publish(
        plan.version_dir,
        plan.version_dir.parents[3],
        project_root,
        pipeline_root,
        plan.subset,
    )


def _update_latest_and_versions(base_dir: Path, version: int) -> None:
    version_label = format_version(version)
    write_json(base_dir / "latest.json", {"version": version_label, "path": f"{version_label}/review.json"})
    versions_path = base_dir / "versions.json"
    versions = read_json(versions_path, [])
    if not isinstance(versions, list):
        versions = []
    next_versions = []
    found = False
    for item in versions:
        if item.get("version") == version_label:
            next_versions.append({"version": version_label, "status": "planned"})
            found = True
        else:
            status = "published" if item.get("status") == "latest" else item.get("status", "")
            next_versions.append({"version": item.get("version"), "status": status})
    if not found:
        next_versions.append({"version": version_label, "status": "planned"})
    write_json(versions_path, next_versions)


def _outputs_for_layer(layer_name: str, layer: dict[str, Any]) -> list[str]:
    outputs = layer.get("outputs")
    if isinstance(outputs, list) and outputs:
        return [str(item) for item in outputs]
    return list(DEFAULT_REVIEW_OUTPUTS.get(str(layer_name).upper(), ["beauty"]))


def _take_label(value: Any) -> str:
    try:
        take = int(value)
    except (TypeError, ValueError):
        take = 1
    return f"take{take:03d}"
