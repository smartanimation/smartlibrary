"""Read-only camera package inspection for desktop UIs; no Maya dependency."""
from pathlib import Path

from .metadata import read_json

SCHEMA = "smartpipeline.camera_package.v1"


def camera_package_info(path):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != '.json':
        return {}
    try:
        data = read_json(path, {}) or {}
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get('schema') not in (SCHEMA, 'smartpipeline.camera_package.v2'):
        return {}
    cameras, rows = data.get('cameras'), data.get('rows')
    if not isinstance(cameras, list) or not isinstance(rows, list):
        return {}
    if any(not isinstance(c, dict) for c in cameras + rows):
        return {}
    primary = next((c for c in cameras if c.get('role') == 'primary'), {})
    lines = [f"Primary: {primary.get('name', '(missing)')}",
             f"Reference: {' × '.join(map(str, data.get('reference_resolution', [])))}"]
    portable = data.get('portable_export') or {}
    if portable:
        status = str(portable.get('status') or 'unknown').upper()
        camera_name = str(portable.get('camera_name') or 'primary_cam')
        formats = ', '.join(sorted((portable.get('files') or {}).keys()))
        suffix = f" ({formats})" if formats else ''
        lines.append(f"Exchange: {camera_name} | {status}{suffix}")
        if portable.get('error'):
            lines.append(f"Exchange error: {portable['error']}")
    for row in rows:
        rule = row.get('camera_rule') or {}
        mode = rule.get('mode', row.get('camera_fit', 'horizontal'))
        if mode == 'scale':
            mode = f"expand ×{rule.get('scale', 1.1)}"
        lines.append(f"{row.get('layer', '')}: {row.get('camera', '')} | "
                     f"{row.get('width')} × {row.get('height')} | {row.get('start')}–{row.get('end')} | "
                     f"{mode} | v{row.get('version')} / t{row.get('take')}")
    return dict(target=data.get('target', 'main'), subset=data.get('subset', 'main'),
                version=data.get('version', ''), summary='\n'.join(lines),
                primary=primary.get('name', ''), path=str(path),
                portable_status=str(portable.get('status') or ''))
