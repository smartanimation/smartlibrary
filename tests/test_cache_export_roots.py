from smartlib.dcc.maya.animation_curves import _cache_export_roots


class SetScene:
    def __init__(self, members):
        self.members = members
        self.queries = []

    def objExists(self, node):
        return node in self.members or node in {'|Hero:geo', '|Hero:geo|body', '|Hero:geo|body|shape'}

    def nodeType(self, node):
        if node in self.members:
            return 'objectSet'
        return 'mesh' if node.endswith('|shape') else 'transform'

    def ls(self, node, **kwargs):
        return [node]

    def sets(self, node, **kwargs):
        self.queries.append(node)
        return self.members[node]

    def listRelatives(self, node, **kwargs):
        if kwargs.get('parent'):
            return ['|Hero:geo|body']
        return ['|Hero:geo|body|shape']

    def getAttr(self, plug):
        return False


def test_nested_sets_cycles_and_duplicate_geometry():
    scene = SetScene({'Hero:cache_geo_set': ['Hero:cache', '|Hero:geo|body|shape'],
        'Hero:cache': ['Hero:nested', '|Hero:geo'],
        'Hero:nested': ['Hero:cache_geo_set', '|Hero:geo|body', 'missing']})
    assert _cache_export_roots(scene, 'Hero') == ['|Hero:geo']
    assert len(scene.queries) == len(set(scene.queries)) == 3


def test_direct_mesh_and_transform_remain_supported():
    for node in ['|Hero:geo|body', '|Hero:geo|body|shape']:
        scene = SetScene({'Hero:cache_geo_set': [node]})
        assert _cache_export_roots(scene, 'Hero:') == ['|Hero:geo|body']


def test_empty_missing_and_unnamespaced_sets():
    assert _cache_export_roots(SetScene({}), 'Hero') == []
    assert _cache_export_roots(SetScene({'Hero:cache_geo_set': []}), 'Hero') == []
    assert _cache_export_roots(SetScene({'cache_geo_set': ['|Hero:geo']}), '') == ['|Hero:geo']
