import pytest

from smartlib.dcc.maya import shot_scene_data as data


class Scene:
    cameras = ['|cam', '|VCCAM', '|custom:renderView', '|persp']

    def __init__(self, selected=False, tagged=False):
        self.selected, self.tagged = selected, tagged

    def ls(self, **kw):
        if kw.get('selection'):
            return ['|cam'] if self.selected else []
        return [n + '|shape' for n in self.cameras] if kw.get('type') == 'camera' else self.cameras

    def listRelatives(self, node, **kw):
        if kw.get('parent'):
            return [node.rsplit('|', 1)[0]]
        return [node + '|shape']

    def nodeType(self, node):
        return 'camera' if node.endswith('|shape') else 'transform'

    def objExists(self, node):
        return self.tagged and node == '|cam.smartpipelineDataType'

    def getAttr(self, node):
        return 'camera'


@pytest.mark.parametrize('selected,tagged', [(False, False), (True, False), (False, True)])
def test_all_scene_cameras_survive_root_preferences(monkeypatch, selected, tagged):
    monkeypatch.setattr(data, '_maya_cmds', lambda: Scene(selected, tagged))
    roots = data.list_scene_component_roots('camera')
    assert '|VCCAM' in roots
    assert '|custom:renderView' in roots
    assert '|cam' in roots
    assert '|persp' not in roots
