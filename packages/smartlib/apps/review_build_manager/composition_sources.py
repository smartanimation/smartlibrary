"""Discover the exact Maya Constructs recorded by completed Review submissions."""
from pathlib import Path
import re

from smartlib.core.metadata import read_json
from smartlib.apps.shot_manager.animation_publish import file_hash

SOURCE_SCHEMA = "smartpipeline.review_animation_source.v1"


def list_submitted_builds(manager, identity, *, department="anim", task=""):
    paths = manager.shots.paths
    workflow = manager.review_workflow(identity)
    expected = {"episode": identity.episode, "sequence": identity.sequence, "shot": identity.shot}
    roots = {
        workflow.review_destination_root(
            department, profile, manager.review_profiles.delivery_profile(profile)
        )
        for profile in manager.delivery_profile_ids()
    }
    rows = []
    for root in sorted(roots):
        for directory in root.glob("v*"):
            if not directory.is_dir() or not re.fullmatch(r"v[0-9]{3,}", directory.name):
                continue
            review_path = paths.artifact_file(directory, "review.json")
            source_path = paths.artifact_file(directory, "source_manifest.json")
            try:
                review = read_json(review_path, {})
                source = read_json(source_path, {})
                if review.get("schema") != "smartpipeline.formal_review.v1":
                    continue
                if any(review.get(key) != value for key, value in expected.items()):
                    continue
                if str(review.get("department", "")).lower() != department.lower():
                    continue
                if review.get("state") not in {"SUBMITTED", "APPROVED"}:
                    continue
                if source.get("schema") != "smartpipeline.review_source_manifest.v1" or not source.get("construct"):
                    continue
                # Verification of a published Snapshot is downstream of Animation.
                # It must not become an animator source and create a publish cycle.
                if source.get("composition_snapshot"):
                    continue
                scene = paths.project_dependency(source["construct"])
                manifest_path = paths.artifact_file(scene.parent, "build_manifest.json")
                validation_path = paths.artifact_file(scene.parent, "validation.json")
                manifest = read_json(manifest_path, {})
                validation = read_json(validation_path, {})
                source_task = str(manifest.get("task") or "")
                if task and source_task and source_task.lower() != task.lower():
                    continue
                reason = ""
                if scene.suffix.lower() not in {".ma", ".mb"} or not scene.is_file():
                    reason = "Construct scene is missing"
                elif manifest.get("format") != "smartpipeline.scene_build_manifest":
                    reason = "Construct Build Manifest is missing"
                elif any(manifest.get(key) != value for key, value in expected.items()):
                    reason = "Construct belongs to a different shot"
                elif str(manifest.get("department", "")).lower() != department.lower():
                    reason = "Construct belongs to a different department"
                elif not manifest.get("scene") or paths.project_dependency(manifest["scene"]) != scene:
                    reason = "Construct scene differs from its Build Manifest"
                elif manifest.get("status") != "validated" or str(validation.get("status") or "").lower() not in {"passed", "warning"}:
                    reason = "Construct validation has not passed"
                elif any(str(item.get("severity", "")).upper() == "ERROR" for item in validation.get("results", [])):
                    reason = "Construct validation has errors"
                rows.append({
                    "review_version": str(review.get("version") or directory.name),
                    "delivery_profile": str(review.get("profile") or ""),
                    "department": department, "task": source_task,
                    "build_version": scene.parent.name, "scene": str(scene),
                    "review_json": str(review_path), "source_manifest": str(source_path),
                    "build_manifest": str(manifest_path), "validation": str(validation_path),
                    "review_scene_sha256": str(source.get("construct_sha256") or ""),
                    "updated": str(review.get("created_at") or ""),
                    "state": "BLOCKED" if reason else "READY", "reason": reason,
                    "checksum_recorded": bool(source.get("construct_sha256")),
                })
            except (OSError, ValueError, TypeError, AttributeError):
                # A corrupt/uncommitted receipt never becomes a selectable source.
                continue
    return sorted(rows, key=lambda r: (r["updated"], r["review_version"]), reverse=True)


def resolve_submitted_build(manager, identity, source_manifest, *, department="anim", task=""):
    chosen = Path(source_manifest).resolve()
    row = next((r for r in list_submitted_builds(manager, identity, department=department, task=task)
                if Path(r["source_manifest"]).resolve() == chosen), None)
    if not row:
        raise ValueError("The selected Submit for Review record is no longer available. Refresh the list.")
    if row["state"] != "READY":
        raise ValueError(row["reason"])
    scene = Path(row["scene"]).resolve()
    digest = file_hash(scene)
    if row["review_scene_sha256"] and row["review_scene_sha256"] != digest:
        raise ValueError("The Construct has changed since Submit for Review. Submit a new Review Build.")
    result = {**row, "schema": SOURCE_SCHEMA, "scene": str(scene), "scene_sha256": digest}
    result["records"] = {
        key: {"path": str(Path(row[key]).resolve()), "sha256": file_hash(Path(row[key]))}
        for key in ("review_json", "source_manifest", "build_manifest", "validation")
    }
    return result


def validate_review_source(selection, scene):
    if selection.get("schema") != SOURCE_SCHEMA:
        raise ValueError("Unsupported Review source selection")
    if Path(selection["scene"]).resolve() != Path(scene).resolve():
        raise ValueError("Build scene differs from the selected Review Construct")
    if file_hash(Path(scene)) != selection.get("scene_sha256"):
        raise ValueError("Selected Review Construct changed before publishing")
    records = selection.get("records") or {}
    if set(records) != {"review_json", "source_manifest", "build_manifest", "validation"}:
        raise ValueError("Selected Review source records are incomplete")
    for record in records.values():
        path = Path(record["path"])
        if not path.is_file() or file_hash(path) != record.get("sha256"):
            raise ValueError("Selected Review record changed. Refresh and select it again.")
