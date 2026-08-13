from __future__ import annotations

from pathlib import Path

from .models import CheckResult, PreflightContext, Severity


def scene_saved(adapter, _context: PreflightContext) -> CheckResult:
    path = adapter.scene_path()
    if not path:
        return _result(Severity.ERROR, "The Maya scene has not been saved.")
    return _result(Severity.PASS, Path(path).name)


def scene_unmodified(adapter, _context: PreflightContext) -> CheckResult:
    if adapter.scene_modified():
        return _result(Severity.WARNING, "The scene has unsaved changes.")
    return _result(Severity.PASS, "The scene is saved.")


def maya_version(adapter, context: PreflightContext) -> CheckResult:
    actual = str(adapter.maya_version())
    expected = context.metadata.get("policy", {}).get("maya_versions") or []
    if isinstance(expected, str):
        expected = [expected]
    if expected and not any(actual.startswith(str(version)) for version in expected):
        return _result(Severity.ERROR, f"Maya {actual} is not allowed; expected {', '.join(map(str, expected))}.")
    return _result(Severity.PASS, f"Maya {actual}")


def linear_unit(adapter, context: PreflightContext) -> CheckResult:
    actual = str(adapter.linear_unit())
    expected = str(context.metadata.get("policy", {}).get("linear_unit") or "")
    if expected and actual != expected:
        return _result(Severity.ERROR, f"Linear unit is {actual}; expected {expected}.")
    return _result(Severity.PASS, actual)


def missing_references(adapter, _context: PreflightContext) -> CheckResult:
    missing = tuple(adapter.missing_references())
    if missing:
        return _result(Severity.ERROR, f"{len(missing)} references are missing.", missing)
    return _result(Severity.PASS, "All references are available.")


def unknown_nodes(adapter, _context: PreflightContext) -> CheckResult:
    nodes = tuple(adapter.unknown_nodes())
    if nodes:
        return _result(Severity.WARNING, f"{len(nodes)} unknown nodes were found.", nodes)
    return _result(Severity.PASS, "No unknown nodes were found.")


def non_manifold_geometry(adapter, _context: PreflightContext) -> CheckResult:
    nodes = tuple(adapter.non_manifold_meshes())
    if nodes:
        return _result(Severity.ERROR, "Non-manifold edges were found.", nodes)
    return _result(Severity.PASS, "No non-manifold geometry was found.")


def asset_root(adapter, _context: PreflightContext) -> CheckResult:
    roots = tuple(adapter.asset_roots())
    if len(roots) != 1:
        return _result(Severity.ERROR, "Exactly one asset root is required.", roots)
    return _result(Severity.PASS, roots[0], roots)


def renderable_camera(adapter, _context: PreflightContext) -> CheckResult:
    cameras = tuple(adapter.renderable_cameras())
    if not cameras:
        return _result(Severity.ERROR, "No publish camera is renderable.")
    if len(cameras) > 1:
        return _result(Severity.WARNING, "Multiple renderable cameras were found.", cameras)
    return _result(Severity.PASS, cameras[0], cameras)


def frame_range(adapter, context: PreflightContext) -> CheckResult:
    current = tuple(int(value) for value in adapter.frame_range())
    expected = tuple(int(value) for value in context.metadata.get("frame_range", ()))
    if len(expected) == 2 and current != expected:
        return _result(
            Severity.ERROR,
            f"Scene range {current[0]}-{current[1]} does not match {expected[0]}-{expected[1]}.",
        )
    return _result(Severity.PASS, f"Frames {current[0]}-{current[1]}")


def animation_curves(adapter, _context: PreflightContext) -> CheckResult:
    count = int(adapter.animation_curve_count())
    if count <= 0:
        return _result(Severity.WARNING, "No animation curves were found.")
    return _result(Severity.PASS, f"{count} animation curves")


