from smartlib.dcc.maya.animation_curves import _resolve_scene_plug


class Scene:
    def __init__(self, nodes):
        self.nodes = nodes
    def objExists(self, plug):
        return plug in [node + ".jointOrientZ" for node in self.nodes]
    def ls(self, *args, **kwargs):
        return self.nodes


def test_removed_shot_parent_is_ignored():
    target = "|DLI:Root|DLI:foot|DLI:arch"
    assert _resolve_scene_plug(Scene([target]), "|assets_grp" + target + ".jointOrientZ") == target + ".jointOrientZ"


def test_different_shot_parent_is_ignored():
    target = "|new_grp|DLI:Root|DLI:foot|DLI:arch"
    assert _resolve_scene_plug(Scene([target]), "|assets_grp|DLI:Root|DLI:foot|DLI:arch.jointOrientZ") == target + ".jointOrientZ"


def test_ambiguous_and_different_rig_hierarchy_are_not_matched():
    source = "|assets_grp|DLI:Root|DLI:foot|DLI:arch.jointOrientZ"
    for nodes in (["|a|DLI:Root|DLI:foot|DLI:arch", "|b|DLI:Root|DLI:foot|DLI:arch"],
                  ["|DLI:Root|DLI:other|DLI:arch"], ["|Other:Root|Other:foot|Other:arch"]):
        assert _resolve_scene_plug(Scene(nodes), source) == source
