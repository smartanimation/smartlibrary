from __future__ import annotations

from contextlib import contextmanager
from typing import Any


AXES = ("X", "Y", "Z")
SIDES = ("negative", "positive")


def delete_half_mesh(axis: str = "X", side: str = "negative", plane: float = 0.0, tolerance: float = 0.0001) -> int:
    """Delete faces on one side of the selected polygon mesh targets."""
    cmds = _maya_cmds()
    axis_index = _axis_index(axis)
    side_sign = _side_sign(side)
    transforms = _selected_mesh_transforms(cmds)
    deleted = 0

    with _undo_chunk(cmds, "Delete Half Mesh"):
        for transform in transforms:
            for shape in _mesh_shapes(cmds, transform):
                face_count = int(cmds.polyEvaluate(shape, face=True) or 0)
                faces = []
                for index in range(face_count):
                    face = f"{shape}.f[{index}]"
                    try:
                        bounds = cmds.xform(face, query=True, worldSpace=True, boundingBox=True)
                    except Exception:
                        continue
                    center = (float(bounds[axis_index]) + float(bounds[axis_index + 3])) * 0.5
                    if _is_on_deleted_side(center, side_sign, float(plane), float(tolerance)):
                        faces.append(face)
                if faces:
                    cmds.delete(faces)
                    deleted += len(faces)

        cmds.select(transforms, replace=True)
    return deleted


def mirror_copy(axis: str = "X", plane: float = 0.0, merge_tolerance: float = 0.001) -> list[str]:
    """Mirror selected polygon mesh targets, combine each pair, and merge center vertices."""
    cmds = _maya_cmds()
    axis_index = _axis_index(axis)
    transforms = _selected_mesh_transforms(cmds)
    combined_meshes = []

    with _undo_chunk(cmds, "Mirror Copy + Combine"):
        for transform in transforms:
            duplicate = _duplicate_transform(cmds, transform)
            for shape in _mesh_shapes(cmds, duplicate):
                _mirror_shape_vertices(cmds, shape, axis_index, float(plane))
                _reverse_mesh_normals(cmds, shape)
            combined = _combine_meshes(cmds, transform, duplicate)
            _merge_plane_vertices(cmds, combined, axis_index, float(plane), float(merge_tolerance))
            combined_meshes.append(combined)

        if combined_meshes:
            cmds.select(combined_meshes, replace=True)
    return combined_meshes


def select_near_x_zero_vertices(tolerance: float = 0.001) -> list[str]:
    """Select vertices on selected meshes whose world-space X value is near zero."""
    cmds = _maya_cmds()
    transforms = _selected_mesh_transforms(cmds)
    vertices = []

    for transform in transforms:
        vertices.extend(_vertices_near_plane(cmds, transform, axis_index=0, plane=0.0, tolerance=float(tolerance)))

    if not vertices:
        raise RuntimeError(f"No vertices found near X=0 within tolerance {tolerance:g}.")

    with _undo_chunk(cmds, "Select X Zero Vertices"):
        _set_vertex_selection_mode(cmds, transforms)
        cmds.select(vertices, replace=True)
    return vertices


def set_selected_vertices_x_zero() -> int:
    """Move selected vertices to world-space X=0."""
    cmds = _maya_cmds()
    vertices = _selected_vertex_components(cmds)
    if not vertices:
        raise RuntimeError("Select one or more polygon vertices.")

    with _undo_chunk(cmds, "Set Vertices X Zero"):
        for vertex in vertices:
            try:
                position = list(cmds.xform(vertex, query=True, worldSpace=True, translation=True))
            except Exception:
                continue
            position[0] = 0.0
            cmds.xform(vertex, worldSpace=True, translation=position)
    return len(vertices)


def move_bbox_bottom_center_to_origin() -> tuple[float, float, float]:
    """Move selected objects so the combined bbox bottom center lands on world origin."""
    cmds = _maya_cmds()
    transforms = _selected_transforms(cmds)
    bounds = cmds.exactWorldBoundingBox(transforms)
    bottom_center = (
        (float(bounds[0]) + float(bounds[3])) * 0.5,
        float(bounds[1]),
        (float(bounds[2]) + float(bounds[5])) * 0.5,
    )
    offset = (-bottom_center[0], -bottom_center[1], -bottom_center[2])

    with _undo_chunk(cmds, "Move BBox Bottom Center To Origin"):
        cmds.move(offset[0], offset[1], offset[2], transforms, relative=True, worldSpace=True)
        cmds.select(transforms, replace=True)
    return offset