def cast_assets_exist(adapter, context: PreflightContext) -> CheckResult:
    issues = tuple(adapter.missing_cast(context))
    if issues:
        return _result(
            Severity.WARNING,
            f"{len(issues)} Cast assets are not loaded. This is allowed for partial work.",
            issues,
        )
    count = len(context.metadata.get("cast") or {})
    return _result(Severity.PASS, f"{count} Cast assets are present.")


def cast_versions(adapter, context: PreflightContext) -> CheckResult:
    issues = tuple(adapter.cast_version_issues(context))
    if issues:
        return _result(Severity.ERROR, "Cast publish versions do not match.", issues)
    return _result(Severity.PASS, "Cast publish versions match the Cast specification.")


def namespace_duplicates(adapter, context: PreflightContext) -> CheckResult:
    duplicates = tuple(adapter.duplicate_namespaces(context))
    if duplicates:
        return _result(Severity.ERROR, "Duplicate Cast namespaces were found.", duplicates)
    return _result(Severity.PASS, "Cast namespaces are unique.")


def resolution(adapter, context: PreflightContext) -> CheckResult:
    current = tuple(int(value) for value in adapter.resolution())
    expected = tuple(int(value) for value in context.metadata.get("resolution", ()))
    if len(expected) == 2 and current != expected:
        return _result(
            Severity.ERROR,
            f"Resolution {current[0]} x {current[1]} does not match {expected[0]} x {expected[1]}.",
        )
    return _result(Severity.PASS, f"{current[0]} x {current[1]}")


def camera_film_fit(adapter, _context: PreflightContext) -> CheckResult:
    invalid = tuple(adapter.non_horizontal_cameras())
    if invalid:
        return _result(Severity.ERROR, "Fit Resolution Gate must be Horizontal.", invalid)
    return _result(Severity.PASS, "Renderable camera Film Fit is Horizontal.")


def all_rig_set(adapter, _context: PreflightContext) -> CheckResult:
    return _required_set(adapter, "allRigSet")


def cache_geo_set(adapter, _context: PreflightContext) -> CheckResult:
    asset_profile = _asset_profile(_context)
    if _is_background_asset(_context):
        return _result(Severity.PASS, f"Skipped for background profile: {asset_profile}")
    return _required_set(adapter, "cache_geo_set")


def _asset_profile(context: PreflightContext) -> str:
    return str(
        context.metadata.get("preflight_profile")
        or context.metadata.get("asset_type")
        or context.metadata.get("category")
        or "default"
    ).casefold()


def _is_background_asset(context: PreflightContext) -> bool:
    background = {
        str(value).casefold()
        for value in context.metadata.get("policy", {}).get("background_categories", ())
    }
    return _asset_profile(context) in background | {"background", "environment", "bg"}


def no_asset_cameras(adapter, _context: PreflightContext) -> CheckResult:
    cameras = tuple(adapter.non_default_cameras())
    if cameras:
        return _result(Severity.ERROR, "Asset scenes must not contain custom cameras.", cameras)
    return _result(Severity.PASS, "Only Maya default cameras are present.")


def empty_display_layers(adapter, _context: PreflightContext) -> CheckResult:
    members = tuple(adapter.nonempty_display_layers())
    if members:
        return _result(Severity.ERROR, "Non-empty Display Layers were found.", members)
    return _result(Severity.PASS, "Display Layers contain no members.")


def publish_geometry_visibility(adapter, context: PreflightContext) -> CheckResult:
    if _is_background_asset(context):
        return _result(
            Severity.PASS,
            f"Skipped for background profile: {_asset_profile(context)}",
        )
    if not adapter.object_set_exists("cache_geo_set"):
        return _result(Severity.ERROR, "Required objectSet was not found: cache_geo_set")
    issues = tuple(adapter.publish_geometry_visibility_issues("cache_geo_set"))
    if issues:
        return _result(Severity.ERROR, "Publish geometry in cache_geo_set is hidden.", issues)
    return _result(Severity.PASS, "All cache_geo_set geometry is visible.")


