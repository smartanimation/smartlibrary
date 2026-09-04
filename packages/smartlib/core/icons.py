from __future__ import annotations

from pathlib import Path

from smartlib.core.config_loader import pipeline_root


_TOOL_ICON_NAMES = {
    "asset_manager": "asset_manager",
    "build_manager": "build_manager",
    "review_build_manager": "build_manager",
    "shot_manager": "shot_manager",
    "smart_ae_browser": "smart_ae_browser",
    "smart_casting": "smart_casting",
    "smart_delivery": "smart_delivery",
    "smart_editorial": "smart_editorial",
    "editorial_intake": "smart_editorial",
    "smart_ingest": "smart_ingest",
    "smart_launcher": "smart_launcher",
    "smart_review": "smart_review",
}

_MENU_ICON_SIZES = {16, 20, 40}

_ASSET_CATEGORY_ICON_NAMES = {
    "asset": "asset",
    "character": "character",
    "characters": "character",
    "char": "character",
    "environment": "environment",
    "environments": "environment",
    "env": "environment",
    "prop": "prop",
    "props": "prop",
    "vehicle": "vehicle",
    "vehicles": "vehicle",
}

_SHOT_DATA_ICON_NAMES = {
    "animation_curve": "animation_curves",
    "animation_curves": "animation_curves",
    "camera": "camera",
    "light": "light",
    "render_manifest": "playblast_settings",
    "playblast_settings": "playblast_settings",
    "review_layers": "review_layers",
    "set_dress_data": "set_dress_work_data",
    "set_dress_work_data": "set_dress_work_data",
}

_BUILD_CONTENT_ICON_NAMES = {
    "audio": "audio",
    "editorial_timing": "editorial_timing",
    "layout_overlay": "layout_overlay",
    "placement": "placement",
    "placements": "placement",
    "rig": "rig",
}

_BUILD_CONTENT_SHOT_DATA_TYPES = {
    "animation_curve": "animation_curve",
    "animation_curves": "animation_curve",
    "camera": "camera",
    "virtual_camera": "camera",
    "light": "light",
    "set_dress": "set_dress_data",
    "set_dress_data": "set_dress_data",
}


def tool_icon_path(tool_id: str, size: int | str = 20) -> Path | None:
    """Resolve a bundled SmartPipeline tool icon through the canonical root."""

    icon_name = _TOOL_ICON_NAMES.get(str(tool_id or "").strip().lower())
    if not icon_name:
        return None
    try:
        normalized_size = int(size)
    except (TypeError, ValueError):
        normalized_size = 20
    variant = str(normalized_size) if normalized_size in _MENU_ICON_SIZES else "master"
    path = pipeline_root() / "resources" / "icons" / "tools" / "small" / variant / f"{icon_name}.png"
    return path if path.is_file() else None


def asset_category_icon_path(category: str, size: int = 20) -> Path | None:
    """Resolve an Asset Manager category icon through the canonical root."""

    icon_name = _ASSET_CATEGORY_ICON_NAMES.get(str(category or "").strip().lower())
    if not icon_name:
        return None
    variant = "20" if int(size) == 20 else "master"
    path = pipeline_root() / "resources" / "icons" / "asset_manager" / "categories" / variant / f"{icon_name}.png"
    return path if path.is_file() else None


def shot_data_icon_path(data_type: str, size: int = 28) -> Path | None:
    """Resolve a Shot Manager Data-type icon through the canonical root."""

    if str(data_type or "").strip().lower() in {"placement", "placements"}:
        # Reuse the map master; Qt renders it at the Data list icon size.
        return build_content_icon_path("placement", size=size)
    icon_name = _SHOT_DATA_ICON_NAMES.get(str(data_type or "").strip().lower())
    if not icon_name:
        return None
    variant = "28" if int(size) == 28 else "master"
    path = pipeline_root() / "resources" / "icons" / "shot_manager" / "data" / variant / f"{icon_name}.png"
    return path if path.is_file() else None


def build_content_icon_path(component_type: str, size: int = 24) -> Path | None:
    """Resolve a Review Build Manager Build Contents type icon."""

    normalized_type = str(component_type or "").strip().lower()
    shot_data_type = _BUILD_CONTENT_SHOT_DATA_TYPES.get(normalized_type)
    if shot_data_type:
        return shot_data_icon_path(shot_data_type, size=28)
    icon_name = _BUILD_CONTENT_ICON_NAMES.get(normalized_type)
    if not icon_name:
        return None
    variant = "24" if int(size) == 24 else "master"
    path = (
        pipeline_root()
        / "resources"
        / "icons"
        / "review_build_manager"
        / "build_contents"
        / variant
        / f"{icon_name}.png"
    )
    return path if path.is_file() else None
