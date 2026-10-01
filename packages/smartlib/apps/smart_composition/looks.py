"""Published Asset Looks and validated deform-to-look bindings."""
from pathlib import Path
from smartlib.core.metadata import read_json
from smartlib.core.path_resolver import AssetIdentity


def look_versions(service, product):
    inputs = product.get('inputs', {})
    rig = (inputs.get('rig') or inputs.get('asset_release') or {}).get('path')
    if product.get('kind') not in ('animation', 'assets') or not rig:
        return []
    result = []
    for parent in Path(rig).parents:
        candidate = service.paths.artifact_file(parent, 'look')
        if not candidate.is_dir():
            continue
        for manifest in candidate.rglob('publish.json'):
            record = read_json(manifest, {}) or {}
            if record.get('publish_type') != 'look' or record.get('status') != 'published':
                continue
            identity = AssetIdentity(record['category'], record['group'], record['asset'], record['variant'])
            if not Path(rig).resolve().is_relative_to(service.paths.asset_variant_root(identity).resolve()):
                continue
            expected = service.paths.artifact_file(service.paths.asset_publish_version_dir(
                identity, 'look', record['subset'], record['version']), 'publish.json')
            if expected.resolve() != manifest.resolve():
                continue
            root = service.paths.asset_publish_dir(identity, 'look', record['subset'])
            latest = read_json(service.paths.artifact_file(root, 'latest.json'), {}) or {}
            result.append(dict(label=f"{record['subset']} / {record['version']}",
                path=record['artifacts']['usd']['path'], manifest=str(manifest),
                latest=latest.get('version') == record['version']))
        break
    return sorted(result, key=lambda r: (r['label'].split(' / ')[0], -int(r['label'].split(' / v')[1])))


def selected_look(product):
    inputs = product.get('inputs', {})
    if inputs.get('preview_look_disabled'):
        return ''
    if inputs.get('preview_look'):
        return inputs['preview_look']['layer']['path']
    release = read_json(inputs.get('asset_release', {}).get('path'), {}) if inputs.get('asset_release') else {}
    look = read_json(release['look']['path'], {}) if release.get('look') else {}
    return look.get('artifacts', {}).get('usd', {}).get('path', '')


def look_options(service, product, look_path):
    from pxr import Usd, UsdGeom
    from smartlib.core.preview_look import apply_preview_look
    ref = service.pin(look_path)
    geometry = Usd.Stage.Open(str(service.check(product['entrypoint'])))
    look = Usd.Stage.Open(str(service.check(ref)))
    contract = look.GetRootLayer().customLayerData.get('preview_meshes', {})
    names = {}
    for prim in geometry.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            name = prim.GetName().removeprefix(product['target'] + '_')
            names.setdefault(name, []).append(str(prim.GetPath()))
    mapping = {}
    for path in contract:
        candidates = names.get(path.rsplit('/', 1)[-1], [])
        if len(candidates) != 1:
            raise ValueError(f"{product['target']}: missing or ambiguous mesh for {path}")
        mapping[path] = candidates[0]
    assets = apply_preview_look(Usd.Stage.CreateInMemory(), '/Preview', geometry, look_path, mapping)
    return dict(layer=ref, mesh_map=mapping, dependencies=[service.pin(p) for p in assets])
