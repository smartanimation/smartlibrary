from smartlib.dcc.maya.set_dress import (
    Change,
    NodeState,
    SetDressLayer,
    SetDressPackage,
    composed_values,
    compose_packages,
    capture_scene,
    create_history_revision,
    diff_states,
    embed_package_in_scene,
    encode_scene_payload,
    decode_scene_payload,
    load_package,
    load_package_from_scene,
    list_history_revisions,
    remember_base,
    recordable_attributes,
    save_package,
    suggested_path,
)


class SceneCmds:
    def __init__(self):
        self.nodes = {}

    def objExists(self, name):
        if "." not in name:
            return name in self.nodes
        node, attr = name.split(".", 1)
        return node in self.nodes and attr in self.nodes[node]

    def nodeType(self, node):
        return self.nodes[node]["_type"]

    def ls(self, type=None):
        return [node for node, attrs in self.nodes.items() if attrs["_type"] == type]

    def createNode(self, node_type, name):
        self.nodes[name] = {"_type": node_type}
        return name

    def addAttr(self, node, longName, **_kwargs):
        self.nodes[node][longName] = None

    def setAttr(self, plug, value, **_kwargs):
        node, attr = plug.split(".", 1)
        self.nodes[node][attr] = value

    def getAttr(self, plug):
        node, attr = plug.split(".", 1)
        return self.nodes[node][attr]


def test_diff_tracks_any_captured_plug():
    before = [NodeState("a", "|set|chair", {"translateX": 0.0, "visibility": True, "custom": 1})]
    after = [NodeState("a", "|set|chair", {"translateX": 3.5, "visibility": False, "custom": 9})]
    changes = diff_states(before, after)
    assert [(item.attribute, item.before, item.after) for item in changes] == [
        ("custom", 1, 9),
        ("translateX", 0.0, 3.5),
        ("visibility", True, False),
    ]


class AttributeCmds:
    def __init__(self):
        self.attrs = {
            "translateX": ("double", False), "visibility": ("bool", False),
            "costumeVariant": ("enum", False), "variantLabel": ("string", False),
            "drivenOutput": ("double", True), "worldMatrix": ("matrix", False),
        }

    def listAttr(self, _node, keyable=False, channelBox=False):
        if keyable:
            return ["translateX", "visibility", "costumeVariant", "drivenOutput"]
        if channelBox:
            return ["variantLabel", "worldMatrix"]
        return []

    def objExists(self, plug):
        return plug.split(".", 1)[1] in self.attrs

    def getAttr(self, plug, lock=False, type=False):
        attr_type, locked = self.attrs[plug.split(".", 1)[1]]
        if lock:
            return locked
        if type:
            return attr_type
        raise AssertionError("value query is not expected")


def test_recordable_attributes_include_rig_variants_and_filter_unsafe_plugs():
    assert recordable_attributes(AttributeCmds(), "rig_SETTINGS") == [
        "costumeVariant", "translateX", "variantLabel", "visibility",
    ]


class CaptureCmds:
    def __init__(self):
        self.values = {
            "|set": {"translateX": ("double", 0.0)},
            "|set|geo": {"translateX": ("double", 2.0)},
            "|set|geo|geoShape": {"castsShadows": ("bool", True)},
        }

    def ls(self, objects=None, selection=False, long=False, type=None, shapes=False):
        if selection:
            return ["|set"]
        candidates = list(objects or self.values)
        if type == "transform":
            return [item for item in candidates if not item.endswith("Shape")]
        if shapes:
            return [item for item in candidates if item.endswith("Shape")]
        return candidates

    def listRelatives(self, _nodes, allDescendents=False, fullPath=False):
        return ["|set|geo|geoShape", "|set|geo"]

    def listAttr(self, node, keyable=False, channelBox=False):
        return list(self.values[node]) if keyable else []

    def objExists(self, plug):
        node, attribute = plug.rsplit(".", 1)
        return node in self.values and attribute in self.values[node]

    def getAttr(self, plug, lock=False, type=False):
        node, attribute = plug.rsplit(".", 1)
        attr_type, value = self.values[node][attribute]
        if lock:
            return False
        if type:
            return attr_type
        return value

    def nodeType(self, _node):
        return "mesh"


def test_capture_scene_includes_transforms_and_shapes_in_selected_hierarchy():
    states = capture_scene(selection_only=True, cmds=CaptureCmds())
    assert [state.node for state in states] == [
        "|set", "|set|geo", "|set|geo|geoShape",
    ]
    assert states[-1].values == {"castsShadows": True}


