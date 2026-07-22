from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


bl_info = {
    "name": "Smart Asset Panel",
    "author": "SmartPipeline",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > SMART ASSET",
    "description": "Open/save asset work scenes and import/export FBX data.",
    "category": "Pipeline",
}


try:
    import bpy
except ImportError:  # Allows syntax checks outside Blender.
    bpy = None


_MANAGER = None
_ASSETS = []
MODEL_FBX_FILENAME = "model.fbx"
MODEL_FBX_TEXTURE_DIRNAME = "model.fbm"


def _root() -> Path:
    return Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or Path(__file__).resolve().parents[4]
    )


def _config_dir() -> Path:
    return Path(os.environ.get("PROJECT_CONFIG_DIR") or _root() / "config" / "STKB")


def _ensure_paths() -> None:
    root = _root()
    for path in (root / "packages", root / "scripts", root):
        text = str(path).replace("\\", "/")
        if text not in sys.path:
            sys.path.insert(0, text)


def _manager():
    global _MANAGER
    if _MANAGER is None:
        _ensure_paths()
        from asset_manager import AssetManager

        _MANAGER = AssetManager(_config_dir())
    return _MANAGER


def _assets():
    global _ASSETS
    if not _ASSETS:
        _ASSETS = _manager().list_assets_from_sheet(fallback_to_filesystem=True)
    return _ASSETS


def _refresh_assets() -> None:
    global _ASSETS
    _ASSETS = _manager().list_assets_from_sheet(fallback_to_filesystem=True)


def _current_asset(props: Any):
    manager = _manager()
    if not props.category or not props.group or not props.asset:
        return None
    return manager.get_asset(props.category, props.group, props.asset)


def _enum(values: list[str]) -> list[tuple[str, str, str]]:
    if not values:
        return [("", "", "")]
    return [(value, value, "") for value in values]


def _category_items(_self, _context):
    return _enum(sorted({asset.category for asset in _assets()}))


def _group_items(self, _context):
    category = self.category
    groups = sorted({asset.group for asset in _assets() if not category or asset.category == category})
    return _enum(groups)


def _asset_items(self, _context):
    category = self.category
    group = self.group
    names = sorted(
        {
            asset.name
            for asset in _assets()
            if (not category or asset.category == category)
            and (not group or asset.group == group)
        }
    )
    return _enum(names)


def _subset_items(self, _context):
    asset = _current_asset(self)
    manager = _manager()
    subsets = manager.work_subsets("model", asset=asset, dcc="blender")
    if asset:
        subsets = sorted(set(subsets) | set(_model_fbx_data_subsets(asset)))
    return _enum(subsets or ["main"])


def _model_fbx_data_subsets(asset) -> list[str]:
    subsets = []
    for variant in _manager().asset_variants(asset):
        if asset.uses_variant_structure(variant):
            base = asset.variant_root(variant) / "data" / "model"
        else:
            base = asset.data_dir / "model"
        if not base.exists():
            continue
        for path in base.iterdir():
            if path.is_dir() and (path / "fbx").exists():
                subsets.append(path.name)
    return sorted(set(subsets))


def _geo_version_items(self, _context):
    asset = _current_asset(self)
    if not asset:
        return [("latest", "Latest", "")]
    versions = ["latest"]
    base = _manager().data_base_dir(
        asset,
        department="geo",
        variant="default",
        subset=self.subset,
        data_format="fbx",
    )
    if base.exists():
        versions.extend(
            sorted(
                [
                    path.name
                    for path in base.iterdir()
                    if path.is_dir() and path.name.lower().startswith("v")
                ],
                reverse=True,
            )
        )
    return [(version, "Latest" if version == "latest" else version, "") for version in versions]


def _work_files(asset, subset: str) -> list[Path]:
    return _manager().list_work_files(
        asset,
        dcc="blender",
        department="model",
        variant="default",
        subset=subset,
        extensions=["blend"],
    )


