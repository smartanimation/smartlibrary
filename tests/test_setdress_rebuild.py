from pathlib import Path

import pytest

from smartlib.dcc.maya import set_dress as sd
from smartlib.dcc.maya.shot_builder import _apply_construct_set_dress


class References:
    def __init__(self, nodes, reference_id='new-ref'):
        self.nodes = nodes
        self.reference_id = reference_id

    def ls(self, value, **kwargs):
        if kwargs.get('uuid'):
            return [self.reference_id]
        return self.nodes if value == 'source-id' else []

    def referenceQuery(self, node, **kwargs):
        return True if kwargs.get('isNodeReferenced') else 'referenceNode'

    def objExists(self, value):
        return True  # A matching name alone must never bypass UUID validation.


def test_rebuild_resolves_namespaced_relative_path_and_source_uuid():
    node = '|assets_grp|Room:Root|Room:props|Room:desk'
    other = '|assets_grp|Other:Root|Other:props|Other:desk'
    assert sd._resolve_node(References([node, other]), 'maya-reference:old-ref:source-id',
                            '|old_group|Room:Root|Room:props|Room:desk') == node


def test_rebuild_rejects_ambiguous_or_changed_asset_nodes():
    path = '|Room:Root|Room:desk'
    for nodes in ([], ['|Other:Root|Other:desk'], ['|a' + path, '|b' + path]):
        assert sd._resolve_node(References(nodes), 'maya-reference:old-ref:source-id', path) == ''


def test_same_reference_uuid_still_survives_rename():
    node = '|renamed|desk'
    assert sd._resolve_node(References([node], 'old-ref'), 'maya-reference:old-ref:source-id',
                            '|Room:Root|Room:desk') == node


def test_enabled_missing_setdress_fails_build(tmp_path):
    with pytest.raises(RuntimeError, match='Set Dress input was not found'):
        _apply_construct_set_dress(tmp_path, {'components': [dict(
            component_type='set_dress', enabled=True, path=str(tmp_path / 'missing.json'))]})


def test_setdress_apply_warnings_fail_build_before_embedding(tmp_path, monkeypatch):
    package = sd.SetDressPackage(layers=[sd.SetDressLayer(changes=[sd.Change('id','node','tx',0,1)])])
    path = sd.save_package(package, tmp_path / 'layer.setdress.json')
    monkeypatch.setattr(sd, 'apply_stack', lambda *a, **kw: ['Missing or ambiguous: node.tx'])
    monkeypatch.setattr(sd, 'embed_package_in_scene', lambda *a, **kw: pytest.fail('Must not mark failed data as applied'))
    with pytest.raises(RuntimeError, match='Set Dress was not fully applied'):
        _apply_construct_set_dress(tmp_path, {'components': [dict(
            component_type='set_dress', enabled=True, path=str(path))]})
