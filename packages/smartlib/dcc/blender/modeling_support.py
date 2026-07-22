from __future__ import annotations


bl_info = {
    "name": "Smart Modeling Support",
    "author": "SmartPipeline",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > SMART MODELING",
    "description": "Modeling helper tools for SmartPipeline.",
    "category": "Pipeline",
}


try:
    import bpy
    from mathutils import Matrix, Vector
except ImportError:  # Allows syntax checks outside Blender.
    bpy = None
    Matrix = None
    Vector = None


def _report(operator, level: set[str], message: str) -> None:
    operator.report(level, message)


if bpy is not None:

    def _selected_bbox_min_z(context) -> float | None:
        min_z = None
        for obj in context.selected_objects:
            if not getattr(obj, "bound_box", None):
                continue
            for corner in obj.bound_box:
                z = (obj.matrix_world @ Vector(corner)).z
                min_z = z if min_z is None else min(min_z, z)
        return min_z


    class SMARTMODELING_OT_bottom_to_origin_height(bpy.types.Operator):
        bl_idname = "smart_modeling.bottom_to_origin_height"
        bl_label = "BBox bottom to origin height"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            min_z = _selected_bbox_min_z(context)
            if min_z is None:
                _report(self, {"WARNING"}, "Select objects with a bounding box")
                return {"CANCELLED"}
            offset_z = -min_z
            if abs(offset_z) <= 1.0e-6:
                _report(self, {"INFO"}, "BBox bottom is already at Z=0")
                return {"FINISHED"}
            translation = Matrix.Translation((0.0, 0.0, offset_z))
            for obj in context.selected_objects:
                obj.matrix_world = translation @ obj.matrix_world
            _report(self, {"INFO"}, f"Moved selected objects by Z {offset_z:.4f}")
            return {"FINISHED"}


    class SMARTMODELING_PT_panel(bpy.types.Panel):
        bl_label = "SMART MODELING"
        bl_idname = "SMARTMODELING_PT_panel"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "SMART MODELING"

        def draw(self, _context):
            layout = self.layout
            layout.operator("smart_modeling.bottom_to_origin_height", text="BBox bottom to Z=0")


    CLASSES = (
        SMARTMODELING_OT_bottom_to_origin_height,
        SMARTMODELING_PT_panel,
    )


    def register() -> None:
        for cls in CLASSES:
            bpy.utils.register_class(cls)


    def unregister() -> None:
        for cls in reversed(CLASSES):
            bpy.utils.unregister_class(cls)


else:

    def register() -> None:
        raise RuntimeError("This module must be registered inside Blender.")


    def unregister() -> None:
        return


if __name__ == "__main__":
    register()
