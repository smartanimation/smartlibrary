"""Maya standalone build adapter and USD validation for final deformation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from smartlib.core.metadata import read_json, write_json


def validate_deform_usd(path: Path):
    from pxr import Usd, UsdGeom, UsdSkel
    stage = Usd.Stage.Open(str(path))
    if not stage:
        raise ValueError(f"Cannot open USD: {path}")
    if len([layer for layer in stage.GetUsedLayers() if not layer.anonymous]) != 1:
        raise ValueError("Final Deform must be self-contained (no external USD layers)")
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    if not meshes:
        raise ValueError("Final Deform USD has no meshes")
    if any(p.GetTypeName() in {"Skeleton", "SkelAnimation"} or
           p.HasAPI(UsdSkel.BindingAPI)
           for p in stage.Traverse()):
        raise ValueError("Final Deform USD must not retain skinning bindings")
    for prim in meshes:
        mesh = UsdGeom.Mesh(prim)
        if not mesh.GetPointsAttr().Get(Usd.TimeCode.EarliestTime()):
            raise ValueError(f"Mesh has no final points: {prim.GetPath()}")
    return [str(prim.GetPath()) for prim in stage.GetPseudoRoot().GetChildren()]


def validate_look_usd(path, prim_path, variants):
    from pxr import Sdf, Usd
    stage = Usd.Stage.Open(str(path))
    if not stage or len([layer for layer in stage.GetUsedLayers() if not layer.anonymous]) != 1:
        raise ValueError("Look layers must be self-contained USD layers")
    prim = stage.GetPrimAtPath(prim_path)
    if not prim:
        raise ValueError(f"Look root not found: {prim_path}")
    errors = []
    def check(spec_path):
        spec = stage.GetRootLayer().GetObjectAtPath(spec_path)
        if isinstance(spec, Sdf.PrimSpec):
            if spec.typeName not in {"", "Scope", "Material", "Shader", "NodeGraph", "GeomSubset"}:
                errors.append(str(spec_path))
            if spec.referenceList.GetAppliedItems() or spec.payloadList.GetAppliedItems():
                errors.append(str(spec_path))
            if spec.HasInfo("active") or spec.HasInfo("instanceable"):
                errors.append(str(spec_path))
        if isinstance(spec, Sdf.PropertySpec):
            allowed = (spec.name.startswith(("material:binding", "inputs:", "outputs:", "info:"))
                       or spec.name in {"familyName", "elementType", "indices"})
            if not allowed:
                errors.append(str(spec_path))
    stage.GetRootLayer().Traverse(Sdf.Path.absoluteRootPath, check)
    if errors:
        raise ValueError("Look override contains geometry/transform opinions: " + ", ".join(errors))
    stage.SetEditTarget(stage.GetSessionLayer())
    for name, value in variants.items():
        vset = prim.GetVariantSets().GetVariantSet(name)
        if value not in vset.GetVariantNames():
            raise ValueError(f"Unknown Look variant: {name}={value}")
        vset.SetVariantSelection(value)
    # Texture inputs must also resolve to immutable Production files (checked by service).
    assets = []
    for p in stage.Traverse():
        for attr in p.GetAttributes():
            value = attr.Get()
            if isinstance(value, Sdf.AssetPath) and value.path:
                resolved = value.resolvedPath
                if not resolved:
                    raise ValueError(f"Unresolved Look asset: {value.path}")
                assets.append(resolved)
    return assets


def compose_deform_usd(path, data):
    from pxr import Sdf, Usd, UsdGeom
    stage = Usd.Stage.CreateNew(str(path))
    root = UsdGeom.Xform.Define(stage, "/Shot").GetPrim()
    stage.SetDefaultPrim(root)
    stage.SetStartTimeCode(data["frame_range"][0])
    stage.SetEndTimeCode(data["frame_range"][1])
    stage.SetTimeCodesPerSecond(data["fps"])
    stage.SetFramesPerSecond(data["fps"])
    for member in data["members"]:
        target = member["instance_id"]
        if not Sdf.Path.IsValidIdentifier(target):
            raise ValueError(f"Cast instance ID must be a USD identifier: {target}")
        wrapper = UsdGeom.Xform.Define(stage, f"/Shot/{target}").GetPrim()
        artifact = member["products"]["deform"]
        for source_prim in artifact["prim_paths"]:
            name = Sdf.Path(source_prim).name
            prim = stage.DefinePrim(f"/Shot/{target}/{name}")
            prim.GetReferences().AddReference(artifact["path"], Sdf.Path(source_prim))
        look = data.get("looks", {}).get(target)
        if look:
            wrapper.GetReferences().AddReference(look["path"], Sdf.Path(look["prim_path"]))
            for name, value in look.get("variants", {}).items():
                if not wrapper.GetVariantSets().GetVariantSet(name).SetVariantSelection(value):
                    raise ValueError(f"Could not select Look variant: {target}/{name}")
    stage.GetRootLayer().Save()
    if not any(p.IsA(UsdGeom.Mesh) for p in stage.Traverse()):
        raise ValueError("Composed Shot has no evaluated meshes")


def build_manifest(shots, identity, source_scene, *, review_source=None):
    """Run only in a dedicated mayapy process; never replace an artist's open scene."""
    import maya.cmds as cmds
    from smartlib.apps.shot_manager.animation_publish import AnimationCompositionService, BUILD_SCHEMA, file_hash
    from smartlib.dcc.maya.animation_curves import (
        export_animation_geometry_cache, export_animation_atom_for_cast, apply_animation_atom_from_file,
    )
    service = AnimationCompositionService(shots)
    profile = shots.project_config.pipeline_profile
    if not profile:
        raise ValueError("Project Profile is not configured")
    source_scene = Path(source_scene).resolve()
    if source_scene.suffix.lower() not in {".ma", ".mb"} or not source_scene.is_file():
        raise ValueError("Select a saved Maya Build scene")
    if review_source:
        from smartlib.apps.review_build_manager.composition_sources import validate_review_source
        validate_review_source(review_source, source_scene)
    source_hash = file_hash(source_scene)
    cast = (shots.load_cast(identity).get("cast") or {})
    active = {key: value for key, value in cast.items() if value.get("animation_required", True)}
    draft = (review_source or {}).get("animation_draft")
    excluded_members = []
    if draft is not None:
        if set(draft) != set(active):
            raise ValueError("Draft cast changed; refresh the selected Construct")
        excluded_members = [key for key, choice in draft.items() if not choice.get("use", True)]
        active = {key: value for key, value in active.items() if key not in excluded_members}
    if not active:
        raise ValueError("Shot has no animation members")
    directory_root = shots.paths.animation_build_dir(identity.episode, identity.sequence, identity.shot)
    _version, directory = service._reserve(directory_root)
    frame_range = list(shots.shot_frame_range(identity))
    cmds.file(str(source_scene), open=True, force=True, prompt=False)
    import maya.api.OpenMaya as om
    source_fps = om.MTime(1, om.MTime.kSeconds).asUnits(om.MTime.uiUnit())
    if abs(source_fps - shots.project_fps) > 0.001:
        raise ValueError(f"Source scene FPS {source_fps} differs from Project FPS {shots.project_fps}")
    source_dependencies = _scene_dependencies(cmds, service, exclude={source_scene})
    members = []
    rend_previews = {}
    if profile.name == "maya_rend_atom":
        rend_previews = {
            row.cast_key: row for row in shots.build_preview(
                identity, cast_contexts={key: "REND" for key in active}
            )
        }
    for target, entry in active.items():
        shots.paths.pipeline_token(target)
        namespace = str(entry.get("namespace") or target)
        member_dir = shots.paths.artifact_file(directory, target)
        member_dir.mkdir()
        row = {"instance_id": target, "asset": entry["asset"],
               "variant": entry.get("variant", "default"), "products": {}}
        if profile.representation in {"usd", "abc"}:
            file_path = shots.paths.artifact_file(member_dir, "deform.usdc" if profile.representation == "usd" else "deform.abc")
            exported = export_animation_geometry_cache(
                namespace=namespace, output_dir=member_dir, frame_range=tuple(frame_range),
                formats=(profile.representation,), final_deform=True,
                resolved_files={profile.representation: file_path},
            )
            if profile.representation == "usd":
                validate_deform_usd(file_path)
            digest = file_hash(file_path)
            row["products"]["deform"] = {
                "source": file_path.as_posix(), "sha256": digest,
                "evaluation": "final_deform", "frame_range": frame_range,
                "topology_signature": _evaluated_topology_signature(cmds, exported["source_nodes"]),
                "dependencies": [],
                "validation": {"ok": True, "source_sha256": digest, "validator": "maya_final_deform.v1"},
            }
        else:
            cmds.file(str(source_scene), open=True, force=True, prompt=False)
            atom = shots.paths.artifact_file(member_dir, "animation.atom")
            atom_manifest_path = shots.paths.artifact_file(member_dir, "animation_manifest.json")
            transfer = export_animation_atom_for_cast(
                atom, cast_key=target, namespace=namespace, asset=entry["asset"],
                source_workfile=source_scene, frame_range=tuple(frame_range),
            )
            transfer["payload"] = atom.name
            transfer["payload_sha256"] = file_hash(atom)
            write_json(atom_manifest_path, transfer)
            rend = rend_previews.get(target)
            rend_path = getattr(rend, "publish_path", "") if rend else ""
            if not rend_path:
                raise ValueError(f"REND Asset Context not found: {target}")
            choice = (draft or {}).get(target, {})
            fixed = choice.get("rend")
            if fixed:
                candidates = shots.asset_publish_resolver.list_context_versions(rend.variant_root, "REND")
                if not any(Path(row["path"]).resolve() == Path(fixed["path"]).resolve() for row in candidates):
                    raise ValueError(f"Selected REND version is no longer available: {target}")
                service._check_dependency(fixed)
                rend_path = fixed["path"]
            pinned_rig = service._fixed_dependency(rend_path)
            cmds.file(new=True, force=True)
            from dataclasses import replace
            from smartlib.dcc.maya.shot_builder import build_shot_from_preview
            referenced = build_shot_from_preview(
                [replace(rend, publish_path=pinned_rig["path"], namespace=namespace)],
                shots.load_shot(identity),
            )
            if not referenced:
                raise ValueError(f"REND Shot construction failed: {target}")
            applied = apply_animation_atom_from_file(atom_manifest_path, namespace=namespace)
            if applied.get("missing_destinations") or not applied.get("applied_destinations"):
                raise ValueError(f"ATOM could not be completely applied: {target}")
            dependencies = [
                service._fixed_dependency(ref)["path"]
                for ref in (cmds.file(query=True, reference=True) or [])
            ]
            # Keep fixed production Rig references and their animation edits.
            # Importing would pull binary-only unknown data into the ASCII scene.
            scene = shots.paths.artifact_file(member_dir, "rend_animation.ma")
            cmds.playbackOptions(minTime=frame_range[0], maxTime=frame_range[1])
            cmds.file(rename=str(scene))
            cmds.file(save=True, type="mayaAscii", force=True)
            dependencies = sorted(set(dependencies + _scene_dependencies(cmds, service, exclude={scene})))
            references = []
            for ref_node in cmds.ls(type="reference") or []:
                if ref_node == "sharedReferenceNode":
                    continue
                ref_path = cmds.referenceQuery(ref_node, filename=True, withoutCopyNumber=True)
                depth, parent = 1, ref_node
                while True:
                    try:
                        parent = cmds.referenceQuery(parent, parent=True, referenceNode=True)
                    except RuntimeError:
                        break
                    if not parent:
                        break
                    depth += 1
                references.append({
                    "path": service._fixed_dependency(ref_path)["path"],
                    "node": ref_node,
                    "namespace": cmds.referenceQuery(ref_node, namespace=True).strip(":").rsplit(":", 1)[-1],
                    "depth": depth,
                    "type": "mayaBinary" if Path(ref_path).suffix.lower() == ".mb" else "mayaAscii",
                })
            for product, path in (("transfer", atom), ("rend", scene)):
                digest = file_hash(path)
                row["products"][product] = {
                    "source": path.as_posix(), "sha256": digest, "frame_range": frame_range,
                    "dependencies": dependencies if product == "rend" else [],
                    "validation": {
                        "ok": True, "source_sha256": digest, "validator": "maya_atom_rend.v1",
                        "atom_applied": product == "rend", "atom_sha256": file_hash(atom),
                        "apply_report": applied if product == "rend" else {},
                        "references": references if product == "rend" else [],
                    },
                }
            row["products"]["transfer"]["transfer_manifest"] = transfer
        members.append(row)
    if file_hash(source_scene) != source_hash:
        raise ValueError("Source scene changed during build")
    manifest = {
        "schema": BUILD_SCHEMA, "pipeline_profile": profile.name, "pipeline_profile_version": profile.version,
        "shot": {"episode": identity.episode, "sequence": identity.sequence, "shot": identity.shot},
        "frame_range": frame_range, "fps": shots.project_fps,
        "source_workfile": source_scene.as_posix(), "source_sha256": source_hash,
        "dependencies": {f"source_asset_{i}": path for i, path in enumerate(source_dependencies)},
        "members": members,
        "excluded_members": excluded_members,
    }
    if review_source:
        validate_review_source(review_source, source_scene)
        manifest["review_source"] = review_source
    return write_json(shots.paths.artifact_file(directory, "build_manifest.json"), manifest)