def combine_selected_to_last_name() -> str:
    """Combine selected mesh objects, name the result after the last selected object, and delete history."""
    cmds = _maya_cmds()
    selection = _ordered_selection(cmds)
    transforms = _transforms_from_selection(cmds, selection)
    transforms = _top_level_transforms(transforms)
    if len(transforms) < 2:
        raise RuntimeError("Select two or more mesh objects to combine.")

    last_name = transforms[-1].split("|")[-1].split(":")[-1]
    with _undo_chunk(cmds, "Combine To Last Selected Name"):
        result = cmds.polyUnite(
            *transforms,
            constructionHistory=False,
            mergeUVSets=True,
            centerPivot=True,
            name=f"{last_name}_combined",
        ) or []
        combined = result[0] if result else ""
        if not combined:
            raise RuntimeError("Could not combine selected mesh objects.")
        for transform in transforms:
            if transform != combined and cmds.objExists(transform):
                cmds.delete(transform)
        cmds.delete(combined, constructionHistory=True)
        combined = _rename_available(cmds, combined, last_name)
        cmds.select(combined, replace=True)
    return combined


def extract_selected_faces_delete_history_center_pivot() -> list[str]:
    """Extract selected polygon faces to new objects, delete history, and center pivots."""
    cmds = _maya_cmds()
    face_groups = _selected_face_indices_by_transform(cmds)
    extracted = []

    with _undo_chunk(cmds, "Extract Selected Faces"):
        for transform, face_indices in face_groups:
            face_count = int(cmds.polyEvaluate(transform, face=True) or 0)
            if not face_indices or face_count <= 0:
                continue

            duplicate = _duplicate_transform_with_name(cmds, transform, f"{transform.split('|')[-1].split(':')[-1]}_extract")
            selected = set(face_indices)
            unselected_faces = [f"{duplicate}.f[{index}]" for index in range(face_count) if index not in selected]
            original_faces = [f"{transform}.f[{index}]" for index in sorted(selected)]

            if unselected_faces:
                cmds.delete(unselected_faces)
            if original_faces:
                cmds.delete(original_faces)

            _delete_history(cmds, transform)
            _delete_history(cmds, duplicate)
            _center_pivot(cmds, duplicate)
            extracted.append(duplicate)

        if not extracted:
            raise RuntimeError("No faces were extracted.")
        cmds.select(extracted, replace=True)
        cmds.selectMode(object=True)
    return extracted


def match_transform_to_last_selected() -> int:
    """Match selected transform objects to the last selected transform."""
    cmds, sources, target, transforms = _match_selection()
    matrix = cmds.xform(target, query=True, worldSpace=True, matrix=True)
    with _undo_chunk(cmds, "Match Transform To Last Selected"):
        for source in sources:
            cmds.xform(source, worldSpace=True, matrix=matrix)
        cmds.select(transforms, replace=True)
    return len(sources)


def match_position_to_last_selected() -> int:
    """Match selected transform object positions to the last selected transform."""
    cmds, sources, target, transforms = _match_selection()
    translation = cmds.xform(target, query=True, worldSpace=True, translation=True)
    with _undo_chunk(cmds, "Match Position To Last Selected"):
        for source in sources:
            cmds.xform(source, worldSpace=True, translation=translation)
        cmds.select(transforms, replace=True)
    return len(sources)


def match_rotate_to_last_selected() -> int:
    """Match selected transform object rotations to the last selected transform."""
    cmds, sources, target, transforms = _match_selection()
    rotation = cmds.xform(target, query=True, worldSpace=True, rotation=True)
    with _undo_chunk(cmds, "Match Rotate To Last Selected"):
        for source in sources:
            cmds.xform(source, worldSpace=True, rotation=rotation)
        cmds.select(transforms, replace=True)
    return len(sources)


def create_locators_at_selected_world() -> list[str]:
    """Create locators matching the world transforms of selected objects."""
    cmds = _maya_cmds()
    transforms = _selected_transform_objects(cmds)
    locators = []

    with _undo_chunk(cmds, "Create Locators At Selected World"):
        for transform in transforms:
            short_name = transform.split("|")[-1].split(":")[-1]
            matrix = cmds.xform(transform, query=True, worldSpace=True, matrix=True)
            locator = cmds.spaceLocator(name=f"{short_name}_loc") or []
            locator_transform = locator[0] if locator else ""
            if not locator_transform:
                raise RuntimeError(f"Could not create locator for: {transform}")
            cmds.xform(locator_transform, worldSpace=True, matrix=matrix)
            locators.append(locator_transform)

        cmds.select(locators, replace=True)
    return locators


