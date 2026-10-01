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
SECTION = "smartpipeline.usd_handoff_section.v1"
KINDS = {"animation", "assets", "camera", "layout"}


def composition_errors(stage):
    """Maya 2024 ships USD without Stage.GetCompositionErrors."""
    if hasattr(stage, 'GetCompositionErrors'):
        return stage.GetCompositionErrors()
    return [error for prim in stage.TraverseAll() for error in prim.GetPrimIndex().localErrors]


class UsdHandoffService(AnimationCompositionService):
    def skel_versions(self, identity, target):
        """Fixed static Rig USD packages for this cast, independent of Maya Context."""
        from smartlib.core.path_resolver import AssetIdentity
        cast = (self.shots.load_cast(identity).get('cast') or {}).get(target)
        if not cast:
            return []
        root = self.shots.find_asset_root(cast.get('asset', ''))
        if not root:
            return []
        metadata = read_json(self.paths.artifact_file(root, 'asset.json'), {})
        asset = AssetIdentity(metadata.get('category') or cast.get('category', ''),
            metadata.get('group') or cast.get('group', 'main'), cast['asset'], cast.get('variant') or 'default')
        directory = self.paths.asset_publish_dir(asset, 'rig', '')
        result = []
        for manifest in directory.glob('*/v*/publish.json'):
            record = read_json(manifest, {})
            if record.get('status') != 'published' or record.get('usd_skel', {}).get('schema') != 'smartpipeline.usd_skel.v2':
                continue
            if not re.fullmatch(r'v[0-9]{3,}', record.get('version', '')):
                continue
            if any(record.get(key) != value for key, value in dict(asset=asset.name,
                    category=asset.category, group=asset.group, variant=asset.variant).items()):
                continue
            expected = self.paths.artifact_file(self.paths.asset_publish_version_dir(
                asset, 'rig', record.get('subset', ''), record.get('version', '')), 'publish.json')
            if expected.resolve() == manifest.resolve():
                result.append(dict(label=record['subset'] + ' / ' + record['version'], path=str(manifest)))
        return sorted(result, key=lambda r: (r['label'].split(' / ')[0], -int(r['label'].split(' / v')[1])))

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

    def plan(self, identity, rows, *, frame_range=None, fps=None, replace_assets=False):
        if not rows and not replace_assets:
            raise ValueError("Select at least one USD target")
        result = {"schema": PLAN, "shot": dict(zip(("episode", "sequence", "shot"), self._identity(identity))),
                  "frame_range": list(frame_range or self.shots.shot_frame_range(identity)),
                  "fps": float(self.shots.project_fps if fps is None else fps), "rows": [],
                  "usd": usd_settings(self.config.load("project_settings"))}
        if replace_assets:
            if any(row['kind'] != 'assets' for row in rows):
                raise ValueError('Assets registration must contain only Assets rows')
            result['replace_assets'] = True
        start, end = result["frame_range"]
        if not all(math.isfinite(float(v)) for v in (start, end, result["fps"])) or end < start or result["fps"] <= 0:
            raise ValueError("Invalid frame range or FPS")
        seen = set()
        for raw in rows:
            row = deepcopy(raw)
            kind, target = row["kind"], row["target"]
            if kind not in KINDS or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", target):
                raise ValueError(f"Invalid USD target: {kind}/{target}")
            if (kind, target) in seen or (kind == 'camera' and row.get('camera_role', 'primary') == 'primary'
                    and any(r['kind'] == 'camera' and r.get('camera_role', 'primary') == 'primary' for r in result['rows'])):
                raise ValueError("Duplicate target or more than one Primary Camera")
            seen.add((kind, target))
            if kind == 'layout' and row.get('layout_type'):
                category, name = row['layout_type'], row.get('layout_name', '')
                if (category not in ('placement', 'setdress') or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name)
                        or target != category + '_' + name):
                    raise ValueError('Invalid Layout category/name/target')
            if kind == 'camera':
                if row.get('camera_role', 'primary') not in ('primary', 'derived'):
                    raise ValueError('Invalid camera role')
                for key in ('camera_snapshot', 'primary_source'):
                    if row.get(key):
                        self.check(row[key])
                if row.get('camera_role') == 'derived' and not row.get('primary_source'):
                    raise ValueError('Derived Camera requires a fixed Primary source')
            if kind == 'assets':
                if row.get('asset_release'):
                    release_record = read_json(self.check(row['asset_release']), {})
                    if release_record.get('status') != 'complete' or release_record.get('schema') != 'smartpipeline.preview_release.v1':
                        raise ValueError('Cast requires a completed Asset USD Release')
                    if self.pin(release_record['absolute_files']['usd']) != self.pin(row['source']):
                        raise ValueError('Cast USD differs from its Release')
                    for dependency in release_record.get('dependencies', []):
                        self.check(dependency)
                provider = row.get('geometry_source', 'asset')
                if provider not in ('asset', 'animation', 'none'):
                    raise ValueError('Invalid Asset Geometry Source')
                if provider == 'asset' and not row.get('source'):
                    raise ValueError('Asset geometry requires a published USD')
                if row.get('registration'):
                    info = row['registration']
                    if not all(isinstance(info.get(k), str) and info[k] for k in ('name', 'category', 'group', 'variant')):
                        raise ValueError('Incomplete Cast Asset identity')
                row['source'] = self.pin(row['source']) if row.get('source') else None
                for ref in row.get('asset_dependencies', []):
                    self.check(ref)
                if row.get('placement'):
                    from .placement_motion import validate_placement
                    if provider != 'asset':
                        raise ValueError('Placement is owned by Animation for deforming assets')
                    validate_placement(row['placement'], result['frame_range'])
            else:
                row["source"] = self.pin(row["source"])
            if kind == 'layout' and row.get('layout_type') == 'placement':
                from .placement_motion import validate_placement
                payload = read_json(row['source']['path'], {})
                if payload.get('schema') != 'smartpipeline.placement_samples.v1':
                    raise ValueError('Expected Placement sample Data')
                validate_placement(payload['marker'], result['frame_range'])
                for item in payload['usd_placements']:
                    if not item['path'].startswith('/Shot/Assets/'):
                        raise ValueError('Placement requires an Asset target')
                    validate_placement(item['data'], result['frame_range'])
            if kind == "animation":
                if row.get('cast_asset'):
                    from .cast_release import animation_asset
                    cast, release = animation_asset(self, identity, target, row['cast_asset'])
                    row['asset_release'] = cast['inputs']['asset_release']
                    row['skel'] = release.get('rig', {}).get('path')
                if row.get('skel'):
                    row['skel'] = self.pin(row['skel'])
                    if not any(self.pin(v['path']) == row['skel'] for v in self.skel_versions(identity, target)):
                        raise ValueError('Static Rig USD belongs to another cast or is not published')
                    receipt = self.check(row['skel'])
                    record = read_json(receipt, {})
                    row['skel_entry'] = self.pin(self.paths.artifact_file(receipt.parent, record['usd_skel']['entry']))
                    if row.get('cast_asset'):
                        row['skel_entry'] = cast['entrypoint']
                    from smartlib.core.udim import usd_dependencies
                    layers, assets, unresolved = usd_dependencies(row['skel_entry']['path'])
                    if unresolved:
                        raise ValueError('Static Rig USD has unresolved dependencies')
                    row['skel_dependencies'] = [self.pin(p) for p in sorted(
                        {l.realPath for l in layers if not l.anonymous} | set(map(str, assets)))]
                    row['skeleton_set'] = self.config.usd_skel_contract.get('skeleton_set', 'skel_export_set')
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
            for key in ("source", "rig", "sculpt", "payload", "skel"):
                if item.get(key):
                    item[key] = str(self.check(item[key]))
            item.pop("payload", None)
            for ref in item.pop('skel_dependencies', []):
                self.check(ref)
            item.pop('skel_entry', None)
            item.pop('skeleton_set', None)
            raw.append(item)
        if self.plan(identity, raw, frame_range=plan["frame_range"], fps=plan["fps"],
                     replace_assets=plan.get('replace_assets', False)) != plan:
            raise ValueError("USD plan no longer matches its fixed inputs/settings")

    def composition_versions(self, identity):
        root = self.paths.composition_dir(*self._identity(identity), 'usd')
        rows = []
        for directory in root.glob('v*'):
            if not re.fullmatch(r'v[0-9]+', directory.name):
                continue
            manifest = self._file(directory, 'manifest.json')
            data = read_json(manifest, {})
            if data.get('schema') == COMPOSITION and data.get('status') == 'published':
                rows.append({'version': directory.name, 'path': str(manifest)})
        return sorted(rows, key=lambda row: int(row['version'][1:]), reverse=True)

    def composition_inputs(self, identity, plan, base_composition):
        if not base_composition:
            return [], []
        base = self.load_handoff(self.check(base_composition))
        if base.get('schema') != COMPOSITION or base.get('shot') != plan['shot']:
            raise ValueError('Base composition belongs to another shot')
        if any(base[key] != plan[key] for key in ('frame_range', 'fps', 'usd')):
            raise ValueError('Base composition range/FPS/units must match the new publish')
        replaced = {(row['kind'], row['target']) for row in plan['rows']}
        replace_camera = any(r['kind'] == 'camera' and r.get('camera_role', 'primary') == 'primary'
                             for r in plan['rows'])
        refs, products = [], []
        for ref in base['products']:
            product = self.load_handoff(self.check(ref))
            key = (product['kind'], product['target'])
            if plan.get('replace_assets') and product['kind'] == 'animation' and product.get('inputs', {}).get('cast_asset'):
                replacement = next((r for r in plan['rows'] if r['target'] == product['target']), {})
                if replacement.get('asset_release') != product['inputs'].get('asset_release'):
                    # Keep the immutable old Animation product, but do not apply it
                    # to an unverified replacement Release in the new composition.
                    continue
            if (key in replaced or (replace_camera and product['kind'] == 'camera')
                    or (plan.get('replace_assets') and product['kind'] == 'assets')):
                continue
            refs.append(ref)
            products.append(product)
        return refs, products

    def publish(self, identity, plan, *, animation_exporter=None, base_composition=None):
        self.validate_plan(plan, identity)
        retained_refs, retained_products = self.composition_inputs(identity, plan, base_composition)
        _, workspace = self._reserve(self.paths.usd_handoff_build_dir(*self._identity(identity)))
        products, pending = list(retained_products), []
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
                validation = data['validation']
                data['usd_kind'] = validation.get('representation', 'deform')
                if data['usd_kind'] == 'usd_skel_animation':
                    comparison = validation.get('comparison', {})
                    if not comparison.get('ok') or comparison.get('frames_checked') != int(plan['frame_range'][1] - plan['frame_range'][0] + 1):
                        raise ValueError('Skeletal publish requires full-frame deformation verification')
                    from smartlib.core.skel_animation import compose_animation
                    animation = self._file(directory, 'animation.usd')
                    source_animation = Path(validation['animation'])
                    if source_animation.resolve().parent != workspace.resolve():
                        raise ValueError('Animation export is outside the build workspace')
                    shutil.copy2(source_animation, animation)
                    if file_hash(source_animation) != file_hash(animation):
                        raise ValueError('Animation copy checksum mismatch')
                    published = self._file(directory, 'animation_asset.usda')
                    compose_animation(published, animation, self.check(row['skel_entry']),
                        validation['bindings'], plan['frame_range'], plan['fps'])
                    data['animation_layer'] = self.pin(animation)
                    validation.pop('animation', None)
                else:
                    published = self._file(directory, 'deform.usdc')
                    shutil.copy2(path, published)
                    if file_hash(path) != file_hash(published):
                        raise ValueError('USD copy checksum mismatch')
                data["entrypoint"] = self.pin(published)
                if row.get('cast_asset'):
                    from .cast_release import animation_asset
                    from smartlib.apps.smart_composition.looks import look_options
                    _, release = animation_asset(self, identity, target, row['cast_asset'])
                    if release.get('look'):
                        look_record = read_json(self.check(release['look']), {})
                        data['inputs'] = deepcopy(data['inputs'])
                        data['inputs']['preview_look'] = look_options(self, data, look_record['artifacts']['usd']['path'])
                if base_composition:
                    previous = self.load_handoff(self.check(base_composition))
                    for ref in previous['products']:
                        old = self.load_handoff(self.check(ref))
                        look = old.get('inputs', {}).get('preview_look')
                        if old['kind'] == kind and old['target'] == target and look:
                            from smartlib.apps.smart_composition.looks import look_options
                            try:
                                data['inputs'] = deepcopy(data['inputs'])
                                data['inputs']['preview_look'] = look_options(self, data, self.check(look['layer']))
                            except ValueError as exc:
                                data['look_warning'] = 'Previous Look was not compatible: ' + str(exc)
                            break
            elif kind in {"assets", "camera"}:
                data["entrypoint"] = row["source"]
            else:
                if row.get('layout_type') == 'placement':
                    data['placement_data'] = read_json(row['source']['path'], {})
                    data['changes'] = []
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
        sections = paths.pop('_sections')
        pending_sections = paths.pop('_pending_sections')
        self.validate_plan(plan, identity)
        self.composition_inputs(identity, plan, base_composition)
        for ref in dependencies:
            self.check(ref)
        # Products become visible only after the complete selection validates.
        for manifest, data in pending:
            data["dependencies"] = dependencies
            self._commit(manifest, data)
        self._commit_sections(identity, pending_sections)
        snapshot = {"schema": COMPOSITION, "status": "published", "approval": "not_reviewed",
                    "shot": plan["shot"], "version": version, "frame_range": plan["frame_range"],
                    "fps": plan["fps"], "usd": plan["usd"], "partial": True,
                    "included_targets": [[p["kind"], p["target"]] for p in products],
                    "products": retained_refs + [self.pin(p) for p, _ in pending], "dependencies": dependencies,
                    "base_composition": base_composition,
                    "sections": {key: self.pin(path) for key, path in sections.items()},
                    "layers": {key: self.pin(path) for key, path in paths.items()},
                    "entrypoint": self.pin(paths["shot"])}
        if plan.get('replace_assets') or (base_composition and
                self.load_handoff(self.check(base_composition)).get('assets_registration_complete')):
            snapshot['assets_registration_complete'] = True
        manifest = self._file(directory, "manifest.json")
        self._commit(manifest, snapshot)
        return manifest

    def _commit(self, manifest, data):
        pending = self._file(manifest.parent, "manifest.pending.json")
        write_json(pending, data)
        pending.replace(manifest)

    def _commit_sections(self, identity, pending):
        for manifest, data in pending:
            data['products'] = [self.pin(self._file(self.paths.usd_handoff_dir(
                *self._identity(identity), row['kind'], row['target'], row['version']), 'manifest.json'))
                for row in data['definition']['members']]
            self._commit(manifest, data)

    def section_versions(self, identity):
        result = {}
        for kind in sorted(KINDS):
            root = self.paths.usd_section_dir(*self._identity(identity), kind)
            rows = []
            for directory in root.glob('v*'):
                if not re.fullmatch(r'v[0-9]+', directory.name):
                    continue
                manifest = self._file(directory, 'manifest.json')
                data = read_json(manifest, {})
                if (data.get('schema') == SECTION and data.get('status') == 'published'
                        and data.get('kind') == kind
                        and data.get('shot') == dict(zip(('episode', 'sequence', 'shot'), self._identity(identity)))):
                    rows.append((directory.name, manifest.as_posix()))
            if rows:
                result[kind] = sorted(rows, key=lambda item: int(item[0][1:]), reverse=True)
        return result

    def section_products(self, identity, manifests):
        """Expand fixed Section selections through pinned product receipts."""
        from .usd_sections import section_definition
        refs, products, kinds = [], [], set()
        expected = dict(zip(('episode', 'sequence', 'shot'), self._identity(identity)))
        for manifest in manifests:
            ref = self.pin(manifest)
            data = self.load_handoff(self.check(ref))
            if data.get('schema') != SECTION or data.get('shot') != expected:
                raise ValueError('Select published Sections for this shot')
            if data['kind'] in kinds:
                raise ValueError('Select one version per Section')
            kinds.add(data['kind'])
            members = [self.load_handoff(self.check(p)) for p in data['products']]
            expected_definition = section_definition(data['kind'], members, data)
            for policy in ('skel_extents_version', 'preview_look_merge_version'):
                if policy not in data['definition']:
                    expected_definition.pop(policy, None)
            if expected_definition != data['definition']:
                raise ValueError('Section product receipts no longer match its definition')
            refs.append(ref)
            products.extend(p['path'] for p in data['products'])
        self.select_products(identity, products)
        return refs, products

    def _build_composition_layers(self, identity, directory, products, plan):
        from smartlib.apps.shot_manager.usd_sections import build_sections
        return build_sections(self, identity, directory, products, plan)

    def load_handoff(self, manifest):
        data = read_json(manifest, {})
        if data.get("schema") not in {PRODUCT, COMPOSITION, SECTION} or data.get("status") != "published":
            raise ValueError("Not a completed USD handoff")
        for ref in data.get("dependencies", []) + data.get("products", []):
            self.check(ref)
        for ref in data.get("layers", {}).values():
            self.check(ref)
        if data.get("entrypoint"):
            self.check(data["entrypoint"])
        if data.get('schema') == COMPOSITION:
            for kind, ref in data.get('sections', {}).items():
                section = self.load_handoff(self.check(ref))
                if section.get('schema') != SECTION or section.get('kind') != kind or section.get('shot') != data.get('shot'):
                    raise ValueError('Invalid Composition section reference')
        return data

    def compose_products(self, identity, manifests):
        """Compose existing versions without re-exporting geometry."""
        refs, products, plan, seen = self.select_products(identity, manifests)
        expected_shot = dict(zip(('episode', 'sequence', 'shot'), self._identity(identity)))
        version, directory = self._reserve(self.paths.composition_dir(*self._identity(identity), 'usd'))
        paths = self._build_composition_layers(identity, directory, products, plan)
        deps = paths.pop('_dependencies')
        sections = paths.pop('_sections')
        pending_sections = paths.pop('_pending_sections')
        for ref in refs + deps:
            self.check(ref)
        self._commit_sections(identity, pending_sections)
        result = dict(plan, schema=COMPOSITION, status='published', approval='not_reviewed',
                      shot=expected_shot, version=version, partial=True,
                      included_targets=sorted(seen), products=refs, dependencies=deps,
                      sections={k: self.pin(v) for k, v in sections.items()},
                      layers={k: self.pin(v) for k, v in paths.items()}, entrypoint=self.pin(paths['shot']))
        manifest = self._file(directory, 'manifest.json')
        self._commit(manifest, result)
        return manifest

    def adopt_preview_look(self, identity, product_manifest, look_path, mesh_map):
        """Create a new Animation product receipt; keep the existing deform fixed."""
        previous = self.pin(product_manifest)
        data = deepcopy(self.load_handoff(self.check(previous)))
        expected = dict(zip(('episode', 'sequence', 'shot'), self._identity(identity)))
        if data.get('schema') != PRODUCT or data.get('kind') not in ('animation', 'assets') or data['shot'] != expected:
            raise ValueError('Select an Animation USD product for this shot')
        from pxr import Usd
        from smartlib.core.preview_look import apply_preview_look
        dependencies = []
        look = None
        if look_path:
            look = self.pin(look_path)
            geometry = Usd.Stage.Open(str(self.check(data['entrypoint'])))
            scratch = Usd.Stage.CreateInMemory()
            assets = apply_preview_look(scratch, '/Preview', geometry, self.check(look), mesh_map)
            dependencies = [self.pin(path) for path in assets]
            data['inputs']['preview_look'] = dict(layer=look, mesh_map=dict(mesh_map), dependencies=dependencies)
            data['inputs'].pop('preview_look_disabled', None)
        else:
            data['inputs'].pop('preview_look', None)
            data['inputs']['preview_look_disabled'] = True
        data['inputs']['previous_product'] = previous
        data['dependencies'] = list({r['path']: r for r in data.get('dependencies', []) + [previous] + ([look] if look else []) + dependencies}.values())
        for ref in data['dependencies']:
            self.check(ref)
        version, directory = self._reserve(self.paths.usd_handoff_dir(
            *self._identity(identity), data['kind'], data['target']))
        data.update(version=version, created_at=datetime.now(timezone.utc).isoformat())
        manifest = self._file(directory, 'manifest.json')
        self._commit(manifest, data)
        return manifest

    def select_products(self, identity, manifests):
        """Shared validation for preview and publication."""
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
            if key in seen:
                raise ValueError('Select one version per target and one Primary Camera')
            seen.add(key)
        validate_cameras(products)
        return refs, products, plan, seen



