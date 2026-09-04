import json
import pytest
from pathlib import Path

from smartlib.dcc.maya.set_dress import Change, SetDressLayer, SetDressPackage, load_package, save_package
from smartlib.setdress import SetDressIdentity, SetDressPublishService
from smartlib.dcc.maya.set_dress import (
    NodeState, layer_package, compose_packages, visible_work_packages,
)


def test_layers_save_and_publish_independently(tmp_path):
    service = SetDressPublishService(Config(tmp_path))
    identity = SetDressIdentity("ep001", "sq010", "sh020")
    desk = SetDressLayer(name="Desk_set", changes=[
        Change("desk", "|desk", "translateX", 0, 8),
    ])
    chair = SetDressLayer(name="Chair", changes=[
        Change("chair", "|chair", "visibility", True, False),
    ])
    package = SetDressPackage(
        layers=[desk, chair, SetDressLayer(name="empty")],
        base=[
            NodeState("desk", "|desk", {"translateX": 0, "translateY": 999}),
            NodeState("chair", "|chair", {"visibility": True}),
        ],
    )
    legacy = save_package(package, service.data_path(identity, "main"))
    paths = service.save_layers(package, identity)
    assert {p.name for p in paths.values()} == {"Desk_set.setdress.json", "Chair.setdress.json"}
    saved_desk = load_package(paths[desk.id])
    assert [layer.name for layer in saved_desk.layers] == ["Desk_set"]
    assert saved_desk.base == [NodeState("desk", "|desk", {"translateX": 0})]
    assert set(visible_work_packages(legacy.parent.glob("*.setdress.json"))) == set(paths.values())
    from smartlib.apps.shot_manager.service import ShotManagerService
    manager = object.__new__(ShotManagerService)
    manager.shot_data_root = lambda _identity: legacy.parent.parent
    rows = manager.list_set_dress_data(identity)
    assert {row.name for row in rows} == {"set_dress_data/Desk_set", "set_dress_data/Chair"}
    assert legacy.is_file()  # Migration does not erase the original bundle.
    composed = compose_packages([load_package(paths[chair.id]), saved_desk])
    assert [layer.name for layer in composed.layers] == ["Desk_set", "Chair"]
    first = service.publish(paths[desk.id], identity, package=desk.name)
    second = service.publish(paths[desk.id], identity, package=desk.name)
    chair_publish = service.publish(paths[chair.id], identity, package=chair.name)
    assert (first.parent.name, second.parent.name, chair_publish.parent.name) == ("v001", "v002", "v001")
    assert len(load_package(chair_publish).layers) == 1


def test_layer_export_uses_change_before_without_base_and_keeps_order():
    layer = SetDressLayer(name="variant", changes=[Change("a", "rig", "variant", 1, 2)])
    stack = SetDressPackage(layers=[SetDressLayer(), layer])
    exported = layer_package(stack, layer)
    assert exported.base == [NodeState("a", "rig", {"variant": 1})]
    assert layer_package(exported, exported.layers[0]).context["layer_order"] == "1"
    rebuilt = compose_packages([exported])
    rebuilt.layers.insert(0, SetDressLayer())
    assert layer_package(rebuilt, rebuilt.layers[1]).context["layer_order"] == "1"


def test_layer_filename_collisions_are_rejected_before_writes(tmp_path):
    service = SetDressPublishService(Config(tmp_path))
    identity = SetDressIdentity("ep001", "sq010", "sh020")
    package = SetDressPackage(layers=[
        SetDressLayer(name=name, changes=[Change("a", "node", "visibility", True, False)])
        for name in ("Desk set", "Desk_set")
    ])
    with pytest.raises(ValueError, match="same file"):
        service.save_layers(package, identity)
    assert not service.data_path(identity, "Desk_set").exists()


def test_rename_hides_old_work_file_without_deleting_it(tmp_path):
    service = SetDressPublishService(Config(tmp_path))
    identity = SetDressIdentity("ep001", "sq010", "sh020")
    layer = SetDressLayer(name="old", changes=[Change("a", "a", "visibility", True, False)])
    package = SetDressPackage(layers=[layer])
    old = service.save_layers(package, identity)[layer.id]
    layer.name = "new"
    new = service.save_layers(package, identity)[layer.id]
    assert visible_work_packages([old, new]) == [new]
    assert old.is_file()


class Config:
    def __init__(self, root: Path):
        self.project_root = root
        self.project_name = "TEST"
        self.config_dir = root / "config"
        self.base = {"anchors": {"project_name": "TEST", "project_root": str(root)}}

    def load(self, name):
        if name == "templates_shots.yml":
            return {"templates": {"shot_root": "{project_root}/shots/{episode}/{seq}/{shot}"}}
        if name == "templates_base.yml":
            return {"templates": {"sequences_root": "{project_root}/sequences"}}
        return {}


def _package(path: Path):
    package = SetDressPackage(
        layers=[
            SetDressLayer(
                name="shot_fix",
                changes=[Change("uuid", "|set|chair", "translateX", 0.0, 2.0)],
            )
        ],
        context={"scene": "shot.ma"},
    )
    return save_package(package, path)


def test_shot_data_path_uses_shot_root(tmp_path):
    service = SetDressPublishService(Config(tmp_path))
    identity = SetDressIdentity("ep001", "sq010", "sh020")
    assert service.data_path(identity, "hero set") == (
        tmp_path / "shots" / "ep001" / "sq010" / "sh020"
        / "data" / "setdress" / "hero_set.setdress.json"
    )


def test_publish_versions_and_latest(tmp_path):
    service = SetDressPublishService(Config(tmp_path))
    identity = SetDressIdentity("ep001", "sq010", "sh020")
    source = _package(service.data_path(identity, "main"))
    first = service.publish(source, identity, package="main", comment="first")
    second = service.publish(source, identity, package="main", comment="second")
    assert first.parent.name == "v001"
    assert second.parent.name == "v002"
    rows = service.list_versions(identity)
    assert [(row.version, row.latest, row.comment) for row in rows] == [
        ("v002", True, "second"),
        ("v001", False, "first"),
    ]


def test_copy_to_shot_creates_next_version_and_retargets_context(tmp_path):
    service = SetDressPublishService(Config(tmp_path))
    source_identity = SetDressIdentity("ep001", "sq010", "sh010")
    target_identity = SetDressIdentity("ep001", "sq010", "sh020")
    source = _package(service.data_path(source_identity, "main"))
    source_publish = service.publish(source, source_identity, package="main", comment="source")

    first = service.copy_to_shot(
        source_publish,
        target_identity,
        package="main",
        comment="copied once",
    )
    second = service.copy_to_shot(
        source_publish,
        target_identity,
        package="main",
        comment="copied twice",
    )

    assert first.parent.name == "v001"
    assert second.parent.name == "v002"
    assert service.next_version(target_identity, package="main") == "v003"
    copied = load_package(second)
    assert copied.context["episode"] == "ep001"
    assert copied.context["sequence"] == "sq010"
    assert copied.context["shot"] == "sh020"
    assert copied.context["package"] == "main"
    assert "scene" not in copied.context
    manifest = json.loads((second.parent / "publish.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "v002"
    assert manifest["copied_from"]["shot"] == "sh010"
    assert manifest["copied_from"]["version"] == "v001"
    assert manifest["comment"] == "copied twice"