def set_selection_mode(mode: str) -> str:
    """Switch Maya selection mode for polygon modeling."""
    cmds = _maya_cmds()
    normalized = str(mode or "object").strip().lower()
    if normalized not in {"object", "vertex", "edge", "face", "uv"}:
        raise ValueError(f"Unsupported selection mode: {mode}")

    with _undo_chunk(cmds, f"Set {normalized.title()} Selection Mode"):
        if normalized == "object":
            cmds.selectMode(object=True)
        else:
            try:
                transforms = _selected_transforms(cmds)
            except RuntimeError:
                transforms = []
            _set_component_selection_mode(cmds, transforms, normalized)
    return normalized


def activate_multi_cut_tool() -> str:
    """Activate Maya's Multi-Cut tool."""
    return _activate_tool(
        "Multi-Cut",
        (
            ("mel", "dR_multiCutTool;"),
            ("mel", "setToolTo polyCutContext;"),
            ("cmds", "polyCutContext"),
            ("mel", "MultiCutTool;"),
        ),
    )


def activate_quad_draw_tool() -> str:
    """Activate Maya's Quad Draw tool."""
    return _activate_tool(
        "Quad Draw",
        (
            ("mel", "dR_quadDrawTool;"),
            ("mel", "setToolTo polyQuadAction;"),
            ("cmds", "polyQuadAction"),
            ("mel", "QuadDrawTool;"),
        ),
    )


def _is_on_deleted_side(value: float, side_sign: int, plane: float, tolerance: float) -> bool:
    if side_sign < 0:
        return value < plane - tolerance
    return value > plane + tolerance


def _axis_index(axis: str) -> int:
    normalized = str(axis or "X").strip().upper()
    if normalized not in AXES:
        raise ValueError(f"Unsupported mirror axis: {axis}")
    return AXES.index(normalized)


def _side_sign(side: str) -> int:
    normalized = str(side or "negative").strip().lower()
    aliases = {
        "-": "negative",
        "-x": "negative",
        "-y": "negative",
        "-z": "negative",
        "minus": "negative",
        "negative": "negative",
        "+": "positive",
        "+x": "positive",
        "+y": "positive",
        "+z": "positive",
        "plus": "positive",
        "positive": "positive",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SIDES:
        raise ValueError(f"Unsupported delete side: {side}")
    return -1 if normalized == "negative" else 1


def _selected_mesh_transforms(cmds: Any) -> list[str]:
    selection = cmds.ls(selection=True, long=True) or []
    if not selection:
        raise RuntimeError("Select one or more polygon mesh objects.")

    transforms = []
    seen = set()
    for item in selection:
        node = str(item).split(".", 1)[0]
        transform = _mesh_transform(cmds, node)
        if transform and transform not in seen:
            transforms.append(transform)
            seen.add(transform)

    if not transforms:
        raise RuntimeError("No polygon mesh objects were found in the selection.")
    return transforms


def _selected_transforms(cmds: Any) -> list[str]:
    selection = cmds.ls(selection=True, long=True) or []
    if not selection:
        raise RuntimeError("Select one or more objects.")

    transforms = _transforms_from_selection(cmds, selection)
    transforms = _top_level_transforms(transforms)
    if not transforms:
        raise RuntimeError("No transform objects were found in the selection.")
    return transforms


def _selected_transform_objects(cmds: Any) -> list[str]:
    selection = cmds.ls(selection=True, long=True) or []
    if not selection:
        raise RuntimeError("Select one or more objects.")

    transforms = _transforms_from_selection(cmds, selection)
    if not transforms:
        raise RuntimeError("No transform objects were found in the selection.")
    return transforms


def _ordered_selection(cmds: Any) -> list[str]:
    selection = cmds.ls(orderedSelection=True, long=True) or []
    if not selection:
        selection = cmds.ls(selection=True, long=True) or []
    if not selection:
        raise RuntimeError("Select one or more objects.")
    return selection


def _match_selection() -> tuple[Any, list[str], str, list[str]]:
    cmds = _maya_cmds()
    selection = _ordered_selection(cmds)
    transforms = _transforms_from_selection(cmds, selection)
    if len(transforms) < 2:
        raise RuntimeError("Select source objects, then select a target object last.")

    target = transforms[-1]
    sources = [transform for transform in transforms[:-1] if transform != target]
    if not sources:
        raise RuntimeError("Select at least one source object and one target object.")
    return cmds, sources, target, transforms


def _transforms_from_selection(cmds: Any, selection: list[str]) -> list[str]:
    transforms = []
    seen = set()
    for item in selection:
        node = str(item).split(".", 1)[0]
        transform = _transform_node(cmds, node)
        if transform and transform not in seen:
            transforms.append(transform)
            seen.add(transform)
    return transforms


def _transform_node(cmds: Any, node: str) -> str:
    try:
        node_type = cmds.nodeType(node)
    except Exception:
        return ""
    if node_type == "transform":
        return node
    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    return parents[0] if parents else ""


def _top_level_transforms(transforms: list[str]) -> list[str]:
    selected = set(transforms)
    top_level = []
    for transform in transforms:
        parts = [part for part in transform.split("|") if part]
        ancestor = ""
        skip = False
        for part in parts[:-1]:
            ancestor = f"{ancestor}|{part}" if ancestor else f"|{part}"
            if ancestor in selected:
                skip = True
                break
        if not skip:
            top_level.append(transform)
    return top_level


def _rename_available(cmds: Any, node: str, name: str) -> str:
    if not cmds.objExists(name):
        return cmds.rename(node, name)
    if node.split("|")[-1] == name:
        return node
    return cmds.rename(node, f"{name}_combined")


def _mesh_transform(cmds: Any, node: str) -> str:
    try:
        node_type = cmds.nodeType(node)
    except Exception:
        return ""
    if node_type == "mesh":
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        return parents[0] if parents else ""
    if node_type == "transform" and _mesh_shapes(cmds, node):
        return node
    return ""


def _mesh_shapes(cmds: Any, transform: str) -> list[str]:
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, noIntermediate=True) or []
    meshes = []
    for shape in shapes:
        try:
            if cmds.nodeType(shape) == "mesh":
                meshes.append(shape)
        except Exception:
            continue
    return meshes