def validate_cameras(products):
    cameras = [p for p in products if p['kind'] == 'camera']
    primary = [p for p in cameras if p.get('inputs', {}).get('camera_role', 'primary') == 'primary']
    if cameras and len(primary) != 1:
        raise ValueError('Select exactly one Primary Camera')
    for camera in cameras:
        inputs = camera.get('inputs', {})
        if inputs.get('camera_role') == 'derived':
            original = primary[0].get('inputs', {})
            if (not inputs.get('primary_source') or inputs['primary_source'] != original.get('camera_snapshot')
                    or inputs.get('primary_fingerprint') != original.get('primary_fingerprint')):
                raise ValueError('Derived Camera does not match the selected Primary version')


def layout_changes(package, node_map):
    from smartlib.dcc.maya.set_dress import composed_values, TRANSFORM_ATTRIBUTES
    node_names = {}
    for layer in package.layers:
        if layer.muted:
            continue
        for change in layer.changes:
            if change.node_id in node_names and node_names[change.node_id] != change.node:
                raise ValueError('Duplicate legacy Set Dress node IDs; re-record layers for referenced instances')
            node_names[change.node_id] = change.node
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


def compose_layers(paths, products, plan, service, *, in_memory=False):
    from pxr import Sdf, Usd, UsdGeom, UsdUtils
    validate_cameras(products)
    placement_targets = set()
    placed_targets = set()
    animation_targets = {p['target'] for p in products if p['kind'] == 'animation'}
    skeletal_overrides = {p['target']: p for p in products if p['kind'] == 'animation'
                          and p.get('inputs', {}).get('cast_asset') and p.get('animation_layer')
                          and p.get('usd_kind') == 'usd_skel_animation'}
    cast_releases = {p['target']: p for p in products if p['kind'] == 'assets' and p.get('inputs', {}).get('asset_release')}
    for product in products:
        if product['kind'] == 'animation' and product.get('inputs', {}).get('cast_asset'):
            cast = cast_releases.get(product['target'])
            if not cast or cast['inputs']['asset_release'] != product['inputs']['asset_release']:
                raise ValueError(product['target'] + ': Cast Release differs from verified Animation; republish Animation against this Cast')
    for product in products:
        if (product['kind'] == 'assets' and product['target'] in animation_targets
                and product.get('inputs', {}).get('geometry_source', 'asset') == 'asset'
                and not product.get('inputs', {}).get('asset_release')):
            raise ValueError('Duplicate geometry provider for ' + product['target'] +
                             ': choose Animation USD / Metadata only for its Asset registration')
    stages = {}
    for key, path in paths.items():
        stage = Usd.Stage.CreateInMemory(key + '.usda') if in_memory else Usd.Stage.CreateNew(str(path))
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
        options = product.get('inputs', {})
        for key in ('camera_snapshot', 'primary_source'):
            if options.get(key):
                service.check(options[key])
                dependencies[options[key]['path']] = options[key]
        for ref in options.get('asset_dependencies', []):
            service.check(ref)
            dependencies[ref['path']] = ref
        if kind == 'assets':
            prim = stages['assets'].DefinePrim('/Shot/Assets/' + product['target'])
            info = dict(options.get('registration', {}))
            info.update(cast_key=product['target'], geometry_source=options.get('geometry_source', 'asset'))
            if product.get('entrypoint'):
                source = service.check(product['entrypoint'])
                dependencies[product['entrypoint']['path']] = product['entrypoint']
                info['usd_path'] = str(source)
            prim.SetCustomDataByKey('smartpipeline', info)
            if options.get('geometry_source', 'asset') != 'asset' or (options.get('asset_release')
                    and product['target'] in animation_targets and product['target'] not in skeletal_overrides):
                # Inventory only: no reference/payload, even with Stage.LoadAll.
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
        if kind == 'assets' and len(roots) == 1:
            placement_targets.add(product['target'])
        if kind == "camera" and len([p for p in src_stage.Traverse() if p.IsA(UsdGeom.Camera)]) != 1:
            raise ValueError("Select a USD containing exactly one Primary Camera")
        animated = any(attr.GetNumTimeSamples() for p in src_stage.Traverse() for attr in p.GetAttributes())
        if animated and (not math.isclose(src_stage.GetTimeCodesPerSecond(), plan['fps'])
                         or src_stage.GetStartTimeCode() > plan['frame_range'][0]
                         or src_stage.GetEndTimeCode() < plan['frame_range'][1]):
            raise ValueError(f'Animated USD timing/range mismatch: {source}')
        from smartlib.core.udim import usd_dependencies
        layers, assets, unresolved = usd_dependencies(source)
        if unresolved:
            raise ValueError(f'Unresolved USD dependencies: {unresolved}')
        for asset in assets:
            ref = service.pin(asset)
            dependencies[ref['path']] = ref
        # Include inactive quality/asset variants, not only the current working set.
        for layer in set(layers) | set(src_stage.GetUsedLayers()):
            if not layer.anonymous:
                ref = service.pin(layer.realPath)
                dependencies[ref["path"]] = ref
        parent = {"animation": "Animation", "camera": "Camera", "assets": "Assets"}[kind]
        if kind == 'animation' and options.get('cast_asset'):
            parent = 'Assets'
        target = f"/Shot/{parent}/{product['target']}"
        stage = stages[kind]
        for root in roots:
            dest = target if len(roots) == 1 else target + "/" + root.GetName()
            prim = stage.DefinePrim(dest)
            if kind == 'assets':
                prim.GetPayloads().AddPayload(str(source), root.GetPath())
                if options.get('registration', {}).get('variant'):
                    prim.GetVariantSets().GetVariantSet('variant').SetVariantSelection(options['registration']['variant'])
                if options.get('placement'):
                    if len(roots) != 1:
                        raise ValueError('Smart Maker placement requires a single Asset defaultPrim')
                    from .placement_motion import author_placement
                    author_placement(prim, options['placement'])
            else:
                if kind == 'animation' and product['target'] in skeletal_overrides:
                    animation_source = service.check(product['animation_layer'])
                    dependencies[product['animation_layer']['path']] = product['animation_layer']
                    prim.GetReferences().AddReference(str(animation_source), root.GetPath())
                    prim.SetTypeName(root.GetTypeName())
                    from pxr import UsdSkel
                    for binding in product['validation']['bindings']:
                        skeleton = Sdf.Path(binding['target_skeleton']).ReplacePrefix(root.GetPath(), Sdf.Path(dest))
                        animation = Sdf.Path(binding['animation_source']).ReplacePrefix(root.GetPath(), Sdf.Path(dest))
                        UsdSkel.BindingAPI.Apply(stage.OverridePrim(skeleton)).CreateAnimationSourceRel().SetTargets([animation])
                else:
                    prim.GetReferences().AddReference(str(source), root.GetPath())
                if kind == 'camera':
                    prim.SetCustomDataByKey('smartpipeline:camera_role', options.get('camera_role', 'primary'))
                    if options.get('camera_settings'):
                        import json
                        prim.SetCustomDataByKey('smartpipeline:camera_settings', json.dumps(options['camera_settings']))
        if composition_errors(stage):
            raise ValueError(f"USD reference errors: {source}")
        if options.get('preview_look'):
            if kind not in ('animation', 'assets') or len(roots) != 1:
                raise ValueError('Preview Look requires a single-root Animation product')
            from smartlib.core.preview_look import apply_preview_look
            look = options['preview_look']
            look_path = service.check(look['layer'])
            dependencies[look['layer']['path']] = look['layer']
            for ref in look['dependencies']:
                service.check(ref)
                dependencies[ref['path']] = ref
            assets = apply_preview_look(stage, target, src_stage, look_path, look['mesh_map'])
            if {service.pin(p)['path'] for p in assets} != {r['path'] for r in look['dependencies']}:
                raise ValueError('Preview Look texture dependencies changed')
        if options.get('preview_look_disabled'):
            from pxr import UsdShade
            for mesh in src_stage.Traverse():
                if mesh.IsA(UsdGeom.Mesh):
                    destination = mesh.GetPath().ReplacePrefix(roots[0].GetPath(), Sdf.Path(target))
                    prim = stage.OverridePrim(destination)
                    UsdShade.MaterialBindingAPI.Apply(prim).UnbindAllBindings()
                    prim.CreateRelationship('material:binding').SetTargets([])
    shot = stages["shot"]
    shot.GetRootLayer().subLayerPaths = [stages[k].GetRootLayer().identifier if in_memory else paths[k].name
        for k in ("animation", "layout", "camera", "assets")]
    if animation_targets:
        from smartlib.core.skel_extents import author_skel_extents
        with Usd.EditContext(shot, stages['animation'].GetRootLayer()):
            author_skel_extents(shot, plan['frame_range'])
    layouts = [p for p in products if p['kind'] == 'layout']
    layouts.sort(key=lambda p: (p.get('inputs', {}).get('layout_type') == 'placement',
                               p.get('inputs', {}).get('layer_order', 0)))
    for product in layouts:
        if product.get('inputs', {}).get('layout_type'):
            key = 'layout:' + product['target']
            stages[key] = Usd.Stage.CreateInMemory(product['target'] + '.usda')
    stages['layout'].GetRootLayer().subLayerPaths = [stages['layout:' + p['target']].GetRootLayer().identifier
        for p in layouts if p.get('inputs', {}).get('layout_type')]
    for product in reversed(layouts):
        layer = stages.get('layout:' + product['target'], stages['layout']).GetRootLayer()
        with Usd.EditContext(shot, layer):
            placement = product.get('placement_data')
            if placement:
                from .placement_motion import author_placement
                marker = UsdGeom.Xform.Define(shot, '/Shot/Placements/' + product['inputs']['layout_name']).GetPrim()
                author_placement(marker, placement['marker'])
                for item in placement['usd_placements']:
                    target = item['path'].split('/')[3]
                    if target in animation_targets:
                        raise ValueError('Placement is owned by Animation for ' + target)
                    if target not in placement_targets:
                        raise ValueError('Missing single Asset geometry root for Placement: ' + target)
                    path, placement_data = item['path'], item['data']
                    if item.get('assembly'):
                        if item['assembly']['cast'] != target:
                            raise ValueError('Assembly Placement parent differs from Asset target')
                        from .assembly_placement import resolve
                        path, placement_data = resolve(shot, item['assembly'], placement_data)
                    if any(path == previous or path.startswith(previous + '/') or previous.startswith(path + '/')
                           for previous in placed_targets):
                        raise ValueError('Multiple Placement products target the same Asset: ' + target)
                    placed_targets.add(path)
                    prim = shot.GetPrimAtPath(path)
                    if not prim or not prim.IsDefined() or prim.IsInstanceProxy():
                        raise ValueError('Missing or instanceable Placement target: ' + item['path'])
                    if any(p['kind'] == 'assets' and p['target'] == target and p.get('inputs', {}).get('placement') for p in products):
                        raise ValueError('Republish Assets without embedded placements before publishing Layout: ' + target)
                    author_placement(prim, placement_data)
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
        if not in_memory:
            stage.GetRootLayer().Save()
    if in_memory:
        return shot, stages, list(dependencies.values())
    return list(dependencies.values())
