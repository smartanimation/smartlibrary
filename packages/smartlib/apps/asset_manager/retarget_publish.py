"""Versioned Retarget profile publishing for Asset Manager."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path


REQUIRED_PROFILE_KEYS = (
    "asset",
    "mcr_scene",
    "animation_rig_scene",
    "source_skeleton",
    "transfer_nodes",
)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return path


def retarget_publish_root(asset_root: Path, variant: str = "default") -> Path:
    return Path(asset_root) / variant / "publish" / "retarget"


def project_test_motion_root(project_root: Path) -> Path:
    return Path(project_root) / "library" / "anim" / "retarget" / "test_motion"


def standard_test_motion(project_root: Path) -> Path:
    root = project_test_motion_root(project_root)
    manifest = read_json(root / "manifest.json", {}) or {}
    configured = str(manifest.get("file") or "").strip()
    if configured and (root / configured).is_file():
        return root / configured
    candidates = sorted(root.glob("humanoid_retarget_test_v*.fbx"))
    return candidates[-1] if candidates else root / "humanoid_retarget_test_v001.fbx"


def validate_retarget_profile(profile_path: Path, *, project_root: Path, test_motion: bool) -> dict:
    profile_path = Path(profile_path)
    errors: list[str] = []
    warnings: list[str] = []
    profile = {}
    if not profile_path.is_file():
        errors.append(f"Retarget profile was not found: {profile_path}")
    else:
        try:
            profile = read_json(profile_path, {}) or {}
        except (OSError, ValueError) as exc:
            errors.append(f"Could not read Retarget profile: {exc}")
    for key in REQUIRED_PROFILE_KEYS:
        if not profile.get(key):
            errors.append(f"Profile is missing required field: {key}")
    for key in ("mcr_scene", "animation_rig_scene"):
        value = str(profile.get(key) or "").strip()
        dependency_path = Path(value)
        if value and not dependency_path.is_absolute():
            dependency_path = profile_path.parent / dependency_path
        if value and not dependency_path.is_file():
            errors.append(f"Profile dependency does not exist: {key}={value}")
    test_path = standard_test_motion(project_root)
    test_result = {"enabled": bool(test_motion), "path": test_path.as_posix(), "status": "skipped"}
    if test_motion:
        if not test_path.is_file():
            errors.append(f"Standard Test Motion was not found: {test_path}")
            test_result["status"] = "failed"
        else:
            inventory_path = test_path.parent / "validation_inventory.json"
            inventory = read_json(inventory_path, []) or []
            entry = inventory[0] if isinstance(inventory, list) and inventory else {}
            joint_count = len(entry.get("joints") or []) if isinstance(entry, dict) else 0
            animated_count = len(entry.get("animated") or []) if isinstance(entry, dict) else 0
            if joint_count < 20 or animated_count < 1:
                warnings.append("Test Motion inventory is missing or incomplete; the FBX exists but was not deeply verified.")
            test_result.update({"status": "passed", "joint_count": joint_count, "animated_nodes": animated_count})
    return {
        "status": "failed" if errors else ("warning" if warnings else "passed"),
        "profile": profile_path.as_posix(),
        "errors": errors,
        "warnings": warnings,
        "test_motion": test_result,
        "validated_at": datetime.now().isoformat(timespec="seconds"),
    }


def list_retarget_versions(asset_root: Path, variant: str = "default") -> list[dict]:
    root = retarget_publish_root(asset_root, variant)
    versions = []
    for directory in sorted(root.glob("v[0-9][0-9][0-9]"), reverse=True):
        manifest = read_json(directory / "publish.json", {}) or {}
        versions.append({
            "version": directory.name,
            "path": directory,
            "published_at": str(manifest.get("published_at") or ""),
            "comment": str(manifest.get("comment") or ""),
            "test_status": str((manifest.get("test_motion") or {}).get("status") or ""),
        })
    return versions


def publish_retarget_profile(
    asset_root: Path,
    variant: str,
    profile_path: Path,
    *,
    project_root: Path,
    comment: str = "",
    run_test_motion: bool = True,
) -> dict:
    validation = validate_retarget_profile(profile_path, project_root=project_root, test_motion=run_test_motion)
    if validation["errors"]:
        raise ValueError("\n".join(validation["errors"]))
    root = retarget_publish_root(asset_root, variant)
    versions = list_retarget_versions(asset_root, variant)
    next_number = max([int(item["version"][1:]) for item in versions] or [0]) + 1
    version = f"v{next_number:03d}"
    version_dir = root / version
    if version_dir.exists():
        raise FileExistsError(f"Retarget publish already exists: {version_dir}")
    version_dir.mkdir(parents=True)
    target_profile = version_dir / f"{Path(asset_root).name}_retarget.json"
    shutil.copy2(profile_path, target_profile)
    write_json(version_dir / "validation.json", validation)
    manifest = {
        "schema_version": 1,
        "publish_type": "retarget",
        "asset": Path(asset_root).name,
        "variant": variant,
        "version": next_number,
        "profile": target_profile.name,
        "source_profile": Path(profile_path).as_posix(),
        "test_motion": validation["test_motion"],
        "validation_status": validation["status"],
        "comment": comment,
        "published_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(version_dir / "publish.json", manifest)
    write_json(root / "latest.json", {"version": version, "publish": f"{version}/publish.json", "profile": f"{version}/{target_profile.name}"})
    write_json(root / "versions.json", {"versions": [item["version"] for item in list_retarget_versions(asset_root, variant)]})
    return {"version": version, "directory": version_dir, "profile": target_profile, "validation": validation}
