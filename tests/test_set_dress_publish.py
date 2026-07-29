from pathlib import Path

from smartlib.dcc.maya.set_dress import Change, SetDressLayer, SetDressPackage, save_package
from smartlib.setdress import SetDressIdentity, SetDressPublishService


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
