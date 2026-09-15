"""Maya mutations isolated from comparison and Qt; no scene save or removal."""
from dataclasses import dataclass, field
from pathlib import Path

from smartlib.apps.smart_reference_editor.service import Reference, path_key


def scan(cmds):
    references = []
    for node in cmds.ls(type='reference') or []:
        if node == 'sharedReferenceNode':
            continue
        # Keep unreadable nodes in the snapshot so changes remain detectable.
        values = dict(namespace='', path='', loaded=False, nested=False)
        errors = []
        queries = {
            'namespace': dict(namespace=True),
            'path': dict(filename=True, withoutCopyNumber=True),
            'loaded': dict(isLoaded=True),
            'nested': dict(parent=True, referenceNode=True),
        }
        for key, flags in queries.items():
            try:
                value = cmds.referenceQuery(node, **flags)
                values[key] = str(value or '').strip(':') if key == 'namespace' else (
                    str(value or '') if key == 'path' else bool(value))
            except RuntimeError as exc:
                errors.append(str(exc))
        if not values['path'] and not errors:
            errors.append('Reference node is not associated with a reference file.')
        references.append(Reference(str(node), **values, error='; '.join(dict.fromkeys(errors))))
    return tuple(sorted(references, key=lambda ref: ref.node))


def snapshot(cmds):
    return str(cmds.file(query=True, sceneName=True) or ''), scan(cmds)


@dataclass(frozen=True)
class Operation:
    kind: str
    namespace: str
    target: str
    reference: Reference | None = None


@dataclass
class ApplyResult:
    completed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def apply(cmds, expected_snapshot, operations):
    operations = list(operations)
    if snapshot(cmds) != expected_snapshot:
        raise RuntimeError('Scene references changed after scanning. Refresh and preview again.')
    namespaces, nodes = set(), set()
    for op in operations:
        if op.kind not in {'add', 'replace'}:
            raise ValueError('Unsupported reference operation.')
        if not Path(op.target).is_file() or Path(op.target).suffix.lower() not in {'.ma', '.mb'}:
            raise RuntimeError(f'Published Maya file is missing: {op.target}')
        if op.kind == 'add':
            if not op.namespace or op.namespace in namespaces or cmds.namespace(exists=op.namespace):
                raise RuntimeError(f'Namespace conflict: {op.namespace}')
            namespaces.add(op.namespace)
        else:
            ref = op.reference
            if ref is None or ref.error or ref.nested or ref not in expected_snapshot[1] or ref.node in nodes:
                raise RuntimeError('Replacement requires a unique top-level reference from the scan.')
            if ref.namespace != op.namespace:
                raise RuntimeError('Replacement must retain the existing namespace.')
            nodes.add(ref.node)
    result = ApplyResult()
    for op in operations:
        try:
            if op.kind == 'add':
                before = {ref.node for ref in scan(cmds)}
                cmds.file(op.target, reference=True, namespace=op.namespace,
                          deferReference=False, mergeNamespacesOnClash=False,
                          defaultNamespace=False, options='v=0;')
                added = [ref for ref in scan(cmds) if ref.node not in before and not ref.nested and not ref.error]
                if len(added) != 1 or added[0].namespace != op.namespace:
                    raise RuntimeError('Maya did not create the expected top-level reference namespace.')
            else:
                ref = op.reference
                if path_key(ref.path) == path_key(op.target):
                    continue
                # Reuse the reference node so namespace and reference edits survive.
                # An unloaded reference is repathed without forcing it to load.
                cmds.file(op.target, loadReference=ref.node,
                          loadReferenceDepth='asPrefs' if ref.loaded else 'none')
                actual = str(cmds.referenceQuery(ref.node, filename=True, withoutCopyNumber=True))
                if path_key(actual) != path_key(op.target):
                    raise RuntimeError('Maya did not retain the selected replacement path.')
                if str(cmds.referenceQuery(ref.node, namespace=True)).strip(':') != ref.namespace:
                    raise RuntimeError('Maya changed the reference namespace; inspect the scene.')
                if ref.loaded:
                    failed = cmds.referenceQuery(ref.node, editStrings=True, failedEdits=True, successfulEdits=False) or []
                    if failed:
                        raise RuntimeError(f'{len(failed)} reference edits failed after replacement; inspect the scene.')
            result.completed.append(f'{op.kind}: {op.namespace} → {op.target}')
        except Exception as exc:
            result.errors.append(f'{op.namespace}: {exc}. Batch stopped; this operation may have modified the scene.')
            break
    return result
