from __future__ import annotations

from . import checks
from .models import CheckDefinition, OutputDefinition, PreflightContext, PreflightProfile


COMMON_CHECKS = (
    CheckDefinition("scene_saved", "Scene saved", checks.scene_saved),
    CheckDefinition("scene_unmodified", "Unsaved changes", checks.scene_unmodified),
    CheckDefinition("maya_version", "Maya version", checks.maya_version),
    CheckDefinition("linear_unit", "Linear unit", checks.linear_unit),
    CheckDefinition("missing_references", "Missing references", checks.missing_references),
    CheckDefinition("unknown_nodes", "Unknown nodes", checks.unknown_nodes),
)


def create_asset_profile() -> PreflightProfile:
    return PreflightProfile(
        key="asset",
        label="Asset",
        publish_label="Publish Asset Package",
        outputs=(
            OutputDefinition("maya_scene", "Maya Scene", "Current asset scene", required=True),
            OutputDefinition("geometry", "Geometry", "Model geometry", selected=True),
            OutputDefinition("thumbnail", "Thumbnail", "Asset preview", selected=True),
            OutputDefinition("metadata", "Metadata", "Asset manifest", required=True),
        ),
        checks=COMMON_CHECKS + (
            CheckDefinition("asset_root", "Asset root", checks.asset_root),
            CheckDefinition("no_asset_cameras", "Custom cameras", checks.no_asset_cameras),
            CheckDefinition("empty_display_layers", "Display Layers", checks.empty_display_layers),
            CheckDefinition(
                "publish_geometry_visibility",
                "Publish Geometry Visibility",
                checks.publish_geometry_visibility,
                outputs=("geometry",),
            ),
            CheckDefinition("no_asset_lights", "Lights", checks.no_asset_lights),
            CheckDefinition("no_asset_references", "Asset references", checks.no_asset_references),
            CheckDefinition("meshes_have_uvs", "Missing UVs", checks.meshes_have_uvs),
            CheckDefinition("texture_files_exist", "Missing textures", checks.texture_files_exist),
            CheckDefinition("no_local_texture_paths", "Local texture paths", checks.no_local_texture_paths),
            CheckDefinition("textures_inside_project", "Project texture paths", checks.textures_inside_project),
            CheckDefinition("valid_node_names", "Node naming", checks.valid_node_names),
            CheckDefinition("no_asset_namespaces", "Asset namespaces", checks.no_asset_namespaces),
            CheckDefinition("all_rig_set", "allRigSet", checks.all_rig_set),
            CheckDefinition(
                "cache_geo_set",
                "cache_geo_set",
                checks.cache_geo_set,
                outputs=("geometry",),
            ),
            CheckDefinition(
                "non_manifold_geometry",
                "Non-manifold geometry",
                checks.non_manifold_geometry,
                outputs=("geometry",),
            ),
        ),
    )


def create_shot_profile() -> PreflightProfile:
    return PreflightProfile(
        key="shot",
        label="Shot",
        publish_label="Publish Shot Package",
        outputs=(
            OutputDefinition("maya_scene", "Maya Scene", "Current shot scene", required=True),
            OutputDefinition("alembic", "Alembic Cache", "Animated cast", selected=True),
            OutputDefinition("playblast", "Playblast", "Review movie", selected=True),
            OutputDefinition("metadata", "Metadata", "Frame range and dependencies", required=True),
        ),
        checks=COMMON_CHECKS + (
            CheckDefinition(
                "references_inside_project",
                "Project reference paths",
                checks.references_inside_project,
            ),
            CheckDefinition("cast_assets_exist", "Cast coverage", checks.cast_assets_exist),
            CheckDefinition("cast_versions", "Cast publish versions", checks.cast_versions),
            CheckDefinition("namespace_duplicates", "Namespace duplicates", checks.namespace_duplicates),
            CheckDefinition("frame_range", "Frame range", checks.frame_range),
            CheckDefinition("resolution", "Resolution", checks.resolution),
            CheckDefinition(
                "renderable_camera",
                "Renderable camera",
                checks.renderable_camera,
                outputs=("playblast",),
            ),
            CheckDefinition(
                "camera_film_fit",
                "Fit Resolution Gate",
                checks.camera_film_fit,
                outputs=("playblast",),
            ),
            CheckDefinition(
                "animation_curves",
                "Animation curves",
                checks.animation_curves,
                outputs=("alembic",),
            ),
        ),
    )


def profile_for_context(context: PreflightContext) -> PreflightProfile:
    if context.kind.lower() == "asset":
        return create_asset_profile()
    return create_shot_profile()
