"""Cast inventory authoring; load/quality policy belongs to Smart Composition."""
from dataclasses import asdict

from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
from smartlib.core.metadata import read_json
from smartlib.core.path_resolver import AssetIdentity


class AssetsPublishService(UsdHandoffService):
    def cast_entries(self, identity):
        result = []
        for target, cast in sorted((self.shots.load_cast(identity).get('cast') or {}).items()):
            root = self.shots.find_asset_root(cast.get('asset', ''))
            metadata = read_json(self.paths.artifact_file(root, 'asset.json'), {}) if root else {}
            asset = AssetIdentity(metadata.get('category') or cast.get('category', ''),
                metadata.get('group') or cast.get('group', 'main'), cast.get('asset', ''),
                cast.get('variant') or 'default')
            row = dict(target=target, asset=asset, versions=[], error='')
            category = asset.category.casefold()
            row['geometry_source'] = 'animation' if category in ('ch', 'character', 'characters') else 'asset'
            try:
                if not asset.name or not asset.category:
                    raise ValueError('Cast Asset identity is incomplete')
                row['versions'] = self.shots.asset_publish_resolver.list_usd_versions(asset)
            except (OSError, ValueError, KeyError) as exc:
                row['error'] = str(exc)
            result.append(row)
        return result

    def registration_plan(self, identity, selections, base=None):
        rows = []
        for selection in selections:
            asset = selection['asset']
            version = selection.get('version')
            source = None
            dependencies = []
            if version:
                resolved = self.shots.asset_publish_resolver.resolve_usd_entry(
                    asset, version=version, quality=None)
                source = resolved['path']
                for ref in resolved.get('usd_dependencies', []):
                    pinned = self.pin(ref['path'])
                    if pinned['sha256'] != ref['sha256']:
                        raise ValueError('Asset USD Pack dependency changed: ' + ref['path'])
                    dependencies.append(pinned)
            elif selection['geometry_source'] == 'asset':
                raise ValueError(selection['target'] + ': select a published Asset USD Version')
            info = asdict(asset)
            info['usd_version'] = version or ''
            rows.append(dict(kind='assets', target=selection['target'], source=source,
                geometry_source=selection['geometry_source'], registration=info,
                asset_dependencies=dependencies))
        timing = {}
        if base:
            snapshot = self.load_handoff(self.check(base))
            timing = dict(frame_range=snapshot['frame_range'], fps=snapshot['fps'])
        plan = self.plan(identity, rows, replace_assets=True, **timing)
        _, retained = self.composition_inputs(identity, plan, base)
        animated = {p['target'] for p in retained if p['kind'] == 'animation'}
        for row in rows:
            if row['target'] in animated and row['geometry_source'] == 'asset':
                raise ValueError(row['target'] + ': duplicate geometry; choose Animation USD')
        return plan
