from __future__ import annotations

from . import checks
from .models import CheckDefinition, OutputDefinition, PreflightContext, PreflightProfile


COMMON_CHECKS = (
    CheckDefinition("scene_saved", "Scene saved", checks.scene_saved),
    CheckDefinition("scene_unmodified", "Unsaved changes", checks.scene_unmodified),
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