def _duplicate_transform(cmds: Any, transform: str) -> str:
    short_name = transform.split("|")[-1].split(":")[-1]
    result = _duplicate_transform_with_name(cmds, transform, f"{short_name}_mirror")
    return result


def _duplicate_transform_with_name(cmds: Any, transform: str, name: str) -> str:
    result = cmds.duplicate(transform, name=name, renameChildren=True, returnRootsOnly=True) or []
    if not result:
        raise RuntimeError(f"Could not duplicate mesh: {transform}")
    return result[0]


def _combine_meshes(cmds: Any, original: str, mirrored: str) -> str:
    short_name = original.split("|")[-1].split(":")[-1]
    result = cmds.polyUnite(
        original,
        mirrored,
        constructionHistory=False,
        mergeUVSets=True,
        centerPivot=True,
        name=f"{short_name}_combined",
    ) or []
    combined = result[0] if result else ""
    if not combined:
        raise RuntimeError(f"Could not combine mirrored mesh: {original}")
    return combined


def _merge_plane_vertices(cmds: Any, transform: str, axis_index: int, plane: float, tolerance: float) -> int:
    if tolerance <= 0.0:
        return 0
    vertex_count = int(cmds.polyEvaluate(transform, vertex=True) or 0)
    vertices = []
    for index in range(vertex_count):
        vertex = f"{transform}.vtx[{index}]"
        try:
            position = cmds.xform(vertex, query=True, worldSpace=True, translation=True)
        except Exception:
            continue
        if abs(float(position[axis_index]) - plane) <= tolerance:
            vertices.append(vertex)
    if len(vertices) < 2:
        return 0
    try:
        cmds.polyMergeVertex(vertices, distance=tolerance, constructionHistory=False)
    except Exception:
        cmds.polyMergeVertex(vertices, d=tolerance, ch=False)
    return len(vertices)


def _vertices_near_plane(cmds: Any, transform: str, axis_index: int, plane: float, tolerance: float) -> list[str]:
    vertex_count = int(cmds.polyEvaluate(transform, vertex=True) or 0)
    vertices = []
    for index in range(vertex_count):
        vertex = f"{transform}.vtx[{index}]"
        try:
            position = cmds.xform(vertex, query=True, worldSpace=True, translation=True)
        except Exception:
            continue
        if abs(float(position[axis_index]) - plane) <= tolerance:
            vertices.append(vertex)
    return vertices


