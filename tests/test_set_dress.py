from smartlib.dcc.maya.set_dress import (
    Change,
    NodeState,
    SetDressLayer,
    SetDressPackage,
    composed_values,
    diff_states,
    load_package,
    save_package,
    suggested_path,
)


def test_diff_tracks_transform_and_visibility_only():
    before = [NodeState("a", "|set|chair", {"translateX": 0.0, "visibility": True, "custom": 1})]
    after = [NodeState("a", "|set|chair", {"translateX": 3.5, "visibility": False, "custom": 9})]
    changes = diff_states(before, after)
    assert [(item.attribute, item.before, item.after) for item in changes] == [
        ("translateX", 0.0, 3.5),
        ("visibility", True, False),
    ]


def test_top_layer_has_override_priority():
    low = SetDressLayer(name="sequence", changes=[Change("a", "chair", "translateX", 0, 10)])
    high = SetDressLayer(name="shot", changes=[Change("a", "chair", "translateX", 0, 20)])
    result = composed_values([high, low])
    assert result[("a", "translateX")].after == 20
    high.muted = True
    assert composed_values([high, low])[("a", "translateX")].after == 10


def test_package_round_trip(tmp_path):
    package = SetDressPackage(
        layers=[SetDressLayer(name="shot_fix", changes=[Change("a", "chair", "visibility", True, False)])],
        context={"sequence": "sq010", "shot": "sh020"},
    )
    path = save_package(package, tmp_path / "shot.setdress.json")
    loaded = load_package(path)
    assert loaded.to_dict() == package.to_dict()


def test_suggested_paths_are_under_project_data(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    context = {"sequence": "sq010", "shot": "sh020"}
    assert suggested_path("shot", context) == (
        tmp_path / "data" / "setdress" / "shot" / "sq010" / "sh020.setdress.json"
    )
    assert suggested_path("sequence", context) == (
        tmp_path / "data" / "setdress" / "sequence" / "sq010.setdress.json"
    )
