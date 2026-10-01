"""Readable, recorded provenance; display does not change composition selection."""
from smartlib.core.metadata import read_json


def reference_text(data, manifest=''):
    lines = []
    if manifest:
        lines.append('Manifest: ' + str(manifest))
    for label, key in [('Type', 'kind'), ('Target', 'target'), ('Version', 'version')]:
        if data.get(key):
            lines.append(f'{label}: {data[key]}')
    entry = data.get('entrypoint') or {}
    if entry.get('path'):
        lines.extend(['', 'USD: ' + entry['path']])
    validation = data.get('validation') or {}
    if data.get('usd_kind') or validation.get('representation'):
        lines.append('Representation: ' + str(data.get('usd_kind') or validation['representation']))
    if validation.get('fallback_reason'):
        lines.append('Deform fallback: ' + validation['fallback_reason'])
    if data.get('look_warning'):
        lines.append(data['look_warning'])
    def collect(value, label):
        if isinstance(value, dict):
            if 'path' in value:
                lines.append(f'{label}: {value["path"]}')
            else:
                for key, item in value.items():
                    collect(item, f'{label}.{key}' if label else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                collect(item, f'{label}[{index + 1}]')
    collect(data.get('inputs', {}), 'Input')
    collect(data.get('products', []), 'Product')
    collect(data.get('dependencies', []), 'Dependency')
    return '\n'.join(lines)


def reference_summary(paths, manifest):
    path = paths.project_dependency(manifest)
    data = read_json(path, {})
    members = (data.get('definition') or {}).get('members', [])
    return reference_text(data, path), [
        (member.get('target', '?'), member.get('version', ''),
         (member.get('entrypoint') or {}).get('path', ''), reference_text(member))
        for member in members]
