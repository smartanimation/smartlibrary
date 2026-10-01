"""USD texture identifiers and explicitly pinned UDIM tile sets."""
from pathlib import Path
import re


def udim_pattern(value):
    value = str(value)
    if '<UDIM>' in value:
        return value
    return re.sub(r'(?<=[._-])1\d{3}(?=\.[^.]+$)', '<UDIM>', value)


def anchored_identifier(value, layer):
    from pxr import Sdf
    result = Sdf.ComputeAssetPathRelativeToLayer(layer, value) if layer else value
    path = Path(result)
    if not path.is_absolute() and layer and layer.realPath:
        path = Path(layer.realPath).parent / path
    return path.resolve().as_posix()


def asset_files(value, layer=None):
    from pxr import Sdf, UsdShade
    value = str(value)
    if '<UDIM>' in value:
        if hasattr(UsdShade, 'UdimUtils'):
            tiles = UsdShade.UdimUtils.ResolveUdimTilePaths(value, layer)
        else:
            # Maya 2024 bundles USD without UdimUtils. Keep compatibility here.
            anchored = anchored_identifier(value, layer)
            pattern = Path(anchored)
            matcher = re.compile('^' + re.escape(pattern.name).replace(re.escape('<UDIM>'), r'(1\d{3})') + '$')
            tiles = [(str(p), matcher.fullmatch(p.name).group(1))
                     for p in sorted(pattern.parent.iterdir())
                     if p.is_file() and matcher.fullmatch(p.name)] if pattern.parent.is_dir() else []
        if not tiles:
            raise ValueError('No UDIM tiles found: ' + value)
        return [str(Path(path).resolve()) for path, _ in tiles]
    anchored = Sdf.ComputeAssetPathRelativeToLayer(layer, value) if layer else value
    path = Path(anchored).resolve()
    if not path.is_file():
        raise ValueError('Unresolved texture: ' + value)
    return [str(path)]


def usd_dependencies(path):
    """Expand UDIM patterns that ComputeAllDependencies reports as unresolved."""
    from pxr import Sdf, UsdUtils
    layers, assets, missing = UsdUtils.ComputeAllDependencies(str(path))
    anchor = Sdf.Layer.FindOrOpen(str(path))
    files, unresolved = [], []
    for layer in layers:
        try:
            validate_tile_contract(layer)
        except ValueError as exc:
            unresolved.append(str(exc))
    for value in list(assets) + list(missing):
        if '<UDIM>' in value:
            try:
                files.extend(asset_files(value, anchor))
            except ValueError:
                unresolved.append(value)
        elif value in missing:
            unresolved.append(value)
        else:
            files.append(value)
    return layers, sorted(set(files)), unresolved


def validate_tile_contract(layer):
    for pattern, expected in layer.customLayerData.get('preview_udim_tiles', {}).items():
        actual = ','.join(sorted(Path(p).name for p in asset_files(pattern, layer)))
        if actual != expected:
            raise ValueError('Published UDIM tile set changed: ' + pattern)


def published_udim(source, published):
    """Match every source tile and every selected published tile by name and hash."""
    from smartlib.apps.shot_manager.animation_publish import file_hash
    pattern = udim_pattern(source)
    if '<UDIM>' not in pattern:
        raise ValueError('UDIM requires a tile number or <UDIM> token: ' + str(source))
    name_pattern = Path(pattern).name
    expected = re.compile('^' + re.escape(name_pattern).replace(re.escape('<UDIM>'), r'(1\d{3})') + '$', re.I)
    refs = [ref for ref in published if expected.fullmatch(Path(ref['path']).name)]
    sources = {Path(p).name.casefold(): p for p in asset_files(pattern)}
    if not refs or {Path(r['path']).name.casefold() for r in refs} != set(sources):
        raise ValueError('UDIM tile set differs from selected Texture Publish: ' + pattern)
    for ref in refs:
        name = Path(ref['path']).name.casefold()
        if file_hash(Path(ref['path'])) != ref['sha256'] or file_hash(Path(sources[name])) != ref['sha256']:
            raise ValueError('UDIM tile content differs from selected Texture Publish: ' + name)
    return name_pattern, refs


def materialize_tiles(pattern, refs, destination):
    """destination(name) is supplied by ProjectPaths for production outputs."""
    import shutil
    from smartlib.apps.shot_manager.animation_publish import file_hash
    matcher = re.compile('^' + re.escape(Path(pattern).name).replace(re.escape('<UDIM>'), r'1\d{3}') + '$')
    if '<UDIM>' not in pattern or not refs:
        raise ValueError('Invalid UDIM tile set')
    names = set()
    for ref in refs:
        source = Path(ref['path'])
        if not matcher.fullmatch(source.name) or source.name.casefold() in names:
            raise ValueError('Invalid or duplicate UDIM tile: ' + source.name)
        names.add(source.name.casefold())
        if file_hash(source) != ref['sha256']:
            raise ValueError('Published UDIM tile changed: ' + str(source))
        output = Path(destination(source.name))
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            if file_hash(output) != ref['sha256']:
                raise ValueError('UDIM destination collision: ' + str(output))
        else:
            shutil.copy2(source, output)
        if file_hash(output) != ref['sha256']:
            raise ValueError('UDIM tile changed during copy: ' + str(output))
    # Pattern is an asset identifier, not a literal file passed to the path resolver.
    first = Path(destination(Path(refs[0]['path']).name))
    return first.with_name(Path(pattern).name).as_posix()