def test_top_layer_has_override_priority():
    low = SetDressLayer(name="sequence", changes=[Change("a", "chair", "translateX", 0, 10)])
    high = SetDressLayer(name="shot", changes=[Change("a", "chair", "translateX", 0, 20)])
    result = composed_values([high, low])
    assert result[("a", "translateX")].after == 20
    high.muted = True
    assert composed_values([high, low])[("a", "translateX")].after == 10


def test_build_packages_compose_layers_base_and_context():
    sequence = SetDressPackage(
        layers=[SetDressLayer(name="sequence")],
        base=[NodeState("set", "|set", {"visibility": True})],
        context={"scope": "sequence", "sequence": "sq010"},
    )
    shot = SetDressPackage(
        layers=[SetDressLayer(name="shot")],
        base=[
            NodeState("set", "|set", {"visibility": False, "translateX": 0.0}),
            NodeState("rig", "|hero", {"costumeVariant": 0}),
        ],
        context={"scope": "shot", "shot": "sh020"},
    )

    result = compose_packages([sequence, shot])

    assert [layer.name for layer in result.layers] == ["sequence", "shot"]
    assert result.context == {
        "scope": "shot", "sequence": "sq010", "shot": "sh020",
    }
    assert result.base == [
        NodeState("set", "|set", {"visibility": True, "translateX": 0.0}),
        NodeState("rig", "|hero", {"costumeVariant": 0}),
    ]


def test_base_snapshot_keeps_the_first_value_across_multiple_captures():
    first = [NodeState("a", "chair", {"translateX": 0, "translateY": 1})]
    second = [NodeState("a", "chair", {"translateX": 10, "translateZ": 2})]
    base = remember_base([], first)
    base = remember_base(base, second)
    assert base == [
        NodeState("a", "chair", {"translateX": 0, "translateY": 1, "translateZ": 2})
    ]


def test_package_round_trip(tmp_path):
    package = SetDressPackage(
        layers=[SetDressLayer(name="shot_fix", target="usd", changes=[Change("a", "chair", "visibility", True, False)])],
        context={"sequence": "sq010", "shot": "sh020"},
        base=[NodeState("a", "chair", {"visibility": True})],
    )
    path = save_package(package, tmp_path / "shot.setdress.json")
    loaded = load_package(path)
    assert loaded.to_dict() == package.to_dict()


def test_version_one_layer_defaults_to_maya_target():
    package = SetDressPackage.from_dict({
        "format": "smart-set-dress",
        "version": 1,
        "layers": [{"name": "legacy", "changes": []}],
    })
    assert package.layers[0].target == "maya"


def test_scene_payload_round_trip_and_checksum():
    package = SetDressPackage(
        layers=[SetDressLayer(name="fix", changes=[Change("a", "chair", "visibility", True, False)])]
    )
    payload, checksum = encode_scene_payload(package)
    assert decode_scene_payload(payload, checksum).to_dict() == package.to_dict()
    try:
        decode_scene_payload(payload, "bad-checksum")
    except ValueError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("Checksum mismatch was not detected")


def test_package_is_embedded_in_network_node():
    cmds = SceneCmds()
    package = SetDressPackage(layers=[SetDressLayer(name="shot_fix")])
    node = embed_package_in_scene(
        package, external_path="P:/shot/data/setdress/main.setdress.json", cmds=cmds
    )
    recovered, path = load_package_from_scene(cmds)
    assert node == "smartSetDressData"
    assert recovered.to_dict() == package.to_dict()
    assert path.endswith("main.setdress.json")


def test_history_revisions_are_numbered_and_pruned(tmp_path):
    package = SetDressPackage(layers=[SetDressLayer(name="shot_fix")])
    working = save_package(
        package, tmp_path / "data" / "setdress" / "main.setdress.json"
    )
    first = create_history_revision(package, working, keep=2)
    second = create_history_revision(package, working, keep=2)
    third = create_history_revision(package, working, keep=2)
    assert first.name == "r0001.setdress.json"
    assert second.name == "r0002.setdress.json"
    assert third.name == "r0003.setdress.json"
    assert [path.name for path in list_history_revisions(working)] == [
        "r0003.setdress.json",
        "r0002.setdress.json",
    ]
    assert not first.exists()


def test_suggested_paths_are_under_project_data(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    context = {"episode": "ep001", "sequence": "sq010", "shot": "sh020", "package": "main"}
    assert suggested_path("shot", context) == (
        tmp_path / "production" / "shots" / "ep001" / "sq010" / "sh020" / "data" / "setdress" / "main.setdress.json"
    )
    assert suggested_path("sequence", context) == (
        tmp_path / "data" / "setdress" / "sequence" / "sq010.setdress.json"
    )
