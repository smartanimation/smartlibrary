"""Versioned Retarget profile publishing for Asset Manager."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from smartlib.retarget.profile import ProfileError, load_retarget_profile


REQUIRED_PROFILE_KEYS = (
    "asset",
    "mcr_scene",
    "animation_rig_scene",
    "source_skeleton",
    "transfer_nodes",
)

ANIMATION_RIG_SUBSETS = ("anim", "anm", "animation")
MOCAP_RIG_SUBSETS = ("mcp", "mcr", "mocap")


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


def _version_number(path: Path) -> int:
    match = re.fullmatch(r"v(\d+)", path.name, re.IGNORECASE)
    return int(match.group(1)) if match else -1


def _published_maya_scene(asset_root: Path, variant: str, subsets: tuple[str, ...]) -> Path | None:
    publish_root = Path(asset_root) / variant / "publish" / "asset"
    for subset in subsets:
        subset_root = publish_root / subset
        versions = sorted(
            (path for path in subset_root.glob("v*") if path.is_dir() and _version_number(path) >= 0),
            key=_version_number,
            reverse=True,
        )
        for version_dir in versions:
            manifest = read_json(version_dir / "publish.json", {}) or {}
            files = manifest.get("files") or {}
            for key in ("mb", "ma"):
                value = str(files.get(key) or "").strip()
                candidate = Path(value) if value else None
                if candidate and not candidate.is_absolute():
                    candidate = version_dir / candidate
                if candidate and candidate.is_file():
                    return candidate
            candidates = sorted(version_dir.glob("*.mb")) + sorted(version_dir.glob("*.ma"))
            if candidates:
                return candidates[0]
    return None


def resolve_retarget_context_rigs(asset_root: Path, variant: str = "default") -> dict:
    """Resolve ANM and MCR scenes from the latest packed asset representations."""
    return {
        "animation_rig_scene": _published_maya_scene(asset_root, variant, ANIMATION_RIG_SUBSETS),
        "mcr_scene": _published_maya_scene(asset_root, variant, MOCAP_RIG_SUBSETS),
    }


def project_retarget_template_root(project_root: Path) -> Path:
    return Path(project_root) / "library" / "anim" / "retarget" / "templates"


def list_project_retarget_templates(project_root: Path) -> list[Path]:
    root = project_retarget_template_root(project_root)
    return sorted(root.glob("*_v[0-9][0-9][0-9].json"), reverse=True) if root.is_dir() else []


def ensure_project_retarget_template(project_root: Path, bundled_template: Path) -> Path:
    """Return the latest project template, seeding it from the bundled default once."""
    templates = list_project_retarget_templates(project_root)
    if templates:
        return templates[0]
    bundled_template = Path(bundled_template)
    if not bundled_template.is_file():
        raise FileNotFoundError(f"Bundled Retarget template was not found: {bundled_template}")
    target = project_retarget_template_root(project_root) / bundled_template.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundled_template, target)
    return target


def generate_retarget_asset_profile(
    asset_root: Path,
    variant: str,
    *,
    project_root: Path,
    bundled_template: Path,
    output_path: Path | None = None,
    overwrite: bool = False,
) -> dict:
    """Generate an editable asset profile from Context Pack ANM/MCR publishes."""
    asset_root = Path(asset_root)
    rigs = resolve_retarget_context_rigs(asset_root, variant)
    missing = [key for key, value in rigs.items() if value is None]
    if missing:
        raise FileNotFoundError(
            "Context Pack is missing required Retarget representation(s): " + ", ".join(missing)
        )
    template_path = ensure_project_retarget_template(project_root, bundled_template)
    template = load_retarget_profile(template_path)
    template_id = str(template.get("template_id") or template_path.stem)
    template_version = str(template.get("template_version") or "v001")
    target = Path(output_path) if output_path else (
        asset_root / variant / "work" / "rig" / "retarget" / f"{asset_root.name}_retarget.json"
    )
    if target.exists() and not overwrite:
        raise FileExistsError(f"Retarget work profile already exists: {target}")
    payload = {
        "schema_version": 1,
        "profile_kind": "asset_profile",
        "asset": asset_root.name,
        "variant": variant,
        "template": {
            "id": template_id,
            "version": template_version,
            "path": template_path.resolve().as_posix(),
        },
        "mcr_scene": rigs["mcr_scene"].resolve().as_posix(),
        "animation_rig_scene": rigs["animation_rig_scene"].resolve().as_posix(),
    }
    write_json(target, payload)
    return {"profile": target, "template": template_path, **rigs}


def materialized_profile(profile_path: Path) -> dict:
    """Resolve inheritance and detach the snapshot from its template file."""
    profile = load_retarget_profile(profile_path)
    template = profile.get("template")
    if isinstance(template, dict):
        profile["template"] = {key: value for key, value in template.items() if key != "path"}
    return profile


def retarget_publish_root(asset_root: Path, variant: str = "default") -> Path:
    return Path(asset_root) / variant / "publish" / "retarget"


def retarget_data_root(asset_root: Path, variant: str = "default") -> Path:
    return Path(asset_root) / variant / "data" / "retarget"


def list_retarget_data_versions(asset_root: Path, variant: str = "default") -> list[dict]:
    root = retarget_data_root(asset_root, variant)
    versions = []
    for directory in sorted(root.glob("v[0-9][0-9][0-9]"), reverse=True):
        manifest = read_json(directory / "data.json", {}) or {}
        profile_name = str(manifest.get("profile") or f"{Path(asset_root).name}_retarget.json")
        versions.append({
            "version": directory.name,
            "path": directory,
            "profile": directory / profile_name,
            "created_at": str(manifest.get("created_at") or ""),
            "comment": str(manifest.get("comment") or ""),
        })
    return versions


def save_retarget_data_version(
    asset_root: Path,
    variant: str,
    profile_path: Path,
    *,
    comment: str = "",
) -> dict:
    profile_path = Path(profile_path)
    try:
        profile = materialized_profile(profile_path)
    except ProfileError as exc:
        raise ValueError(str(exc)) from exc
    root = retarget_data_root(asset_root, variant)
    versions = list_retarget_data_versions(asset_root, variant)
    next_number = max([int(item["version"][1:]) for item in versions] or [0]) + 1
    version = f"v{next_number:03d}"
    version_dir = root / version
    version_dir.mkdir(parents=True, exist_ok=False)
    target = version_dir / f"{Path(asset_root).name}_retarget.json"
    write_json(target, profile)
    manifest = {
        "schema_version": 1,
        "data_type": "retarget",
        "asset": Path(asset_root).name,
        "variant": variant,
        "version": next_number,
        "profile": target.name,
        "source_profile": profile_path.as_posix(),
        "comment": comment,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(version_dir / "data.json", manifest)
    write_json(root / "latest.json", {"version": version, "profile": f"{version}/{target.name}", "data": f"{version}/data.json"})
    write_json(root / "versions.json", {"versions": [item["version"] for item in list_retarget_data_versions(asset_root, variant)]})
    return {"version": version, "directory": version_dir, "profile": target, "manifest": manifest}


def latest_retarget_data_profile(asset_root: Path, variant: str = "default") -> Path | None:
    root = retarget_data_root(asset_root, variant)
    latest = read_json(root / "latest.json", {}) or {}
    relative = str(latest.get("profile") or "").strip()
    path = root / relative if relative else None
    return path if path and path.is_file() else None


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
            profile = load_retarget_profile(profile_path)
        except (OSError, ValueError, ProfileError) as exc:
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
    # Publish a self-contained snapshot so a later template update cannot alter it.
    write_json(target_profile, materialized_profile(profile_path))
    write_json(version_dir / "validation.json", validation)
    manifest = {
        "schema_version": 1,
        "publish_type": "retarget",
        "asset": Path(asset_root).name,
        "variant": variant,
        "version": next_number,
        "profile": target_profile.name,
        "source_profile": Path(profile_path).as_posix(),
        "source_data": _source_data_summary(profile_path, asset_root, variant),
        "test_motion": validation["test_motion"],
        "validation_status": validation["status"],
        "comment": comment,
        "published_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(version_dir / "publish.json", manifest)
    write_json(root / "latest.json", {"version": version, "publish": f"{version}/publish.json", "profile": f"{version}/{target_profile.name}"})
    write_json(root / "versions.json", {"versions": [item["version"] for item in list_retarget_versions(asset_root, variant)]})
    return {"version": version, "directory": version_dir, "profile": target_profile, "validation": validation}


def _source_data_summary(profile_path: Path, asset_root: Path, variant: str) -> dict:
    profile_path = Path(profile_path)
    root = retarget_data_root(asset_root, variant)
    try:
        relative = profile_path.resolve().relative_to(root.resolve())
    except ValueError:
        return {"version": "", "path": profile_path.as_posix()}
    version = relative.parts[0] if relative.parts and relative.parts[0].lower().startswith("v") else ""
    return {"version": version, "path": profile_path.as_posix()}
