from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.apps.sequence_cast_publisher.service import CastCandidate


@dataclass(frozen=True)
class CandidateAnalysis:
    candidate: CastCandidate
    included: bool
    evidence: str
    hits: int
    samples: int


@dataclass(frozen=True)
class ShotAnalysis:
    node: str
    shot: str
    camera: str
    start: int
    end: int
    candidates: tuple[CandidateAnalysis, ...]
    thumbnail: str = ""


def sample_frames(start: int, end: int, count: int = 9) -> list[int]:
    if end <= start or count <= 1:
        return [int(start)]
    size = min(count, end - start + 1)
    return sorted({int(round(start + (end - start) * index / (size - 1))) for index in range(size)})


def analyze_sequence(candidates: list[CastCandidate], *, sample_count: int = 9) -> list[ShotAnalysis]:
    from smartlib.dcc.maya.smart_shot import list_sequencer_shots
    return [analyze_shot(shot, candidates, sample_count=sample_count) for shot in list_sequencer_shots()]


def analyze_shot(shot, candidates: list[CastCandidate], *, sample_count: int = 9) -> ShotAnalysis:
    cmds = _maya_cmds()
    original_time = cmds.currentTime(query=True)
    frames = sample_frames(shot.start, shot.end, sample_count)
    hits = {candidate.cast_key: 0 for candidate in candidates}
    try:
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            for candidate in candidates:
                roots = _namespace_roots(cmds, candidate.namespace)
                if roots and _roots_in_camera(cmds, roots, shot.camera_shape):
                    hits[candidate.cast_key] += 1
    finally:
        cmds.currentTime(original_time, edit=True)

    rows = []
    for candidate in candidates:
        count = hits[candidate.cast_key]
        entry = candidate.entry
        category = str(entry.get("category") or "").lower()
        entity_type = str(entry.get("entity_type") or "asset").lower()
        required_set = entity_type == "assembly" or category in {"environment", "set"}
        if required_set:
            evidence, included = "REQUIRED SET", True
        elif count == len(frames):
            evidence, included = "IN CAMERA", True
        elif count:
            evidence, included = f"EDGE / {count} frames", True
        else:
            evidence, included = "OUTSIDE CAMERA", False
        rows.append(CandidateAnalysis(candidate, included, evidence, count, len(frames)))
    return ShotAnalysis(
        node=shot.node,
        shot=shot.shot,
        camera=shot.camera,
        start=shot.start,
        end=shot.end,
        candidates=tuple(rows),
        thumbnail=capture_thumbnail(shot),
    )


def capture_thumbnail(shot) -> str:
    cmds = _maya_cmds()
    output_dir = Path(tempfile.gettempdir()) / "smartpipeline" / "sequence_cast_publisher"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{shot.shot}.png"
    original_time = cmds.currentTime(query=True)
    panel = cmds.getPanel(withFocus=True)
    original_camera = ""
    try:
        if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
            original_camera = cmds.modelPanel(panel, query=True, camera=True) or ""
            cmds.lookThru(panel, shot.camera)
        frame = int(round((shot.start + shot.end) / 2))
        cmds.currentTime(frame, edit=True)
        cmds.playblast(
            format="image", completeFilename=str(output), frame=frame,
            viewer=False, showOrnaments=False, offScreen=True, percent=100,
            widthHeight=(160, 90), compression="png", forceOverwrite=True,
        )
    except Exception:
        return ""
    finally:
        cmds.currentTime(original_time, edit=True)
        if panel and original_camera:
            try:
                cmds.lookThru(panel, original_camera)
            except Exception:
                pass
    return str(output) if output.is_file() else ""


def _namespace_roots(cmds: Any, namespace: str) -> list[str]:
    clean = str(namespace or "").rstrip(":")
    if not clean:
        return []
    return [root for root in (cmds.ls(f"{clean}:*", assemblies=True, long=True) or []) if cmds.objExists(root)]


def _roots_in_camera(cmds: Any, roots: list[str], camera_shape: str) -> bool:
    if not camera_shape or not cmds.objExists(camera_shape):
        return False
    try:
        from maya.api import OpenMaya as om
        selection = om.MSelectionList()
        selection.add(camera_shape)
        camera_path = selection.getDagPath(0)
        camera = om.MFnCamera(camera_path)
        width = float(cmds.getAttr("defaultResolution.width") or 1920)
        height = float(cmds.getAttr("defaultResolution.height") or 1080)
        left, right, bottom, top = camera.getRenderingFrustum(width / max(height, 1.0))
        near = float(camera.nearClippingPlane)
        inverse = camera_path.inclusiveMatrixInverse()
        for root in roots:
            bounds = cmds.exactWorldBoundingBox(root, ignoreInvisible=True)
            projected = []
            for x in (bounds[0], bounds[3]):
                for y in (bounds[1], bounds[4]):
                    for z in (bounds[2], bounds[5]):
                        point = om.MPoint(x, y, z) * inverse
                        depth = -float(point.z)
                        if depth <= near:
                            continue
                        px = float(point.x) * near / depth
                        py = float(point.y) * near / depth
                        projected.append((px, py))
            if projected:
                xs = [point[0] for point in projected]
                ys = [point[1] for point in projected]
                if max(xs) >= left and min(xs) <= right and max(ys) >= bottom and min(ys) <= top:
                    return True
    except Exception:
        return False
    return False


def _maya_cmds():
    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Sequence Cast Publisher is available inside Maya.") from exc
    return cmds
