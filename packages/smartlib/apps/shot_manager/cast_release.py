"""Pinned Cast release inputs shared by animation and composition."""
from smartlib.core.metadata import read_json


def cast_product(service, identity, target, base):
    if not base:
        return None
    snapshot = service.load_handoff(service.check(base))
    expected = dict(zip(('episode', 'sequence', 'shot'), service._identity(identity)))
    if snapshot.get('shot') != expected:
        raise ValueError('Cast composition belongs to another shot')
    for ref in snapshot['products']:
        product = service.load_handoff(service.check(ref))
        if product['kind'] == 'assets' and product['target'] == target and product['inputs'].get('asset_release'):
            return ref
    return None


def animation_asset(service, identity, target, ref):
    product = service.load_handoff(service.check(ref))
    expected = dict(zip(('episode', 'sequence', 'shot'), service._identity(identity)))
    if product['kind'] != 'assets' or product['target'] != target or product['shot'] != expected:
        raise ValueError('Select a Cast publish for this shot and target')
    release = product['inputs'].get('asset_release')
    if not release:
        raise ValueError('Cast must pin an Asset USD Release')
    record = read_json(service.check(release), {})
    for dependency in record.get('dependencies', []):
        service.check(dependency)
    return product, record