def _selected_vertex_components(cmds: Any) -> list[str]:
    selection = cmds.ls(selection=True, flatten=True) or []
    vertices = cmds.filterExpand(selection, selectionMask=31, expand=True) or []
    return list(dict.fromkeys(vertices))


def _selected_face_indices_by_transform(cmds: Any) -> list[tuple[str, list[int]]]:
    selection = cmds.ls(selection=True, flatten=True) or []
    faces = cmds.filterExpand(selection, selectionMask=34, expand=True) or []
    if not faces:
        raise RuntimeError("Select polygon faces to extract.")

    groups: dict[str, set[int]] = {}
    order = []
    for face in faces:
        node, _, component = str(face).partition(".")
        transform = _mesh_transform(cmds, node)
        if not transform:
            continue
        if transform not in groups:
            groups[transform] = set()
            order.append(transform)
        groups[transform].update(_component_indices(component))

    result = [(transform, sorted(groups[transform])) for transform in order if groups.get(transform)]
    if not result:
        raise RuntimeError("No polygon faces were found in the selection.")
    return result


def _component_indices(component: str) -> list[int]:
    start = component.find("[")
    end = component.find("]", start + 1)
    if start < 0 or end < 0:
        return []
    indices = []
    for part in component[start + 1 : end].split(","):
        text = part.strip()
        if not text:
            continue
        if ":" in text:
            first, _, last = text.partition(":")
            indices.extend(range(int(first), int(last) + 1))
        else:
            indices.append(int(text))
    return indices


def _delete_history(cmds: Any, node: str) -> None:
    try:
        if cmds.objExists(node):
            cmds.delete(node, constructionHistory=True)
    except Exception:
        pass


def _center_pivot(cmds: Any, node: str) -> None:
    try:
        cmds.xform(node, centerPivots=True)
    except Exception:
        try:
            cmds.CenterPivot(node)
        except Exception:
            pass


def _set_vertex_selection_mode(cmds: Any, transforms: list[str]) -> None:
    _set_component_selection_mode(cmds, transforms, "vertex")


def _set_component_selection_mode(cmds: Any, transforms: list[str], mode: str) -> None:
    try:
        if transforms:
            cmds.hilite(transforms, replace=True)
    except Exception:
        pass
    try:
        cmds.selectMode(component=True)
    except Exception:
        pass
    try:
        cmds.selectType(
            allComponents=False,
            polymeshVertex=mode == "vertex",
            polymeshEdge=mode == "edge",
            polymeshFace=mode == "face",
            polymeshUV=mode == "uv",
        )
    except Exception:
        try:
            fallback = {
                "vertex": {"vertex": True},
                "edge": {"edge": True},
                "face": {"facet": True},
                "uv": {"meshComponents": True},
            }
            cmds.selectType(**fallback.get(mode, {"vertex": True}))
        except Exception:
            pass


def _mirror_shape_vertices(cmds: Any, shape: str, axis_index: int, plane: float) -> None:
    vertex_count = int(cmds.polyEvaluate(shape, vertex=True) or 0)
    for index in range(vertex_count):
        vertex = f"{shape}.vtx[{index}]"
        try:
            position = list(cmds.xform(vertex, query=True, worldSpace=True, translation=True))
        except Exception:
            continue
        position[axis_index] = (plane * 2.0) - float(position[axis_index])
        cmds.xform(vertex, worldSpace=True, translation=position)


def _reverse_mesh_normals(cmds: Any, shape: str) -> None:
    try:
        cmds.polyNormal(shape, normalMode=0, userNormalMode=0, constructionHistory=False)
    except Exception:
        try:
            cmds.polyNormal(shape, normalMode=0, constructionHistory=False)
        except Exception:
            pass


def _activate_tool(label: str, candidates: tuple[tuple[str, str], ...]) -> str:
    cmds = _maya_cmds()
    mel = _maya_mel()
    last_error = None
    for kind, command in candidates:
        try:
            if kind == "mel":
                mel.eval(command)
            else:
                cmds.setToolTo(command)
            return label
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Could not activate {label}: {last_error}")


@contextmanager
def _undo_chunk(cmds: Any, name: str):
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


def _maya_cmds() -> Any:
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Modeling Support is available inside Maya.") from exc
    return cmds


def _maya_mel() -> Any:
    try:
        import maya.mel as mel
    except ImportError as exc:
        raise RuntimeError("Modeling Support is available inside Maya.") from exc
    return mel
