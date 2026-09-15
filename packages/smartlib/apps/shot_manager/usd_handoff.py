"""Profile-independent, explicitly selected Data -> USD handoffs.

No Submit prerequisite, implicit latest adoption, or current Maya scene fallback.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import shutil

from smartlib.apps.shot_manager.animation_publish import AnimationCompositionService, file_hash
from smartlib.core.metadata import read_json, write_json
from smartlib.core.usd_settings import usd_settings

PLAN = "smartpipeline.usd_handoff_plan.v1"
PRODUCT = "smartpipeline.usd_handoff_product.v1"
COMPOSITION = "smartpipeline.usd_handoff_composition.v1"
KINDS = {"animation", "assets", "camera", "layout"}


def composition_errors(stage):
    """Maya 2024 ships USD without Stage.GetCompositionErrors."""
    if hasattr(stage, 'GetCompositionErrors'):
        return stage.GetCompositionErrors()
    return [error for prim in stage.TraverseAll() for error in prim.GetPrimIndex().localErrors]


class UsdHandoffService(AnimationCompositionService):
    def animation_rig_versions(self, identity, target):
        """Published asset contexts for one cast, using existing resolvers."""
        from smartlib.apps.asset_manager.context import AssetContextService
        from smartlib.core.path_resolver import AssetIdentity
        cast = (self.shots.load_cast(identity).get('cast') or {}).get(target)
        if not cast:
            return {}
        root = self.shots.find_asset_root(cast.get('asset', ''))
        if not root:
            return {}
        metadata = read_json(self.paths.artifact_file(root, 'asset.json'), {})
        asset = AssetIdentity(metadata.get('category') or cast.get('category', ''),
            metadata.get('group') or cast.get('group', 'main'),
            cast['asset'], cast.get('variant') or 'default')
        contexts = AssetContextService(self.config).quality_profiles_for_asset(asset)
        return {context: self.shots.asset_publish_resolver.list_context_versions(
            self.paths.asset_variant_root(asset), context,
            publish_root=self.paths.asset_publish_dir(asset, 'asset', '')) for context in contexts}

    def publish_sculpt_data(self, identity, target, base_usd, sculpted_usd, curve_data):
        """Version the optional Data separately; never implicitly adopt it."""
        from smartlib.dcc.maya.shot_sculpt import collect_from_usd
        self.paths.pipeline_token(target)
        curve = self.pin(curve_data)
        metadata = read_json(curve['path'], {})
        if metadata.get('schema') != 'smartpipeline.animation_atom.v3':
            raise ValueError('Select ATOM Data for this Shot Sculpt')
        baseline_ref = self.pin(base_usd)
        baseline = self.load_handoff(baseline_ref['path'])
        if baseline.get('schema') != PRODUCT or baseline.get('kind') != 'animation' or baseline['inputs']['source'] != curve:
            raise ValueError('Select a published Animation USD product generated from this Curve Data')
        if baseline['target'] != target or baseline['shot'] != dict(zip(('episode', 'sequence', 'shot'), self._identity(identity))):
            raise ValueError('Shot Sculpt baseline belongs to another shot/target')
        if baseline['inputs'].get('sculpt'):
            raise ValueError('Use an unsculpted baseline to avoid applying corrections twice')
        base_usd = baseline['entrypoint']['path']
        data = collect_from_usd(base_usd, sculpted_usd, curve_sha256=curve['sha256'],
                                frame_range=metadata['frame_range'])
        self.check(curve)
        self.check(baseline_ref)
        root = self.paths.shot_data_dir(*self._identity(identity), 'shot_sculpt', target, 'main')
        version, directory = self._reserve(root)
        data.update(target=target, version=version, curve_data=curve, baseline=baseline_ref,
                    shot=dict(zip(('episode', 'sequence', 'shot'), self._identity(identity))))
        path = self._file(directory, 'shot_sculpt.json')
        write_json(path, data)
        write_json(self._file(directory, 'data.json'), dict(data_type='shot_sculpt', target=target,
                   subset='main', version=version, files={'shot_sculpt': path.name}))
        return path

    def pin(self, value):
        path = self.paths.project_dependency(value)
        if not path.is_file() or not any(re.fullmatch(r"v[0-9]{3,}", p) for p in path.parts):
            raise ValueError(f"Select a fixed Data/Publish version, not WORK/latest: {path}")
        return {"path": path.as_posix(), "sha256": file_hash(path)}

    def check(self, ref):
        actual = self.pin(ref["path"])
        if actual != ref:
            raise ValueError(f"Input changed after selection: {ref['path']}")
        return Path(actual["path"])

    def plan(self, identity, rows, *, frame_range=None, fps=None):
        if not rows:
            raise ValueError("Select at least one USD target")
        result = {"schema": PLAN, "shot": dict(zip(("episode", "sequence", "shot"), self._identity(identity))),
                  "frame_range": list(frame_range or self.shots.shot_frame_range(identity)),
                  "fps": float(self.shots.project_fps if fps is None else fps), "rows": [],
                  "usd": usd_settings(self.config.load("project_settings"))}
        start, end = result["frame_range"]
        if not all(math.isfinite(float(v)) for v in (start, end, result["fps"])) or end < start or result["fps"] <= 0:
            raise ValueError("Invalid frame range or FPS")
        seen = set()
        for raw in rows:
            row = deepcopy(raw)
            kind, target = row["kind"], row["target"]
            if kind not in KINDS or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", target):
                raise ValueError(f"Invalid USD target: {kind}/{target}")
            if (kind, target) in seen or (kind == "camera" and any(k == "camera" for k, _ in seen)):
                raise ValueError("Duplicate target or more than one Primary Camera")
            seen.add((kind, target))
            row["source"] = self.pin(row["source"])
            if kind == "animation":
                row["rig"] = self.pin(row["rig"])
                row["sculpt"] = self.pin(row["sculpt"]) if row.get("sculpt") else None
                data = read_json(row["source"]["path"], {})
                if data.get("schema") != "smartpipeline.animation_atom.v3":
                    raise ValueError("Animation requires published ATOM v3 Data")
                timing = data.get('scene_timing', {})
                if timing and not math.isclose(float(timing['fps']), result['fps'], abs_tol=1e-6):
                    raise ValueError('Animation Data FPS differs from the USD plan')
                if timing and timing.get('up_axis') != result['usd']['up_axis']:
                    raise ValueError('Animation Data up-axis differs from the USD plan')
                meters = {'mm': .001, 'cm': .01, 'm': 1., 'km': 1000., 'in': .0254, 'ft': .3048, 'yd': .9144}
                if timing and not math.isclose(meters.get(timing.get('linear_unit'), -1), result['usd']['meters_per_unit']):
                    raise ValueError('Animation Data linear unit differs from the USD plan')
                payload = self.paths.manifest_source(row["source"]["path"], data["payload"])
                row["payload"] = self.pin(payload)
                if row["payload"]["sha256"] != data.get("payload_sha256"):
                    raise ValueError("ATOM payload checksum mismatch")
                if row.get('rig_context'):
                    available = self.animation_rig_versions(identity, target).get(row['rig_context'], [])
                    if not any(self.pin(item['path']) == row['rig'] for item in available):
                        raise ValueError('Selected Rig is not a published version of this cast/context')
                elif row["rig"] not in data.get("rig_dependencies", []):
                    raise ValueError("Rig is not pinned by Animation Data; republish Animation Curves")
                if not data.get('rig_dependencies'):
                    raise ValueError('Animation Data must record its source Rig dependencies')
                for ref in data.get("rig_dependencies", []):
                    self.check(ref)
                bounds = data.get("frame_range", [1, 0])
                if bounds[0] > start or bounds[1] < end:
                    raise ValueError("Animation Data does not cover the requested range")
            result["rows"].append(row)
        return result

    def validate_plan(self, plan, identity):
        if plan.get("schema") != PLAN or plan.get("shot") != dict(zip(("episode", "sequence", "shot"), self._identity(identity))):
            raise ValueError("Invalid USD plan or different shot")
        raw = []
        for row in plan["rows"]:
            item = deepcopy(row)
            for key in ("source", "rig", "sculpt", "payload"):
                if item.get(key):
                    item[key] = str(self.check(item[key]))
            item.pop("payload", None)
            raw.append(item)
        if self.plan(identity, raw, frame_range=plan["frame_range"], fps=plan["fps"]) != plan:
            raise ValueError("USD plan no longer matches its fixed inputs/settings")

    def publish(self, identity, plan, *, animation_exporter=None):
        self.validate_plan(plan, identity)
        _, workspace = self._reserve(self.paths.usd_handoff_build_dir(*self._identity(identity)))
        products, pending = [], []
        for row in plan["rows"]:
            kind, target = row["kind"], row["target"]
            version, directory = self._reserve(self.paths.usd_handoff_dir(*self._identity(identity), kind, target))
            data = {"schema": PRODUCT, "status": "published", "approval": "not_reviewed",
                    "shot": plan["shot"], "kind": kind, "target": target, "version": version,
                    "frame_range": plan["frame_range"], "fps": plan["fps"], "usd": plan["usd"],
                    "inputs": row, "created_at": datetime.now(timezone.utc).isoformat()}
            if kind == "animation":
                if animation_exporter is None:
                    raise ValueError("Animation requires the isolated Maya Data rebuild worker")
                path = self._file(workspace, target + '.usdc')
                data["validation"] = animation_exporter(row, plan, path)
                if data["validation"].get("ok") is not True:
                    raise ValueError("Animation validation failed")
                from smartlib.dcc.maya.animation_build import validate_deform_usd
                validate_deform_usd(path)
                self.validate_plan(plan, identity)
                published = self._file(directory, 'deform.usdc')
                shutil.copy2(path, published)
                if file_hash(path) != file_hash(published):
                    raise ValueError('USD copy checksum mismatch')
                data["entrypoint"] = self.pin(published)
            elif kind in {"assets", "camera"}:
                data["entrypoint"] = row["source"]
            else:
                from smartlib.dcc.maya.set_dress import SetDressPackage
                package = SetDressPackage.from_dict(read_json(row["source"]["path"], {}))
                data["changes"] = layout_changes(package, row.get("node_map", {}))
            manifest = self._file(directory, "manifest.json")
            pending.append((manifest, data))
            products.append(data)
        version, directory = self._reserve(self.paths.composition_dir(*self._identity(identity), "usd"))
        paths = self._build_composition_layers(identity, directory, products, plan)
        dependencies = paths.pop('_dependencies')
        self.validate_plan(plan, identity)
        for ref in dependencies:
            self.check(ref)
        # Products become visible only after the complete selection validates.
        for manifest, data in pending:
            data["dependencies"] = dependencies
            self._commit(manifest, data)
        snapshot = {"schema": COMPOSITION, "status": "published", "approval": "not_reviewed",
                    "shot": plan["shot"], "version": version, "frame_range": plan["frame_range"],
                    "fps": plan["fps"], "usd": plan["usd"], "partial": True,
                    "included_targets": [[p["kind"], p["target"]] for p in products],
                    "products": [self.pin(p) for p, _ in pending], "dependencies": dependencies,
                    "layers": {key: self.pin(path) for key, path in paths.items()},
                    "entrypoint": self.pin(paths["shot"])}
        manifest = self._file(directory, "manifest.json")
        self._commit(manifest, snapshot)
        return manifest

    def _commit(self, manifest, data):
        pending = self._file(manifest.parent, "manifest.pending.json")
        write_json(pending, data)
        pending.replace(manifest)

    def _build_composition_layers(self, identity, directory, products, plan):
        _, workspace = self._reserve(self.paths.usd_handoff_build_dir(*self._identity(identity)))
        build_paths = {k: self._file(workspace, k + '.usda') for k in ('shot', 'animation', 'camera', 'assets', 'layout')}
        deps = compose_layers(build_paths, products, plan, self)
        paths = {}
        for key, source in build_paths.items():
            target = self._file(directory, source.name)
            shutil.copy2(source, target)
            if file_hash(source) != file_hash(target):
                raise ValueError('Composition copy checksum mismatch')
            paths[key] = target
        paths['_dependencies'] = deps
        return paths

    def load_handoff(self, manifest):
        data = read_json(manifest, {})
        if data.get("schema") not in {PRODUCT, COMPOSITION} or data.get("status") != "published":
            raise ValueError("Not a completed USD handoff")
        for ref in data.get("dependencies", []) + data.get("products", []):
            self.check(ref)
        for ref in data.get("layers", {}).values():
            self.check(ref)
        if data.get("entrypoint"):
            self.check(data["entrypoint"])
        return data

    def compose_products(self, identity, manifests):
        """Compose existing versions without re-exporting geometry."""
        refs = [self.pin(path) for path in manifests]
        products = [self.load_handoff(ref['path']) for ref in refs]
        if not products or any(p['schema'] != PRODUCT for p in products):
            raise ValueError('Select published USD products')
        expected_shot = dict(zip(('episode', 'sequence', 'shot'), self._identity(identity)))
        plan = {key: products[0][key] for key in ('frame_range', 'fps', 'usd')}
        seen = set()
        for p in products:
            if p['shot'] != expected_shot or any(p[k] != plan[k] for k in plan):
                raise ValueError('Products belong to different shots, timing or units')
            key = (p['kind'], p['target'])
            if key in seen or (p['kind'] == 'camera' and any(k == 'camera' for k, _ in seen)):
                raise ValueError('Select one version per target and one Primary Camera')
            seen.add(key)
        version, directory = self._reserve(self.paths.composition_dir(*self._identity(identity), 'usd'))
        paths = self._build_composition_layers(identity, directory, products, plan)
        deps = paths.pop('_dependencies')
        for ref in refs + deps:
            self.check(ref)
        result = dict(plan, schema=COMPOSITION, status='published', approval='not_reviewed',
                      shot=expected_shot, version=version, partial=True,
                      included_targets=sorted(seen), products=refs, dependencies=deps,
                      layers={k: self.pin(v) for k, v in paths.items()}, entrypoint=self.pin(paths['shot']))
        manifest = self._file(directory, 'manifest.json')
        self._commit(manifest, result)
        return manifest


def layout_changes(package, node_map):
    from smartlib.dcc.maya.set_dress import composed_values, TRANSFORM_ATTRIBUTES
    result = []
    for change in composed_values(package.layers).values():
        path = node_map.get(change.node_id) or node_map.get(change.node)
        if not path or not path.startswith("/Shot/Assets/"):
            raise ValueError(f"Set Dress node requires an explicit Asset prim mapping: {change.node}")
        if change.attribute not in TRANSFORM_ATTRIBUTES:
            raise ValueError(f"Set Dress attribute has no USD mapping: {change.attribute}")
        if not isinstance(change.after, (float, int, bool)) or not math.isfinite(float(change.after)):
            raise ValueError("Set Dress requires finite scalar values")
        result.append({"path": path, "attribute": change.attribute, "value": change.after})
    return result


def set_layout_value(prim, attribute, value):
    """Author only the changed USD property, not unrelated transform groups."""
    from pxr import Gf, Usd, UsdGeom
    if attribute == 'visibility':
        UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(
            UsdGeom.Tokens.inherited if value else UsdGeom.Tokens.invisible)
        return
    common = UsdGeom.XformCommonAPI(prim)
    translate, rotate, scale, _pivot, order = common.GetXformVectors(Usd.TimeCode.Default())
    groups = {'translate': list(translate), 'rotate': list(rotate), 'scale': list(scale)}
    group = next(k for k in groups if attribute.startswith(k))
    groups[group]['XYZ'.index(attribute[-1])] = float(value)
    if group == 'translate':
        ok = common.SetTranslate(Gf.Vec3d(*groups[group]))
    elif group == 'rotate':
        ok = common.SetRotate(Gf.Vec3f(*groups[group]), order)
    else:
        ok = common.SetScale(Gf.Vec3f(*groups[group]))
    if not ok:
        raise ValueError(f'Unsupported Asset transform stack: {prim.GetPath()}')


def compose_layers(paths, products, plan, service):
    from pxr import Sdf, Usd, UsdGeom, UsdUtils
    stages = {}
    for key, path in paths.items():
        stage = Usd.Stage.CreateNew(str(path))
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Shot").GetPrim())
        stage.SetStartTimeCode(plan["frame_range"][0])
        stage.SetEndTimeCode(plan["frame_range"][1])
        stage.SetFramesPerSecond(plan["fps"])
        stage.SetTimeCodesPerSecond(plan["fps"])
        UsdGeom.SetStageMetersPerUnit(stage, plan["usd"]["meters_per_unit"])
        UsdGeom.SetStageUpAxis(stage, plan["usd"]["up_axis"])
        stages[key] = stage
    dependencies = {}
    for product in products:
        kind = product["kind"]
        if kind == "layout":
            continue
        source = service.check(product["entrypoint"])
        src_stage = Usd.Stage.Open(str(source))
        if not src_stage or composition_errors(src_stage):
            raise ValueError(f"Cannot compose USD input: {source}")
        if (not math.isclose(UsdGeom.GetStageMetersPerUnit(src_stage), plan["usd"]["meters_per_unit"])
                or UsdGeom.GetStageUpAxis(src_stage) != plan["usd"]["up_axis"]):
            raise ValueError(f"USD units/up-axis mismatch: {source}")
        roots = [src_stage.GetDefaultPrim()] if src_stage.GetDefaultPrim() else list(src_stage.GetPseudoRoot().GetChildren())
        if not roots:
            raise ValueError(f"USD input has no roots: {source}")
        if kind == "camera" and len([p for p in src_stage.Traverse() if p.IsA(UsdGeom.Camera)]) != 1:
            raise ValueError("Select a USD containing exactly one Primary Camera")
        animated = any(attr.GetNumTimeSamples() for p in src_stage.Traverse() for attr in p.GetAttributes())
        if animated and (not math.isclose(src_stage.GetTimeCodesPerSecond(), plan['fps'])
                         or src_stage.GetStartTimeCode() > plan['frame_range'][0]
                         or src_stage.GetEndTimeCode() < plan['frame_range'][1]):
            raise ValueError(f'Animated USD timing/range mismatch: {source}')
        layers, assets, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(source)))
        if unresolved:
            raise ValueError(f'Unresolved USD dependencies: {unresolved}')
        for asset in assets:
            ref = service.pin(asset)
            dependencies[ref['path']] = ref
        for layer in src_stage.GetUsedLayers():
            if not layer.anonymous:
                ref = service.pin(layer.realPath)
                dependencies[ref["path"]] = ref
        parent = {"animation": "Animation", "camera": "Camera", "assets": "Assets"}[kind]
        target = f"/Shot/{parent}/{product['target']}"
        stage = stages[kind]
        for root in roots:
            dest = target if len(roots) == 1 else target + "/" + root.GetName()
            stage.DefinePrim(dest).GetReferences().AddReference(str(source), root.GetPath())
        if composition_errors(stage):
            raise ValueError(f"USD reference errors: {source}")
    shot = stages["shot"]
    shot.GetRootLayer().subLayerPaths = [paths[k].name for k in ("animation", "layout", "camera", "assets")]
    with Usd.EditContext(shot, stages["layout"].GetRootLayer()):
        for product in reversed(products):
            if product["kind"] != "layout":
                continue
            for change in product["changes"]:
                prim = shot.GetPrimAtPath(Sdf.Path(change["path"]))
                if not prim or not prim.IsDefined():
                    raise ValueError(f"Missing Set Dress target: {change['path']}")
                if prim.IsInstanceProxy():
                    raise ValueError("Cannot override inside an instanceable Asset")
                set_layout_value(prim, change["attribute"], change["value"])
    for stage in stages.values():
        if composition_errors(stage):
            raise ValueError("USD composition contains unresolved references")
        stage.GetRootLayer().Save()
    return list(dependencies.values())
