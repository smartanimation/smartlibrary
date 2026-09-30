"""Shot browsing through the existing Shot Manager and ProjectPaths."""
import re
from smartlib.core.metadata import read_json
from smartlib.apps.shot_manager.usd_handoff import COMPOSITION


def composition_versions(shots, identity):
    paths = shots.paths
    root = paths.composition_dir(identity.episode, identity.sequence, identity.shot, 'usd')
    result = []
    if not root.is_dir():
        return result
    expected = dict(episode=identity.episode, sequence=identity.sequence, shot=identity.shot)
    for directory in root.iterdir():
        if not directory.is_dir() or not re.fullmatch(r'v[0-9]{3,}', directory.name):
            continue
        manifest = paths.artifact_file(directory, 'manifest.json')
        try:
            data = read_json(manifest, {})
        except (OSError, ValueError):
            continue
        if (data.get('schema') == COMPOSITION and data.get('status') == 'published'
                and data.get('shot') == expected):
            result.append((directory.name, manifest.as_posix()))
    return sorted(result, key=lambda row: int(row[0][1:]), reverse=True)