def no_asset_lights(adapter, _context: PreflightContext) -> CheckResult:
    lights = tuple(adapter.asset_lights())
    if lights:
        return _result(Severity.ERROR, "Asset scenes must not contain lights.", lights)
    return _result(Severity.PASS, "No lights are present.")


def no_asset_references(adapter, _context: PreflightContext) -> CheckResult:
    references = tuple(row["path"] for row in adapter.reference_records())
    if references:
        return _result(Severity.ERROR, "Asset scenes must not contain references.", references)
    return _result(Severity.PASS, "The asset scene contains no references.")


def meshes_have_uvs(adapter, _context: PreflightContext) -> CheckResult:
    meshes = tuple(adapter.meshes_without_uvs())
    if meshes:
        return _result(Severity.ERROR, "Meshes without UVs were found.", meshes)
    return _result(Severity.PASS, "All meshes have UVs.")


def texture_files_exist(adapter, _context: PreflightContext) -> CheckResult:
    nodes = tuple(adapter.missing_texture_nodes())
    if nodes:
        return _result(Severity.ERROR, "Texture files could not be resolved.", nodes)
    return _result(Severity.PASS, "All texture files exist.")


def no_local_texture_paths(adapter, _context: PreflightContext) -> CheckResult:
    nodes = tuple(adapter.local_texture_nodes())
    if nodes:
        return _result(Severity.ERROR, "Local user texture paths were found.", nodes)
    return _result(Severity.PASS, "No local user texture paths are used.")


def textures_inside_project(adapter, context: PreflightContext) -> CheckResult:
    project_root = str(context.metadata.get("policy", {}).get("project_root") or "")
    nodes = tuple(adapter.outside_project_texture_nodes(project_root))
    if nodes:
        return _result(Severity.ERROR, "Texture paths outside the project were found.", nodes)
    if not project_root:
        return _result(Severity.WARNING, "Project root is not configured; texture scope was not checked.")
    return _result(Severity.PASS, "All absolute texture paths are inside the project.")


def valid_node_names(adapter, context: PreflightContext) -> CheckResult:
    forbidden = context.metadata.get("policy", {}).get("forbidden_name_characters") or []
    nodes = tuple(adapter.invalid_node_names(forbidden))
    if nodes:
        return _result(Severity.ERROR, "Node names contain whitespace, non-ASCII, or forbidden characters.", nodes)
    return _result(Severity.PASS, "Node names use allowed characters.")


def no_asset_namespaces(adapter, _context: PreflightContext) -> CheckResult:
    namespaces = tuple(adapter.asset_namespaces())
    if namespaces:
        return _result(Severity.ERROR, "Namespaces remain in the asset scene.", namespaces)
    return _result(Severity.PASS, "No custom namespaces remain.")


def references_inside_project(adapter, context: PreflightContext) -> CheckResult:
    project_root = str(context.metadata.get("policy", {}).get("project_root") or "")
    if not project_root:
        return _result(Severity.WARNING, "Project root is not configured; reference scope was not checked.")
    references = tuple(adapter.outside_project_references(project_root))
    if references:
        return _result(Severity.ERROR, "Reference paths outside the project were found.", references)
    return _result(Severity.PASS, "All reference paths are inside the project.")


def _required_set(adapter, name: str) -> CheckResult:
    if not adapter.object_set_exists(name):
        return _result(Severity.ERROR, f"Required objectSet was not found: {name}")
    members = tuple(adapter.object_set_members(name))
    if not members:
        return _result(Severity.ERROR, f"Required objectSet is empty: {name}")
    return _result(Severity.PASS, f"{name}: {len(members)} members", members)


def _result(
    severity: Severity,
    message: str,
    nodes: tuple[str, ...] = (),
) -> CheckResult:
    return CheckResult("", "", severity, message, nodes)