def _latest_work_file(asset, subset: str) -> Path | None:
    latest = None
    latest_key = (-1, -1)
    for path in _work_files(asset, subset):
        parsed = _manager().parse_work_file(path)
        if not parsed:
            continue
        key = (int(parsed.get("version") or 0), int(parsed.get("take") or 0))
        if key > latest_key:
            latest_key = key
            latest = path
    return latest


def _current_blend_path() -> str:
    if bpy is None:
        return ""
    return str(Path(bpy.data.filepath)) if bpy.data.filepath else ""


def _data_latest_path(asset, subset: str, version: str) -> Path | None:
    manager = _manager()
    base = manager.data_base_dir(
        asset,
        department="geo",
        variant="default",
        subset=subset,
        data_format="fbx",
    )
    if version == "latest":
        latest_json = base / "latest.json"
        if not latest_json.exists():
            return None
        with latest_json.open("r", encoding="utf-8") as stream:
            latest = json.load(stream) or {}
        path = latest.get("path")
        return base / path if path else None
    candidate = base / version / "geo.fbx"
    return candidate if candidate.exists() else None


def _report(operator, level: set[str], message: str) -> None:
    operator.report(level, message)


if bpy is not None:

    def _selected_texture_images(objects) -> list:
        images = []
        seen = set()
        for obj in objects:
            for slot in getattr(obj, "material_slots", []):
                material = getattr(slot, "material", None)
                node_tree = getattr(material, "node_tree", None)
                if node_tree is None:
                    continue
                for node in node_tree.nodes:
                    image = getattr(node, "image", None)
                    if image is None or image.name in seen:
                        continue
                    seen.add(image.name)
                    images.append(image)
        return images


    def _unique_texture_path(directory: Path, filename: str) -> Path:
        path = directory / filename
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        index = 2
        while True:
            candidate = directory / f"{stem}_{index:02d}{suffix}"
            if not candidate.exists():
                return candidate
            index += 1


    def _image_source_path(image) -> Path | None:
        filepath = getattr(image, "filepath", "") or getattr(image, "filepath_raw", "")
        if not filepath:
            return None
        try:
            path = Path(bpy.path.abspath(filepath, library=getattr(image, "library", None)))
        except TypeError:
            path = Path(bpy.path.abspath(filepath))
        return path if path.exists() else None


    def _copy_selected_textures(objects, texture_dir: Path) -> tuple[list[tuple[Any, str]], list[Path]]:
        texture_dir.mkdir(parents=True, exist_ok=True)
        restore_paths = []
        copied = []
        for image in _selected_texture_images(objects):
            source = _image_source_path(image)
            filename = Path(getattr(image, "filepath", "") or image.name).name
            if not filename:
                filename = f"{image.name}.png"
            if not Path(filename).suffix:
                filename = f"{filename}.png"
            target = _unique_texture_path(texture_dir, filename)
            if source:
                shutil.copy2(source, target)
            elif getattr(image, "packed_file", None):
                original_raw = image.filepath_raw
                image.filepath_raw = str(target)
                try:
                    image.save()
                finally:
                    image.filepath_raw = original_raw
            else:
                continue
            restore_paths.append((image, image.filepath))
            image.filepath = str(target)
            copied.append(target)
        return restore_paths, copied


    def _restore_image_paths(restore_paths: list[tuple[Any, str]]) -> None:
        for image, filepath in restore_paths:
            image.filepath = filepath

    class SmartAssetProperties(bpy.types.PropertyGroup):
        category: bpy.props.EnumProperty(name="category", items=_category_items)
        group: bpy.props.EnumProperty(name="group", items=_group_items)
        asset: bpy.props.EnumProperty(name="asset", items=_asset_items)
        subset: bpy.props.EnumProperty(name="subset", items=_subset_items)
        geo_version: bpy.props.EnumProperty(name="Version", items=_geo_version_items, default=None)


    class SMARTASSET_OT_refresh(bpy.types.Operator):
        bl_idname = "smart_asset.refresh"
        bl_label = "Refresh Smart Assets"

        def execute(self, context):
            _refresh_assets()
            _report(self, {"INFO"}, "Smart assets refreshed")
            return {"FINISHED"}


    class SMARTASSET_OT_open_work(bpy.types.Operator):
        bl_idname = "smart_asset.open_work"
        bl_label = "OPEN"

        def execute(self, context):
            props = context.scene.smart_asset
            asset = _current_asset(props)
            if not asset:
                _report(self, {"WARNING"}, "Select an asset first")
                return {"CANCELLED"}
            path = _latest_work_file(asset, props.subset)
            if not path:
                _report(self, {"WARNING"}, "No Blender work file was found")
                return {"CANCELLED"}
            bpy.ops.wm.open_mainfile(filepath=str(path))
            _report(self, {"INFO"}, f"Opened: {path.name}")
            return {"FINISHED"}


    class SMARTASSET_OT_save_work(bpy.types.Operator):
        bl_idname = "smart_asset.save_work"
        bl_label = "SAVE"

        def execute(self, context):
            props = context.scene.smart_asset
            asset = _current_asset(props)
            if not asset:
                _report(self, {"WARNING"}, "Select an asset first")
                return {"CANCELLED"}
            current = _current_blend_path()
            manager = _manager()
            if current:
                target = manager.next_work_take_path(
                    asset,
                    current_path=current,
                    dcc="blender",
                    department="model",
                    variant="default",
                    subset=props.subset,
                    ext="blend",
                )
            else:
                target = manager.next_work_take_path(
                    asset,
                    dcc="blender",
                    department="model",
                    variant="default",
                    subset=props.subset,
                    version=1,
                    ext="blend",
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=str(target))
            manager.update_file_metadata(
                target,
                comment="",
                scene_info={
                    "dcc": "blender",
                    "file": str(target).replace("\\", "/"),
                },
            )
            _report(self, {"INFO"}, f"Saved: {target.name}")
            return {"FINISHED"}


    class SMARTASSET_OT_export_geo(bpy.types.Operator):
        bl_idname = "smart_asset.export_geo"
        bl_label = "Export geo"

        def execute(self, context):
            props = context.scene.smart_asset
            asset = _current_asset(props)
            if not asset:
                _report(self, {"WARNING"}, "Select an asset first")
                return {"CANCELLED"}
            if not context.selected_objects:
                _report(self, {"WARNING"}, "Select objects to export")
                return {"CANCELLED"}
            if not props.subset:
                _report(self, {"WARNING"}, "Select a subset first")
                return {"CANCELLED"}
            manager = _manager()
            version = manager.next_data_version(
                asset,
                department="geo",
                variant="default",
                subset=props.subset,
                data_format="fbx",
            )
            version_dir = manager.data_version_dir(
                asset,
                department="geo",
                variant="default",
                subset=props.subset,
                data_format="fbx",
                version=version,
            )
            version_dir.mkdir(parents=True, exist_ok=True)
            path = version_dir / "geo.fbx"
            bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True)
            manager.register_data_export(
                asset,
                department="geo",
                variant="default",
                subset=props.subset,
                data_format="fbx",
                version=version,
                files={"fbx": path.name},
                source_workfile=_current_blend_path(),
                comment="",
            )
            _report(self, {"INFO"}, f"Exported: {path.name}")
            return {"FINISHED"}


    class SMARTASSET_OT_export_model_fbx(bpy.types.Operator):
        bl_idname = "smart_asset.export_model_fbx"
        bl_label = "Export FBX model"

        def execute(self, context):
            props = context.scene.smart_asset
            asset = _current_asset(props)
            if not asset:
                _report(self, {"WARNING"}, "Select an asset first")
                return {"CANCELLED"}
            if not context.selected_objects:
                _report(self, {"WARNING"}, "Select objects to export")
                return {"CANCELLED"}
            if not props.subset:
                _report(self, {"WARNING"}, "Select a subset first")
                return {"CANCELLED"}
            manager = _manager()
            version = manager.next_data_version(
                asset,
                department="model",
                variant="default",
                subset=props.subset,
                data_format="fbx",
            )
            version_dir = manager.data_version_dir(
                asset,
                department="model",
                variant="default",
                subset=props.subset,
                data_format="fbx",
                version=version,
            )
            version_dir.mkdir(parents=True, exist_ok=True)
            path = version_dir / MODEL_FBX_FILENAME
            texture_dir = version_dir / MODEL_FBX_TEXTURE_DIRNAME
            restore_paths, copied_textures = _copy_selected_textures(context.selected_objects, texture_dir)
            try:
                bpy.ops.export_scene.fbx(
                    filepath=str(path),
                    use_selection=True,
                    path_mode="RELATIVE",
                    embed_textures=False,
                )
            finally:
                _restore_image_paths(restore_paths)
            files = {"fbx": path.name}
            if copied_textures:
                files["textures"] = texture_dir.name
            manager.register_data_export(
                asset,
                department="model",
                variant="default",
                subset=props.subset,
                data_format="fbx",
                version=version,
                files=files,
                source_workfile=_current_blend_path(),
                comment="blender model fbx export",
            )
            _report(self, {"INFO"}, f"Exported model FBX: {path.parent.name}/{path.name}")
            return {"FINISHED"}


    class SMARTASSET_OT_import_geo(bpy.types.Operator):
        bl_idname = "smart_asset.import_geo"
        bl_label = "Import geo"

        def execute(self, context):
            props = context.scene.smart_asset
            asset = _current_asset(props)
            if not asset:
                _report(self, {"WARNING"}, "Select an asset first")
                return {"CANCELLED"}
            path = _data_latest_path(asset, props.subset, props.geo_version or "latest")
            if not path or not path.exists():
                _report(self, {"WARNING"}, "No geo FBX data was found")
                return {"CANCELLED"}
            bpy.ops.import_scene.fbx(filepath=str(path))
            _report(self, {"INFO"}, f"Imported: {path.name}")
            return {"FINISHED"}


    class SMARTASSET_PT_panel(bpy.types.Panel):
        bl_label = "SMART ASSET"
        bl_idname = "SMARTASSET_PT_panel"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "SMART ASSET"

        def draw(self, context):
            layout = self.layout
            props = context.scene.smart_asset

            header = layout.row(align=True)
            header.operator("smart_asset.refresh", text="", icon="FILE_REFRESH")

            layout.prop(props, "category", text="category")
            layout.prop(props, "group", text="group")
            layout.prop(props, "asset", text="asset")
            layout.prop(props, "subset", text="subset")

            layout.separator()
            layout.operator("smart_asset.open_work", text="OPEN")
            layout.operator("smart_asset.save_work", text="SAVE")

            row = layout.row(align=True)
            row.prop(props, "geo_version", text="")
            row.operator("smart_asset.import_geo", text="Import geo")
            layout.operator("smart_asset.export_geo", text="Export geo")
            layout.separator()
            layout.operator("smart_asset.export_model_fbx", text="Export FBX model")


    CLASSES = (
        SmartAssetProperties,
        SMARTASSET_OT_refresh,
        SMARTASSET_OT_open_work,
        SMARTASSET_OT_save_work,
        SMARTASSET_OT_export_geo,
        SMARTASSET_OT_export_model_fbx,
        SMARTASSET_OT_import_geo,
        SMARTASSET_PT_panel,
    )


    def register() -> None:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
        bpy.types.Scene.smart_asset = bpy.props.PointerProperty(type=SmartAssetProperties)
        _refresh_assets()


    def unregister() -> None:
        if hasattr(bpy.types.Scene, "smart_asset"):
            del bpy.types.Scene.smart_asset
        for cls in reversed(CLASSES):
            bpy.utils.unregister_class(cls)


else:

    def register() -> None:
        raise RuntimeError("This module must be registered inside Blender.")


    def unregister() -> None:
        return


if __name__ == "__main__":
    register()