def _scene_dependencies(cmds, service, *, exclude=None):
    excluded = {Path(p).resolve() for p in (exclude or set())}
    sources = set(cmds.file(query=True, list=True, withoutCopyNumber=True) or [])
    # Explicit texture queries include tiles that Maya may omit from file -list.
    for node_type, attribute in (("file", "fileTextureName"), ("aiImage", "filename"),
                                  ("aiStandIn", "dso"), ("gpuCache", "cacheFileName")):
        for node in cmds.ls(type=node_type) or []:
            value = cmds.getAttr(f"{node}.{attribute}")
            if value:
                sources.add(str(value))
    dependencies = set()
    for source in sources:
        for path in service.paths.dependency_files(source):
            if path.resolve() not in excluded:
                dependencies.add(service._fixed_dependency(path)["path"])
    return sorted(dependencies)


def _evaluated_topology_signature(cmds, roots):
    """Include vertex order connectivity, not merely matching vertex/face counts."""
    from smartlib.dcc.maya.animation_curves import _cache_mesh_shapes
    rows = []
    for root in roots:
        for shape in _cache_mesh_shapes(cmds, root):
            rows.append({
                "mesh": shape.split(":")[-1],
                "vertices": cmds.polyEvaluate(shape, vertex=True),
                "faces": cmds.polyInfo(shape, faceToVertex=True) or [],
            })
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    for name in ("config", "episode", "sequence", "shot", "source", "result"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--review-source")
    parser.add_argument("--status")
    args = parser.parse_args()
    def status(state, task, progress, message=""):
        if args.status:
            write_json(args.status, {"state": state, "task": task, "progress": progress, "message": message})
    status("RUNNING", "Initialize Maya", 5)
    import maya.standalone
    maya.standalone.initialize(name="python")
    try:
        from smartlib.core.config_loader import ProjectConfig
        from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
        shots = ShotManagerService(ProjectConfig(args.config))
        review_source = read_json(args.review_source, {}) if args.review_source else None
        if args.review_source and not review_source:
            raise ValueError("Review source selection is empty")
        status("RUNNING", "Build Animation Products", 20)
        result = build_manifest(
            shots, ShotIdentity(args.episode, args.sequence, args.shot), args.source,
            review_source=review_source,
        )
        write_json(args.result, {"ok": True, "manifest": str(result)})
        status("RUNNING", "Ready to Publish Animation", 90)
    except Exception as exc:
        report = getattr(exc, "report", None)
        failed = [row for row in (report or []) if row.get("state") in {"MISSING", "FAILED"}]
        detail = str(exc)
        if failed:
            detail += "\n" + "\n".join(
                f"{row.get('state')}: {row.get('target') or row.get('source')} {row.get('error') or ''}"
                for row in failed)
        write_json(args.result, {"ok": False, "error": detail, "report": report})
        status("FAILED", "Animation Build Failed", 100, detail)
        raise
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
