"""Conservative Maya-to-USD matching, scoped to a Cast instance."""


def source_path(node):
    return '|' + '|'.join(part.rsplit(':', 1)[-1] for part in node.split('|') if part)


def prim_record(stage, path):
    from pxr import Sdf, UsdGeom
    if not Sdf.Path.IsValidPathString(path):
        raise ValueError('Invalid USD prim path: ' + path)
    prim = stage.GetPrimAtPath(path)
    if not path.startswith('/Shot/Assets/') or not prim or not prim.IsDefined() or prim.IsInstanceProxy() or not UsdGeom.Xformable(prim):
        raise ValueError('Select an editable USD Asset transform: ' + path)
    root = stage.GetPrimAtPath('/'.join(path.split('/')[:4]))
    info = root.GetCustomDataByKey('smartpipeline') or {}
    return dict(path=path, asset_source=info.get('usd_path', ''),
                asset={key: info.get(key, '') for key in ('cast_key', 'name', 'category', 'group', 'variant')},
                source_id=prim.GetCustomDataByKey('smartpipeline:setdress:sourceId') or '')


def resolve(stage, nodes, cast, saved=None, source_ids=None):
    """Only authoritative unique matches and unchanged saved choices are filled in."""
    records = {}
    for prim in stage.Traverse():
        path = str(prim.GetPath())
        if path.startswith('/Shot/Assets/'):
            try:
                records[path] = prim_record(stage, path)
            except ValueError:
                pass
    result = {}
    for key, node in nodes.items():
        namespaces = [part.rsplit(':', 1)[0] for part in node.split('|') if ':' in part]
        instances = [target for target, entry in cast.items()
                     if str(entry.get('namespace') or target).strip(':') in namespaces]
        scoped = [path for path in records if path.split('/')[3] in instances]
        automatic = []
        normalized = source_path(node)
        for path in scoped:
            prim = stage.GetPrimAtPath(path)
            original = prim.GetCustomDataByKey('smartpipeline:setdress:sourcePath') or ''
            source_id = records[path]['source_id']
            if (source_id and source_id == (source_ids or {}).get(key, key)) or (original and normalized.endswith(original)):
                automatic.append(path)
        old = (saved or {}).get(key)
        selected, state = '', 'Choose a prim'
        if old and old == records.get(old.get('path')):
            selected, state = old['path'], 'Saved mapping'
        elif len(automatic) == 1 and len(instances) == 1:
            selected, state = automatic[0], 'Asset mapping'
        elif old:
            state = 'Asset changed — confirm mapping'
        elif len(automatic) > 1:
            state = 'Ambiguous — choose a prim'
        def hierarchy_score(path):
            score = 0
            for maya_part, usd_part in zip(reversed(normalized.split('|')), reversed(path.split('/')[4:])):
                if maya_part != usd_part:
                    break
                score += 1
            return score
        suggestions = automatic or sorted([p for p in (scoped or records) if hierarchy_score(p)],
                                           key=lambda p: (-hierarchy_score(p), p))
        if suggestions and state == 'Choose a prim':
            state = 'Name candidates — confirm'
        ordered = list(dict.fromkeys(([old['path']] if old and old.get('path') in records else []) + suggestions + sorted(scoped) + sorted(records)))
        if selected:
            reason = state
        elif not instances:
            reason = 'Maya namespace does not match a Cast instance.'
        elif len(instances) > 1:
            reason = 'Maya namespace matches multiple Cast instances.'
        elif not scoped:
            reason = 'Selected Composition has no editable USD geometry for this Cast instance.'
        elif not any(stage.GetPrimAtPath(p).GetCustomDataByKey('smartpipeline:setdress:sourcePath') or records[p]['source_id'] for p in scoped):
            reason = 'Selected Composition uses Asset USD without mapping metadata. Publish Assets using the updated USD, then select the new Base Composition.'
        else:
            reason = 'Asset mapping exists but no unique source ID / hierarchy match was found. Confirm a candidate.'
        result[key] = dict(selected=selected, state=state, candidates=ordered, reason=reason)
    return result
